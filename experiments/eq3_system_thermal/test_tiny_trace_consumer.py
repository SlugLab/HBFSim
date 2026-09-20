import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from causal_workload import (CausalExecutor, TINY_TRACE_ORIGIN, TRACE_ORIGIN,
                             build_architecture_trace)


ARTIFACT = Path(__file__).parent / "stage/traces/tiny_qwen2_cpu_trace_v1/trace.json"
TRACE_SHA = "feea4657abc5e7b981208b23b8d2fff9836194694c5c3dec69c6bf59bbe627a7"


def config(model="Qwen/Qwen2.5-7B-Instruct", mode="tiny_cpu_template"):
    result = {
        "model_id": model, "batch_intervals": 1, "batch_size": 2,
        "batch_interval_ns": 100, "prefetch_layers": 2,
        "attention_compute_ns_per_token": 3,
        "mlp_compute_ns_per_token": 5,
        "output_compute_ns_per_token": 7,
        "embedding_access": "full_weight_stress",
        "dependency_mode": mode,
    }
    if mode == "tiny_cpu_template":
        result.update({"tiny_trace_path": str(ARTIFACT),
                       "tiny_trace_sha256": TRACE_SHA,
                       "projection_context_tokens": 4096})
    return result


class TinyTraceConsumerTests(unittest.TestCase):
    def test_on_demand_has_no_hidden_within_layer_prefetch(self):
        cfg=config();cfg.update(prefetch_layers=0,prefetch_mode="on_demand")
        trace=build_architecture_trace(cfg)
        tasks={t['task_id']:t for t in trace['batches'][0]['tasks']}
        self.assertEqual(tasks['interval0:l0:attn_read']['issue_after'],['interval0:embed_read'])
        self.assertEqual(tasks['interval0:l0:mlp_read']['issue_after'],['interval0:l0:attn_compute'])
        self.assertEqual(tasks['interval0:l1:attn_read']['issue_after'],['interval0:l0:mlp_compute'])
        cfg['prefetch_layers']=1
        with self.assertRaises(ValueError):build_architecture_trace(cfg)

    def test_validated_template_drives_target_dependencies_and_shapes(self):
        for model, layers, payload in (
                ("Qwen/Qwen2.5-7B-Instruct", 28, 15_231_233_024),
                ("Qwen/Qwen2.5-72B-Instruct", 80, 145_412_407_296)):
            trace = build_architecture_trace(config(model))
            self.assertEqual(trace["trace_origin"], TINY_TRACE_ORIGIN)
            provenance = trace["structure_provenance"]
            self.assertEqual(provenance["trace_sha256"], TRACE_SHA)
            projection = provenance["target_projection"]
            self.assertEqual(projection["layer_count"], layers)
            self.assertEqual(projection["tensor_payload_bytes"], payload)
            self.assertGreater(projection["analytical_total_macs_per_token_at_context"], 0)
            for region in projection["logical_regions"]:
                elements = sum(self._product(shape) for shape in region["weight_shapes"])
                self.assertEqual(elements * 2, region["bytes"])
                self.assertEqual(region["logical_address_bytes"] % (1024 * 1024), 0)
            tasks = trace["batches"][0]["tasks"]
            for layer in range(layers):
                attention = next(row for row in tasks
                                 if row["task_id"] == f"interval0:l{layer}:attn_compute")
                mlp = next(row for row in tasks
                           if row["task_id"] == f"interval0:l{layer}:mlp_compute")
                self.assertEqual(attention["structure_role"],
                                 "ATTENTION_AFTER_INPUT_AND_WEIGHT_READ")
                self.assertEqual(mlp["depends_on"],
                                 [f"interval0:l{layer}:attn_compute",
                                  f"interval0:l{layer}:mlp_read"])
            head = next(row for row in tasks if row["task_id"] == "interval0:head_read")
            self.assertEqual(head["structure_template_op_ids"],
                             ["prefill:op282:lm_head"])

    @staticmethod
    def _product(shape):
        result = 1
        for value in shape:
            result *= value
        return result

    def test_executor_consumes_template_trace_without_changing_compute_cost(self):
        trace = build_architecture_trace(config())
        compute = [row for row in trace["batches"][0]["tasks"] if row["type"] == "compute"]
        self.assertEqual(next(row for row in compute if row["task_id"].endswith("l0:attn_compute"))[
            "duration_ns"], 6)
        executor = CausalExecutor(trace, {
            "cache_mode": "disabled", "cache_capacity_bytes": 0,
            "coalescing_enabled": True, "prefetch_wait_mode": "wait_at_consumption",
            "migration_mode": "fixed", "stripe_unit_bytes": 4096,
            "default_placement": {"stack": "hbf0", "channel": "0", "route": "direct"},
        })
        self.assertTrue(executor.poll(0))

    def test_existing_synthetic_mode_remains_explicit(self):
        trace = build_architecture_trace(config(mode="synthetic_metadata_dag"))
        self.assertEqual(trace["trace_origin"], TRACE_ORIGIN)
        self.assertEqual(trace["dependency_mode"], "synthetic_metadata_dag")
        self.assertIsNone(trace["structure_provenance"])
        self.assertFalse(any("structure_template_op_ids" in row
                             for row in trace["batches"][0]["tasks"]))

    def test_checksum_and_causal_dependency_tampering_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "trace.json"
            document = json.loads(ARTIFACT.read_text())
            operation = next(row for row in document["operations"]
                             if row["name"] == "layer0.rope_gqa_causal_attention")
            operation["depends_on"] = ["missing"]
            payload = dict(document)
            payload.pop("trace_sha256")
            checksum = hashlib.sha256(json.dumps(
                payload, sort_keys=True, separators=(",", ":"), allow_nan=False
            ).encode()).hexdigest()
            document["trace_sha256"] = checksum
            path.write_text(json.dumps(document))
            bad = config()
            bad["tiny_trace_path"] = str(path)
            bad["tiny_trace_sha256"] = checksum
            with self.assertRaisesRegex(ValueError, "dependency"):
                build_architecture_trace(bad)


if __name__ == "__main__":
    unittest.main()
