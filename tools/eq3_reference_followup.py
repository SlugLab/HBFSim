#!/usr/bin/env python3
"""Prepare, approval-gate, run, and compare the fixed P2 reference follow-up.

No command runs by default. ``describe`` only prints a deterministic plan.
``run-approved`` is the sole workload path and validates the existing EQ3 gate
before creating its output root. ``analyze`` reads completed immutable outputs.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import pathlib
import resource
import subprocess
import time

import eq3_experiment_gate
import eq3_reference


STEP_S = 0.025
FINE_CELL_M = 0.0005
TIMEOUT_S = 300
TOTAL_CPU_S = 1200.0
ADDRESS_SPACE_BYTES = 4 * 1024**3
DISK_BYTES = 1024**3
RUN_SPECS = (
    ("eq3-p2-ref-train-mesh1mm-dt25ms", "train", "original", 0.001),
    ("eq3-p2-ref-train-mesh0p5mm-dt25ms", "train", "derived_fine", FINE_CELL_M),
    ("eq3-p2-ref-heldout-mesh1mm-dt25ms", "heldout", "original", 0.001),
    ("eq3-p2-ref-heldout-mesh0p5mm-dt25ms", "heldout", "derived_fine", FINE_CELL_M),
)
WORKFLOW_COMMANDS = {
    "approved_run": (
        "python3 tools/eq3_reference_followup.py run-approved "
        "--case configs/eq3_thermal/reference/package_scenario.json "
        "--materials configs/eq3_thermal/reference/materials.json "
        "--power configs/eq3_thermal/reference/power_traces.json "
        "--generator tools/eq3_reference.py --solver ${SOLVER_PATH} "
        "--plan ${PLAN_PATH} --manifest ${MANIFEST_PATH} --approval ${APPROVAL_PATH} "
        "--root . --output ${NEW_OUTPUT_ROOT}"
    ),
    "analysis": (
        "python3 tools/eq3_reference_followup.py analyze --plan ${PLAN_PATH} "
        "--power configs/eq3_thermal/reference/power_traces.json "
        "--run-root ${COMPLETED_OUTPUT_ROOT} --output ${NEW_ANALYSIS_JSON}"
    ),
}


def sha256_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def json_bytes(value: dict) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True) + "\n").encode("utf-8")


def fine_case(original: dict) -> dict:
    result = copy.deepcopy(original)
    if result["mesh_cell_m"].get("fine") != 0.001:
        raise ValueError("original package case fine mesh is not the expected 1 mm")
    result["mesh_cell_m"]["fine"] = FINE_CELL_M
    return result


def build_plan(case_path: pathlib.Path, materials_path: pathlib.Path,
               power_path: pathlib.Path, generator_path: pathlib.Path,
               solver_path: pathlib.Path) -> tuple[dict, bytes]:
    paths = (case_path, materials_path, power_path, generator_path, solver_path)
    if any(not path.is_file() for path in paths):
        raise FileNotFoundError("all plan inputs and the solver must be existing files")
    original = eq3_reference.load(case_path)
    materials = eq3_reference.load(materials_path)
    power = eq3_reference.load(power_path)
    eq3_reference.validate_case(original, materials, power)
    derived_bytes = json_bytes(fine_case(original))
    plan = {
        "schema_version": "eq3-reference-followup-plan-v1",
        "approval_status": "PENDING_USER_APPROVAL",
        "purpose": "Characterize 1 mm versus 0.5 mm spatial-reference uncertainty at fixed dt=0.025 s before any RC structural change.",
        "non_goals": ["RC or ROM fitting", "GPU work", "physical calibration", "paper claim"],
        "inputs": {
            "package_scenario_sha256": eq3_reference.sha256(case_path),
            "materials_sha256": eq3_reference.sha256(materials_path),
            "power_traces_sha256": eq3_reference.sha256(power_path),
            "generator_sha256": eq3_reference.sha256(generator_path),
            "derived_fine_case_sha256": sha256_bytes(derived_bytes),
            "solver_binary_sha256": eq3_reference.sha256(solver_path),
        },
        "resource_limits": {
            "runs": 4, "executions_per_run": 1, "serial": True,
            "omp_threads": 1, "blas_threads": 1, "timeout_s_per_run": TIMEOUT_S,
            "total_cpu_seconds": TOTAL_CPU_S, "address_space_bytes_per_run": ADDRESS_SPACE_BYTES,
            "total_output_bytes": DISK_BYTES, "gpu_compute_minutes": 0,
        },
        "runs": [
            {"run_id": run_id, "trace": trace, "case_variant": variant,
             "mesh_cell_m": cell, "mesh_key": "fine", "step_s": STEP_S,
             "execution_index": 1}
            for run_id, trace, variant, cell in RUN_SPECS
        ],
        "analysis": {
            "pairs": [
                {"trace": trace,
                 "mesh1mm_run_id": f"eq3-p2-ref-{trace}-mesh1mm-dt25ms",
                 "mesh0p5mm_run_id": f"eq3-p2-ref-{trace}-mesh0p5mm-dt25ms"}
                for trace in ("train", "heldout")
            ],
            "metrics": ["per-sensor MAE/max delta K", "grid-hotspot MAE/max delta K",
                        "maximum hotspot-position displacement m"],
            "acceptance": "CHARACTERIZATION_ONLY_NO_PASS_THRESHOLD",
        },
        "workflow_commands": WORKFLOW_COMMANDS,
    }
    return plan, derived_bytes


def validate_fixed_plan(plan: dict) -> None:
    if plan.get("schema_version") != "eq3-reference-followup-plan-v1":
        raise ValueError("unsupported follow-up plan schema")
    expected = [(a, b, c, d, "fine", STEP_S, 1) for a, b, c, d in RUN_SPECS]
    observed = [(r.get("run_id"), r.get("trace"), r.get("case_variant"),
                 r.get("mesh_cell_m"), r.get("mesh_key"), r.get("step_s"),
                 r.get("execution_index")) for r in plan.get("runs", [])]
    if observed != expected:
        raise ValueError("plan is not the exact fixed four-run matrix")
    limits = plan.get("resource_limits", {})
    required = {"runs": 4, "executions_per_run": 1, "serial": True,
                "omp_threads": 1, "blas_threads": 1, "timeout_s_per_run": TIMEOUT_S,
                "total_cpu_seconds": TOTAL_CPU_S,
                "address_space_bytes_per_run": ADDRESS_SPACE_BYTES,
                "total_output_bytes": DISK_BYTES, "gpu_compute_minutes": 0}
    if limits != required:
        raise ValueError("plan resource limits differ from the fixed approved envelope")
    if plan.get("workflow_commands") != WORKFLOW_COMMANDS:
        raise ValueError("plan command interface differs from the fixed workflow")


def _manifest_binding(manifest: dict, plan_path: pathlib.Path, plan: dict) -> None:
    scientific = manifest["scientific_config"]
    numerics = scientific["time_and_numerics"]
    scan = scientific["scan_matrix_and_repetitions"]
    if numerics.get("plan_sha256") != eq3_reference.sha256(plan_path):
        raise ValueError("manifest does not bind the supplied plan bytes")
    if numerics.get("solver_binary_sha256") != plan["inputs"]["solver_binary_sha256"]:
        raise ValueError("manifest and plan solver hashes differ")
    if scan.get("run_ids") != [item["run_id"] for item in plan["runs"]]:
        raise ValueError("manifest does not bind the exact four-run order")


def _limit_child() -> None:
    resource.setrlimit(resource.RLIMIT_AS, (ADDRESS_SPACE_BYTES, ADDRESS_SPACE_BYTES))
    resource.setrlimit(resource.RLIMIT_FSIZE, (DISK_BYTES, DISK_BYTES))


def _tree_size(path: pathlib.Path) -> int:
    return sum(item.stat().st_size for item in path.rglob("*") if item.is_file())


def run_approved(args: argparse.Namespace) -> None:
    manifest = eq3_experiment_gate.load_json(args.manifest)
    approval = eq3_experiment_gate.load_json(args.approval)
    plan = eq3_reference.load(args.plan)
    validate_fixed_plan(plan)
    _manifest_binding(manifest, args.plan, plan)
    # Production gate comes before mkdir, generated cases, logs, or subprocesses.
    eq3_experiment_gate.validate_gate(manifest, approval, args.root, args.approval)
    solver_hash = eq3_reference.sha256(args.solver)
    if solver_hash != plan["inputs"]["solver_binary_sha256"]:
        raise ValueError("actual solver binary differs from the manifest-bound digest")
    rebuilt, derived_bytes = build_plan(args.case, args.materials, args.power,
                                        args.generator, args.solver)
    if rebuilt != plan:
        raise ValueError("current inputs do not deterministically reproduce the approved plan")
    args.output.mkdir(parents=True, exist_ok=False)
    case_dir = args.output / "cases"
    case_dir.mkdir()
    derived_path = case_dir / "package_scenario_mesh0p5mm.json"
    derived_path.write_bytes(derived_bytes)
    cpu_total = 0.0
    environment = os.environ.copy()
    environment.update({"OMP_NUM_THREADS": "1", "OPENBLAS_NUM_THREADS": "1",
                        "MKL_NUM_THREADS": "1", "VECLIB_MAXIMUM_THREADS": "1"})
    for spec in plan["runs"]:
        if cpu_total >= TOTAL_CPU_S:
            raise RuntimeError("aggregate CPU budget exhausted before next run")
        run_dir = args.output / spec["run_id"]
        case = args.case if spec["case_variant"] == "original" else derived_path
        gen_args = argparse.Namespace(case=case, materials=args.materials, power=args.power,
                                      trace=spec["trace"], mesh="fine", step_s=STEP_S,
                                      output=run_dir)
        eq3_reference.generate(gen_args)
        before = resource.getrusage(resource.RUSAGE_CHILDREN)
        started = time.monotonic()
        with (run_dir / "reference_stdout.log").open("wb") as stdout, \
             (run_dir / "reference_stderr.log").open("wb") as stderr:
            completed = subprocess.run(
                [str(args.solver.resolve()), "package.stk"], cwd=run_dir,
                env=environment, stdout=stdout, stderr=stderr, timeout=TIMEOUT_S,
                check=False, preexec_fn=_limit_child,
            )
        after = resource.getrusage(resource.RUSAGE_CHILDREN)
        cpu = (after.ru_utime - before.ru_utime) + (after.ru_stime - before.ru_stime)
        cpu_total += cpu
        outputs = sorted(list(run_dir.glob("temperature_*.txt")) +
                         list(run_dir.glob("maximum_*.txt")) +
                         [path for path in (run_dir / "source_map.txt", run_dir / "xaxis.txt",
                                            run_dir / "yaxis.txt") if path.is_file()])
        receipt = {"run_id": spec["run_id"], "kind": "reference",
                   "command": ["SOLVER_BINARY", "package.stk"],
                   "exit_code": completed.returncode, "wall_seconds": time.monotonic() - started,
                   "cpu_seconds": cpu, "aggregate_cpu_seconds": cpu_total,
                   "timeout_s": TIMEOUT_S, "address_space_bytes": ADDRESS_SPACE_BYTES,
                   "threads": 1, "solver_sha256": solver_hash,
                   "outputs_sha256": {path.name: eq3_reference.sha256(path) for path in outputs}}
        (run_dir / "reference_receipt.json").write_text(json.dumps(receipt, indent=2) + "\n")
        if completed.returncode:
            raise RuntimeError(f"reference run failed: {spec['run_id']}")
        if _tree_size(args.output) > DISK_BYTES:
            raise RuntimeError("aggregate output disk budget exceeded")
    if cpu_total > TOTAL_CPU_S:
        raise RuntimeError("aggregate CPU budget exceeded")
    (args.output / "DONE.json").write_text(json.dumps({
        "status": "COMPLETED_NOT_ACCEPTED", "plan_sha256": eq3_reference.sha256(args.plan),
        "manifest_hash": manifest["canonical_manifest_hash"], "runs": 4,
        "aggregate_cpu_seconds": cpu_total, "output_bytes": _tree_size(args.output),
    }, indent=2) + "\n")


def _verified_series(run_dir: pathlib.Path, trace: str, power: dict) -> tuple[dict, list[dict]]:
    manifest = eq3_reference.load(run_dir / "input_manifest.json")
    receipt = eq3_reference.load(run_dir / "reference_receipt.json")
    if manifest["trace"] != trace or manifest["step_s"] != STEP_S or receipt["exit_code"] != 0:
        raise ValueError(f"run identity or completion mismatch: {run_dir}")
    for name, expected in receipt["outputs_sha256"].items():
        if eq3_reference.sha256(run_dir / name) != expected:
            raise ValueError(f"output hash mismatch: {run_dir / name}")
    return (eq3_reference.reference_series(run_dir, power["slot_s"]),
            eq3_reference.map_hotspots(run_dir))


def analyze(args: argparse.Namespace) -> None:
    if args.output.exists():
        raise FileExistsError(args.output)
    plan = eq3_reference.load(args.plan)
    validate_fixed_plan(plan)
    power = eq3_reference.load(args.power)
    results = {}
    for trace in ("train", "heldout"):
        one = args.run_root / f"eq3-p2-ref-{trace}-mesh1mm-dt25ms"
        half = args.run_root / f"eq3-p2-ref-{trace}-mesh0p5mm-dt25ms"
        one_series, one_hot = _verified_series(one, trace, power)
        half_series, half_hot = _verified_series(half, trace, power)
        results[trace] = {"region_average_delta": eq3_reference.series_delta(one_series, half_series),
                          "grid_hotspot_delta": eq3_reference.hotspot_delta(one_hot, half_hot)}
    args.output.write_text(json.dumps({
        "evidence_class": "NUMERICAL_REFERENCE_UNCERTAINTY",
        "interpretation": "Spatial-reference characterization only; no RC fitting, physical calibration, or pass claim.",
        "plan_sha256": eq3_reference.sha256(args.plan), "pairs": results,
    }, indent=2) + "\n")


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    sub = result.add_subparsers(dest="command", required=True)
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--case", type=pathlib.Path, required=True)
    common.add_argument("--materials", type=pathlib.Path, required=True)
    common.add_argument("--power", type=pathlib.Path, required=True)
    common.add_argument("--generator", type=pathlib.Path, required=True)
    common.add_argument("--solver", type=pathlib.Path, required=True)
    describe = sub.add_parser("describe", parents=[common])
    describe.set_defaults(function=lambda a: print(json.dumps(build_plan(
        a.case, a.materials, a.power, a.generator, a.solver)[0], indent=2, sort_keys=True)))
    run = sub.add_parser("run-approved", parents=[common])
    run.add_argument("--plan", type=pathlib.Path, required=True)
    run.add_argument("--manifest", type=pathlib.Path, required=True)
    run.add_argument("--approval", type=pathlib.Path, required=True)
    run.add_argument("--root", type=pathlib.Path, required=True)
    run.add_argument("--output", type=pathlib.Path, required=True)
    run.set_defaults(function=run_approved)
    analysis = sub.add_parser("analyze")
    analysis.add_argument("--plan", type=pathlib.Path, required=True)
    analysis.add_argument("--power", type=pathlib.Path, required=True)
    analysis.add_argument("--run-root", type=pathlib.Path, required=True)
    analysis.add_argument("--output", type=pathlib.Path, required=True)
    analysis.set_defaults(function=analyze)
    return result


def main() -> None:
    args = parser().parse_args()
    args.function(args)


if __name__ == "__main__":
    main()
