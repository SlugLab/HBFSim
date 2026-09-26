#!/usr/bin/env python3
"""CPU-only Li6 admission preflight.

This is deliberately separate from the archived unlaunchable draft launcher.
It proves the actual controller/guard paths, constructs the successful base
environment, and checks the two Li6 selectors against the immutable 147-storage
native inventory.  It never calls nvidia-smi, creates a GPU owner, or starts a
worker.  The launch-time target capture contract is included so the eventual
root-reviewed launcher records the real target PID/start/PGID, maps and
filtered environment after the target libraries are loaded.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import sys
import time


ROOT = Path(__file__).resolve().parents[2]
WS = ROOT.parent
TASK = ROOT / "task/all-weights-category-representatives-v1"
SPEC = TASK / "LI6_RUNTIME_SPEC.json"
BASE_CONFIG = ROOT / "task/qkv-model-combined-v3/config-v1/runtime-config.pre-resource-combined.json"
BASE_REPRO = ROOT / "task/qkv-model-combined-v1/reproduce_combined_v1.py"
INVENTORY = ROOT / "runs/all-weights-discovery-v2/result/observer"
BUNDLE = TASK / "bundle-li6-v1"
PLUGIN = ROOT / "task/new-category-li6-adapter-v1"
CONTROLLER = ROOT / "task/run_bounded_gpu_stage.py"
GUARD = ROOT / "task/all-weights-discovery-run-v1/resource_guard.py"
PROVIDER = ROOT / "task/qkv-model-dispatch-repair-v1/build-provider-v1/libprovider_qkv_production.so"
SOURCE = ROOT / "task/qkv-output-repair-v1/cpu-build-v1/qkv.quarantine.ptx"
STAGE = ROOT / "task/qkv-hbf-replay-v1/stage-v1"
STAGED = STAGE / (hashlib.sha256(SOURCE.read_bytes()).hexdigest() + ".ptx")
WRAPPER = ROOT / "task/qkv-model-combined-v1/run_with_bpftime_combined.sh"
RUNNER = ROOT / "workspace-freeze-copy-v5/B-weight-binding-config-source-v1/adapters/vllm/run.py"
MODEL = WS / "model/OLMoE-1B-7B-0924-6d84c485"

SELECTORS = {
    "o_proj_layer0": (re.compile(r"^model\.layers\.0\.self_attn\.o_proj\.weight$"), 8388608),
    "lm_head": (re.compile(r"^lm_head\.weight$"), 206045184),
}
ALLOWED_ENV_PREFIXES = ("HBFSIM_", "BPFTIME_")
ALLOWED_ENV_NAMES = {
    "PATH", "PYTHONPATH", "LD_LIBRARY_PATH", "LD_PRELOAD", "TMPDIR", "HF_HOME",
    "XDG_CACHE_HOME", "TORCH_HOME", "CUDA_CACHE_PATH", "TORCHINDUCTOR_CACHE_DIR",
    "TRITON_CACHE_DIR", "CUDA_HOME", "CUDA_PATH", "CUDACXX", "FLASHINFER_NVCC",
    "CUDA_VISIBLE_DEVICES", "CUDA_LAUNCH_BLOCKING", "PYTHONUNBUFFERED", "CC", "CXX",
    "CUDAHOSTCXX", "VLLM_TUNED_CONFIG_FOLDER",
}
PINNED = {
    BASE_CONFIG: "4b234507c86f302a72a7649ba61272ded2f157528a7f19e74647a44bad148b55",
    BASE_REPRO: "311d9c71548a26c684df4689b0619ae190db741d341ea38a5e34b292b769cce1",
    CONTROLLER: "adadc2369e344912645b9e798761942f7542632b86824959d40b99e233f822f6",
    GUARD: "11d9429aab086e3e0a1406619098a6dadbb6cd19ac1b2b0a3eef67da85a5b3e4",
    WRAPPER: "fff7b7b15207a89adc78fbf2124f287891b93c114c9cd3debf68e87f2d8fad50",
    RUNNER: "3815e3f655e9e559f668503b35637a3a5068c0c6970d3c4efcc6edc2f0e6ed33",
    PROVIDER: "b495ef888e5fda2605470e306a59c19a8f9b42a34eadb34e37fd2c4d16f551b0",
    SOURCE: "6db074711d19c3e31cbcc98c6e170f0b4330259f220c2d518ce8aee42b9c9eab",
    STAGED: "7219c1e8f58fdca32bc520a3a8ccdb1055aaccc33d12ff7935d7ef7baf786d67",
    PLUGIN / "li6_model_adapter/__init__.py": "9c7d5aa01d5f59204f5459ccf198e25836ba68709ef0891c8ba2a57c154dc849",
    BUNDLE / "BUNDLE_MANIFEST.json": "c88e569595264d9d84c640c1f136e32afc43f90f5285fdc31238384f843b9168",
    BUNDLE / "BUNDLE_RECEIPT.json": "d757a0743ea5ff880b06afc6d563d3946f7e73aa8a645229ccdaa16e293a989e",
}


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def check(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def save_new(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, indent=2, sort_keys=True)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())


def inventory_rows() -> tuple[list[dict], Path]:
    files = sorted(INVENTORY.glob("*.jsonl"))
    rows = []
    source = None
    for path in files:
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            if row.get("event") == "storage_inventory":
                check(source is None, "native inventory is ambiguous: multiple storage_inventory events")
                source = path
                rows = row["storages"]
                check(row.get("storage_count") == len(rows) == 147, "native inventory is not exactly 147 storages")
    check(source is not None, "native 147-storage inventory event is missing")
    check(len({tuple(a["name"] for a in r["aliases"]) for r in rows}) == len(rows),
          "native inventory aliases are not storage-deduplicated")
    return rows, source


def select_inventory(rows: list[dict]) -> dict:
    selected = {}
    for category, (pattern, expected_bytes) in SELECTORS.items():
        hits = [row for row in rows if any(pattern.fullmatch(a["name"]) for a in row["aliases"])]
        check(len(hits) == 1, f"{category}: selector matched {len(hits)} storages")
        row = hits[0]
        aliases = [a for a in row["aliases"] if pattern.fullmatch(a["name"])]
        check(len(aliases) == 1, f"{category}: selector matched {len(aliases)} aliases in one storage")
        check(row["storage_bytes"] == expected_bytes, f"{category}: byte size mismatch")
        selected[category] = {"alias": aliases[0]["name"], "bytes": row["storage_bytes"],
                              "storage_base": row["storage_base"], "shape": aliases[0]["shape"],
                              "dtype": aliases[0]["dtype"], "storage_count": len(hits)}
    check(sum(x["bytes"] for x in selected.values()) == 214433792, "Li6 storage total mismatch")
    return selected


def base_files(cfg: dict) -> dict[str, Path]:
    files = {key: (Path(row["path"]) if Path(row["path"]).is_absolute() else WS / row["path"])
             for key, row in cfg["artifacts"].items()}
    files.update({
        "agent": BUNDLE / "build/runtime/agent/libbpftime-agent.so",
        "gate": BUNDLE / "build/libhbfsim_launch_gate.so",
        "core": BUNDLE / "build/libhbfsim.so.0.1.0",
        "pass_plugin": BUNDLE / "build/libptxpass_hbf.so",
        "provider": PROVIDER,
        "qkv_worker_plugin": PLUGIN / "li6_model_adapter/__init__.py",
    })
    for name, path in files.items():
        check(path.is_file(), f"effective environment artifact missing: {name}: {path}")
    return files


def effective_environment(cfg: dict, files: dict[str, Path], out: Path) -> dict[str, str]:
    module_spec = importlib.util.spec_from_file_location("li6_base_repro", BASE_REPRO)
    module = importlib.util.module_from_spec(module_spec)
    assert module_spec.loader is not None
    module_spec.loader.exec_module(module)
    env = module.environment(cfg.copy(), files, WS, out, True)
    for key in ("HBFSIM_NATIVE_BINDING_MANIFEST_PATH", "HBFSIM_QKV_MODEL_PLUGIN_V1",
                "HBFSIM_QKV_MODEL_RECEIPT_DIR", "HBFSIM_QKV_ABI_MAP_SHA256",
                "HBFSIM_QKV_SOURCE_PTX_PATH", "HBFSIM_QKV_STAGED_PTX_PATH",
                "HBFSIM_QKV_STAGED_PTX_SHA256"):
        env.pop(key, None)
    env.update({
        "HBFSIM_BUILD_DIR": str(BUNDLE / "build"),
        "HBFSIM_BPFTIME_BUILD_DIR": str(BUNDLE / "build"),
        "HBFSIM_BPFTIME_PROBE": str(BUNDLE / "build/vllm_fused_moe_probe.bpf.o"),
        "HBFSIM_DAEMON_PATH": str(BUNDLE / "build/hbfsimd"),
        "BPFTIME_CUDA_LATE_PTX_DIR": str(STAGE),
        "HBFSIM_PRESTAGED_PASS_MANIFEST_PATH": str(STAGE / "pass-manifests.jsonl"),
        "HBFSIM_QKV_STAGED_PTX_PATH": str(STAGED),
        "HBFSIM_QKV_STAGED_PTX_SHA256": PINNED[STAGED],
        "HBFSIM_LI6_MODEL_PLUGIN_V1": "1",
        "HBFSIM_LI6_MODEL_RECEIPT_DIR": str(out / "li6-plugin"),
        "HBFSIM_LI6_REPRESENTATIVE_V1": "o_proj_layer0_and_lm_head",
        "HBFSIM_QKV_EPOCH": "6701",
        "HBFSIM_QKV_RECOVERED_ABI_V1": "1",
        "BPFTIME_CUDA_EXACT_BIND_ONLY": "0",
        "HBFSIM_QKV_SOURCE_PTX_PATH": str(SOURCE),
        "HBFSIM_QKV_ABI_MAP_SHA256": "20754dd201073fb033f724c0a61ee0177b39eb2c920807c870e5825136096ed9",
        "HBFSIM_CUDA12_FRAMEWORK_DOMAIN": "torch/vllm CUDA12.8 overlay",
        "HBFSIM_CUDA13_PRIVATE_DOMAIN": "agent/HBF private CUDA13 build",
        "HBFSIM_PROVIDER_TRACE_LOG": str(out / "logs/blas.jsonl"),
        "HBFSIM_PROVIDER_CORRELATION_LOG": str(out / "logs/correlation.jsonl"),
        "HBFSIM_PROVIDER_MODULE_DIR": str(out / "logs/provider-modules"),
        "HBFSIM_COVERAGE_PATH": str(out / "logs/coverage.jsonl"),
        "HBFSIM_PASS_MANIFEST_PATH": str(out / "logs/pass-manifests.jsonl"),
        "HBFSIM_NATIVE_REGISTRATION_LOG_PATH": str(out / "logs/native-registration.jsonl"),
        "HBFSIM_STRICT_BRIDGE_LOG_PATH": str(out / "logs/strict-bridge.jsonl"),
        "HBFSIM_STRICT_RUNTIME_LOG_PATH": str(out / "logs/strict-runtime.jsonl"),
        "HBFSIM_QKV_ABI_DECISION_LOG": str(out / "logs/li6-abi-decision.jsonl"),
        "LD_PRELOAD": "",
    })
    env.pop("VLLM_PLUGINS", None)
    check(env["HBFSIM_QKV_RECOVERED_ABI_V1"] == "1" and env["BPFTIME_CUDA_EXACT_BIND_ONLY"] == "0",
          "Li6 ABI/fallback policy changed")
    check(str(files["qkv_worker_plugin"].parent.parent) in env["PYTHONPATH"].split(":"),
          "Li6 plugin root absent from effective PYTHONPATH")
    controlled = {k: str(v) for k, v in env.items()
                  if k.startswith(ALLOWED_ENV_PREFIXES) or k in ALLOWED_ENV_NAMES}
    check("HBFSIM_NATIVE_BINDING_MANIFEST_PATH" not in controlled and
          "HBFSIM_QKV_MODEL_PLUGIN_V1" not in controlled and
          controlled.get("HBFSIM_QKV_STAGED_PTX_PATH") == str(STAGED),
          "Li6 effective environment retained old98 binding or lost staged PTX")
    return dict(sorted(controlled.items()))


def target_capture_contract() -> dict:
    return {
        "status": "IMPLEMENTED_FOR_ROOT_REVIEWED_LAUNCH",
        "required_files": ["controller/worker-start.json", "target-start-capture.json",
                            "target-start-maps.txt", "target-start-env.json"],
        "identity_fields": ["pid", "start_ticks", "pgid", "cgroup", "cmdline"],
        "map_requirements": [str(BUNDLE / "build/runtime/agent/libbpftime-agent.so"),
                             str(BUNDLE / "build/libhbfsim_launch_gate.so"), str(PROVIDER)],
        "target_cmdline_fragment": str(RUNNER),
        "environment_filter": sorted(ALLOWED_ENV_NAMES),
        "capture_rule": "after target PID/start is stable and required DSOs are mapped; one immutable capture; no inferred PID or environment",
        "gpu_status": "NOT_CAPTURED_CPU_PREFLIGHT_ONLY",
        "map_status_rule": "CAPTURED requires every map requirement; otherwise INCOMPLETE/NOT_CAPTURED is retained",
    }


def process_ticks(pid: int) -> int:
    tail = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
    return int(tail[19])


def capture_target_once(controller_process, run: Path, required_maps: list[str],
                        target_cmd_fragment: str, timeout_seconds: float = 20.0) -> dict:
    """Capture one real target identity after its required DSOs are mapped.

    This helper is launch-time code only; CPU preflight never invokes it.  It
    refuses PID reuse, waits for the target's own worker-start receipt, and
    writes maps/environment exactly once with filtered keys.
    """
    start_file = run / "controller/worker-start.json"
    allowed = ALLOWED_ENV_NAMES | {"HBFSIM_LI6_MODEL_PLUGIN_V1", "HBFSIM_LI6_MODEL_RECEIPT_DIR",
                                   "HBFSIM_QKV_STAGED_PTX_PATH", "HBFSIM_QKV_SOURCE_PTX_PATH",
                                   "HBFSIM_QKV_STAGED_PTX_SHA256"}
    until = time.monotonic() + timeout_seconds
    result = {"status": "NOT_CAPTURED", "reason": "worker-start or maps unavailable"}
    while time.monotonic() < until and controller_process.poll() is None:
        if not start_file.is_file():
            time.sleep(0.25)
            continue
        try:
            started = load(start_file)
            pid = int(started["pid"])
            start_ticks = int(started["start_ticks"])
            pgid = int(started["pgid"])
            check(process_ticks(pid) == start_ticks, "target PID/start changed before capture")
            check(os.getpgid(pid) == pgid == pid, "target PGID is not the controller worker leader")
            # The controller worker is the shell wrapper.  The actual target
            # is its descendant in the same PGID, identified by the pinned
            # runner command line; never treat the wrapper PID as the model PID.
            targets = []
            for candidate in Path("/proc").iterdir():
                if not candidate.name.isdigit():
                    continue
                candidate_pid = int(candidate.name)
                try:
                    if candidate_pid == pid or os.getpgid(candidate_pid) != pgid:
                        continue
                    stat = (candidate / "stat").read_text().rsplit(")", 1)[1].split()
                    parent_pid, candidate_ticks = int(stat[1]), int(stat[19])
                    if parent_pid != pid:
                        continue
                    argv = (candidate / "cmdline").read_bytes().split(b"\0")
                    argv = [arg.decode("utf-8", "replace") for arg in argv if arg]
                    if not target_cmd_fragment or target_cmd_fragment not in argv:
                        continue
                    targets.append((candidate_pid, " ".join(argv), parent_pid, candidate_ticks))
                except (FileNotFoundError, ProcessLookupError, PermissionError):
                    continue
            # The wrapper may publish worker-start before the child runner has
            # appeared in /proc.  Treat that as transient and keep the bounded
            # startup capture window open; only the final status may be
            # NOT_CAPTURED/INCOMPLETE when the descendant never materializes.
            check(len(targets) <= 1, "ambiguous direct target children in worker PGID")
            if not targets:
                time.sleep(0.25)
                continue
            target_pid, target_cmdline, target_parent, target_start_ticks = targets[0]
            target_pgid = os.getpgid(target_pid)
            maps = Path(f"/proc/{target_pid}/maps").read_text(encoding="utf-8")
            maps_complete = all(path in maps for path in required_maps)
            if not maps_complete:
                if time.monotonic() + 0.5 < until:
                    time.sleep(0.5)
                    continue
                result = {"status": "INCOMPLETE", "pid": target_pid,
                          "start_ticks": target_start_ticks, "pgid": target_pgid,
                          "parent_pid": target_parent, "cmdline": target_cmdline,
                          "reason": "required target DSOs were not all mapped before capture window ended",
                          "required_maps_present": False, "time_unix_ns": time.time_ns()}
                break
            environ = Path(f"/proc/{target_pid}/environ").read_bytes().split(b"\0")
            filtered = {}
            for entry in environ:
                key, separator, value = entry.partition(b"=")
                name = key.decode("utf-8", "replace")
                if separator and (name in allowed or name.startswith(ALLOWED_ENV_PREFIXES)):
                    filtered[name] = value.decode("utf-8", "replace")
            cgroup = Path(f"/proc/{target_pid}/cgroup").read_text()
            final_stat = Path(f"/proc/{target_pid}/stat").read_text().rsplit(")", 1)[1].split()
            check(int(final_stat[19]) == target_start_ticks and int(final_stat[1]) == target_parent
                  and os.getpgid(target_pid) == pgid and process_ticks(pid) == start_ticks,
                  "wrapper/target identity changed during capture")
            run.mkdir(parents=True, exist_ok=True)
            with (run / "target-start-maps.txt").open("x", encoding="utf-8") as stream:
                stream.write(maps)
            save_new(run / "target-start-env.json", filtered)
            result = {"status": "CAPTURED", "wrapper": {"pid": pid, "start_ticks": start_ticks,
                      "pgid": pgid, "cmdline": " ".join(started.get("command", []))},
                      "pid": target_pid, "start_ticks": target_start_ticks,
                      "pgid": target_pgid, "parent_pid": target_parent, "cmdline": target_cmdline,
                      "cgroup": cgroup,
                      "time_unix_ns": time.time_ns(),
                      "required_maps_present": maps_complete}
            break
        except (OSError, ValueError, KeyError, RuntimeError) as error:
            result = {"status": "NOT_CAPTURED", "reason": repr(error)}
            break
    save_new(run / "target-start-capture.json", result)
    return result


def main() -> int:
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=TASK / "preflight-v2/Li6_CPU_PREFLIGHT.json")
    args = parser.parse_args()
    check(SPEC.is_file(), "restored Li6 runtime spec is absent")
    spec = load(SPEC)
    check(spec["status"] == "CPU_PREPARED_GPU_PENDING_ROOT_REVIEW", "spec unexpectedly permits GPU")
    for path, expected in PINNED.items():
        check(path.is_file() and sha(path) == expected, f"pinned input changed: {path}")
    check(CONTROLLER == ROOT / "task/run_bounded_gpu_stage.py" and GUARD == ROOT / "task/all-weights-discovery-run-v1/resource_guard.py",
          "controller/guard paths not bound to actual active files")
    rows, inventory_path = inventory_rows()
    selected = select_inventory(rows)
    cfg = load(BASE_CONFIG)
    preflight_out = args.output.parent / "constructed-env"
    env = effective_environment(cfg, base_files(cfg), preflight_out)
    receipt = {
        "schema": "hbfsim.li6_cpu_preflight.v2",
        "status": "CPU_PREFLIGHT_PASS_GPU_PENDING_ROOT_REVIEW",
        "generated_unix_ns": time.time_ns(),
        "spec_sha256": sha(SPEC),
        "controller": {"path": str(CONTROLLER), "sha256": sha(CONTROLLER), "exists": True},
        "guard": {"path": str(GUARD), "sha256": sha(GUARD), "exists": True},
        "base_config_sha256": sha(BASE_CONFIG),
        "inventory": {"path": str(inventory_path), "sha256": sha(inventory_path),
                       "storage_count": len(rows), "selected": selected},
        "selector_contract": {name: {"pattern": pattern.pattern, "expected_bytes": size}
                              for name, (pattern, size) in SELECTORS.items()},
        "effective_environment": env,
        "environment_checks": {
            "constructed_from": str(BASE_REPRO),
            "plugin_root_present": str(PLUGIN) in env["PYTHONPATH"].split(":"),
            "source_ptx": {"path": str(SOURCE), "sha256": sha(SOURCE), "role": "original_recovered_ptx"},
            "staged_ptx": {"path": str(STAGED), "sha256": sha(STAGED), "role": "pass_output_staged_ptx"},
            "cuda_domains": {"framework": "CUDA12.8", "agent_hbf": "CUDA13_private"},
            "cuda_domain_evidence": "labels only; live target-start maps are required for actual loaded-library proof",
            "base_nvptx_opt_policy": {"status": "RETAINED_FROM_SUCCESSFUL_BASE_ENVIRONMENT",
                                       "reason": "pinned per-raw policy is part of the staged pass environment; no new optimization was introduced"},
            "old98_binding_manifest_removed": "HBFSIM_NATIVE_BINDING_MANIFEST_PATH" not in env,
            "old_qkv_plugin_removed": "HBFSIM_QKV_MODEL_PLUGIN_V1" not in env,
        },
        "target_capture": target_capture_contract(),
        "launch": {"status": "NOT_STARTED", "gpu_owner": "ROOT_ONLY", "root_review": "MISSING"},
        "evidence_classification": {
            "environment_and_selector": "CPU_PREFLIGHT_PASS",
            "lift_output": "UNVERIFIED",
            "instrumented_kernel": "UNVERIFIED",
            "model_connected": "UNVERIFIED",
        },
    }
    save_new(args.output, receipt)
    print(json.dumps({"status": receipt["status"], "receipt": str(args.output),
                      "selected": selected}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
