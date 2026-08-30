#!/usr/bin/env python3
"""Materialize Phase-II golden datasets and fit/validate one ROM per height."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from pathlib import Path
import subprocess
import sys
from typing import Any


REQUIRED_HELD = {"square_wave", "burst", "mixed_gpu_hbm_hbf",
                 "write_heavy_hbf", "read_heavy_hbf"}


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


def read_csv(path: Path, expected: list[str]) -> list[tuple[int, list[float]]]:
    with path.open("r", encoding="utf-8", newline="") as stream:
        rows = list(csv.reader(stream))
    if not rows or rows[0] != ["time_ns", *expected]:
        raise ValueError(f"node order mismatch in {path}")
    result = [(int(row[0]), [float(value) for value in row[1:]]) for row in rows[1:]]
    if len(result) < 2:
        raise ValueError(f"too few samples in {path}")
    return result


def run(command: list[str], cwd: Path) -> None:
    completed = subprocess.run(command, cwd=cwd, check=False)
    if completed.returncode:
        raise RuntimeError(f"command failed ({completed.returncode}): {' '.join(command)}")


def materialize_case(case_path: Path, output: Path,
                     solver_identity: str) -> tuple[int, Path, dict[str, Any]]:
    case = json.loads(case_path.read_text(encoding="utf-8"))
    case_dir = case_path.parent
    result_path = case_dir / "run-result.json"
    result = json.loads(result_path.read_text(encoding="utf-8"))
    if result.get("returncode") != 0:
        raise ValueError(f"golden case is not successful: {case['case_id']}")
    powers = read_csv(case_dir / case["power_trace"], case["input_names"])
    temperatures = read_csv(case_dir / "temperatures.csv", case["output_names"])
    if [item[0] for item in powers] != [item[0] for item in temperatures]:
        raise ValueError(f"power/temperature timestamps differ: {case['case_id']}")
    period = int(case["sample_period_ns"])
    if any(right[0] - left[0] != period for left, right in zip(powers, powers[1:])):
        raise ValueError(f"non-uniform timestamps: {case['case_id']}")
    dataset = {
        "schema_version": 1,
        "trace_id": case["trace_id"],
        "trace_kind": case["trace_kind"],
        "sample_period_ns": period,
        "input_names": case["input_names"],
        "output_names": case["output_names"],
        "samples": [
            {"time_ns": when, "power_w": power, "temperature_c": temperature}
            for (when, power), (_, temperature) in zip(powers, temperatures)],
        "provenance": {
            "evidence_label": "literature_parameterized",
            "case_sha256": sha256(case_path),
            "power_sha256": sha256(case_dir / case["power_trace"]),
            "temperature_sha256": sha256(case_dir / "temperatures.csv"),
            "run_result_sha256": sha256(result_path),
            "solver_identity": solver_identity,
            "phase1_source_case_sha256": case["phase1_source_case_sha256"],
            "power_scaling": case["power_scaling"],
        },
    }
    target = output / "datasets" / f"{case['case_id']}.json"
    write_json(target, dataset)
    return int(case["geometry"]["stack_height"]), target, case


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--campaign", type=Path, required=True)
    parser.add_argument("--offline-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--solver-identity", required=True)
    parser.add_argument("--python", default=sys.executable)
    parser.add_argument("--external-blocks", type=int, default=512)
    parser.add_argument("--external-rank", type=int, default=192)
    parser.add_argument("--hbf-blocks", type=int, default=64)
    parser.add_argument("--hbf-rank", type=int, default=32)
    args = parser.parse_args()
    campaign = args.campaign.resolve()
    offline = args.offline_dir.resolve()
    output = args.output.resolve()
    if output.exists():
        raise ValueError(f"refusing to overwrite {output}")
    output.mkdir(parents=True)
    plan_path = campaign / "campaign-plan.json"
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    if plan.get("kind") != "phase2_10ms_golden" or plan.get("sample_period_ns") != 10_000_000:
        raise ValueError("campaign is not a Phase-II 10 ms golden")

    by_height: dict[int, list[tuple[Path, dict[str, Any]]]] = {8: [], 16: []}
    for relative in plan["cases"]:
        height, dataset, case = materialize_case(campaign / relative, output,
                                                  args.solver_identity)
        by_height[height].append((dataset, case))

    reports = []
    for height, entries in by_height.items():
        training = [(path, case) for path, case in entries if case["split"] == "training"]
        held = [(path, case) for path, case in entries if case["split"] == "held_out"]
        if {case["trace_kind"] for _, case in held} != REQUIRED_HELD:
            raise ValueError(f"{height}Hi held-out trace kinds are incomplete")
        geometry_payload = {
            "geometry": entries[0][1]["geometry"],
            "boundary": entries[0][1]["boundary"],
            "mesh": entries[0][1]["mesh"],
            "materials": entries[0][1]["materials"],
            "campaign_plan_sha256": sha256(plan_path),
        }
        geometry_path = output / f"geometry-{height}hi.json"
        write_json(geometry_path, geometry_payload)
        geometry_sha = sha256(geometry_path)
        model = output / f"package-rom-g1-{height}hi-10ms.json"
        fit = [args.python, str(offline / "fit_era_model.py")]
        for path, _ in training:
            fit.extend(["--training", str(path)])
        for path, _ in held:
            fit.extend(["--held-out", str(path)])
        fit.extend(["--model-id", f"package-rom-g1-{height}hi-10ms",
                    "--external-blocks", str(args.external_blocks),
                    "--external-rank", str(args.external_rank),
                    "--hbf-blocks", str(args.hbf_blocks),
                    "--hbf-rank", str(args.hbf_rank),
                    "--geometry-sha256", geometry_sha,
                    "--solver-identity", args.solver_identity,
                    "--output", str(model)])
        run(fit, offline)

        validation = output / f"rom-validation-{height}hi.json"
        validate = [args.python, str(offline / "validate_reduced_model.py"),
                    "--model", str(model), "--output", str(validation),
                    "--max-rmse-c", "1.0",
                    "--max-steady-hotspot-error-c", "1.0",
                    "--max-absolute-error-c", "1.0",
                    "--threshold-c", "100", "--threshold-c", "105"]
        for path, _ in held:
            validate.extend(["--held-out", str(path)])
        run(validate, offline)

        mixed = next(path for path, case in held
                     if case["trace_kind"] == "mixed_gpu_hbm_hbf")
        decomposition = output / f"rom-decomposition-{height}hi.json"
        run([args.python, str(offline / "decompose_rom.py"),
             "--model", str(model), "--dataset", str(mixed),
             "--initial-temperature-c", "30", "--output", str(decomposition)],
            offline)
        validation_value = json.loads(validation.read_text(encoding="utf-8"))
        decomposition_value = json.loads(decomposition.read_text(encoding="utf-8"))
        fit_metadata = Path(str(model) + ".fit.json")
        reports.append({
            "height": height, "training_count": len(training),
            "held_out_count": len(held), "geometry_sha256": geometry_sha,
            "model": model.name, "model_sha256": sha256(model),
            "validation": validation.name,
            "validation_sha256": sha256(validation),
            "fit_metadata": fit_metadata.name,
            "fit_metadata_sha256": sha256(fit_metadata),
            "accepted": validation_value["accepted"],
            "validation_aggregate": validation_value["aggregate"],
            "decomposition": decomposition.name,
            "decomposition_sha256": sha256(decomposition),
            "superposition_accepted": decomposition_value["accepted"],
        })

    write_json(output / "phase2-rom-summary.json", {
        "schema_version": 1, "campaign_plan_sha256": sha256(plan_path),
        "method": "grouped_era",
        "group_configuration": {
            "external_blocks": args.external_blocks,
            "external_rank": args.external_rank,
            "hbf_blocks": args.hbf_blocks,
            "hbf_rank": args.hbf_rank,
        },
        "solver_identity": args.solver_identity,
        "offline_tool_hashes": {
            name: sha256(offline / name) for name in (
                "fit_era_model.py", "validate_reduced_model.py", "decompose_rom.py",
                "_common.py")},
        "accepted": all(item["accepted"] and item["superposition_accepted"]
                        for item in reports),
        "heights": reports,
    })
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
