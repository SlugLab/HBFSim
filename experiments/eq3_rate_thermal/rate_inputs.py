"""Convert prescribed HBF read-rate schedules to incremental thermal energy.

This module is deliberately disconnected from every runtime by default.  It
does not issue storage requests or estimate achieved backend throughput.
"""

from __future__ import annotations

import math
from typing import Any


SCHEMA_VERSION = 1
THERMAL_STEP_NS = 20_000_000
REFERENCE_READ_BPS = 1.6e12
ARRAY_J_PER_BYTE = 40e-12
BASE_J_PER_BYTE = 10e-12
PROVENANCE = "SCENARIO_ASSUMPTION_USER_CONFIRMED"


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


def _finite_number(value: Any, path: str, *, nonnegative: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{path} must be a finite number")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"{path} must be a finite number")
    if nonnegative and result < 0.0:
        raise ValueError(f"{path} must be nonnegative")
    return result


def _approved_parameter(profile: dict, key: str, expected: float) -> float:
    value = _finite_number(profile.get(key), f"profile.{key}")
    if value != expected:
        raise ValueError(f"profile.{key} must equal the approved value {expected!r}")
    return value


def _discover_hbf_entities(normalized: dict) -> dict[str, dict[str, Any]]:
    components = normalized.get("components")
    if not isinstance(components, list):
        raise ValueError("normalized.components must be an array")

    stacks: dict[str, dict[str, Any]] = {}
    seen_component_ids: set[str] = set()
    for index, component in enumerate(components):
        if not isinstance(component, dict):
            raise ValueError(f"normalized.components[{index}] must be an object")
        if component.get("physical_type") != "HBF" or component.get("powered") is not True:
            continue
        component_id = component.get("id")
        stack_id = component.get("device_id")
        role = component.get("role")
        if not isinstance(component_id, str) or not component_id:
            raise ValueError(f"normalized.components[{index}].id must be a nonempty string")
        if not isinstance(stack_id, str) or not stack_id:
            raise ValueError(f"normalized.components[{index}].device_id must be a nonempty string")
        if component_id in seen_component_ids:
            raise ValueError(f"duplicate powered HBF component id {component_id!r}")
        seen_component_ids.add(component_id)
        stack = stacks.setdefault(stack_id, {"base": None, "array_dies": []})
        if role == "base_die":
            if stack["base"] is not None:
                raise ValueError(f"HBF stack {stack_id!r} has more than one powered base_die")
            stack["base"] = component_id
        elif role == "array_die":
            stack["array_dies"].append(component_id)
        else:
            raise ValueError(
                f"powered HBF component {component_id!r} has unsupported role {role!r}"
            )

    if not stacks:
        raise ValueError("normalized model has no powered HBF stacks")
    for stack_id, stack in stacks.items():
        if stack["base"] is None:
            raise ValueError(f"HBF stack {stack_id!r} has no powered base_die")
        if not stack["array_dies"]:
            raise ValueError(f"HBF stack {stack_id!r} has no powered array_die")
        stack["array_dies"].sort()
    return dict(sorted(stacks.items()))


def _resolve_weights(profile: dict, stacks: dict[str, dict[str, Any]]) -> dict[str, dict[str, float]]:
    configured = profile.get("die_weights", {})
    configured = _mapping(configured, "profile.die_weights")
    unknown = sorted(set(configured) - set(stacks))
    if unknown:
        raise ValueError(f"profile.die_weights contains unknown HBF stacks: {unknown}")

    result: dict[str, dict[str, float]] = {}
    for stack_id, stack in stacks.items():
        dies = stack["array_dies"]
        if stack_id not in configured:
            weight = 1.0 / len(dies)
            result[stack_id] = {component_id: weight for component_id in dies}
            continue
        raw = _mapping(configured[stack_id], f"profile.die_weights.{stack_id}")
        if set(raw) != set(dies):
            missing = sorted(set(dies) - set(raw))
            extra = sorted(set(raw) - set(dies))
            raise ValueError(
                f"profile.die_weights.{stack_id} must cover exactly the discovered dies; "
                f"missing={missing}, extra={extra}"
            )
        weights = {
            component_id: _finite_number(
                raw[component_id],
                f"profile.die_weights.{stack_id}.{component_id}",
                nonnegative=True,
            )
            for component_id in dies
        }
        if not math.isclose(sum(weights.values()), 1.0, rel_tol=0.0, abs_tol=1e-12):
            raise ValueError(f"profile.die_weights.{stack_id} must sum to 1")
        result[stack_id] = weights
    return result


def _resolve_channels(profile: dict, stacks: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    raw_map = profile.get("channel_map")
    raw_caps = profile.get("channel_capacity_Bps")
    if raw_map is None and raw_caps is None:
        return {}
    if raw_map is None or raw_caps is None:
        raise ValueError("profile.channel_map and profile.channel_capacity_Bps must be provided together")
    raw_map = _mapping(raw_map, "profile.channel_map")
    raw_caps = _mapping(raw_caps, "profile.channel_capacity_Bps")
    if set(raw_map) != set(raw_caps):
        raise ValueError("profile.channel_map and profile.channel_capacity_Bps must cover the same stacks")
    unknown_stacks = sorted(set(raw_map) - set(stacks))
    if unknown_stacks:
        raise ValueError(f"profile.channel_map contains unknown HBF stacks: {unknown_stacks}")

    result = {}
    for stack_id in sorted(raw_map):
        mapping = _mapping(raw_map[stack_id], f"profile.channel_map.{stack_id}")
        capacities = _mapping(raw_caps[stack_id], f"profile.channel_capacity_Bps.{stack_id}")
        if not mapping:
            raise ValueError(f"profile.channel_map.{stack_id} must not be empty")
        if set(mapping) != set(capacities):
            raise ValueError(
                f"profile channel map and capacities for {stack_id!r} must cover the same channels"
            )
        actual_dies = set(stacks[stack_id]["array_dies"])
        checked_mapping = {}
        checked_capacities = {}
        for channel_id, component_id in mapping.items():
            if not isinstance(channel_id, str) or not channel_id:
                raise ValueError(f"profile.channel_map.{stack_id} channel IDs must be nonempty strings")
            if component_id not in actual_dies:
                raise ValueError(
                    f"profile.channel_map.{stack_id}.{channel_id} refers to unknown array die "
                    f"{component_id!r}"
                )
            capacity = _finite_number(
                capacities[channel_id],
                f"profile.channel_capacity_Bps.{stack_id}.{channel_id}",
                nonnegative=True,
            )
            if capacity == 0.0:
                raise ValueError(
                    f"profile.channel_capacity_Bps.{stack_id}.{channel_id} must be positive"
                )
            checked_mapping[channel_id] = component_id
            checked_capacities[channel_id] = capacity
        result[stack_id] = {"map": checked_mapping, "capacities": checked_capacities}
    return result


def _validate_schedule(
    schedule: dict,
    stack_ids: set[str],
    reference_read_bps: float,
    channels: dict[str, dict[str, Any]],
) -> tuple[int, list]:
    if schedule.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("schedule.schema_version must equal 1")
    end_ns = _integer(schedule.get("end_ns"), "schedule.end_ns", positive=True)
    segments = schedule.get("segments")
    if not isinstance(segments, list) or not segments:
        raise ValueError("schedule.segments must be a nonempty array")

    validated = []
    expected_start = 0
    for index, segment in enumerate(segments):
        segment = _mapping(segment, f"schedule.segments[{index}]")
        start_ns = _integer(segment.get("start_ns"), f"schedule.segments[{index}].start_ns")
        segment_end_ns = _integer(segment.get("end_ns"), f"schedule.segments[{index}].end_ns")
        if start_ns != expected_start:
            raise ValueError("schedule segments must continuously cover [0, end_ns) without gaps or overlap")
        if segment_end_ns <= start_ns or segment_end_ns > end_ns:
            raise ValueError(f"schedule.segments[{index}] has an invalid half-open interval")
        read_bps = _mapping(segment.get("read_Bps", {}), f"schedule.segments[{index}].read_Bps")
        unknown = sorted(set(read_bps) - stack_ids)
        if unknown:
            raise ValueError(f"schedule.segments[{index}].read_Bps has unknown HBF stacks: {unknown}")
        channel_field_present = "channel_read_Bps" in segment
        raw_channel_rates = _mapping(
            segment.get("channel_read_Bps", {}),
            f"schedule.segments[{index}].channel_read_Bps",
        )
        unknown_channel_stacks = sorted(set(raw_channel_rates) - set(channels))
        if unknown_channel_stacks:
            raise ValueError(
                f"schedule.segments[{index}].channel_read_Bps has stacks without a channel map: "
                f"{unknown_channel_stacks}"
            )
        rates = {}
        segment_channel_rates = {}
        for stack_id in stack_ids:
            explicit_channels = None
            if channel_field_present and stack_id in raw_channel_rates:
                configured = channels[stack_id]
                raw_stack_channels = _mapping(
                    raw_channel_rates[stack_id],
                    f"schedule.segments[{index}].channel_read_Bps.{stack_id}",
                )
                unknown_channels = sorted(set(raw_stack_channels) - set(configured["map"]))
                if unknown_channels:
                    raise ValueError(
                        f"schedule.segments[{index}].channel_read_Bps.{stack_id} has unknown "
                        f"channels: {unknown_channels}"
                    )
                explicit_channels = {}
                for channel_id in configured["map"]:
                    channel_rate = _finite_number(
                        raw_stack_channels.get(channel_id, 0.0),
                        f"schedule.segments[{index}].channel_read_Bps.{stack_id}.{channel_id}",
                        nonnegative=True,
                    )
                    if channel_rate > configured["capacities"][channel_id]:
                        raise ValueError(
                            f"schedule.segments[{index}].channel_read_Bps.{stack_id}.{channel_id} "
                            "exceeds its configured channel capacity"
                        )
                    explicit_channels[channel_id] = channel_rate
                channel_total = sum(explicit_channels.values())
                if stack_id in read_bps:
                    requested_total = _finite_number(
                        read_bps[stack_id],
                        f"schedule.segments[{index}].read_Bps.{stack_id}",
                        nonnegative=True,
                    )
                    if not math.isclose(requested_total, channel_total, rel_tol=1e-12, abs_tol=1e-6):
                        raise ValueError(
                            f"schedule.segments[{index}] total read_Bps and channel_read_Bps "
                            f"disagree for {stack_id!r}"
                        )
                rate = channel_total
            else:
                rate = _finite_number(
                    read_bps.get(stack_id, 0.0),
                    f"schedule.segments[{index}].read_Bps.{stack_id}",
                    nonnegative=True,
                )
            if rate > reference_read_bps:
                raise ValueError(
                    f"schedule.segments[{index}].read_Bps.{stack_id} exceeds the approved "
                    f"{reference_read_bps:g} B/s envelope"
                )
            if stack_id in channels and rate > sum(channels[stack_id]["capacities"].values()):
                raise ValueError(f"read_Bps.{stack_id} exceeds the configured aggregate channel capacity")
            rates[stack_id] = rate
            segment_channel_rates[stack_id] = explicit_channels
        validated.append((start_ns, segment_end_ns, rates, segment_channel_rates))
        expected_start = segment_end_ns
    if expected_start != end_ns:
        raise ValueError("schedule segments must continuously cover [0, end_ns) without gaps or overlap")
    return end_ns, validated


def build_windows(
    profile: dict,
    schedule: dict,
    normalized: dict,
    step_ns: int = THERMAL_STEP_NS,
) -> dict:
    """Build 20 ms component-energy windows from a prescribed rate schedule.

    All time intervals are integer-nanosecond, half-open intervals.  A stack
    omitted from a segment has zero *read* energy in that segment; no idle
    power is inferred.
    """

    profile = _mapping(profile, "profile")
    schedule = _mapping(schedule, "schedule")
    normalized = _mapping(normalized, "normalized")
    if profile.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("profile.schema_version must equal 1")
    if profile.get("provenance") != PROVENANCE:
        raise ValueError(f"profile.provenance must equal {PROVENANCE!r}")
    reference_read_bps = _approved_parameter(profile, "reference_read_Bps", REFERENCE_READ_BPS)
    array_j_per_byte = _approved_parameter(profile, "array_j_per_byte", ARRAY_J_PER_BYTE)
    base_j_per_byte = _approved_parameter(profile, "base_j_per_byte", BASE_J_PER_BYTE)
    step_ns = _integer(step_ns, "step_ns", positive=True)
    if step_ns != THERMAL_STEP_NS:
        raise ValueError(f"step_ns must equal the approved thermal window {THERMAL_STEP_NS}")

    stacks = _discover_hbf_entities(normalized)
    weights = _resolve_weights(profile, stacks)
    channels = _resolve_channels(profile, stacks)
    end_ns, segments = _validate_schedule(
        schedule, set(stacks), reference_read_bps, channels
    )

    component_ids = sorted(
        component_id
        for stack in stacks.values()
        for component_id in [stack["base"], *stack["array_dies"]]
    )
    windows = []
    segment_index = 0
    for window_start_ns in range(0, end_ns, step_ns):
        window_end_ns = min(window_start_ns + step_ns, end_ns)
        duration_s = (window_end_ns - window_start_ns) * 1e-9
        bytes_by_stack = {stack_id: 0.0 for stack_id in stacks}
        die_bytes_by_stack = {
            stack_id: {component_id: 0.0 for component_id in stack["array_dies"]}
            for stack_id, stack in stacks.items()
        }
        channel_bytes_by_stack = {
            stack_id: {channel_id: 0.0 for channel_id in config["map"]}
            for stack_id, config in channels.items()
        }
        uniform_positive = {stack_id: False for stack_id in stacks}
        while segment_index < len(segments) and segments[segment_index][1] <= window_start_ns:
            segment_index += 1
        scan_index = segment_index
        while scan_index < len(segments):
            segment_start_ns, segment_end_ns, rates, segment_channel_rates = segments[scan_index]
            if segment_start_ns >= window_end_ns:
                break
            overlap_ns = min(window_end_ns, segment_end_ns) - max(window_start_ns, segment_start_ns)
            if overlap_ns > 0:
                for stack_id, rate in rates.items():
                    # Divide the exact integer duration before multiplying by a
                    # potentially large B/s value.  This avoids the avoidable
                    # rounding introduced by a ~1e19 intermediate product.
                    overlap_s = overlap_ns / 1_000_000_000
                    interval_bytes = rate * overlap_s
                    bytes_by_stack[stack_id] += interval_bytes
                    explicit_channels = segment_channel_rates[stack_id]
                    if explicit_channels is None:
                        if rate > 0.0:
                            uniform_positive[stack_id] = True
                        for component_id, weight in weights[stack_id].items():
                            die_bytes_by_stack[stack_id][component_id] += interval_bytes * weight
                    else:
                        for channel_id, channel_rate in explicit_channels.items():
                            channel_bytes = channel_rate * overlap_s
                            channel_bytes_by_stack[stack_id][channel_id] += channel_bytes
                            component_id = channels[stack_id]["map"][channel_id]
                            die_bytes_by_stack[stack_id][component_id] += channel_bytes
            scan_index += 1

        component_energy_j = {component_id: 0.0 for component_id in component_ids}
        stack_receipts = {}
        for stack_id, stack in stacks.items():
            byte_count = bytes_by_stack[stack_id]
            array_energy_j = byte_count * array_j_per_byte
            base_energy_j = byte_count * base_j_per_byte
            component_energy_j[stack["base"]] = base_energy_j
            for component_id, die_bytes in die_bytes_by_stack[stack_id].items():
                component_energy_j[component_id] = die_bytes * array_j_per_byte
            assigned_array_energy_j = sum(
                component_energy_j[component_id] for component_id in stack["array_dies"]
            )
            if not math.isclose(
                assigned_array_energy_j, array_energy_j, rel_tol=1e-12, abs_tol=1e-15
            ):
                raise AssertionError(f"array energy assignment does not conserve {stack_id!r}")
            total_energy_j = array_energy_j + base_energy_j
            if uniform_positive[stack_id]:
                activity_mode = "UNIFORM_ASSUMED_CHANNEL_ACTIVITY_UNKNOWN"
                active_channel_count = None
                channel_requested_bytes = None
            elif stack_id in channel_bytes_by_stack:
                activity_mode = "EXPLICIT_PRESCRIBED_CHANNEL_RATES"
                active_channel_count = sum(
                    value > 0.0 for value in channel_bytes_by_stack[stack_id].values()
                )
                channel_requested_bytes = channel_bytes_by_stack[stack_id]
            else:
                activity_mode = "UNIFORM_ASSUMED_CHANNEL_ACTIVITY_UNKNOWN"
                active_channel_count = None
                channel_requested_bytes = None
            stack_receipts[stack_id] = {
                "requested_bytes": byte_count,
                "modelled_bytes": byte_count,
                "channel_activity": activity_mode,
                "active_channel_count": active_channel_count,
                "channel_requested_bytes": channel_requested_bytes,
                "source_energy_j": {
                    "array": array_energy_j,
                    "base": base_energy_j,
                    "total": total_energy_j,
                },
                "source_power_w": {
                    "array": array_energy_j / duration_s,
                    "base": base_energy_j / duration_s,
                    "total": total_energy_j / duration_s,
                },
            }
        windows.append(
            {
                "start_ns": window_start_ns,
                "end_ns": window_end_ns,
                "component_energy_j": component_energy_j,
                "stacks": stack_receipts,
            }
        )

    entity_mapping = {}
    for stack_id, stack in stacks.items():
        entity_mapping[stack_id] = {
            "base": stack["base"],
            "array_dies": list(stack["array_dies"]),
            "die_weights": weights[stack_id],
            "weight_mode": "EXPLICIT" if stack_id in profile.get("die_weights", {}) else "UNIFORM",
            "channel_map": channels.get(stack_id, {}).get("map"),
            "channel_capacity_Bps": channels.get(stack_id, {}).get("capacities"),
            "channel_fallback": "UNIFORM_ASSUMED_CHANNEL_ACTIVITY_UNKNOWN",
        }
    return {
        "schema_version": "eq3-rate-thermal-windows-v1",
        "metadata": {
            "mode": "DEFAULT_OFF_PURE_RATE_TO_INCREMENTAL_ENERGY",
            "provenance": PROVENANCE,
            "step_ns": step_ns,
            "end_ns": end_ns,
            "interval_semantics": "INTEGER_NS_HALF_OPEN",
            "rate_semantics": "PRESCRIBED_READ_RATE_NOT_BACKEND_THROUGHPUT",
            "idle_semantics": "ZERO_READ_ONLY_NOT_ZERO_IDLE",
            "energy_semantics": "INCREMENTAL_READ_ENERGY_ONLY",
            "entity_mapping": entity_mapping,
        },
        "parameters": {
            "reference_read_Bps": reference_read_bps,
            "array_j_per_byte": array_j_per_byte,
            "base_j_per_byte": base_j_per_byte,
            "reference_power_w": {
                "array": reference_read_bps * array_j_per_byte,
                "base": reference_read_bps * base_j_per_byte,
                "total": reference_read_bps * (array_j_per_byte + base_j_per_byte),
            },
        },
        "windows": windows,
    }


__all__ = ["build_windows"]
