"""Host-only CPU domains; no thread-count, service, or device changes.

The owner discovers four backend physical cores using the unchanged normal
selector. All domains are subsets of the actual allowed mask; they are shared
affinity budgets and do not reserve CPUs or alter an external service.
"""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path


def expand(value):
    result = set()
    for item in value.split(","):
        start, separator, end = item.partition("-")
        result.update(range(int(start), int(end if separator else start) + 1))
    return result


def ticks(pid):
    return int(Path(f"/proc/{pid}/stat").read_text(encoding="utf-8").rsplit(")", 1)[1].split()[19])


def extend_roles(roles):
    allowed = set(roles["allowed"])
    backend = set().union(*(expand(row["siblings"]) for row in roles["selected"])) & allowed
    numa = tuple(roles["selected"][0]["numa"])
    remaining = []
    for cpu in sorted(allowed - backend):
        root = Path(f"/sys/devices/system/cpu/cpu{cpu}")
        if tuple(sorted(p.name for p in root.glob("node[0-9]*"))) == numa:
            remaining.append(cpu)
    if not remaining:
        raise RuntimeError("no independent allowed controller CPU in backend NUMA node")
    controller_cpu = remaining[0]
    controller_siblings = expand(Path(f"/sys/devices/system/cpu/cpu{controller_cpu}/topology/thread_siblings_list").read_text(encoding="utf-8").strip()) & allowed
    framework = set(remaining) - controller_siblings
    if not framework or framework & backend or framework & controller_siblings:
        raise RuntimeError("independent framework/backend/controller CPU domains unavailable")
    roles["host_role_domains"] = {
        "schema": "hbfsim.normal_model_host_role_domains.v1",
        "initial_target_allowed": sorted(allowed),
        "backend_physical_and_smt": sorted(backend),
        "controller_cpu": controller_cpu,
        "controller_physical_and_smt": sorted(controller_siblings),
        "framework_after_backend_prepare": sorted(framework),
        "numa": list(numa),
        "policy": "preserve all allowed same-NUMA framework CPUs except backend/controller physical cores and SMT siblings; preserve OMP/torch parameters; shared affinity, no exclusive reservation",
        "external_service_affinity_changed": False,
        "other_numa_allowed_not_used_after_prepare": sorted(allowed - set(remaining) - backend),
    }
    return roles


def read_roles(env):
    path = Path(env["HBFSIM_NORMAL_ROLE_CPUS_FILE"])
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != env["HBFSIM_NORMAL_ROLE_CPUS_SHA256"]:
        raise RuntimeError("host role file identity mismatch")
    roles = json.loads(raw)
    domain = roles["host_role_domains"]
    if domain["schema"] != "hbfsim.normal_model_host_role_domains.v1":
        raise RuntimeError("unknown host role schema")
    allowed = set(roles["allowed"])
    framework = set(domain["framework_after_backend_prepare"])
    backend = set(domain["backend_physical_and_smt"])
    controller = set(domain["controller_physical_and_smt"])
    if not framework or not (framework | backend | controller) <= allowed or framework & (backend | controller) or backend & controller:
        raise RuntimeError("overlapping or invalid role masks")
    if domain["controller_cpu"] not in controller or set(domain["initial_target_allowed"]) != allowed:
        raise RuntimeError("initial target/controller role mismatch")
    return roles


def place_controller(env):
    roles = read_roles(env)
    pid = os.getpid()
    start = ticks(pid)
    allowed = set(os.sched_getaffinity(0))
    if not set(roles["allowed"]) <= allowed:
        raise RuntimeError("controller inherited mask excludes selected startup CPUs")
    tasks = sorted(int(p.name) for p in Path(f"/proc/{pid}/task").iterdir())
    # The original controller is single-threaded before subprocess creation.
    if tasks != [pid]:
        raise RuntimeError("controller affinity setup requires original single-threaded caller")
    cpu = roles["host_role_domains"]["controller_cpu"]
    os.sched_setaffinity(pid, {cpu})
    if os.sched_getaffinity(pid) != {cpu} or ticks(pid) != start:
        raise RuntimeError("controller affinity/identity changed")
    return {"pid": pid, "start_ticks": start, "task_affinity": {str(pid): [cpu]},
            "target_initial_allowed": roles["host_role_domains"]["initial_target_allowed"]}


def place_framework(roles, target_start):
    domain = roles["host_role_domains"]
    pid = os.getpid()
    if ticks(pid) != target_start:
        raise RuntimeError("model target identity changed before framework placement")
    mask = set(domain["framework_after_backend_prepare"])
    if not mask <= set(os.sched_getaffinity(0)):
        raise RuntimeError("model inherited mask excludes framework domain")
    tasks = sorted(int(p.name) for p in Path(f"/proc/{pid}/task").iterdir())
    before = {str(tid): sorted(os.sched_getaffinity(tid)) for tid in tasks}
    for tid in tasks:
        os.sched_setaffinity(tid, mask)
    after = {str(tid): sorted(os.sched_getaffinity(tid)) for tid in tasks}
    if any(set(value) != mask for value in after.values()) or ticks(pid) != target_start:
        raise RuntimeError("framework task affinity/identity verification failed")
    return {"pid": pid, "start_ticks": target_start, "task_affinity_before": before,
            "task_affinity_after": after, "domain": domain,
            "boundary": "after original backend pin/control validation, before normal generate timer",
            "threads_spawned_after_receipt": "inherit spawning task mask; actual future task census separately captured; no fabricated universal thread census"}
