from __future__ import annotations

import json
import pathlib
import tempfile
import unittest

from model_inventory import ModelInventory
from trace_replay import TraceAccess, capacity_geometry, replay_cell


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


if __name__ == "__main__":
    unittest.main()
