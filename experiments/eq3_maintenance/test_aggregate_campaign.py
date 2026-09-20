import unittest
import csv
import json
import tempfile
from pathlib import Path

from aggregate_campaign import aggregate, pair_identity, scenario_key, _native_coverage, _cost_metrics


class AggregateCampaignTests(unittest.TestCase):
    def test_native_die_is_channel_local_in_both_geometry_profiles(self):
        for channels, count, transactions, expected in [
            ([8], 16, [(8, 0), (8, 15)], [0, 15]),
            ([16, 17], 1, [(16, 0), (17, 0)], [0, 1]),
        ]:
            with self.subTest(channels=channels), tempfile.TemporaryDirectory() as directory:
                point = Path(directory)
                (point / "stack-map.json").write_text(json.dumps({
                    "dies_per_channel": count, "stacks": [{"id": "hbf1", "channels": channels}]}))
                with (point / "native.csv").open("w") as stream:
                    writer = csv.DictWriter(stream, fieldnames=["transactions"])
                    writer.writeheader()
                    writer.writerow({"transactions": json.dumps([
                        dict(stack="hbf1", channel=c, chip=0, die=d) for c, d in transactions])})
                self.assertEqual(_native_coverage(point), {"hbf1": expected})

    def test_deadline_miss_uses_commit_and_preserves_unreset_age(self):
        with tempfile.TemporaryDirectory() as directory:
            point = Path(directory)
            (point / "requests.csv").write_text("phase,stack,external_wait_ns,backend_latency_ns,fabric_latency_ns\nFINAL_COMPLETE,hbf0,10,20,3\n")
            with (point / "maintenance.csv").open("w") as stream:
                fields = ["request_id", "deadline_ns", "due_ns", "submit_ns", "mapping_committed", "age_reset_ns", "initial_age_s", "backend_status"]
                writer = csv.DictWriter(stream, fieldnames=fields); writer.writeheader()
                writer.writerows([
                    dict(request_id=1, deadline_ns=100, due_ns=50, mapping_committed=True, age_reset_ns=90, initial_age_s=20),
                    dict(request_id=2, deadline_ns=100, due_ns=50, mapping_committed=True, age_reset_ns=120, initial_age_s=20),
                    dict(request_id=3, deadline_ns=100, due_ns=50, mapping_committed=False, initial_age_s=30),
                    dict(request_id=4, deadline_ns=100, due_ns=50, mapping_committed=False, initial_age_s=20, backend_status="REJECTED_UNMAPPED"),
                    dict(request_id=5, deadline_ns=100, due_ns=50, submit_ns=150, mapping_committed=False, initial_age_s=20, backend_status="REJECTED_INVALID_TARGET"),
                ])
            actual = _cost_metrics(point, 200)
            self.assertEqual(actual["maintenance_deadline_missed_by_end"], 2)
            self.assertEqual(actual["maintenance_rejected_without_service"], 2)
            self.assertEqual(actual["maintenance_rejected_after_deadline"], 1)
            self.assertEqual(actual["maintenance_declared_unfulfilled_by_deadline"], 4)
            self.assertEqual(actual["maintenance_due_to_commit_max_ns"], 70)
            self.assertAlmostEqual(actual["maintenance_declared_age_peak_s"], 30.0000002)
            self.assertEqual(actual["hbf_backend_latency_ns_p95"], 20)

    def test_pair_identity_ignores_policy_profile_but_not_workload(self):
        base = {"mode": "mixed_direct", "workload": "W1", "active_ns": 10, "end_ns": 20,
                "weight_model": "model", "thermal_model_dir": "/model",
                "executable_sha256": {"backend": "a", "thermal": "b"},
                "input_sha256": {"requests-input.json": "same", "configuration.json": "cfg",
                                 "energy-profile.json": "energy", "policy-profile.json": "p0"}}
        other_policy = {**base, "input_sha256": {**base["input_sha256"],
                                                  "policy-profile.json": "p2"}}
        changed_trace = {**base, "input_sha256": {**base["input_sha256"],
                                                   "requests-input.json": "different"}}
        self.assertEqual(pair_identity(base)[0], pair_identity(other_policy)[0])
        self.assertNotEqual(pair_identity(base)[0], pair_identity(changed_trace)[0])

    def test_scenario_key_excludes_policy_and_includes_model_duration(self):
        base = {"id": "a", "workload": "W1", "mode": "relay", "scene": "Safe",
                "policy": "guard_only", "config": {"active_s": .6, "recovery_s": .4,
                                                     "weight_model": "7B"}}
        other_policy = {**base, "policy": "read_rate_feedback_thermal_guard_v1"}
        other_model = {**base, "config": {**base["config"], "weight_model": "72B"}}
        self.assertEqual(scenario_key(base), scenario_key(other_policy))
        self.assertNotEqual(scenario_key(base), scenario_key(other_model))

    def test_not_started_receipt_overrides_launcher_failure(self):
        spec={"id":"point","workload":"W1","mode":"relay","scene":"Safe",
              "policy":"guard_only","config":{"weight_model":"7B"}}
        with tempfile.TemporaryDirectory() as directory:
            point=Path(directory)/"point";point.mkdir()
            (point/"NOT_STARTED.json").write_text(json.dumps({
                "execution_status":"NOT_STARTED","reason":{"reason":"PROFILE_REVISED"}}))
            rows,_=aggregate({"points":[spec]},Path(directory),{
                "points":[{"id":"point","status":"FAILED","exit_code":75}]})
            self.assertEqual(rows[0]["execution_state"],"NOT_STARTED")
            self.assertEqual(rows[0]["launcher_exit_code"],75)
            self.assertEqual(rows[0]["functional_state"],"NOT_EVALUATED")


if __name__ == "__main__":
    unittest.main()
