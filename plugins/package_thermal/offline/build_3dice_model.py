#!/usr/bin/env python3
"""Build deterministic 3D-ICE sweep cases without vendoring or invoking it."""

from __future__ import annotations

import argparse
import csv
import pathlib
import re
import shutil
import sys

from _common import (OfflineError, finite_number, load_json, require_keys,
                     sha256_file, validate_evidence_label, write_json,
                     write_power_csv)


TOKEN = re.compile(r"\{\{POWER_VALUES:([^{}]+)\}\}")
SHA256 = re.compile(r"[0-9a-fA-F]{64}")
PROVENANCE_FIELDS = ["parameter", "value", "unit", "class", "source",
                     "locator", "dataset_sha256", "calibration_sha256",
                     "note"]


def arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate unit-step and held-out external 3D-ICE 4.0 cases")
    parser.add_argument("--package-profile", type=pathlib.Path, required=True)
    parser.add_argument("--geometry", type=pathlib.Path, required=True,
                        help="audited geometry/material/mesh provenance JSON")
    parser.add_argument("--template-dir", type=pathlib.Path, required=True,
                        help="vetted 3D-ICE case templates; .in files may use POWER_VALUES tokens")
    parser.add_argument("--output", type=pathlib.Path, required=True)
    parser.add_argument("--samples", type=int, default=64)
    parser.add_argument("--step-watts", type=float, default=1.0)
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def pattern(kind: str, active: int, count: int, nodes: int) -> list[list[float]]:
    rows: list[list[float]] = []
    for index in range(count):
        value = [0.0] * nodes
        if kind == "unit_step":
            if index >= count // 4:
                value[active] = 1.0
        elif kind == "square_wave":
            value[active] = 1.0 if (index // 8) % 2 else 0.0
        elif kind == "burst":
            value[active] = 1.0 if count // 3 <= index < count // 3 + 4 else 0.0
        elif kind == "mixed_gpu_hbm_hbf":
            value[0] = 0.8 if (index // 5) % 2 else 0.2
            if nodes > 1:
                value[1] = 0.4
            value[active] = 0.6 if index >= count // 2 else 0.1
        elif kind == "write_heavy_hbf":
            value[active] = 0.9 if index % 5 else 0.3
        elif kind == "read_heavy_hbf":
            value[active] = 0.35 if index % 7 else 0.6
        else:
            raise OfflineError(f"unknown trace kind {kind}")
        rows.append(value)
    return rows


def validate_provenance_csv(path: pathlib.Path) -> None:
    try:
        stream = path.open("r", encoding="utf-8", newline="")
    except OSError as error:
        raise OfflineError(f"failed to open parameter provenance: {error}") from error
    with stream:
        reader = csv.DictReader(stream)
        if reader.fieldnames != PROVENANCE_FIELDS:
            raise OfflineError("parameter provenance CSV header is invalid")
        rows = list(reader)
    if not rows:
        raise OfflineError("parameter provenance CSV is empty")
    for line, row in enumerate(rows, start=2):
        if None in row or any(row[field] is None for field in PROVENANCE_FIELDS):
            raise OfflineError(f"parameter provenance row width mismatch at line {line}")
        for field in ("parameter", "value", "unit", "class", "source", "locator"):
            if not row[field].strip():
                raise OfflineError(
                    f"parameter provenance {field} is empty at line {line}")
        evidence = row["class"].strip()
        if evidence not in {"S", "L", "C", "M"}:
            raise OfflineError(f"invalid provenance class at line {line}")
        dataset = row["dataset_sha256"].strip()
        calibration = row["calibration_sha256"].strip()
        if dataset and SHA256.fullmatch(dataset) is None:
            raise OfflineError(f"invalid dataset SHA-256 at line {line}")
        if calibration and SHA256.fullmatch(calibration) is None:
            raise OfflineError(f"invalid calibration SHA-256 at line {line}")
        if evidence == "C" and bool(dataset) != bool(calibration):
            raise OfflineError(
                f"calibrated provenance needs both SHA-256 hashes at line {line}")
        if evidence == "M" and not dataset:
            raise OfflineError(
                f"measured provenance needs a dataset SHA-256 at line {line}")


def render_templates(source: pathlib.Path, target: pathlib.Path,
                     names: list[str], values: list[list[float]]) -> str:
    stack_files: list[str] = []
    for item in sorted(source.rglob("*")):
        if item.is_dir():
            continue
        relative = item.relative_to(source)
        destination = target / relative
        if destination.suffix == ".in":
            destination = destination.with_suffix("")
            text = item.read_text(encoding="utf-8")
            unknown = sorted(set(TOKEN.findall(text)) - set(names))
            if unknown:
                raise OfflineError(f"template {item} contains unknown power nodes {unknown}")
            index = {name: offset for offset, name in enumerate(names)}
            text = TOKEN.sub(
                lambda match: ", ".join(
                    format(row[index[match.group(1)]], ".17g") for row in values),
                text)
            if TOKEN.search(text):
                raise OfflineError(f"unresolved power token in {item}")
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(text, encoding="utf-8")
        else:
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(item, destination)
        if destination.suffix == ".stk":
            stack_files.append(destination.relative_to(target).as_posix())
    if len(stack_files) != 1:
        raise OfflineError("each rendered case must contain exactly one .stk file")
    return stack_files[0]


def main() -> int:
    args = arguments()
    if args.samples < 16:
        raise OfflineError("--samples must be at least 16")
    step_watts = finite_number(args.step_watts, "--step-watts", nonnegative=True)
    if step_watts <= 0.0:
        raise OfflineError("--step-watts must be positive")
    profile = load_json(args.package_profile)
    geometry = load_json(args.geometry)
    require_keys(geometry,
                 {"schema_version", "evidence_label", "three_d_ice_version",
                  "three_d_ice_commit", "material_config", "solver_settings",
                  "grid_settings", "geometry", "parameter_provenance_csv"},
                 "geometry manifest")
    if geometry["schema_version"] != 1 or geometry["three_d_ice_version"] != "4.0":
        raise OfflineError("geometry manifest must target 3D-ICE 4.0 schema 1")
    evidence_label = validate_evidence_label(
        geometry["evidence_label"], "geometry manifest evidence_label")
    if not args.template_dir.is_dir():
        raise OfflineError("--template-dir does not exist")
    bound_nodes = set()
    for template in args.template_dir.rglob("*.in"):
        bound_nodes.update(TOKEN.findall(template.read_text(encoding="utf-8")))
    provenance_csv = (args.geometry.parent /
                      geometry["parameter_provenance_csv"]).resolve()
    if not provenance_csv.is_file():
        raise OfflineError("geometry parameter_provenance_csv is missing")
    validate_provenance_csv(provenance_csv)
    nodes = profile.get("topology", {}).get("node_names")
    if not isinstance(nodes, list) or not nodes or len(set(nodes)) != len(nodes):
        raise OfflineError("package profile topology node_names are invalid")
    if bound_nodes != set(nodes):
        raise OfflineError(
            "template POWER_VALUES bindings must cover exactly every topology node; "
            f"missing={sorted(set(nodes) - bound_nodes)}, "
            f"unknown={sorted(bound_nodes - set(nodes))}")
    period_value = profile.get("bin_width_ns", {}).get("value")
    period = int(finite_number(period_value, "bin_width_ns", nonnegative=True))
    if period <= 0 or float(period) != float(period_value):
        raise OfflineError("bin_width_ns must be an exact positive integer")
    hbf = [index for index, name in enumerate(nodes) if name.startswith("hbf.")]
    if not hbf:
        raise OfflineError("package profile has no hbf.* power nodes")
    definitions: list[tuple[str, str, int]] = []
    definitions.extend((f"unit-{index:03d}", "unit_step", index)
                       for index in range(len(nodes)))
    definitions.extend([
        ("held-square-wave", "square_wave", hbf[0]),
        ("held-burst", "burst", hbf[-1]),
        ("held-mixed", "mixed_gpu_hbm_hbf", hbf[len(hbf) // 2]),
        ("held-write-heavy", "write_heavy_hbf", hbf[-1]),
        ("held-read-heavy", "read_heavy_hbf", hbf[0]),
    ])
    plan = {"schema_version": 1, "evidence_label": evidence_label,
            "three_d_ice_version": "4.0",
            "three_d_ice_commit": geometry["three_d_ice_commit"],
            "package_profile_sha256": sha256_file(args.package_profile),
            "geometry_sha256": sha256_file(args.geometry),
            "parameter_provenance_sha256": sha256_file(provenance_csv),
            "template_sha256": sorted(
                (path.relative_to(args.template_dir).as_posix(), sha256_file(path))
                for path in args.template_dir.rglob("*") if path.is_file()),
            "sample_period_ns": period, "samples": args.samples,
            "source_ordering": nodes, "observation_ordering": nodes,
            "material_config": geometry["material_config"],
            "solver_settings": geometry["solver_settings"],
            "grid_settings": geometry["grid_settings"], "cases": []}
    if args.dry_run:
        print(f"would generate {len(definitions)} cases in {args.output}")
        return 0
    args.output.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(provenance_csv, args.output / "parameter-provenance.csv")
    for trace_id, kind, active in definitions:
        values = pattern(kind, active, args.samples, len(nodes))
        if kind == "unit_step":
            values = [[item * step_watts for item in row] for row in values]
        case_dir = args.output / "cases" / trace_id
        stack = render_templates(args.template_dir, case_dir, nodes, values)
        power_path = args.output / "power_traces" / f"{trace_id}.csv"
        write_power_csv(power_path, nodes,
                        ((index * period, row) for index, row in enumerate(values)))
        case = {"schema_version": 1, "trace_id": trace_id,
                "trace_kind": kind, "active_source": nodes[active],
                "step_watts": step_watts if kind == "unit_step" else 0.0,
                "evidence_label": evidence_label,
                "stack_file": stack,
                "power_trace": power_path.relative_to(args.output).as_posix(),
                "sample_period_ns": period, "input_names": nodes,
                "output_names": nodes}
        write_json(case_dir / "case.json", case)
        plan["cases"].append(
            (case_dir / "case.json").relative_to(args.output).as_posix())
    write_json(args.output / "sweep-plan.json", plan)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except OfflineError as error:
        print(f"build_3dice_model.py: {error}", file=sys.stderr)
        raise SystemExit(2)
