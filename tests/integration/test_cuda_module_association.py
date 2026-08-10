#!/usr/bin/env python3

import ctypes
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import threading


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def canonical_ptx(identity: bytes) -> str:
    values = ", ".join(f"0x{byte:02x}" for byte in identity)
    return (
        ".version 8.7\n.target sm_120\n.address_size 64\n"
        ".visible .const .align 8 .b8 __hbfsim_module_identity[32] = {"
        f"{values}}};\n.visible .entry kernel() {{ ret; }}\n"
    )


def manifest(identity: bytes) -> dict:
    return {
        "module_id": "ptx:sha256:" + identity.hex(),
        "kernel": "kernel",
        "ptx_target": "sm_120",
        "instrumented": True,
        "cubin_only": False,
        "parameters": [
            {"index": 0, "offset": 0, "width": 8, "kind": "pointer"}
        ],
        "unsupported_parameters": [],
    }


def main() -> int:
    gate = str(pathlib.Path(sys.argv[1]).resolve())
    fake = str(pathlib.Path(sys.argv[2]).resolve())
    plugin = str(pathlib.Path(sys.argv[3]).resolve())
    fixture = str(pathlib.Path(sys.argv[4]).resolve())
    toolkit_version = tuple(int(part) for part in sys.argv[5].split(".")[:2])
    if os.environ.get("HBFSIM_FAKE_MODULE_ACTIVE") != "1":
        with tempfile.TemporaryDirectory(prefix="hbfsim-module-association-") as root:
            environment = os.environ.copy()
            environment["HBFSIM_FAKE_MODULE_ACTIVE"] = "1"
            environment["HBFSIM_PASS_MANIFEST_PATH"] = str(
                pathlib.Path(root) / "manifest.jsonl"
            )
            environment["HBFSIM_COVERAGE_PATH"] = str(
                pathlib.Path(root) / "coverage.jsonl"
            )
            environment["LD_PRELOAD"] = ":".join(
                item for item in (gate, fake, environment.get("LD_PRELOAD", ""))
                if item
            )
            environment["LD_LIBRARY_PATH"] = ":".join(
                item for item in (
                    str(pathlib.Path(fake).parent),
                    environment.get("LD_LIBRARY_PATH", ""),
                ) if item
            )
            return subprocess.run([sys.executable, __file__, gate, fake,
                                   plugin, fixture, sys.argv[5]],
                                  env=environment, check=False).returncode

    manifest_path = pathlib.Path(os.environ["HBFSIM_PASS_MANIFEST_PATH"])
    initial_identity = bytes([0x42]) + bytes(31)
    manifest_path.write_text(json.dumps(manifest(initial_identity)) + "\n")

    process = ctypes.CDLL(None)
    fake_library = ctypes.CDLL(fake)
    add_range = process.hbfsim_coverage_add_range
    add_range.argtypes = [ctypes.c_size_t, ctypes.c_size_t]
    add_range.restype = ctypes.c_int
    require(add_range(0x1000, 0x2000) == 0, "failed to register test HBF range")

    begin = process.hbfsim_begin_module_load_from_ptx
    begin.argtypes = [ctypes.c_char_p, ctypes.c_size_t]
    begin.restype = ctypes.c_uint64
    end = process.hbfsim_end_module_load
    end.argtypes = [ctypes.c_uint64]
    load = process.cuModuleLoadDataEx
    load.argtypes = [ctypes.POINTER(ctypes.c_void_p), ctypes.c_void_p,
                     ctypes.c_uint, ctypes.c_void_p,
                     ctypes.POINTER(ctypes.c_void_p)]
    load.restype = ctypes.c_int
    unload = process.cuModuleUnload
    unload.argtypes = [ctypes.c_void_p]
    unload.restype = ctypes.c_int
    launch = process.cuLaunchKernel
    launch.argtypes = [ctypes.c_void_p] + [ctypes.c_uint] * 7 + [
        ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(ctypes.c_void_p),
    ]
    launch.restype = ctypes.c_int
    fake_library.fakeCudaLaunchCount.restype = ctypes.c_int
    fake_library.fakeCudaSetUnloadFailure.argtypes = [ctypes.c_int]
    fake_library.fakeCudaSetLifecycleFailure.argtypes = [ctypes.c_int]
    fake_library.fakeCudaSetMarkerAvailable.argtypes = [ctypes.c_int]
    fake_library.fakeCudaResetConcurrentLoads.argtypes = []
    fake_library.fakeCudaSetModuleIdentity.argtypes = [
        ctypes.POINTER(ctypes.c_uint8), ctypes.c_size_t,
    ]
    fake_library.fakeCudaSetModuleIdentity.restype = ctypes.c_int

    function = ctypes.c_void_p(0x9000)
    pointer = ctypes.c_size_t(0x1008)
    parameters = (ctypes.c_void_p * 1)(
        ctypes.cast(ctypes.byref(pointer), ctypes.c_void_p)
    )

    def set_live_identity(identity: bytes) -> None:
        identity_buffer = (ctypes.c_uint8 * len(identity)).from_buffer_copy(identity)
        require(fake_library.fakeCudaSetModuleIdentity(
                    identity_buffer, len(identity)) == 0,
                "failed to update fake module identity")

    def begin_ptx(ptx: str) -> int:
        encoded = ptx.encode()
        return begin(encoded, len(encoded))

    def load_image(image: bytes) -> tuple[int, ctypes.c_void_p]:
        module = ctypes.c_void_p()
        storage = ctypes.create_string_buffer(image)
        result = load(ctypes.byref(module), ctypes.cast(storage, ctypes.c_void_p),
                      0, None, None)
        return result, module

    def launch_kernel() -> int:
        return launch(function, 1, 1, 1, 1, 1, 1, 0, None,
                      parameters, None)

    result, spoof = load_image(b"spoof")
    require(result == 0 and launch_kernel() != 0,
            "copied-identity cubin launched without exact load provenance")
    require(unload(spoof) == 0, "failed to unload initial spoof module")

    pass_library = ctypes.CDLL(plugin)
    pass_library.process_input.argtypes = [
        ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p,
    ]
    pass_library.process_input.restype = ctypes.c_int
    request = json.dumps({
        "input": {
            "full_ptx": pathlib.Path(fixture).read_text(),
            "to_patch_kernel": "kernel",
            "global_ebpf_map_info_symbol": "map_info",
            "ebpf_communication_data_symbol": "constData",
        },
        "ebpf_instructions": [],
    }).encode()
    pass_output = ctypes.create_string_buffer(16 * 1024 * 1024)
    require(pass_library.process_input(request, len(pass_output), pass_output) == 0,
            "trusted PTX pass failed before module load")
    response = json.loads(pass_output.value)
    trusted_ptx = response["output_ptx"]
    trusted_identity = bytes.fromhex(
        json.loads(manifest_path.read_text().splitlines()[-1])["module_id"]
        .removeprefix("ptx:sha256:")
    )
    set_live_identity(trusted_identity)

    require(begin_ptx(".visible .entry kernel() { ret; }") == 0,
            "begin accepted PTX without a module identity")
    require(begin_ptx(trusted_ptx + trusted_ptx) == 0,
            "begin accepted duplicate module identities")
    nested = begin_ptx(trusted_ptx)
    require(nested != 0 and begin_ptx(trusted_ptx) == 0,
            "nested begin did not fail closed")
    end(nested)
    result, nested_spoof = load_image(b"nested-spoof")
    require(result == 0 and launch_kernel() != 0,
            "nested begin left stale authorization")
    require(unload(nested_spoof) == 0, "failed to unload nested spoof")

    missing = begin_ptx(trusted_ptx)
    require(missing != 0, "failed to begin missing-marker load")
    fake_library.fakeCudaSetMarkerAvailable(0)
    result, missing_module = load_image(b"missing-marker")
    fake_library.fakeCudaSetMarkerAvailable(1)
    require(result == 0 and launch_kernel() != 0,
            "module without a live marker was associated")
    require(unload(missing_module) == 0, "failed to unload missing-marker module")

    other_identity = bytes([0x7A]) + bytes(31)
    mismatch = begin_ptx(trusted_ptx)
    require(mismatch != 0, "failed to begin mismatched-marker load")
    set_live_identity(other_identity)
    result, mismatch_module = load_image(b"mismatch")
    require(result == 0 and launch_kernel() != 0,
            "mismatched live marker was associated")
    require(unload(mismatch_module) == 0, "failed to unload mismatch module")

    set_live_identity(trusted_identity)
    cancelled = begin_ptx(trusted_ptx)
    require(cancelled != 0, "failed to begin cancelled load")
    end(cancelled)
    result, cancelled_module = load_image(b"cancelled")
    require(result == 0 and launch_kernel() != 0,
            "ended transaction authorized a later load")
    require(unload(cancelled_module) == 0, "failed to unload cancelled module")

    require(begin_ptx(trusted_ptx) != 0, "failed to begin trusted load")
    result, trusted = load_image(b"trusted")
    require(result == 0 and launch_kernel() == 0,
            "exact transformed module load was not associated")
    require(launch_kernel() == 0,
            "module-pool-style reuse lost its live association")

    fake_library.fakeCudaSetUnloadFailure(1)
    require(unload(trusted) != 0 and launch_kernel() == 0,
            "failed unload erased a live association")
    fake_library.fakeCudaSetUnloadFailure(0)
    require(unload(trusted) == 0, "trusted module did not unload")

    result, reused = load_image(b"reuse-spoof")
    require(result == 0 and reused.value == trusted.value and launch_kernel() != 0,
            "reused module handle retained stale authorization")
    require(unload(reused) == 0, "reused spoof module did not unload")

    require(begin_ptx(trusted_ptx) != 0,
            "failed to begin transaction before failed load")
    result, _ = load_image(b"fail")
    require(result != 0, "fake failed module load unexpectedly succeeded")
    result, after_failure = load_image(b"after-failure-spoof")
    require(result == 0 and launch_kernel() != 0,
            "failed module load left reusable trust")
    require(unload(after_failure) == 0, "failed to unload post-failure spoof")

    identity_a = bytes([0xA1]) + bytes(31)
    identity_b = bytes([0xB2]) + bytes(31)
    with manifest_path.open("a") as output:
        output.write(json.dumps(manifest(identity_b)) + "\n")
    set_live_identity(identity_b)
    fake_library.fakeCudaResetConcurrentLoads()
    results: dict[str, tuple[int, ctypes.c_void_p]] = {}

    def concurrent_load(name: str, identity: bytes, image: bytes) -> None:
        require(begin_ptx(canonical_ptx(identity)) != 0,
                f"failed to begin concurrent {name} transaction")
        results[name] = load_image(image)

    thread_a = threading.Thread(
        target=concurrent_load, args=("a", identity_a, b"fail-a"))
    thread_b = threading.Thread(
        target=concurrent_load, args=("b", identity_b, b"success-b"))
    thread_a.start()
    thread_b.start()
    thread_a.join()
    thread_b.join()
    require(results["a"][0] != 0 and results["b"][0] == 0,
            "concurrent fake loads did not split failure and success")
    require(launch_kernel() == 0,
            "A failure cancelled B's successful exact transaction")
    require(unload(results["b"][1]) == 0, "failed to unload concurrent B module")

    set_live_identity(identity_a)
    result, stale_a = load_image(b"stale-a-spoof")
    require(result == 0 and launch_kernel() != 0,
            "failed concurrent A transaction authorized a later load")
    require(unload(stale_a) == 0, "failed to unload stale A spoof")

    driver_lifecycle = [
        ("cuCtxDestroy", ctypes.c_void_p(0xC000)),
        ("cuCtxDestroy_v2", ctypes.c_void_p(0xC000)),
        ("cuDevicePrimaryCtxReset", ctypes.c_int(0)),
        ("cuDevicePrimaryCtxReset_v2", ctypes.c_int(0)),
        ("cuDevicePrimaryCtxRelease", ctypes.c_int(0)),
        ("cuDevicePrimaryCtxRelease_v2", ctypes.c_int(0)),
    ]
    if toolkit_version >= (12, 4):
        driver_lifecycle.append(
            ("cuGreenCtxDestroy", ctypes.c_void_p(0xC100)))
    lifecycle = list(driver_lifecycle)
    lifecycle.append(("cudaDeviceReset", None))
    if toolkit_version < (13, 0):
        lifecycle.append(("cudaThreadExit", None))

    set_live_identity(trusted_identity)
    for symbol, argument in lifecycle:
        require(begin_ptx(trusted_ptx) != 0,
                f"failed to begin trusted load before {symbol}")
        result, before_context_end = load_image(
            f"trusted-before-{symbol}".encode())
        require(result == 0 and launch_kernel() == 0,
                f"failed to establish association before {symbol}")

        function_under_test = getattr(process, symbol)
        function_under_test.restype = ctypes.c_int
        if argument is None:
            function_under_test.argtypes = []
            call = []
        else:
            function_under_test.argtypes = [type(argument)]
            call = [argument]

        fake_library.fakeCudaSetLifecycleFailure(1)
        require(function_under_test(*call) != 0 and launch_kernel() == 0,
                f"failed {symbol} erased a live association")
        fake_library.fakeCudaSetLifecycleFailure(0)
        require(function_under_test(*call) == 0,
                f"successful {symbol} failed in fake driver")

        result, context_reuse = load_image(
            f"reuse-after-{symbol}".encode())
        require(result == 0 and
                context_reuse.value == before_context_end.value and
                launch_kernel() != 0,
                f"successful {symbol} retained stale authorization")
        require(unload(context_reuse) == 0,
                f"failed to unload spoof after {symbol}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
