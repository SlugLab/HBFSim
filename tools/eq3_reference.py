#!/usr/bin/env python3
"""Generate and compare the bounded EQ3 P2 NEW_REFERENCE package case.

Only the Python standard library is used. Paths to external executables and run
directories are command-line inputs so generated research logic is portable.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import pathlib
import resource
import subprocess
import sys
import time
from typing import Iterable

SENSORS = ("gpu", "hbm0", "hbm1", "hbm2", "hbm3", "hbf0", "hbf1", "hbf2", "hbf3")
ACCEPTANCE = {"stack_mae_k": 1.0, "hotspot_max_error_k": 2.0, "crossing_fraction": 0.05,
              "crossing_min_samples": 2, "threshold_k": 301.0}


def load(path: pathlib.Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def sha256(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def um(value_m: float) -> float:
    return value_m * 1.0e6


def material(materials: dict) -> dict:
    return materials["materials"]["silicon_case"]


def trace_vectors(power: dict, trace_name: str) -> dict[str, list[float]]:
    trace = power["traces"][trace_name]
    order = power["region_order"]
    result = {name: [] for name in order}
    for slot in trace["slots"]:
        if len(slot["power_w"]) != len(order):
            raise ValueError("power vector length does not match region_order")
        for name, value in zip(order, slot["power_w"]):
            result[name].append(float(value))
    return result


def validate_case(case: dict, materials: dict, power: dict) -> None:
    package = case["package"]
    if package["width_m"] <= 0 or package["height_m"] <= 0:
        raise ValueError("package dimensions must be positive")
    if set(case["sensor_mapping"]["regions"]) != set(SENSORS):
        raise ValueError("sensor mapping must name the fixed nine comparison regions")
    area = sum(r["width_m"] * r["height_m"] for r in case["regions"])
    expected = package["width_m"] * package["height_m"]
    if not math.isclose(area, expected, rel_tol=0, abs_tol=1e-15):
        raise ValueError("regions must cover the package exactly by area")
    mat = material(materials)
    for key in ("density_kg_m3", "specific_heat_j_kg_k", "conductivity_w_m_k"):
        if not math.isfinite(mat[key]) or mat[key] <= 0:
            raise ValueError(f"invalid material field {key}")
    if power["slot_s"] <= 0:
        raise ValueError("slot_s must be positive")
    for name in power["traces"]:
        vectors = trace_vectors(power, name)
        if any(value < 0 or not math.isfinite(value) for values in vectors.values() for value in values):
            raise ValueError("power values must be finite and nonnegative")


def floorplan_text(case: dict, power: dict, trace_name: str) -> str:
    vectors = trace_vectors(power, trace_name)
    lines: list[str] = []
    for region in case["regions"]:
        name = region["id"]
        lines.extend([
            f"{name} :",
            f"  position {um(region['x_m']):.9g}, {um(region['y_m']):.9g} ;",
            f"  dimension {um(region['width_m']):.9g}, {um(region['height_m']):.9g} ;",
        ])
        values = vectors.get(name, [0.0] * len(next(iter(vectors.values()))))
        lines.append("  power values " + ", ".join(f"{v:.9g}" for v in values) + " ;")
        lines.append("")
    return "\n".join(lines)


def stack_text(case: dict, materials: dict, power: dict, mesh_name: str,
               step_s: float, trace_name: str) -> str:
    package = case["package"]
    mat = material(materials)
    cell = case["mesh_cell_m"][mesh_name]
    if not math.isclose(package["width_m"] / cell, round(package["width_m"] / cell), abs_tol=1e-12):
        raise ValueError("mesh must divide package width")
    if not math.isclose(power["slot_s"] / step_s, round(power["slot_s"] / step_s), abs_tol=1e-12):
        raise ValueError("time step must divide the power slot")
    k_um = mat["conductivity_w_m_k"] / 1.0e6
    cv_um = mat["density_kg_m3"] * mat["specific_heat_j_kg_k"] / 1.0e18
    h_um2 = package["top_heat_transfer_w_m2_k"] / 1.0e12
    outputs = "\n".join(
        f'  Tflpel ( PACKAGE.{name}, "temperature_{name}.txt", average, slot );\n'
        f'  Tflpel ( PACKAGE.{name}, "maximum_{name}.txt", maximum, slot );' for name in SENSORS
    )
    return f"""material SILICON_CASE :
  thermal conductivity {k_um:.12g} ;
  volumetric heat capacity {cv_um:.12g} ;

top heat sink :
  heat transfer coefficient {h_um2:.12g} ;
  temperature {case['ambient_k']:.12g} ;

dimensions :
  chip length {um(package['width_m']):.12g}, width {um(package['height_m']):.12g} ;
  cell length {um(cell):.12g}, width {um(cell):.12g} ;

die PACKAGE_DIE :
  layer {um(package['spreader_thickness_m']):.12g} SILICON_CASE ;
  source {um(package['source_thickness_m']):.12g} SILICON_CASE ;

stack:
  die PACKAGE PACKAGE_DIE floorplan "package.flp" ;

solver:
  transient step {step_s:.12g}, slot {power['slot_s']:.12g} ;
  initial temperature {case['ambient_k']:.12g} ;
  numofcores 1 ;

output:
{outputs}
  Tmap ( PACKAGE, "source_map.txt", slot );
"""


def shared_length(a: dict, b: dict) -> tuple[float, float] | None:
    ax0, ax1 = a["x_m"], a["x_m"] + a["width_m"]
    ay0, ay1 = a["y_m"], a["y_m"] + a["height_m"]
    bx0, bx1 = b["x_m"], b["x_m"] + b["width_m"]
    by0, by1 = b["y_m"], b["y_m"] + b["height_m"]
    if math.isclose(ax1, bx0, abs_tol=1e-15) or math.isclose(bx1, ax0, abs_tol=1e-15):
        overlap = min(ay1, by1) - max(ay0, by0)
        return (overlap, (a["width_m"] + b["width_m"]) / 2) if overlap > 0 else None
    if math.isclose(ay1, by0, abs_tol=1e-15) or math.isclose(by1, ay0, abs_tol=1e-15):
        overlap = min(ax1, bx1) - max(ax0, bx0)
        return (overlap, (a["height_m"] + b["height_m"]) / 2) if overlap > 0 else None
    return None


def rc_model_text(case: dict, materials: dict) -> str:
    package = case["package"]
    mat = material(materials)
    rho_cp = mat["density_kg_m3"] * mat["specific_heat_j_kg_k"]
    k = mat["conductivity_w_m_k"]
    source_t = package["source_thickness_m"]
    spread_t = package["spreader_thickness_m"]
    initial = case["ambient_k"]
    nodes = ["HBFSIM_EQ3_THERMAL_MODEL 1", "coupling on"]
    for region in case["regions"]:
        area = region["width_m"] * region["height_m"]
        capacity = rho_cp * area * source_t
        kind = region["kind"]
        physical = kind if kind in {"gpu", "hbm", "hbf"} else "other"
        logical = {"gpu":"compute", "hbm":"fast_memory", "hbf":"capacity_memory"}.get(kind, "package")
        nodes.append(f"node {region['id']} {physical} {logical} {region['id']} -1 {capacity:.17g} {initial:.17g} 0 0 {initial:.17g}")
    spread_capacity = rho_cp * package["width_m"] * package["height_m"] * spread_t
    package_area = package["width_m"] * package["height_m"]
    convection_r = 1.0 / (package["top_heat_transfer_w_m2_k"] * package_area)
    half_spreader_r = spread_t / (2 * k * package_area)
    boundary_g = 1.0 / (convection_r + half_spreader_r)
    nodes.append(f"node spreader interposer package spreader -1 {spread_capacity:.17g} {initial:.17g} 0 {boundary_g:.17g} {initial:.17g}")
    contact_r = materials["interfaces"]["source_to_spreader"]["contact_resistance_m2_k_w"]
    for region in case["regions"]:
        area = region["width_m"] * region["height_m"]
        resistance = source_t / (2 * k * area) + contact_r / area + spread_t / (2 * k * area)
        nodes.append(f"edge {region['id']} spreader {1.0 / resistance:.17g} component")
    regions = case["regions"]
    for index, left in enumerate(regions):
        for right in regions[index + 1:]:
            shared = shared_length(left, right)
            if shared:
                length, distance = shared
                area = length * source_t
                conductance = area / (distance / (2 * k) + distance / (2 * k))
                nodes.append(f"edge {left['id']} {right['id']} {conductance:.17g} component")
    return "\n".join(nodes) + "\n"


def rc_events_text(power: dict, trace_name: str) -> str:
    vectors = trace_vectors(power, trace_name)
    slot = power["slot_s"]
    lines = ["HBFSIM_EQ3_THERMAL_EVENTS 1"]
    event_id = 0
    for slot_index in range(len(power["traces"][trace_name]["slots"])):
        for stack_index, name in enumerate(power["region_order"]):
            watts = vectors[name][slot_index]
            if watts == 0:
                continue
            event_id += 1
            start, end = slot_index * slot, (slot_index + 1) * slot
            source = "external" if name == "gpu" else "demand"
            kind = "external_heat" if name == "gpu" else "read"
            lines.append(
                f"activity {event_id} {event_id} {kind} {source} 0 {stack_index} -1 -1 "
                f"{start:.12g} {end:.12g} {end:.12g} {name} {watts * slot:.17g}"
            )
    return "\n".join(lines) + "\n"


def generate(args: argparse.Namespace) -> None:
    case, materials, power = load(args.case), load(args.materials), load(args.power)
    validate_case(case, materials, power)
    args.output.mkdir(parents=True, exist_ok=False)
    (args.output / "package.flp").write_text(floorplan_text(case, power, args.trace), encoding="utf-8")
    (args.output / "package.stk").write_text(
        stack_text(case, materials, power, args.mesh, args.step_s, args.trace), encoding="utf-8")
    (args.output / "model.txt").write_text(rc_model_text(case, materials), encoding="utf-8")
    (args.output / "events.txt").write_text(rc_events_text(power, args.trace), encoding="utf-8")
    manifest = {
        "reference_identity":"NEW_REFERENCE", "evidence_class":"NUMERICAL_REFERENCE",
        "trace":args.trace, "mesh":args.mesh, "step_s":args.step_s,
        "input_sha256": {str(p.name):sha256(p) for p in (args.case, args.materials, args.power)},
        "generator_sha256":sha256(pathlib.Path(__file__)),
        "generated_sha256": {name:sha256(args.output / name) for name in ("package.flp","package.stk","model.txt","events.txt")},
    }
    (args.output / "input_manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")


def run_command(args: argparse.Namespace) -> None:
    started = time.time()
    usage_before = resource.getrusage(resource.RUSAGE_CHILDREN)
    executable = args.executable.resolve()
    environment = {"OMP_NUM_THREADS":"1", "OPENBLAS_NUM_THREADS":"1"}
    import os
    run_env = os.environ.copy()
    run_env.update(environment)
    if args.kind == "reference":
        command = [str(executable), "package.stk"]
        output = args.run_dir / "reference_stdout.log"
        protected = [output, args.run_dir / "reference_stderr.log", args.run_dir / "reference_receipt.json",
                     args.run_dir / "source_map.txt", args.run_dir / "xaxis.txt", args.run_dir / "yaxis.txt"]
        protected.extend(args.run_dir.glob("temperature_*.txt"))
        protected.extend(args.run_dir.glob("maximum_*.txt"))
    else:
        manifest = load(args.run_dir / "input_manifest.json")
        power = load(args.power)
        end_s = power["slot_s"] * len(power["traces"][manifest["trace"]]["slots"])
        command = [str(executable), "--mode", "shadow", "--model", "model.txt", "--events", "events.txt",
                   "--step-s", f"{args.step_s:.12g}", "--end-s", f"{end_s:.12g}"]
        output = args.run_dir / "rc.csv"
        protected = [output, args.run_dir / "rc_stderr.log", args.run_dir / "rc_receipt.json"]
    existing = [str(path) for path in protected if path.exists()]
    if existing:
        raise FileExistsError("refusing to overwrite immutable run output: " + ", ".join(existing))
    with output.open("wb") as stdout, (args.run_dir / f"{args.kind}_stderr.log").open("wb") as stderr:
        completed = subprocess.run(command, cwd=args.run_dir, env=run_env, stdout=stdout, stderr=stderr,
                                   timeout=args.timeout_s, check=False)
    usage_after = resource.getrusage(resource.RUSAGE_CHILDREN)
    receipt = {
        "kind":args.kind, "command":command, "exit_code":completed.returncode,
        "wall_seconds":time.time()-started, "threads":1, "timeout_s":args.timeout_s,
        "cpu_user_seconds":usage_after.ru_utime-usage_before.ru_utime,
        "cpu_system_seconds":usage_after.ru_stime-usage_before.ru_stime,
        "max_rss_kib_process_tree_upper_bound":usage_after.ru_maxrss,
        "executable_sha256":sha256(executable),
        "outputs_sha256": {p.name:sha256(p) for pattern in
                           (("temperature_*.txt", "maximum_*.txt", "source_map.txt", "xaxis.txt", "yaxis.txt")
                            if args.kind == "reference" else ("rc.csv",))
                           for p in sorted(args.run_dir.glob(pattern))},
    }
    (args.run_dir / f"{args.kind}_receipt.json").write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    if completed.returncode:
        raise SystemExit(completed.returncode)


def numeric_rows(path: pathlib.Path) -> list[tuple[float, float]]:
    values: list[tuple[float, float]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        fields = line.split()
        if not fields:
            continue
        try:
            values.append((float(fields[0]), float(fields[-1])))
        except ValueError:
            continue
    return values


def reference_series(run_dir: pathlib.Path, slot_s: float) -> dict[str, list[tuple[float, float]]]:
    result = {}
    for sensor in SENSORS:
        values = numeric_rows(run_dir / f"temperature_{sensor}.txt")
        for index, (timestamp, _) in enumerate(values):
            expected = (index + 1) * slot_s
            if not math.isclose(timestamp, expected, abs_tol=1e-6):
                raise ValueError(f"unexpected 3D-ICE timestamp {timestamp} in {sensor}; expected {expected}")
        result[sensor] = values
    return result


def map_hotspots(run_dir: pathlib.Path) -> list[dict]:
    maps: list[list[list[float]]] = []
    current: list[list[float]] = []
    for line in (run_dir / "source_map.txt").read_text(encoding="utf-8").splitlines():
        if not line.strip():
            if current:
                maps.append(current)
                current = []
            continue
        try:
            current.append([float(value) for value in line.split()])
        except ValueError:
            if current:
                raise ValueError("unexpected nonnumeric line inside thermal map")
            continue
    if current:
        maps.append(current)
    xaxis = [value / 1.0e6 for _, value in numeric_rows(run_dir / "xaxis.txt")]
    yaxis = [value / 1.0e6 for _, value in numeric_rows(run_dir / "yaxis.txt")]
    hotspots = []
    for grid in maps:
        candidates = [(value, row, column) for row, values in enumerate(grid)
                      for column, value in enumerate(values)]
        value, row, column = max(candidates)
        hotspots.append({"value_k":value, "row":row, "column":column,
                         "x_m":xaxis[column],
                         "y_m":yaxis[row],
                         "layer":"PACKAGE.source"})
    return hotspots


def rc_series(path: pathlib.Path, sample_times: Iterable[float]) -> dict[str, list[tuple[float, float]]]:
    wanted = list(sample_times)
    rows: dict[str, dict[float, float]] = {name:{} for name in SENSORS}
    with path.open(newline="", encoding="utf-8") as stream:
        for row in csv.DictReader(stream):
            if row["record_type"] == "node" and row["location"] in rows:
                rows[row["location"]][round(float(row["time_s"]), 9)] = float(row["value_k"])
    return {name:[(t, rows[name][round(t, 9)]) for t in wanted] for name in SENSORS}


def first_crossing(series: list[tuple[float, float]], threshold: float) -> float | None:
    for time_s, value in series:
        if value >= threshold:
            return time_s
    return None


def compare(args: argparse.Namespace) -> None:
    power = load(args.power)
    case = load(args.case)
    reference_manifest = load(args.reference_run / "input_manifest.json")
    rc_manifest = load(args.rc_run / "input_manifest.json")
    reference_receipt = load(args.reference_run / "reference_receipt.json")
    rc_receipt = load(args.rc_run / "rc_receipt.json")
    if reference_manifest["trace"] != args.split or rc_manifest["trace"] != args.split:
        raise ValueError("comparison split differs from run manifests")
    if reference_manifest["input_sha256"] != rc_manifest["input_sha256"]:
        raise ValueError("reference and RC input identities differ")
    if reference_manifest["input_sha256"][args.power.name] != sha256(args.power):
        raise ValueError("current power input differs from run manifest")
    if reference_receipt["exit_code"] != 0 or rc_receipt["exit_code"] != 0:
        raise ValueError("comparison requires successful run receipts")
    for run_dir, receipt in ((args.reference_run, reference_receipt), (args.rc_run, rc_receipt)):
        for name, expected_hash in receipt["outputs_sha256"].items():
            path = run_dir / name
            if not path.is_file() or sha256(path) != expected_hash:
                raise ValueError(f"run output identity mismatch: {path}")
    slot = power["slot_s"]
    ref = reference_series(args.reference_run, slot)
    count = len(next(iter(ref.values())))
    expected_count = len(power["traces"][args.split]["slots"])
    if count != expected_count or any(len(values) != expected_count for values in ref.values()):
        raise ValueError(f"heldout reference is truncated: expected {expected_count} slots")
    times = [(i + 1) * slot for i in range(count)]
    rc = rc_series(args.rc_run / "rc.csv", times)
    per_sensor = {}
    all_errors = []
    for name in SENSORS:
        errors = [abs(a[1] - b[1]) for a, b in zip(ref[name], rc[name])]
        all_errors.extend(errors)
        per_sensor[name] = {"mae_k":sum(errors)/len(errors), "max_error_k":max(errors)}
    ref_hot = [(t, max(ref[name][i][1] for name in SENSORS)) for i, t in enumerate(times)]
    rc_hot = [(t, max(rc[name][i][1] for name in SENSORS)) for i, t in enumerate(times)]
    sensor_hot_error = max(abs(a[1] - b[1]) for a, b in zip(ref_hot, rc_hot))
    grid = map_hotspots(args.reference_run)
    if len(grid) != len(times):
        raise ValueError(f"expected {len(times)} thermal maps, found {len(grid)}")
    grid_hot_error = max(abs(point["value_k"] - rc_hot[i][1]) for i, point in enumerate(grid))
    ref_cross = first_crossing(ref_hot, ACCEPTANCE["threshold_k"])
    rc_cross = first_crossing(rc_hot, ACCEPTANCE["threshold_k"])
    allowed = None if ref_cross is None else max(ACCEPTANCE["crossing_min_samples"] * slot,
                                                  ACCEPTANCE["crossing_fraction"] * ref_cross)
    crossing_error = None if ref_cross is None or rc_cross is None else abs(ref_cross - rc_cross)
    result = {
        "acceptance_preregistered":ACCEPTANCE,
        "split":"HELD_OUT_ENTIRE_TRACE" if args.split == "heldout" else "TRAIN_ENTIRE_TRACE",
        "per_sensor":per_sensor,
        "aggregate_stack_mae_k":sum(all_errors)/len(all_errors),
        "sensor_average_hotspot_max_error_k":sensor_hot_error,
        "grid_hotspot_max_error_k":grid_hot_error,
        "reference_grid_hotspots":grid,
        "threshold_crossing":{"reference_s":ref_cross,"rc_s":rc_cross,"absolute_error_s":crossing_error,"allowed_s":allowed},
    }
    crossing_pass = ((ref_cross is None and rc_cross is None) or
                     (ref_cross is not None and rc_cross is not None and crossing_error <= allowed))
    result["acceptance_evaluated"] = args.split == "heldout"
    result["pass"] = ((all(v["mae_k"] <= ACCEPTANCE["stack_mae_k"] for v in per_sensor.values())
                       and grid_hot_error <= ACCEPTANCE["hotspot_max_error_k"] and crossing_pass)
                      if args.split == "heldout" else None)
    args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")


def series_delta(left: dict, right: dict) -> dict:
    errors = []
    per_sensor = {}
    for name in SENSORS:
        if [row[0] for row in left[name]] != [row[0] for row in right[name]]:
            raise ValueError("reference comparison timestamps differ")
        sensor_errors = [abs(a[1] - b[1]) for a, b in zip(left[name], right[name])]
        errors.extend(sensor_errors)
        per_sensor[name] = {"mae_k":sum(sensor_errors)/len(sensor_errors),
                            "max_error_k":max(sensor_errors)}
    return {"aggregate_mae_k":sum(errors)/len(errors), "max_error_k":max(errors),
            "per_sensor":per_sensor}


def hotspot_delta(left: list[dict], right: list[dict]) -> dict:
    if len(left) != len(right):
        raise ValueError("hotspot series lengths differ")
    value_errors = [abs(a["value_k"] - b["value_k"]) for a, b in zip(left, right)]
    distances = [math.hypot(a["x_m"] - b["x_m"], a["y_m"] - b["y_m"])
                 for a, b in zip(left, right)]
    return {"mae_k":sum(value_errors)/len(value_errors), "max_error_k":max(value_errors),
            "max_position_distance_m":max(distances)}


def convergence(args: argparse.Namespace) -> None:
    power = load(args.power)
    slot = power["slot_s"]
    runs = {name:path for name, path in (("coarse_dt100",args.coarse_dt100),
                                         ("coarse_dt50",args.coarse_dt50),
                                         ("fine_dt100",args.fine_dt100),
                                         ("fine_dt50",args.fine_dt50))}
    expected = {"coarse_dt100":("coarse",0.1), "coarse_dt50":("coarse",0.05),
                "fine_dt100":("fine",0.1), "fine_dt50":("fine",0.05)}
    for name, path in runs.items():
        manifest = load(path / "input_manifest.json")
        receipt = load(path / "reference_receipt.json")
        mesh, step = expected[name]
        if manifest["trace"] != "train" or manifest["mesh"] != mesh or not math.isclose(manifest["step_s"], step):
            raise ValueError(f"convergence run identity mismatch: {name}")
        if receipt["exit_code"] != 0:
            raise ValueError(f"unsuccessful convergence receipt: {name}")
        for output_name, digest in receipt["outputs_sha256"].items():
            if sha256(path / output_name) != digest:
                raise ValueError(f"convergence output identity mismatch: {name}/{output_name}")
    series = {name:reference_series(path, slot) for name, path in runs.items()}
    hotspots = {name:map_hotspots(path) for name, path in runs.items()}
    result = {
        "evidence_class":"NUMERICAL_REFERENCE",
        "identity":"NEW_REFERENCE",
        "trace":"train",
        "temporal_coarse_dt100_vs_dt50":series_delta(series["coarse_dt100"], series["coarse_dt50"]),
        "temporal_fine_dt100_vs_dt50":series_delta(series["fine_dt100"], series["fine_dt50"]),
        "spatial_coarse_vs_fine_at_dt50":series_delta(series["coarse_dt50"], series["fine_dt50"]),
        "grid_hotspot_temporal_coarse_dt100_vs_dt50":hotspot_delta(hotspots["coarse_dt100"], hotspots["coarse_dt50"]),
        "grid_hotspot_temporal_fine_dt100_vs_dt50":hotspot_delta(hotspots["fine_dt100"], hotspots["fine_dt50"]),
        "grid_hotspot_spatial_coarse_vs_fine_at_dt50":hotspot_delta(hotspots["coarse_dt50"], hotspots["fine_dt50"]),
        "input_energy_j":sum(sum(slot_values["power_w"]) * slot
                             for slot_values in power["traces"]["train"]["slots"]),
        "energy_note":"Identical floorplan power vectors make injected energy identical. 3D-ICE heat-flux/storage balance was not instrumented in this bounded run.",
        "receipts":{name:load(path / "reference_receipt.json") for name, path in runs.items()},
    }
    args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser()
    sub = root.add_subparsers(dest="command", required=True)
    gen = sub.add_parser("generate")
    gen.add_argument("--case", type=pathlib.Path, required=True)
    gen.add_argument("--materials", type=pathlib.Path, required=True)
    gen.add_argument("--power", type=pathlib.Path, required=True)
    gen.add_argument("--trace", choices=("train","heldout"), required=True)
    gen.add_argument("--mesh", choices=("coarse","fine"), required=True)
    gen.add_argument("--step-s", type=float, required=True)
    gen.add_argument("--output", type=pathlib.Path, required=True)
    gen.set_defaults(function=generate)
    run = sub.add_parser("run")
    run.add_argument("--kind", choices=("reference","rc"), required=True)
    run.add_argument("--executable", type=pathlib.Path, required=True)
    run.add_argument("--run-dir", type=pathlib.Path, required=True)
    run.add_argument("--power", type=pathlib.Path)
    run.add_argument("--step-s", type=float, default=0.05)
    run.add_argument("--timeout-s", type=int, default=300)
    run.set_defaults(function=run_command)
    comp = sub.add_parser("compare")
    comp.add_argument("--power", type=pathlib.Path, required=True)
    comp.add_argument("--case", type=pathlib.Path, required=True)
    comp.add_argument("--mesh", choices=("coarse","fine"), required=True)
    comp.add_argument("--split", choices=("train","heldout"), required=True)
    comp.add_argument("--reference-run", type=pathlib.Path, required=True)
    comp.add_argument("--rc-run", type=pathlib.Path, required=True)
    comp.add_argument("--output", type=pathlib.Path, required=True)
    comp.set_defaults(function=compare)
    conv = sub.add_parser("convergence")
    conv.add_argument("--power", type=pathlib.Path, required=True)
    conv.add_argument("--coarse-dt100", type=pathlib.Path, required=True)
    conv.add_argument("--coarse-dt50", type=pathlib.Path, required=True)
    conv.add_argument("--fine-dt100", type=pathlib.Path, required=True)
    conv.add_argument("--fine-dt50", type=pathlib.Path, required=True)
    conv.add_argument("--output", type=pathlib.Path, required=True)
    conv.set_defaults(function=convergence)
    return root


def main() -> None:
    args = parser().parse_args()
    args.function(args)


if __name__ == "__main__":
    main()
