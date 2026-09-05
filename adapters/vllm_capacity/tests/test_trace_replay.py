from __future__ import annotations

import json
import pathlib
import tempfile
import unittest

from model_inventory import ModelInventory
from trace_replay import TraceAccess, capacity_geometry, replay_cell
from trace_validation import validate_trace


def tensor(name: str, begin: int, shape: list[int]) -> dict[str, object]:
    size = 8
    return {
        "tensor": name,
        "source_shard": "model.safetensors",
        "source_shard_sha256": "a" * 64,
        "file_offset_begin": begin,
        "file_offset_end": begin + size,
        "bytes": size,
        "dtype": "BF16",
        "shape": shape,
    }


class TraceReplayTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        path = pathlib.Path(self.temporary.name) / "manifest.json"
        experts = []
        offset = 0
        for expert in range(2):
            experts.append(
                {
                    "layer": 0,
                    "expert_id": expert,
                    "total_bytes": 16_384,
                    "w13": {
                        "bytes": 16,
                        "tensors": [
                            tensor(f"e{expert}.gate", offset, [2, 2]),
                            tensor(f"e{expert}.up", offset + 8, [2, 2]),
                        ],
                    },
                    "w2": {
                        "bytes": 16_368,
                        "tensor": {
                            **tensor(f"e{expert}.down", offset + 16, [2, 2]),
                            "file_offset_end": offset + 16_384,
                            "bytes": 16_368,
                        },
                    },
                }
            )
            offset += 16_384
        path.write_text(
            json.dumps(
                {
                    "ModelFingerprint": "b" * 64,
                    "configuration": {
                        "num_hidden_layers": 1,
                        "num_experts": 2,
                        "num_experts_per_tok": 1,
                    },
                    "expert_weight_bytes": 32_768,
                    "expert_weights": experts,
                }
            ),
            encoding="utf-8",
        )
        self.inventory = ModelInventory(path)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_geometry_rounds_to_whole_experts(self) -> None:
        geometry = capacity_geometry(self.inventory, (1, 4), 16_384)
        self.assertEqual(geometry["complete_expert_slots"], 0)
        self.assertEqual(geometry["actual_hbm_bytes"], 0)
        self.assertIsNone(geometry["achieved_ratio_hbf_over_hbm"])

    def test_conservation_and_null_denominators(self) -> None:
        accesses = [
            TraceAccess((0, value), "prefill", "r", 0, index, 0)
            for index, value in enumerate((0, 1, 0))
        ]
        geometry = capacity_geometry(self.inventory, (1, 1), 16_384)
        result = replay_cell(
            accesses,
            self.inventory,
            {
                "name": "test",
                "read_latency_ns": 10,
                "aggregate_bandwidth_bytes_per_s": 1_000_000_000,
            },
            {"trace_fingerprint": "c" * 64},
            geometry,
            "lru",
            "d" * 40,
            "e" * 64,
            0,
        )
        capacity = result["stats_v2"]["capacity"]
        self.assertEqual(capacity["hits"] + capacity["misses"], 3)
        self.assertTrue(all(result["conservation"].values()))
        self.assertEqual(capacity["hbf_read_bytes"], capacity["misses"] * 16_384)

    def test_profile_contents_bind_cell_identity(self) -> None:
        accesses = [TraceAccess((0, 0), "prefill", "r", 0, 0, 0)]
        geometry = capacity_geometry(self.inventory, (1, 1), 16_384)
        results = []
        for latency in (10, 1_000_000):
            profile = {"name": "same-name", "read_latency_ns": latency,
                       "aggregate_bandwidth_bytes_per_s": 1_000_000_000}
            results.append(replay_cell(
                accesses, self.inventory, profile,
                {"trace_fingerprint": "c" * 64}, geometry, "lru",
                "d" * 40, "e" * 64, 0))
        self.assertNotEqual(results[0]["cell_id"], results[1]["cell_id"])
        for result, latency in zip(results, (10, 1_000_000)):
            self.assertEqual(result["cell_manifest"]["hbf_profile_config"]
                             ["read_latency_ns"], latency)
            self.assertEqual(len(result["cell_manifest"]["hbf_profile_sha256"]), 64)

    def validation_fixture(self):
        work = pathlib.Path(self.temporary.name)
        summary = work / "summary.json"
        summary.write_text(json.dumps({
            "protocol": {"num_prompts": 1, "input_len": 1, "output_len": 1},
            "trace": {"event_count": 1},
        }))
        event = {"model_fingerprint": self.inventory.model_fingerprint,
                 "layer_id": 0, "topk_expert_ids": [0], "phase": "prefill",
                 "expert_tensors": self.inventory.compact_tensor_accesses(0, [0])}
        for sequence, item in enumerate(event["expert_tensors"]):
            item["access_order_sequence"] = sequence
        return work / "trace.jsonl", summary, event

    def test_valid_complete_tensor_trace(self) -> None:
        trace, summary, event = self.validation_fixture()
        trace.write_text(json.dumps(event) + "\n")
        result = validate_trace(trace, summary, self.inventory)
        self.assertTrue(all(result["validation"]["checks"].values()))

    def test_duplicate_tensor_cannot_replace_missing_tensor(self) -> None:
        trace, summary, event = self.validation_fixture()
        event["expert_tensors"][1] = {**event["expert_tensors"][0],
                                       "access_order_sequence": 1}
        trace.write_text(json.dumps(event) + "\n")
        with self.assertRaises(ValueError):
            validate_trace(trace, summary, self.inventory)

    def test_wrong_dtype_or_source_is_rejected(self) -> None:
        for field, value in (("dtype", "WRONG"), ("source_shard", "wrong"),
                             ("source_offset_begin", 100), ("expert_id", 1)):
            with self.subTest(field=field):
                trace, summary, event = self.validation_fixture()
                event["expert_tensors"][0][field] = value
                trace.write_text(json.dumps(event) + "\n")
                with self.assertRaises(ValueError):
                    validate_trace(trace, summary, self.inventory)


if __name__ == "__main__":
    unittest.main()
