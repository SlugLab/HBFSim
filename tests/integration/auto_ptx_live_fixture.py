#!/usr/bin/env python3
"""Prepare and run the bounded two-entry automatic PTX live fixture.

Preparation and ``static-check`` are CPU-only.  ``target`` requires an
explicitly scheduled GPU process launched through run_with_bpftime.sh.
"""

from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import time
from typing import Any, Sequence


ROOT = pathlib.Path(__file__).resolve().parents[2]
ADAPTER = ROOT / "adapters" / "vllm"
sys.path.insert(0, str(ADAPTER))
import auto_prepare_ptx  # noqa: E402


ENTRIES = ("auto_weight_hit", "auto_known_nohit")
COUNT_LIMIT = 128


MAIN_PTX = r""".version 8.8
.target sm_120
.address_size 64

.visible .entry auto_weight_hit(
    .param .u64 .ptr .global .align 8 weight,
    .param .u64 .ptr .global .align 8 output,
    .param .u32 count
)
{
    .reg .pred %p;
    .reg .b32 %r<3>;
    .reg .b64 %rd<8>;
    ld.param.u64 %rd1, [weight];
    ld.param.u64 %rd6, [output];
    ld.param.u32 %r1, [count];
    mov.u32 %r2, %tid.x;
    setp.ge.u32 %p, %r2, %r1;
    @%p bra HIT_DONE;
    mul.wide.u32 %rd2, %r2, 8;
    add.s64 %rd3, %rd1, %rd2;
    ld.global.u64 %rd4, [%rd3];
    add.u64 %rd5, %rd4, 17;
    add.s64 %rd7, %rd6, %rd2;
    st.global.u64 [%rd7], %rd5;
HIT_DONE:
    ret;
}

.visible .entry auto_known_nohit(
    .param .u64 .ptr .global .align 8 weight,
    .param .u64 .ptr .global .align 8 output,
    .param .u32 count
)
{
    .reg .pred %p;
    .reg .b32 %r<3>;
    .reg .b64 %rd<8>;
    ld.param.u64 %rd1, [weight];
    ld.param.u64 %rd6, [output];
    ld.param.u32 %r1, [count];
    mov.u32 %r2, %tid.x;
    setp.ge.u32 %p, %r2, %r1;
    @%p bra NOHIT_DONE;
    mul.wide.u32 %rd2, %r2, 8;
    add.s64 %rd3, %rd1, %rd2;
    ld.global.u64 %rd4, [%rd3];
    add.u64 %rd5, %rd4, 29;
    add.s64 %rd7, %rd6, %rd2;
    st.global.u64 [%rd7], %rd5;
NOHIT_DONE:
    ret;
}
"""


UNKNOWN_PTX = r""".version 8.8
.target sm_120
.address_size 64
.visible .entry auto_unknown(.param .u64 .ptr .global .align 8 pointer)
{
    .reg .b64 %rd<3>;
    ld.param.u64 %rd1, [pointer];
    ld.global.u64 %rd2, [%rd1];
    ret;
}
"""


PROFILE = {
    "name": "auto-ptx-live-fixture",
    "capacity_bytes": 1048576,
    "page_bytes": 16384,
    "read_latency_ns": 1000,
    "program_latency_ns": 100000,
    "channels": 1,
    "dies_per_channel": 1,
    "planes_per_die": 1,
    "pages_per_block": 1,
    "channel_width_bits": 8,
    "channel_transfer_rate_mtps": 1600,
    "queue_depth": 8,
    "aggregate_bandwidth_bytes_per_s": 1000000000,
    "hbm_cache_bytes": 1048576,
    "reference_sample_rate": 0.0,
    "reference_warmup_requests": 0,
    "time_scale": 1,
    "timing_tolerance_ns": 10000,
}


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def sha256(path: pathlib.Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_exact(path: pathlib.Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)


def probe_source(entries: Sequence[str]) -> str:
    lines = ["#define SEC(name) __attribute__((section(name), used))\n"]
    for index, entry in enumerate(entries):
        require(entry.replace("_", "a").isalnum(), f"unsafe entry: {entry}")
        lines.append(
            f'\nSEC("kprobe/{entry}")\n'
            f"int cuda__auto_live_{index}(void* context)\n{{\n"
            "    (void)context;\n    return 0;\n}\n")
    lines.append('\nchar LICENSE[] SEC("license") = "GPL";\n')
    return "".join(lines)


def run_checked(command: Sequence[str], timeout: int) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(
        list(command), text=True, capture_output=True, timeout=timeout,
        check=False)
    if completed.returncode:
        raise RuntimeError(
            f"tool failed ({completed.returncode}): {' '.join(command)}\n"
            f"{completed.stderr.strip()}")
    return completed


def render_ptx(template: str, arch: str) -> bytes:
    require(arch.startswith("sm_") and arch[3:].isdigit(), "invalid GPU arch")
    return template.replace(".target sm_120", f".target {arch}", 1).encode()


def static_check(arch: str, *, ptxas: str | None = None,
                 clang: str | None = None,
                 objdump: str | None = None) -> dict[str, Any]:
    payload = render_ptx(MAIN_PTX, arch)
    entries = auto_prepare_ptx._entry_names(payload)
    require(entries == ENTRIES, f"entry inventory mismatch: {entries}")
    require(auto_prepare_ptx._call_graph_status(payload)[0] ==
            "NO_UNRESOLVED_MEMORY_FUNCTION", "main PTX call graph unresolved")
    unknown = auto_prepare_ptx._entry_names(render_ptx(UNKNOWN_PTX, arch))
    require(unknown == ("auto_unknown",), "unknown PTX inventory mismatch")
    probe_text = probe_source(entries)
    require(all(f'kprobe/{entry}' in probe_text for entry in entries),
            "probe source lacks an entry")
    result: dict[str, Any] = {
        "status": "STATIC_READY", "arch": arch,
        "raw_sha256": hashlib.sha256(payload).hexdigest(),
        "entries": list(entries), "payload_bytes": len(payload),
    }
    tools = (ptxas, clang, objdump)
    if any(tools):
        require(all(tools), "ptxas, clang and objdump must be provided together")
        with tempfile.TemporaryDirectory(prefix="auto-ptx-live-static-") as raw_dir:
            directory = pathlib.Path(raw_dir)
            main = directory / "main.ptx"
            unknown_path = directory / "unknown.ptx"
            main.write_bytes(payload)
            unknown_path.write_bytes(render_ptx(UNKNOWN_PTX, arch))
            cubin_sizes = []
            for ptx_source in (main, unknown_path):
                cubin = ptx_source.with_suffix(".cubin")
                run_checked([ptxas, f"--gpu-name={arch}", str(ptx_source),
                             "--output-file", str(cubin)], 60)
                cubin_sizes.append(cubin.stat().st_size)
            probe_c = directory / "probe.c"
            probe_o = directory / "probe.o"
            probe_c.write_text(probe_text)
            run_checked([clang, "-target", "bpf", "-O2", "-g", "-c",
                         str(probe_c), "-o", str(probe_o)], 60)
            sections = run_checked([objdump, "-h", str(probe_o)], 60).stdout
            require(all(f"kprobe/{entry}" in sections for entry in entries),
                    "compiled static probe lacks expected sections")
            result["tool_checks"] = {
                "main_cubin_bytes": cubin_sizes[0],
                "unknown_cubin_bytes": cubin_sizes[1],
                "probe_object_bytes": probe_o.stat().st_size,
            }
    return result


def prepare(args: argparse.Namespace) -> dict[str, Any]:
    output = pathlib.Path(args.output_dir).resolve()
    require(not output.exists(), f"output directory already exists: {output}")
    output.mkdir(parents=True)
    cache = output / "cache"
    cache.mkdir()
    raw = cache / "auto-two-entry.ptx"
    unknown_raw = output / "auto-unknown.ptx"
    write_exact(raw, render_ptx(MAIN_PTX, args.gpu_arch))
    write_exact(unknown_raw, render_ptx(UNKNOWN_PTX, args.gpu_arch))
    profile = output / "profile.json"
    profile.write_text(json.dumps(PROFILE, indent=2, sort_keys=True) + "\n")
    stage = output / "stage"
    pass_manifest = stage / "pass-manifests.jsonl"
    manifest = auto_prepare_ptx.stage_all_ptx(
        cache, stage, pathlib.Path(args.pass_library), pass_manifest,
        policy="strict", module_timeout_seconds=args.module_timeout,
    )
    require(manifest["status"] == "READY", "strict stage is not READY")
    published = sorted({
        result["kernel"] for variant in manifest["variants"]
        for result in variant["entry_results"]
        if result["status"] == "SUPPORTED_TRANSFORMED"
    })
    require(published == sorted(ENTRIES), f"published entries mismatch: {published}")
    pass_records = [json.loads(line) for line in pass_manifest.read_text().splitlines()
                    if line.strip()]
    require(len(pass_records) == len(ENTRIES), "pass manifest record count mismatch")
    for record in pass_records:
        kinds = [parameter.get("kind") for parameter in record.get("parameters", [])]
        require(kinds == ["pointer", "pointer", "scalar"],
                f"unexpected parameter classification: {record.get('kernel')} {kinds}")
        require(record.get("rewritten_instructions") == 2 and
                record.get("unsupported_instructions") == 0,
                f"unexpected rewrite coverage: {record.get('kernel')}")
    staged = pathlib.Path(manifest["variants"][0]["staged_path"])

    main_cubin = output / "auto-two-entry.cubin"
    unknown_cubin = output / "auto-unknown.cubin"
    for source, cubin in ((raw, main_cubin), (unknown_raw, unknown_cubin)):
        run_checked([
            args.ptxas, f"--gpu-name={args.gpu_arch}", str(source),
            "--output-file", str(cubin),
        ], args.tool_timeout)

    probe_c = output / "auto-two-entry-probe.bpf.c"
    probe_o = output / "auto-two-entry-probe.bpf.o"
    probe_c.write_text(probe_source(published))
    run_checked([
        args.clang, "-target", "bpf", "-O2", "-g", "-c", str(probe_c),
        "-o", str(probe_o),
    ], args.tool_timeout)
    sections = run_checked(
        [args.objdump, "-h", str(probe_o)], args.tool_timeout).stdout
    require(all(f"kprobe/{entry}" in sections for entry in published),
            "compiled probe lacks expected sections")
    (output / "probe-sections.txt").write_text(sections)

    artifacts = {
        "raw_ptx": raw, "staged_ptx": staged, "pass_manifest": pass_manifest,
        "main_cubin": main_cubin, "unknown_cubin": unknown_cubin,
        "probe_object": probe_o, "profile": profile,
        "completion": stage / "COMPLETE.json",
    }
    if args.bundle_receipt:
        bundle_receipt = pathlib.Path(args.bundle_receipt).resolve()
        require(bundle_receipt.is_file(), "bundle receipt does not exist")
        artifacts["bundle_receipt"] = bundle_receipt
    gate = pathlib.Path(args.build_dir).resolve() / "libhbfsim_launch_gate.so"
    build_dir = pathlib.Path(args.build_dir).resolve()
    bpftime_dir = pathlib.Path(args.bpftime_build_dir).resolve()
    agent = (bpftime_dir /
             "runtime/agent/libbpftime-agent.so")
    required_files = {
        "ptx_pass": build_dir / "libptxpass_hbf.so",
        "launch_gate": gate,
        "public_runtime": build_dir / "libhbfsim.so",
        "daemon": build_dir / "hbfsimd",
        "attach_loader": build_dir / "hbfsim_bpftime_attach_loader",
        "bpftime_agent": agent,
        "bpftime_server": (bpftime_dir /
                            "runtime/syscall-server/libbpftime-syscall-server.so"),
        "bpftime_provenance": bpftime_dir / "hbfsim-bpftime.provenance",
    }
    capability_checks = {
        "gate_export": False, "agent_export": False,
        "gate_path": str(gate), "agent_path": str(agent),
    }
    for label, path, symbol in (
        ("gate_export", gate, "hbfsim_instrumentation_policy_capabilities_v1"),
        ("agent_export", agent, "bpftime_nv_strict_bridge_capabilities_v1"),
    ):
        if path.is_file():
            inspected = subprocess.run(
                [args.nm, "-D", "--defined-only", str(path)], text=True,
                capture_output=True, timeout=args.tool_timeout, check=False)
            capability_checks[label] = (
                inspected.returncode == 0 and symbol in inspected.stdout)
    runtime_candidate_ready = all(
        capability_checks[name] for name in ("gate_export", "agent_export"))
    file_checks = {name: path.is_file() for name, path in required_files.items()}
    runtime_file_sha256 = {
        name: (sha256(path) if file_checks[name] else None)
        for name, path in required_files.items()
    }
    executable_loader = (file_checks["attach_loader"] and
                         os.access(required_files["attach_loader"], os.X_OK))
    executable_daemon = (file_checks["daemon"] and
                         os.access(required_files["daemon"], os.X_OK))
    runtime_candidate_ready = (
        runtime_candidate_ready and all(file_checks.values()) and
        executable_loader and executable_daemon)
    receipt = {
        "schema_version": 1,
        "status": ("PREPARED_CPU_ONLY" if runtime_candidate_ready else
                   "STAGED_BRIDGE_PENDING"),
        "gpu_launched": False, "entries": published,
        "per_entry_rewritten_instructions": 2,
        "per_entry_parameter_kinds": ["pointer", "pointer", "scalar"],
        "count_limit": COUNT_LIMIT, "artifacts": {
            name: {"path": str(path), "sha256": sha256(path)}
            for name, path in artifacts.items()
        },
        "runtime_candidate_ready": runtime_candidate_ready,
        "capability_checks": capability_checks,
        "runtime_file_checks": file_checks,
        "runtime_file_sha256": runtime_file_sha256,
        "attach_loader_executable": executable_loader,
        "daemon_executable": executable_daemon,
        "target_command": [
            str(ROOT / "scripts" / "run_with_bpftime.sh"), "--",
            sys.executable, str(pathlib.Path(__file__).resolve()), "target",
            "--fixture-dir", str(output), "--build-dir", args.build_dir,
            "--count", str(args.count),
        ],
        "required_environment": {
            "HBFSIM_BUILD_DIR": args.build_dir,
            "HBFSIM_DAEMON_PATH": str(required_files["daemon"]),
            "HBFSIM_BPFTIME_BUILD_DIR": args.bpftime_build_dir,
            "HBFSIM_CUDA_ROOT": args.cuda_root,
            "HBFSIM_BPFTIME_PROBE": str(probe_o),
            "HBFSIM_PRESTAGED_PASS_MANIFEST_PATH": str(pass_manifest),
            "HBFSIM_PASS_MANIFEST_PATH": str(output / "runtime-pass-manifests.jsonl"),
            "HBFSIM_COVERAGE_PATH": str(output / "coverage.jsonl"),
            "HBFSIM_STRICT_BRIDGE_LOG_PATH": str(output / "strict-bridge.jsonl"),
            "HBFSIM_INSTRUMENTATION_POLICY": "strict",
            "BPFTIME_CUDA_LATE_PTX_DIR": str(stage),
            "BPFTIME_CUDA_LATE_PTX_PREPATCHED": "1",
        },
    }
    (output / "fixture.json").write_text(
        json.dumps(receipt, indent=2, sort_keys=True) + "\n")
    if not runtime_candidate_ready and not args.allow_pending_bridge:
        raise RuntimeError(
            "strict runtime candidate lacks required capability exports; "
            "STAGED_BRIDGE_PENDING receipt retained")
    return receipt


class HbfOptions(ctypes.Structure):
    _fields_ = [
        ("profile_path", ctypes.c_char_p), ("report_dir", ctypes.c_char_p),
        ("mode", ctypes.c_uint32), ("ring_capacity", ctypes.c_uint32),
        ("request_timeout_ns", ctypes.c_uint64),
    ]


class RangeOptions(ctypes.Structure):
    _fields_ = [
        ("mode", ctypes.c_uint32), ("permissions", ctypes.c_uint32),
        ("cache_policy", ctypes.c_uint32), ("stream_id", ctypes.c_uint32),
    ]


def json_snapshot(function: Any) -> dict[str, Any]:
    required = int(function(None, 0))
    require(required > 1, f"snapshot size failed: {required}")
    buffer = ctypes.create_string_buffer(required)
    require(int(function(buffer, required)) == required, "snapshot size changed")
    return json.loads(buffer.value)


def counter_total(snapshot: dict[str, Any], name: str) -> int:
    modules = snapshot.get("access", {}).get("per_module", [])
    values = [module.get("counters", {}).get(name) for module in modules]
    require(values and all(isinstance(value, int) for value in values),
            f"missing accounting counter: {name}")
    return sum(values)


def target(args: argparse.Namespace) -> dict[str, Any]:
    fixture = pathlib.Path(args.fixture_dir).resolve()
    metadata = json.loads((fixture / "fixture.json").read_text())
    require(0 < args.count <= metadata["count_limit"], "count out of bounds")
    build = pathlib.Path(args.build_dir).resolve()
    hbf = ctypes.CDLL(str(build / "libhbfsim.so"), mode=ctypes.RTLD_GLOBAL)
    cuda = ctypes.CDLL("libcuda.so.1")
    process = ctypes.CDLL(None)

    cuda.cuInit.argtypes = [ctypes.c_uint]
    cuda.cuInit.restype = ctypes.c_int
    cuda.cuDeviceGet.argtypes = [ctypes.POINTER(ctypes.c_int), ctypes.c_int]
    cuda.cuDeviceGet.restype = ctypes.c_int
    cuda.cuDevicePrimaryCtxRetain.argtypes = [ctypes.POINTER(ctypes.c_void_p), ctypes.c_int]
    cuda.cuDevicePrimaryCtxRetain.restype = ctypes.c_int
    cuda.cuCtxSetCurrent.argtypes = [ctypes.c_void_p]
    cuda.cuCtxSetCurrent.restype = ctypes.c_int
    cuda.cuMemAlloc_v2.argtypes = [ctypes.POINTER(ctypes.c_uint64), ctypes.c_size_t]
    cuda.cuMemAlloc_v2.restype = ctypes.c_int
    cuda.cuMemcpyHtoD_v2.argtypes = [ctypes.c_uint64, ctypes.c_void_p, ctypes.c_size_t]
    cuda.cuMemcpyHtoD_v2.restype = ctypes.c_int
    cuda.cuMemcpyDtoH_v2.argtypes = [ctypes.c_void_p, ctypes.c_uint64, ctypes.c_size_t]
    cuda.cuMemcpyDtoH_v2.restype = ctypes.c_int
    cuda.cuCtxSynchronize.argtypes = []
    cuda.cuCtxSynchronize.restype = ctypes.c_int
    cuda.cuModuleGetFunction.argtypes = [ctypes.POINTER(ctypes.c_void_p), ctypes.c_void_p, ctypes.c_char_p]
    cuda.cuModuleGetFunction.restype = ctypes.c_int
    cuda.cuModuleGetGlobal_v2.argtypes = [ctypes.POINTER(ctypes.c_uint64), ctypes.POINTER(ctypes.c_size_t), ctypes.c_void_p, ctypes.c_char_p]
    cuda.cuModuleGetGlobal_v2.restype = ctypes.c_int

    load = process.cuModuleLoadDataEx
    load.argtypes = [ctypes.POINTER(ctypes.c_void_p), ctypes.c_void_p,
                     ctypes.c_uint, ctypes.c_void_p, ctypes.c_void_p]
    load.restype = ctypes.c_int
    launch = process.cuLaunchKernel
    launch.argtypes = [ctypes.c_void_p] + [ctypes.c_uint] * 7 + [
        ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p]
    launch.restype = ctypes.c_int
    binder = process.bpftime_nv_bind_ptx_variant
    binder.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_size_t,
                       ctypes.c_char_p]
    binder.restype = ctypes.c_int
    begin_accounting = process.hbfsim_request_accounting_begin_v1
    begin_accounting.argtypes = [ctypes.c_uint64, ctypes.c_uint64,
                                 ctypes.c_uint64]
    begin_accounting.restype = ctypes.c_int
    snapshot_accounting = process.hbfsim_request_accounting_snapshot_v1
    snapshot_accounting.argtypes = [ctypes.c_char_p, ctypes.c_size_t]
    snapshot_accounting.restype = ctypes.c_longlong

    hbf.hbfsim_context_create.argtypes = [ctypes.POINTER(HbfOptions),
                                          ctypes.POINTER(ctypes.c_void_p)]
    hbf.hbfsim_context_create.restype = ctypes.c_int
    hbf.hbfsim_register_device.argtypes = [ctypes.c_void_p, ctypes.c_void_p,
                                           ctypes.c_size_t,
                                           ctypes.POINTER(RangeOptions)]
    hbf.hbfsim_register_device.restype = ctypes.c_int
    hbf.hbfsim_unregister.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    hbf.hbfsim_unregister.restype = ctypes.c_int
    hbf.hbfsim_context_destroy.argtypes = [ctypes.c_void_p]
    hbf.hbfsim_context_destroy.restype = None

    require(cuda.cuInit(0) == 0, "cuInit failed")
    device = ctypes.c_int()
    context = ctypes.c_void_p()
    require(cuda.cuDeviceGet(ctypes.byref(device), args.gpu_index) == 0,
            "cuDeviceGet failed")
    require(cuda.cuDevicePrimaryCtxRetain(ctypes.byref(context), device) == 0,
            "primary context retain failed")
    require(cuda.cuCtxSetCurrent(context) == 0, "cuCtxSetCurrent failed")

    byte_count = args.count * ctypes.sizeof(ctypes.c_uint64)
    weight = ctypes.c_uint64()
    other = ctypes.c_uint64()
    require(cuda.cuMemAlloc_v2(ctypes.byref(weight), byte_count) == 0,
            "weight allocation failed")
    require(cuda.cuMemAlloc_v2(ctypes.byref(other), byte_count) == 0,
            "other allocation failed")
    output_address = ctypes.c_uint64()
    require(cuda.cuMemAlloc_v2(ctypes.byref(output_address), byte_count) == 0,
            "output allocation failed")
    hit_values = (ctypes.c_uint64 * args.count)(
        *(0x1000 + index for index in range(args.count)))
    nohit_values = (ctypes.c_uint64 * args.count)(
        *(0x2000 + index for index in range(args.count)))
    require(cuda.cuMemcpyHtoD_v2(weight, hit_values, byte_count) == 0,
            "weight copy failed")
    require(cuda.cuMemcpyHtoD_v2(other, nohit_values, byte_count) == 0,
            "other copy failed")

    options = HbfOptions(
        str(fixture / "profile.json").encode(), str(fixture / "runtime").encode(),
        0, 64, 5_000_000_000)
    (fixture / "runtime").mkdir(exist_ok=False)
    hbf_context = ctypes.c_void_p()
    context_rc = hbf.hbfsim_context_create(ctypes.byref(options),
                                            ctypes.byref(hbf_context))
    (fixture / "context-create.json").write_text(json.dumps({
        "schema_version": 1,
        "return_code": int(context_rc),
        "daemon_path": os.environ.get("HBFSIM_DAEMON_PATH"),
        "context_nonnull": bool(hbf_context.value),
    }, indent=2, sort_keys=True) + "\n")
    require(context_rc == 0,
            f"hbfsim_context_create failed rc={context_rc}")
    range_options = RangeOptions(1, 1, 0, 0)
    require(hbf.hbfsim_register_device(
        hbf_context, ctypes.c_void_p(weight.value), byte_count,
        ctypes.byref(range_options)) == 0, "weight registration failed")

    def load_cubin(path: pathlib.Path) -> ctypes.c_void_p:
        payload = path.read_bytes()
        storage = ctypes.create_string_buffer(payload)
        module = ctypes.c_void_p()
        require(load(ctypes.byref(module), storage, 0, None, None) == 0,
                f"module load failed: {path}")
        return module

    module = load_cubin(fixture / "auto-two-entry.cubin")
    unknown_module = load_cubin(fixture / "auto-unknown.cubin")
    functions: dict[str, ctypes.c_void_p] = {}
    for name in ENTRIES:
        function = ctypes.c_void_p()
        require(cuda.cuModuleGetFunction(
            ctypes.byref(function), module, name.encode()) == 0,
            f"cuModuleGetFunction failed: {name}")
        functions[name] = function
    unknown = ctypes.c_void_p()
    require(cuda.cuModuleGetFunction(
        ctypes.byref(unknown), unknown_module, b"auto_unknown") == 0,
        "unknown function lookup failed")
    original_ptx = pathlib.Path(
        metadata["artifacts"]["raw_ptx"]["path"]).read_bytes()
    original_sha256 = hashlib.sha256(original_ptx).hexdigest()
    require(original_sha256 == metadata["artifacts"]["raw_ptx"]["sha256"],
            "raw PTX artifact digest mismatch")
    manifest_records = [json.loads(line) for line in pathlib.Path(
        metadata["artifacts"]["pass_manifest"]["path"]).read_text().splitlines()
        if line.strip()]
    require(manifest_records and all(
        record.get("module_id") == f"ptx:sha256:{original_sha256}"
        for record in manifest_records), "raw PTX manifest identity mismatch")

    def bind(name: str) -> None:
        result = 1
        for _ in range(100):
            result = binder(functions[name], original_ptx,
                            len(original_ptx), name.encode())
            if result != 1:
                break
            time.sleep(0.05)
        with (fixture / "binder-attempts.jsonl").open("a") as stream:
            stream.write(json.dumps({
                "schema_version": 1,
                "kernel": name,
                "raw_sha256": original_sha256,
                "function": hex(int(functions[name].value)),
                "return_code": int(result),
            }, sort_keys=True) + "\n")
        require(result == 0, f"exact bind failed for {name}: {result}")

    def launch_one(function: ctypes.c_void_p, pointer: int) -> int:
        pointer_arg = ctypes.c_uint64(pointer)
        output_arg = ctypes.c_uint64(output_address.value)
        count_arg = ctypes.c_uint32(args.count)
        parameters = (ctypes.c_void_p * 3)(
            ctypes.cast(ctypes.byref(pointer_arg), ctypes.c_void_p),
            ctypes.cast(ctypes.byref(output_arg), ctypes.c_void_p),
            ctypes.cast(ctypes.byref(count_arg), ctypes.c_void_p))
        return launch(function, 1, 1, 1, args.count, 1, 1, 0, None,
                      parameters, None)

    bind(ENTRIES[0])
    require(launch_one(functions[ENTRIES[1]], other.value) != 0,
            "missing-alias launch was not rejected")
    unknown_pointer = ctypes.c_uint64(other.value)
    unknown_parameters = (ctypes.c_void_p * 1)(
        ctypes.cast(ctypes.byref(unknown_pointer), ctypes.c_void_p))
    require(launch(unknown, 1, 1, 1, 1, 1, 1, 0, None,
                   unknown_parameters, None) != 0,
            "unknown module launch was not rejected")

    def measured(name: str, pointer: int, epoch: int, addend: int,
                 source: Any) -> dict[str, Any]:
        require(begin_accounting(0, epoch, 4096) == 0,
                f"accounting begin failed: {name}")
        require(launch_one(functions[name], pointer) == 0,
                f"launch failed: {name}")
        require(cuda.cuCtxSynchronize() == 0, f"sync failed: {name}")
        snapshot = json_snapshot(snapshot_accounting)
        (fixture / f"{name}-accounting.json").write_text(
            json.dumps(snapshot, indent=2, sort_keys=True) + "\n")
        require(snapshot.get("status") == "COMPLETE",
                f"accounting incomplete: {name}")
        output = (ctypes.c_uint64 * args.count)()
        require(cuda.cuMemcpyDtoH_v2(
            output, output_address.value, byte_count) == 0,
            f"output copy failed: {name}")
        expected = [int(source[index]) + addend for index in range(args.count)]
        (fixture / f"{name}-output.json").write_text(json.dumps({
            "observed": list(output), "expected": expected,
        }, indent=2, sort_keys=True) + "\n")
        require(list(output) == expected, f"full output mismatch: {name}")
        return snapshot

    hit = measured(ENTRIES[0], weight.value, 101, 17, hit_values)
    bind(ENTRIES[1])
    nohit = measured(ENTRIES[1], other.value, 102, 29, nohit_values)
    hit_supported = counter_total(hit, "supported_accesses")
    hit_supported_bytes = counter_total(hit, "supported_bytes")
    hit_in_range = counter_total(hit, "in_range_accesses")
    hit_intersection_bytes = counter_total(hit, "in_range_intersection_bytes")
    hit_native = counter_total(hit, "native_out_of_range_accesses")
    hit_native_bytes = counter_total(hit, "native_out_of_range_bytes")
    hit_admitted = counter_total(hit, "modeled_admitted_accesses")
    hit_admitted_bytes = counter_total(hit, "modeled_admitted_bytes")
    hit_completed = counter_total(hit, "service_completed_accesses")
    hit_completed_bytes = counter_total(hit, "service_completed_bytes")
    nohit_supported = counter_total(nohit, "supported_accesses")
    nohit_supported_bytes = counter_total(nohit, "supported_bytes")
    nohit_in_range = counter_total(nohit, "in_range_accesses")
    nohit_native = counter_total(nohit, "native_out_of_range_accesses")
    nohit_native_bytes = counter_total(nohit, "native_out_of_range_bytes")
    nohit_intersection_bytes = counter_total(
        nohit, "in_range_intersection_bytes")
    nohit_admitted = counter_total(nohit, "modeled_admitted_accesses")
    nohit_admitted_bytes = counter_total(nohit, "modeled_admitted_bytes")
    nohit_completed = counter_total(nohit, "service_completed_accesses")
    nohit_completed_bytes = counter_total(nohit, "service_completed_bytes")
    expected_accesses = 2 * args.count
    expected_bytes = 8 * expected_accesses
    require(hit_supported == expected_accesses and
            hit_supported_bytes == expected_bytes,
            "direct-hit supported total is not exactly 2N x 8 bytes")
    require(hit_in_range == args.count and
            hit_intersection_bytes == 8 * args.count,
            "direct-hit in-range load count is not exactly N x 8 bytes")
    require(hit_native == args.count and hit_native_bytes == 8 * args.count,
            "direct-hit output store count is not exactly N native accesses")
    require(hit_supported == hit_in_range + hit_native,
            "direct-hit supported access partition does not close")
    require(hit_in_range == hit_admitted == hit_completed == args.count and
            hit_intersection_bytes == hit_admitted_bytes ==
            hit_completed_bytes == 8 * args.count,
            "direct-hit N=M=K access/byte closure failed")
    require(nohit_supported == expected_accesses and
            nohit_supported_bytes == expected_bytes,
            "known no-hit supported total is not exactly 2N x 8 bytes")
    require(nohit_in_range == 0 and nohit_native == expected_accesses and
            nohit_native_bytes == expected_bytes,
            "known no-hit partition is not zero in-range plus 2N native")
    require(nohit_supported == nohit_in_range + nohit_native,
            "known no-hit supported access partition does not close")
    require(nohit_intersection_bytes == nohit_admitted ==
            nohit_admitted_bytes == nohit_completed ==
            nohit_completed_bytes == 0,
            "known no-hit admitted/completed/intersection closure is nonzero")
    error_counters = (
        "failed_after_issue_accesses", "failed_after_issue_bytes",
        "unsupported_preissue_accesses", "unsupported_preissue_bytes",
        "failed_preissue_accesses", "failed_preissue_bytes",
        "translation_failed_accesses", "translation_failed_bytes",
        "unclassified_accesses", "unclassified_bytes", "counter_overflow",
    )
    for label, snapshot in (("hit", hit), ("nohit", nohit)):
        nonzero = {name: counter_total(snapshot, name) for name in error_counters
                   if counter_total(snapshot, name) != 0}
        require(not nonzero, f"{label} accounting errors are nonzero: {nonzero}")

    coverage = [json.loads(line) for line in pathlib.Path(
        os.environ["HBFSIM_COVERAGE_PATH"]).read_text().splitlines()
        if line.strip()]
    bridge = [json.loads(line) for line in pathlib.Path(
        os.environ["HBFSIM_STRICT_BRIDGE_LOG_PATH"]).read_text().splitlines()
        if line.strip()]
    original_handles = {
        **{name: hex(functions[name].value) for name in ENTRIES},
        "auto_unknown": hex(unknown.value),
    }

    def bridge_matches_original(row: dict[str, Any], name: str) -> bool:
        value = row.get("original_function")
        observed = (hex(value) if isinstance(value, int) else
                    str(value).lower())
        return observed == original_handles[name].lower()
    require(any(row.get("kernel") == ENTRIES[0] and row.get("modeled") is True
                for row in coverage), "direct-hit modeled decision missing")
    require(any(row.get("kernel") == ENTRIES[1] and
                row.get("modeled") is False and
                row.get("requires_instrumented_execution") is True and
                row.get("allowed") is True for row in coverage),
            "known no-hit strict decision missing")
    for name in ENTRIES:
        require(any(row.get("schema_version") == 1 and
                    bridge_matches_original(row, name) and
                    row.get("kernel_name") in (None, name) and
                    row.get("gate_decision") == 2 and
                    row.get("exact_alias_found") is True and
                    row.get("selected_path") == "PATCHED" and
                    row.get("patched_function") is not None and
                    row.get("selected_function") == row.get("patched_function") and
                    row.get("selected_function") != row.get("original_function") and
                    row.get("cuda_result") == 0 for row in bridge),
                f"PATCHED bridge receipt missing: {name}")
    negative_rows: dict[str, list[dict[str, Any]]] = {}
    for name in (ENTRIES[1], "auto_unknown"):
        negative_rows[name] = [
            row for row in bridge if bridge_matches_original(row, name) and
            row.get("kernel_name") in (None, name) and
            row.get("selected_path") == "REJECTED" and
            row.get("cuda_result") != 0
        ]
        require(negative_rows[name],
                f"negative bridge rejection receipt missing: {name}")
    missing_alias_proven = any(
        row.get("gate_decision") == 2 and
        row.get("exact_alias_found") is False
        for row in negative_rows[ENTRIES[1]])
    missing_alias_classification = (
        "DECISION2_MISSING_EXACT_ALIAS_REJECTED" if missing_alias_proven else
        "UNBOUND_OR_UNKNOWN_REJECTED_MISSING_ALIAS_NOT_PROVEN")

    require(hbf.hbfsim_unregister(
        hbf_context, ctypes.c_void_p(weight.value)) == 0,
        "weight unregister failed")
    hbf.hbfsim_context_destroy(hbf_context)

    receipt = {
        "schema_version": 1, "status": "SUCCESS",
        "count": args.count, "output_values_checked": 2 * args.count,
        "hit_supported_accesses": hit_supported,
        "hit_in_range_accesses": hit_in_range,
        "hit_native_out_of_range_accesses": hit_native,
        "hit_modeled_admitted_accesses": hit_admitted,
        "hit_service_completed_accesses": hit_completed,
        "nohit_supported_accesses": nohit_supported,
        "nohit_in_range_accesses": nohit_in_range,
        "nohit_native_out_of_range_accesses": nohit_native,
        "nohit_modeled_admitted_accesses": nohit_admitted,
        "nohit_service_completed_accesses": nohit_completed,
        "negative_unbound_launch_rejected": True,
        "missing_alias_classification": missing_alias_classification,
        "decision2_missing_alias_proven": missing_alias_proven,
        "negative_unknown_rejected": True,
        "coverage_records": len(coverage), "bridge_records": len(bridge),
        "evidence_scope": "minimal_fixture_not_full_model",
    }
    (fixture / "target-result.json").write_text(
        json.dumps(receipt, indent=2, sort_keys=True) + "\n")
    print(json.dumps(receipt, indent=2, sort_keys=True))
    return receipt


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    static = subparsers.add_parser("static-check")
    static.add_argument("--gpu-arch", default="sm_120")
    static.add_argument("--ptxas")
    static.add_argument("--clang")
    static.add_argument("--objdump")
    prepare_parser = subparsers.add_parser("prepare")
    prepare_parser.add_argument("--output-dir", required=True)
    prepare_parser.add_argument("--pass-library", required=True)
    prepare_parser.add_argument("--build-dir", required=True)
    prepare_parser.add_argument("--bpftime-build-dir", required=True)
    prepare_parser.add_argument("--cuda-root", required=True)
    prepare_parser.add_argument("--bundle-receipt")
    prepare_parser.add_argument("--gpu-arch", default="sm_120")
    prepare_parser.add_argument("--count", type=int, default=64)
    prepare_parser.add_argument("--module-timeout", type=float, default=120)
    prepare_parser.add_argument("--tool-timeout", type=int, default=60)
    prepare_parser.add_argument("--ptxas", default="ptxas")
    prepare_parser.add_argument("--clang", default="clang")
    prepare_parser.add_argument("--objdump", default="llvm-objdump")
    prepare_parser.add_argument("--nm", default="nm")
    prepare_parser.add_argument("--allow-pending-bridge", action="store_true")
    target_parser = subparsers.add_parser("target")
    target_parser.add_argument("--fixture-dir", required=True)
    target_parser.add_argument("--build-dir", required=True)
    target_parser.add_argument("--gpu-index", type=int, default=0)
    target_parser.add_argument("--count", type=int, default=64)
    args = parser.parse_args(argv)
    if hasattr(args, "count") and not 0 < args.count <= COUNT_LIMIT:
        parser.error(f"count must be in 1..{COUNT_LIMIT}")
    return args


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    if args.command == "static-check":
        result = static_check(args.gpu_arch, ptxas=args.ptxas,
                              clang=args.clang, objdump=args.objdump)
    elif args.command == "prepare":
        result = prepare(args)
    else:
        result = target(args)
    if args.command != "target":
        print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
