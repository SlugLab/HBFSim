#!/usr/bin/env python3
"""Prepare immutable inputs and a run index for the approved rate/thermal stage."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import sys
from typing import Any


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))

from model_workloads import build_workload


MODEL_IDS = {
    "7B": "Qwen/Qwen2.5-7B-Instruct",
    "72B": "Qwen/Qwen2.5-72B-Instruct",
    "235B": "Qwen/Qwen3-235B-A22B",
}
TOPOLOGIES = {
    "mixed_direct": {"tag": "mixed", "stack_count": 4},
    "all_hbf_direct": {"tag": "allhbf", "stack_count": 8},
}
POLICIES = {
    "guard_only": "P0",
    "thermal_hysteresis_guard": "P1",
    "read_rate_feedback_thermal_guard_v1": "P2",
}


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _encoded(value: Any) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n").encode()


def _write_immutable(path: Path, value: Any) -> None:
    data = _encoded(value)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if path.read_bytes() != data:
            raise FileExistsError(f"refusing to replace different frozen input: {path}")
        return
    path.write_bytes(data)


def _workload_config(*, model: str, topology: str, pattern: str,
                     scans: int, active_s: int, recovery_s: int) -> dict:
    result = {
        "schema_version": "eq3-rate-model-workload-config-v1",
        "model_id": MODEL_IDS[model],
        "full_scans_per_s": scans,
        "pattern": pattern,
        "stack_count": TOPOLOGIES[topology]["stack_count"],
        "channels_per_stack": 16,
        "step_ns": 20_000_000,
        "active_ns": active_s * 1_000_000_000,
        "recovery_ns": recovery_s * 1_000_000_000,
    }
    if pattern == "burst_equal_mean":
        result.update(burst_period_ns=200_000_000, burst_on_ns=100_000_000)
    return result


def _input_set(stage: Path, *, phase: str, topology: str, model: str,
               pattern: str, scans: int, active_s: int, recovery_s: int) -> tuple[Path, Path, Path, dict]:
    key = f"{phase}-{TOPOLOGIES[topology]['tag']}-{model}-{pattern}-{scans}scan"
    config_path = stage / "inputs" / "configs" / f"{key}.json"
    workload_path = stage / "inputs" / "workloads" / f"{key}.json"
    scenario_path = stage / "inputs" / "scenarios" / f"{key}.json"
    config = _workload_config(model=model, topology=topology, pattern=pattern,
                              scans=scans, active_s=active_s, recovery_s=recovery_s)
    workload = build_workload(config)
    mean_bps = workload["metadata"]["mean_active_offered_Bps"]
    stack_count = TOPOLOGIES[topology]["stack_count"]
    target_bps = max(1, min(mean_bps // stack_count, 1_536_000_000_000 * 4 // 5))
    scenario = {
        "schema_version": "eq3-rate-controlled-scenario-v1",
        "scenario_id": key,
        "topology": topology,
        "window_ns": 20_000_000,
        "target_read_Bps_per_stack": target_bps,
    }
    _write_immutable(config_path, config)
    _write_immutable(workload_path, workload)
    _write_immutable(scenario_path, scenario)
    return config_path, workload_path, scenario_path, workload


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stage", type=Path, required=True)
    args = parser.parse_args()
    stage = args.stage.resolve(strict=True)
    preflight_path = stage / "PREFLIGHT.json"
    preflight = json.loads(preflight_path.read_text())
    if preflight.get("task") != "EQ3-RATE-THERMAL-CONTROL-CONTINUATION-v1":
        raise ValueError("unexpected stage preflight task")
    if preflight.get("pilot", {}).get("points") != 2 or preflight.get("main", {}).get("points") != 39:
        raise ValueError("preflight must freeze two pilots and 39 main points")
    resources = preflight.get("resources", {})
    expected_resources = {
        "cpu_processes": 1, "OMP_BLAS_threads": 1, "address_limit_gib": 4,
        "per_point_watchdog_s": 600, "stage_wall_s": 5400,
        "host_memory_reserve_gib": 32, "host_disk_reserve_gib": 100,
    }
    for field, expected in expected_resources.items():
        if resources.get(field) != expected:
            raise ValueError(f"preflight resource {field} differs from {expected}")

    binary = Path(preflight["thermal_binary"]).resolve(strict=True)
    artifact_root = binary.parents[2]
    model_dirs = {key: Path(value).resolve(strict=True)
                  for key, value in preflight["model_dirs"].items()}
    profiles = {
        topology: (stage / f"profile-{topology}.json").resolve(strict=True)
        for topology in TOPOLOGIES
    }
    source_files = [
        HERE / "prepare_control_stage.py", HERE / "launch_control_stage.py",
        HERE / "model_workloads.py", HERE / "fluid_service.py",
        HERE / "run_controlled.py",
        ROOT / "experiments" / "eq3_maintenance" / "read_rate_policy.py",
        ROOT / "experiments" / "eq3_maintenance" / "thermal_client.py",
    ]
    source_locks = {str(path.relative_to(ROOT)): _sha256(path) for path in source_files}
    source_revision = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, check=True,
        text=True, capture_output=True,
    ).stdout.strip()

    points = []

    def add_points(*, phase: str, topology: str, model: str, pattern: str,
                   scans: int, active_s: int, recovery_s: int,
                   policies: list[str]) -> None:
        config_path, workload_path, scenario_path, workload = _input_set(
            stage, phase=phase, topology=topology, model=model, pattern=pattern,
            scans=scans, active_s=active_s, recovery_s=recovery_s,
        )
        for policy in policies:
            point_id = (
                f"RT-{phase.upper()}-{TOPOLOGIES[topology]['tag']}-{model}-"
                f"{pattern}-{scans}scan-{POLICIES[policy]}"
            )
            points.append({
                "ordinal": len(points) + 1,
                "point_id": point_id,
                "phase": phase,
                "topology": topology,
                "model": model,
                "pattern": pattern,
                "full_scans_per_s": scans,
                "strategy": policy,
                "active_s": active_s,
                "recovery_s": recovery_s,
                "profile": str(profiles[topology]),
                "config": str(config_path),
                "workload": str(workload_path),
                "scenario": str(scenario_path),
                "model_dir": str(model_dirs[topology]),
                "thermal_binary": str(binary),
                "artifact_root": str(artifact_root),
                "output": str(stage / "points" / point_id),
                "input_sha256": {
                    "profile": _sha256(profiles[topology]),
                    "config": _sha256(config_path),
                    "workload": _sha256(workload_path),
                    "scenario": _sha256(scenario_path),
                },
                "expected_active_offered_bytes": workload["metadata"]["expected_active_offered_bytes"],
                "expected_backlog_is_failure": False,
            })

    for topology in ("mixed_direct", "all_hbf_direct"):
        add_points(
            phase="pilot", topology=topology, model="235B", pattern="continuous",
            scans=16, active_s=8, recovery_s=4,
            policies=["read_rate_feedback_thermal_guard_v1"],
        )
    for topology in ("mixed_direct", "all_hbf_direct"):
        for model in ("7B", "72B", "235B"):
            for pattern in ("continuous", "burst_equal_mean"):
                add_points(
                    phase="main", topology=topology, model=model, pattern=pattern,
                    scans=16, active_s=20, recovery_s=10,
                    policies=list(POLICIES),
                )
    add_points(
        phase="main", topology="all_hbf_direct", model="235B", pattern="continuous",
        scans=32, active_s=20, recovery_s=10, policies=list(POLICIES),
    )
    if len(points) != 41 or sum(row["phase"] == "pilot" for row in points) != 2:
        raise AssertionError("prepared point count does not match the frozen stage")

    model_locks = {}
    for topology, directory in model_dirs.items():
        model_locks[topology] = {
            name: _sha256(directory / name)
            for name in ("normalized.json", "model.txt", "rc_grid.json", "rc_sensors.json")
        }
    index = {
        "schema_version": "eq3-rate-thermal-control-run-index-v1",
        "prepared_utc": datetime.now(timezone.utc).isoformat(),
        "stage": str(stage),
        "authority": "USER_CONFIRMED_RATE_THERMAL_CONTINUATION_AND_SATURATION_AMENDMENT",
        "preflight": str(preflight_path),
        "preflight_sha256": _sha256(preflight_path),
        "source_root": str(ROOT),
        "source_revision": source_revision,
        "source_locks": source_locks,
        "thermal_binary_sha256": _sha256(binary),
        "model_locks": model_locks,
        "resources": resources,
        "point_count": len(points),
        "pilot_count": 2,
        "main_count": 39,
        "points": points,
    }
    index_path = stage / "RUN_INDEX.json"
    _write_immutable(index_path, index)
    receipt = {
        "status": "PREPARED_NOT_STARTED",
        "run_index": str(index_path),
        "run_index_sha256": _sha256(index_path),
        "point_count": 41,
        "pilot_count": 2,
        "main_count": 39,
        "source_revision": source_revision,
    }
    _write_immutable(stage / "PREPARED.json", receipt)
    print(json.dumps(receipt, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
