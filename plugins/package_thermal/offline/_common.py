#!/usr/bin/env python3
"""Shared strict helpers for the external/offline package-thermal workflow."""

from __future__ import annotations

import csv
import hashlib
import json
import math
import pathlib
import struct
from typing import Any, Iterable


class OfflineError(RuntimeError):
    pass


EVIDENCE_LABELS = (
    "synthetic_fixture",
    "literature_parameterized",
    "calibrated_external_solver",
    "measured",
)
_EVIDENCE_RANK = {label: rank for rank, label in enumerate(EVIDENCE_LABELS)}


def validate_evidence_label(value: Any, where: str) -> str:
    if not isinstance(value, str) or value not in _EVIDENCE_RANK:
        raise OfflineError(
            f"{where} must be one of {list(EVIDENCE_LABELS)}")
    return value


def select_evidence_label(requested: str | None, sources: Iterable[str],
                          where: str) -> str:
    labels = [validate_evidence_label(label, f"{where} source evidence")
              for label in sources]
    if not labels:
        raise OfflineError(f"{where} has no source evidence labels")
    ceiling = min(labels, key=_EVIDENCE_RANK.__getitem__)
    if requested is None:
        return ceiling
    selected = validate_evidence_label(requested, f"{where} evidence")
    if _EVIDENCE_RANK[selected] > _EVIDENCE_RANK[ceiling]:
        raise OfflineError(
            f"{where} evidence {selected!r} would upgrade source evidence "
            f"above {ceiling!r}")
    return selected


def portable_relative_path(value: Any, where: str) -> pathlib.Path:
    if not isinstance(value, str) or not value or "\x00" in value:
        raise OfflineError(f"{where} must be a non-empty relative path")
    normalized = value.replace("\\", "/")
    pure = pathlib.PurePosixPath(normalized)
    if (pure.is_absolute() or not pure.parts or
            any(part in {"", ".", ".."} for part in pure.parts) or
            ":" in pure.parts[0]):
        raise OfflineError(f"{where} must be a portable relative path")
    return pathlib.Path(*pure.parts)


def load_json(path: pathlib.Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise OfflineError(f"failed to load JSON {path}: {error}") from error
    if not isinstance(value, dict):
        raise OfflineError(f"JSON root must be an object: {path}")
    return value


def write_json(path: pathlib.Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n",
                         encoding="utf-8")
    temporary.replace(path)


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      allow_nan=False)


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: pathlib.Path) -> str:
    try:
        return sha256_bytes(path.read_bytes())
    except OSError as error:
        raise OfflineError(f"failed to hash {path}: {error}") from error


def rom_payload_sha256(payload: dict, schema_version: int) -> str:
    """Hash a ROM payload with the schema's cross-language encoding."""
    if schema_version == 1:
        return sha256_bytes(canonical_json(payload).encode())
    if schema_version != 2:
        raise OfflineError(f"unsupported ROM hash schema: {schema_version}")
    encoded = bytearray(b"HBFSimRomPayloadV2\0")

    def unsigned(value: int) -> None:
        encoded.extend(struct.pack(">Q", value))

    def string(value: str) -> None:
        raw = value.encode("utf-8")
        unsigned(len(raw))
        encoded.extend(raw)

    def strings(values: list[str]) -> None:
        unsigned(len(values))
        for value in values:
            string(value)

    def doubles(values: list[float]) -> None:
        unsigned(len(values))
        for value in values:
            encoded.extend(struct.pack(">d", float(value)))

    string(payload["model_id"])
    string(payload["evidence_label"])
    unsigned(payload["sample_period_ns"])
    strings(payload["input_names"])
    strings(payload["output_names"])
    unsigned(payload["state_count"])
    for field in ("a", "b", "bias", "c", "d", "offset"):
        doubles(payload[field])
    for field in ("solver_identity", "geometry_sha256", "training_split",
                  "held_out_split"):
        string(payload[field])
    doubles([payload["held_out_max_error_c"],
             payload["held_out_p95_error_c"]])
    return sha256_bytes(bytes(encoded))


def require_keys(value: dict[str, Any], required: set[str], where: str) -> None:
    actual = set(value)
    if actual != required:
        missing = sorted(required - actual)
        unknown = sorted(actual - required)
        raise OfflineError(
            f"{where} fields mismatch; missing={missing}, unknown={unknown}")


def finite_number(value: Any, where: str, *, nonnegative: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise OfflineError(f"{where} must be numeric")
    result = float(value)
    if not math.isfinite(result) or (nonnegative and result < 0.0):
        raise OfflineError(f"{where} must be finite" +
                           (" and non-negative" if nonnegative else ""))
    return result


def write_power_csv(path: pathlib.Path, names: list[str], rows: Iterable[tuple[int, list[float]]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as output:
        writer = csv.writer(output, lineterminator="\n")
        writer.writerow(["time_ns", *names])
        for timestamp, power in rows:
            if len(power) != len(names):
                raise OfflineError("power row dimension mismatch")
            writer.writerow([timestamp, *power])


def load_dataset(path: pathlib.Path) -> dict[str, Any]:
    value = load_json(path)
    required = {"schema_version", "trace_id", "trace_kind", "sample_period_ns",
                "input_names", "output_names", "samples", "provenance"}
    require_keys(value, required, f"dataset {path}")
    if value["schema_version"] != 1:
        raise OfflineError(f"unsupported dataset schema in {path}")
    provenance = value["provenance"]
    if not isinstance(provenance, dict):
        raise OfflineError(f"dataset provenance must be an object: {path}")
    validate_evidence_label(provenance.get("evidence_label"),
                            f"dataset {path} provenance.evidence_label")
    inputs = value["input_names"]
    outputs = value["output_names"]
    if (not isinstance(inputs, list) or not inputs or
            not all(isinstance(item, str) and item for item in inputs) or
            len(set(inputs)) != len(inputs)):
        raise OfflineError(f"invalid input_names in {path}")
    if (not isinstance(outputs, list) or not outputs or
            not all(isinstance(item, str) and item for item in outputs) or
            len(set(outputs)) != len(outputs)):
        raise OfflineError(f"invalid output_names in {path}")
    period = value["sample_period_ns"]
    if not isinstance(period, int) or isinstance(period, bool) or period <= 0:
        raise OfflineError(f"invalid sample period in {path}")
    samples = value["samples"]
    if not isinstance(samples, list) or len(samples) < 2:
        raise OfflineError(f"dataset must contain at least two samples: {path}")
    previous = None
    for index, sample in enumerate(samples):
        if not isinstance(sample, dict):
            raise OfflineError(f"invalid sample {index} in {path}")
        require_keys(sample, {"time_ns", "power_w", "temperature_c"},
                     f"sample {index} in {path}")
        timestamp = sample["time_ns"]
        if not isinstance(timestamp, int) or isinstance(timestamp, bool):
            raise OfflineError(f"invalid timestamp in {path}")
        if previous is not None and timestamp - previous != period:
            raise OfflineError(f"timestamps are not uniformly sampled in {path}")
        previous = timestamp
        if len(sample["power_w"]) != len(inputs) or len(sample["temperature_c"]) != len(outputs):
            raise OfflineError(f"sample dimensions mismatch in {path}")
        sample["power_w"] = [finite_number(item, f"power in {path}", nonnegative=True)
                             for item in sample["power_w"]]
        sample["temperature_c"] = [finite_number(item, f"temperature in {path}")
                                   for item in sample["temperature_c"]]
        if any(item < -273.15 for item in sample["temperature_c"]):
            raise OfflineError(f"temperature below absolute zero in {path}")
    return value


def percentile(values: list[float], fraction: float) -> float:
    if not values:
        raise OfflineError("cannot compute percentile of empty values")
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1,
                       math.ceil(fraction * len(ordered)) - 1))
    return ordered[index]
