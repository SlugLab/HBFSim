import heapq
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / "tools"))

from closed_loop import ClosedLoopCoordinator, UnsupportedComposition
from read_rate_policy import EngineeringProfile, ReadRatePolicy
from eq3_basic_fabric import BasicFabric
from eq3_basic_system import engineering_fixture
from incremental_fabric import IncrementalBasicFabric


class FakeMqsim:
    def __init__(self, *, raw_offset=0, maintenance="UNSUPPORTED_CAPABILITY"):
        self.now = 0
        self.raw_offset = raw_offset
        self.maintenance_mode = maintenance
        self.observations, self.native_observations = [], []
        self.requests, self.completions, self.pending = {}, {}, []
        self.maintenance_events, self.maintenance_completions = [], {}
        self.maintenance_requests = {}

    def try_submit(self, request):
        request = dict(request)
        rid = request["request_id"]
        self.requests[rid] = request
        reported = request["issue_ns"] + 50
        heapq.heappush(self.pending, (reported, rid, "request"))
        self.observations.extend({"kind": kind, "request_id": rid,
                                  "time_ns": request["issue_ns"],
                                  "reported_complete": 0, "bytes": request["bytes"]}
                                 for kind in (0, 1))
        self.native_observations.append({"phase": "media_start", "request_id": rid,
                                         "start_ns": request["issue_ns"], "end_ns": reported,
                                         "stack": request["stack"], "bytes": request["bytes"]})
        return {"submitted": True, "disposition": 0, "reason": "fixture",
                "target_ns": None, "backend_arrival_ns": request["issue_ns"]}

    def until(self, horizon):
        if self.pending and self.pending[0][0] <= horizon:
            reported, rid, kind = heapq.heappop(self.pending)
            self.now = reported
            if kind == "maintenance":
                self.maintenance_events.append({"request_id": rid, "parent_id": rid,
                                                "state": "commit", "time_ns": reported,
                                                "transaction_id": None, "source": {},
                                                "destination": None})
                cleanup_failure = self.maintenance_mode == "POSTCOMMIT_ERASE_FAILURE"
                self.maintenance_completions[rid] = {
                    "request_id": rid, "parent_id": rid, "status": "COMMITTED",
                    "enqueue_ns": reported-25, "start_ns": reported-25,
                    "end_ns": reported, "mapping_committed": True,
                    "age_reset_ns": reported, "erase_completed": not cleanup_failure}
                if cleanup_failure:
                    self.maintenance_completions[rid]["status"] = "FAILED_AFTER_COMMIT_NEEDS_RECONCILE"
                elif self.maintenance_mode == "COMMITTED_RECLAIM_DEFERRED":
                    self.maintenance_completions[rid]["status"] = "COMMITTED_RECLAIM_DEFERRED"
                completion = None
            else:
                request = self.requests[rid]
                self.observations.append({"kind": 2, "request_id": rid,
                                          "time_ns": reported + self.raw_offset,
                                          "reported_complete": reported,
                                          "bytes": request["bytes"]})
                completion = {"request_id": rid, "reported_complete": reported, "status": 0}
                self.completions[rid] = completion
            return completion
        self.now = horizon
        return None

    def maintain(self, request):
        if self.maintenance_mode == "UNSUPPORTED_CAPABILITY":
            return {"status": "UNSUPPORTED_CAPABILITY", "submitted": False}
        rid = request['request_id']
        self.maintenance_requests[rid] = dict(request)
        heapq.heappush(self.pending, (self.now + 25, rid, "maintenance"))
        return {"status": "ACCEPTED", "maintenance_accepted": 1,
                "placement": {"stack": request["stack"]}, "target": {}}

    def finish(self):
        if self.pending:
            raise ValueError("unfinished fake backend")
        return {"status": "FINISHED", "issued": len(self.requests),
                "completed": len(self.completions), "pending": 0}


class FakeEnergy:
    def __init__(self):
        self.now = 0
        self.rows = []

    def native(self, event):
        self.rows.append({"scope": "native", "request_id": event["request_id"]})

    def hbm(self, fact):
        self.rows.append({"scope": "hbm", "request_id": fact["request_id"]})

    def fabric(self, event, request):
        if request["route"] == "relay" and "partner" not in request:
            raise AssertionError("relay partner is required")
        self.rows.append({"scope": "fabric", "request_id": event["request_id"]})

    def flush(self, boundary):
        if boundary < self.now:
            raise ValueError("energy time moved backward")
        result = {"component:test": (boundary-self.now) * 1e-12}
        self.now = boundary
        return result


class FakeThermal:
    def __init__(self, stacks, states=None):
        self.stacks = tuple(stacks)
        self.states = list(states or [])
        self.calls = []

    def advance(self, start_ns, end_ns, component_energy_j):
        state = self.states.pop(0) if self.states else "normal"
        row = {"start_ns": start_ns, "end_ns": end_ns,
               "stack_states": {stack: state for stack in self.stacks},
               "temperatures": {stack: 300.0 for stack in self.stacks}}
        self.calls.append(row)
        return row


def request(request_id, arrival=0):
    return {"request_id": request_id, "stack": "hbf0", "stack_local_page": 0,
            "route": "direct", "bytes": 64, "arrival_ns": arrival, "operation": "read"}


class ClosedLoopTests(unittest.TestCase):
    def build(self, *, end=100, raw_offset=0, policy=None, thermal_states=None,
              maintenance="UNSUPPORTED_CAPABILITY", budget=1024,
              fabric_class=BasicFabric):
        fixture = engineering_fixture("all_hbf_direct", 64)
        fabric = fabric_class(fixture["fabric"])
        stacks = sorted(fixture["fabric"]["hbf"])
        budgets = {stack: budget for stack in stacks}
        policy = policy or ReadRatePolicy(EngineeringProfile(profile_id="off", window_ns=100))
        endpoint = {f"{stack}:gpu-link": budget for stack in stacks}
        mqsim = FakeMqsim(raw_offset=raw_offset, maintenance=maintenance)
        thermal = FakeThermal(stacks, thermal_states)
        system = ClosedLoopCoordinator(
            "all_hbf_direct", mqsim, None, fabric, thermal, FakeEnergy(), policy,
            thermal_window_ns=100, experiment_end_ns=end,
            initial_stack_budget_bytes=budgets, shared_endpoint_caps_bytes=endpoint)
        return system, mqsim, thermal

    def test_empty_foreground_still_advances_cooling_windows(self):
        system, _, thermal = self.build(end=300)
        result = system.run([])
        self.assertEqual([(r["start_ns"], r["end_ns"]) for r in thermal.calls],
                         [(0, 100), (100, 200), (200, 300)])
        self.assertEqual(result["summary"]["offered_count"], 0)

    def test_submitted_request_drains_but_does_not_count_in_observation_window(self):
        system, _, _ = self.build(end=100)
        result = system.run([request("late", 90)])
        self.assertEqual(result["summary"]["observation_completed_count"], 0)
        self.assertEqual(result["summary"]["drain_completed_count"], 1)
        self.assertGreater(result["drain_end_ns"], result["observation_end_ns"])
        self.assertEqual([row["phase"] for row in result["timeline"]["energy"]
                          if row["kind"] == "WINDOW_TOTAL"],
                         ["OBSERVATION", "DRAIN"])

    def test_same_timestamp_thermal_shutdown_precedes_new_admission(self):
        profile = EngineeringProfile(profile_id="on", enabled=True, strategy="guard_only",
                                     window_ns=100, target_bytes_per_s=10_000_000_000,
                                     step_bytes=64, minimum_budget_bytes=0,
                                     maximum_budget_bytes=1024, severe_budget_bytes=64)
        system, mqsim, _ = self.build(end=200, policy=ReadRatePolicy(profile),
                                      thermal_states=["shutdown", "shutdown"])
        result = system.run([request("before", 0), request("at-boundary", 100)])
        self.assertEqual(len(mqsim.requests), 1)
        by_id = {row["request_id"]: row for row in result["requests"]}
        self.assertEqual(by_id["before"]["state"], "COMPLETE")
        self.assertTrue(by_id["at-boundary"]["censored"])
        self.assertIsNone(by_id["at-boundary"]["backend_submit_ns"])

    def test_raw_and_reported_mismatch_is_unsupported_composition(self):
        system, _, _ = self.build(raw_offset=-1)
        with self.assertRaisesRegex(UnsupportedComposition, "raw media and reported"):
            system.run([request("mismatch")])

    def test_unsupported_maintenance_is_reported_and_age_is_not_scaled(self):
        system, _, _ = self.build()
        maintenance = [{"request_id": 1001, "stack": "hbf0", "stack_local_page": 0,
                        "due_ns": 10, "initial_age_s": 86_399.5, "bytes": 64}]
        result = system.run([], maintenance)
        self.assertEqual(result["maintenance"][0]["state"], "UNSUPPORTED_CAPABILITY")
        self.assertEqual(result["maintenance"][0]["initial_age_s"], 86_399.5)
        self.assertEqual(result["summary"]["maintenance_unsupported"], 1)

    def test_inflight_maintenance_is_drained(self):
        system, _, _ = self.build(end=100, maintenance="ACCEPTED")
        maintenance = [{"request_id": 1001, "stack": "hbf0", "stack_local_page": 0,
                        "due_ns": 90, "initial_age_s": 86_400, "bytes": 64}]
        result = system.run([], maintenance)
        self.assertEqual(result["maintenance"][0]["state"], "COMMITTED")
        self.assertEqual(result["summary"]["maintenance_committed"], 1)
        self.assertGreater(result["drain_end_ns"], 100)

    def test_maintenance_waits_for_recovery_from_severe_guard(self):
        system, _, _ = self.build(end=300, maintenance="ACCEPTED",
                                  thermal_states=["severe", "normal", "normal"])
        maintenance = [{"request_id": 1001, "stack": "hbf0", "stack_local_page": 0,
                        "due_ns": 110, "initial_age_s": 86_400, "bytes": 64}]
        result = system.run([], maintenance)
        row = result["maintenance"][0]
        self.assertEqual(row["state"], "COMMITTED")
        self.assertEqual(row["submit_ns"], 200)
        self.assertTrue(any(event["phase"] == "THERMAL_BLOCKED"
                            for event in result["timeline"]["maintenance"]))

    def test_weight_metadata_and_effective_bytes_are_preserved(self):
        system, mqsim, _ = self.build(budget=64)
        weighted = request("weighted")
        weighted.update(valid_weight_bytes=16, global_page=9, global_byte_address=144,
                        logical_regions=["layer.0"], scan_index=2, local_page=1)
        result = system.run([weighted])
        row = result["requests"][0]
        self.assertEqual(row["valid_weight_bytes"], 16)
        self.assertEqual(row["global_page"], 9)
        self.assertEqual(row["logical_regions"], ["layer.0"])
        self.assertEqual(mqsim.requests[1]["bytes"], 64)
        self.assertEqual(result["summary"]["offered_effective_bytes"], 16)
        self.assertEqual(result["summary"]["offered_physical_bytes"], 64)
        rate = result["timeline"]["rates"][0]["stacks"][0]
        self.assertEqual(rate["offered_bytes"], 16)
        self.assertEqual(rate["physical_offered_bytes"], 64)

    def test_invalid_valid_weight_extent_is_rejected(self):
        system, _, _ = self.build()
        invalid = request("invalid")
        invalid["valid_weight_bytes"] = 65
        with self.assertRaisesRegex(ValueError, "must not exceed"):
            system.run([invalid])

    def test_admission_gate_still_reserves_physical_bytes(self):
        profile = EngineeringProfile(profile_id="physical-gate", enabled=True,
                                     strategy="guard_only", window_ns=100,
                                     target_bytes_per_s=1, step_bytes=1,
                                     minimum_budget_bytes=0, maximum_budget_bytes=16,
                                     severe_budget_bytes=0)
        system, mqsim, _ = self.build(budget=16, policy=ReadRatePolicy(profile))
        weighted = request("physical-gate")
        weighted["valid_weight_bytes"] = 8
        result = system.run([weighted])
        self.assertEqual(mqsim.requests, {})
        self.assertEqual(result["summary"]["censored_effective_bytes"], 8)
        self.assertEqual(result["summary"]["censored_physical_bytes"], 64)

    def test_trigger_and_postcommit_cleanup_failure_preserve_age_reset(self):
        system, mqsim, _ = self.build(maintenance="POSTCOMMIT_ERASE_FAILURE")
        maintenance = [{"request_id": 1001, "stack": "hbf0", "stack_local_page": 0,
                        "due_ns": 10, "initial_age_s": 86_399.5, "bytes": 64}]
        result = system.run([], maintenance)
        row = result["maintenance"][0]
        self.assertEqual(mqsim.maintenance_requests[1001]["trigger_reason"], "RETENTION_AGE_DUE")
        self.assertEqual(row["state"], "COMMITTED_CLEANUP_FAILED")
        self.assertTrue(row["mapping_committed"])
        self.assertIsInstance(row["age_reset_ns"], int)
        self.assertEqual(result["summary"]["maintenance_committed"], 1)
        self.assertEqual(result["summary"]["maintenance_failed"], 0)
        self.assertEqual(result["summary"]["maintenance_cleanup_failed_after_commit"], 1)

    def test_coordinator_prefers_incremental_fabric_observer(self):
        class IncrementalOnly(IncrementalBasicFabric):
            def events(self):
                raise AssertionError("full event snapshot must not be requested")

            def completions(self):
                raise AssertionError("full completion snapshot must not be requested")

        system, _, _ = self.build(fabric_class=IncrementalOnly)
        result = system.run([request("incremental")])
        self.assertEqual(result["summary"]["observation_completed_count"], 1)

    def test_safe_reclaim_deferred_is_not_cleanup_failure(self):
        system, _, _ = self.build(maintenance="COMMITTED_RECLAIM_DEFERRED")
        maintenance = [{"request_id": 1001, "stack": "hbf0", "stack_local_page": 0,
                        "due_ns": 10, "initial_age_s": 86_399.5, "bytes": 64}]
        result = system.run([], maintenance)
        row = result["maintenance"][0]
        self.assertEqual(row["state"], "COMMITTED")
        self.assertFalse(row["cleanup_failed"])
        self.assertEqual(result["summary"]["maintenance_committed"], 1)
        self.assertEqual(result["summary"]["maintenance_cleanup_failed_after_commit"], 0)


if __name__ == "__main__":
    unittest.main()
