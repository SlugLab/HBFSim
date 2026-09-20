#!/usr/bin/env python3
"""Run the frozen rate/thermal control index serially with bounded resources."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import signal
import shutil
import subprocess
import sys
import time
from typing import Any


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
GIB = 1024 ** 3
MIB = 1024 ** 2


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _save(path: Path, value: Any) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n")
    temporary.replace(path)


def _tree_bytes(path: Path) -> int:
    if not path.exists():
        return 0
    return sum(item.stat().st_size for item in path.rglob("*") if item.is_file())


def _memory_available_bytes() -> int:
    values = dict(line.split(":", 1) for line in Path("/proc/meminfo").read_text().splitlines())
    return int(values["MemAvailable"].split()[0]) * 1024


def _check_locks(index: dict) -> None:
    if _sha256(Path(index["preflight"])) != index["preflight_sha256"]:
        raise RuntimeError("preflight lock mismatch")
    for relative, expected in index["source_locks"].items():
        if _sha256(Path(index["source_root"]) / relative) != expected:
            raise RuntimeError(f"source lock mismatch: {relative}")
    if _sha256(Path(index["points"][0]["thermal_binary"])) != index["thermal_binary_sha256"]:
        raise RuntimeError("thermal binary lock mismatch")
    for topology, locks in index["model_locks"].items():
        row = next(point for point in index["points"] if point["topology"] == topology)
        directory = Path(row["model_dir"])
        for name, expected in locks.items():
            if _sha256(directory / name) != expected:
                raise RuntimeError(f"thermal model lock mismatch: {topology}/{name}")
    for point in index["points"]:
        for field, expected in point["input_sha256"].items():
            if _sha256(Path(point[field])) != expected:
                raise RuntimeError(f"point input lock mismatch: {point['point_id']}/{field}")


def _command(point: dict, resources: dict) -> list[str]:
    return [
        sys.executable, "-B", str(HERE / "run_controlled.py"),
        "--profile", point["profile"],
        "--model-dir", point["model_dir"],
        "--workload", point["workload"],
        "--scenario", point["scenario"],
        "--thermal-binary", point["thermal_binary"],
        "--artifact-root", point["artifact_root"],
        "--output", point["output"],
        "--strategy", point["strategy"],
        "--address-limit-gib", str(resources["address_limit_gib"]),
    ]


def _run_point(stage: Path, point: dict, resources: dict, stage_deadline: float,
               effective_point_limit: int | None, effective_stage_limit: int | None) -> dict:
    output = Path(point["output"])
    launch = stage / "launch" / point["point_id"]
    if output.exists() or launch.exists():
        raise FileExistsError(f"refusing to overwrite existing point evidence: {point['point_id']}")
    if _memory_available_bytes() < resources["host_memory_reserve_gib"] * GIB:
        raise RuntimeError("host memory reserve unavailable before point start")
    if shutil.disk_usage(stage).free < resources["host_disk_reserve_gib"] * GIB:
        raise RuntimeError("host disk reserve unavailable before point start")
    launch.mkdir(parents=True, exist_ok=False)
    command = _command(point, resources)
    started_utc = datetime.now(timezone.utc).isoformat()
    _save(launch / "launch.json", {
        "point_id": point["point_id"], "started_utc": started_utc,
        "command": command, "point_watchdog_s": resources["per_point_watchdog_s"],
        "stage_deadline_remaining_s": max(0.0, stage_deadline - time.monotonic()),
        "host_memory_reserve_gib": resources["host_memory_reserve_gib"],
        "host_disk_reserve_gib": resources["host_disk_reserve_gib"],
        "effective_point_output_limit_bytes": effective_point_limit,
        "effective_stage_output_limit_bytes": effective_stage_limit,
    })
    reason = None
    started = time.monotonic()
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", CUDA_VISIBLE_DEVICES="",
               OMP_NUM_THREADS="1", OPENBLAS_NUM_THREADS="1", MKL_NUM_THREADS="1",
               NUMEXPR_NUM_THREADS="1")
    with (launch / "stdout.log").open("wb") as stdout, (launch / "stderr.log").open("wb") as stderr:
        child = subprocess.Popen(command, cwd=ROOT, env=env, stdout=stdout, stderr=stderr,
                                 start_new_session=True)
        while True:
            point_remaining = resources["per_point_watchdog_s"] - (time.monotonic() - started)
            stage_remaining = stage_deadline - time.monotonic()
            if point_remaining <= 0:
                reason = "POINT_WATCHDOG"
                break
            if stage_remaining <= 0:
                reason = "STAGE_WATCHDOG"
                break
            try:
                return_code = child.wait(timeout=min(10.0, point_remaining, stage_remaining))
                break
            except subprocess.TimeoutExpired:
                if _memory_available_bytes() < resources["host_memory_reserve_gib"] * GIB:
                    reason = "HOST_MEMORY_RESERVE"
                    break
                if shutil.disk_usage(stage).free < resources["host_disk_reserve_gib"] * GIB:
                    reason = "HOST_DISK_RESERVE"
                    break
                if effective_point_limit is not None and _tree_bytes(output) > effective_point_limit:
                    reason = "EVIDENCE_ADJUSTED_POINT_OUTPUT_LIMIT"
                    break
                if effective_stage_limit is not None and _tree_bytes(stage) > effective_stage_limit:
                    reason = "EVIDENCE_ADJUSTED_STAGE_OUTPUT_LIMIT"
                    break
        if reason:
            os.killpg(child.pid, signal.SIGTERM)
            try:
                return_code = child.wait(timeout=5)
            except subprocess.TimeoutExpired:
                os.killpg(child.pid, signal.SIGKILL)
                return_code = child.wait()
    result = {
        "point_id": point["point_id"],
        "execution_status": "COMPLETED" if return_code == 0 else "FAILED",
        "exit_code": return_code,
        "reason": reason,
        "wall_s": time.monotonic() - started,
        "output_bytes": _tree_bytes(output),
        "finished_utc": datetime.now(timezone.utc).isoformat(),
    }
    _save(launch / "result.json", result)
    if return_code != 0 or not (output / "DONE.json").is_file():
        raise RuntimeError(f"point failed with retained evidence: {point['point_id']}")
    return result


def _review_pilots(stage: Path, points: list[dict], main_points: list[dict],
                   resources: dict, launch_results: list[dict]) -> tuple[int, int, float, dict]:
    rows = []
    observed_max = 0
    for point, launch_result in zip(points, launch_results):
        done = json.loads((Path(point["output"]) / "DONE.json").read_text())
        summary = done.get("summary", {})
        peaks = summary.get("peak_k_by_stack", {})
        if done.get("execution_status") != "COMPLETED":
            raise RuntimeError(f"pilot did not complete: {point['point_id']}")
        if summary.get("byte_conservation_error") != 0:
            raise RuntimeError(f"pilot byte conservation failed: {point['point_id']}")
        energy_tolerance = 1e-9 * max(1.0, float(summary.get("energy_j", 0.0)))
        if abs(done.get("served_to_thermal_energy_error_j", math.inf)) > energy_tolerance:
            raise RuntimeError(f"pilot thermal energy mapping failed: {point['point_id']}")
        if not peaks or min(peaks.values()) < 300 or max(peaks.values()) >= 400:
            raise RuntimeError(f"pilot temperature domain review failed: {point['point_id']}")
        if max(peaks.values()) <= 300:
            raise RuntimeError(f"pilot showed no thermal response: {point['point_id']}")
        if launch_result["wall_s"] > resources["per_point_watchdog_s"]:
            raise RuntimeError(f"pilot exceeded point watchdog: {point['point_id']}")
        observed_max = max(observed_max, launch_result["output_bytes"])
        rows.append({
            "point_id": point["point_id"],
            "byte_conservation_error": 0,
            "served_to_thermal_energy_error_j": done["served_to_thermal_energy_error_j"],
            "served_to_thermal_energy_tolerance_j": energy_tolerance,
            "peak_k": max(peaks.values()),
            "wall_s": launch_result["wall_s"],
            "output_bytes": launch_result["output_bytes"],
            "final_backlog_bytes": summary.get("final_backlog_bytes"),
            "backlog_interpretation": "EXPECTED_EVIDENCE_NOT_EXECUTION_FAILURE",
        })
    estimate = resources["per_point_output_mib"] * MIB
    # Main points cover 30 simulated seconds versus 12 seconds for pilots.
    # Scale measured pilot evidence by that exact duration ratio, then retain
    # 25% headroom for transcript/state-size variation.
    effective_point = max(estimate, math.ceil(observed_max * 30 / 12 * 1.25))
    # The old values are reviewed estimates, not restored hard ceilings.  The
    # evidence-adjusted finite bounds remain subordinate to host free-space reserve.
    effective_stage = max(
        resources["stage_new_retained_gib"] * GIB,
        _tree_bytes(stage) + effective_point * 39,
    )
    pilot_wall_by_topology = {
        point["topology"]: result["wall_s"]
        for point, result in zip(points, launch_results)
    }
    projected_main_wall = sum(
        pilot_wall_by_topology[point["topology"]] * 30 / 12
        for point in main_points
    )
    effective_stage_wall = max(
        float(resources["stage_wall_s"]),
        sum(row["wall_s"] for row in launch_results) + projected_main_wall * 1.25,
    )
    review = {
        "status": "PILOT_REVIEW_PASS_MAIN_RELEASED",
        "reviewed_utc": datetime.now(timezone.utc).isoformat(),
        "rows": rows,
        "preflight_output_estimate_bytes_per_point": estimate,
        "observed_max_output_bytes": observed_max,
        "pilot_to_main_duration_ratio": 30 / 12,
        "output_headroom_fraction": 0.25,
        "effective_point_output_limit_bytes": effective_point,
        "effective_stage_output_limit_bytes": effective_stage,
        "projected_main_wall_s_from_topology_pilots": projected_main_wall,
        "effective_stage_watchdog_s": effective_stage_wall,
        "resource_semantics": "FINITE_EVIDENCE_ADJUSTED_NOT_OLD_FIXED_HARD_LIMIT",
    }
    _save(stage / "PILOT_REVIEW.json", review)
    return effective_point, effective_stage, effective_stage_wall, review


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stage", type=Path, required=True)
    args = parser.parse_args()
    stage = args.stage.resolve(strict=True)
    index_path = stage / "RUN_INDEX.json"
    index = json.loads(index_path.read_text())
    if index.get("schema_version") != "eq3-rate-thermal-control-run-index-v1":
        raise ValueError("unsupported run index")
    if index.get("point_count") != 41 or index.get("pilot_count") != 2 or index.get("main_count") != 39:
        raise ValueError("run index does not contain frozen 2+39 points")
    _check_locks(index)
    resources = index["resources"]
    stage_started = time.monotonic()
    stage_deadline = stage_started + resources["stage_wall_s"]
    status = {
        "execution_status": "RUNNING", "started_utc": datetime.now(timezone.utc).isoformat(),
        "run_index_sha256": _sha256(index_path), "completed": [], "failed": None,
    }
    _save(stage / "STATUS.json", status)
    try:
        pilots = [row for row in index["points"] if row["phase"] == "pilot"]
        mains = [row for row in index["points"] if row["phase"] == "main"]
        pilot_results = []
        for point in pilots:
            result = _run_point(stage, point, resources, stage_deadline, None, None)
            pilot_results.append(result)
            status["completed"].append(point["point_id"])
            _save(stage / "STATUS.json", status)
        point_limit, stage_limit, effective_stage_wall, review = _review_pilots(
            stage, pilots, mains, resources, pilot_results
        )
        stage_deadline = stage_started + effective_stage_wall
        status["pilot_review"] = review["status"]
        _save(stage / "STATUS.json", status)
        for point in mains:
            result = _run_point(stage, point, resources, stage_deadline, point_limit, stage_limit)
            status["completed"].append(point["point_id"])
            _save(stage / "STATUS.json", status)
        status.update(
            execution_status="COMPLETED", finished_utc=datetime.now(timezone.utc).isoformat(),
            wall_s=time.monotonic() - stage_started,
            retained_bytes=_tree_bytes(stage),
        )
        _save(stage / "DONE.json", status)
        _save(stage / "STATUS.json", status)
        print(json.dumps(status, indent=2))
        return 0
    except BaseException as exc:
        status.update(
            execution_status="FAILED_PAUSED", failed=repr(exc),
            finished_utc=datetime.now(timezone.utc).isoformat(),
            wall_s=time.monotonic() - stage_started,
            retained_bytes=_tree_bytes(stage),
        )
        _save(stage / "FAILED.json", status)
        _save(stage / "STATUS.json", status)
        raise


if __name__ == "__main__":
    raise SystemExit(main())
