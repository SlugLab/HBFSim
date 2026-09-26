#!/usr/bin/env python3
"""Finite resource guard for one HBFSim worker process group."""

import argparse
import json
import os
import shutil
import signal
import subprocess
import time
from pathlib import Path


def ticks(pid):
    return int(Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[19])


def gpu_state(index):
    output = subprocess.run(
        ["nvidia-smi", "-i", str(index), "--query-gpu=uuid,memory.total,memory.free", "--format=csv,noheader,nounits"],
        check=True, capture_output=True, text=True, timeout=10,
    ).stdout.strip().splitlines()
    if len(output) != 1:
        raise RuntimeError("configured GPU query did not identify one selected device")
    uuid, total, free = [value.strip() for value in output[0].split(",")]
    return uuid, int(total), int(free)


def compute_pids(index):
    result = subprocess.run(
        ["nvidia-smi", "-i", str(index), "--query-compute-apps=pid", "--format=csv,noheader,nounits"],
        check=True, capture_output=True, text=True, timeout=10,
    )
    return {int(line.strip()) for line in result.stdout.splitlines() if line.strip().isdigit()}

def wrong_device_worker_pids(selected_uuid, worker_pgid, getpgid=os.getpgid):
    result = subprocess.run(
        ["nvidia-smi", "--query-compute-apps=gpu_uuid,pid", "--format=csv,noheader,nounits"],
        check=True, capture_output=True, text=True, timeout=10,
    )
    wrong = set()
    for line in result.stdout.splitlines():
        parts = [part.strip() for part in line.split(",")]
        if len(parts) != 2 or not parts[1].isdigit(): continue
        pid = int(parts[1])
        try:
            if getpgid(pid) == worker_pgid and parts[0] != selected_uuid: wrong.add(pid)
        except ProcessLookupError:
            continue
    return wrong


def mem_available():
    rows = {}
    for line in Path("/proc/meminfo").read_text().splitlines():
        parts = line.split()
        rows[parts[0].rstrip(":")] = int(parts[1])
    return rows["MemTotal"], rows["MemAvailable"]


def owner_group(pid, expected_ticks, expected_pgid):
    try:
        return ticks(pid) == expected_ticks and os.getpgid(pid) == expected_pgid
    except (FileNotFoundError, ProcessLookupError):
        return False


def log(path, event):
    with path.open("a", encoding="utf-8") as output:
        output.write(json.dumps({"unix_ns": time.time_ns(), **event}, sort_keys=True) + "\n")


def stop_owned_worker(pid, start, pgid, path, reason):
    if not owner_group(pid, start, pgid):
        log(path, {"event": "IDENTITY_CHANGED_NO_SIGNAL", "reason": reason})
        return
    os.killpg(pgid, signal.SIGTERM)
    log(path, {"event": "OWNED_WORKER_TERM", "reason": reason, "worker_pid": pid})
    for _ in range(10):
        time.sleep(1)
        if not owner_group(pid, start, pgid):
            return
    if owner_group(pid, start, pgid):
        os.killpg(pgid, signal.SIGKILL)
        log(path, {"event": "OWNED_WORKER_KILL_AFTER_TERM", "reason": reason,
                   "worker_pid": pid})


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--worker-start", required=True, type=Path)
    parser.add_argument("--reservation-pid", required=True, type=int)
    parser.add_argument("--reservation-ticks", required=True, type=int)
    parser.add_argument("--gpu-index", required=True, type=int)
    parser.add_argument("--minimum-free-mib", required=True, type=int)
    parser.add_argument("--minimum-disk-gib", required=True, type=int)
    parser.add_argument("--disk-path", required=True, type=Path)
    parser.add_argument("--log", required=True, type=Path)
    parser.add_argument("--deadline-unix", required=True, type=int)
    parser.add_argument("--sample-seconds", type=int, default=5)
    args = parser.parse_args()
    if args.sample_seconds != 5 or args.gpu_index < 0 or args.minimum_free_mib <= 0 or args.minimum_disk_gib <= 0:
        raise ValueError("invalid configured guard resource bounds")
    if args.deadline_unix <= time.time() or not args.disk_path.is_dir():
        raise ValueError("invalid guard deadline or disk path")
    if ticks(args.reservation_pid) != args.reservation_ticks:
        raise RuntimeError("reservation identity mismatch")
    if not args.worker_start.is_file():
        raise RuntimeError("worker-start receipt missing")
    receipt = json.loads(args.worker_start.read_text())
    pid, pgid, start = (int(receipt[key]) for key in ("pid", "pgid", "start_ticks"))
    if pid != pgid or not owner_group(pid, start, pgid):
        raise RuntimeError("worker identity mismatch")
    args.log.parent.mkdir(parents=True, exist_ok=True)
    bad_count = 0
    log(args.log, {"event": "START", "worker_pid": pid, "worker_ticks": start,
                   "reservation_pid": args.reservation_pid, "deadline_unix": args.deadline_unix,
                   "configured_gpu_index": args.gpu_index})
    while time.time() < args.deadline_unix:
        if not owner_group(pid, start, pgid):
            log(args.log, {"event": "WORKER_EXITED"})
            return
        try:
            if ticks(args.reservation_pid) != args.reservation_ticks:
                raise RuntimeError("reservation identity changed")
            uuid, total, free = gpu_state(args.gpu_index)
            total_mem, available_mem = mem_available()
            disk_free = shutil.disk_usage(args.disk_path).free
            apps = compute_pids(args.gpu_index)
            wrong_device = wrong_device_worker_pids(uuid, pgid)
            unexpected = {app for app in apps if app != args.reservation_pid
                          and (not Path(f"/proc/{app}").exists() or os.getpgid(app) != pgid)}
            reasons = []
            if free < max(args.minimum_free_mib, int(total * 0.05) + 1):
                reasons.append("vram")
            if available_mem < int(total_mem * 0.05) + 1:
                reasons.append("ram")
            if disk_free < args.minimum_disk_gib * 1024**3:
                reasons.append("disk")
            if unexpected:
                reasons.append("unexpected_gpu_pid")
            if wrong_device:
                reasons.append("worker_wrong_gpu")
            observation = {"event": "SAMPLE", "observed_gpu_uuid": uuid,
                           "configured_gpu_index": args.gpu_index,"free_vram_mib": free,
                           "available_ram_kib": available_mem, "free_disk_bytes": disk_free,
                           "unexpected_gpu_pids": sorted(unexpected),
                           "worker_wrong_gpu_pids": sorted(wrong_device), "reasons": reasons}
        except (OSError, RuntimeError, subprocess.SubprocessError, ValueError) as error:
            reasons = ["observation_error"]
            observation = {"event": "SAMPLE_ERROR", "error": repr(error), "reasons": reasons}
        bad_count = bad_count + 1 if reasons else 0
        if reasons or bad_count == 0:
            log(args.log, observation)
        if bad_count >= 2:
            stop_owned_worker(pid, start, pgid, args.log, ",".join(reasons))
            return
        time.sleep(args.sample_seconds)
    stop_owned_worker(pid, start, pgid, args.log, "guard_deadline")


if __name__ == "__main__":
    main()
