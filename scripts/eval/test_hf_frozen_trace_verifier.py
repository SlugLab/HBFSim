"""CPU/MOCK tests for frozen HF routing semantics; no model payload is executed."""
from __future__ import annotations

import copy
from dataclasses import replace
import io
import json
import os
from pathlib import Path
import struct
import sys
import tempfile
import unittest
from unittest import mock

import evaluation_inventory
import hf_routing_worker
import verify_hf_metadata as metadata
from model_inventory import ModelInventory
from trace_validation import validate_trace
from test_verify_hf_metadata import fixture

from hf_frozen_trace_verifier import FrozenTraceArm, validate_frozen_trace


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False).encode()


def npy_i16(values, shape, *, padding=0):
    text = repr({"descr": "<i2", "fortran_order": False,
                 "shape": tuple(shape)}).encode("ascii")
    header = text + b" " * padding + b"\n"
    return b"\x93NUMPY\x01\x00" + struct.pack("<H", len(header)) + header + \
        b"".join(struct.pack("<h", value) for value in values)


class MemoryText:
    def __init__(self, raw):
        self.text = raw.decode("utf-8")

    def resolve(self, strict=False):
        return self

    def read_text(self, encoding=None):
        return self.text

    def open(self, mode="r", encoding=None):
        return io.StringIO(self.text)


class ObservedArmName(str):
    def __new__(cls, value, calls):
        result = super().__new__(cls, value)
        result.calls = calls
        return result

    def __eq__(self, other):
        self.calls.append(other)
        return super().__eq__(other)

    __hash__ = str.__hash__


class ObservedArtifactName:
    def __init__(self, calls):
        self.calls = calls

    def __eq__(self, other):
        self.calls.append(other)
        return False


class FrozenTraceVerifierTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(
            prefix=".test-frozen-trace-", dir=metadata.ROOT)
        base = Path(cls.tmp.name)
        checkpoint, donor_path, _ = fixture(base, vocab_size=2048, top_k=2)
        bundle = base / "refresh"
        metadata.verify(checkpoint, donor_path, bundle, test_only=True)
        cls.snapshot = evaluation_inventory.load_hf_snapshot(bundle)
        cls.receipt, cls.artifacts, cls.donor, cls.table = \
            evaluation_inventory._unpack(cls.snapshot)
        cls.attempt = ("/root/hbfsim-exp/eval-base-integration/results/gold/"
                       "hf-routing-runner/frozen-trace-test-attempt-001")
        cls.work_parent = ("/root/hbfsim-exp/eval-base-integration/results/tmp/"
                           "hf-routing/private-work-17")
        cls.arms = cls.build_arms(cls.snapshot)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    @classmethod
    def build_arms(cls, snapshot, *, npy_padding=(0, 3), declared_test_only=True):
        receipt, artifacts, donor, _ = evaluation_inventory._unpack(snapshot)
        config = metadata.strict_object(artifacts["metadata/config.json"])
        prompt = list(range(1000, 1032))
        protocol = hf_routing_worker.make_protocol(receipt, config, prompt)
        effective_test_only = receipt["evidence"] == "TEST_ONLY" or declared_test_only
        if effective_test_only:
            protocol.update(source_kind="TEST_ONLY", provenance="MOCK")
        values = []
        for token in range(39):
            for layer in range(protocol["layers"]):
                first = (token + layer) % protocol["experts"]
                values.extend((first, 1 - first))
        expert_rows = {(row["layer"], row["expert_id"]): row
                       for row in donor["expert_weights"]}
        common = dict(
            checkpoint=receipt["checkpoint"],
            metadata_receipt_sha256=metadata.digest(snapshot.receipt_bytes),
            metadata_complete_sha256=metadata.digest(snapshot.complete_bytes),
            metadata_identity_sha256=receipt["metadata_identity_sha256"],
            observation_identity_sha256=receipt["observation_identity_sha256"],
            donor_sha256=metadata.digest(artifacts["donor.json"]),
            model_fingerprint=donor["ModelFingerprint"],
            runtime_source_manifest_sha256="1" * 64,
            selected_tuning_manifest_sha256="2" * 64,
            selected_tuning_input_report_sha256="3" * 64,
            device_name_declared="TEST_ONLY DEVICE DECLARATION",
            protocol_sha256=metadata.digest(encoded(protocol)),
            gpu_uuid="GPU-TEST-ONLY-0001",
            run_id="TEST_ONLY-frozen-run",
            git_commit="4" * 40,
            environment_fingerprint="5" * 64,
            provenance="MOCK" if effective_test_only else "UNVALIDATED_ROUTING_CAPTURE",
            test_only=declared_test_only,
            scientific_validation_passed=False)
        result = []
        for arm_index, arm in enumerate(("native", "capture", "repeat")):
            request_id = "request-" + arm
            binding = dict(common, arm=arm,
                           work_dir=cls.work_parent + "/" + arm)
            raw_return = dict(
                schema_version=1, request_id=request_id,
                prompt_token_ids=prompt, output_index=0,
                output_token_ids=list(range(200, 208)), finished=True,
                finish_reason="length", stop_reason=None,
                num_cached_tokens=None,
                route_shape=None if arm == "native" else protocol["route_shape"],
                route_dtype=None if arm == "native" else "int16",
                source_kind=protocol["source_kind"], provenance=protocol["provenance"],
                origin_validation="UNVALIDATED_RETURN_SERIALIZATION",
                scientific_validation_passed=False)
            if arm == "native":
                raw_routes = trace = summary = None
            else:
                raw_routes = npy_i16(values, protocol["route_shape"],
                                     padding=npy_padding[arm_index - 1])
                events = []
                sequence = 0
                for token in range(39):
                    for layer in range(protocol["layers"]):
                        ids = values[(token * protocol["layers"] + layer) * 2:
                                     (token * protocol["layers"] + layer + 1) * 2]
                        tensors = []
                        expert_bytes = 0
                        begin = sequence
                        for expert_id in ids:
                            row = expert_rows[(layer, expert_id)]
                            expert_bytes += row["total_bytes"]
                            descriptors = [(item, "w13") for item in row["w13"]["tensors"]]
                            descriptors.append((row["w2"]["tensor"], "w2"))
                            for item, kind in descriptors:
                                tensors.append(dict(
                                    expert_id=expert_id, tensor=item["tensor"],
                                    tensor_kind=kind,
                                    source_shard=item["source_shard"],
                                    source_offset_begin=item["file_offset_begin"],
                                    source_offset_end=item["file_offset_end"],
                                    tensor_bytes=item["bytes"], dtype=item["dtype"],
                                    shape=item["shape"],
                                    access_order_sequence=sequence,
                                    source_shard_sha256=item["source_shard_sha256"],
                                    page_begin=item["file_offset_begin"] // 16384,
                                    page_end=(item["file_offset_end"] + 16383) // 16384))
                                sequence += 1
                        phase = "prefill" if token < 32 else "decode"
                        events.append(dict(
                            schema_version=1, run_id=common["run_id"],
                            request_id=request_id, sequence_id=0, prompt_id=0,
                            phase=phase,
                            token_step=token if phase == "prefill" else token - 32,
                            route_token_index=token, layer_id=layer,
                            topk_expert_ids=ids, topk_weights=None,
                            topk_weights_availability="not exposed by returned-routes API",
                            expert_tensors=tensors, expert_access_bytes=expert_bytes,
                            access_order_sequence_begin=begin,
                            access_order_sequence_end=sequence,
                            host_monotonic_timestamp_ns=(arm_index + 1) * 100000 + len(events),
                            host_timestamp_semantics="post-request JSONL materialization",
                            gpu_event_timestamp_ns=None, previous_compute_gap_ns=None,
                            capture_source=("TEST_ONLY routed-array fixture" if effective_test_only
                                            else "caller-supplied vLLM returned routes; origin unvalidated"),
                            capture_validation_status="UNVALIDATED",
                            model_fingerprint=donor["ModelFingerprint"],
                            inventory_sha256=metadata.digest(artifacts["donor.json"]),
                            environment_fingerprint=common["environment_fingerprint"],
                            git_commit=common["git_commit"]))
                trace = b"".join(encoded(event) + b"\n" for event in events)
                summary = dict(
                    schema_version=1, status="CAPTURED_UNVALIDATED",
                    evidence_class=("TEST_ONLY" if effective_test_only
                                    else "UNVALIDATED_ROUTING_CAPTURE"),
                    scientific_validation_passed=False,
                    donor_commit="37144843906b3bd71f3fbac1fecc6b5080d82b95",
                    trace_path=f"{cls.attempt}/arms/{arm}/routing.jsonl",
                    trace_sha256=metadata.digest(trace), request_count=1,
                    event_count=39 * protocol["layers"],
                    expert_access_count=39 * protocol["layers"] * protocol["top_k"],
                    tensor_access_count=39 * protocol["layers"] * protocol["top_k"] * 3,
                    access_order_sequence_count=39 * protocol["layers"] * protocol["top_k"] * 3,
                    per_layer_event_count={str(layer): 39 for layer in range(protocol["layers"])},
                    per_phase_event_count={"prefill": 32 * protocol["layers"],
                                           "decode": 7 * protocol["layers"]},
                    model_fingerprint=donor["ModelFingerprint"],
                    inventory_sha256=metadata.digest(artifacts["donor.json"]),
                    environment_fingerprint=common["environment_fingerprint"],
                    git_commit=common["git_commit"],
                    boundary=("Returned route metadata only; no live concurrency, GPU timing, "
                              "transferred-byte or independent gold proof."))
                summary = encoded(summary)
            result.append(FrozenTraceArm(
                arm=arm, input_binding=encoded(binding), protocol=encoded(protocol),
                raw_return=encoded(raw_return), frozen_donor=artifacts["donor.json"],
                raw_routes=raw_routes, routing_trace=trace, trace_summary=summary))
        return tuple(result)

    def arm_json(self, arms, index, field, mutation, *, rehash_trace=False):
        arms = list(arms)
        value = json.loads(getattr(arms[index], field))
        mutation(value)
        changes = {field: encoded(value)}
        if field == "routing_trace":
            raise AssertionError("use trace_events")
        arms[index] = replace(arms[index], **changes)
        return tuple(arms)

    def trace_events(self, arms, index, mutation, *, update_summary=True):
        arms = list(arms)
        events = [json.loads(line) for line in arms[index].routing_trace.splitlines()]
        mutation(events)
        trace = b"".join(encoded(event) + b"\n" for event in events)
        changes = {"routing_trace": trace}
        if update_summary:
            summary = json.loads(arms[index].trace_summary)
            summary["trace_sha256"] = metadata.digest(trace)
            changes["trace_summary"] = encoded(summary)
        arms[index] = replace(arms[index], **changes)
        return tuple(arms)

    @staticmethod
    def json_encoding(value, encoding):
        return json.dumps(value, ensure_ascii=False, sort_keys=True,
                          separators=(",", ":"), allow_nan=False).encode(encoding)

    def resealed_snapshot(self, receipt, artifacts, *, receipt_encoding="utf-8",
                          complete_encoding="utf-8"):
        receipt["metadata_identity_sha256"], receipt["observation_identity_sha256"] = \
            metadata.identities(receipt)
        receipt_raw = self.json_encoding(receipt, receipt_encoding)
        complete = json.loads(self.snapshot.complete_bytes)
        complete.update(receipt_sha256=metadata.digest(receipt_raw),
                        observation_identity_sha256=receipt["observation_identity_sha256"])
        return replace(self.snapshot, receipt_bytes=receipt_raw,
                       complete_bytes=self.json_encoding(complete, complete_encoding),
                       artifacts=tuple(sorted(artifacts.items())))

    def reencoded_snapshot_json(self, name, encoding):
        receipt = copy.deepcopy(self.receipt)
        artifacts = dict(self.snapshot.artifacts)
        if name == "receipt.json":
            return self.resealed_snapshot(receipt, artifacts,
                                          receipt_encoding=encoding)
        if name == "COMPLETE.json":
            return self.resealed_snapshot(receipt, artifacts,
                                          complete_encoding=encoding)
        if name in ("donor.json", "tensors.json"):
            raw = self.json_encoding(json.loads(artifacts[name]), encoding)
            artifacts[name] = raw
            receipt["artifacts"][name] = metadata.digest(raw)
            if name == "donor.json":
                receipt["legacy_inventory_sha256"] = metadata.digest(raw)
            return self.resealed_snapshot(receipt, artifacts)
        if name in ("config.json", "model.safetensors.index.json"):
            artifact_name = "metadata/" + name
            raw = self.json_encoding(json.loads(artifacts[artifact_name]), encoding)
            artifacts[artifact_name] = raw
            donor = copy.deepcopy(self.donor)
            file_row = next(row for row in donor["files"] if row["path"] == name)
            file_row.update(size_bytes=len(raw), sha256=metadata.digest(raw))
            donor["total_model_file_bytes"] = sum(row["size_bytes"] for row in donor["files"])
            donor["ModelFingerprint"] = metadata.digest(encoded([
                {key: row[key] for key in ("path", "size_bytes", "sha256")}
                for row in donor["files"]]))
            donor_raw = encoded(donor)
            artifacts["donor.json"] = donor_raw
            state = receipt["inputs"][name]
            state["file_identity"]["size"] = len(raw)
            state.update(read_bytes=len(raw), metadata_sha256=metadata.digest(raw),
                         read_ranges=[[0, len(raw)]])
            receipt["metadata_bytes_read"] = sum(
                state["read_bytes"] for state in receipt["inputs"].values())
            receipt["artifacts"].update({artifact_name: metadata.digest(raw),
                                         "donor.json": metadata.digest(donor_raw)})
            receipt.update(legacy_inventory_sha256=metadata.digest(donor_raw),
                           historical_model_fingerprint=donor["ModelFingerprint"])
            return self.resealed_snapshot(receipt, artifacts)
        if name == "safetensors-header.json":
            artifact_name = next(key for key in artifacts
                                 if key.startswith("headers/") and key.endswith(".header"))
            shard = artifact_name.removeprefix("headers/").removesuffix(".header")
            old_raw = artifacts[artifact_name]
            payload = self.json_encoding(json.loads(old_raw[8:]), encoding)
            raw = struct.pack("<Q", len(payload)) + payload
            artifacts[artifact_name] = raw
            delta = len(raw) - len(old_raw)
            donor = copy.deepcopy(self.donor)
            file_row = next(row for row in donor["files"] if row["path"] == shard)
            file_row["size_bytes"] += delta
            donor["safetensors_shard_bytes"] += delta
            donor["total_model_file_bytes"] += delta
            for expert in donor["expert_weights"]:
                tensors = expert["w13"]["tensors"] + [expert["w2"]["tensor"]]
                for tensor in tensors:
                    if tensor["source_shard"] == shard:
                        tensor["file_offset_begin"] += delta
                        tensor["file_offset_end"] += delta
            donor["ModelFingerprint"] = metadata.digest(encoded([
                {key: row[key] for key in ("path", "size_bytes", "sha256")}
                for row in donor["files"]]))
            donor_raw = encoded(donor)
            artifacts["donor.json"] = donor_raw
            table = json.loads(artifacts["tensors.json"])
            for tensor in table.values():
                if tensor["source_shard"] == shard:
                    tensor["file_offset_begin"] += delta
                    tensor["file_offset_end"] += delta
            table_raw = encoded(table)
            artifacts["tensors.json"] = table_raw
            state = receipt["inputs"][shard]
            state["file_identity"]["size"] += delta
            state.update(read_bytes=len(raw), metadata_sha256=metadata.digest(raw),
                         read_ranges=[[0, 8], [8, len(raw) - 8]])
            receipt["metadata_bytes_read"] = sum(
                state["read_bytes"] for state in receipt["inputs"].values())
            receipt["summary"]["tensor_metadata_sha256"] = metadata.digest(table_raw)
            receipt["artifacts"].update({
                artifact_name: metadata.digest(raw),
                "donor.json": metadata.digest(donor_raw),
                "tensors.json": metadata.digest(table_raw)})
            receipt.update(legacy_inventory_sha256=metadata.digest(donor_raw),
                           historical_model_fingerprint=donor["ModelFingerprint"])
            return self.resealed_snapshot(receipt, artifacts)
        raise AssertionError(name)

    def assert_rejects(self, arms=None, snapshot=None, attempt=None):
        baseline = validate_frozen_trace(self.snapshot, self.arms,
                                         expected_attempt_dir=self.attempt)
        self.assertEqual(baseline["status"], "ROUTE_CONSISTENCY_ONLY")
        with self.assertRaises((ValueError, AssertionError, KeyError, TypeError)):
            validate_frozen_trace(snapshot or self.snapshot, arms or self.arms,
                                  expected_attempt_dir=attempt or self.attempt)

    def test_valid_baseline_is_deterministic_pure_and_limited(self):
        before = tuple(tuple(getattr(arm, field) for field in arm.__dataclass_fields__)
                       for arm in self.arms)
        forbidden = ("numpy", "torch", "vllm")
        modules_before = set(sys.modules)
        blocked = AssertionError("filesystem access")
        with mock.patch("builtins.open", side_effect=blocked), \
             mock.patch.object(io, "open", side_effect=blocked), \
             mock.patch.object(os, "open", side_effect=blocked), \
             mock.patch.object(Path, "open", side_effect=blocked), \
             mock.patch.object(Path, "read_text", side_effect=blocked), \
             mock.patch.object(Path, "read_bytes", side_effect=blocked), \
             mock.patch.object(Path, "resolve", side_effect=blocked):
            first = validate_frozen_trace(self.snapshot, self.arms,
                                          expected_attempt_dir=self.attempt)
            second = validate_frozen_trace(self.snapshot, self.arms,
                                           expected_attempt_dir=self.attempt)
        self.assertEqual(first, second)
        self.assertEqual(first["status"], "ROUTE_CONSISTENCY_ONLY")
        self.assertEqual(first["evidence_class"], "TEST_ONLY")
        self.assertEqual(first["provenance"], "MOCK")
        self.assertFalse(first["scientific_validation_passed"])
        self.assertTrue(first["checks"]["token_ids_equal"])
        self.assertTrue(first["checks"]["decoded_routes_equal"])
        self.assertNotEqual(first["arms"][1]["artifacts"]["raw-routes.npy"],
                            first["arms"][2]["artifacts"]["raw-routes.npy"])
        self.assertNotIn("COMPLETE", json.dumps(first))
        self.assertEqual(before, tuple(tuple(getattr(arm, field)
                         for field in arm.__dataclass_fields__) for arm in self.arms))
        imported = set(sys.modules) - modules_before
        self.assertFalse(any(name == root or name.startswith(root + ".")
                             for name in imported for root in forbidden))

    def test_exact_triplet_and_textual_paths_are_enforced(self):
        self.assert_rejects(arms=self.arms[::-1])
        self.assert_rejects(arms=self.arms[:2])
        self.assert_rejects(attempt=self.attempt + "/")
        self.assert_rejects(attempt=self.attempt.replace("/results/", "/results/./"))
        for change in (lambda b: b.update(work_dir=b["work_dir"] + "/.."),
                       lambda b: b.update(work_dir=self.work_parent + "/capture"),
                       lambda b: b.update(arm="repeat")):
            self.assert_rejects(self.arm_json(self.arms, 0, "input_binding", change))
        arms = self.arm_json(self.arms, 1, "trace_summary",
                             lambda s: s.update(trace_path=s["trace_path"].replace("capture", "repeat")))
        self.assert_rejects(arms)

    def test_external_names_require_exact_strings_before_any_comparison(self):
        baseline = validate_frozen_trace(self.snapshot, self.arms,
                                         expected_attempt_dir=self.attempt)
        self.assertEqual(baseline["status"], "ROUTE_CONSISTENCY_ONLY")
        arm_calls = []
        changed = list(self.arms)
        changed[0] = replace(changed[0], arm=ObservedArmName("native", arm_calls))
        with self.subTest(case="arm string subclass"):
            with self.assertRaises(ValueError):
                validate_frozen_trace(self.snapshot, tuple(changed),
                                      expected_attempt_dir=self.attempt)
            self.assertEqual(arm_calls, [])

        artifact_calls = []
        entries = list(self.snapshot.artifacts)
        entries[0] = (ObservedArtifactName(artifact_calls), entries[0][1])
        changed_snapshot = replace(self.snapshot, artifacts=tuple(entries))
        with self.subTest(case="artifact non-string name"):
            with self.assertRaises(ValueError):
                validate_frozen_trace(changed_snapshot, self.arms,
                                      expected_attempt_dir=self.attempt)
            self.assertEqual(artifact_calls, [])

        changed = list(self.arms)
        changed[0] = replace(changed[0], arm="wrong")
        with self.assertRaises(ValueError):
            validate_frozen_trace(self.snapshot, tuple(changed),
                                  expected_attempt_dir=self.attempt)

    def test_binding_protocol_schema_types_and_cross_arm_joins(self):
        mutations = [
            lambda b: b.update(metadata_receipt_sha256="0" * 64),
            lambda b: b.update(runtime_source_manifest_sha256="g" * 64),
            lambda b: b.update(selected_tuning_manifest_sha256="8" * 64),
            lambda b: b.update(protocol_sha256=True),
            lambda b: b.update(git_commit="a" * 39),
            lambda b: b.update(environment_fingerprint="b" * 63),
            lambda b: b.update(run_id=""),
            lambda b: b.update(device_name_declared=7),
            lambda b: b.update(test_only=1),
            lambda b: b.update(scientific_validation_passed=True),
            lambda b: b.update(unexpected=1),
        ]
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                self.assert_rejects(self.arm_json(self.arms, 1, "input_binding", mutation))
        arms = self.arm_json(self.arms, 2, "protocol", lambda p: p.update(top_k=1))
        self.assert_rejects(arms)
        self.assert_rejects(self.arm_json(
            self.arms, 2, "input_binding", lambda b: b.update(test_only=False)))

    def test_real_metadata_preserves_labels_unless_declared_test_only_forces_mock(self):
        forced = validate_frozen_trace(
            self.snapshot, self.build_arms(self.snapshot, declared_test_only=False),
            expected_attempt_dir=self.attempt)
        self.assertTrue(forced["effective_test_only"])
        self.assertEqual((forced["source_kind"], forced["provenance"]),
                         ("TEST_ONLY", "MOCK"))
        receipt = json.loads(self.snapshot.receipt_bytes)
        receipt.update(evidence="CHECKPOINT_METADATA", provenance="CHECKPOINT_METADATA")
        receipt_raw = encoded(receipt)
        complete = json.loads(self.snapshot.complete_bytes)
        complete["receipt_sha256"] = metadata.digest(receipt_raw)
        real_snapshot = replace(self.snapshot, receipt_bytes=receipt_raw,
                                complete_bytes=encoded(complete))
        real_arms = self.build_arms(real_snapshot, declared_test_only=False)
        report = validate_frozen_trace(real_snapshot, real_arms,
                                       expected_attempt_dir=self.attempt)
        self.assertEqual(report["source_kind"], "CHECKPOINT_METADATA")
        self.assertEqual(report["provenance"], "CHECKPOINT_METADATA")
        self.assertEqual(report["evidence_class"], "UNVALIDATED_ROUTING_CAPTURE")
        self.assertFalse(report["effective_test_only"])
        mock_arms = self.build_arms(real_snapshot, declared_test_only=True)
        report = validate_frozen_trace(real_snapshot, mock_arms,
                                       expected_attempt_dir=self.attempt)
        self.assertEqual((report["source_kind"], report["provenance"]),
                         ("TEST_ONLY", "MOCK"))
        self.assertTrue(report["effective_test_only"])

    def test_strict_utf8_duplicate_nonfinite_and_bounded_json(self):
        arms = list(self.arms)
        arms[0] = replace(arms[0], raw_return=b'{"schema_version":1,"schema_version":1}')
        self.assert_rejects(tuple(arms))
        arms = list(self.arms)
        arms[0] = replace(arms[0], raw_return=b'{"schema_version":NaN}')
        self.assert_rejects(tuple(arms))
        arms = list(self.arms)
        arms[0] = replace(arms[0], raw_return="{}".encode("utf-16"))
        self.assert_rejects(tuple(arms))
        arms = list(self.arms)
        arms[0] = replace(arms[0], raw_return=b" " * ((1 << 20) + 1))
        self.assert_rejects(tuple(arms))

    def test_all_arm_artifacts_require_exact_bytes_or_native_none(self):
        fields = ("input_binding", "protocol", "raw_return", "frozen_donor",
                  "raw_routes", "routing_trace", "trace_summary")
        for arm_index, arm in enumerate(self.arms):
            for field in fields:
                original = getattr(arm, field)
                if original is None:
                    continue
                for wrapper in (bytearray, memoryview):
                    changed = list(self.arms)
                    changed[arm_index] = replace(arm, **{field: wrapper(original)})
                    with self.subTest(arm=arm.arm, field=field,
                                      wrapper=wrapper.__name__):
                        self.assert_rejects(tuple(changed))

    def test_every_snapshot_json_interpretation_requires_strict_utf8(self):
        names = ("receipt.json", "COMPLETE.json", "donor.json", "tensors.json",
                 "config.json", "model.safetensors.index.json",
                 "safetensors-header.json")
        for name in names:
            for encoding in ("utf-16", "utf-32"):
                with self.subTest(name=name, encoding=encoding):
                    snapshot = self.reencoded_snapshot_json(name, encoding)
                    evaluation_inventory._unpack(snapshot)
                    arms = self.build_arms(snapshot)
                    self.assert_rejects(arms, snapshot=snapshot)

    def test_protocol_binding_hashes_original_bytes_and_joins_arms(self):
        pretty = (json.dumps(json.loads(self.arms[0].protocol), indent=2,
                             ensure_ascii=False, sort_keys=True) + "\n").encode()
        changed = list(self.arms)
        changed[1] = replace(changed[1], protocol=pretty)
        self.assert_rejects(tuple(changed))
        changed = []
        for arm in self.arms:
            binding = json.loads(arm.input_binding)
            binding["protocol_sha256"] = metadata.digest(pretty)
            changed.append(replace(arm, protocol=pretty,
                                   input_binding=encoded(binding)))
        report = validate_frozen_trace(self.snapshot, tuple(changed),
                                       expected_attempt_dir=self.attempt)
        self.assertEqual(report["metadata"]["protocol_sha256"],
                         metadata.digest(pretty))

    def test_raw_tokens_labels_and_optional_routes_are_exact(self):
        mutations = [
            lambda r: r["prompt_token_ids"].__setitem__(0, 999),
            lambda r: r["output_token_ids"].__setitem__(0, 999),
            lambda r: r.update(output_index=True),
            lambda r: r.update(finished=1),
            lambda r: r.update(num_cached_tokens=False),
            lambda r: r.update(origin_validation="VALIDATED"),
            lambda r: r.update(scientific_validation_passed=True),
            lambda r: r.update(extra=None),
        ]
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                self.assert_rejects(self.arm_json(self.arms, 0, "raw_return", mutation))
        for field in ("raw_routes", "routing_trace", "trace_summary"):
            arms = list(self.arms)
            arms[0] = replace(arms[0], **{field: b"x"})
            self.assert_rejects(tuple(arms))
            arms = list(self.arms)
            arms[1] = replace(arms[1], **{field: None})
            self.assert_rejects(tuple(arms))
        self.assert_rejects(self.arm_json(
            self.arms, 0, "raw_return",
            lambda r: r.update(route_shape=[39, 2, 2], route_dtype="int16")))
        self.assert_rejects(self.arm_json(
            self.arms, 1, "raw_return",
            lambda r: r.update(route_shape=None, route_dtype=None)))

    def test_route_shape_dtype_values_uniqueness_and_bounds_are_exact(self):
        for mutation in (lambda r: r.update(route_shape=[39, 2, 1]),
                         lambda r: r.update(route_dtype="uint16")):
            self.assert_rejects(self.arm_json(self.arms, 1, "raw_return", mutation))
        arms = list(self.arms)
        changed = bytearray(arms[2].raw_routes)
        changed[-2:] = struct.pack("<h", 1)
        arms[2] = replace(arms[2], raw_routes=bytes(changed))
        self.assert_rejects(tuple(arms))
        arms = list(self.arms)
        changed = bytearray(arms[1].raw_routes)
        changed[-2:] = struct.pack("<h", 2)
        arms[1] = replace(arms[1], raw_routes=bytes(changed))
        with self.assertRaisesRegex(ValueError, r"expert ID.*inventory"):
            validate_frozen_trace(self.snapshot, tuple(arms),
                                  expected_attempt_dir=self.attempt)

    def test_event_order_routes_and_all_tensor_fields_are_independently_bound(self):
        mutations = [
            lambda e: e.__setitem__(slice(0, 2), [e[1], e[0]]),
            lambda e: e[0].update(topk_expert_ids=e[0]["topk_expert_ids"][::-1]),
            lambda e: e[0].update(layer_id=1),
            lambda e: e[0].update(phase="decode"),
            lambda e: e[0].update(token_step=True),
            lambda e: e[0].update(route_token_index=1),
            lambda e: e[0].update(request_id="wrong"),
            lambda e: e[0].update(run_id="wrong"),
            lambda e: e[0].update(model_fingerprint="0" * 64),
            lambda e: e[0].update(environment_fingerprint="0" * 64),
            lambda e: e[0].update(git_commit="0" * 40),
            lambda e: e[0].update(expert_access_bytes=e[0]["expert_access_bytes"] - 1),
            lambda e: e[0].update(access_order_sequence_begin=1),
            lambda e: e[0].update(host_timestamp_semantics="inference latency"),
            lambda e: e[0].update(host_monotonic_timestamp_ns=1.0),
            lambda e: e[0].update(capture_source="REAL"),
            lambda e: e[0].update(capture_validation_status="VALIDATED"),
            lambda e: e[0].update(extra=1),
        ]
        tensor_changes = [
            lambda t: t.update(tensor_bytes=t["tensor_bytes"] + 1),
            lambda t: t.update(dtype="F16"),
            lambda t: t.update(source_offset_begin=t["source_offset_begin"] + 1),
            lambda t: t.update(source_shard="wrong.safetensors"),
            lambda t: t.update(source_shard_sha256="0" * 64),
            lambda t: t.update(expert_id=1 - t["expert_id"]),
            lambda t: t.update(tensor="wrong.tensor"),
            lambda t: t.update(page_begin=t["page_begin"] + 1),
            lambda t: t.update(access_order_sequence=t["access_order_sequence"] + 1),
            lambda t: t.update(shape=[1, 1]),
            lambda t: t.update(extra=1),
        ]
        mutations.extend(lambda e, change=change: change(e[0]["expert_tensors"][0])
                         for change in tensor_changes)
        mutations.append(lambda e: e[0]["expert_tensors"].reverse())
        mutations.append(lambda e: e[0].update(access_order_sequence_end=999))
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                self.assert_rejects(self.trace_events(self.arms, 1, mutation))

    def test_timestamp_order_and_trace_line_framing_and_bounds(self):
        self.assert_rejects(self.trace_events(
            self.arms, 1,
            lambda e: e[1].update(host_monotonic_timestamp_ns=e[0]["host_monotonic_timestamp_ns"] - 1)))
        arms = list(self.arms)
        arms[1] = replace(arms[1], routing_trace=arms[1].routing_trace[:-1])
        self.assert_rejects(tuple(arms))
        arms = list(self.arms)
        arms[1] = replace(arms[1], routing_trace=b"\n" + arms[1].routing_trace)
        self.assert_rejects(tuple(arms))
        lines = self.arms[1].routing_trace.splitlines(keepends=True)
        padding = (1 << 20) - len(lines[0])
        lines[0] = lines[0][:-1] + b" " * padding + b"\n"
        trace = b"".join(lines)
        summary = json.loads(self.arms[1].trace_summary)
        summary["trace_sha256"] = metadata.digest(trace)
        arms = list(self.arms)
        arms[1] = replace(arms[1], routing_trace=trace,
                          trace_summary=encoded(summary))
        report = validate_frozen_trace(self.snapshot, tuple(arms),
                                       expected_attempt_dir=self.attempt)
        self.assertEqual(report["status"], "ROUTE_CONSISTENCY_ONLY")
        lines[0] = lines[0][:-1] + b" \n"
        trace = b"".join(lines)
        summary["trace_sha256"] = metadata.digest(trace)
        arms[1] = replace(arms[1], routing_trace=trace,
                          trace_summary=encoded(summary))
        with self.assertRaisesRegex(ValueError, r"trace line .*bound"):
            validate_frozen_trace(self.snapshot, tuple(arms),
                                  expected_attempt_dir=self.attempt)
        arms = list(self.arms)
        arms[1] = replace(arms[1], routing_trace=arms[1].routing_trace + b"{}\n")
        self.assert_rejects(tuple(arms))

    def test_summary_exact_counts_hash_identity_claims_and_boundary(self):
        mutations = [
            lambda s: s.update(status="PASS"),
            lambda s: s.update(evidence_class="REAL_QWEN_TRACE"),
            lambda s: s.update(scientific_validation_passed=True),
            lambda s: s.update(trace_sha256="0" * 64),
            lambda s: s.update(event_count=s["event_count"] - 1),
            lambda s: s.update(expert_access_count=True),
            lambda s: s["per_layer_event_count"].update({"0": 38}),
            lambda s: s.update(inventory_sha256="0" * 64),
            lambda s: s.update(environment_fingerprint="0" * 64),
            lambda s: s.update(boundary="independent gold proof"),
            lambda s: s.update(extra=1),
        ]
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                self.assert_rejects(self.arm_json(self.arms, 1, "trace_summary", mutation))

    def test_donor_primitive_coercion_accepted_by_legacy_is_rejected_here(self):
        donor = copy.deepcopy(self.donor)
        donor["configuration"]["num_hidden_layers"] = 2.0
        donor_raw = encoded(donor)
        receipt = json.loads(self.snapshot.receipt_bytes)
        artifacts = dict(self.snapshot.artifacts)
        artifacts["donor.json"] = donor_raw
        receipt["artifacts"]["donor.json"] = metadata.digest(donor_raw)
        receipt["legacy_inventory_sha256"] = metadata.digest(donor_raw)
        receipt["metadata_identity_sha256"], receipt["observation_identity_sha256"] = \
            metadata.identities(receipt)
        receipt_raw = encoded(receipt)
        complete = json.loads(self.snapshot.complete_bytes)
        complete.update(receipt_sha256=metadata.digest(receipt_raw),
                        observation_identity_sha256=receipt["observation_identity_sha256"])
        forged = replace(self.snapshot, receipt_bytes=receipt_raw,
                         complete_bytes=encoded(complete),
                         artifacts=tuple(sorted(artifacts.items())))
        evaluation_inventory._unpack(forged)
        legacy = ModelInventory(MemoryText(donor_raw))
        capture = self.arms[1]
        wrapper = encoded(dict(protocol=dict(num_prompts=1, input_len=32, output_len=8),
                               trace=json.loads(capture.trace_summary)))
        self.assertTrue(all(validate_trace(MemoryText(capture.routing_trace),
                                           MemoryText(wrapper), legacy)
                            ["validation"]["checks"].values()))
        forged_arms = list(self.build_arms(forged))
        self.assert_rejects(tuple(forged_arms), snapshot=forged)


if __name__ == "__main__":
    unittest.main()
