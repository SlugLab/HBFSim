#!/usr/bin/env python3
"""Strict process-level default-vs-isolated maintenance-off equivalence check."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import random
import subprocess
import sys


MAINTENANCE_ONLY_KEYS = {
    "maintenance_backend", "maintenance_data_semantics",
    "maintenance_payload_validation", "maintenance_events",
    "maintenance_completions", "maintenance_issued", "maintenance_completed",
    "pending_maintenance", "maintenance_request_id", "maintenance_parent_id",
}
DEFAULT_ENGINE_PATHS = (
    "CMakeLists.txt", "benchmarks/replay/hbf_mqsim_service.cpp",
    "include/hbfsim/mqsim_online.hpp", "src/mqsim_adapter/mqsim_online.cpp",
)


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def without_maintenance(value):
    if isinstance(value, dict):
        return {key: without_maintenance(item) for key, item in value.items()
                if key not in MAINTENANCE_ONLY_KEYS and not key.startswith("maintenance_")}
    if isinstance(value, list):
        return [without_maintenance(item) for item in value]
    return value


def workload(page_bytes, stack_ids, seed):
    rng = random.Random(seed)
    rows = []
    order = [(stack, local) for local in range(3) for stack in stack_ids]
    rng.shuffle(order)
    for request_id, (stack, local) in enumerate(order, 1):
        rows.append({"request_id": request_id,
                     "issue_ns": (request_id-1)//len(stack_ids) * 1_000,
                     "bytes": page_bytes, "operation": "read",
                     "stack": stack, "route": "direct",
                     "stack_local_page": local})
    return sorted(rows, key=lambda row: (row["issue_ns"], row["request_id"]))


def run_service(client_class, binary, profile, stack_map, directory, artifact_root, requests):
    with client_class(binary, profile, directory, timeout=30, artifact_root=artifact_root,
                      stack_map=stack_map, native_observations=True) as service:
        header = without_maintenance(service.header)
        for request in requests:
            target = request["issue_ns"]
            while service.now < target:
                service.until(target)
            if service.now != target:
                raise AssertionError("service did not stop at the fixed issue horizon")
            service.submit(request)
        while len(service.completions) != len(requests):
            service.until(service.now + 10_000_000)
        receipt = service.finish()
        return {
            "header": header,
            "requests": {str(key): value for key, value in sorted(service.requests.items())},
            "completions": {str(key): value for key, value in sorted(service.completions.items())},
            "events": without_maintenance(service.observations),
            "native_command_events": without_maintenance(service.native_observations),
            "receipt": without_maintenance(receipt),
        }


def link_provenance(root, default_binary, isolated_binary):
    default_link = default_binary.parent / "CMakeFiles/hbf_mqsim_service.dir/link.txt"
    isolated_ninja = isolated_binary.parents[1] / "build.ninja"
    if not default_link.is_file() or not isolated_ninja.is_file():
        raise ValueError("link provenance files are missing")
    default_command = default_link.read_text().strip()
    if default_command.split().count("libmqsim_hbf.a") != 1:
        raise AssertionError("default service must link exactly one default MQSim engine archive")
    lines = isolated_ninja.read_text().splitlines()
    marker = "build bin/hbf_mqsim_eq3_maint:"
    index = next((i for i, line in enumerate(lines) if line.startswith(marker)), None)
    if index is None:
        raise AssertionError("isolated service target missing from build graph")
    link_line = next((line.strip() for line in lines[index:index+20]
                      if line.strip().startswith("LINK_LIBRARIES =")), None)
    if link_line is None:
        raise AssertionError("isolated service link libraries are missing")
    libraries = link_line.split("=", 1)[1].split()
    if libraries.count("lib/libmqsim_eq3_maint.a") != 1:
        raise AssertionError("isolated service must link exactly one isolated MQSim engine archive")
    default_diff = subprocess.run(
        ["git", "-C", str(root), "diff", "--exit-code", "HEAD", "--", *DEFAULT_ENGINE_PATHS],
        text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
    if default_diff.returncode != 0:
        raise AssertionError("default MQSim/service sources differ from HEAD")
    status = subprocess.run(
        ["git", "-C", str(root), "status", "--porcelain=v1"], text=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True).stdout.splitlines()
    return {
        "default_binary": str(default_binary), "default_binary_sha256": sha256(default_binary),
        "isolated_binary": str(isolated_binary), "isolated_binary_sha256": sha256(isolated_binary),
        "default_link_command": default_command,
        "default_link_sha256": sha256(default_link),
        "isolated_link_libraries": libraries,
        "isolated_build_ninja_sha256": sha256(isolated_ninja),
        "single_engine_archive_per_process": True,
        "default_engine_source_paths": list(DEFAULT_ENGINE_PATHS),
        "default_engine_sources_match_head": True,
        "git_status_porcelain": status,
        "ldd": {
            "default": subprocess.run(["ldd", str(default_binary)], text=True,
                                      stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                      check=True).stdout.splitlines(),
            "isolated": subprocess.run(["ldd", str(isolated_binary)], text=True,
                                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                       check=True).stdout.splitlines(),
        },
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--default-binary", type=Path, required=True)
    parser.add_argument("--isolated-binary", type=Path, required=True)
    parser.add_argument("--profile", type=Path, required=True)
    parser.add_argument("--stack-map", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--seed", type=int, default=20260920)
    args = parser.parse_args()
    root = args.root.resolve(strict=True)
    default_binary = args.default_binary.resolve(strict=True)
    isolated_binary = args.isolated_binary.resolve(strict=True)
    profile = args.profile.resolve(strict=True)
    stack_map = args.stack_map.resolve(strict=True)
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    sys.path.insert(0, str(root / "scripts/eval"))
    from mqsim_service import MqsimService

    profile_value = json.loads(profile.read_text())
    map_value = json.loads(stack_map.read_text())
    stack_ids = [row["id"] for row in map_value["stacks"]]
    requests = workload(profile_value["page_bytes"], stack_ids, args.seed)
    provenance = link_provenance(root, default_binary, isolated_binary)
    a = run_service(MqsimService, default_binary, profile, stack_map,
                    output / "default-service", root.parents[2], requests)
    b = run_service(MqsimService, isolated_binary, profile, stack_map,
                    output / "isolated-service", root.parents[2], requests)
    (output / "default-result.json").write_text(
        json.dumps(a, indent=2, sort_keys=True, allow_nan=False) + "\n")
    (output / "isolated-result.json").write_text(
        json.dumps(b, indent=2, sort_keys=True, allow_nan=False) + "\n")
    if a != b:
        keys = [key for key in a if a[key] != b[key]]
        failure = {"status": "FAIL", "differing_sections": keys,
                   "comparison": "STRICT_EXCEPT_EXPLICIT_MAINTENANCE_ONLY_FIELDS"}
        (output / "summary.json").write_text(json.dumps(failure, indent=2) + "\n")
        raise AssertionError(f"default/isolated foreground mismatch: {keys}")
    result = {
        "schema_version": "eq3-maintenance-process-ab-v1", "status": "PASS",
        "comparison": "STRICT_EXCEPT_EXPLICIT_MAINTENANCE_ONLY_FIELDS",
        "time_tolerance_ns": 0, "workload_seed": args.seed,
        "request_count": len(requests),
        "compared": ["request_and_raw_and_reported_completion", "native_phase_order",
                     "native_physical_address", "existing_service_statistics"],
        "ignored_fields": sorted(MAINTENANCE_ONLY_KEYS),
        "profile": str(profile), "profile_sha256": sha256(profile),
        "stack_map": str(stack_map), "stack_map_sha256": sha256(stack_map),
        "provenance": provenance,
    }
    (output / "summary.json").write_text(
        json.dumps(result, indent=2, sort_keys=True, allow_nan=False) + "\n")
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
