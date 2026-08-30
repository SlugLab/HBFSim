#!/usr/bin/env python3
"""Prepare, run, and summarize the Phase-II 3D-ICE campaign.

The script deliberately keeps geometry, boundary, mesh, and time-step choices
in machine-readable case manifests.  It reuses only power shapes and floorplan
placement from the immutable Phase-I golden sweeps; every Phase-II stack file
is regenerated here.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import csv
import datetime as dt
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import time
from typing import Any


SOLVER_SHA256 = "48d736c7eedc23c1bf4dc6a1c76b3bb58671f59bb9b3bbbba8c07a8abf463e60"
THREE_D_ICE_COMMIT = "e0bb6850c5e446363e26936586d625270c87f224"
AMBIENT_C = 30.0
AMBIENT_K = 303.15
HBF_TOTAL_W = 53.72
BASELINE_PER_DIE_W = HBF_TOTAL_W / 8.0
DOMAIN_UM = (50_000.0, 30_000.0)
MIN_FREE_BYTES = 35 * 1024**3
TIMESTEP_PERIODS_MS = (10, 25, 50, 100)
THERMAL_THRESHOLDS_C = (80.0, 90.0, 100.0, 105.0)

MATERIALS = {
    "SILICON": ((1.30e-4, 1.30e-4, 1.30e-4), 1.628e-12,
                "L", "3D-ICE 4.0 test/plugin/test_aligned.stk"),
    "MOLD": ((0.70e-6, 0.70e-6, 0.70e-6), 1.50e-12,
             "C", "campaign sensitivity; unreleased encapsulant"),
    "BOND": ((1.40e-6, 1.40e-6, 1.40e-6), 1.55e-12,
             "C", "campaign sensitivity; effective bond/oxide"),
    "SUBSTRATE": ((10.0e-6, 10.0e-6, 10.0e-6), 1.50e-12,
                  "C", "campaign sensitivity; effective substrate"),
    "TIM": ((4.0e-6, 4.0e-6, 4.0e-6), 1.50e-12,
            "C", "campaign sensitivity; nominal package TIM"),
    # CAP is a structural sensitivity layer.  Reusing the published fixture's
    # silicon properties avoids implying an unmeasured production lid alloy.
    "CAP": ((1.30e-4, 1.30e-4, 1.30e-4), 1.628e-12,
            "C", "structural cap sensitivity using fixture silicon properties"),
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n",
                         encoding="utf-8")
    temporary.replace(path)


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def find_phase1_golden(root: Path, height: int) -> Path:
    matches = sorted(root.glob(f"golden-{height}hi-v9-128x250ms-*"))
    matches = [path for path in matches
               if (path / "3dice-run-manifest.json").is_file()]
    if len(matches) != 1:
        raise ValueError(f"expected one immutable {height}Hi v9 golden, found {matches}")
    return matches[0]


def geometry_layers(kind: str, height: int) -> tuple[list[tuple[float, str]], float]:
    active = height * 31.0 + 50.0 + 100.0
    if kind == "g0_legacy_fixed_envelope":
        filler = 775.0 - active
        return [(filler, "MOLD")], 775.0
    if kind == "g1_constant_top_interface":
        layers = [(75.0, "CAP"), (50.0, "TIM"), (4.0, "MOLD")]
        return layers, active + sum(item[0] for item in layers)
    cap_match = re.fullmatch(r"g1_cap_(\d+)um", kind)
    if cap_match:
        cap_um = float(cap_match.group(1))
        layers = [(cap_um, "CAP"), (50.0, "TIM"), (4.0, "MOLD")]
        return layers, active + sum(item[0] for item in layers)
    if kind == "g2_fixed_envelope_explicit":
        filler = 775.0 - active - 75.0 - 50.0
        if filler < 0.0:
            raise ValueError("fixed-envelope layers exceed package height")
        return [(75.0, "CAP"), (50.0, "TIM"), (filler, "MOLD")], 775.0
    raise ValueError(f"unknown geometry: {kind}")


def material_text() -> str:
    chunks = []
    for name, (conductivity, cv, _, _) in MATERIALS.items():
        kx, ky, kz = conductivity
        chunks.append(
            f"material {name} :\n"
            f"  thermal conductivity {kx:.12g}, {ky:.12g}, {kz:.12g} ;\n"
            f"  volumetric heat capacity {cv:.12g} ;\n")
    return "\n".join(chunks)


def stack_text(height: int, geometry: str, mesh_um: float,
               top_h: float | None, bottom_h: float | None,
               transient_s: float | None, output_names: list[str]) -> str:
    interface_layers, _ = geometry_layers(geometry, height)
    sinks = []
    if top_h is not None:
        sinks.append("top heat sink :\n"
                     f"  heat transfer coefficient {top_h:.12g} ;\n"
                     f"  temperature {AMBIENT_K:.12g} ;\n")
    if bottom_h is not None:
        sinks.append("bottom heat sink :\n"
                     f"  heat transfer coefficient {bottom_h:.12g} ;\n"
                     f"  temperature {AMBIENT_K:.12g} ;\n")
    top_layers = "\n".join(
        f"  layer {thickness:.12g} {material} ;"
        for thickness, material in interface_layers if thickness > 0.0)
    stack = [
        f"  die HBF_L{layer} {'TOP_NAND_DIE' if layer == height - 1 else 'NAND_DIE'} "
        f"floorplan \"hbf-layer-{layer}.flp\" ;"
        for layer in reversed(range(height))
    ]
    stack.append("  die PACKAGE_BASE COPACKAGE_DIE floorplan \"copackage.flp\" ;")
    coordinates = {
        "gpu": ("PACKAGE_BASE", 10_000.0, 10_000.0),
        "hbm": ("PACKAGE_BASE", 26_500.0, 6_000.0),
        "hbf.base": ("PACKAGE_BASE", 41_000.0, 5_487.5),
        **{f"hbf.s0.l{layer}": (f"HBF_L{layer}", 41_000.0, 5_487.5)
           for layer in range(height)},
    }
    instant = "step" if transient_s is not None else "final"
    outputs = []
    for name in output_names:
        die, x, y = coordinates[name]
        outputs.append(
            f"  T ( {die}, {x:g}, {y:g}, "
            f"\"temperature-{name.replace('.', '_')}.txt\", {instant} ) ;")
    solver = (f"  transient step {transient_s:.12g}, slot {transient_s:.12g} ;"
              if transient_s is not None else "  steady ;")
    return (
        material_text() + "\n" + "\n".join(sinks) + "\n"
        "dimensions :\n"
        f"  chip length {DOMAIN_UM[0]:g}, width {DOMAIN_UM[1]:g} ;\n"
        f"  cell length {mesh_um:g}, width {mesh_um:g} ;\n"
        "  non-uniform true ;\n\n"
        "die NAND_DIE :\n"
        "  source 30 MOLD ;\n"
        "  layer 1 BOND ;\n\n"
        "die TOP_NAND_DIE :\n"
        f"{top_layers}\n"
        "  source 30 MOLD ;\n"
        "  layer 1 BOND ;\n\n"
        "die COPACKAGE_DIE :\n"
        "  source 50 MOLD ;\n"
        "  layer 100 SUBSTRATE ;\n\n"
        "stack :\n" + "\n".join(stack) + "\n\n"
        "solver :\n" + solver + "\n"
        f"  initial temperature {AMBIENT_K:.12g} ;\n"
        "  numofcores 1 ;\n\n"
        "output :\n" + "\n".join(outputs) + "\n")


def block_to_node(block: str) -> str | None:
    if block in ("gpu", "hbm"):
        return block
    if block == "hbf_base":
        return "hbf.base"
    match = re.fullmatch(r"hbf_l(\d+)", block)
    if match:
        return f"hbf.s0.l{match.group(1)}"
    match = re.fullmatch(r"hbf_s(\d+)_l(\d+)", block)
    return f"hbf.s{match.group(1)}.l{match.group(2)}" if match else None


def rewrite_floorplan(source: Path, target: Path,
                      values: dict[str, list[float]], zero_count: int) -> None:
    current: str | None = None
    rendered = []
    replacements = 0
    for line in source.read_text(encoding="utf-8").splitlines():
        match = re.match(r"^([A-Za-z0-9_]+):\s*$", line)
        if match:
            current = block_to_node(match.group(1))
        if "power values" in line:
            row = values.get(current, [0.0] * zero_count)
            line = "  power values " + ", ".join(f"{item:.12g}" for item in row) + " ;"
            replacements += 1
        rendered.append(line)
    if not replacements:
        raise ValueError(f"no power values in {source}")
    target.write_text("\n".join(rendered) + "\n", encoding="utf-8")


def read_power(path: Path) -> tuple[list[str], list[int], list[list[float]]]:
    with path.open("r", encoding="utf-8", newline="") as stream:
        rows = list(csv.reader(stream))
    return rows[0][1:], [int(row[0]) for row in rows[1:]], [
        [float(value) for value in row[1:]] for row in rows[1:]]


def resolve_phase1_power(case_dir: Path, relative: str) -> Path:
    candidates = [(case_dir / relative).resolve(),
                  (case_dir.parents[1] / relative).resolve()]
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    raise ValueError(f"cannot resolve Phase-I power trace {relative} from {case_dir}")


def resample(times: list[int], rows: list[list[float]], period_ns: int
             ) -> tuple[list[int], list[list[float]]]:
    if len(times) < 2 or times[0] != 0:
        raise ValueError("power trace must start at zero and contain at least two rows")
    stop = times[-1] + (times[-1] - times[-2])
    new_times = list(range(0, stop, period_ns))
    result = []
    source_index = 0
    for when in new_times:
        while source_index + 1 < len(times) and times[source_index + 1] <= when:
            source_index += 1
        result.append(rows[source_index])
    return new_times, result


def scale_held_out_rows(names: list[str], rows: list[list[float]]) -> list[list[float]]:
    """Scale normalized Phase-I held-out shapes to documented package powers."""
    result = []
    for row in rows:
        scaled = []
        for name, value in zip(names, row):
            if name == "gpu":
                factor = 375.0  # normalized 0.8 peak -> 300 W [C]
            elif name == "hbm":
                factor = 237.5  # normalized 0.4 peak -> 95 W [L/C]
            elif name == "hbf.base" or name.startswith("hbf.s"):
                factor = 89.53333333333333  # normalized aggregate peak -> 53.72 W [L/C]
            else:
                factor = 1.0
            scaled.append(value * factor)
        result.append(scaled)
    return result


def case_nodes(height: int) -> list[str]:
    return ["gpu", "hbm", "hbf.base", *
            (f"hbf.s0.l{layer}" for layer in range(height))]


def source_template(golden_root: Path, height: int) -> Path:
    golden = find_phase1_golden(golden_root, height)
    return golden / "cases" / "held-mixed"


def case_provenance(height: int, geometry: str, mesh_um: float,
                    top_h: float | None, bottom_h: float | None) -> dict[str, Any]:
    interface, total = geometry_layers(geometry, height)
    return {
        "schema_version": 2,
        "evidence_level": "literature_bounded_sensitivity",
        "three_d_ice": {"version": "4.0", "commit": THREE_D_ICE_COMMIT},
        "geometry": {
            "arm": geometry, "stack_height": height,
            "package_total_height_um": total,
            "nand_die_um": 30.0, "inter_die_bond_um": 1.0,
            "base_source_um": 50.0, "substrate_um": 100.0,
            "top_interface_layers": [
                {"thickness_um": value, "material": material}
                for value, material in interface],
            "footprint_um": [16_000.0, 10_975.0],
        },
        "boundary": {"ambient_c": AMBIENT_C, "top_h_w_per_um2_k": top_h,
                     "bottom_h_w_per_um2_k": bottom_h},
        "mesh": {"xy_nominal_um": mesh_um, "mode": "non-uniform",
                 "bond_z_resolution_um": 1.0},
        "materials": {
            name: {"kx_w_per_um_k": value[0][0], "ky_w_per_um_k": value[0][1],
                   "kz_w_per_um_k": value[0][2], "cv_j_per_um3_k": value[1],
                   "class": value[2], "source": value[3]}
            for name, value in MATERIALS.items()},
    }


def prepare_study(golden_root: Path, output: Path) -> None:
    if output.exists():
        raise ValueError(f"refusing to overwrite {output}")
    output.mkdir(parents=True)
    specs: dict[tuple[Any, ...], set[str]] = {}

    def add(role: str, geometry: str, height: int, boundary: str,
            mesh: int, power: str) -> None:
        specs.setdefault((geometry, height, boundary, mesh, power), set()).add(role)

    for height in (8, 16):
        for geometry in ("g0_legacy_fixed_envelope", "g1_constant_top_interface",
                         "g2_fixed_envelope_explicit"):
            for power in ("equal_total", "equal_per_die"):
                add("geometry", geometry, height, "top_nominal", 2000, power)
        for boundary in ("top_weak", "top_nominal", "top_strong", "top_and_bottom"):
            add("boundary", "g1_constant_top_interface", height, boundary, 2000,
                "equal_total")
        for mesh in (4000, 2000, 1000, 500):
            add("mesh", "g1_constant_top_interface", height, "top_nominal", mesh,
                "equal_total")

    boundaries = {
        "top_weak": (0.5e-7, None), "top_nominal": (1.0e-7, None),
        "top_strong": (2.0e-7, None), "top_and_bottom": (1.0e-7, 1.0e-7),
    }
    cases = []
    for index, (spec, roles) in enumerate(sorted(specs.items())):
        geometry, height, boundary, mesh, power = spec
        top_h, bottom_h = boundaries[boundary]
        case_id = f"case-{index:03d}-{height}hi-{geometry[:2]}-{boundary}-{mesh}um-{power}"
        target = output / "cases" / case_id
        target.mkdir(parents=True)
        template = source_template(golden_root, height)
        names = case_nodes(height)
        die_w = HBF_TOTAL_W / height if power == "equal_total" else BASELINE_PER_DIE_W
        values = {name: [die_w] for name in names if name.startswith("hbf.s")}
        for source in template.glob("*.flp"):
            rewrite_floorplan(source, target / source.name, values, 1)
        (target / "package.stk").write_text(
            stack_text(height, geometry, mesh, top_h, bottom_h, None, names),
            encoding="utf-8")
        manifest = case_provenance(height, geometry, mesh, top_h, bottom_h)
        manifest.update({
            "case_id": case_id, "study_roles": sorted(roles), "analysis": "steady",
            "input_names": names, "output_names": names,
            "power": {"arm": power, "hbf_total_w": die_w * height,
                      "per_nand_die_w": die_w, "gpu_w": 0.0, "hbm_w": 0.0,
                      "hbf_base_w": 0.0},
        })
        write_json(target / "case.json", manifest)
        cases.append(f"cases/{case_id}/case.json")
    write_json(output / "campaign-plan.json", {
        "schema_version": 2, "created_utc": utc_now(), "kind": "phase2_steady_study",
        "case_count": len(cases), "cases": cases,
        "deduplication": "identical physical cases shared across study roles",
    })


def prepare_cap_sweep(golden_root: Path, output: Path) -> None:
    """Prepare the required constant-active-stack cap-thickness comparison."""
    if output.exists():
        raise ValueError(f"refusing to overwrite {output}")
    output.mkdir(parents=True)
    cases = []
    for height in (8, 16):
        template = source_template(golden_root, height)
        names = case_nodes(height)
        die_w = HBF_TOTAL_W / height
        values = {name: [die_w] for name in names if name.startswith("hbf.s")}
        for cap_um in (129, 200, 300, 377):
            geometry = f"g1_cap_{cap_um}um"
            case_id = f"cap-{height}hi-{cap_um}um-equal-total"
            target = output / "cases" / case_id
            target.mkdir(parents=True)
            for source in template.glob("*.flp"):
                rewrite_floorplan(source, target / source.name, values, 1)
            (target / "package.stk").write_text(
                stack_text(height, geometry, 2000, 1.0e-7, None, None, names),
                encoding="utf-8")
            manifest = case_provenance(
                height, geometry, 2000, 1.0e-7, None)
            manifest.update({
                "case_id": case_id,
                "study_roles": ["constant_active_stack_cap_sweep"],
                "analysis": "steady",
                "input_names": names,
                "output_names": names,
                "power": {
                    "arm": "equal_total",
                    "hbf_total_w": HBF_TOTAL_W,
                    "per_nand_die_w": die_w,
                    "gpu_w": 0.0,
                    "hbm_w": 0.0,
                    "hbf_base_w": 0.0,
                },
            })
            write_json(target / "case.json", manifest)
            cases.append(f"cases/{case_id}/case.json")
    write_json(output / "campaign-plan.json", {
        "schema_version": 2,
        "created_utc": utc_now(),
        "kind": "phase2_cap_thickness_sweep",
        "cap_thickness_um": [129, 200, 300, 377],
        "mesh_um": 2000,
        "power_arm": "equal_total",
        "hbf_total_w": HBF_TOTAL_W,
        "case_count": len(cases),
        "cases": cases,
    })


def prepare_golden(golden_root: Path, output: Path, mesh_um: int,
                   geometry: str = "g1_constant_top_interface") -> None:
    if output.exists():
        raise ValueError(f"refusing to overwrite {output}")
    output.mkdir(parents=True)
    period_ns = 10_000_000
    cases = []
    for height in (8, 16):
        source = find_phase1_golden(golden_root, height)
        for old_case_path in sorted(source.glob("cases/*/case.json")):
            old_case = json.loads(old_case_path.read_text(encoding="utf-8"))
            old_dir = old_case_path.parent
            names, times, rows = read_power(
                resolve_phase1_power(old_dir, old_case["power_trace"]))
            split = "training" if old_dir.name.startswith("unit-") else "held_out"
            if split == "held_out":
                rows = scale_held_out_rows(names, rows)
            new_times, new_rows = resample(times, rows, period_ns)
            case_id = f"{height}hi-{old_dir.name}"
            target = output / "cases" / case_id
            target.mkdir(parents=True)
            by_name = {name: [row[index] for row in new_rows]
                       for index, name in enumerate(names)}
            for source_flp in old_dir.glob("*.flp"):
                rewrite_floorplan(source_flp, target / source_flp.name,
                                  by_name, len(new_times))
            output_names = old_case["output_names"]
            (target / "package.stk").write_text(
                stack_text(height, geometry, mesh_um, 1.0e-7, None,
                           period_ns / 1e9, output_names), encoding="utf-8")
            with (target / "power.csv").open("w", encoding="utf-8", newline="") as stream:
                writer = csv.writer(stream, lineterminator="\n")
                writer.writerow(["time_ns", *names])
                writer.writerows([[when, *row] for when, row in zip(new_times, new_rows)])
            manifest = case_provenance(height, geometry, mesh_um, 1.0e-7, None)
            manifest.update({
                "case_id": case_id, "analysis": "transient", "sample_period_ns": period_ns,
                "samples": len(new_times), "input_names": names,
                "output_names": output_names, "power_trace": "power.csv",
                "phase1_source_case": str(old_case_path),
                "phase1_source_case_sha256": sha256(old_case_path),
                "trace_id": old_case.get("trace_id", old_dir.name),
                "trace_kind": old_case["trace_kind"],
                "active_source": old_case.get("active_source"),
                "step_watts": old_case.get("step_watts", 0.0),
                "split": split,
                "power_scaling": ("unit_step_1w_unscaled" if split == "training" else
                                  "gpu_300w_hbm_95w_hbf_53.72w_peak_shape"),
            })
            write_json(target / "case.json", manifest)
            cases.append(f"cases/{case_id}/case.json")
    write_json(output / "campaign-plan.json", {
        "schema_version": 2, "created_utc": utc_now(), "kind": "phase2_10ms_golden",
        "geometry": geometry, "mesh_um": mesh_um, "sample_period_ns": period_ns,
        "case_count": len(cases), "cases": cases,
    })


def prepare_timestep(golden_root: Path, output: Path, mesh_um: int,
                     geometry: str = "g1_constant_top_interface") -> None:
    if output.exists():
        raise ValueError(f"refusing to overwrite {output}")
    output.mkdir(parents=True)
    cases = []
    for height in (8, 16):
        source = find_phase1_golden(golden_root, height) / "cases" / "held-mixed"
        old_case_path = source / "case.json"
        old_case = json.loads(old_case_path.read_text(encoding="utf-8"))
        names, times, rows = read_power(
            resolve_phase1_power(source, old_case["power_trace"]))
        rows = scale_held_out_rows(names, rows)
        for period_ms in TIMESTEP_PERIODS_MS:
            period_ns = period_ms * 1_000_000
            new_times, new_rows = resample(times, rows, period_ns)
            case_id = f"{height}hi-held-mixed-{period_ms}ms"
            target = output / "cases" / case_id
            target.mkdir(parents=True)
            by_name = {name: [row[index] for row in new_rows]
                       for index, name in enumerate(names)}
            for source_flp in source.glob("*.flp"):
                rewrite_floorplan(source_flp, target / source_flp.name,
                                  by_name, len(new_times))
            output_names = old_case["output_names"]
            (target / "package.stk").write_text(
                stack_text(height, geometry, mesh_um, 1.0e-7, None,
                           period_ns / 1e9, output_names), encoding="utf-8")
            with (target / "power.csv").open("w", encoding="utf-8", newline="") as stream:
                writer = csv.writer(stream, lineterminator="\n")
                writer.writerow(["time_ns", *names])
                writer.writerows([[when, *row] for when, row in zip(new_times, new_rows)])
            manifest = case_provenance(height, geometry, mesh_um, 1.0e-7, None)
            manifest.update({
                "case_id": case_id, "analysis": "transient",
                "sample_period_ns": period_ns, "samples": len(new_times),
                "input_names": names, "output_names": output_names,
                "power_trace": "power.csv", "trace_id": "held-mixed",
                "split": "timestep", "period_ms": period_ms,
                "power_scaling": "gpu_300w_hbm_95w_hbf_53.72w_peak_shape",
                "phase1_source_case": str(old_case_path),
                "phase1_source_case_sha256": sha256(old_case_path),
            })
            write_json(target / "case.json", manifest)
            cases.append(f"cases/{case_id}/case.json")
    write_json(output / "campaign-plan.json", {
        "schema_version": 2, "created_utc": utc_now(),
        "kind": "phase2_timestep_study", "geometry": geometry,
        "mesh_um": mesh_um, "periods_ms": list(TIMESTEP_PERIODS_MS),
        "reference_period_ms": 10, "case_count": len(cases), "cases": cases,
    })


def numeric_rows(path: Path) -> list[list[float]]:
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("%"):
            continue
        rows.append([float(value) for value in line.split()])
    if not rows:
        raise ValueError(f"no numeric output in {path}")
    return rows


def convert_transient(case_dir: Path, case: dict[str, Any]) -> None:
    names = case["output_names"]
    traces = {name: numeric_rows(case_dir / f"temperature-{name.replace('.', '_')}.txt")
              for name in names}
    count = len(next(iter(traces.values())))
    if any(len(value) != count for value in traces.values()):
        raise ValueError("temperature output lengths differ")
    period_ns = int(case["sample_period_ns"])
    output = case_dir / "temperatures.csv"
    with output.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream, lineterminator="\n")
        writer.writerow(["time_ns", *names])
        writer.writerow([0, *([AMBIENT_C] * len(names))])
        for index in range(1, count):
            writer.writerow([index * period_ns, *
                             (traces[name][index - 1][-1] - 273.15 for name in names)])


def summarize_case(case_dir: Path, case: dict[str, Any]) -> dict[str, Any]:
    names = case["output_names"]
    hbf = [index for index, name in enumerate(names)
           if name == "hbf.base" or name.startswith("hbf.s")]
    if case["analysis"] == "steady":
        final = [numeric_rows(case_dir / f"temperature-{name.replace('.', '_')}.txt")[-1][-1]
                 - 273.15 for name in names]
        hotspot = max(final[index] for index in hbf)
        total_w = case["power"]["hbf_total_w"]
        return {"final_hbf_hotspot_c": hotspot,
                "thermal_resistance_k_per_w": (hotspot - AMBIENT_C) / total_w,
                "final_temperatures_c": dict(zip(names, final))}
    with (case_dir / "temperatures.csv").open("r", encoding="utf-8", newline="") as stream:
        rows = list(csv.reader(stream))
    values = [[float(value) for value in row[1:]] for row in rows[1:]]
    hotspots = [max(row[index] for index in hbf) for row in values]
    return {"samples": len(values), "peak_hbf_hotspot_c": max(hotspots),
            "final_hbf_hotspot_c": hotspots[-1]}


def archive_failed_attempt(case_dir: Path) -> None:
    archive_root = case_dir / "failed-attempts"
    archive_root.mkdir(exist_ok=True)
    index = 1
    while (archive_root / f"attempt-{index:03d}").exists():
        index += 1
    target = archive_root / f"attempt-{index:03d}"
    target.mkdir()
    names = ("run-result.json", "command.txt", "stdout.log", "stderr.log",
             "resource-usage.txt", "temperatures.csv")
    for name in names:
        source = case_dir / name
        if source.exists():
            source.replace(target / name)
    for source in case_dir.glob("temperature-*.txt"):
        source.replace(target / source.name)


def run_one(solver: Path, case_path: Path, timeout_s: int,
            retry_failed: bool = False, force_rerun: bool = False) -> dict[str, Any]:
    case_dir = case_path.parent
    result_path = case_dir / "run-result.json"
    if result_path.exists():
        existing = json.loads(result_path.read_text(encoding="utf-8"))
        if existing.get("returncode") == 0 and not force_rerun:
            return existing
        if existing.get("returncode") != 0 and not retry_failed and not force_rerun:
            raise RuntimeError(f"previous failed attempt exists for {case_dir.name}")
        archive_failed_attempt(case_dir)
    if shutil.disk_usage(case_dir).free < MIN_FREE_BYTES:
        raise RuntimeError("campaign free-space guard reached")
    case = json.loads(case_path.read_text(encoding="utf-8"))
    command = ["/usr/bin/time", "-v", "-o", "resource-usage.txt", "env",
               "OPENBLAS_NUM_THREADS=1", "OMP_NUM_THREADS=1", "MKL_NUM_THREADS=1",
               "timeout", "--signal=TERM", "--kill-after=30s", f"{timeout_s}s",
               str(solver), "package.stk"]
    (case_dir / "command.txt").write_text(" ".join(command) + "\n", encoding="utf-8")
    started = time.monotonic()
    with (case_dir / "stdout.log").open("wb") as stdout, \
            (case_dir / "stderr.log").open("wb") as stderr:
        completed = subprocess.run(command, cwd=case_dir, stdout=stdout, stderr=stderr,
                                   check=False)
    result: dict[str, Any] = {
        "case_id": case["case_id"], "returncode": completed.returncode,
        "elapsed_seconds": time.monotonic() - started,
        "finished_utc": utc_now(), "case_sha256": sha256(case_path),
        "stack_sha256": sha256(case_dir / "package.stk"),
    }
    if completed.returncode == 0:
        if case["analysis"] == "transient":
            convert_transient(case_dir, case)
        result["summary"] = summarize_case(case_dir, case)
        result["outputs"] = [
            {"path": path.name, "sha256": sha256(path)}
            for path in sorted(case_dir.glob("temperature-*.txt"))]
        if (case_dir / "temperatures.csv").is_file():
            result["aligned_temperature_sha256"] = sha256(case_dir / "temperatures.csv")
    write_json(result_path, result)
    if completed.returncode != 0:
        raise RuntimeError(f"3D-ICE failed for {case['case_id']}: {completed.returncode}")
    return result


def run_campaign(solver: Path, root: Path, jobs: int, timeout_s: int,
                 case_regex: str | None = None, retry_failed: bool = False,
                 expected_solver_sha256: str = SOLVER_SHA256,
                 force_rerun: bool = False) -> None:
    solver = solver.resolve()
    actual_solver_sha256 = sha256(solver)
    if actual_solver_sha256 != expected_solver_sha256:
        raise ValueError("solver SHA-256 mismatch")
    plan_path = root / "campaign-plan.json"
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    case_paths = [(root / relative).resolve() for relative in plan["cases"]]
    if case_regex:
        pattern = re.compile(case_regex)
        case_paths = [path for path in case_paths if pattern.search(path.parent.name)]
    if not case_paths:
        raise ValueError("case filter selected no cases")
    results = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=jobs) as pool:
        futures = {pool.submit(run_one, solver, path, timeout_s,
                               retry_failed, force_rerun): path
                   for path in case_paths}
        for future in concurrent.futures.as_completed(futures):
            results.append(future.result())
            print(f"completed {results[-1]['case_id']} ({len(results)}/{len(case_paths)})",
                  flush=True)
    results.sort(key=lambda value: value["case_id"])
    manifest_name = "run-manifest.json" if case_regex is None else (
        f"run-manifest-filtered-{int(time.time())}.json")
    write_json(root / manifest_name, {
        "schema_version": 2, "status": "complete", "finished_utc": utc_now(),
        "solver_sha256": actual_solver_sha256, "solver_commit": THREE_D_ICE_COMMIT,
        "campaign_plan_sha256": sha256(plan_path), "jobs": jobs,
        "case_regex": case_regex, "retry_failed": retry_failed,
        "force_rerun": force_rerun, "runs": results,
    })
    summarize_campaign(root)


def summarize_campaign(root: Path) -> None:
    plan = json.loads((root / "campaign-plan.json").read_text(encoding="utf-8"))
    rows = []
    for relative in plan["cases"]:
        case_path = root / relative
        case = json.loads(case_path.read_text(encoding="utf-8"))
        result_path = case_path.parent / "run-result.json"
        if not result_path.is_file():
            rows.append({"case_id": case["case_id"], "status": "not_run"})
            continue
        result = json.loads(result_path.read_text(encoding="utf-8"))
        if result.get("returncode") != 0:
            rows.append({"case_id": case["case_id"], "status": "failed",
                         "returncode": result.get("returncode"),
                         "elapsed_seconds": result.get("elapsed_seconds")})
            continue
        row = {"case_id": case["case_id"], "analysis": case["analysis"],
               "height": case["geometry"]["stack_height"],
               "geometry": case["geometry"]["arm"],
               "mesh_um": case["mesh"]["xy_nominal_um"],
               "top_h": case["boundary"]["top_h_w_per_um2_k"],
               "bottom_h": case["boundary"]["bottom_h_w_per_um2_k"],
               "elapsed_seconds": result["elapsed_seconds"], **result["summary"]}
        if "power" in case:
            row.update({"power_arm": case["power"]["arm"],
                        "hbf_total_w": case["power"]["hbf_total_w"],
                        "study_roles": case["study_roles"]})
            if plan["kind"] == "phase2_cap_thickness_sweep":
                row["cap_thickness_um"] = case["geometry"][
                    "top_interface_layers"][0]["thickness_um"]
        else:
            row.update({"trace_id": case["trace_id"], "split": case["split"]})
        rows.append(row)
    summary: dict[str, Any] = {"schema_version": 2, "kind": plan["kind"], "cases": rows}
    if plan["kind"] == "phase2_steady_study":
        mesh_rows = [row for row in rows if "mesh" in row.get("study_roles", [])]
        convergence = []
        for height in (8, 16):
            by_mesh = {row["mesh_um"]: row for row in mesh_rows if row["height"] == height}
            # The requested coarse/medium/fine sequence is 4000/2000/1000 um.
            # Medium is the deployable candidate and fine is its next-finer
            # reference.  A 500 um arm may exist as supplementary evidence but
            # is not required for accepting the 2000 um mesh.
            fine, next_fine = by_mesh[2000], by_mesh[1000]
            temp_error = abs(fine["final_hbf_hotspot_c"] - next_fine["final_hbf_hotspot_c"])
            resistance_error = abs(fine["thermal_resistance_k_per_w"] /
                                   next_fine["thermal_resistance_k_per_w"] - 1.0) * 100.0
            convergence.append({"height": height, "candidate_mesh_um": 2000,
                                "next_finer_mesh_um": 1000,
                                "hotspot_difference_c": temp_error,
                                "thermal_resistance_difference_percent": resistance_error,
                                "accepted": temp_error <= 0.5 and resistance_error <= 2.0})
        summary["mesh_acceptance"] = convergence
        summary["selected_mesh_um"] = 2000 if all(item["accepted"] for item in convergence) else None
    elif plan["kind"] == "phase2_cap_thickness_sweep":
        summary["resistance_curve"] = [
            {
                "height": row["height"],
                "cap_thickness_um": row["cap_thickness_um"],
                "thermal_resistance_k_per_w": row[
                    "thermal_resistance_k_per_w"],
                "final_hbf_hotspot_c": row["final_hbf_hotspot_c"],
            }
            for row in rows
            if row.get("status") is None
        ]
    elif plan["kind"] == "phase2_timestep_study":
        comparisons = []
        for height in (8, 16):
            reference_case = root / "cases" / f"{height}hi-held-mixed-10ms"
            ref_names, reference = load_aligned_temperatures(
                reference_case / "temperatures.csv")
            hbf_indices = [index for index, name in enumerate(ref_names)
                           if name == "hbf.base" or name.startswith("hbf.s")]
            for period_ms in TIMESTEP_PERIODS_MS:
                case_dir = root / "cases" / f"{height}hi-held-mixed-{period_ms}ms"
                names, actual = load_aligned_temperatures(case_dir / "temperatures.csv")
                if names != ref_names:
                    raise ValueError("timestep node order mismatch")
                common = sorted(set(actual) & set(reference))
                errors = [actual[when][index] - reference[when][index]
                          for when in common for index in range(len(names))]
                hot_errors = [max(actual[when][index] for index in hbf_indices) -
                              max(reference[when][index] for index in hbf_indices)
                              for when in common]
                actual_hot = [(when, max(actual[when][index] for index in hbf_indices))
                              for when in sorted(actual)]
                ref_hot = [(when, max(reference[when][index] for index in hbf_indices))
                           for when in sorted(reference)]
                crossings = {}
                for threshold in THERMAL_THRESHOLDS_C:
                    left = first_crossing(actual_hot, threshold)
                    right = first_crossing(ref_hot, threshold)
                    crossings[f"{threshold:g}c"] = {
                        "case_time_ns": left, "reference_time_ns": right,
                        "absolute_error_ns": (abs(left - right)
                                              if left is not None and right is not None else None),
                        "classification_match": (left is None) == (right is None),
                    }
                maximum = max(abs(value) for value in errors)
                hotspot_rmse = math.sqrt(sum(value * value for value in hot_errors) /
                                         len(hot_errors))
                comparisons.append({
                    "height": height, "period_ms": period_ms,
                    "common_samples": len(common),
                    "all_node_rmse_c": math.sqrt(sum(value * value for value in errors) /
                                                  len(errors)),
                    "maximum_all_node_error_c": maximum,
                    "hotspot_rmse_c": hotspot_rmse,
                    "threshold_crossing": crossings,
                    "thermal_fidelity_accepted": (maximum <= 1.0 and hotspot_rmse <= 1.0 and
                        all(value["classification_match"] for value in crossings.values())),
                })
        summary["comparisons_vs_10ms"] = comparisons
        summary["performance_50ms_thermal_fidelity"] = all(
            item["thermal_fidelity_accepted"] for item in comparisons
            if item["period_ms"] == 50)
    write_json(root / "summary.json", summary)


def load_aligned_temperatures(path: Path) -> tuple[list[str], dict[int, list[float]]]:
    with path.open("r", encoding="utf-8", newline="") as stream:
        rows = list(csv.reader(stream))
    return rows[0][1:], {int(row[0]): [float(value) for value in row[1:]]
                         for row in rows[1:]}


def first_crossing(series: list[tuple[int, float]], threshold: float) -> int | None:
    return next((when for when, value in series if value >= threshold), None)


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    prepare = sub.add_parser("prepare-study")
    prepare.add_argument("--phase1-golden-root", type=Path, required=True)
    prepare.add_argument("--output", type=Path, required=True)
    cap_sweep = sub.add_parser("prepare-cap-sweep")
    cap_sweep.add_argument("--phase1-golden-root", type=Path, required=True)
    cap_sweep.add_argument("--output", type=Path, required=True)
    golden = sub.add_parser("prepare-golden")
    golden.add_argument("--phase1-golden-root", type=Path, required=True)
    golden.add_argument("--output", type=Path, required=True)
    golden.add_argument("--mesh-um", type=int, required=True)
    timestep = sub.add_parser("prepare-timestep")
    timestep.add_argument("--phase1-golden-root", type=Path, required=True)
    timestep.add_argument("--output", type=Path, required=True)
    timestep.add_argument("--mesh-um", type=int, required=True)
    run = sub.add_parser("run")
    run.add_argument("--solver", type=Path, required=True)
    run.add_argument("--root", type=Path, required=True)
    run.add_argument("--jobs", type=int, default=4)
    run.add_argument("--timeout-seconds", type=int, default=7200)
    run.add_argument("--case-regex")
    run.add_argument("--retry-failed", action="store_true")
    run.add_argument("--force-rerun", action="store_true")
    run.add_argument("--solver-sha256", default=SOLVER_SHA256)
    summarize = sub.add_parser("summarize")
    summarize.add_argument("--root", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "prepare-study":
        prepare_study(args.phase1_golden_root.resolve(), args.output.resolve())
    elif args.command == "prepare-cap-sweep":
        prepare_cap_sweep(args.phase1_golden_root.resolve(),
                          args.output.resolve())
    elif args.command == "prepare-golden":
        prepare_golden(args.phase1_golden_root.resolve(), args.output.resolve(), args.mesh_um)
    elif args.command == "prepare-timestep":
        prepare_timestep(args.phase1_golden_root.resolve(), args.output.resolve(), args.mesh_um)
    elif args.command == "run":
        run_campaign(args.solver, args.root.resolve(), args.jobs, args.timeout_seconds,
                     args.case_regex, args.retry_failed, args.solver_sha256,
                     args.force_rerun)
    else:
        summarize_campaign(args.root.resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
