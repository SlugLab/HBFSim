#!/usr/bin/env python3
"""Generate canonical model-size-driven offered-byte workload windows.

The output is demand, not delivered service.  It deliberately permits offered
rates above a channel's service capacity so a separate fluid model can retain
the excess as backlog rather than silently dropping it here.
"""

from __future__ import annotations

import argparse
from fractions import Fraction
import json
from pathlib import Path
from typing import Any


CONFIG_SCHEMA = "eq3-rate-model-workload-config-v1"
OUTPUT_SCHEMA = "eq3-rate-model-workload-v1"
CATALOG_VERSION = "eq3-official-weight-metadata-2026-09-20-v1"
STEP_NS = 20_000_000
CHANNELS_PER_STACK = 16
ALLOWED_STACK_COUNTS = (4, 8)
ALLOWED_SCANS_PER_S = (16, 32)
PATTERNS = ("continuous", "burst_equal_mean")

MODEL_CATALOG = {
    "Qwen/Qwen2.5-7B-Instruct": {
        "tensor_payload_bytes": 15_231_233_024,
        "revision": "main",
        "resolved_commit": "UNKNOWN",
        "source_class": "DOC_DERIVED_OFFICIAL_METADATA",
        "source": "experiments/eq3_maintenance/sources/qwen2_5_weight_models.json",
        "workload_interpretation": "SYNTHETIC_FULL_STORED_WEIGHT_SCAN",
    },
    "Qwen/Qwen2.5-72B-Instruct": {
        "tensor_payload_bytes": 145_412_407_296,
        "revision": "main",
        "resolved_commit": "UNKNOWN",
        "source_class": "DOC_DERIVED_OFFICIAL_METADATA",
        "source": "experiments/eq3_maintenance/sources/qwen2_5_weight_models.json",
        "workload_interpretation": "SYNTHETIC_FULL_STORED_WEIGHT_SCAN",
    },
    "Qwen/Qwen3-235B-A22B": {
        "tensor_payload_bytes": 470_187_269_120,
        "revision": "8efa61729e24bd65b1d152b5ab5409052aa80e65",
        "resolved_commit": "8efa61729e24bd65b1d152b5ab5409052aa80e65",
        "source_class": "DOC_DERIVED_OFFICIAL_METADATA",
        "source": (
            "eq3_thermal/plans/isolated-maintenance-campaign-v1/"
            "large-model-sources/EXTENT_AUDIT.json"
        ),
        "workload_interpretation": "SYNTHETIC_FULL_STORED_WEIGHT_SCAN_NOT_MOE_TOKEN_TRACE",
        "model_card_parameters": "235B_TOTAL_22B_ACTIVE",
    },
}


def _mapping(value: Any, path: str) -> dict:
    if not isinstance(value, dict):
        raise ValueError(f"{path} must be an object")
    return value


def _integer(value: Any, path: str, *, positive: bool = False) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{path} must be an integer")
    if positive and value <= 0:
        raise ValueError(f"{path} must be positive")
    return value


def _validate_config(config: dict) -> dict:
    config = _mapping(config, "config")
    allowed_keys = {
        "schema_version", "model_id", "full_scans_per_s", "pattern",
        "stack_count", "channels_per_stack", "step_ns", "active_ns",
        "recovery_ns", "burst_period_ns", "burst_on_ns",
    }
    unknown = sorted(set(config) - allowed_keys)
    if unknown:
        raise ValueError(f"config contains unknown fields: {unknown}")
    if config.get("schema_version") != CONFIG_SCHEMA:
        raise ValueError(f"config.schema_version must equal {CONFIG_SCHEMA!r}")
    model_id = config.get("model_id")
    if model_id not in MODEL_CATALOG:
        raise ValueError(f"config.model_id is not in catalog {CATALOG_VERSION!r}")
    scans = _integer(config.get("full_scans_per_s"), "config.full_scans_per_s", positive=True)
    if scans not in ALLOWED_SCANS_PER_S:
        raise ValueError(f"config.full_scans_per_s must be one of {ALLOWED_SCANS_PER_S}")
    pattern = config.get("pattern")
    if pattern not in PATTERNS:
        raise ValueError(f"config.pattern must be one of {PATTERNS}")
    stack_count = _integer(config.get("stack_count"), "config.stack_count", positive=True)
    if stack_count not in ALLOWED_STACK_COUNTS:
        raise ValueError(f"config.stack_count must be one of {ALLOWED_STACK_COUNTS}")
    channel_count = _integer(
        config.get("channels_per_stack"), "config.channels_per_stack", positive=True
    )
    if channel_count != CHANNELS_PER_STACK:
        raise ValueError(f"config.channels_per_stack must equal {CHANNELS_PER_STACK}")
    step_ns = _integer(config.get("step_ns"), "config.step_ns", positive=True)
    if step_ns != STEP_NS:
        raise ValueError(f"config.step_ns must equal {STEP_NS}")
    active_ns = _integer(config.get("active_ns"), "config.active_ns", positive=True)
    recovery_ns = _integer(config.get("recovery_ns"), "config.recovery_ns")
    if recovery_ns < 0:
        raise ValueError("config.recovery_ns must be nonnegative")
    if active_ns % step_ns or recovery_ns % step_ns:
        raise ValueError("config active_ns and recovery_ns must align to step_ns")

    period_ns = config.get("burst_period_ns")
    on_ns = config.get("burst_on_ns")
    if pattern == "continuous":
        if period_ns is not None or on_ns is not None:
            raise ValueError("continuous config must omit burst_period_ns and burst_on_ns")
        period_ns = None
        on_ns = None
    else:
        period_ns = _integer(period_ns, "config.burst_period_ns", positive=True)
        on_ns = _integer(on_ns, "config.burst_on_ns", positive=True)
        if period_ns != 200_000_000 or on_ns != 100_000_000:
            raise ValueError("burst_equal_mean requires a 200ms period and 100ms on interval")
        if period_ns % step_ns or on_ns % step_ns or active_ns % period_ns:
            raise ValueError("burst timing and active_ns must align to complete thermal windows/periods")

    equivalent_scans = Fraction(scans * active_ns, 1_000_000_000)
    if equivalent_scans.denominator != 1:
        raise ValueError("active_ns must produce an integer number of equivalent full scans")
    canonical = {
        "schema_version": CONFIG_SCHEMA,
        "model_id": model_id,
        "full_scans_per_s": scans,
        "pattern": pattern,
        "stack_count": stack_count,
        "channels_per_stack": channel_count,
        "step_ns": step_ns,
        "active_ns": active_ns,
        "recovery_ns": recovery_ns,
    }
    if pattern == "burst_equal_mean":
        canonical["burst_period_ns"] = period_ns
        canonical["burst_on_ns"] = on_ns
    return canonical


def _multiplier(config: dict, start_ns: int) -> int:
    if start_ns >= config["active_ns"]:
        return 0
    if config["pattern"] == "continuous":
        return 1
    return 2 if start_ns % config["burst_period_ns"] < config["burst_on_ns"] else 0


def build_workload(config: dict) -> dict:
    """Return canonical per-window, per-stack, per-channel offered bytes."""

    config = _validate_config(config)
    model = dict(MODEL_CATALOG[config["model_id"]])
    payload_bytes = model["tensor_payload_bytes"]
    stack_ids = [f"hbf{index}" for index in range(config["stack_count"])]
    channel_ids = [str(index) for index in range(config["channels_per_stack"])]
    destinations = [(stack_id, channel_id) for stack_id in stack_ids for channel_id in channel_ids]
    destination_count = len(destinations)
    end_ns = config["active_ns"] + config["recovery_ns"]
    exact_carry = Fraction(0, 1)
    stripe_cursor = 0
    windows = []
    total_emitted = 0

    for start_ns in range(0, end_ns, config["step_ns"]):
        end_window_ns = start_ns + config["step_ns"]
        multiplier = _multiplier(config, start_ns)
        exact_window_bytes = Fraction(
            payload_bytes * config["full_scans_per_s"] * config["step_ns"] * multiplier,
            1_000_000_000,
        )
        exact_carry += exact_window_bytes
        emitted_window_bytes = exact_carry.numerator // exact_carry.denominator
        exact_carry -= emitted_window_bytes

        base, remainder = divmod(emitted_window_bytes, destination_count)
        flat = [base] * destination_count
        for offset in range(remainder):
            flat[(stripe_cursor + offset) % destination_count] += 1
        stripe_cursor = (stripe_cursor + remainder) % destination_count
        offered = {stack_id: {channel_id: 0 for channel_id in channel_ids} for stack_id in stack_ids}
        for (stack_id, channel_id), byte_count in zip(destinations, flat):
            offered[stack_id][channel_id] = byte_count
        if sum(sum(channels.values()) for channels in offered.values()) != emitted_window_bytes:
            raise AssertionError("uniform stripe did not conserve offered bytes")
        windows.append({
            "start_ns": start_ns,
            "end_ns": end_window_ns,
            "stack_channel_offered_bytes": offered,
            "total_offered_bytes": emitted_window_bytes,
            "demand_multiplier": multiplier,
        })
        total_emitted += emitted_window_bytes

    expected_active_bytes = payload_bytes * int(
        Fraction(config["full_scans_per_s"] * config["active_ns"], 1_000_000_000)
    )
    if exact_carry != 0 or total_emitted != expected_active_bytes:
        raise AssertionError("offered-byte generation did not conserve full-scan demand")

    offered_mean_bps = payload_bytes * config["full_scans_per_s"]
    metadata = {
        "catalog_version": CATALOG_VERSION,
        "model_id": config["model_id"],
        "model_revision": model["revision"],
        "model_resolved_commit": model["resolved_commit"],
        "model_source_class": model["source_class"],
        "model_source": model["source"],
        "weight_bytes": payload_bytes,
        "page_bytes": 4096,
        "full_scans_per_s": config["full_scans_per_s"],
        "pattern": config["pattern"],
        "active_ns": config["active_ns"],
        "recovery_ns": config["recovery_ns"],
        "step_ns": config["step_ns"],
        "stack_count": config["stack_count"],
        "channels_per_stack": config["channels_per_stack"],
        "stack_ids": stack_ids,
        "channel_ids": channel_ids,
        "burst_period_ns": config.get("burst_period_ns"),
        "burst_on_ns": config.get("burst_on_ns"),
        "mean_active_offered_Bps": offered_mean_bps,
        "demand_formula": "tensor_payload_bytes * full_scans_per_s",
        "equivalent_full_scans": expected_active_bytes // payload_bytes,
        "expected_active_offered_bytes": expected_active_bytes,
        "actual_total_offered_bytes": total_emitted,
        "distribution": "UNIFORM_STRIPED_GLOBAL_FRACTIONAL_CARRY_ROTATING_REMAINDER",
        "semantics": {
            "bytes": "OFFERED_DEMAND_NOT_DELIVERED_SERVICE",
            "capacity": "NOT_APPLIED_HERE_EXCESS_MUST_ENTER_FLUID_BACKLOG",
            "scan": model["workload_interpretation"],
            "token_per_s": "UNKNOWN",
            "idle_or_recovery": "ZERO_OFFERED_READ_NOT_ZERO_PHYSICAL_IDLE_POWER",
        },
        "provenance": "USER_CONFIRMED_METHOD_WITH_DOC_DERIVED_OFFICIAL_WEIGHT_BYTES",
    }
    if "model_card_parameters" in model:
        metadata["model_card_parameters"] = model["model_card_parameters"]
        metadata["moe_limit"] = (
            "FULL_235B_SCAN_IS_SYNTHETIC_PRESSURE; 22B_ACTIVE_MODEL_CARD_LABEL; "
            "DO_NOT_INTERPRET_AS_ALL_WEIGHTS_PER_TOKEN"
        )
    return {
        "schema_version": OUTPUT_SCHEMA,
        "metadata": metadata,
        "canonical_config": config,
        "windows": windows,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)
    config = json.loads(args.config.read_text())
    result = build_workload(config)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True, allow_nan=False) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
