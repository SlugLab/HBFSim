"""Focused CPU controls for the route-only causal opportunity ledger."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

import budget_fast_tier as budget_module
import evaluation_inventory as inventory_module
import hf_route_prefetch_opportunity as target
from test_evaluation_inventory import hf_fixture
from verify_hf_metadata import canonical


def encoded(document):
    return canonical(document) + b"\n"


class HFRoutePrefetchOpportunityTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(
            prefix=".test-hf-route-opportunity-", dir=target.ROOT
        )
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        _, bundle = hf_fixture(self.base, top_k=1)
        self.snapshot = inventory_module.load_hf_snapshot(bundle)
        self.inventory = inventory_module.adapt_hf_inventory(
            self.snapshot, 16384
        )
        self.inventory_bytes = encoded(self.inventory)
        effective = self.inventory["eligible_expert_bytes"] // 16
        shape = self.inventory["kv_shape"]
        kv_bytes = (
            self.inventory["layers"] * shape["heads_kv"]
            * (shape["key_length"] + shape["value_length"]) * 2
        )
        self.budget = budget_module.budget_fast_tier(
            self.inventory,
            fast_bytes=(self.inventory["resident_non_offloaded_bytes"]
                        + kv_bytes + effective),
            active_sequences=1,
            context_tokens=1,
            kv_element_bytes=2,
            workspace_bytes=0,
            safety_bytes=0,
            legacy_ratio=None,
            hf_snapshot=self.snapshot,
            inventory_file_bytes=self.inventory_bytes,
        )
        self.real = self.routing("real", {
            (0, 0): 0, (0, 1): 1,
            (1, 0): 1, (1, 1): 1,
            (2, 0): 0, (2, 1): 0,
        })
        self.shuffled = self.routing("shuffled", {
            (0, 0): 1, (0, 1): 0,
            (1, 0): 0, (1, 1): 1,
            (2, 0): 0, (2, 1): 0,
        }, source_steps={0: (2, 0, 1), 1: (1, 2, 0)})
        self.real_bytes = encoded(self.real)
        self.shuffled_bytes = encoded(self.shuffled)
        binding = self.inventory["model_binding"]
        route_sha = "3" * 64
        route_array_sha = "5" * 64
        self.consistency = {
            "schema_version": 1,
            "status": "ROUTE_CONSISTENCY_ONLY",
            "evidence_class": "UNVALIDATED_ROUTING_CAPTURE",
            "effective_test_only": False,
            "scientific_validation_passed": False,
            "metadata": {
                "receipt_sha256": binding["receipt_sha256"],
                "complete_sha256": binding["complete_sha256"],
                "donor_sha256": binding["legacy_inventory_sha256"],
                "metadata_identity_sha256":
                    binding["metadata_identity_sha256"],
                "observation_identity_sha256":
                    binding["observation_identity_sha256"],
            },
            "checks": {"decoded_routes_equal": True},
            "arms": [
                {"arm": "native"},
                {"arm": "capture", "artifacts": {
                    "frozen-donor.json": binding["legacy_inventory_sha256"],
                    "raw-routes.npy": route_array_sha,
                    "routing.jsonl": route_sha,
                }},
                {"arm": "repeat"},
            ],
        }
        self.consistency_bytes = encoded(self.consistency)
        self.manifest = {
            "schema_version": 1,
            "provenance": "PROJECTED",
            "input_source_kind": "CAPTURED_ROUTE",
            "capture_validation": "NOT_CERTIFIED_BY_POSTPROCESSOR",
            "concurrency_kind": "TRACE_COMPOSED",
            "composition_seed": 0,
            "composition_rule": "ALIGN_DECODE_STEP_DROP_FINISHED_SEQUENCES",
            "inventory_sha256": binding["legacy_inventory_sha256"],
            "E": self.inventory["E"],
            "k": self.inventory["k"],
            "layers": self.inventory["layers"],
            "member_traces": ["tiny-request"],
            "input_members": [{
                "member_id": "tiny-request", "path": "/TEST_ONLY/routes.jsonl",
                "sha256": route_sha,
            }],
            "outputs": {
                "real": {"path": "real.json",
                         "sha256": hashlib.sha256(self.real_bytes).hexdigest()},
                "shuffled": {
                    "path": "shuffled.json",
                    "sha256": hashlib.sha256(self.shuffled_bytes).hexdigest(),
                },
            },
        }
        self.manifest_bytes = encoded(self.manifest)
        self.expected = target.ExpectedBindings(
            consistency_sha256=hashlib.sha256(
                self.consistency_bytes
            ).hexdigest(),
            routing_manifest_sha256=hashlib.sha256(
                self.manifest_bytes
            ).hexdigest(),
            legacy_inventory_sha256=binding["legacy_inventory_sha256"],
            capture_route_sha256=route_sha,
            capture_route_array_sha256=route_array_sha,
            real_sha256=hashlib.sha256(self.real_bytes).hexdigest(),
            shuffled_sha256=hashlib.sha256(self.shuffled_bytes).hexdigest(),
            member_id="tiny-request",
            decode_steps=3,
            prediction_nodes=5,
            matched_prediction_nodes=4,
        )

    def routing(self, series, experts, source_steps=None):
        source_steps = source_steps or {0: (0, 1, 2), 1: (0, 1, 2)}
        routes = []
        steps = []
        for step in range(3):
            for layer in range(2):
                routes.append({
                    "member": "tiny-request",
                    "token_step": step,
                    "source_token_step": source_steps[layer][step],
                    "layer_id": layer,
                    "topk_expert_ids": [experts[step, layer]],
                })
                steps.append({
                    "token_step": step, "layer_id": layer,
                    "active_sequences": 1,
                })
        return {
            "series": series,
            "provenance": "PROJECTED",
            "routes": routes,
            "steps": steps,
            "reuse": [],
            "layers": [{"layer_id": 0}, {"layer_id": 1}],
        }

    def snapshots(self):
        return {
            "inventory": self.inventory_bytes,
            "budget": encoded(self.budget),
            "consistency": self.consistency_bytes,
            "routing_manifest": self.manifest_bytes,
            "real": self.real_bytes,
            "shuffled": self.shuffled_bytes,
        }

    def test_coherent_tiny_oracle_separates_prediction_horizon(self):
        result = target.build_ledger(
            self.snapshots(), hf_snapshot=self.snapshot,
            expected=self.expected,
        )
        self.assertEqual(result["status"],
                         "PROJECTED_ROUTE_ONLY_CAUSAL_CONTROL")
        self.assertFalse(result["scientific_validation_passed"])
        self.assertEqual(
            {(row["bytes"], row["packed_logical_pages"])
             for row in self.inventory["experts"]},
            {(49152, 3)},
        )
        self.assertEqual(
            (self.inventory["page_bytes"],
             self.budget["C_fast_effective"],
             self.budget["page_aligned_effective_bytes"]),
            (16384, 12288, 0),
        )
        expected_rows = {
            "real": [
                ((0, 0), None, None, None, None),
                ((1, 1), (0, 0), (0, 0, 0), (0, 1), None),
                ((0, 1), (1, 1), (0, 0, 1), (1, 1), (1, 1)),
                ((1, 1), (0, 1), (1, 1, 0), (0, 0), None),
                ((0, 0), (1, 1), (1, 1, 1), (1, 0), None),
                ((1, 0), (0, 0), (2, 2, 0), None, None),
            ],
            "shuffled": [
                ((0, 1), None, None, None, None),
                ((1, 0), (0, 1), (0, 2, 0), (0, 0), None),
                ((0, 0), (1, 0), (0, 1, 1), (1, 1), None),
                ((1, 1), (0, 0), (1, 0, 0), (0, 0), (0, 0)),
                ((0, 0), (1, 1), (1, 2, 1), (1, 0), None),
                ((1, 0), (0, 0), (2, 1, 0), None, None),
            ],
        }
        expected_nodes = [
            (0, 0, 0, 1), (0, 1, 1, 0), (1, 0, 1, 1),
            (1, 1, 2, 0), (2, 0, 2, 1), (2, 1, 3, 0),
        ]

        def compact(rows):
            return [(row["layer"], row["expert"]) for row in rows]

        for name in ("real", "shuffled"):
            report = result["series"][name]
            self.assertEqual((report["demand_nodes"],
                              report["prediction_bearing_nodes"],
                              report["matched_prediction_nodes"],
                             report["end_of_horizon_candidate_nodes"]),
                             (6, 5, 4, 1))
            self.assertEqual(
                (report["captured_overlap_expert_occurrences"],
                 report["captured_overlap_experts_per_matched_node"]),
                (1, {"0": 3, "1": 1}),
            )
            for index, (event, expected) in enumerate(zip(
                    report["events"], expected_rows[name], strict=True)):
                current, candidate, basis, demand, overlap = expected
                self.assertEqual(
                    (event["observation_step"],
                     event["observation_layer"],
                     event["target_step"], event["target_layer"]),
                    expected_nodes[index],
                )
                self.assertEqual(compact(event["current_demand_experts"]),
                                 [current])
                self.assertEqual(compact(event["candidate_experts"]),
                                 [] if candidate is None else [candidate])
                self.assertEqual(
                    [(row["observed_token_step"],
                      row["observed_source_token_step"],
                      row["observed_layer"])
                     for row in event["prediction_basis"]],
                    [] if basis is None else [basis],
                )
                self.assertEqual(
                    compact(event["captured_target_demand_experts"]),
                    [] if demand is None else [demand],
                )
                self.assertEqual(
                    compact(event["captured_overlap_experts"]),
                    [] if overlap is None else [overlap],
                )
                for objects in (
                        event["current_demand_experts"],
                        event["candidate_experts"],
                        event["captured_target_demand_experts"],
                        event["captured_overlap_experts"]):
                    self.assertTrue(all(
                        (row["payload_bytes"], row["packed_bytes"])
                        == (49152, 49152) for row in objects
                    ))
                predicted = candidate is not None
                overlapped = overlap is not None
                self.assertEqual(
                    (event["prediction_available"],
                     event["candidate_occurrences"],
                     event["candidate_duplicates_suppressed"],
                     event["candidate_payload_bytes"],
                     event["candidate_packed_bytes"],
                     event["captured_overlap_payload_bytes"],
                     event["captured_overlap_packed_bytes"]),
                    (predicted, int(predicted), 0,
                     49152 if predicted else 0,
                     49152 if predicted else 0,
                     49152 if overlapped else 0,
                     49152 if overlapped else 0),
                )
                self.assertEqual(
                    event["captured_target_demand_available"], index < 5,
                )
                self.assertEqual(
                    (event["capacity_check"]["page_aligned_effective_bytes"],
                     event["capacity_check"]["working_set_packed_bytes"],
                     event["capacity_check"]["fits"]),
                    (0, 49152 if index == 0 else 98304, False),
                )
                self.assertEqual(
                    event["status"],
                    ("NO_PRIOR_TARGET_LAYER_OBSERVATION" if index == 0 else
                     "END_OF_CAPTURE_HORIZON" if index == 5 else
                     "MATCHED_CAPTURED_DEMAND"),
                )

        for capacity, expected_fits in (
                (98304, [True] * 6),
                (98303, [True] + [False] * 5)):
            budget = dict(self.budget,
                          page_aligned_effective_bytes=capacity)
            report = target.series_ledger(
                self.real, "real", self.inventory, budget, self.expected,
            )
            self.assertEqual(
                [(event["capacity_check"]["page_aligned_effective_bytes"],
                  event["capacity_check"]["working_set_packed_bytes"],
                  event["capacity_check"]["fits"])
                 for event in report["events"]],
                [(capacity, 49152 if index == 0 else 98304, fits)
                 for index, fits in enumerate(expected_fits)],
            )

    def test_single_join_corruptions_are_rejected(self):
        for name in ("metadata", "donor", "consistency", "route-output", "budget"):
            documents = {
                key: json.loads(value) for key, value in self.snapshots().items()
            }
            if name == "metadata":
                documents["inventory"]["model_binding"][
                    "historical_model_fingerprint"
                ] = "0" * 64
            elif name == "donor":
                documents["consistency"]["metadata"]["donor_sha256"] = "0" * 64
            elif name == "consistency":
                documents["consistency"]["checks"][
                    "decoded_routes_equal"
                ] = False
            elif name == "route-output":
                documents["real"]["routes"][0]["topk_expert_ids"] = [99]
            else:
                documents["budget"]["C_fast_effective"] += 1
            changed = {key: encoded(value) for key, value in documents.items()}
            with self.subTest(name=name), self.assertRaises(ValueError):
                target.build_ledger(
                    changed, hf_snapshot=self.snapshot,
                    expected=self.expected,
                )

    def test_no_compute_service_or_gpu_surface_is_accepted_or_emitted(self):
        snapshots = self.snapshots()
        with self.assertRaises(ValueError):
            target.build_ledger(
                dict(snapshots, compute=encoded({"compute_ns": 1})),
                hf_snapshot=self.snapshot, expected=self.expected,
            )
        result = target.build_ledger(
            snapshots, hf_snapshot=self.snapshot, expected=self.expected,
        )
        payload = json.dumps(result).lower()
        self.assertNotIn("compute_ns", payload)
        self.assertNotIn("mqsim", payload)
        self.assertNotIn("cuda", payload)
        source = Path(target.__file__).read_text().lower()
        self.assertNotIn("mqsimservice", source)
        self.assertNotIn("subprocess.popen", source)
        self.assertNotIn("import torch", source)


if __name__ == "__main__":
    unittest.main()
