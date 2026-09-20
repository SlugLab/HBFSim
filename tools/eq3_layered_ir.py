"""Normalize layered EQ3 package inputs into a solver-neutral SI IR.

This module performs validation and data conversion only.  It does not invoke a
thermal solver and deliberately has no dependency on the P1 fixture generator.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from copy import deepcopy
import math
from typing import Any


IR_SCHEMA_VERSION = "eq3-layered-ir-v1"
SUPPORTED_STAGES = {"thermal_only", "system_behavior"}
_SYSTEM_UNKNOWN = ("UNKNOWN", "NOT_IMPLEMENTED", "UNSPECIFIED")


class ValidationError(ValueError):
    """An input cannot be normalized without an unsafe assumption."""

    def __init__(
        self,
        path: str,
        reason: str,
        *,
        gaps: Sequence[Mapping[str, Any]] | None = None,
    ) -> None:
        self.path = path
        self.reason = reason
        self.gaps = [dict(gap) for gap in (gaps or [])]
        super().__init__(f"{path}: {reason}")


def _fail(path: str, reason: str) -> None:
    raise ValidationError(path, reason)


def _mapping(value: Any, path: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        _fail(path, "must be an object")
    return value


def _list(value: Any, path: str, length: int | None = None) -> list[Any]:
    if not isinstance(value, list):
        _fail(path, "must be a list")
    if length is not None and len(value) != length:
        _fail(path, f"must contain exactly {length} values")
    return value


def _number(value: Any, path: str, *, positive: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        _fail(path, "must be a finite number")
    result = float(value)
    if not math.isfinite(result):
        _fail(path, "must be a finite number")
    if positive and result <= 0:
        _fail(path, "must be greater than zero")
    return result


def _identifier(value: Any, path: str) -> str:
    if not isinstance(value, str) or not value.strip():
        _fail(path, "must be a non-empty stable identifier")
    return value


def _unique(items: Sequence[Mapping[str, Any]], key: str, path: str) -> None:
    seen: set[str] = set()
    for index, item in enumerate(items):
        identity = _identifier(item.get(key), f"{path}[{index}].{key}")
        if identity in seen:
            _fail(f"{path}[{index}].{key}", f"duplicate identifier {identity!r}")
        seen.add(identity)


def _role(block: Mapping[str, Any], parent: str) -> str:
    explicit = block.get("component_role") or block.get("role")
    if explicit is not None:
        return _identifier(explicit, f"blocks[{block.get('id', '?')}].component_role")
    identity = str(block["id"])
    suffix = identity.rsplit(".", 1)[-1]
    if parent == "package":
        return "package_layer"
    if suffix == "base":
        return "base_die"
    if suffix.startswith("die"):
        return "array_die"
    if suffix.startswith("bond") or suffix in {"attach", "tim"}:
        return "interface_layer"
    if parent == "gpu" and identity == "gpu":
        return "compute_die"
    return "structural_layer"


def _parent_id(block: Mapping[str, Any], placement_ids: set[str]) -> str:
    explicit = block.get("parent_device_id")
    if explicit is not None:
        parent = _identifier(explicit, f"blocks[{block.get('id', '?')}].parent_device_id")
    elif block.get("device") == "package":
        parent = "package"
    else:
        identity = str(block.get("id", ""))
        parent = identity.split(".", 1)[0]
    if parent != "package" and parent not in placement_ids:
        _fail(
            f"blocks[{block.get('id', '?')}].parent_device_id",
            f"unknown parent device {parent!r}",
        )
    return parent


def _normalize_materials(profile: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    raw = _mapping(profile.get("materials"), "materials")
    if not raw:
        _fail("materials", "must not be empty")
    result: dict[str, dict[str, Any]] = {}
    for name in sorted(raw):
        material = _mapping(raw[name], f"materials.{name}")
        k = _list(material.get("k_w_m_k"), f"materials.{name}.k_w_m_k", 3)
        k_xyz = [
            _number(value, f"materials.{name}.k_w_m_k[{axis}]", positive=True)
            for axis, value in enumerate(k)
        ]
        rho = _number(material.get("rho_kg_m3"), f"materials.{name}.rho_kg_m3", positive=True)
        cp = _number(material.get("cp_j_kg_k"), f"materials.{name}.cp_j_kg_k", positive=True)
        result[name] = {
            "k_xyz_w_m_k": k_xyz,
            "cv_j_m3_k": rho * cp,
            "source_id": material.get("source_id"),
            "evidence_kind": material.get("evidence_kind"),
        }
    return result


def _normalize_geometry(
    profile: Mapping[str, Any], materials: Mapping[str, Any]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[float], dict[str, Any]]:
    units = _mapping(profile.get("units"), "units")
    geometry_unit = units.get("geometry")
    unit_specs = {
        "um": ("um", 1e-6),
        "micrometer": ("um", 1e-6),
        "micrometre": ("um", 1e-6),
        "mm": ("mm", 1e-3),
        "millimeter": ("mm", 1e-3),
        "millimetre": ("mm", 1e-3),
        "m": ("m", 1.0),
        "meter": ("m", 1.0),
        "metre": ("m", 1.0),
    }
    if geometry_unit not in unit_specs:
        _fail("units.geometry", "must explicitly select um, mm, or m")
    suffix, length_scale = unit_specs[str(geometry_unit)]
    package_key = f"package_size_{suffix}"
    package_input = _list(profile.get(package_key), package_key, 3)
    package_size_m = [
        _number(value, f"{package_key}[{axis}]", positive=True) * length_scale
        for axis, value in enumerate(package_input)
    ]

    placements = _list(profile.get("placements"), "placements")
    for index, placement in enumerate(placements):
        _mapping(placement, f"placements[{index}]")
    _unique(placements, "id", "placements")
    placement_ids = {str(item["id"]) for item in placements}

    devices: list[dict[str, Any]] = []
    physical_type_by_id: dict[str, str] = {}
    for index, placement in enumerate(placements):
        identity = str(placement["id"])
        physical_type = _identifier(placement.get("device"), f"placements[{index}].device")
        footprint_key = f"footprint_{suffix}"
        xy_key = f"xy_{suffix}"
        footprint = _list(placement.get(footprint_key), f"placements[{index}].{footprint_key}", 2)
        xy = _list(placement.get(xy_key), f"placements[{index}].{xy_key}", 2)
        footprint_m = [
            _number(v, f"placements[{index}].{footprint_key}[{j}]", positive=True) * length_scale
            for j, v in enumerate(footprint)
        ]
        xy_m = [
            _number(v, f"placements[{index}].{xy_key}[{j}]") * length_scale
            for j, v in enumerate(xy)
        ]
        if any(v < 0 for v in xy_m) or any(
            xy_m[j] + footprint_m[j] > package_size_m[j] + 1e-15 for j in range(2)
        ):
            _fail(f"placements[{index}]", "footprint lies outside the package")
        count = placement.get("array_die_count")
        if isinstance(count, bool) or not isinstance(count, int) or count < 0:
            _fail(f"placements[{index}].array_die_count", "must be a non-negative integer")
        devices.append(
            {
                "id": identity,
                "physical_type": physical_type,
                "xy_m": xy_m,
                "footprint_m": footprint_m,
                "array_die_count": count,
                "external": False,
            }
        )
        physical_type_by_id[identity] = physical_type

    raw_blocks = _list(profile.get("blocks"), "blocks")
    for index, block in enumerate(raw_blocks):
        _mapping(block, f"blocks[{index}]")
    _unique(raw_blocks, "id", "blocks")
    components: list[dict[str, Any]] = []
    for index, block in enumerate(raw_blocks):
        identity = str(block["id"])
        xyz_key = f"xyz_{suffix}"
        size_key = f"size_{suffix}"
        xyz_input = _list(block.get(xyz_key), f"blocks[{index}].{xyz_key}", 3)
        size_input = _list(block.get(size_key), f"blocks[{index}].{size_key}", 3)
        xyz_m = [_number(v, f"blocks[{index}].{xyz_key}[{j}]") * length_scale for j, v in enumerate(xyz_input)]
        size_m = [
            _number(v, f"blocks[{index}].{size_key}[{j}]", positive=True) * length_scale
            for j, v in enumerate(size_input)
        ]
        if any(v < 0 for v in xyz_m) or any(
            xyz_m[j] + size_m[j] > package_size_m[j] + 1e-15 for j in range(3)
        ):
            _fail(f"blocks[{index}]", "component lies outside the package")
        material = _identifier(block.get("material"), f"blocks[{index}].material")
        if material not in materials:
            _fail(f"blocks[{index}].material", f"unknown material {material!r}")
        parent = _parent_id(block, placement_ids)
        role = _role(block, parent)
        if "powered" in block and not isinstance(block["powered"], bool):
            _fail(f"blocks[{index}].powered", "must be a JSON boolean")
        powered = block.get("powered", block.get("power_group") is not None)
        die_index = block.get("die_index")
        if isinstance(die_index, bool) or not isinstance(die_index, int):
            _fail(f"blocks[{index}].die_index", "must be an integer")
        volume = math.prod(size_m)
        components.append(
            {
                "id": identity,
                "device_id": parent,
                "device": str(block.get("device")),
                "physical_type": "package" if parent == "package" else physical_type_by_id[parent],
                "role": role,
                "xyz_m": xyz_m,
                "size_m": size_m,
                "volume_m3": volume,
                "material": material,
                "powered": powered,
                "power_group": block.get("power_group"),
                "die_index": die_index,
                "input_sensor": block.get("sensor"),
                "contact_model": block.get("contact_model"),
                "geometry_evidence": block.get("geometry_evidence"),
                "source_id": block.get("source_id"),
            }
        )

    # Strict solid overlap.  Touching faces are allowed; background is a fill
    # rule and is therefore validated separately rather than treated as a solid.
    ordered = sorted(components, key=lambda item: item["id"])
    for left_index, left in enumerate(ordered):
        for right in ordered[left_index + 1 :]:
            overlap = all(
                left["xyz_m"][axis] < right["xyz_m"][axis] + right["size_m"][axis] - 1e-15
                and right["xyz_m"][axis] < left["xyz_m"][axis] + left["size_m"][axis] - 1e-15
                for axis in range(3)
            )
            if overlap:
                _fail("blocks", f"components {left['id']!r} and {right['id']!r} overlap")

    background = _mapping(profile.get("background"), "background")
    z_key = f"z_interval_{suffix}"
    xy_extent_key = f"xy_extent_{suffix}"
    z_input = _list(background.get(z_key), f"background.{z_key}", 2)
    xy_input = _list(background.get(xy_extent_key), f"background.{xy_extent_key}", 4)
    z = [_number(v, f"background.{z_key}[{i}]") * length_scale for i, v in enumerate(z_input)]
    xy = [_number(v, f"background.{xy_extent_key}[{i}]") * length_scale for i, v in enumerate(xy_input)]
    if not (0 <= z[0] < z[1] <= package_size_m[2] + 1e-15):
        _fail(f"background.{z_key}", "must be an ordered interval inside the package")
    if not (0 <= xy[0] < xy[2] <= package_size_m[0] + 1e-15 and 0 <= xy[1] < xy[3] <= package_size_m[1] + 1e-15):
        _fail(f"background.{xy_extent_key}", "must be an ordered rectangle inside the package")
    background_material = _identifier(background.get("material"), "background.material")
    if background_material not in materials:
        _fail("background.material", f"unknown material {background_material!r}")
    rule = _identifier(background.get("rule"), "background.rule")
    if "complement" not in rule.lower() or "no overlap" not in rule.lower():
        _fail("background.rule", "must explicitly define non-overlapping complement fill")
    normalized_background = {
        "z_range_m": z,
        "xy_extent_m": xy,
        "material": background_material,
        "rule": rule,
    }
    return devices, ordered, package_size_m, normalized_background


def _device_kind(device: Mapping[str, Any]) -> str:
    value = str(device["physical_type"]).upper()
    if value.startswith("HBM"):
        return "HBM"
    if value.startswith("HBF"):
        return "HBF"
    if value == "GPU":
        return "GPU"
    return value


def _normalize_topology(
    profile: Mapping[str, Any], devices: list[dict[str, Any]], components: list[dict[str, Any]]
) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any]]:
    raw = profile.get("topology", profile.get("data_topology", {}))
    topology = _mapping(raw, "data_topology")
    identity = str(
        profile.get("topology_id")
        or topology.get("id")
        or topology.get("name")
        or topology.get("kind")
        or ""
    )
    aliases = {
        "all_hbf_direct": "all_hbf_direct",
        "all_hbf_direct_8": "all_hbf_direct",
        "mixed_direct": "mixed_direct",
        "mixed_direct_8": "mixed_direct",
        "daisy": "relay",
        "daisy_4hbm_4hbf": "relay",
        "relay": "relay",
        "dash": "dash",
        "dual": "dash",
        "dash_4pairs_8stacks": "dash",
    }
    physical = [item for item in devices if _device_kind(item) in {"HBM", "HBF"}]
    hbm_ids = sorted(item["id"] for item in physical if _device_kind(item) == "HBM")
    hbf_ids = sorted(item["id"] for item in physical if _device_kind(item) == "HBF")
    if identity:
        mode = aliases.get(identity.lower())
        if mode is None:
            _fail("data_topology.kind", f"unknown topology {identity!r}; no fallback is permitted")
    else:
        layout = str(topology.get("layout", "")).lower()
        if layout == "direct":
            mode = "all_hbf_direct" if not hbm_ids else "mixed_direct"
        else:
            mode = aliases.get(layout)
        if mode is None:
            _fail("data_topology.layout", f"unknown topology layout {layout!r}; no fallback is permitted")
        identity = layout
    if len(physical) != 8:
        _fail("placements", "required EQ3 topology must contain exactly eight memory stacks")
    if mode == "all_hbf_direct" and (hbm_ids or len(hbf_ids) != 8):
        _fail("data_topology", "all-HBF direct requires zero HBM and eight HBF stacks")
    if mode == "mixed_direct" and (not hbm_ids or not hbf_ids):
        _fail("data_topology", "mixed-direct requires at least one HBM and one HBF stack")

    pairs_raw = topology.get("pairs", topology.get("stack_pairs"))
    pairs: list[list[str]] = []
    if mode in {"relay", "dash"}:
        if not isinstance(pairs_raw, list) or not pairs_raw:
            _fail("data_topology.pairs", f"{mode} requires explicit HBM/HBF pair identities")
        known = set(hbm_ids + hbf_ids)
        used: set[str] = set()
        for index, pair in enumerate(pairs_raw):
            pair_values = _list(pair, f"data_topology.pairs[{index}]", 2)
            a = _identifier(pair_values[0], f"data_topology.pairs[{index}][0]")
            b = _identifier(pair_values[1], f"data_topology.pairs[{index}][1]")
            if a not in known or b not in known:
                _fail(f"data_topology.pairs[{index}]", "contains an unknown stack")
            kinds = {_device_kind(next(d for d in physical if d["id"] == item)) for item in (a, b)}
            if kinds != {"HBM", "HBF"}:
                _fail(f"data_topology.pairs[{index}]", "must pair one HBM with one HBF")
            if a in used or b in used:
                _fail(f"data_topology.pairs[{index}]", "stack appears in more than one pair")
            used.update((a, b))
            pairs.append([a, b])
        if used != known or len(hbm_ids) != len(hbf_ids):
            _fail("data_topology.pairs", "pairs must cover every HBM/HBF stack exactly once")
        if mode == "relay" and topology.get("custom_base_die_relay") is not True:
            _fail("data_topology.custom_base_die_relay", "relay topology must explicitly enable custom base relay")

    component_by_device: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for component in components:
        component_by_device[component["device_id"]].append(component)
    for device in physical:
        members = component_by_device[device["id"]]
        bases = [item for item in members if item["role"] == "base_die"]
        arrays = [item for item in members if item["role"] == "array_die"]
        if len(bases) != 1:
            _fail(f"devices.{device['id']}", "HBM/HBF stack must contain exactly one base_die")
        if len(arrays) != device["array_die_count"]:
            _fail(
                f"devices.{device['id']}.array_die_count",
                f"declares {device['array_die_count']} but geometry contains {len(arrays)} array dies",
            )

    external_devices: list[dict[str, Any]] = []
    if mode == "all_hbf_direct":
        external_identity = (
            topology.get("external_fast_memory_profile")
            or profile.get("external_fast_memory_profile")
            or _mapping(profile.get("device_selection", {}), "device_selection").get("gddr")
        )
        if not external_identity:
            _fail(
                "data_topology.external_fast_memory_profile",
                "all-HBF direct must retain an external physical GDDR identity",
            )
        external_devices.append(
            {
                "id": "external_fast_memory",
                "physical_type": "GDDR",
                "profile": deepcopy(external_identity),
                "external": True,
                "package_geometry_modeled": False,
            }
        )

    data_graph = deepcopy(dict(topology))
    links = topology.get("gpu_links")
    if links is not None:
        links = _list(links, "data_topology.gpu_links")
        allowed_endpoints = {
            str(device["id"])
            for device in devices
            if _device_kind(device) in {"GPU", "HBM", "HBF"}
        }
        allowed_endpoints.update(str(device["id"]) for device in external_devices)
        normalized_edges: set[tuple[str, str]] = set()
        for index, raw_link in enumerate(links):
            link = _list(raw_link, f"data_topology.gpu_links[{index}]", 2)
            left = _identifier(link[0], f"data_topology.gpu_links[{index}][0]")
            right = _identifier(link[1], f"data_topology.gpu_links[{index}][1]")
            if left == right:
                _fail(f"data_topology.gpu_links[{index}]", "data edge endpoints must be distinct")
            unknown = sorted({left, right} - allowed_endpoints)
            if unknown:
                _fail(
                    f"data_topology.gpu_links[{index}]",
                    f"data edge references undeclared endpoint(s) {unknown}",
                )
            canonical = tuple(sorted((left, right)))
            if canonical in normalized_edges:
                _fail(f"data_topology.gpu_links[{index}]", "duplicate data edge")
            normalized_edges.add(canonical)

    normalized = {
        "id": identity,
        "mode": mode,
        "stack_count": len(physical),
        "hbm_count": len(hbm_ids),
        "hbf_count": len(hbf_ids),
        "pairs": pairs,
        "external_devices": external_devices,
    }
    declared_counts = {
        "stack_count": len(physical),
        "hbm_count": len(hbm_ids),
        "hbf_count": len(hbf_ids),
        "pair_count": len(pairs),
    }
    for field, actual in declared_counts.items():
        if field in topology:
            declared = topology[field]
            if isinstance(declared, bool) or not isinstance(declared, int) or declared != actual:
                _fail(f"data_topology.{field}", f"declares {declared!r}, but geometry implies {actual}")
    selection = profile.get("device_selection")
    if isinstance(selection, Mapping):
        for selection_key, actual in (("hbm", len(hbm_ids)), ("hbf", len(hbf_ids))):
            selected = selection.get(selection_key)
            if isinstance(selected, Mapping) and "count" in selected:
                declared = selected["count"]
                if isinstance(declared, bool) or not isinstance(declared, int) or declared != actual:
                    _fail(
                        f"device_selection.{selection_key}.count",
                        f"declares {declared!r}, but placements contain {actual}",
                    )
    return normalized, external_devices, data_graph


def _power_groups(
    power: Mapping[str, Any], components: Sequence[Mapping[str, Any]]
) -> tuple[str, dict[str, dict[str, float]], bool]:
    component_ids = {str(item["id"]) for item in components}
    powered = {str(item["id"]) for item in components if item["powered"]}
    mode = power.get("mode")
    groups_raw = power.get("groups", power.get("group_weights"))
    legacy = False
    if "allow_additive" in power and not isinstance(power["allow_additive"], bool):
        _fail("power.allow_additive", "must be a JSON boolean")
    allow_additive = power.get("allow_additive", False)
    if mode is None and groups_raw is None and "group_order" in power:
        distribution = str(power.get("distribution", "")).lower()
        if "equal" not in distribution:
            _fail("power.distribution", "legacy group input must explicitly state equal distribution")
        mode = "explicit_group_weights"
        legacy = True
        generated: dict[str, dict[str, float]] = {}
        group_order = _list(power.get("group_order"), "power.group_order")
        normalized_order = [_identifier(group, f"power.group_order[{index}]") for index, group in enumerate(group_order)]
        if len(set(normalized_order)) != len(normalized_order):
            _fail("power.group_order", "source identities must be unique")
        for group in normalized_order:
            members = sorted(
                str(item["id"]) for item in components if item.get("power_group") == group
            )
            if not members:
                _fail("power.group_order", f"group {group!r} has no powered components")
            generated[str(group)] = {member: 1.0 / len(members) for member in members}
        groups_raw = generated
    if mode is None:
        mode = "per_component"
    if mode not in {"per_component", "explicit_group_weights"}:
        _fail("power.mode", "must be per_component or explicit_group_weights")
    if mode == "per_component":
        return mode, {item: {item: 1.0} for item in sorted(powered)}, legacy

    groups = _mapping(groups_raw, "power.groups")
    result: dict[str, dict[str, float]] = {}
    covered: set[str] = set()
    for group_name in sorted(groups):
        group = groups[group_name]
        if isinstance(group, Mapping) and "members" in group:
            members_raw = group["members"]
            if isinstance(members_raw, Mapping):
                weights = dict(members_raw)
            else:
                weights = {}
                seen_members: set[str] = set()
                for index, member in enumerate(_list(members_raw, f"power.groups.{group_name}.members")):
                    entry = _mapping(member, f"power.groups.{group_name}.members[{index}]")
                    member_id = entry.get("component_id", entry.get("id"))
                    normalized_member = _identifier(
                        member_id, f"power.groups.{group_name}.members[{index}].component_id"
                    )
                    if normalized_member in seen_members:
                        _fail(
                            f"power.groups.{group_name}.members[{index}].component_id",
                            f"duplicate member {normalized_member!r}",
                        )
                    seen_members.add(normalized_member)
                    weights[normalized_member] = entry.get("weight")
        else:
            weights = dict(_mapping(group, f"power.groups.{group_name}"))
        if not weights:
            _fail(f"power.groups.{group_name}", "must have explicit members and weights")
        normalized_weights: dict[str, float] = {}
        for member, weight in weights.items():
            member_id = _identifier(member, f"power.groups.{group_name}.members")
            if member_id not in component_ids:
                _fail(f"power.groups.{group_name}", f"unknown component {member_id!r}")
            value = _number(weight, f"power.groups.{group_name}.{member_id}")
            if value < 0:
                _fail(f"power.groups.{group_name}.{member_id}", "weight must be non-negative")
            normalized_weights[member_id] = value
        if not math.isclose(sum(normalized_weights.values()), 1.0, rel_tol=0, abs_tol=1e-9):
            _fail(f"power.groups.{group_name}", "weights must sum to one")
        overlap = covered.intersection(normalized_weights)
        if overlap and not allow_additive:
            _fail(
                f"power.groups.{group_name}",
                f"duplicate source assignment for {sorted(overlap)} requires allow_additive=true",
            )
        covered.update(normalized_weights)
        result[str(group_name)] = normalized_weights
    if covered != powered:
        missing = sorted(powered - covered)
        extra = sorted(covered - powered)
        _fail("power.groups", f"powered-component coverage mismatch; missing={missing}, nonpowered={extra}")
    return str(mode), result, legacy


def _select_trace(power: Mapping[str, Any], trace: str | Mapping[str, Any]) -> tuple[str, Mapping[str, Any]]:
    if isinstance(trace, str):
        traces = _mapping(power.get("traces"), "power.traces")
        if trace not in traces:
            _fail("trace", f"unknown trace {trace!r}")
        return trace, _mapping(traces[trace], f"power.traces.{trace}")
    return str(trace.get("id", "inline")), _mapping(trace, "trace")


def _trace_rows(
    power: Mapping[str, Any], trace_data: Mapping[str, Any], sources: set[str]
) -> list[tuple[float, float, Mapping[str, Any]]]:
    if "intervals" in trace_data:
        rows: list[tuple[float, float, Mapping[str, Any]]] = []
        previous_end = 0.0
        for index, raw in enumerate(_list(trace_data["intervals"], "trace.intervals")):
            interval = _mapping(raw, f"trace.intervals[{index}]")
            start = _number(interval.get("start_s"), f"trace.intervals[{index}].start_s")
            end = _number(interval.get("end_s"), f"trace.intervals[{index}].end_s")
            if start < 0 or end <= start:
                _fail(f"trace.intervals[{index}]", "must have 0 <= start_s < end_s")
            if start < previous_end - 1e-12:
                _fail(
                    f"trace.intervals[{index}]",
                    "time intervals must not overlap",
                )
            if start > previous_end + 1e-12:
                _fail(f"trace.intervals[{index}]", "time intervals contain an implicit-zero gap")
            previous_end = end
            values = _mapping(interval.get("power_w", interval.get("values_w")), f"trace.intervals[{index}].power_w")
            rows.append((start, end, values))
        if not rows:
            _fail("trace.intervals", "must not be empty")
        duration = trace_data.get("duration_s")
        if duration is not None and not math.isclose(
            _number(duration, "trace.duration_s", positive=True), previous_end, rel_tol=0, abs_tol=1e-9
        ):
            _fail("trace.duration_s", "does not equal the end of the final interval")
        return rows

    slot = _number(trace_data.get("slot_s", power.get("slot_s")), "power.slot_s", positive=True)
    slots = _list(trace_data.get("slots_W"), "trace.slots_W")
    if not slots:
        _fail("trace.slots_W", "must not be empty")
    order = trace_data.get("source_order", power.get("group_order"))
    rows = []
    for index, raw in enumerate(slots):
        if isinstance(raw, Mapping):
            values = raw
        else:
            if order is None:
                _fail("power.group_order", "vector slots require an explicit source order")
            order_list = _list(order, "power.group_order")
            normalized_order = [
                _identifier(source, f"power.group_order[{source_index}]")
                for source_index, source in enumerate(order_list)
            ]
            if len(set(normalized_order)) != len(normalized_order):
                _fail("power.group_order", "source identities must be unique")
            values_list = _list(raw, f"trace.slots_W[{index}]", len(normalized_order))
            values = dict(zip(normalized_order, values_list, strict=True))
        rows.append((index * slot, (index + 1) * slot, values))
    duration = trace_data.get("duration_s")
    if duration is not None and not math.isclose(
        _number(duration, "trace.duration_s", positive=True), len(slots) * slot, rel_tol=0, abs_tol=1e-9
    ):
        _fail("trace.duration_s", "does not equal slot count times slot_s")
    return rows


def _normalize_power(
    power: Mapping[str, Any], trace: str | Mapping[str, Any], components: Sequence[Mapping[str, Any]]
) -> dict[str, Any]:
    if power.get("power_unit", "W") not in {"W", "watt", "watts"}:
        _fail("power.power_unit", "only watts are supported")
    mode, groups, legacy = _power_groups(power, components)
    trace_id, trace_data = _select_trace(power, trace)
    rows = _trace_rows(power, trace_data, set(groups))
    caps_raw = power.get("caps_W")
    caps: dict[str, float] = {}
    if caps_raw is not None:
        if isinstance(caps_raw, Mapping):
            caps = {str(k): _number(v, f"power.caps_W.{k}") for k, v in caps_raw.items()}
        else:
            order = _list(power.get("group_order"), "power.group_order")
            cap_values = _list(caps_raw, "power.caps_W", len(order))
            caps = {str(k): _number(v, f"power.caps_W[{i}]") for i, (k, v) in enumerate(zip(order, cap_values, strict=True))}

    intervals: list[dict[str, Any]] = []
    component_energy: dict[str, float] = defaultdict(float)
    expected = set(groups)
    for index, (start, end, raw_values) in enumerate(rows):
        supplied = set(map(str, raw_values))
        if supplied != expected:
            _fail(
                f"trace.intervals[{index}].power_w",
                f"must explicitly specify every source (including zero); missing={sorted(expected-supplied)}, extra={sorted(supplied-expected)}",
            )
        component_power: dict[str, float] = defaultdict(float)
        for source, weights in groups.items():
            value = _number(raw_values[source], f"trace.intervals[{index}].power_w.{source}")
            if value < 0:
                _fail(f"trace.intervals[{index}].power_w.{source}", "power must be non-negative")
            if source in caps and value > caps[source] + 1e-12:
                _fail(f"trace.intervals[{index}].power_w.{source}", "power exceeds declared cap")
            for component_id, weight in weights.items():
                component_power[component_id] += value * weight
        duration = end - start
        normalized_power = {key: component_power[key] for key in sorted(component_power)}
        for component_id, value in normalized_power.items():
            component_energy[component_id] += value * duration
        intervals.append({"start_s": start, "end_s": end, "power_w": normalized_power})

    total_from_intervals = sum(component_energy.values())
    source_energy = 0.0
    for start, end, raw_values in rows:
        source_energy += (end - start) * sum(float(value) for value in raw_values.values())
    if not math.isclose(total_from_intervals, source_energy, rel_tol=1e-12, abs_tol=1e-12):
        _fail("power", "source-to-component energy conservation failed")
    return {
        "mode": mode,
        "legacy_explicit_equal_weights": legacy,
        "trace_id": trace_id,
        "intervals": intervals,
        "component_energy_j": {key: component_energy[key] for key in sorted(component_energy)},
        "total_energy_j": total_from_intervals,
    }


def _normalize_boundaries(profile: Mapping[str, Any]) -> dict[str, Any]:
    boundaries = deepcopy(dict(_mapping(profile.get("boundaries"), "boundaries")))
    _number(boundaries.get("initial_temperature_k"), "boundaries.initial_temperature_k", positive=True)
    for face in ("top", "bottom"):
        value = _mapping(boundaries.get(face), f"boundaries.{face}")
        _number(value.get("ambient_k"), f"boundaries.{face}.ambient_k", positive=True)
        _number(value.get("h_w_m2_k"), f"boundaries.{face}.h_w_m2_k")
        if float(value["h_w_m2_k"]) < 0:
            _fail(f"boundaries.{face}.h_w_m2_k", "must be non-negative")
    resistance = _number(
        boundaries.get("additional_area_contact_resistance_m2_k_W"),
        "boundaries.additional_area_contact_resistance_m2_k_W",
    )
    if resistance < 0:
        _fail("boundaries.additional_area_contact_resistance_m2_k_W", "must be non-negative")
    _identifier(boundaries.get("sides"), "boundaries.sides")
    return boundaries


def _temperature_domain(profile: Mapping[str, Any]) -> list[float]:
    model_domain = _mapping(profile.get("model_domain"), "model_domain")
    values = _list(model_domain.get("temperature_k"), "model_domain.temperature_k", 2)
    low = _number(values[0], "model_domain.temperature_k[0]", positive=True)
    high = _number(values[1], "model_domain.temperature_k[1]", positive=True)
    if low >= high:
        _fail("model_domain.temperature_k", "must satisfy finite low < high")
    return [low, high]


def _sensors(components: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    powered = [item for item in components if item["powered"]]
    observed = [
        item
        for item in components
        if item["powered"] or item["role"] in {"base_die", "array_die", "compute_die"}
    ]
    for component in sorted(observed, key=lambda item: item["id"]):
        result.append(
            {
                "id": f"component:{component['id']}:mean",
                "reduction": "weighted_mean",
                "weights": [{"component_id": component["id"], "weight": 1.0}],
            }
        )
        result.append(
            {
                "id": f"component:{component['id']}:hotspot",
                "reduction": "max",
                "components": [component["id"]],
            }
        )
    by_device: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for component in observed:
        if component["role"] in {"array_die", "base_die"}:
            by_device[str(component["device_id"])].append(component)
    for device_id in sorted(by_device):
        stack_members = sorted(by_device[device_id], key=lambda item: item["id"])
        members = [item for item in stack_members if item["role"] == "array_die"]
        total_volume = sum(float(item["volume_m3"]) for item in members)
        result.append(
            {
                "id": f"stack:{device_id}:array_mean",
                "reduction": "weighted_mean",
                "weights": [
                    {"component_id": item["id"], "weight": item["volume_m3"] / total_volume}
                    for item in members
                ],
            }
        )
        result.append(
            {
                "id": f"stack:{device_id}:array_hotspot",
                "reduction": "max",
                "components": [item["id"] for item in members],
            }
        )
        stack_volume = sum(float(item["volume_m3"]) for item in stack_members)
        result.append(
            {
                "id": f"stack:{device_id}:mean",
                "reduction": "weighted_mean",
                "weights": [
                    {"component_id": item["id"], "weight": item["volume_m3"] / stack_volume}
                    for item in stack_members
                ],
            }
        )
        result.append(
            {
                "id": f"stack:{device_id}:hotspot",
                "reduction": "max",
                "components": [item["id"] for item in stack_members],
            }
        )
    result.append(
        {
            "id": "package:powered_hotspot",
            "reduction": "max",
            "components": sorted(str(item["id"]) for item in powered),
        }
    )
    return sorted(result, key=lambda item: item["id"])


def _system_gaps(profile: Mapping[str, Any], topology: Mapping[str, Any]) -> list[dict[str, str]]:
    data = _mapping(profile.get("data_topology", profile.get("topology", {})), "data_topology")
    gaps: list[dict[str, str]] = []
    for key, value in sorted(data.items()):
        if isinstance(value, str) and any(marker in value.upper() for marker in _SYSTEM_UNKNOWN):
            gaps.append(
                {
                    "topology_id": str(topology["id"]),
                    "stage": "system_behavior",
                    "parameter_path": f"data_topology.{key}",
                    "reason": value,
                    "affected_claim": "system behavior/service conclusions",
                }
            )
    required = {
        "relay": ("path_definition", "shared_resources", "arbitration", "latency", "phy_relay_energy", "maintenance_rules"),
        "dash": ("path_definition", "shared_resources", "arbitration", "latency", "phy_relay_energy", "maintenance_rules"),
        "mixed_direct": ("path_definition", "shared_resources", "arbitration", "latency", "phy_energy", "maintenance_rules"),
        "all_hbf_direct": ("path_definition", "shared_resources", "arbitration", "latency", "phy_energy", "maintenance_rules"),
    }[str(topology["mode"])]
    behavior = data.get("system_behavior", profile.get("system_behavior"))
    behavior_map = behavior if isinstance(behavior, Mapping) else {}
    existing_paths = {gap["parameter_path"] for gap in gaps}
    if not data.get("gpu_links"):
        path = "data_topology.gpu_links"
        gaps.append(
            {
                "topology_id": str(topology["id"]),
                "stage": "system_behavior",
                "parameter_path": path,
                "reason": "data graph is absent; thermal-only normalization remains valid",
                "affected_claim": "routing and system behavior/service conclusions",
            }
        )
        existing_paths.add(path)
    for field in required:
        value = behavior_map.get(field)
        path = f"data_topology.system_behavior.{field}"
        if value is None or (isinstance(value, str) and any(marker in value.upper() for marker in _SYSTEM_UNKNOWN)):
            if path not in existing_paths:
                gaps.append(
                    {
                        "topology_id": str(topology["id"]),
                        "stage": "system_behavior",
                        "parameter_path": path,
                        "reason": "required system behavior parameter is not closed",
                        "affected_claim": "system behavior/service conclusions",
                    }
                )
    return sorted(gaps, key=lambda item: item["parameter_path"])


def normalize(
    profile: Mapping[str, Any],
    power: Mapping[str, Any],
    trace: str | Mapping[str, Any],
    stage: str = "thermal_only",
) -> dict[str, Any]:
    """Validate and convert a layered profile and power trace to canonical SI.

    ``thermal_only`` returns unresolved system gaps without claiming system
    readiness.  ``system_behavior`` raises :class:`ValidationError` carrying the
    same gaps until every required service parameter is explicitly closed.
    """

    if stage not in SUPPORTED_STAGES:
        _fail("stage", f"must be one of {sorted(SUPPORTED_STAGES)}")
    profile = _mapping(profile, "profile")
    power = _mapping(power, "power")
    materials = _normalize_materials(profile)
    devices, components, package_size_m, background = _normalize_geometry(profile, materials)
    topology, external_devices, data_graph = _normalize_topology(profile, devices, components)
    devices = sorted(devices + external_devices, key=lambda item: item["id"])
    normalized_power = _normalize_power(power, trace, components)
    boundaries = _normalize_boundaries(profile)
    if "initial_k" in power:
        power_initial_k = _number(power["initial_k"], "power.initial_k", positive=True)
        boundary_initial_k = float(boundaries["initial_temperature_k"])
        if not math.isclose(power_initial_k, boundary_initial_k, rel_tol=0, abs_tol=1e-12):
            _fail(
                "power.initial_k",
                "conflicts with authoritative profile boundaries.initial_temperature_k",
            )
        normalized_power["initial_temperature_k"] = power_initial_k
    temperature_domain_k = _temperature_domain(profile)
    gaps = _system_gaps(profile, topology)
    if stage == "system_behavior" and gaps:
        raise ValidationError(
            "system_behavior",
            "required system behavior inputs are incomplete",
            gaps=gaps,
        )
    return {
        "schema_version": IR_SCHEMA_VERSION,
        "profile_id": profile.get("profile_id"),
        "stage": stage,
        "topology": topology,
        "data_graph": data_graph,
        "package_size_m": package_size_m,
        "background": background,
        "materials": materials,
        "devices": devices,
        "components": components,
        "power": normalized_power,
        "boundaries": boundaries,
        "temperature_domain_k": temperature_domain_k,
        "sensors": _sensors(components),
        "gaps": gaps,
        "provenance": {
            "profile_schema_version": profile.get("schema_version"),
            "profile_status": profile.get("status"),
            "profile_evidence_kind": profile.get("evidence_kind"),
            "profile_approval_scope": profile.get("approval_scope"),
            "geometry_anchor": deepcopy(profile.get("geometry_anchor")),
            "power_schema_version": power.get("schema_version"),
            "power_status": power.get("status"),
            "power_evidence_kind": power.get("evidence_kind"),
            "power_claim_scope": power.get("claim_scope"),
        },
    }


__all__ = ["IR_SCHEMA_VERSION", "SUPPORTED_STAGES", "ValidationError", "normalize"]
