#!/usr/bin/env python3

import ctypes
import json
import os
import pathlib
import subprocess
import sys
import tempfile


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def main() -> int:
    gate = str(pathlib.Path(sys.argv[1]).resolve())
    fake = str(pathlib.Path(sys.argv[2]).resolve())
    plugin = str(pathlib.Path(sys.argv[3]).resolve())
    fixture = str(pathlib.Path(sys.argv[4]).resolve())
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
                                   plugin, fixture],
                                  env=environment, check=False).returncode

    identity = bytes([0x42]) + bytes(31)
    module_id = "ptx:sha256:" + identity.hex()
    pathlib.Path(os.environ["HBFSIM_PASS_MANIFEST_PATH"]).write_text(
        json.dumps({
            "module_id": module_id,
            "kernel": "kernel",
            "ptx_target": "sm_120",
            "instrumented": True,
            "cubin_only": False,
            "parameters": [
                {"index": 0, "offset": 0, "width": 8, "kind": "pointer"}
            ],
            "unsupported_parameters": [],
        }) + "\n"
    )

    process = ctypes.CDLL(None)
    fake_library = ctypes.CDLL(fake)
    add_range = process.hbfsim_coverage_add_range
    add_range.argtypes = [ctypes.c_size_t, ctypes.c_size_t]
    add_range.restype = ctypes.c_int
    require(add_range(0x1000, 0x2000) == 0, "failed to register test HBF range")

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
    fake_library.fakeCudaSetModuleIdentity.argtypes = [
        ctypes.POINTER(ctypes.c_uint8), ctypes.c_size_t,
    ]
    fake_library.fakeCudaSetModuleIdentity.restype = ctypes.c_int

    function = ctypes.c_void_p(0x9000)
    pointer = ctypes.c_size_t(0x1008)
    parameters = (ctypes.c_void_p * 1)(
        ctypes.cast(ctypes.byref(pointer), ctypes.c_void_p)
    )

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
    require(result == 0, "fake spoof module did not load")
    require(launch_kernel() != 0 and fake_library.fakeCudaLaunchCount() == 0,
            "copied-identity cubin launched without trusted provenance")
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
    manifests = [
        json.loads(line) for line in
        pathlib.Path(os.environ["HBFSIM_PASS_MANIFEST_PATH"]).read_text().splitlines()
        if line
    ]
    identity = bytes.fromhex(
        manifests[-1]["module_id"].removeprefix("ptx:sha256:")
    )
    identity_buffer = (ctypes.c_uint8 * len(identity)).from_buffer_copy(identity)
    require(fake_library.fakeCudaSetModuleIdentity(
                identity_buffer, len(identity)) == 0,
            "failed to update fake module's transformed identity")

    expect = process.hbfsim_expect_module_identity
    expect.argtypes = [ctypes.POINTER(ctypes.c_uint8), ctypes.c_size_t]
    expect.restype = ctypes.c_int
    result, trusted = load_image(b"trusted")
    require(result == 0 and launch_kernel() == 0,
            "pass-published transformed module was not associated")
    require(launch_kernel() == 0 and fake_library.fakeCudaLaunchCount() == 2,
            "bpftime-style cached module reuse lost its association")
    repeated_output = ctypes.create_string_buffer(16 * 1024 * 1024)
    require(pass_library.process_input(
                request, len(repeated_output), repeated_output) == 0,
            "repeated pass failed during module-cache reuse")

    fake_library.fakeCudaSetUnloadFailure(1)
    require(unload(trusted) != 0 and launch_kernel() == 0,
            "failed unload erased a live module association")
    fake_library.fakeCudaSetUnloadFailure(0)
    require(unload(trusted) == 0, "trusted module did not unload")

    result, reused = load_image(b"reuse-spoof")
    require(result == 0 and reused.value == trusted.value,
            "fake driver did not reuse its module handle")
    require(launch_kernel() != 0 and fake_library.fakeCudaLaunchCount() == 3,
            "reused module handle retained stale authorization")
    require(unload(reused) == 0, "reused spoof module did not unload")

    require(expect(identity_buffer, len(identity)) == 0,
            "failed to publish identity before failed load")
    result, _ = load_image(b"fail")
    require(result != 0, "fake failed module load unexpectedly succeeded")
    result, after_failure = load_image(b"after-failure-spoof")
    require(result == 0 and launch_kernel() != 0,
            "failed module load left reusable pending trust")
    require(unload(after_failure) == 0, "final spoof module did not unload")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
