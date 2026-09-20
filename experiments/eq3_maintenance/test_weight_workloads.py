import unittest

from weight_workloads import generate_weight_requests, model_metadata, weight_extent


MODEL_7B = "Qwen/Qwen2.5-7B-Instruct"
MODEL_72B = "Qwen/Qwen2.5-72B-Instruct"


class WeightWorkloadTests(unittest.TestCase):
    def test_official_metadata_values_and_unknown_resolved_revision(self):
        seven = model_metadata(MODEL_7B)
        seventy_two = model_metadata(MODEL_72B)
        self.assertEqual(15_231_233_024, seven["tensor_payload_bytes"])
        self.assertEqual(145_412_407_296, seventy_two["tensor_payload_bytes"])
        self.assertEqual("bfloat16", seven["dtype"])
        self.assertEqual("UNKNOWN", seven["resolved_commit"])
        self.assertEqual(28, seven["architecture"]["num_hidden_layers"])
        self.assertEqual(80, seventy_two["architecture"]["num_hidden_layers"])

    def test_extent_exact_pages_regions_and_four_stack_stripe(self):
        extent = weight_extent(MODEL_7B, 16_384, 4)
        self.assertEqual(929_641, extent["global_page_count"])
        self.assertEqual(5_120, extent["last_page_padding_bytes"])
        self.assertEqual(
            {"hbf0": 232_411, "hbf1": 232_410, "hbf2": 232_410, "hbf3": 232_410},
            extent["stack_page_counts"],
        )
        self.assertEqual("model.embed_tokens", extent["regions"][0]["id"])
        self.assertEqual("lm_head", extent["regions"][-1]["id"])
        self.assertEqual(28, len([x for x in extent["regions"] if x["kind"] == "attention"]))
        self.assertEqual(extent["tensor_payload_bytes"], extent["regions"][-1]["end_byte"])

    def test_72b_eight_stack_extent_has_no_page_padding(self):
        extent = weight_extent(MODEL_72B, 16_384, 8)
        self.assertEqual(8_875_269, extent["global_page_count"])
        self.assertEqual(0, extent["last_page_padding_bytes"])
        self.assertEqual(8_875_269, sum(extent["stack_page_counts"].values()))

    def test_finite_window_keeps_global_addresses_and_read_only_mix(self):
        result = generate_weight_requests(
            MODEL_7B, request_count=4, start_page=700_000, period_ns=250,
            stacks=4, start_time_ns=1_000,
        )
        self.assertEqual([700_000, 700_001, 700_002, 700_003],
                         [x["global_page"] for x in result["requests"]])
        self.assertEqual([0, 1, 2, 3],
                         [int(x["stack"][3:]) for x in result["requests"]])
        self.assertEqual(700_000 * 16_384,
                         result["requests"][0]["global_byte_address"])
        self.assertEqual([1_000, 1_250, 1_500, 1_750],
                         [x["arrival_ns"] for x in result["requests"]])
        self.assertEqual({"read_fraction": 1.0, "write_fraction": 0.0},
                         result["operation_mix"])
        self.assertAlmostEqual(4 / 929_641,
                               result["coverage"]["equivalent_full_page_scans"])

    def test_full_model_wrap_records_scan_without_small_window_modulo(self):
        result = generate_weight_requests(
            MODEL_7B, request_count=3, start_page=929_640, period_ns=1, stacks=4,
        )
        self.assertEqual([929_640, 0, 1], [x["global_page"] for x in result["requests"]])
        self.assertEqual([0, 1, 1], [x["scan_index"] for x in result["requests"]])
        self.assertEqual(11_264, result["requests"][0]["valid_weight_bytes"])
        self.assertEqual(3, result["coverage"]["unique_global_pages"])

    def test_layer_hotspot_retains_global_pages(self):
        extent = weight_extent(MODEL_7B)
        layer = [x for x in extent["regions"] if x["id"] == "model.layers.9.self_attn"][0]
        result = generate_weight_requests(
            MODEL_7B, request_count=2,
            start_page=layer["first_global_page"], period_ns=10,
            region="model.layers.9.self_attn",
        )
        self.assertEqual(layer["first_global_page"], result["requests"][0]["global_page"])
        self.assertGreater(result["requests"][0]["global_page"], 0)
        self.assertIn("model.layers.9.self_attn", result["requests"][0]["logical_regions"])

    def test_invalid_model_stack_and_region_fail_closed(self):
        with self.assertRaisesRegex(ValueError, "unknown model"):
            weight_extent("not/a/model")
        with self.assertRaisesRegex(ValueError, "4 or 8"):
            weight_extent(MODEL_7B, stacks=5)
        with self.assertRaisesRegex(ValueError, "unknown logical region"):
            generate_weight_requests(MODEL_7B, 1, 0, 1, region="missing")
        with self.assertRaisesRegex(ValueError, "outside selected global range"):
            generate_weight_requests(MODEL_7B, 1, 0, 1, region="model.layers.9")

    def test_zero_request_window_has_zero_coverage(self):
        result = generate_weight_requests(MODEL_7B, 0, 0, 1)
        self.assertEqual([], result["requests"])
        self.assertEqual(0.0, result["coverage"]["model_payload_fraction"])


if __name__ == "__main__":
    unittest.main()
