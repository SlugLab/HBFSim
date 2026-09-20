import json
from pathlib import Path
import tempfile
import unittest

from model_workloads import build_workload, main


def config(**updates):
    value = {
        "schema_version": "eq3-rate-model-workload-config-v1",
        "model_id": "Qwen/Qwen2.5-7B-Instruct",
        "full_scans_per_s": 16,
        "pattern": "continuous",
        "stack_count": 4,
        "channels_per_stack": 16,
        "step_ns": 20_000_000,
        "active_ns": 1_000_000_000,
        "recovery_ns": 40_000_000,
    }
    value.update(updates)
    return value


class ModelWorkloadTests(unittest.TestCase):
    def test_continuous_exact_bytes_uniform_stripe_and_recovery(self):
        result = build_workload(config())
        expected = 15_231_233_024 * 16
        self.assertEqual(result["metadata"]["actual_total_offered_bytes"], expected)
        self.assertEqual(result["metadata"]["equivalent_full_scans"], 16)
        self.assertEqual(len(result["windows"]), 52)
        self.assertTrue(all(window["total_offered_bytes"] == 0 for window in result["windows"][-2:]))
        cumulative = {f"hbf{s}": {str(c): 0 for c in range(16)} for s in range(4)}
        for window in result["windows"]:
            self.assertEqual(window["end_ns"] - window["start_ns"], 20_000_000)
            for stack, channels in window["stack_channel_offered_bytes"].items():
                self.assertEqual(set(channels), {str(c) for c in range(16)})
                for channel, byte_count in channels.items():
                    self.assertIsInstance(byte_count, int)
                    self.assertGreaterEqual(byte_count, 0)
                    cumulative[stack][channel] += byte_count
        totals = [value for channels in cumulative.values() for value in channels.values()]
        self.assertLessEqual(max(totals) - min(totals), 1)
        self.assertEqual(sum(totals), expected)

    def test_burst_has_same_mean_and_twice_then_zero_pattern(self):
        continuous = build_workload(config(recovery_ns=0))
        burst = build_workload(config(pattern="burst_equal_mean", recovery_ns=0,
                                      burst_period_ns=200_000_000,
                                      burst_on_ns=100_000_000))
        self.assertEqual(continuous["metadata"]["actual_total_offered_bytes"],
                         burst["metadata"]["actual_total_offered_bytes"])
        multipliers = [window["demand_multiplier"] for window in burst["windows"][:10]]
        self.assertEqual(multipliers, [2] * 5 + [0] * 5)

    def test_all_models_stack_counts_and_high_pressure_are_canonical(self):
        expected = {
            "Qwen/Qwen2.5-7B-Instruct": 15_231_233_024,
            "Qwen/Qwen2.5-72B-Instruct": 145_412_407_296,
            "Qwen/Qwen3-235B-A22B": 470_187_269_120,
        }
        for model_id, payload in expected.items():
            for stack_count in (4, 8):
                with self.subTest(model=model_id, stacks=stack_count):
                    result = build_workload(config(model_id=model_id, stack_count=stack_count,
                                                   full_scans_per_s=32, recovery_ns=0))
                    self.assertEqual(result["metadata"]["weight_bytes"], payload)
                    self.assertEqual(result["metadata"]["actual_total_offered_bytes"], payload * 32)
                    self.assertEqual(len(result["windows"][0]["stack_channel_offered_bytes"]),
                                     stack_count)
        qwen3 = build_workload(config(model_id="Qwen/Qwen3-235B-A22B", recovery_ns=0))
        self.assertEqual(qwen3["metadata"]["semantics"]["token_per_s"], "UNKNOWN")
        self.assertIn("DO_NOT_INTERPRET", qwen3["metadata"]["moe_limit"])
        self.assertEqual(qwen3["metadata"]["semantics"]["capacity"],
                         "NOT_APPLIED_HERE_EXCESS_MUST_ENTER_FLUID_BACKLOG")

    def test_rejects_unfrozen_or_malformed_inputs(self):
        cases = [
            {"full_scans_per_s": 17},
            {"stack_count": 5},
            {"channels_per_stack": 8},
            {"step_ns": 10_000_000},
            {"pattern": "burst_equal_mean", "burst_period_ns": 180_000_000,
             "burst_on_ns": 100_000_000},
            {"unknown": 1},
        ]
        for update in cases:
            with self.subTest(update=update), self.assertRaises(ValueError):
                build_workload(config(**update))

    def test_cli_writes_canonical_json(self):
        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            source = directory / "config.json"
            output = directory / "workload.json"
            source.write_text(json.dumps(config(recovery_ns=0)))
            self.assertEqual(main(["--config", str(source), "--output", str(output)]), 0)
            result = json.loads(output.read_text())
            self.assertEqual(result["schema_version"], "eq3-rate-model-workload-v1")
            self.assertEqual(result["canonical_config"], config(recovery_ns=0))


if __name__ == "__main__":
    unittest.main()
