"""Small explicit GGUF fixtures test metadata contracts, never measured data."""
import tempfile
import unittest
from pathlib import Path

import gguf
import numpy as np

from inventory_checkpoint import identity, inventory_checkpoint, validate_inventory
from budget_fast_tier import budget_fast_tier


class InventoryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "TEST_ONLY.gguf"

    def write(self, missing=False, experts=2):
        writer = gguf.GGUFWriter(self.path, "qwen3moe")
        writer.add_block_count(2)
        writer.add_expert_count(experts)
        writer.add_expert_used_count(1)
        writer.add_head_count_kv(1)
        writer.add_key_length(2)
        writer.add_value_length(2)
        writer.add_context_length(32)
        writer.add_tensor("token_embd.weight", np.zeros((4, 2), dtype=np.float16))
        for layer in range(2):
            for projection in ("up", "gate", "down"):
                if missing and (layer, projection) == (1, "down"):
                    continue
                writer.add_tensor(f"blk.{layer}.ffn_{projection}_exps.weight",
                                  np.zeros((2, 2, 2), dtype=np.float16))
            writer.add_tensor(f"blk.{layer}.attn_q.weight",
                              np.zeros((2, 2), dtype=np.float16))
        writer.write_header_to_file()
        writer.write_kv_data_to_file()
        writer.write_tensors_to_file()
        writer.close()

    def test_real_file_metadata_and_complete_experts(self):
        self.write()
        inv = inventory_checkpoint(self.path, page_bytes=16)
        self.assertEqual((inv["layers"], inv["E"], inv["k"]), (2, 2, 1))
        self.assertEqual(inv["tensor_bytes"], 128)
        self.assertEqual(inv["eligible_expert_bytes"], 96)
        self.assertEqual(inv["resident_non_offloaded_bytes"], 32)
        self.assertEqual(len(inv["experts"]), 4)
        self.assertEqual(sum(e["bytes"] for e in inv["experts"]), 96)
        self.assertEqual(inv["weight_payload_hash"], None)
        validate_inventory(inv)

    def test_missing_projection_rejected(self):
        self.write(missing=True)
        with self.assertRaisesRegex(ValueError, "projection"):
            inventory_checkpoint(self.path)

    def test_metadata_shape_disagreement_rejected(self):
        self.write(experts=3)
        with self.assertRaisesRegex(ValueError, "expert.*dimension"):
            inventory_checkpoint(self.path)

    def test_truncated_payload_rejected_without_reading_weights(self):
        self.write()
        with self.path.open("r+b") as output:
            # GGUFWriter pads the tail to 32 bytes; remove payload as well.
            output.truncate(self.path.stat().st_size - 40)
        with self.assertRaises((ValueError, TypeError)):
            inventory_checkpoint(self.path)

    def test_budget_deductions_and_legacy_separation(self):
        self.write()
        inv = inventory_checkpoint(self.path, page_bytes=16)
        result = budget_fast_tier(inv, fast_bytes=512, active_sequences=3,
                                 context_tokens=5, kv_element_bytes=2,
                                 workspace_bytes=64, safety_bytes=128,
                                 legacy_ratio=0.9)
        self.assertEqual(result["kv_bytes"], 240)
        self.assertEqual(result["C_fast_effective"], 48)
        self.assertEqual(result["rho"], 0.5)
        self.assertEqual(result["legacy_ratio"], 0.9)
        self.assertEqual(result["fast_pages"], 3)
        self.assertEqual(result["achieved_rho"], 24 / 96)
        self.assertEqual(result["unused_bytes"], 16)
        self.assertEqual(result["cache_validation"], "NOT_EXECUTED")

    def test_impossible_budget_and_corrupt_inventory_rejected(self):
        self.write()
        inv = inventory_checkpoint(self.path)
        with self.assertRaisesRegex(ValueError, "budget"):
            budget_fast_tier(inv, fast_bytes=1, active_sequences=1,
                            context_tokens=1, kv_element_bytes=2,
                            workspace_bytes=0, safety_bytes=0)
        inv["eligible_expert_bytes"] += 1
        with self.assertRaises(ValueError):
            validate_inventory(inv)

    def test_segment_identity_and_embedded_config_cannot_drift(self):
        self.write()
        for field in ("segment", "kv", "top_k"):
            with self.subTest(field=field):
                inv = inventory_checkpoint(self.path)
                if field == "segment":
                    inv["experts"][1]["segments"][0]["source_offset"] = inv["experts"][0]["segments"][0]["source_offset"]
                elif field == "kv":
                    inv["kv_shape"]["heads_kv"] += 1
                else:
                    inv["k"] = 2
                with self.assertRaises(ValueError):
                    validate_inventory(inv)

    def test_loaded_layout_contract_is_revalidated(self):
        self.write()
        for field in ("dtype", "shape", "unknown_projection", "category"):
            with self.subTest(field=field):
                inv = inventory_checkpoint(self.path)
                tensor = next(t for t in inv["tensors"] if t["category"] == "eligible_expert")
                if field == "dtype":
                    tensor["dtype"] = "Q4_0"
                elif field == "shape":
                    tensor["shape"][0] += 1
                elif field == "unknown_projection":
                    tensor = next(t for t in inv["tensors"] if t["category"] == "other_resident")
                    tensor["name"] = "blk.0.ffn_extra_exps.weight"
                else:
                    tensor = next(t for t in inv["tensors"] if t["category"] == "attention")
                    tensor["category"] = "other_resident"
                inv["tensor_manifest_sha256"] = identity(inv["tensors"])
                with self.assertRaises(ValueError):
                    validate_inventory(inv)

    def test_padded_experts_do_not_overstate_capacity(self):
        self.write()
        inv = inventory_checkpoint(self.path, page_bytes=32)
        result = budget_fast_tier(inv, fast_bytes=128, active_sequences=1,
                                 context_tokens=1, kv_element_bytes=1,
                                 workspace_bytes=0, safety_bytes=0)
        self.assertEqual(result["fast_pages"], 2)
        self.assertEqual(result["achieved_rho"], 0.5)
        self.assertEqual(result["budget_covered_bytes"], 48)

    def test_no_complete_expert_fits_is_infeasible(self):
        self.write()
        inv = inventory_checkpoint(self.path, page_bytes=16)
        result = budget_fast_tier(inv, fast_bytes=48, active_sequences=1,
                                 context_tokens=1, kv_element_bytes=1,
                                 workspace_bytes=0, safety_bytes=0)
        self.assertEqual(result["placement_status"], "INFEASIBLE_NO_WHOLE_EXPERT_FITS")
        self.assertEqual(result["selected_experts"], [])


if __name__ == "__main__":
    unittest.main()
