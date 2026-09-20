#!/usr/bin/env python3
"""Prepare nine bounded nonuniform/coupling diagnostics without launching them."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from prepare_stage import config as base_config


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
RUNNER = HERE / "run_endpoint_guard_point.py"
TOPOLOGIES = ("mixed_direct", "relay", "dash", "all_hbf_direct")
STRATEGIES = ("guard_only", "thermal_hysteresis_guard",
              "read_rate_feedback_thermal_guard_v1")
RATE = 1_536_000_000_000


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def save(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n")


def required_files(directory: Path) -> dict[str, str]:
    names = ("model.txt", "normalized.json", "rc_grid.json", "rc_sensors.json")
    if not all((directory / name).is_file() for name in names):
        raise FileNotFoundError(directory)
    return {name: digest(directory / name) for name in names}


def _models(manifest_path: Path) -> dict[str, str]:
    data = json.loads(manifest_path.read_text())
    result = {
        "mixed_full": data["source_models"]["mixed_full_2mm"]["model_dir"],
        "all_hbf_full": data["source_models"]["all_hbf_full_2mm"]["model_dir"],
    }
    rows = [row for row in data["derived_models"]
            if row["ambient_k"] == 300 and row["external_resistance_scale"] == 1
            and row["coupling_mode"] == "no_cross_domain_lateral"]
    if len(rows) != 1:
        raise ValueError("exactly one registered mixed no-cross-domain model is required")
    result["mixed_no_cross_domain"] = rows[0]["model_dir"]
    for path in result.values():
        required_files(Path(path))
    return result


def _base_points(index_path: Path) -> tuple[dict[tuple, dict], dict]:
    index = json.loads(index_path.read_text())
    rows = {}
    for entry in index["points"]:
        if entry.get("kind") != "base":
            continue
        config_path = Path(entry["config"])
        if digest(config_path) != entry["config_sha256"]:
            raise ValueError("base config hash differs from RUN_INDEX")
        config = json.loads(config_path.read_text())
        key = (config["topology"], config["workload"]["per_stack_Bps"], config["strategy"])
        if key in rows:
            raise ValueError(f"duplicate base identity {key}")
        rows[key] = {"point_id": entry["point_id"], "config": str(config_path.resolve()),
                     "config_sha256": entry["config_sha256"],
                     "output": entry["output"]}
    return rows, index


def prepare(output: Path, model_manifest: Path, base_index: Path,
            thermal_binary: Path, artifact_root: Path) -> dict:
    if output.exists():
        raise FileExistsError(output)
    models = _models(model_manifest)
    base, frozen_base_index = _base_points(base_index)
    thermal_binary = thermal_binary.resolve(strict=True)
    artifact_root = artifact_root.resolve(strict=True)
    if not thermal_binary.is_relative_to(artifact_root):
        raise ValueError("thermal binary must be within artifact_root")
    inputs = output / "inputs"; inputs.mkdir(parents=True)
    entries = []

    def emit(point_id: str, topology: str, distribution: str, *, hot_stack=None,
             model_key=None, diagnostic: str) -> None:
        config = base_config(point_id, topology, RATE,
                             "read_rate_feedback_thermal_guard_v1", 20, 10)
        config["workload"]["channel_distribution"] = distribution
        if hot_stack is not None:
            config["workload"]["hot_stack"] = hot_stack
        config["rate_diagnostic"] = {
            "schema_version": "eq3-system-rate-diagnostic-point-v1",
            "diagnostic": diagnostic,
            "offered_demand_semantics": "MODELLED_BYTES_NOT_LLM_TOKEN_OR_NATIVE_MQSIM_THROUGHPUT",
            "total_offered_preserved_vs_uniform": True,
        }
        path = inputs / f"{point_id}.json"; save(path, config)
        if model_key is None:
            model_key = "all_hbf_full" if topology == "all_hbf_direct" else "mixed_full"
        entries.append({"point_id": point_id, "kind": "rate_diagnostic",
                        "topology": topology, "diagnostic": diagnostic,
                        "config": str(path.resolve()), "config_sha256": digest(path),
                        "model_dir": models[model_key], "thermal_binary": str(thermal_binary),
                        "artifact_root": str(artifact_root),
                        "output": str((output / "points" / point_id).resolve())})

    for topology in TOPOLOGIES:
        emit(f"diag-{topology}-first-half-1536-feedback-01", topology, "first_half",
             diagnostic="FIRST_HALF_CHANNELS_SAME_TOTAL_OFFERED")
        emit(f"diag-{topology}-hot-hbf0-1536-feedback-01", topology, "uniform",
             hot_stack="hbf0", diagnostic="HOT_STACK_HBF0_SAME_TOTAL_OFFERED")
    emit("diag-mixed-no-cross-domain-1536-feedback-01", "mixed_direct", "uniform",
         model_key="mixed_no_cross_domain",
         diagnostic="NO_CROSS_DOMAIN_LATERAL_THERMAL_COUPLING_SAME_WORKLOAD")
    if len(entries) != 9:
        raise AssertionError("rate diagnostic design must contain nine points")

    def baseline(topology: str, rate: int, strategy: str) -> dict:
        key = (topology, rate, strategy)
        if key not in base:
            raise ValueError(f"base comparison missing {key}")
        return base[key]

    uniform_feedback = {
        topology: baseline(topology, RATE, "read_rate_feedback_thermal_guard_v1")
        for topology in TOPOLOGIES
    }
    comparisons = {
        "nonuniform_vs_uniform": [
            {"diagnostic_point_id": row["point_id"],
             "uniform_base": uniform_feedback[row["topology"]],
             "identity": "SAME_TOPOLOGY_RATE_POLICY_DURATION_TOTAL_OFFERED"}
            for row in entries if row["diagnostic"].startswith(("FIRST_HALF", "HOT_STACK"))
        ],
        "no_coupling_vs_full": [{
            "diagnostic_point_id": "diag-mixed-no-cross-domain-1536-feedback-01",
            "full_coupling_base": uniform_feedback["mixed_direct"],
            "identity": "SAME_CONFIG_AND_WORKLOAD_ONLY_THERMAL_MODEL_DIFFERS",
        }],
        "same_total_offered_existing_base": [
            {"strategy": strategy,
             "mixed_4x1536": baseline("mixed_direct", RATE, strategy),
             "all_hbf_8x768": baseline("all_hbf_direct", 768_000_000_000, strategy),
             "identity": "6.144_TBPS_TOTAL_MODELLED_OFFERED_BOTH"}
            for strategy in STRATEGIES
        ],
        "same_per_stack_existing_base": [
            {"strategy": strategy,
             "mixed_4x1536": baseline("mixed_direct", RATE, strategy),
             "all_hbf_8x1536": baseline("all_hbf_direct", RATE, strategy),
             "identity": "1.536_TBPS_PER_STACK_DIFFERENT_STACK_COUNT_AND_TOTAL_OFFERED"}
            for strategy in STRATEGIES
        ],
    }

    unique_models = sorted({row["model_dir"] for row in entries})
    runtime_sources = [RUNNER, HERE / "endpoint_policy.py", HERE / "run_system_point.py", HERE / "energy.py",
                       HERE / "rate_workload.py", HERE / "topology_service.py",
                       ROOT / "experiments/eq3_maintenance/thermal_client.py",
                       ROOT / "experiments/eq3_maintenance/read_rate_policy.py"]
    index = {
        "schema_version": "eq3-system-rate-diagnostics-index-v2",
        "status": "PENDING_DEPENDENCIES_BASE_MATRIX",
        "authorization": "USER_EXPLICIT_BOUNDED_NONUNIFORM_AND_COUPLING_DIAGNOSTICS",
        "point_count": 9, "points": entries, "comparisons": comparisons,
        "base_index": str(base_index.resolve()), "base_index_sha256": digest(base_index),
        "base_source_locks": frozen_base_index.get("source_locks", {}),
        "model_manifest": str(model_manifest.resolve()),
        "model_manifest_sha256": digest(model_manifest),
        "model_locks_sha256": {path: required_files(Path(path)) for path in unique_models},
        "runner": str(RUNNER.resolve()),
        "runner_sha256": digest(RUNNER),
        "endpoint_policy_semantics": (
            "HBF_LEGACY_READ_RATE_POLICY; HBM_SHARED_ENDPOINT_THERMAL_GUARD_"
            "RESTORES_BASELINE_ON_NORMAL_WITHOUT_FOREGROUND_DEMAND_INFERENCE"),
        "runtime_source_locks_sha256": {
            str(path.resolve().relative_to(ROOT.resolve())): digest(path) for path in runtime_sources},
        "thermal_binary_sha256": digest(thermal_binary),
        "dependencies": {"base_done": str((output.parent / "BASE_DONE.json").resolve()),
                         "required_status": "COMPLETED"},
        "resources": {"point_wall_s": 600, "point_output_gib": 1,
                      "address_space_gib": 8, "cpu_experiments": 1, "gpu": 0,
                      "new_output_gib": 4.5, "parent_combined_output_gib": 80,
                      "parent_accounting_gib": {"base_estimate": 30,
                                                "sensitivity_allocation": 27,
                                                "rate_diagnostics_allocation": 4.5,
                                                "remaining_for_maintenance_causal": 18.5},
                      "stage_wall_s": 21600, "host_ram_reserve_gib": 32,
                      "host_disk_reserve_gib": 100},
        "limitations": ["MODELLED_AGGREGATED_BYTE_PRESSURE_NOT_LLM_THROUGHPUT",
                        "NO_NEW_BASELINE_RUNS", "NO_TOKEN_PER_S_INFERENCE",
                        "CROSS_TOPOLOGY_GEOMETRY_AND_STACK_COUNT_REMAIN_CONFOUNDERS"],
    }
    save(output / "RATE_DIAGNOSTICS_INDEX.json", index)
    return index


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--model-manifest", type=Path, required=True)
    parser.add_argument("--base-index", type=Path, required=True)
    parser.add_argument("--thermal-binary", type=Path, required=True)
    parser.add_argument("--artifact-root", type=Path, required=True)
    args = parser.parse_args()
    index = prepare(args.output.resolve(), args.model_manifest.resolve(strict=True),
                    args.base_index.resolve(strict=True), args.thermal_binary,
                    args.artifact_root)
    print(json.dumps({"status": index["status"], "point_count": index["point_count"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
