#!/usr/bin/env python3
"""Prepare, but never launch, the authorized bounded sensitivity design."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
from pathlib import Path

from prepare_stage import config as base_config

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
RUNNER = HERE / "run_endpoint_guard_point.py"


STRATEGIES = ("guard_only", "read_rate_feedback_thermal_guard_v1")
RATE_BPS = 1_536_000_000_000
ACTIVE_S = 20
RECOVERY_S = 10
BASE_LIMITS_K = {
    "hbf": [353.15, 363.15, 378.15],
    "hbm": [353.15, 363.15, 378.15],
    "gpu": [363.15, 373.15, 383.15],
}


def _save(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n")


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _model_map(model_manifest: Path) -> dict[tuple[int, float], str]:
    data = json.loads(model_manifest.read_text())
    mixed = data["source_models"]["mixed_full_2mm"]["model_dir"]
    result = {(300, 1.0): mixed}
    for row in data["derived_models"]:
        if row["coupling_mode"] == "full":
            result[(int(row["ambient_k"]), float(row["external_resistance_scale"]))] = row["model_dir"]
    required = {(300, .5), (300, 1.0), (300, 1.5), (310, 1.0), (320, 1.0),
                (320, 1.5)}
    if set(result) != required:
        raise ValueError(f"thermal model variants differ from frozen sensitivity set: {set(result)}")
    for path in result.values():
        directory = Path(path)
        if not all((directory / name).is_file() for name in
                   ("model.txt", "normalized.json", "rc_grid.json", "rc_sensors.json")):
            raise FileNotFoundError(directory)
    return result


def _levels() -> dict:
    return {
        "ambient_k": [300, 310, 320],
        "external_resistance_scale": [.5, 1.0, 1.5],
        "hbf_read_energy_scale": [.5, 1.0, 1.5],
        "gpu_external_w": [0, 100, 200],
        # Shutdown remains the registered 105 C hard envelope.
        "hbf_guard_light_severe_offset_k": [-5, 0, 5],
        "hbm_guard_light_severe_offset_k": [-5, 0, 5],
    }


def physical_combinations() -> list[dict]:
    nominal = {
        "ambient_k": 300,
        "external_resistance_scale": 1.0,
        "hbf_read_energy_scale": 1.0,
        "gpu_external_w": 0,
        "hbf_guard_light_severe_offset_k": 0,
        "hbm_guard_light_severe_offset_k": 0,
    }
    rows = [{"id": "baseline", "values": dict(nominal), "kind": "baseline"}]
    for axis, values in _levels().items():
        center = nominal[axis]
        for value in values:
            if value == center:
                continue
            row = dict(nominal); row[axis] = value
            value_label = str(value).replace("-", "m").replace(".", "p")
            rows.append({"id": f"oat-{axis}-{value_label}",
                         "values": row, "kind": "oat", "axis": axis})
    interactions = (
        ("interaction-hot-ambient-poor-boundary",
         {"ambient_k": 320, "external_resistance_scale": 1.5}),
        ("interaction-high-memory-energy-high-gpu",
         {"hbf_read_energy_scale": 1.5, "gpu_external_w": 200}),
        ("interaction-conservative-hbf-hbm-guards",
         {"hbf_guard_light_severe_offset_k": -5,
          "hbm_guard_light_severe_offset_k": -5}),
    )
    for identity, changes in interactions:
        row = dict(nominal); row.update(changes)
        rows.append({"id": identity, "values": row, "kind": "interaction",
                     "axes": sorted(changes)})
    if len(rows) != 16 or len({json.dumps(row["values"], sort_keys=True) for row in rows}) != 16:
        raise AssertionError("sensitivity physical combinations are not the frozen 13 OAT + 3 interactions")
    return rows


def _apply(config: dict, values: dict) -> None:
    scale = values["hbf_read_energy_scale"]
    config["energy"]["read_array_j_per_byte"] = 40e-12 * scale
    config["energy"]["read_base_j_per_byte"] = 10e-12 * scale
    config["gpu_external_w"] = values["gpu_external_w"]
    config["thermal_limits_k"] = copy.deepcopy(BASE_LIMITS_K)
    for device in ("hbf", "hbm"):
        offset = values[f"{device}_guard_light_severe_offset_k"]
        config["thermal_limits_k"][device][0] += offset
        config["thermal_limits_k"][device][1] += offset
        if config["thermal_limits_k"][device][2] != 378.15:
            raise AssertionError("shutdown envelope changed")


def _point(point_id: str, topology: str, strategy: str, values: dict,
           execution_mode: str, model_dir: str, mechanism_scope: str) -> tuple[dict, dict]:
    config = base_config(point_id, topology, RATE_BPS, strategy, ACTIVE_S, RECOVERY_S)
    _apply(config, values)
    if execution_mode == "uncontrolled_first_constraint":
        config["control_disabled"] = True
    config["sensitivity"] = {
        "schema_version": "eq3-system-sensitivity-point-v1",
        "execution_mode": execution_mode,
        "mechanism_scope": mechanism_scope,
        "values": values,
        "energy_evidence": "SCENARIO_ASSUMPTION_SCALE_OF_USER_CONFIRMED_40_ARRAY_10_BASE_PJ_PER_B",
        "guard_evidence": "RESEARCH_POLICY_SENSITIVITY_NOT_PRODUCT_LIMIT",
        "shutdown_envelope_k": {"hbf": 378.15, "hbm": 378.15, "gpu": 383.15},
        "domain_max_k_unchanged": 400,
    }
    return config, {"point_id": point_id, "kind": "sensitivity", "topology": topology,
                    "strategy": strategy, "execution_mode": execution_mode,
                    "mechanism_scope": mechanism_scope, "model_dir": model_dir,
                    "sensitivity_values": values}


def prepare(output: Path, model_manifest: Path, thermal_binary: Path,
            artifact_root: Path) -> dict:
    if output.exists():
        raise FileExistsError(output)
    models = _model_map(model_manifest)
    thermal_binary = thermal_binary.resolve(strict=True)
    artifact_root = artifact_root.resolve(strict=True)
    if not thermal_binary.is_relative_to(artifact_root):
        raise ValueError("thermal binary must be within artifact_root")
    inputs = output / "inputs"
    inputs.mkdir(parents=True)
    entries = []

    def emit(spec: dict, topology: str, strategy: str, execution_mode: str,
             mechanism_scope: str) -> None:
        values = spec["values"]
        point_id = (f"sens-{topology}-{spec['id']}-"
                    f"{'u' if execution_mode.startswith('uncontrolled') else STRATEGIES.index(strategy)}-01")
        model_dir = models[(values["ambient_k"], values["external_resistance_scale"])]
        config, entry = _point(point_id, topology, strategy, values, execution_mode,
                               model_dir, mechanism_scope)
        path = inputs / f"{point_id}.json"
        _save(path, config)
        entry.update({"config": str(path.resolve()), "config_sha256": _digest(path),
                      "thermal_binary": str(thermal_binary), "artifact_root": str(artifact_root),
                      "output": str((output / "points" / point_id).resolve())})
        entries.append(entry)

    combinations = physical_combinations()
    for spec in combinations:
        scope = ("MIXED_DIRECT_HBM_LIMIT_OBSERVATIONAL_ONLY_NO_HBM_DEMAND_OR_RELAY_FEEDBACK"
                 if spec.get("axis") == "hbm_guard_light_severe_offset_k" else
                 "MIXED_DIRECT_CONTROLLED_OAT_OR_INTERACTION")
        for strategy in STRATEGIES:
            emit(spec, "mixed_direct", strategy, "controlled", scope)
        emit(spec, "mixed_direct", "guard_only", "uncontrolled_first_constraint", scope)

    # Two HBM threshold endpoints are repeated on relay because only that route
    # makes the paired HBM endpoint state causal for HBF delivery.
    for spec in combinations:
        if spec.get("axis") != "hbm_guard_light_severe_offset_k":
            continue
        for strategy in STRATEGIES:
            emit(spec, "relay", strategy, "controlled",
                 "RELAY_HBM_ENDPOINT_ACTUAL_CONSUMER_EXTENSION")
        emit(spec, "relay", "guard_only", "uncontrolled_first_constraint",
             "RELAY_HBM_ENDPOINT_ACTUAL_CONSUMER_EXTENSION")

    if len(entries) != 54 or len({row["point_id"] for row in entries}) != 54:
        raise AssertionError("active sensitivity design must contain exactly 54 unique points")
    deferred = {
        "schema_version": "eq3-system-sensitivity-deferred-ea-v1",
        "status": "DEFERRED_CONSUMER_NOT_READY",
        "point_count_when_ready": 6,
        "ea_ev": [1.01, 1.04, 1.08],
        "strategies": list(STRATEGIES),
        "required_consumer": "ReliabilityLedger equivalent_age_or_wall plus actual maintenance_driver completion",
        "initial_age_contract": "near-equivalent-age DAY minus approximately 2 s, exact value frozen with consumer",
        "fairness_contract": "same 4 GiB per-stack aged subset; separate fresh-null comparator; no reuse of fresh rate baseline",
        "claim_limit": "CONDITIONAL_EQUIVALENT_AGE_ONLY_NO_RBER_ECC_OR_LIFETIME",
        "runnable": False,
    }
    _save(output / "DEFERRED_EA.json", deferred)
    unique_models = sorted({row["model_dir"] for row in entries})
    model_locks = {
        directory: {name: _digest(Path(directory) / name) for name in
                    ("model.txt", "normalized.json", "rc_grid.json", "rc_sensors.json")}
        for directory in unique_models
    }
    runtime_sources = [
        RUNNER, HERE / "endpoint_policy.py", HERE / "run_system_point.py",
        HERE / "energy.py", HERE / "rate_workload.py",
        HERE / "topology_service.py",
        ROOT / "experiments" / "eq3_maintenance" / "thermal_client.py",
        ROOT / "experiments" / "eq3_maintenance" / "read_rate_policy.py",
    ]
    index = {
        "schema_version": "eq3-system-sensitivity-index-v2",
        "status": "PENDING_DEPENDENCIES_BASE_MATRIX",
        "authorization": "USER_EXPLICIT_SEVEN_AXIS_BOUNDED_SENSITIVITY",
        "point_count": 54,
        "points": sorted(entries, key=lambda row: row["point_id"]),
        "model_manifest": str(model_manifest.resolve()),
        "model_manifest_sha256": _digest(model_manifest),
        "model_locks_sha256": model_locks,
        "runner": str(RUNNER.resolve()),
        "runner_sha256": _digest(RUNNER),
        "endpoint_policy_semantics": (
            "HBF_LEGACY_READ_RATE_POLICY; HBM_SHARED_ENDPOINT_THERMAL_GUARD_"
            "RESTORES_BASELINE_ON_NORMAL_WITHOUT_FOREGROUND_DEMAND_INFERENCE"),
        "runtime_source_locks_sha256": {
            str(path.resolve().relative_to(ROOT.resolve())): _digest(path)
            for path in runtime_sources
        },
        "thermal_binary_sha256": _digest(thermal_binary),
        "dependencies": {
            "base_done": str((output.parent / "BASE_DONE.json").resolve()),
            "required_status": "COMPLETED",
        },
        "design": {
            "representative": "Q1 mixed_direct W1 continuous 1.536 TB/s per HBF stack; relay only for HBM endpoint consumer extension",
            "controlled_strategies": list(STRATEGIES),
            "active_s": ACTIVE_S, "recovery_s": RECOVERY_S,
            "oat_physical_combinations": 13, "interaction_combinations": 3,
            "controlled_points": 32, "uncontrolled_first_constraint_points": 16,
            "relay_hbm_consumer_extension_points": 6,
            "total_active_points": 54, "deferred_ea_points": 6,
        },
        "failure_contract": "400K_DOMAIN_FAILURE_RETAINED; continue independent points; no clamp or threshold relaxation",
        "resources": {"point_wall_s": 600, "point_output_gib": 1,
                      "address_space_gib": 8, "cpu_experiments": 1, "gpu": 0,
                      "stage_wall_s": 21600, "sensitivity_output_gib": 27,
                      "parent_combined_output_gib": 80,
                      "host_ram_reserve_gib": 32, "host_disk_reserve_gib": 100,
                      "basis": "four pilots max 98.34 s projected 30 s point and 496,762,063 B; base projection 29,805,723,750 B"},
    }
    _save(output / "SENSITIVITY_INDEX.json", index)
    return index


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--model-manifest", type=Path, required=True)
    parser.add_argument("--thermal-binary", type=Path, required=True)
    parser.add_argument("--artifact-root", type=Path, required=True)
    args = parser.parse_args()
    index = prepare(args.output.resolve(), args.model_manifest.resolve(strict=True),
                    args.thermal_binary, args.artifact_root)
    print(json.dumps({"status": index["status"], "point_count": index["point_count"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
