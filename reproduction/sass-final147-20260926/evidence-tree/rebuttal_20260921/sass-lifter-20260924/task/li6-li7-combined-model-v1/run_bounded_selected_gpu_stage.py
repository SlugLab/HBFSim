#!/usr/bin/env python3
"""One-shot finite selected-device controller for an already reviewed stage.

The JSON plan is immutable input. This script never reserves a GPU, stops a
service, chooses a new experiment, retries, or starts a second stage.
"""

import argparse
import hashlib
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path


def ticks(pid):
    return int(Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[19])


def write_new(path, value):
    with path.open("x", encoding="utf-8") as file:
        json.dump(value, file, indent=2, sort_keys=True)
        file.write("\n")
        file.flush()
        os.fsync(file.fileno())


def same_group(pid, start, pgid):
    try:
        return ticks(pid) == start and os.getpgid(pid) == pgid
    except (FileNotFoundError, ProcessLookupError):
        return False


def stop_owned(pid, start, pgid):
    if not same_group(pid, start, pgid):
        return "identity_changed_no_signal"
    os.killpg(pgid, signal.SIGTERM)
    for _ in range(10):
        time.sleep(1)
        if not same_group(pid, start, pgid):
            return "term"
    if same_group(pid, start, pgid):
        os.killpg(pgid, signal.SIGKILL)
        return "kill_after_term"
    return "term"


def check_plan(plan):
    required = ("stage_id", "command", "cwd", "output_dir", "guard_script",
                "guard_sha256",
                "reservation_pid", "reservation_start_ticks", "reserve_deadline_unix",
                "configured_gpu_index", "minimum_free_mib", "minimum_disk_gib", "disk_path",
                "worker_seconds", "outer_seconds", "collection_seconds")
    for key in required:
        if key not in plan:
            raise ValueError(f"missing {key}")
    if not isinstance(plan["command"], list) or not plan["command"] or not all(
            isinstance(item, str) and item for item in plan["command"]):
        raise ValueError("command must be a nonempty argv array")
    if not isinstance(plan.get("env", {}), dict) or not all(
            isinstance(k, str) and isinstance(v, str) for k, v in plan.get("env", {}).items()):
        raise ValueError("env must contain string keys and values")
    if not Path(plan["command"][0]).is_absolute() or not Path(plan["command"][0]).is_file():
        raise ValueError("command executable must be an existing absolute file")
    for key in ("reservation_pid", "reservation_start_ticks", "reserve_deadline_unix",
                "minimum_free_mib", "minimum_disk_gib", "worker_seconds",
                "outer_seconds", "collection_seconds"):
        if not isinstance(plan[key], int) or plan[key] <= 0:
            raise ValueError(f"invalid {key}")
    if plan["worker_seconds"] >= plan["outer_seconds"]:
        raise ValueError("outer deadline must exceed worker limit")
    if (not isinstance(plan["configured_gpu_index"], int) or plan["configured_gpu_index"] < 0 or
            plan["minimum_free_mib"] <= 0 or plan["minimum_disk_gib"] <= 0):
        raise ValueError("invalid configured selected-device resource bounds")
    for key in ("cwd", "guard_script", "disk_path"):
        if not Path(plan[key]).is_absolute() or not Path(plan[key]).exists():
            raise ValueError(f"missing absolute {key}")
    if hashlib.sha256(Path(plan["guard_script"]).read_bytes()).hexdigest() != plan["guard_sha256"]:
        raise ValueError("guard script SHA-256 mismatch")
    if not Path(plan["output_dir"]).is_absolute():
        raise ValueError("output_dir must be absolute")
    if Path(plan["output_dir"]).exists():
        raise ValueError("output_dir already exists; same attempt will not be repeated")
    if ticks(plan["reservation_pid"]) != plan["reservation_start_ticks"]:
        raise ValueError("reservation identity mismatch")
    if time.time() + plan["outer_seconds"] + plan["collection_seconds"] > plan["reserve_deadline_unix"]:
        raise ValueError("full stage and collection tail do not fit reservation deadline")

def guard_command(plan, worker_start, log_path, deadline_unix):
    return [sys.executable, plan["guard_script"], "--worker-start", str(worker_start),
            "--reservation-pid", str(plan["reservation_pid"]),
            "--reservation-ticks", str(plan["reservation_start_ticks"]),
            "--gpu-index", str(plan["configured_gpu_index"]),
            "--minimum-free-mib", str(plan["minimum_free_mib"]),
            "--minimum-disk-gib", str(plan["minimum_disk_gib"]),
            "--disk-path", plan["disk_path"], "--log", str(log_path),
            "--deadline-unix", str(deadline_unix)]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--plan", required=True, type=Path)
    parser.add_argument("--plan-sha256", required=True)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    plan_bytes = args.plan.read_bytes()
    digest = hashlib.sha256(plan_bytes).hexdigest()
    if digest != args.plan_sha256.lower():
        raise ValueError("plan SHA-256 mismatch")
    plan = json.loads(plan_bytes)
    check_plan(plan)
    if args.dry_run:
        print(json.dumps({"status": "VALIDATED_NO_GPU_START", "plan_sha256": digest,
                          "stage_id": plan["stage_id"]}, sort_keys=True))
        return 0

    out = Path(plan["output_dir"])
    out.mkdir(parents=True, exist_ok=False)
    env = os.environ.copy()
    env.update(plan.get("env", {}))
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    start_unix_ns = time.time_ns()
    stage_start_monotonic = time.monotonic()
    worker = None
    start = None
    pgid = None
    guard = None
    result = {"stage_id": plan["stage_id"], "plan_sha256": digest,
              "start_unix_ns": start_unix_ns, "status": "STARTED",
              "reservation_pid": plan["reservation_pid"],
              "reservation_start_ticks": plan["reservation_start_ticks"],
              "reserve_deadline_unix": plan["reserve_deadline_unix"]}
    try:
        with (out / "worker.stdout").open("xb") as stdout, (out / "worker.stderr").open("xb") as stderr:
            worker = subprocess.Popen(plan["command"], cwd=plan["cwd"], env=env,
                                      stdin=subprocess.DEVNULL, stdout=stdout, stderr=stderr,
                                      start_new_session=True)
            start = ticks(worker.pid)
            pgid = os.getpgid(worker.pid)
            if pgid != worker.pid:
                raise RuntimeError("worker is not its process-group leader")
            write_new(out / "worker-start.json", {"pid": worker.pid, "pgid": pgid,
                      "start_ticks": start, "start_unix_ns": time.time_ns(),
                      "command": plan["command"], "cwd": plan["cwd"]})
            guard_cmd = guard_command(plan, out / "worker-start.json",
                        out / "resource-guard.jsonl",
                        min(plan["reserve_deadline_unix"],
                            start_unix_ns // 1_000_000_000 + plan["outer_seconds"]))
            with (out / "guard.stdout").open("xb") as guard_out, (out / "guard.stderr").open("xb") as guard_err:
                guard = subprocess.Popen(guard_cmd, stdin=subprocess.DEVNULL,
                                         stdout=guard_out, stderr=guard_err,
                                         start_new_session=True)
                result["worker"] = {"pid": worker.pid, "start_ticks": start, "pgid": pgid}
                result["guard"] = {"pid": guard.pid, "start_ticks": ticks(guard.pid)}
                result["status"] = "RUNNING"
                write_new(out / "controller-start.json", result)
                worker_deadline = stage_start_monotonic + plan["worker_seconds"]
                outer_deadline = stage_start_monotonic + plan["outer_seconds"]
                stop_reason = None
                while worker.poll() is None:
                    if guard.poll() is not None:
                        stop_reason = f"resource_guard_exit_{guard.returncode}"
                        break
                    if time.monotonic() >= worker_deadline:
                        stop_reason = "worker_timeout"
                        break
                    if time.monotonic() >= outer_deadline:
                        stop_reason = "outer_timeout"
                        break
                    time.sleep(1)
                if stop_reason and worker.poll() is None:
                    result["stop_action"] = stop_owned(worker.pid, start, pgid)
                try:
                    worker.wait(timeout=max(1, outer_deadline - time.monotonic()))
                except subprocess.TimeoutExpired:
                    result["stop_action"] = stop_owned(worker.pid, start, pgid)
                    worker.wait(timeout=15)
                    stop_reason = stop_reason or "outer_timeout"
                if guard.poll() is None:
                    try:
                        guard.wait(timeout=min(20, max(0.1, outer_deadline - time.monotonic())))
                    except subprocess.TimeoutExpired:
                        guard.terminate()
                        try:
                            guard.wait(timeout=5)
                        except subprocess.TimeoutExpired:
                            guard.kill()
                            guard.wait(timeout=5)
                guard_events = []
                guard_log = out / "resource-guard.jsonl"
                if guard_log.is_file():
                    for line in guard_log.read_text(encoding="utf-8").splitlines():
                        guard_events.append(json.loads(line).get("event"))
                safety_events = {"OWNED_WORKER_TERM", "OWNED_WORKER_KILL_AFTER_TERM",
                                 "IDENTITY_CHANGED_NO_SIGNAL"}
                guard_clean = guard.returncode == 0 and "START" in guard_events and (
                    "WORKER_EXITED" in guard_events) and not safety_events.intersection(guard_events)
                process_ok = worker.returncode == 0 and not stop_reason and guard_clean
                result.update({"status": "PROCESS_RC0_PENDING_RESULT_VALIDATION" if process_ok else "FAILED",
                               "worker_returncode": worker.returncode,
                               "guard_returncode": guard.returncode,
                               "guard_events": guard_events,
                               "guard_clean": guard_clean,
                               "stop_reason": stop_reason,
                               "finish_unix_ns": time.time_ns()})
                write_new(out / "controller-finish.json", result)
                return 0 if process_ok else 1
    except BaseException as error:
        if worker is not None and worker.poll() is None and start is not None and pgid is not None:
            result["stop_action"] = stop_owned(worker.pid, start, pgid)
        if guard is not None and guard.poll() is None:
            guard.terminate()
        result.update({"status": "CONTROLLER_ERROR", "error": repr(error),
                       "finish_unix_ns": time.time_ns()})
        write_new(out / "controller-error.json", result)
        raise


if __name__ == "__main__":
    raise SystemExit(main())
