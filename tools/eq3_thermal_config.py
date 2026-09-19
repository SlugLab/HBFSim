#!/usr/bin/env python3
"""Validate declarative P1 fixtures and translate to the standalone C++ reader.

No data scheduler or physical calibration is implied by this graph generator.
Only Python's standard library is required; output directories are never reused.
"""
import argparse
import hashlib
import json
import math
from pathlib import Path


def load(path):
    def reject(value):
        raise ValueError(f"nonfinite JSON token: {value}")
    return json.loads(Path(path).read_text(), parse_constant=reject)


def positive(value, label, zero=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label} must be numeric")
    if not math.isfinite(value) or (value < 0 if zero else value <= 0):
        raise ValueError(f"invalid {label}: {value}")


def validate_devices(devices):
    if devices.get("schema_version") != 1:
        raise ValueError("unsupported devices schema")
    for name, profile in devices["profiles"].items():
        count = profile.get("die_count")
        if count is not None and (type(count) is not int or count <= 0):
            raise ValueError(f"invalid die count: {name}")
        if count is not None and profile.get("maximum_stack_height") is not None:
            if count > profile["maximum_stack_height"]:
                raise ValueError(f"die count exceeds specified maximum: {name}")
        for key in ("capacity_bytes", "die_capacity_bits", "bandwidth_bytes_s"):
            if profile.get(key) is not None:
                positive(profile[key], f"{name}.{key}")
        if all(profile.get(k) is not None for k in
               ("die_count", "die_capacity_bits", "capacity_bytes")):
            if count * profile["die_capacity_bits"] != 8 * profile["capacity_bytes"]:
                raise ValueError(f"capacity not conserved: {name}")
        point = profile.get("operating_point")
        if point:
            positive(point["bit_rate_s"], "bit rate")
            positive(profile["interface_bits"], "interface width")
            if profile["interface_bits"] * point["bit_rate_s"] / 8 != point["peak_bytes_s"]:
                raise ValueError(f"interface bandwidth not conserved: {name}")


def generate(topology, devices, thermal, power):
    validate_devices(devices)
    for obj in (topology, thermal, power):
        if obj.get("schema_version") != 1:
            raise ValueError("unsupported schema version")
    for key in ("hbm_count", "hbf_count", "stack_count"):
        if type(topology[key]) is not int or topology[key] < 0:
            raise ValueError(f"invalid {key}")
    hbm, hbf = topology["hbm_count"], topology["hbf_count"]
    if hbm + hbf != topology["stack_count"] or hbm + hbf == 0:
        raise ValueError("stack count not conserved")
    layout = topology["layout"]
    if layout not in ("direct", "daisy", "dual"):
        raise ValueError("unknown layout")
    if layout != "direct" and (hbm != hbf or not topology.get("custom_base_die_relay")):
        raise ValueError("paired relay needs equal counts and custom base die")
    profiles = devices["profiles"]
    hp, fp = profiles[topology["hbm_profile"]], profiles[topology["hbf_profile"]]
    if hp["physical_kind"] != "HBM4" or fp["physical_kind"] != "HBF":
        raise ValueError("stack profile physical kind mismatch")
    for count, profile in ((hbm, hp), (hbf, fp)):
        if count and profile["die_count"] is None:
            raise ValueError("unknown die count; choose explicit assumed_8hi/16hi fixture")
    ext = topology.get("external_fast_memory_profile")
    if ext and profiles[ext]["physical_kind"] != "GDDR":
        raise ValueError("external fast-memory fixture must remain physical GDDR")
    c, g = thermal["capacity_j_k"], thermal["conductance_w_k"]
    for key, value in c.items():
        positive(value, key)
    for key, value in g.items():
        positive(value, key, zero=True)
    positive(thermal["initial_k"], "initial temperature")
    positive(thermal["ambient_k"], "ambient temperature")
    positive(power["end_s"] - power["start_s"], "activity duration")
    positive(power["start_s"], "activity start", zero=True)
    positive(power["energy_j_per_source"], "activity energy", zero=True)
    if type(power["bytes"]) is not int or power["bytes"] < 0:
        raise ValueError("bytes must be a nonnegative integer")
    if power["operation"] != "read":
        raise ValueError("mixed-device P1 fixture supports read only; NAND maintenance needs per-device operations")
    if power["origin"] not in ("demand", "prefetch", "refresh"):
        raise ValueError("unsupported physical activity origin")
    nodes, edges, data_edges, resources, excitations = [], [], [], [], []

    def node(name, physical, logical, group, die, capacity, boundary=0):
        nodes.append((name, physical, logical, group, die, capacity,
                      thermal["initial_k"], 0, boundary, thermal["ambient_k"]))

    def edge(a, b, conductance):
        if conductance > 0:
            edges.append((a, b, conductance))

    node("gpu", "gpu", "compute", "gpu", -1, c["GPU"])
    node("interposer", "interposer", "package", "interposer", -1, c["interposer"])
    node("sink", "cooling", "cooling", "sink", -1, c["sink"], g["sink_ambient"])
    edge("gpu", "interposer", g["gpu_interposer"])
    edge("gpu", "sink", g["gpu_sink"])
    edge("interposer", "sink", g["interposer_sink"])
    excitations.append(("gpu", "external_heat", "external", -1, -1))
    stacks = []
    for kind, count, profile in (("hbm", hbm, hp), ("hbf", hbf, fp)):
        for i in range(count):
            sid = f"{kind}{i}"
            base = f"{sid}_base"
            role = "fast_memory" if kind == "hbm" else "capacity_memory"
            node(base, kind, role, sid, -1, c["base"])
            edge(base, "interposer", g["base_interposer"])
            last = base
            for die in range(profile["die_count"]):
                nid = f"{sid}_die{die}"
                node(nid, kind, role, sid, die, c["die"])
                edge(last, nid, g["vertical"])
                last = nid
            edge(last, "sink", g["top_sink"])
            stacks.append({"stack_id": sid, "physical_kind": profile["physical_kind"],
                           "logical_role": role, "die_count": profile["die_count"],
                           "device_profile_id": topology[f"{kind}_profile"]})
            excitations.append((f"{sid}_die0", power["operation"], power["origin"], len(stacks)-1, 0))
            if kind == "hbm":
                resources.append(f"{sid}_gpu")
                data_edges.append({"from": sid, "to": "gpu", "resource": f"{sid}_gpu"})
            else:
                # Both paths refer to ONE array/TSV resource, never duplicated NAND.
                shared = [f"{sid}_array", f"{sid}_tsv"]
                resources.extend(shared)
                if layout in ("direct", "dual"):
                    data_edges.append({"from": sid, "to": "gpu", "shared": shared})
                if layout in ("daisy", "dual"):
                    data_edges.append({"from": sid, "to": f"hbm{i}_base",
                                       "shared": shared + [f"hbm{i}_gpu"],
                                       "dram_array_access": False,
                                       "energy_node": f"hbm{i}_base"})
                    excitations.append((f"hbm{i}_base", "relay", power["origin"], i, -1))
    if ext:
        node("gddr", "gddr", "fast_memory", "gddr", -1, c["GDDR"], g["gddr_ambient"])
        edge("gddr", "gpu", g["gddr_gpu_board_proxy"])
        data_edges.append({"from": "gddr", "to": "gpu", "location": "board_not_stack_slot"})
        excitations.append(("gddr", power["operation"], power["origin"], -1, -1))
    model = ["HBFSIM_EQ3_THERMAL_MODEL 1", "coupling on"]
    model.extend("node " + " ".join(map(str, n)) for n in nodes)
    model.extend("edge " + " ".join(map(str, e)) for e in edges)
    events = ["HBFSIM_EQ3_THERMAL_EVENTS 1"]
    for i, (nid, kind, origin, stack, die) in enumerate(excitations):
        events.append(f"activity {i+1} {i+1} {kind} {origin} {power['bytes']} "
                      f"{stack} {die} -1 {power['start_s']} {power['end_s']} "
                      f"{power['end_s']} {nid} {power['energy_j_per_source']}")
    graph = {"schema_version": 1, "evidence_class": "UNCALIBRATED_TEST_FIXTURE",
             "topology": topology, "stacks": stacks, "data_edges": data_edges,
             "shared_resources": resources, "thermal_nodes": [n[0] for n in nodes],
             "thermal_edges": edges, "arbitration_status": "NOT_IMPLEMENTED",
             "geometry_status": thermal["geometry"]["status"],
             "note": "Separate graphs; connectivity does not validate throughput or physical geometry."}
    return "\n".join(model)+"\n", "\n".join(events)+"\n", graph


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    default = Path(__file__).resolve().parents[1] / "configs/eq3_thermal"
    parser.add_argument("--topology", type=Path, required=True)
    parser.add_argument("--devices", type=Path, default=default / "devices.json")
    parser.add_argument("--thermal", type=Path, default=default / "thermal_fixture.json")
    parser.add_argument("--power", type=Path, default=default / "power_fixture.json")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    paths = [args.topology, args.devices, args.thermal, args.power]
    model, events, graph = generate(*(load(p) for p in paths))
    args.output.mkdir(parents=True, exist_ok=False)
    (args.output / "model.txt").write_text(model)
    (args.output / "events.txt").write_text(events)
    (args.output / "graph.json").write_text(json.dumps(graph, indent=2)+"\n")
    hashes = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}
    (args.output / "inputs.json").write_text(json.dumps({"hashes": hashes,
        "evidence_class": "UNCALIBRATED_TEST_FIXTURE", "generator_sha256":
        hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}, indent=2)+"\n")
    print(json.dumps({"output": str(args.output), "nodes": len(graph["thermal_nodes"]),
                      "stacks": len(graph["stacks"]), "status": "CONFIG_VALIDATED_NOT_PHYSICAL_CALIBRATION"}))


if __name__ == "__main__":
    main()
