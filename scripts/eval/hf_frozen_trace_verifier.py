"""Pure consistency validation for an already frozen three-arm HF route trace.

This module authenticates no process, checkpoint origin, runtime setting, GPU,
or scientific result.  It only checks exact caller-supplied immutable buffers.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import io
from pathlib import PurePosixPath
import re

from evaluation_inventory import HFMetadataSnapshot, _unpack
from hf_route_array import decode_route_array
from hf_routing_worker import make_protocol
from model_inventory import ModelInventory
from trace_validation import validate_trace
from verify_hf_metadata import LIMITS, canonical, strict_object


_ARM_NAMES = ("native", "capture", "repeat")
_JSON_LIMIT = 8 << 20
_RAW_LIMIT = 1 << 20
_TRACE_LIMIT = 64 << 20
_LINE_LIMIT = 1 << 20
_PAGE_BYTES = 16384
_HEX64 = re.compile(r"[0-9a-f]{64}")
_HEX40 = re.compile(r"[0-9a-f]{40}")
_DTYPES = {"int8", "uint8", "int16", "uint16", "int32", "uint32",
           "int64", "uint64"}

_BINDING_KEYS = {
    "arm", "checkpoint", "metadata_receipt_sha256",
    "metadata_complete_sha256", "metadata_identity_sha256",
    "observation_identity_sha256", "donor_sha256", "model_fingerprint",
    "runtime_source_manifest_sha256", "selected_tuning_manifest_sha256",
    "selected_tuning_input_report_sha256", "device_name_declared",
    "protocol_sha256", "gpu_uuid", "work_dir", "run_id", "git_commit",
    "environment_fingerprint", "provenance", "test_only",
    "scientific_validation_passed",
}
_RAW_KEYS = {
    "schema_version", "request_id", "prompt_token_ids", "output_index",
    "output_token_ids", "finished", "finish_reason", "stop_reason",
    "num_cached_tokens", "route_shape", "route_dtype", "source_kind",
    "provenance", "origin_validation", "scientific_validation_passed",
}
_EVENT_KEYS = {
    "schema_version", "run_id", "request_id", "sequence_id", "prompt_id",
    "phase", "token_step", "route_token_index", "layer_id",
    "topk_expert_ids", "topk_weights", "topk_weights_availability",
    "expert_tensors", "expert_access_bytes", "access_order_sequence_begin",
    "access_order_sequence_end", "host_monotonic_timestamp_ns",
    "host_timestamp_semantics", "gpu_event_timestamp_ns",
    "previous_compute_gap_ns", "capture_source", "capture_validation_status",
    "model_fingerprint", "inventory_sha256", "environment_fingerprint",
    "git_commit",
}
_TENSOR_KEYS = {
    "expert_id", "tensor", "tensor_kind", "source_shard",
    "source_offset_begin", "source_offset_end", "tensor_bytes", "dtype",
    "shape", "access_order_sequence", "source_shard_sha256", "page_begin",
    "page_end",
}
_DONOR_TENSOR_KEYS = {
    "tensor", "dtype", "shape", "bytes", "data_offset_begin",
    "data_offset_end", "file_offset_begin", "file_offset_end",
    "source_shard", "source_shard_sha256",
}
_SUMMARY_KEYS = {
    "schema_version", "status", "evidence_class",
    "scientific_validation_passed", "donor_commit", "trace_path",
    "trace_sha256", "request_count", "event_count", "expert_access_count",
    "tensor_access_count", "access_order_sequence_count",
    "per_layer_event_count", "per_phase_event_count", "model_fingerprint",
    "inventory_sha256", "environment_fingerprint", "git_commit", "boundary",
}
_BOUNDARY = ("Returned route metadata only; no live concurrency, GPU timing, "
             "transferred-byte or independent gold proof.")


@dataclass(frozen=True)
class FrozenTraceArm:
    arm: str
    input_binding: bytes
    protocol: bytes
    raw_return: bytes
    frozen_donor: bytes
    raw_routes: bytes | None
    routing_trace: bytes | None
    trace_summary: bytes | None


class _ImmutableText:
    """The fixed path-shaped adapter required by unchanged legacy validators."""
    __slots__ = ("_text",)

    def __init__(self, raw: bytes) -> None:
        if type(raw) is not bytes:
            raise ValueError("immutable text adapter requires bytes")
        try:
            self._text = raw.decode("utf-8", errors="strict")
        except UnicodeDecodeError as error:
            raise ValueError("legacy text is not strict UTF-8") from error

    def resolve(self, strict: bool = False):
        if strict is not True:
            raise ValueError("immutable adapter requires strict resolution")
        return self

    def read_text(self, encoding: str | None = None) -> str:
        if encoding != "utf-8":
            raise ValueError("immutable adapter requires UTF-8")
        return self._text

    def open(self, mode: str = "r", encoding: str | None = None):
        if mode != "r" or encoding != "utf-8":
            raise ValueError("immutable adapter is text-read-only")
        return io.StringIO(self._text)


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError("frozen HF trace: " + message)


def _json(raw: bytes, limit: int, label: str) -> dict:
    _require(type(raw) is bytes and 0 < len(raw) <= limit,
             label + " must be bounded bytes")
    try:
        text = raw.decode("utf-8", errors="strict")
    except UnicodeDecodeError as error:
        raise ValueError("frozen HF trace: " + label + " is not strict UTF-8") from error
    try:
        return strict_object(text)
    except (ValueError, TypeError, KeyError) as error:
        raise ValueError("frozen HF trace: invalid " + label) from error


def _same(actual, expected, label: str) -> None:
    try:
        equal = canonical(actual) == canonical(expected)
    except (ValueError, TypeError, OverflowError) as error:
        raise ValueError("frozen HF trace: invalid " + label) from error
    _require(equal, label + " mismatch")


def _int(value, label: str, minimum: int = 0) -> int:
    _require(type(value) is int and minimum <= value <= (1 << 63) - 1,
             label + " must be an exact bounded integer")
    return value


def _text(value, label: str, maximum: int = 4096) -> str:
    _require(type(value) is str and 0 < len(value.encode("utf-8")) <= maximum,
             label + " must be bounded nonempty text")
    return value


def _hex(value, pattern, label: str) -> str:
    _require(type(value) is str and pattern.fullmatch(value) is not None,
             label + " must be lowercase hexadecimal")
    return value


def _posix_absolute(value: str, label: str) -> PurePosixPath:
    _text(value, label)
    _require("\\" not in value and "\x00" not in value and value.startswith("/")
             and not value.endswith("/") and "//" not in value,
             label + " must be a canonical absolute POSIX path")
    path = PurePosixPath(value)
    _require(str(path) == value and "." not in path.parts and ".." not in path.parts,
             label + " must be a canonical absolute POSIX path")
    return path


def _guard_metadata(receipt: dict, artifacts: dict, donor: dict, table: dict) -> None:
    donor_raw = artifacts.get("donor.json")
    parsed_donor = _json(donor_raw, LIMITS["donor_bytes"], "metadata donor")
    _same(parsed_donor, donor, "unpacked donor")
    config = _json(artifacts.get("metadata/config.json"), LIMITS["file_bytes"] + 8,
                   "metadata config")
    donor_config = donor.get("configuration")
    _require(type(donor_config) is dict, "donor configuration must be an object")
    for key in ("num_hidden_layers", "num_experts", "num_experts_per_tok"):
        value = _int(donor_config.get(key), "donor configuration " + key, 1)
        _require(type(config.get(key)) is int and config[key] == value,
                 "donor/config routing primitive mismatch")
    _hex(donor.get("ModelFingerprint"), _HEX64, "donor model fingerprint")
    aggregate_keys = (
        "expert_count_total", "expert_weight_bytes", "tensor_payload_bytes",
        "non_expert_tensor_bytes", "safetensors_shard_bytes", "shard_count",
        "total_model_file_bytes",
    )
    for key in aggregate_keys:
        _int(donor.get(key), "donor " + key, 1 if key != "non_expert_tensor_bytes" else 0)
    unique = donor.get("per_expert_bytes_unique")
    _require(type(unique) is list and len(unique) == 1,
             "donor per-expert aggregate must have one size")
    _int(unique[0], "donor per-expert byte size", 1)
    rows = donor.get("expert_weights")
    _require(type(rows) is list, "donor expert weights must be a list")
    seen = set()
    for row in rows:
        _require(type(row) is dict, "donor expert row must be an object")
        layer = _int(row.get("layer"), "donor layer")
        expert = _int(row.get("expert_id"), "donor expert ID")
        _require((layer, expert) not in seen, "duplicate donor expert")
        seen.add((layer, expert))
        total = _int(row.get("total_bytes"), "donor expert total bytes", 1)
        w13 = row.get("w13")
        w2 = row.get("w2")
        _require(type(w13) is dict and type(w2) is dict,
                 "donor expert aggregates must be objects")
        w13_bytes = _int(w13.get("bytes"), "donor w13 bytes", 1)
        w2_bytes = _int(w2.get("bytes"), "donor w2 bytes", 1)
        _require(total == w13_bytes + w2_bytes,
                 "donor expert byte aggregates differ")
        _require(type(w13.get("dtype")) is str and w13["dtype"] == "BF16"
                 and type(w2.get("dtype")) is str and w2["dtype"] == "BF16",
                 "donor expert dtype mismatch")
        components = w13.get("shape_components")
        _require(type(components) is list and len(components) == 2,
                 "donor w13 shapes must be a pair")
        for shape in components + [w2.get("shape")]:
            _require(type(shape) is list and shape and
                     all(type(n) is int and n > 0 for n in shape),
                     "donor aggregate shape primitives are invalid")
        tensors = w13.get("tensors")
        _require(type(tensors) is list and len(tensors) == 2
                 and type(w2.get("tensor")) is dict,
                 "donor expert tensor order/cardinality mismatch")
        ordered = tensors + [w2["tensor"]]
        prefix = f"model.layers.{layer}.mlp.experts.{expert}."
        names = [prefix + projection + "_proj.weight"
                 for projection in ("gate", "up", "down")]
        for descriptor, name in zip(ordered, names):
            _require(type(descriptor) is dict and set(descriptor) == _DONOR_TENSOR_KEYS,
                     "donor tensor descriptor schema mismatch")
            _require(descriptor.get("tensor") == name,
                     "donor tensor descriptor order mismatch")
            _text(descriptor.get("source_shard"), "donor source shard", 256)
            _hex(descriptor.get("source_shard_sha256"), _HEX64,
                 "donor source shard hash")
            _require(type(descriptor.get("dtype")) is str
                     and descriptor["dtype"] == "BF16",
                     "donor tensor dtype mismatch")
            shape = descriptor.get("shape")
            _require(type(shape) is list and shape and
                     all(type(n) is int and n > 0 for n in shape),
                     "donor tensor shape primitives are invalid")
            for key in ("bytes", "data_offset_begin", "data_offset_end",
                        "file_offset_begin", "file_offset_end"):
                _int(descriptor.get(key), "donor tensor " + key,
                     1 if key == "bytes" else 0)
            _require(name in table, "donor expert tensor absent from unpacked table")
            _same(descriptor, table[name], "donor/unpacked tensor descriptor")
    layers = donor_config["num_hidden_layers"]
    experts = donor_config["num_experts"]
    _require(seen == {(layer, expert) for layer in range(layers)
                      for expert in range(experts)},
             "donor expert identity coverage mismatch")
    summary = receipt.get("summary")
    _require(type(summary) is dict, "metadata summary must be an object")
    for key in ("layers", "experts_per_layer", "top_k", "tensor_count",
                "expert_count", "expert_weight_bytes", "tensor_payload_bytes",
                "resident_non_offloaded_bytes", "shard_count"):
        _int(summary.get(key), "metadata summary " + key,
             0 if key == "resident_non_offloaded_bytes" else 1)
    _require(summary["layers"] == layers
             and summary["experts_per_layer"] == experts
             and summary["top_k"] == donor_config["num_experts_per_tok"],
             "metadata routing geometry mismatch")
    _hex(summary.get("tensor_metadata_sha256"), _HEX64,
         "metadata tensor table hash")
    _require(type(summary.get("dtype")) is str and summary["dtype"] == "BF16",
             "metadata dtype mismatch")
    _text(receipt.get("checkpoint"), "metadata checkpoint")
    for key in ("metadata_identity_sha256", "observation_identity_sha256",
                "legacy_inventory_sha256", "historical_model_fingerprint"):
        _hex(receipt.get(key), _HEX64, "metadata " + key)


def _preflight_snapshot_json(snapshot: HFMetadataSnapshot) -> None:
    """Reject non-UTF-8 JSON before the unchanged metadata helper sees it."""
    _require(type(snapshot.receipt_bytes) is bytes
             and len(snapshot.receipt_bytes) <= LIMITS["file_bytes"],
             "metadata receipt must be bounded exact bytes")
    _require(type(snapshot.complete_bytes) is bytes
             and len(snapshot.complete_bytes) <= 4096,
             "metadata marker must be bounded exact bytes")
    entries = snapshot.artifacts
    _require(type(entries) is tuple, "metadata artifacts must be an immutable tuple")
    remaining = (LIMITS["donor_bytes"] + LIMITS["current_total_bytes"]
                 + LIMITS["file_bytes"])
    checked = []
    for entry in entries:
        _require(type(entry) is tuple and len(entry) == 2,
                 "metadata artifact entry must be a name/bytes tuple")
        name, raw = entry
        _require(type(name) is str, "metadata artifact name must be exact text")
        limit = (LIMITS["donor_bytes"] if name == "donor.json"
                 else LIMITS["file_bytes"] + 8)
        _require(type(raw) is bytes and len(raw) <= limit and len(raw) <= remaining,
                 "metadata artifact exceeds its frozen byte bound")
        remaining -= len(raw)
        checked.append((name, raw))
    _json(snapshot.receipt_bytes, LIMITS["file_bytes"], "metadata receipt")
    _json(snapshot.complete_bytes, 4096, "metadata marker")
    direct_json = {
        "donor.json", "tensors.json", "metadata/config.json",
        "metadata/model.safetensors.index.json",
    }
    for name, raw in checked:
        if name in direct_json:
            _json(raw, (LIMITS["donor_bytes"] if name == "donor.json"
                        else LIMITS["file_bytes"] + 8), name)
        elif name.startswith("headers/") and name.endswith(".safetensors.header"):
            _require(len(raw) > 8, name + " must contain a binary prefix and JSON")
            _json(raw[8:], LIMITS["file_bytes"], name + " JSON")


def _work_parent(path_value: str, arm: str) -> str:
    path = _posix_absolute(path_value, arm + " work path")
    parts = path.parts
    marker = ("results", "tmp", "hf-routing")
    positions = [index for index in range(len(parts) - 2)
                 if tuple(parts[index:index + 3]) == marker]
    _require(len(positions) == 1, "work path must be under results/tmp/hf-routing")
    index = positions[0]
    _require(len(parts) >= index + 5 and parts[-1] == arm,
             "work path must have a shared private parent and arm basename")
    return str(path.parent)


def _tokens(value, count: int, vocab: int, label: str) -> list[int]:
    _require(type(value) is list and len(value) == count and
             all(type(token) is int and 0 <= token < vocab for token in value),
             label + " token IDs mismatch")
    return value


def _parse_trace(raw: bytes, count: int, label: str) -> list[dict]:
    _require(type(raw) is bytes and 0 < len(raw) <= _TRACE_LIMIT,
             label + " trace must be bounded bytes")
    _require(raw.endswith(b"\n") and not raw.endswith(b"\n\n"),
             label + " trace must have exact newline framing")
    lines = raw.splitlines(keepends=True)
    _require(len(lines) == count, label + " trace event count mismatch")
    events = []
    for line in lines:
        _require(line.endswith(b"\n") and not line.endswith(b"\r\n")
                 and 1 < len(line) <= _LINE_LIMIT,
                 label + " trace line framing/bound mismatch")
        events.append(_json(line[:-1], _LINE_LIMIT, label + " trace event"))
    return events


def _expert_index(donor: dict) -> dict[tuple[int, int], tuple[int, list[dict]]]:
    result = {}
    for row in donor["expert_weights"]:
        ordered = [(item, "w13") for item in row["w13"]["tensors"]]
        ordered.append((row["w2"]["tensor"], "w2"))
        result[(row["layer"], row["expert_id"])] = (row["total_bytes"], ordered)
    return result


def _expected_tensors(index, layer: int, ids: list[int], sequence: int):
    tensors = []
    expert_bytes = 0
    for expert_id in ids:
        total, descriptors = index[(layer, expert_id)]
        expert_bytes += total
        for item, kind in descriptors:
            tensors.append(dict(
                expert_id=expert_id, tensor=item["tensor"], tensor_kind=kind,
                source_shard=item["source_shard"],
                source_offset_begin=item["file_offset_begin"],
                source_offset_end=item["file_offset_end"],
                tensor_bytes=item["bytes"], dtype=item["dtype"],
                shape=item["shape"], access_order_sequence=sequence,
                source_shard_sha256=item["source_shard_sha256"],
                page_begin=item["file_offset_begin"] // _PAGE_BYTES,
                page_end=(item["file_offset_end"] + _PAGE_BYTES - 1) // _PAGE_BYTES))
            sequence += 1
    return tensors, expert_bytes, sequence


def validate_frozen_trace(snapshot, arms, *, expected_attempt_dir: str):
    """Validate exact supplied buffers without reading paths or authenticating origin."""
    _require(type(snapshot) is HFMetadataSnapshot,
             "metadata input must be a retained HF snapshot")
    _posix_absolute(expected_attempt_dir, "expected attempt directory")
    _require(type(arms) is tuple and len(arms) == 3
             and all(type(arm) is FrozenTraceArm for arm in arms),
             "arms must be an immutable three-record tuple")
    _require(all(type(arm.arm) is str for arm in arms),
             "arm names must be exact text")
    _require(tuple(arm.arm for arm in arms) == _ARM_NAMES,
             "arms must be ordered native/capture/repeat")
    for index, arm in enumerate(arms):
        for field in ("input_binding", "protocol", "raw_return", "frozen_donor"):
            _require(type(getattr(arm, field)) is bytes,
                     arm.arm + " " + field + " must be exact bytes")
        for field in ("raw_routes", "routing_trace", "trace_summary"):
            expected_type = type(None) if index == 0 else bytes
            _require(type(getattr(arm, field)) is expected_type,
                     arm.arm + " " + field + " has the wrong exact type")
    _preflight_snapshot_json(snapshot)
    receipt, artifacts, donor, table = _unpack(snapshot)
    _guard_metadata(receipt, artifacts, donor, table)
    donor_raw = artifacts["donor.json"]
    config = _json(artifacts["metadata/config.json"], LIMITS["file_bytes"] + 8,
                   "metadata config")
    bindings = [_json(arm.input_binding, _JSON_LIMIT, arm.arm + " binding")
                for arm in arms]
    for binding in bindings:
        _require(set(binding) == _BINDING_KEYS, "input binding schema mismatch")
        _require(type(binding["test_only"]) is bool,
                 "input binding test_only must be an exact boolean")
    _require(bindings[0]["test_only"] is bindings[1]["test_only"]
             and bindings[1]["test_only"] is bindings[2]["test_only"],
             "input binding test_only declarations differ")
    effective_test_only = receipt["evidence"] == "TEST_ONLY" or bindings[0]["test_only"]
    expected_protocol = make_protocol(receipt, config, list(range(1000, 1032)))
    if effective_test_only:
        expected_protocol.update(source_kind="TEST_ONLY", provenance="MOCK")
    protocols = [_json(arm.protocol, _JSON_LIMIT, arm.arm + " protocol")
                 for arm in arms]
    for protocol in protocols:
        _same(protocol, expected_protocol, "stored protocol")
    _same(protocols[1], protocols[0], "capture/native protocol")
    _same(protocols[2], protocols[0], "repeat/native protocol")
    protocol_shas = [_sha(arm.protocol) for arm in arms]
    protocol_sha = protocol_shas[0]
    receipt_sha = _sha(snapshot.receipt_bytes)
    complete_sha = _sha(snapshot.complete_bytes)
    donor_sha = _sha(donor_raw)
    binding_provenance = "MOCK" if effective_test_only else "UNVALIDATED_ROUTING_CAPTURE"
    shared_keys = _BINDING_KEYS - {"arm", "work_dir"}
    for index, (arm, binding) in enumerate(zip(arms, bindings)):
        _require(arm.frozen_donor == donor_raw,
                 arm.arm + " frozen donor differs from retained metadata")
        expected = dict(
            arm=arm.arm, checkpoint=receipt["checkpoint"],
            metadata_receipt_sha256=receipt_sha,
            metadata_complete_sha256=complete_sha,
            metadata_identity_sha256=receipt["metadata_identity_sha256"],
            observation_identity_sha256=receipt["observation_identity_sha256"],
            donor_sha256=donor_sha, model_fingerprint=donor["ModelFingerprint"],
            protocol_sha256=protocol_shas[index], provenance=binding_provenance,
            test_only=bindings[0]["test_only"], scientific_validation_passed=False)
        for key, value in expected.items():
            _same(binding.get(key), value, arm.arm + " binding " + key)
        for key in ("runtime_source_manifest_sha256",
                    "selected_tuning_manifest_sha256",
                    "selected_tuning_input_report_sha256"):
            _hex(binding[key], _HEX64, arm.arm + " declared " + key)
        _text(binding["device_name_declared"], arm.arm + " declared device", 512)
        _text(binding["gpu_uuid"], arm.arm + " declared GPU", 256)
        _require(binding["gpu_uuid"].startswith("GPU-") and "," not in binding["gpu_uuid"],
                 arm.arm + " declared GPU is invalid")
        _text(binding["run_id"], arm.arm + " run ID", 128)
        _hex(binding["git_commit"], _HEX40, arm.arm + " git commit")
        _hex(binding["environment_fingerprint"], _HEX64,
             arm.arm + " environment fingerprint")
        if index:
            for key in shared_keys:
                _same(binding[key], bindings[0][key], "cross-arm binding " + key)
    parents = [_work_parent(binding["work_dir"], arm.arm)
               for arm, binding in zip(arms, bindings)]
    _require(len(set(binding["work_dir"] for binding in bindings)) == 3
             and len(set(parents)) == 1,
             "arm work paths must be distinct under one private parent")

    raw_returns = [_json(arm.raw_return, _RAW_LIMIT, arm.arm + " raw return")
                   for arm in arms]
    prompt = expected_protocol["prompt_token_ids"]
    vocab = expected_protocol["vocab_size"]
    outputs = []
    decoded = []
    for index, (arm, raw_return) in enumerate(zip(arms, raw_returns)):
        _require(set(raw_return) == _RAW_KEYS, "raw return schema mismatch")
        _require(type(raw_return["schema_version"]) is int
                 and raw_return["schema_version"] == 1,
                 "raw return schema version mismatch")
        _text(raw_return["request_id"], arm.arm + " request ID", 128)
        _same(_tokens(raw_return["prompt_token_ids"], 32, vocab,
                      arm.arm + " prompt"), prompt, arm.arm + " prompt")
        outputs.append(_tokens(raw_return["output_token_ids"], 8, vocab,
                               arm.arm + " output"))
        _require(type(raw_return["output_index"]) is int
                 and raw_return["output_index"] == 0
                 and raw_return["finished"] is True
                 and type(raw_return["finish_reason"]) is str
                 and raw_return["finish_reason"] == "length"
                 and raw_return["stop_reason"] is None,
                 arm.arm + " completion state mismatch")
        cached = raw_return["num_cached_tokens"]
        _require(cached is None or (type(cached) is int and cached == 0),
                 arm.arm + " cached-token declaration mismatch")
        _require(raw_return["source_kind"] == expected_protocol["source_kind"]
                 and raw_return["provenance"] == expected_protocol["provenance"]
                 and raw_return["origin_validation"] == "UNVALIDATED_RETURN_SERIALIZATION"
                 and raw_return["scientific_validation_passed"] is False,
                 arm.arm + " raw-return claim boundary mismatch")
        if index == 0:
            _require(raw_return["route_shape"] is None
                     and raw_return["route_dtype"] is None
                     and arm.raw_routes is None and arm.routing_trace is None
                     and arm.trace_summary is None,
                     "native arm must contain no supplied route artifacts")
            decoded.append(None)
        else:
            _same(raw_return["route_shape"], expected_protocol["route_shape"],
                  arm.arm + " route shape")
            _require(type(raw_return["route_dtype"]) is str
                     and raw_return["route_dtype"] in _DTYPES,
                     arm.arm + " route dtype is unsupported")
            _require(type(arm.raw_routes) is bytes
                     and type(arm.routing_trace) is bytes
                     and type(arm.trace_summary) is bytes,
                     arm.arm + " route artifacts must be exact bytes")
            decoded.append(decode_route_array(
                arm.raw_routes, expected_protocol,
                expected_dtype=raw_return["route_dtype"]))
    _require(outputs[0] == outputs[1] == outputs[2],
             "output token IDs differ across arms")
    _require(decoded[1].shape == decoded[2].shape
             and decoded[1].dtype == decoded[2].dtype
             and decoded[1].values == decoded[2].values,
             "capture and repeat decoded routes differ")

    expert_index = _expert_index(donor)
    expected_events = expected_protocol["expected_counts"]["event_count"]
    legacy_checks = []
    for arm_index in (1, 2):
        arm = arms[arm_index]
        binding = bindings[arm_index]
        raw_return = raw_returns[arm_index]
        events = _parse_trace(arm.routing_trace, expected_events, arm.arm)
        sequence = 0
        previous_timestamp = 0
        for event_index, event in enumerate(events):
            token = event_index // expected_protocol["layers"]
            layer = event_index % expected_protocol["layers"]
            route_begin = ((token * expected_protocol["layers"] + layer)
                           * expected_protocol["top_k"])
            ids = list(decoded[arm_index].values[
                route_begin:route_begin + expected_protocol["top_k"]])
            tensors, expert_bytes, end = _expected_tensors(
                expert_index, layer, ids, sequence)
            phase = "prefill" if token < 32 else "decode"
            expected = dict(
                schema_version=1, run_id=binding["run_id"],
                request_id=raw_return["request_id"], sequence_id=0, prompt_id=0,
                phase=phase, token_step=token if phase == "prefill" else token - 32,
                route_token_index=token, layer_id=layer, topk_expert_ids=ids,
                topk_weights=None,
                topk_weights_availability="not exposed by returned-routes API",
                expert_tensors=tensors, expert_access_bytes=expert_bytes,
                access_order_sequence_begin=sequence,
                access_order_sequence_end=end,
                host_timestamp_semantics="post-request JSONL materialization",
                gpu_event_timestamp_ns=None, previous_compute_gap_ns=None,
                capture_source=("TEST_ONLY routed-array fixture" if effective_test_only
                                else "caller-supplied vLLM returned routes; origin unvalidated"),
                capture_validation_status="UNVALIDATED",
                model_fingerprint=donor["ModelFingerprint"],
                inventory_sha256=donor_sha,
                environment_fingerprint=binding["environment_fingerprint"],
                git_commit=binding["git_commit"])
            _require(set(event) == _EVENT_KEYS, arm.arm + " event schema mismatch")
            timestamp = event.get("host_monotonic_timestamp_ns")
            _int(timestamp, arm.arm + " host timestamp", 1)
            _require(timestamp >= previous_timestamp,
                     arm.arm + " host timestamps decrease")
            previous_timestamp = timestamp
            without_timestamp = dict(event)
            without_timestamp.pop("host_monotonic_timestamp_ns")
            _same(without_timestamp, expected, arm.arm + " independently derived event")
            for tensor in event["expert_tensors"]:
                _require(type(tensor) is dict and set(tensor) == _TENSOR_KEYS,
                         arm.arm + " tensor event schema mismatch")
            sequence = end
        summary = _json(arm.trace_summary, _JSON_LIMIT, arm.arm + " trace summary")
        _require(set(summary) == _SUMMARY_KEYS, arm.arm + " trace summary schema mismatch")
        expected_summary = dict(
            schema_version=1, status="CAPTURED_UNVALIDATED",
            evidence_class=("TEST_ONLY" if effective_test_only
                            else "UNVALIDATED_ROUTING_CAPTURE"),
            scientific_validation_passed=False,
            donor_commit="37144843906b3bd71f3fbac1fecc6b5080d82b95",
            trace_path=expected_attempt_dir + "/arms/" + arm.arm + "/routing.jsonl",
            trace_sha256=_sha(arm.routing_trace), request_count=1,
            event_count=expected_events,
            expert_access_count=expected_protocol["expected_counts"]["expert_access_count"],
            tensor_access_count=expected_protocol["expected_counts"]["tensor_access_count"],
            access_order_sequence_count=expected_protocol["expected_counts"]["tensor_access_count"],
            per_layer_event_count={str(layer): 39
                                   for layer in range(expected_protocol["layers"])},
            per_phase_event_count={
                "prefill": expected_protocol["expected_counts"]["prefill_event_count"],
                "decode": expected_protocol["expected_counts"]["decode_event_count"]},
            model_fingerprint=donor["ModelFingerprint"], inventory_sha256=donor_sha,
            environment_fingerprint=binding["environment_fingerprint"],
            git_commit=binding["git_commit"], boundary=_BOUNDARY)
        _same(summary, expected_summary, arm.arm + " direct trace summary")
        inventory = ModelInventory(_ImmutableText(donor_raw))
        legacy_summary = canonical(dict(
            protocol=dict(num_prompts=1, input_len=32, output_len=8),
            trace=summary))
        legacy = validate_trace(_ImmutableText(arm.routing_trace),
                                _ImmutableText(legacy_summary), inventory)
        _require(type(legacy) is dict and
                 all(value is True for value in legacy["validation"]["checks"].values()),
                 arm.arm + " supplemental legacy validation failed")
        legacy_checks.append(True)

    artifact_reports = []
    names = (("input-binding.json", "input_binding"),
             ("protocol.json", "protocol"), ("raw-return.json", "raw_return"),
             ("frozen-donor.json", "frozen_donor"),
             ("raw-routes.npy", "raw_routes"),
             ("routing.jsonl", "routing_trace"),
             ("trace-summary.json", "trace_summary"))
    for arm in arms:
        artifact_reports.append(dict(
            arm=arm.arm,
            artifacts={name: None if getattr(arm, field) is None
                       else _sha(getattr(arm, field)) for name, field in names}))
    report = dict(
        schema_version=1, status="ROUTE_CONSISTENCY_ONLY",
        evidence_class=("TEST_ONLY" if effective_test_only
                        else "UNVALIDATED_ROUTING_CAPTURE"),
        source_kind=expected_protocol["source_kind"],
        provenance=expected_protocol["provenance"],
        effective_test_only=effective_test_only,
        scientific_validation_passed=False,
        metadata=dict(
            receipt_sha256=receipt_sha, complete_sha256=complete_sha,
            donor_sha256=donor_sha,
            metadata_identity_sha256=receipt["metadata_identity_sha256"],
            observation_identity_sha256=receipt["observation_identity_sha256"],
            protocol_sha256=protocol_sha,
            runtime_source_manifest_sha256=bindings[0]["runtime_source_manifest_sha256"],
            selected_tuning_manifest_sha256=bindings[0]["selected_tuning_manifest_sha256"],
            selected_tuning_input_report_sha256=bindings[0]["selected_tuning_input_report_sha256"]),
        derived_counts=dict(expected_protocol["expected_counts"]),
        checks=dict(token_ids_equal=True, decoded_routes_equal=True,
                    independent_event_derivation=True,
                    supplemental_legacy_validation=all(legacy_checks)),
        arms=artifact_reports,
        limitations=[
            "Caller-supplied buffers do not authenticate process origin, current original files, runtime settings, hardware, or scientific validity.",
            "Runtime and tuning hashes are checked only as shared declarations because their original buffers are absent.",
            "A native None value states only that this supplied record has no route artifacts; it does not establish directory absence or completeness.",
            "Host timestamps describe JSONL materialization and are not inference latency.",
            "POSIX paths are checked textually and no filesystem identity is established.",
        ])
    _require(len(canonical(report)) <= _JSON_LIMIT,
             "consistency report exceeds its bounded size")
    return report
