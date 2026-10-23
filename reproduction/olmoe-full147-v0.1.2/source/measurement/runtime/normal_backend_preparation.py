"""Bounded host preparation inside the loaded model worker, before generate.

No diagnostic getter, device/helper modification, or polling loop. The exact
DIAG_N profile additionally admits the stable pre-existing diagnostic magic. The control
header is read through the exact owned daemon fd. Runtime CPU placement applies
only to the daemon and its backend workers, before the measured generation.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import struct
import time
from cpu_role_domains import extend_roles, place_framework


import runpy as _diag_runpy
_DIAG = _diag_runpy.run_path(str(Path(__file__).resolve().parents[1] / "diag_profile.py"))

def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def ticks(pid):
    return int(Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[19])


def child_ids(pid):
    result = set()
    for task in Path(f"/proc/{pid}/task").iterdir():
        result.update(int(x) for x in (task / "children").read_text().split())
    return sorted(result)


def identify(pid, expected_path, expected_sha):
    start = ticks(pid)
    proc = Path(f"/proc/{pid}")
    executable = (proc / "exe").resolve(strict=True)
    expected = Path(expected_path).resolve(strict=True)
    if executable != expected or sha(executable) != expected_sha:
        raise RuntimeError("backend executable identity differs")
    command = [x.decode() for x in (proc / "cmdline").read_bytes().split(b"\0") if x]
    if ticks(pid) != start:
        raise RuntimeError("backend PID/start changed")
    return {"pid": pid, "start_ticks": start, "exe": str(executable),
            "exe_sha256": expected_sha, "cmdline": command}


def discover_cpu_roles():
    allowed = sorted(os.sched_getaffinity(0))
    groups = {}
    for cpu in allowed:
        root = Path(f"/sys/devices/system/cpu/cpu{cpu}")
        topology = root / "topology"
        core = (topology / "physical_package_id").read_text().strip() + ":" + (topology / "core_id").read_text().strip()
        l3 = (root / "cache/index3/shared_cpu_list").read_text().strip()
        numa = tuple(sorted(p.name for p in root.glob("node[0-9]*")))
        row = {"cpu": cpu, "physical_core": core, "l3": l3, "numa": numa,
               "siblings": (topology / "thread_siblings_list").read_text().strip()}
        groups.setdefault((l3, numa), {}).setdefault(core, row)
    eligible = [list(cores.values()) for cores in groups.values() if len(cores) >= 4]
    if not eligible:
        raise RuntimeError("no allowed same-L3/NUMA four-physical-core deployment")
    def cpu_ticks():
        return {int(parts[0][3:]): [int(x) for x in parts[1:9]]
                for line in Path("/proc/stat").read_text().splitlines()
                if (parts := line.split()) and parts[0].startswith("cpu") and parts[0][3:].isdigit()}

    def expand(mask):
        result = set()
        for part in mask.split(","):
            start, _, end = part.partition("-")
            result.update(range(int(start), int(end or start) + 1))
        return result

    start_ns = time.monotonic_ns()
    before = cpu_ticks()
    time.sleep(1)
    after = cpu_ticks()
    end_ns = time.monotonic_ns()
    usage = {}
    for cpu in before.keys() & after.keys():
        delta = [b - a for a, b in zip(before[cpu], after[cpu])]
        if any(x < 0 for x in delta):
            raise RuntimeError("CPU tick snapshot reset")
        total = sum(delta)
        busy = total - delta[3] - delta[4]
        usage[cpu] = {"delta_ticks": delta, "busy_fraction": busy / total if total else None}

    def score(row):
        return max(usage[cpu]["busy_fraction"] or 0 for cpu in expand(row["siblings"]))

    ranked = [sorted(rows, key=lambda row: (score(row), row["cpu"]))[:4] for rows in eligible]
    chosen = min(ranked, key=lambda rows: (sum(score(row) for row in rows), tuple(row["cpu"] for row in rows)))
    observed = set().union(*(expand(row["siblings"]) for row in chosen))
    processes = []
    for proc in Path("/proc").iterdir():
        if not proc.name.isdigit():
            continue
        try:
            process_start = ticks(int(proc.name))
            mask = sorted(os.sched_getaffinity(int(proc.name)))
            if not observed.intersection(mask):
                continue
            command = [x.decode(errors="replace") for x in (proc / "cmdline").read_bytes().split(b"\0") if x]
            if command and ticks(int(proc.name)) == process_start:
                processes.append({"pid": int(proc.name), "start_ticks": process_start,
                                  "allowed_cpus": mask, "cmdline": command})
        except (FileNotFoundError, ProcessLookupError, PermissionError):
            continue
    return extend_roles({"allowed": allowed, "selected": chosen,
            "idle_snapshot": {"begin_monotonic_ns": start_ns, "end_monotonic_ns": end_ns,
                              "selected_and_smt": {str(cpu): usage[cpu] for cpu in sorted(observed)},
                              "processes_allowed_on_selected_or_smt": processes},
            "selection_scope": "same L3 and NUMA, distinct physical cores; choose least one-second busy group including SMT; no hard utilization threshold or exclusive CPU reservation"})


def prepare(model):
    if getattr(model, "_hbfsim_timing_session", None) is None:
        raise RuntimeError("no loaded worker timing session")
    import torch
    torch.cuda.synchronize()
    expected_phase = _DIAG["worker_phase"](os.environ)
    backend = os.environ["HBFSIM_NORMAL_BACKEND"]
    if backend not in ("ucie", "legacy_hbf"):
        raise RuntimeError("unknown normal backend role")
    role_file = Path(os.environ["HBFSIM_NORMAL_ROLE_CPUS_FILE"])
    if sha(role_file) != os.environ["HBFSIM_NORMAL_ROLE_CPUS_SHA256"]:
        raise RuntimeError("runtime CPU role file identity changed")
    roles = json.loads(role_file.read_bytes())
    cpus = [row["cpu"] for row in roles["selected"]]
    if len(cpus) != 4 or len(set(cpus)) != 4 or not set(cpus) <= set(os.sched_getaffinity(0)):
        raise RuntimeError("runtime backend CPU roles invalid")
    pid = os.getpid()
    target_start = ticks(pid)
    daemon_path = Path(os.environ["HBFSIM_DAEMON_PATH"]).resolve(strict=True)
    candidates = []
    for child in child_ids(pid):
        try:
            if Path(f"/proc/{child}/exe").resolve(strict=True) == daemon_path:
                candidates.append(child)
        except FileNotFoundError:
            continue
    if len(candidates) != 1:
        raise RuntimeError("expected one direct owned timing daemon")
    daemon = identify(candidates[0], daemon_path, os.environ["HBFSIM_NORMAL_DAEMON_SHA256"])
    workers = []
    if backend == "ucie":
        for child in child_ids(daemon["pid"]):
            row = identify(child, os.environ["HBFSIM_UCIE_WORKER"],
                           os.environ["HBFSIM_NORMAL_WORKER_SHA256"])
            index = row["cmdline"].index("--stack")
            row["stack"] = int(row["cmdline"][index + 1])
            workers.append(row)
        workers.sort(key=lambda row: row["stack"])
        if [row["stack"] for row in workers] != [1, 2, 3]:
            raise RuntimeError("expected three owned backend workers and local stack zero")
    elif child_ids(daemon["pid"]):
        raise RuntimeError("legacy HBF daemon unexpectedly owns workers")
    for stack, row in [(0, daemon)] + [(row["stack"], row) for row in workers]:
        if ticks(row["pid"]) != row["start_ticks"]:
            raise RuntimeError("backend exited before pre-generation CPU placement")
        tasks = sorted(int(task.name) for task in Path(f"/proc/{row['pid']}/task").iterdir())
        for tid in tasks:
            os.sched_setaffinity(tid, {cpus[stack]})
        row["task_affinity"] = {str(tid): sorted(os.sched_getaffinity(tid)) for tid in tasks}
        if any(mask != [cpus[stack]] for mask in row["task_affinity"].values()) or ticks(row["pid"]) != row["start_ticks"]:
            raise RuntimeError("backend affinity/PID verification failed")
    observer_source = Path(os.environ["HBFSIM_CONTROL_OBSERVER_SOURCE"])
    if sha(observer_source) != os.environ["HBFSIM_CONTROL_OBSERVER_SHA256"]:
        raise RuntimeError("control observer source identity changed")
    spec = importlib.util.spec_from_file_location("normal_control_source", observer_source)
    observer = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(observer)
    report_dir = Path(os.environ["HBFSIM_NORMAL_REPORT_DIR"]).resolve(strict=True)
    header = observer.control_header(pid, target_start, report_dir, daemon_path)
    if header.get("status") != "VERIFIED_READ_ONLY_HOST_HEADER" or (
        header["daemon_pid"], header["daemon_start_ticks"]) != (daemon["pid"], daemon["start_ticks"]):
        raise RuntimeError("no verified owned fresh control header")
    fd_path = Path(f"/proc/{daemon['pid']}/fd/{header['control_fd']}")
    descriptor = os.open(fd_path, os.O_RDONLY | os.O_CLOEXEC)
    try:
        stat = os.fstat(descriptor)
        first = os.pread(descriptor, 8, 368)
        second = os.pread(descriptor, 8, 368)
        from hbf_range_observation import capture as capture_settled_ranges
        registration=json.loads((report_dir/'registration.json').read_bytes())
        range_observation=capture_settled_ranges(descriptor,header,daemon,pid,target_start,ticks,registration)
        after = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    _DIAG["stable_phase"](first, second, expected_phase)
    if (stat.st_dev, stat.st_ino, stat.st_size) != (header["control_device"], header["control_inode"], header["region_bytes"]) or (
        stat.st_dev, stat.st_ino, stat.st_size) != (after.st_dev, after.st_ino, after.st_size) or first != second or first != expected_phase:
        raise RuntimeError("actual phase field/owned control identity is not stable or expected")
    maps = Path(f"/proc/{pid}/maps").read_text()
    device = f"{os.major(stat.st_dev):02x}:{os.minor(stat.st_dev):02x}"
    joined = [line for line in maps.splitlines() if len(line.split()) >= 6 and
              line.split()[3] == device and int(line.split()[4]) == stat.st_ino and
              int(line.split()[2], 16) == 0 and "/memfd:hbfsim-control (deleted)" in line]
    if len(joined) != 1 or ticks(pid) != target_start or ticks(daemon["pid"]) != daemon["start_ticks"]:
        raise RuntimeError("fresh control/target maps join failed")
    framework = place_framework(roles, target_start)
    receipt = {"schema": "hbfsim.normal_backend_pre_generation.v1", "status": "PASS",
               "backend": backend, "target_pid": pid, "target_start_ticks": target_start,
               "daemon": daemon, "workers": workers, "cpu_roles": roles, "framework_cpu_role": framework,
               "control_header": header, "range_observation": range_observation,
               "phase_field_offset": 368,
               "phase_field_first_hex": first.hex(), "phase_field_second_hex": second.hex(),
               "phase_field_value": struct.unpack("<Q", first)[0], "target_control_map": joined[0],
               "sample_unix_ns": time.time_ns(), "getter_called": False,
               "limit": "Focused stable host read before generate; loaded DSO identity is additionally enforced by owner capture."}
    pending = report_dir / "normal-backend-pre-generation.json.pending"
    final = report_dir / "normal-backend-pre-generation.json"
    with pending.open("x", encoding="utf-8") as output:
        json.dump(receipt, output, indent=2, sort_keys=True)
        output.write("\n")
        output.flush()
        os.fsync(output.fileno())
    # Publish complete closed JSON exclusively; an existing final is an error.
    os.link(pending, final)
    pending.unlink()
    return receipt
