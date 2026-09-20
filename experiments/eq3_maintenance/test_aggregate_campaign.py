import unittest
import json
import tempfile
from pathlib import Path

from aggregate_campaign import aggregate, pair_identity, scenario_key


class AggregateCampaignTests(unittest.TestCase):
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
            self.assertEqual(rows[0]["execution_state"],"NOT_STARTED_SUPERSEDED_V1")
            self.assertEqual(rows[0]["launcher_exit_code"],75)
            self.assertEqual(rows[0]["functional_state"],"NOT_EVALUATED")


if __name__ == "__main__":
    unittest.main()
