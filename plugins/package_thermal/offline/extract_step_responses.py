#!/usr/bin/env python3
"""Normalize power and 3D-ICE temperature CSVs into audited datasets."""

from __future__ import annotations

import argparse
import csv
import pathlib
import sys

from _common import (EVIDENCE_LABELS, OfflineError, finite_number, load_json,
                     portable_relative_path, select_evidence_label, sha256_file,
                     write_json)


def arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Extract one normalized golden trace from external 3D-ICE output")
    parser.add_argument("--case", type=pathlib.Path, required=True)
    parser.add_argument("--power", type=pathlib.Path, required=True)
    parser.add_argument("--temperatures", type=pathlib.Path, required=True,
                        help="CSV with time_ns then output-node columns")
    parser.add_argument("--run-manifest", type=pathlib.Path, required=True)
    parser.add_argument("--output", type=pathlib.Path, required=True)
    parser.add_argument(
        "--evidence-label", choices=EVIDENCE_LABELS,
        help="optional evidence downgrade; defaults to the case evidence label")
    return parser.parse_args()


def read_csv(path: pathlib.Path, names: list[str]) -> list[tuple[int, list[float]]]:
    try:
        stream = path.open("r", encoding="utf-8", newline="")
    except OSError as error:
        raise OfflineError(f"failed to open CSV {path}: {error}") from error
    with stream:
        reader = csv.reader(stream)
        try:
            header = next(reader)
        except StopIteration as error:
            raise OfflineError(f"empty CSV: {path}") from error
        if header != ["time_ns", *names]:
            raise OfflineError(f"CSV header does not match configured node order: {path}")
        result = []
        previous = None
        for line, row in enumerate(reader, start=2):
            if len(row) != len(header):
                raise OfflineError(f"CSV row width mismatch at {path}:{line}")
            try:
                timestamp = int(row[0])
            except ValueError as error:
                raise OfflineError(f"invalid timestamp at {path}:{line}") from error
            if timestamp < 0 or (previous is not None and timestamp <= previous):
                raise OfflineError(f"non-monotonic timestamp at {path}:{line}")
            previous = timestamp
            result.append((timestamp, [finite_number(float(item),
                                                    f"{path}:{line}")
                                       for item in row[1:]]))
    if len(result) < 2:
        raise OfflineError(f"CSV needs at least two rows: {path}")
    return result


def source_evidence_labels(case_path: pathlib.Path, case: dict) -> list[str]:
    labels = []
    if "evidence_label" in case:
        labels.append(case["evidence_label"])

    resolved_case = case_path.resolve()
    cases_dir = resolved_case.parent.parent
    if cases_dir.name == "cases":
        sweep_root = cases_dir.parent
        plan_path = sweep_root / "sweep-plan.json"
        if plan_path.is_file():
            plan = load_json(plan_path)
            if plan.get("schema_version") != 1:
                raise OfflineError("unsupported parent sweep-plan schema")
            declared = [
                portable_relative_path(value, "sweep-plan case path").as_posix()
                for value in plan.get("cases", [])
            ]
            relative = resolved_case.relative_to(sweep_root).as_posix()
            if relative not in declared:
                raise OfflineError("case is not declared by its parent sweep plan")
            labels.append(plan.get("evidence_label"))
    if not labels:
        raise OfflineError(
            "case has no evidence label and no declaring parent sweep plan")
    return labels


def main() -> int:
    args = arguments()
    case = load_json(args.case)
    required = {"schema_version", "trace_id", "trace_kind", "active_source",
                "stack_file", "power_trace", "step_watts", "sample_period_ns",
                "input_names", "output_names"}
    actual = set(case)
    allowed = required | {"evidence_label"}
    if not required.issubset(actual) or not actual.issubset(allowed):
        raise OfflineError(
            f"case fields mismatch; missing={sorted(required - actual)}, "
            f"unknown={sorted(actual - allowed)}")
    if case["schema_version"] != 1:
        raise OfflineError("unsupported case schema")
    evidence_label = select_evidence_label(
        args.evidence_label, source_evidence_labels(args.case, case),
        "extracted dataset")
    run = load_json(args.run_manifest)
    if run.get("schema_version") != 1 or not run.get("runs"):
        raise OfflineError("invalid 3D-ICE run manifest")
    if not any(item.get("trace_id") == case["trace_id"] and
               item.get("returncode") == 0 for item in run["runs"]):
        raise OfflineError("run manifest has no successful matching trace")
    powers = read_csv(args.power, case["input_names"])
    temperatures = read_csv(args.temperatures, case["output_names"])
    if [item[0] for item in powers] != [item[0] for item in temperatures]:
        raise OfflineError("power and temperature timestamps do not match")
    period = case["sample_period_ns"]
    if any(powers[index][0] - powers[index - 1][0] != period
           for index in range(1, len(powers))):
        raise OfflineError("CSV timestamps do not match case sample period")
    samples = []
    for (timestamp, power), (_, temperature) in zip(powers, temperatures):
        if any(item < 0.0 for item in power):
            raise OfflineError("power input contains a negative value")
        if any(item < -273.15 for item in temperature):
            raise OfflineError("temperature is below absolute zero")
        samples.append({"time_ns": timestamp, "power_w": power,
                        "temperature_c": temperature})
    write_json(args.output,
               {"schema_version": 1, "trace_id": case["trace_id"],
                "trace_kind": case["trace_kind"],
                "sample_period_ns": period,
                "input_names": case["input_names"],
                "output_names": case["output_names"], "samples": samples,
                "provenance": {
                    "evidence_label": evidence_label,
                    "active_source": case["active_source"],
                    "step_watts": case["step_watts"],
                    "case_sha256": sha256_file(args.case),
                    "power_sha256": sha256_file(args.power),
                    "temperature_sha256": sha256_file(args.temperatures),
                    "run_manifest_sha256": sha256_file(args.run_manifest)}})
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OfflineError, ValueError) as error:
        print(f"extract_step_responses.py: {error}", file=sys.stderr)
        raise SystemExit(2)
