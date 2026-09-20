import copy
import unittest

from tiny_cpu_trace import build_trace


CONFIG = {
    "schema_version": "eq3-tiny-qwen2-cpu-trace-config-v1",
    "seed": 20260920,
    "vocab_size": 128,
    "hidden_size": 56,
    "layers": 28,
    "num_attention_heads": 28,
    "num_key_value_heads": 4,
    "intermediate_size": 128,
    "rms_norm_eps": 1e-6,
    "rope_theta": 10000.0,
    "prompt_token_ids": [1, 7, 11, 19],
    "decode_tokens": 2,
    "projection_context_tokens": 4096,
}


class TinyCpuTraceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.trace = build_trace(copy.deepcopy(CONFIG))

    def test_forward_structure_and_gqa(self):
        self.assertEqual([x["forward_id"] for x in self.trace["forwards"]],
                         ["prefill", "decode0", "decode1"])
        self.assertEqual([x["context_tokens_after"] for x in self.trace["forwards"]],
                         [4, 5, 6])
        attention = [x for x in self.trace["operations"]
                     if x["name"].endswith("rope_gqa_causal_attention")]
        self.assertEqual(len(attention), 28 * 3)
        self.assertEqual(attention[0]["details"]["q_shape"], [4, 28, 2])
        self.assertEqual(attention[0]["details"]["kv_shape"], [4, 4, 2])
        self.assertEqual(attention[-1]["details"]["context_tokens"], 6)

    def test_accesses_are_real_arrays_and_dependencies_are_ordered(self):
        op_sequence = {row["op_id"]: row["sequence"] for row in self.trace["operations"]}
        for row in self.trace["operations"]:
            for dependency in row["depends_on"]:
                if dependency != "token_ids":
                    self.assertLess(op_sequence[dependency], row["sequence"])
        self.assertTrue(self.trace["weight_accesses"])
        self.assertTrue(all(row["access_bytes"] > 0 for row in self.trace["weight_accesses"]))
        self.assertEqual(self.trace["weight_accesses"][0]["weight_name"],
                         "model.embed_tokens.weight")

    def test_official_target_projection_is_independent_and_exact(self):
        expected = {"Qwen/Qwen2.5-7B-Instruct": 15_231_233_024,
                    "Qwen/Qwen2.5-72B-Instruct": 145_412_407_296}
        for model, byte_count in expected.items():
            row = self.trace["target_projection"][model]
            self.assertEqual(row["tensor_payload_bytes"], byte_count)
            self.assertEqual(sum(x["payload_bytes"] for x in row["logical_regions"]), byte_count)
            self.assertGreater(row["analytical_macs_per_token_at_context"], 0)
            addresses = [x["logical_address_bytes"] for x in row["logical_regions"]]
            self.assertTrue(all(value % (1024 * 1024) == 0 for value in addresses))

    def test_deterministic_checksum(self):
        again = build_trace(copy.deepcopy(CONFIG))
        self.assertEqual(self.trace["trace_sha256"], again["trace_sha256"])


if __name__ == "__main__":
    unittest.main()
