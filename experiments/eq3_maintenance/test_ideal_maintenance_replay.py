import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from experiments.eq3_maintenance.build_ideal_maintenance_bundle import EVIDENCE, SCHEMA
from experiments.eq3_maintenance.ideal_maintenance_replay import IdealIndependentMaintenanceReplay


class FakeForegroundService:
    def __init__(self):
        self.now = 0
        self.header = {"maintenance_issued": 0}
        self.requests = {}
        self.completions = {}
        self.observations = []
        self.native_observations = []
        self.closed = False

    def maintain(self, *args, **kwargs):
        raise AssertionError("wrapper must never submit maintenance to MQSim")

    def submit(self, request):
        self.requests[request["request_id"]] = dict(request)

    def try_submit(self, request):
        self.submit(request)
        return {"submitted": True, "backend_arrival_ns": self.now}

    def until(self, horizon):
        self.now = horizon
        return None

    def finish(self):
        return {"status": "FINISHED", "maintenance_issued": 0,
                "maintenance_completed": 0, "pending_maintenance": 0}

    def close(self):
        self.closed = True


class IdealReplayTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.profile = self.root / "profile.json"
        self.stack_map = self.root / "stack-map.json"
        self.foreground = self.root / "requests.json"
        self.profile.write_text('{"name":"p"}\n')
        self.stack_map.write_text('{"schema_version":1}\n')
        self.foreground.write_text(json.dumps([{
            "request_id": "read-1", "stack": "hbf0", "operation": "read",
            "bytes": 16384, "stack_local_page": 0,
        }]) + "\n")
        digest = lambda path: hashlib.sha256(path.read_bytes()).hexdigest()
        self.intent = {
            "request_id": 7, "stack": "hbf0", "stack_local_page": 0,
            "due_ns": 10, "deadline_ns": 30, "reclaim_source_block": True,
            "trigger_reason": "RETENTION_AGE_DUE",
        }
        self.bundle = self.root / "bundle.json"
        self.bundle.write_text(json.dumps({
            "schema_version": SCHEMA, "evidence": EVIDENCE,
            "resource_model": "IDEAL_INDEPENDENT_MAINTENANCE_REPLAY",
            "input_contract": {
                "profile_sha256": digest(self.profile),
                "stack_map_sha256": digest(self.stack_map),
                "foreground_requests_sha256": digest(self.foreground),
            },
            "maintenance_intents": [self.intent],
            "submission_facts": {"7": {"submit_ns": 10,
                                           "placement": {"stack": "hbf0"},
                                           "target": {"channel": 0, "die": 0}}},
            "backend_events": [{"request_id": 7, "state": "READ", "time_ns": 11,
                                  "replay_order": 0, "evidence": EVIDENCE}],
            "native_events": [{
                "command_id": "A2R-C1", "source_command_id": 1, "command_code": 48,
                "phase": 1, "time_ns": 11, "replay_order": 0, "evidence": EVIDENCE,
                "transactions": [{"transaction_id": "A2R-T1",
                    "source_transaction_id": 1, "maintenance_request_id": 7,
                    "maintenance_id": 7, "type": 0}],
            }],
            "completions": [{"request_id": 7, "end_ns": 12,
                "status": "COMMITTED_RECLAIM_DEFERRED", "mapping_committed": True,
                "age_reset_ns": 12, "source_version": "UNKNOWN_REPLAY",
                "committed_version": "UNKNOWN_REPLAY", "transaction_ids": ["A2R-T1"],
                "baseline_transaction_ids": [1], "evidence": EVIDENCE}],
        }) + "\n")

    def tearDown(self):
        self.temporary.cleanup()

    def wrapper(self, inner=None):
        return IdealIndependentMaintenanceReplay(
            inner or FakeForegroundService(), self.bundle,
            profile_path=self.profile, stack_map_path=self.stack_map,
            foreground_requests_path=self.foreground)

    def test_replays_without_backend_submission_and_conserves_identity(self):
        inner = FakeForegroundService()
        service = self.wrapper(inner)
        service.until(10)
        response = service.maintain(dict(self.intent))
        self.assertEqual(response["maintenance_accepted"], 1)
        self.assertFalse(response["actual_backend_submitted"])
        service.until(12)
        self.assertEqual(service.maintenance_events[0]["state"], "READ")
        event = service.native_observations[0]
        self.assertEqual(event["command_id"], "A2R-C1")
        self.assertEqual(event["transactions"][0]["maintenance_id"], 7)
        completion = service.maintenance_completions[7]
        self.assertEqual(completion["source_version"], "UNKNOWN_REPLAY")
        self.assertEqual(completion["transaction_ids"], ["A2R-T1"])
        receipt = service.finish()["ideal_independent_maintenance_replay"]
        self.assertEqual(receipt["actual_backend_maintenance_issued"], 0)
        self.assertEqual(receipt["replay_maintenance_completed"], 1)
        self.assertFalse(receipt["current_mqsim_mapping_mutated_by_replay"])

    def test_rejects_write_input_and_runtime_write(self):
        service = self.wrapper()
        with self.assertRaisesRegex(ValueError, "read-only"):
            service.submit({"request_id": 1, "operation": "write"})
        self.foreground.write_text(json.dumps([{
            "request_id": "write-1", "stack": "hbf0", "operation": "write",
        }]) + "\n")
        bundle = json.loads(self.bundle.read_text())
        bundle["input_contract"]["foreground_requests_sha256"] = hashlib.sha256(
            self.foreground.read_bytes()).hexdigest()
        self.bundle.write_text(json.dumps(bundle) + "\n")
        with self.assertRaisesRegex(ValueError, "concurrent HBF writes"):
            self.wrapper()

    def test_rejects_changed_input_intent_or_submit_time(self):
        self.profile.write_text('{"name":"different"}\n')
        with self.assertRaisesRegex(ValueError, "input identity"):
            self.wrapper()
        self.profile.write_text('{"name":"p"}\n')
        service = self.wrapper()
        service.until(9)
        with self.assertRaisesRegex(RuntimeError, "submit time"):
            service.maintain(dict(self.intent))
        service = self.wrapper()
        service.until(10)
        changed = dict(self.intent, stack_local_page=2)
        with self.assertRaisesRegex(ValueError, "frozen A2 intent"):
            service.maintain(changed)


if __name__ == "__main__":
    unittest.main()
