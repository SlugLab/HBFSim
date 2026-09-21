#!/usr/bin/env python3
"""Serial launcher for the separately frozen sensitivity index."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import resource
import shutil
import signal
import subprocess
import sys
import time


GIB = 1024**3
ROOT = Path(__file__).resolve().parents[2]


def deadline_reached(elapsed, limit):
    return limit is not None and elapsed >= limit


def safety_wait(elapsed, limit):
    return 15 if limit is None else min(15, max(.1, limit - elapsed))


def save(path: Path, value: dict) -> None:
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")
    temp.replace(path)


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def size(path: Path) -> int:
    return sum(row.stat().st_size for row in path.rglob("*") if row.is_file())


def available_memory() -> int:
    rows = dict(line.split(":", 1) for line in Path("/proc/meminfo").read_text().splitlines())
    return int(rows["MemAvailable"].split()[0]) * 1024


def domain_failure(output: Path) -> bool:
    transcript = output / "thermal-process" / "thermal-transcript.jsonl"
    if not transcript.is_file() or not (output / "FAILED.json").is_file():
        return False
    for line in transcript.read_text().splitlines():
        value = json.loads(line)
        response = value.get("response")
        if isinstance(response, dict) and response.get("type") == "ERROR":
            if response.get("status") == "DOMAIN_FAILURE":
                return True
    return False


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--index", type=Path, required=True)
    args = parser.parse_args()
    index_path = args.index.resolve(strict=True)
    index = json.loads(index_path.read_text())
    stage = index_path.parent
    parent_stage = stage.parent
    resources = index["resources"]
    if index.get("status") != "PENDING_DEPENDENCIES_BASE_MATRIX":
        raise ValueError("sensitivity index status is not the frozen prelaunch state")
    dependency = Path(index["dependencies"]["base_done"])
    if not dependency.is_file() or json.loads(dependency.read_text()).get("status") != "COMPLETED":
        raise RuntimeError("base matrix dependency is not complete")
    runner = Path(index["runner"])
    if digest(runner) != index["runner_sha256"]:
        raise ValueError("frozen sensitivity runner changed")
    for relative, expected in index["runtime_source_locks_sha256"].items():
        if digest(ROOT / relative) != expected:
            raise ValueError(f"runtime source changed: {relative}")
    for directory, locks in index["model_locks_sha256"].items():
        for name, expected in locks.items():
            if digest(Path(directory) / name) != expected:
                raise ValueError(f"thermal model changed: {directory}/{name}")
    binary = Path(index["points"][0]["thermal_binary"])
    if digest(binary) != index["thermal_binary_sha256"]:
        raise ValueError("thermal binary changed")

    env = dict(os.environ, OMP_NUM_THREADS="1", OPENBLAS_NUM_THREADS="1",
               MKL_NUM_THREADS="1", NUMEXPR_NUM_THREADS="1", CUDA_VISIBLE_DEVICES="",
               PYTHONDONTWRITEBYTECODE="1")
    started = time.monotonic()
    completed, domain_failures = [], []
    for row in index["points"]:
        output = Path(row["output"])
        if output.exists():
            raise FileExistsError(f"preserve existing sensitivity evidence: {output}")
        config = Path(row["config"])
        if digest(config) != row["config_sha256"]:
            raise ValueError(f"input freeze mismatch: {row['point_id']}")
        if available_memory() < resources["host_ram_reserve_gib"] * GIB:
            raise RuntimeError("host memory reserve")
        if shutil.disk_usage(parent_stage).free < resources["host_disk_reserve_gib"] * GIB:
            raise RuntimeError("host disk reserve")
        if size(stage) > resources["sensitivity_output_gib"] * GIB:
            raise RuntimeError("sensitivity retained-output budget")
        if size(parent_stage) > resources["parent_combined_output_gib"] * GIB:
            raise RuntimeError("parent combined retained-output budget")
        if deadline_reached(time.monotonic() - started, resources["stage_wall_s"]):
            raise RuntimeError("sensitivity stage watchdog")

        launch = stage / "launch" / row["point_id"]
        launch.mkdir(parents=True, exist_ok=False)
        command = [sys.executable, "-B", str(runner)]
        for key in ("config", "model_dir", "thermal_binary", "artifact_root", "output"):
            command += ["--" + key.replace("_", "-"), row[key]]
        if resources.get('diagnostic_stack_interval_s'):
            # Read-only wall-time diagnostics; no simulated-time or scheduling change.
            interval = int(resources['diagnostic_stack_interval_s'])
            command = [sys.executable, '-B', '-c',
                'import faulthandler,runpy,sys,os; '
                f'faulthandler.dump_traceback_later({interval},repeat=True); '
                'sys.argv=sys.argv[1:]; sys.path.insert(0,os.path.dirname(sys.argv[0])); '
                'runpy.run_path(sys.argv[0],run_name="__main__")',
                *command[2:]]
        save(launch / "launch.json", {"command": command, "point": row,
                                      "resources": resources})
        save(stage / "STATUS.json", {"status": "RUNNING", "active": row["point_id"],
                                      "completed": completed, "domain_failures": domain_failures})
        wall = time.monotonic(); reason = None
        with (launch / "stdout.log").open("w") as stdout, (launch / "stderr.log").open("w") as stderr:
            child = subprocess.Popen(command, stdout=stdout, stderr=stderr, env=env,
                                     start_new_session=True)
            while True:
                try:
                    code = child.wait(timeout=safety_wait(time.monotonic() - wall, resources["point_wall_s"]))
                    break
                except subprocess.TimeoutExpired:
                    if deadline_reached(time.monotonic() - wall, resources["point_wall_s"]):
                        reason = "POINT_WATCHDOG"
                    elif deadline_reached(time.monotonic() - started, resources["stage_wall_s"]):
                        reason = "STAGE_WATCHDOG"
                    elif size(output) > resources["point_output_gib"] * GIB:
                        reason = "POINT_OUTPUT_BUDGET"
                    elif size(stage) > resources["sensitivity_output_gib"] * GIB:
                        reason = "SENSITIVITY_OUTPUT_BUDGET"
                    elif size(parent_stage) > resources["parent_combined_output_gib"] * GIB:
                        reason = "PARENT_COMBINED_OUTPUT_BUDGET"
                    elif available_memory() < resources["host_ram_reserve_gib"] * GIB:
                        reason = "HOST_RAM_RESERVE"
                    elif shutil.disk_usage(parent_stage).free < resources["host_disk_reserve_gib"] * GIB:
                        reason = "HOST_DISK_RESERVE"
                    if reason:
                        os.killpg(child.pid, signal.SIGTERM)
                        try:
                            code = child.wait(timeout=5)
                        except subprocess.TimeoutExpired:
                            os.killpg(child.pid, signal.SIGKILL); code = child.wait()
                        break
        result = {"point_id": row["point_id"], "exit_code": code, "reason": reason,
                  "wall_s": time.monotonic() - wall,
                  "output_bytes": size(output) if output.exists() else 0}
        if code == 0 and (output / "DONE.json").is_file():
            result["classification"] = "COMPLETED"
            completed.append(result)
        elif reason is None and domain_failure(output):
            result["classification"] = "DOMAIN_FAILURE"
            domain_failures.append(result)
        else:
            result["classification"] = "EXECUTION_FAILURE"
            save(launch / "result.json", result)
            save(stage / "FAILED.json", {"failed": result, "completed": completed,
                                         "domain_failures": domain_failures})
            return 1
        save(launch / "result.json", result)
    receipt = {"status": "COMPLETED_WITH_RETAINED_DOMAIN_FAILURES" if domain_failures else "COMPLETED",
               "completed": completed, "domain_failures": domain_failures,
               "wall_s": time.monotonic() - started}
    save(stage / "DONE.json", receipt)
    save(stage / "STATUS.json", receipt)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
