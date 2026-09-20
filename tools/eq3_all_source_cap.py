"""Build an auditable all-source-cap event file without reading thermal output."""
import argparse
import hashlib
import json
import math
from pathlib import Path

from eq3_layered_ir import normalize


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def number(value):
    return format(float(value), ".17g")


def build(profile, power, grid):
    order = power.get("group_order")
    caps = power.get("caps_W")
    if (not isinstance(order, list) or not isinstance(caps, list) or
            len(order) != 17 or len(caps) != len(order) or len(set(order)) != len(order)):
        raise ValueError("frozen all-source cap requires 17 unique ordered groups and caps")
    if any(isinstance(value, bool) or not isinstance(value, (int, float)) or
           not math.isfinite(value) or value < 0 for value in caps):
        raise ValueError("caps_W must be finite and nonnegative")
    slot_s = float(power["slot_s"])
    inline = {"id": "all_source_cap", "duration_s": slot_s,
              "slots_W": [dict(zip(order, caps, strict=True))]}
    ir = normalize(profile, power, inline)
    interval = ir["power"]["intervals"]
    if len(interval) != 1 or interval[0]["start_s"] != 0 or interval[0]["end_s"] != slot_s:
        raise ValueError("all-source normalization must produce exactly one cap slot")
    cells = grid.get("cells")
    component_cells = grid.get("component_cells")
    if not isinstance(cells, list) or not isinstance(component_cells, dict):
        raise ValueError("RC grid lacks cells/component_cells")
    components = {item["id"]: item for item in ir["components"]}
    component_power = interval[0]["power_w"]
    events = ["HBFSIM_EQ3_THERMAL_EVENTS 1"]
    emitted_by_component = {}
    node_cap_w = {}
    used_indices = set()
    for event_id, component_id in enumerate(sorted(component_power), 1):
        watts = float(component_power[component_id])
        indices = component_cells.get(component_id)
        if not isinstance(indices, list) or not indices:
            raise ValueError(f"RC grid lacks powered component {component_id}")
        if (any(isinstance(index, bool) or not isinstance(index, int) or
                index < 0 or index >= len(cells) for index in indices) or
                used_indices.intersection(indices)):
            raise ValueError(f"RC grid has invalid or multiply owned cells for {component_id}")
        used_indices.update(indices)
        if any(cells[index].get("component") != component_id for index in indices):
            raise ValueError(f"RC grid component ownership mismatch for {component_id}")
        expected_volume = math.prod(components[component_id]["size_m"])
        actual_volume = math.fsum(float(cells[index]["volume_m3"]) for index in indices)
        if not math.isclose(actual_volume, expected_volume, rel_tol=1e-10, abs_tol=1e-24):
            raise ValueError(f"RC grid volume mismatch for {component_id}")
        assignments = []
        emitted = 0.0
        for index in indices:
            cell = cells[index]
            energy = watts * slot_s * float(cell["volume_m3"]) / expected_volume
            emitted += energy
            node_cap_w[str(cell["id"])] = node_cap_w.get(str(cell["id"]), 0.0) + energy / slot_s
            assignments.extend((str(cell["id"]), number(energy)))
        emitted_by_component[component_id] = emitted / slot_s
        events.append(" ".join([
            "activity", str(event_id), str(event_id), "external_heat", "external",
            "0", "-1", "-1", "-1", "0", number(slot_s), number(slot_s),
            *assignments]))
    group_members = {}
    for component in ir["components"]:
        group = component.get("power_group")
        if group in order:
            group_members.setdefault(group, []).append(component["id"])
    for group, cap in zip(order, caps, strict=True):
        actual = math.fsum(emitted_by_component.get(item, 0.0)
                           for item in group_members.get(group, []))
        if not math.isclose(actual, float(cap), rel_tol=1e-10, abs_tol=1e-10):
            raise ValueError(f"emitted cap mismatch for group {group}")
    total = math.fsum(emitted_by_component.values())
    expected_total = math.fsum(float(value) for value in caps)
    if not math.isclose(total, expected_total, rel_tol=1e-10, abs_tol=1e-10):
        raise ValueError("all-source cap total is not conserved")
    receipt = {
        "schema_version": "eq3-all-source-cap-v1",
        "status": "CAP_INPUT_GENERATED_NOT_SOLVED",
        "blind_trajectory_read": False,
        "trace_id": "all_source_cap",
        "duration_s": slot_s,
        "group_order": order,
        "caps_w": dict(zip(order, caps, strict=True)),
        "group_members": {key: sorted(value) for key, value in sorted(group_members.items())},
        "component_cap_w": {key: emitted_by_component[key]
                            for key in sorted(emitted_by_component)},
        "node_cap_w": {key: node_cap_w[key] for key in sorted(node_cap_w)},
        "total_cap_w": total,
        "event_count": len(events) - 1,
        "powered_node_count": len(node_cap_w),
    }
    return "\n".join(events) + "\n", receipt


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", type=Path, required=True)
    parser.add_argument("--power", type=Path, required=True)
    parser.add_argument("--rc-grid", type=Path, required=True)
    parser.add_argument("--events-output", type=Path, required=True)
    parser.add_argument("--receipt-output", type=Path, required=True)
    args = parser.parse_args()
    for output in (args.events_output, args.receipt_output):
        if output.exists():
            raise FileExistsError(f"refusing to overwrite {output}")
    events, receipt = build(json.loads(args.profile.read_text()),
                            json.loads(args.power.read_text()),
                            json.loads(args.rc_grid.read_text()))
    receipt["source_sha256"] = {
        "profile": sha256(args.profile), "power": sha256(args.power),
        "rc_grid": sha256(args.rc_grid)}
    args.events_output.write_text(events)
    receipt["events_sha256"] = hashlib.sha256(events.encode()).hexdigest()
    args.receipt_output.write_text(json.dumps(receipt, indent=2) + "\n")
    print(json.dumps({"status": receipt["status"], "total_cap_w": receipt["total_cap_w"],
                      "event_count": receipt["event_count"]}))


if __name__ == "__main__":
    main()
