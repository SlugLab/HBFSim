#!/usr/bin/env python3
"""Run a no-Torch exact sideband probe and merge it into a vLLM report."""

from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import os
import pathlib
import time
from typing import Any


HBFSIM_OK = 0
HBFSIM_VLLM_ABI_VERSION = 3
CUDA_MEMCPY_HOST_TO_DEVICE = 1
CUDA_MEMCPY_DEVICE_TO_HOST = 2
PROBE_THREADS = 128
PROBE_DEPTH = 8
PROBE_ITERATIONS = 384
PROBE_DISTANCE = 32
PROBE_BYTES = 12288
PROBE_WORDS = PROBE_BYTES // 8
PROBE_RING_CAPACITY = PROBE_THREADS * PROBE_DEPTH
# The hybrid profile deliberately routes its first 1,024 operations through
# the host reference path.  A one-second watchdog is shorter than that full
# warmup window after the nominal 100x timing scale and can turn valid queue
# progress into a false timeout.  This is a liveness watchdog, not simulated
# latency, so leave enough headroom for the complete reference batch.
PROBE_REQUEST_TIMEOUT_NS = 10_000_000_000
PROBE_SEED = 0x0123456789ABCDEF
PROBE_SLOT_MIX = 0xD1B54A32D192ED03
PROBE_INPUT_MIX = 0x9E3779B97F4A7C15
PROBE_NATIVE_PREHEAT_NS = 250_000_000
U64_MASK = (1 << 64) - 1


class _NativeOptions(ctypes.Structure):
    _fields_ = [
        ("struct_bytes", ctypes.c_uint32),
        ("profile_path", ctypes.c_char_p),
        ("report_dir", ctypes.c_char_p),
        ("ring_capacity", ctypes.c_uint32),
        ("timing_model", ctypes.c_uint32),
        ("request_timeout_ns", ctypes.c_uint64),
        ("exact_profile_path", ctypes.c_char_p),
        ("exact_cache_condition", ctypes.c_uint32),
        ("exact_concurrency_condition", ctypes.c_uint32),
        ("exact_cluster_x", ctypes.c_uint32),
        ("exact_cluster_y", ctypes.c_uint32),
        ("exact_cluster_z", ctypes.c_uint32),
    ]


def _atomic_json(path: pathlib.Path, value: dict[str, Any]) -> None:
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")
    os.replace(temporary, path)


def _regular_file(path: pathlib.Path, label: str) -> pathlib.Path:
    if path.is_symlink() or not path.is_file():
        raise RuntimeError(f"{label} is not a regular file: {path}")
    return path.resolve()


def merge_probe_result(
    result_path: pathlib.Path, probe: dict[str, Any]
) -> dict[str, Any]:
    result = json.loads(result_path.read_text())
    if result.get("mode") != "exact" or \
            result.get("exact_scope") != "one_shot_sideband_probe" or \
            result.get("model_graph_fidelity") != "native" or \
            result.get("exact_probe_deferred") is not True or \
            result.get("exact_session_count") != 0 or \
            result.get("exact_post_run_finalized") is not False or \
            result.get("exact_probe") is not None:
        raise RuntimeError("result is not a deferred exact vLLM run")
    if probe.get("status") != "passed" or probe.get("bit_exact") is not True:
        raise RuntimeError("exact probe lacks a passing bit-exact oracle")
    result.update({
        "exact_probe_deferred": False,
        "exact_session_count": 1,
        "exact_post_run_finalized": True,
        "exact_probe": probe,
    })
    _atomic_json(result_path, result)
    return result


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", required=True)
    parser.add_argument("--exact-profile", required=True)
    parser.add_argument("--exact-preheat", action="store_true")
    parser.add_argument("--report-dir", required=True)
    parser.add_argument(
        "--hbf-timing-model",
        choices=("reference", "fast", "hybrid"),
        default="hybrid",
    )
    return parser.parse_args()


def probe_input_values() -> list[int]:
    return [
        (PROBE_SEED ^ (index * PROBE_INPUT_MIX)) & U64_MASK
        for index in range(PROBE_WORDS)
    ]


def _step(value: int) -> int:
    value = (value ^ (value << 13)) & U64_MASK
    value = (value ^ (value >> 7)) & U64_MASK
    return (value ^ (value << 17)) & U64_MASK


def expected_probe_output(input_values: list[int]) -> list[int]:
    if len(input_values) != PROBE_WORDS:
        raise RuntimeError("exact probe input shape mismatch")
    outputs = []
    for thread in range(PROBE_THREADS):
        values = [
            (PROBE_SEED ^ (thread << 32) ^ thread ^
             (slot * PROBE_SLOT_MIX)) & U64_MASK
            for slot in range(PROBE_DEPTH)
        ]
        for iteration in range(PROBE_ITERATIONS):
            for slot in range(PROBE_DEPTH):
                index = (thread * 131 + iteration + PROBE_DISTANCE +
                         slot * 17) % PROBE_WORDS
                values[slot] = _step(values[slot] ^ input_values[index])
        checksum = 0
        for value in values:
            checksum ^= value
        outputs.append(checksum & U64_MASK)
    return outputs


def validate_probe_output(
    input_values: list[int], output_values: list[int]
) -> list[int]:
    expected = expected_probe_output(input_values)
    if output_values != expected:
        mismatch = next(
            (index for index, pair in enumerate(zip(expected, output_values))
             if pair[0] != pair[1]),
            min(len(expected), len(output_values)),
        )
        raise RuntimeError(f"exact probe output mismatch at thread {mismatch}")
    return expected


def _bind_cuda_runtime(library: Any) -> None:
    library.cudaSetDevice.argtypes = [ctypes.c_int]
    library.cudaSetDevice.restype = ctypes.c_int
    library.cudaMalloc.argtypes = [
        ctypes.POINTER(ctypes.c_void_p), ctypes.c_size_t,
    ]
    library.cudaMalloc.restype = ctypes.c_int
    library.cudaMemcpy.argtypes = [
        ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t, ctypes.c_int,
    ]
    library.cudaMemcpy.restype = ctypes.c_int
    library.cudaDeviceSynchronize.argtypes = []
    library.cudaDeviceSynchronize.restype = ctypes.c_int
    library.cudaFree.argtypes = [ctypes.c_void_p]
    library.cudaFree.restype = ctypes.c_int


def _bind_extension(library: Any) -> None:
    library.hbfsim_vllm_abi_version.argtypes = []
    library.hbfsim_vllm_abi_version.restype = ctypes.c_uint32
    library.hbfsim_vllm_session_create.argtypes = [
        ctypes.POINTER(_NativeOptions), ctypes.POINTER(ctypes.c_void_p),
    ]
    library.hbfsim_vllm_session_create.restype = ctypes.c_int
    library.hbfsim_vllm_register_storage.argtypes = [
        ctypes.c_void_p, ctypes.c_size_t, ctypes.c_size_t,
    ]
    library.hbfsim_vllm_register_storage.restype = ctypes.c_int
    library.hbfsim_vllm_publish_exact_contract.argtypes = [ctypes.c_void_p]
    library.hbfsim_vllm_publish_exact_contract.restype = ctypes.c_int
    library.hbfsim_vllm_finalize_exact.argtypes = [ctypes.c_void_p]
    library.hbfsim_vllm_finalize_exact.restype = ctypes.c_int
    library.hbfsim_vllm_session_close.argtypes = [
        ctypes.POINTER(ctypes.c_void_p),
    ]
    library.hbfsim_vllm_session_close.restype = ctypes.c_int
    library.hbfsim_vllm_status_string.argtypes = [ctypes.c_int]
    library.hbfsim_vllm_status_string.restype = ctypes.c_char_p


def _status(extension: Any, status: int) -> str:
    value = extension.hbfsim_vllm_status_string(status)
    return value.decode() if value else "unknown"


def _check_hbfsim(extension: Any, status: int, operation: str) -> None:
    if status != HBFSIM_OK:
        raise RuntimeError(
            f"{operation} failed: {_status(extension, status)} ({status})"
        )


def _check_cuda(status: int, operation: str) -> None:
    if status != 0:
        raise RuntimeError(f"{operation} failed with CUDA status {status}")


def _validate_preheat(report: pathlib.Path, exact_profile: pathlib.Path) -> None:
    preheat_path = _regular_file(report / "preheat.json", "preheat evidence")
    preheat = json.loads(preheat_path.read_text())
    if preheat.get("status") != "passed" or \
            pathlib.Path(preheat.get("profile_path", "")) != exact_profile or \
            not (preheat.get("temperature_min_c", 1) <=
                 preheat.get("final_temperature_c", 0) <=
                 preheat.get("temperature_max_c", 0)):
        raise RuntimeError("native preheat evidence is invalid")


def run_exact_probe(args: argparse.Namespace) -> dict[str, Any]:
    report = pathlib.Path(args.report_dir).resolve()
    report.mkdir(parents=True, exist_ok=True)
    profile = _regular_file(pathlib.Path(args.profile), "timing profile")
    exact_profile = _regular_file(
        pathlib.Path(args.exact_profile), "exact profile"
    )
    if args.exact_preheat:
        _validate_preheat(report, exact_profile)

    build = pathlib.Path(os.environ.get("HBFSIM_BUILD_DIR", ""))
    cuda_root = pathlib.Path(
        os.environ.get("HBFSIM_CUDA_ROOT", "/usr/local/cuda-13.0")
    )
    extension_path = _regular_file(pathlib.Path(os.environ.get(
        "HBFSIM_VLLM_EXTENSION", str(build / "libhbfsim_vllm_extension.so")
    )), "vLLM extension")
    probe_library_path = _regular_file(pathlib.Path(os.environ.get(
        "HBFSIM_VLLM_EXACT_PROBE_LIBRARY",
        str(build / "libhbfsim_llama_probe.so"),
    )), "exact probe library")
    probe_ptx_path = _regular_file(pathlib.Path(os.environ.get(
        "HBFSIM_VLLM_EXACT_PROBE_PTX",
        str(build / "hbfsim_llama_probe.ptx"),
    )), "exact probe PTX")
    os.environ.setdefault("HBFSIM_DAEMON_PATH", str(build / "hbfsimd"))
    if "HBFSIM_TARGET_ORIGINAL_LD_PRELOAD" in os.environ:
        os.environ["LD_PRELOAD"] = os.environ[
            "HBFSIM_TARGET_ORIGINAL_LD_PRELOAD"
        ]

    cudart = ctypes.CDLL(
        str(cuda_root / "lib64/libcudart.so.13"), mode=ctypes.RTLD_GLOBAL
    )
    _bind_cuda_runtime(cudart)
    _check_cuda(cudart.cudaSetDevice(0), "cudaSetDevice")
    input_device = ctypes.c_void_p()
    output_device = ctypes.c_void_p()
    session = ctypes.c_void_p()
    extension = None
    try:
        _check_cuda(
            cudart.cudaMalloc(ctypes.byref(input_device), PROBE_BYTES),
            "cudaMalloc(input)",
        )
        _check_cuda(
            cudart.cudaMalloc(
                ctypes.byref(output_device), PROBE_THREADS * 8
            ),
            "cudaMalloc(output)",
        )
        input_values = probe_input_values()
        input_array = (ctypes.c_uint64 * PROBE_WORDS)(*input_values)
        _check_cuda(cudart.cudaMemcpy(
            input_device, input_array, PROBE_BYTES,
            CUDA_MEMCPY_HOST_TO_DEVICE,
        ), "cudaMemcpy(input)")

        probe = ctypes.CDLL(
            str(probe_library_path), mode=ctypes.RTLD_GLOBAL
        )
        probe.hbfsim_llama_probe_function.argtypes = []
        probe.hbfsim_llama_probe_function.restype = ctypes.c_void_p
        probe.hbfsim_llama_launch_probe.argtypes = [
            ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint64, ctypes.c_void_p,
        ]
        probe.hbfsim_llama_launch_probe.restype = ctypes.c_int

        # Raise the device to the calibrated nominal clock before an exact
        # owner or HBF range exists. This launch is native and deliberately
        # excluded from exact counters and coverage.
        _check_cuda(probe.hbfsim_llama_launch_probe(
            output_device, input_device, PROBE_NATIVE_PREHEAT_NS, None
        ), "native probe preheat")
        _check_cuda(
            cudart.cudaDeviceSynchronize(), "native preheat synchronize"
        )

        extension = ctypes.CDLL(
            str(extension_path), mode=ctypes.RTLD_GLOBAL
        )
        _bind_extension(extension)
        if extension.hbfsim_vllm_abi_version() != HBFSIM_VLLM_ABI_VERSION:
            raise RuntimeError("HBFSim vLLM extension ABI mismatch")
        profile_bytes = str(profile).encode()
        report_bytes = str(report).encode()
        exact_profile_bytes = str(exact_profile).encode()
        options = _NativeOptions(
            ctypes.sizeof(_NativeOptions), profile_bytes, report_bytes,
            PROBE_RING_CAPACITY, {"reference": 0, "fast": 1, "hybrid": 2}[
                args.hbf_timing_model
            ], PROBE_REQUEST_TIMEOUT_NS, exact_profile_bytes, 1, 1, 1, 1, 1,
        )
        _check_hbfsim(extension, extension.hbfsim_vllm_session_create(
            ctypes.byref(options), ctypes.byref(session)
        ), "exact session creation")
        _check_hbfsim(extension, extension.hbfsim_vllm_register_storage(
            session, input_device.value, PROBE_BYTES
        ), "probe storage registration")
        _atomic_json(report / "registration.json", {
            "schema_version": 1,
            "mode": "exact_sideband_probe",
            "requested_fidelity": "exact",
            "device": 0,
            "registered_bytes": PROBE_BYTES,
            "profile_path": str(profile),
            "exact_profile_path": str(exact_profile),
            "storages": [{
                "address": input_device.value,
                "bytes": PROBE_BYTES,
                "aliases": ["__hbfsim_vllm_exact_probe_shadow__"],
            }],
        })

        ptx = probe_ptx_path.read_bytes()
        function = probe.hbfsim_llama_probe_function()
        if not function:
            raise RuntimeError("exact probe CUDA function is unavailable")
        binder = ctypes.CDLL(None).bpftime_nv_bind_ptx_variant
        binder.argtypes = [
            ctypes.c_void_p, ctypes.c_char_p, ctypes.c_size_t, ctypes.c_char_p,
        ]
        binder.restype = ctypes.c_int
        binding_status = 1
        attempts = 0
        while binding_status == 1 and attempts <= 100:
            binding_status = int(binder(
                ctypes.c_void_p(function), ptx, len(ptx),
                b"hbfsim_llama_probe_kernel",
            ))
            if binding_status == 1 and attempts < 100:
                time.sleep(0.05)
            attempts += 1
        if binding_status != 0:
            raise RuntimeError(
                f"exact probe PTX binding failed: {binding_status}"
            )
        _check_hbfsim(
            extension,
            extension.hbfsim_vllm_publish_exact_contract(session),
            "exact contract publication",
        )
        _check_cuda(probe.hbfsim_llama_launch_probe(
            output_device, input_device, 0, None
        ), "exact probe launch")
        _check_cuda(cudart.cudaDeviceSynchronize(), "cudaDeviceSynchronize")
        output_array = (ctypes.c_uint64 * PROBE_THREADS)()
        _check_cuda(cudart.cudaMemcpy(
            output_array, output_device, PROBE_THREADS * 8,
            CUDA_MEMCPY_DEVICE_TO_HOST,
        ), "cudaMemcpy(output)")
        output_values = list(output_array)
        expected_output = validate_probe_output(
            input_values, output_values
        )
        _check_hbfsim(
            extension, extension.hbfsim_vllm_finalize_exact(session),
            "exact finalization",
        )
        _check_hbfsim(
            extension,
            extension.hbfsim_vllm_session_close(ctypes.byref(session)),
            "exact session close",
        )
        result = {
            "schema_version": 1,
            "status": "passed",
            "scope": "one_shot_sideband_probe",
            "model_graph_fidelity": "native",
            "device": 0,
            "registered_bytes": PROBE_BYTES,
            "input_sha256": hashlib.sha256(bytes(input_array)).hexdigest(),
            "output_sha256": hashlib.sha256(bytes(output_array)).hexdigest(),
            "expected_output_sha256": hashlib.sha256(bytes(
                (ctypes.c_uint64 * PROBE_THREADS)(*expected_output)
            )).hexdigest(),
            "bit_exact": True,
            "native_preheat_ns": PROBE_NATIVE_PREHEAT_NS,
            "binding": {
                "result": "bound",
                "attempts": attempts,
                "ptx_path": str(probe_ptx_path),
                "ptx_sha256": hashlib.sha256(ptx).hexdigest(),
                "original_function": hex(int(function)),
            },
        }
        _atomic_json(report / "exact-probe.json", result)
        return result
    finally:
        if session.value and extension is not None:
            extension.hbfsim_vllm_session_close(ctypes.byref(session))
        if output_device.value:
            cudart.cudaFree(output_device)
        if input_device.value:
            cudart.cudaFree(input_device)


def main() -> int:
    args = parse_args()
    probe = run_exact_probe(args)
    result = merge_probe_result(
        pathlib.Path(args.report_dir).resolve() / "result.json", probe
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
