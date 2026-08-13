#!/usr/bin/env python3

import ctypes
import hashlib
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


Publish = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_void_p)
Activate = ctypes.CFUNCTYPE(
    ctypes.c_int, ctypes.c_size_t, ctypes.c_size_t, ctypes.c_size_t,
    ctypes.c_int, ctypes.POINTER(ctypes.c_uint64))
Register = ctypes.CFUNCTYPE(
    ctypes.c_int, ctypes.c_size_t, ctypes.c_uint64, ctypes.c_size_t,
    ctypes.c_size_t, Publish, ctypes.c_void_p)
BeginRetire = ctypes.CFUNCTYPE(
    ctypes.c_int, ctypes.c_size_t, ctypes.c_uint64,
    ctypes.POINTER(ctypes.c_size_t))
FinishRetire = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_size_t)


class GateApi(ctypes.Structure):
    _fields_ = [
        ("abi_version", ctypes.c_uint32),
        ("struct_bytes", ctypes.c_uint32),
        ("activate", Activate),
        ("register_range", Register),
        ("unregister_range", Register),
        ("begin_retire", BeginRetire),
        ("invalidate_retire", FinishRetire),
        ("finish_retire", FinishRetire),
        ("quarantine_retire", FinishRetire),
    ]


def canonical_ptx(identity: bytes, ptx_target: str) -> str:
    values = ", ".join(f"0x{byte:02x}" for byte in identity)
    return (
        f".version 8.7\n.target {ptx_target}\n.address_size 64\n"
        ".visible .const .align 8 .b8 __hbfsim_module_identity[32] = {"
        f"{values}}};\n.visible .entry kernel() {{ ret; }}\n"
    )


def manifest(identity: bytes, ptx_target: str) -> dict:
    return {
        "module_id": "ptx:sha256:" + identity.hex(),
        "kernel": "kernel",
        "ptx_target": ptx_target,
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
    ptx_target = sys.argv[6]
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
                                   plugin, fixture, sys.argv[5], ptx_target],
                                  env=environment, check=False).returncode

    manifest_path = pathlib.Path(os.environ["HBFSIM_PASS_MANIFEST_PATH"])
    initial_identity = bytes([0x42]) + bytes(31)
    manifest_path.write_text(
        json.dumps(manifest(initial_identity, ptx_target)) + "\n"
    )

    process = ctypes.CDLL(None)
    fake_library = ctypes.CDLL(fake)
    getter = process.hbfsim_launch_gate_get_api
    getter.argtypes = [ctypes.c_uint32]
    getter.restype = ctypes.POINTER(GateApi)
    api_pointer = getter(2)
    require(bool(api_pointer), "launch gate v2 API unavailable")
    api = api_pointer.contents
    fake_library.fakeCudaSetCurrentDomain.argtypes = [ctypes.c_size_t,
                                                       ctypes.c_int]
    fake_library.fakeCudaSetCurrentDomain(0xCA00, 3)
    generation = ctypes.c_uint64()
    require(api.activate(0xA000, 0xFEED0000, 0xCA00, 3,
                         ctypes.byref(generation)) == 0,
            "failed to activate test timing owner")

    @Publish
    def publish(_state: ctypes.c_void_p) -> int:
        return 0

    require(api.register_range(
                0xA000, generation.value, 0x1000, 0x2000, publish, None) == 0,
            "failed to register owned test HBF range")

    begin = process.hbfsim_begin_module_load_from_ptx
    begin.argtypes = [ctypes.c_char_p, ctypes.c_size_t]
    begin.restype = ctypes.c_uint64
    begin_aot = process.hbfsim_begin_module_load_from_aot
    begin_aot.argtypes = [ctypes.c_void_p, ctypes.c_size_t,
                          ctypes.c_char_p, ctypes.c_size_t]
    begin_aot.restype = ctypes.c_uint64
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
    fake_library.fakeCudaSetModuleIdentity.argtypes = [
        ctypes.POINTER(ctypes.c_uint8), ctypes.c_size_t,
    ]
    fake_library.fakeCudaSetModuleIdentity.restype = ctypes.c_int
    aot_verified = process.hbfsim_test_module_aot_verified
    aot_verified.argtypes = [ctypes.c_void_p]
    aot_verified.restype = ctypes.c_int

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
        storage = ctypes.create_string_buffer(image)
        return load_storage(storage)

    def load_storage(storage: ctypes.Array) -> tuple[int, ctypes.c_void_p]:
        module = ctypes.c_void_p()
        result = load(ctypes.byref(module), ctypes.cast(storage, ctypes.c_void_p),
                      0, None, None)
        return result, module

    def artifact_for(image: bytes, identity: bytes) -> bytes:
        return json.dumps({
            "schema_version": 1,
            "module_id": "ptx:sha256:" + identity.hex(),
            "ptx_target": ptx_target,
            "toolchain": {
                "cuda_release": "13.0",
                "ptxas_version": "ptxas release 13.0, V13.0.88",
                "nvdisasm_version": "nvdisasm release 13.0, V13.0.85",
                "cuobjdump_version": "cuobjdump release 13.0, V13.0.85",
            },
            "hashes": {
                "original_ptx_sha256": "22" * 32,
                "transformed_ptx_sha256": "33" * 32,
                "cubin_sha256": hashlib.sha256(image).hexdigest(),
                "sass_sha256": "55" * 32,
            },
            "kernels": [{
                "name": "kernel", "registers": 48,
                "spill_store_bytes": 0, "spill_load_bytes": 0,
                "static_shared_bytes": 1024,
                "max_dynamic_shared_bytes": 49152,
                "block_threads": 256, "occupancy_blocks_per_sm": 2,
            }],
        }).encode()

    def begin_aot_storage(storage: ctypes.Array, image: bytes,
                          identity: bytes, artifact: bytes | None = None) -> int:
        record = artifact if artifact is not None else artifact_for(image, identity)
        return begin_aot(ctypes.cast(storage, ctypes.c_void_p), len(image),
                         record, len(record))

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
            "full_ptx": pathlib.Path(fixture).read_text().replace(
                ".target sm_120", f".target {ptx_target}", 1
            ),
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

    aot_image = b"\x7fELF-trusted-aot"
    aot_storage = ctypes.create_string_buffer(aot_image)
    require(begin_aot_storage(aot_storage, aot_image, trusted_identity) != 0,
            "failed to begin trusted AOT load")
    result, trusted_aot = load_storage(aot_storage)
    require(result == 0 and launch_kernel() == 0,
            "byte-exact AOT module was not associated")
    require(aot_verified(trusted_aot) == 1,
            "AOT association lost verified artifact evidence")
    require(unload(trusted_aot) == 0, "failed to unload trusted AOT module")

    pointer_image = b"\x7fELF-pointer-aot"
    authorized_storage = ctypes.create_string_buffer(pointer_image)
    require(begin_aot_storage(authorized_storage, pointer_image,
                              trusted_identity) != 0,
            "failed to begin pointer mismatch test")
    result, pointer_mismatch = load_image(pointer_image)
    require(result == 0 and launch_kernel() != 0,
            "different cubin pointer consumed AOT authorization")
    require(unload(pointer_mismatch) == 0,
            "failed to unload pointer mismatch module")

    tamper_image = b"\x7fELF-tamper-aot"
    tamper_storage = ctypes.create_string_buffer(tamper_image)
    require(begin_aot_storage(tamper_storage, tamper_image,
                              trusted_identity) != 0,
            "failed to begin AOT tamper test")
    tamper_storage[0] = b"X"
    result, tampered_aot = load_storage(tamper_storage)
    require(result == 0 and launch_kernel() != 0,
            "mutated cubin consumed AOT authorization")
    require(unload(tampered_aot) == 0, "failed to unload tampered AOT module")

    wrong_record = json.loads(artifact_for(aot_image, trusted_identity))
    wrong_record["hashes"]["cubin_sha256"] = "00" * 32
    wrong_bytes = json.dumps(wrong_record).encode()
    rejected_storage = ctypes.create_string_buffer(aot_image)
    require(begin_aot_storage(rejected_storage, aot_image, trusted_identity,
                              wrong_bytes) == 0,
            "AOT begin accepted a wrong cubin digest")

    wrong_marker_storage = ctypes.create_string_buffer(aot_image)
    require(begin_aot_storage(wrong_marker_storage, aot_image,
                              trusted_identity) != 0,
            "failed to begin wrong marker AOT test")
    set_live_identity(bytes([0x7B]) + bytes(31))
    result, wrong_marker_aot = load_storage(wrong_marker_storage)
    require(result == 0 and launch_kernel() != 0,
            "AOT load with wrong live marker was associated")
    require(unload(wrong_marker_aot) == 0,
            "failed to unload wrong-marker AOT module")
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
        output.write(json.dumps(manifest(identity_b, ptx_target)) + "\n")
    set_live_identity(identity_b)
    results: dict[str, tuple[int, ctypes.c_void_p]] = {}
    transactions_ready = threading.Barrier(2)

    def concurrent_load(name: str, identity: bytes, image: bytes) -> None:
        require(begin_ptx(canonical_ptx(identity, ptx_target)) != 0,
                f"failed to begin concurrent {name} transaction")
        transactions_ready.wait()
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
        ("cuCtxDestroy", ctypes.c_void_p(0xCB00)),
        ("cuCtxDestroy_v2", ctypes.c_void_p(0xCB00)),
        ("cuCtxDetach", ctypes.c_void_p(0xCB00)),
        ("cuDevicePrimaryCtxReset", ctypes.c_int(4)),
        ("cuDevicePrimaryCtxReset_v2", ctypes.c_int(4)),
        ("cuDevicePrimaryCtxRelease", ctypes.c_int(4)),
        ("cuDevicePrimaryCtxRelease_v2", ctypes.c_int(4)),
    ]
    if toolkit_version >= (12, 4):
        driver_lifecycle.append(
            ("cuGreenCtxDestroy", ctypes.c_void_p(0xC200)))

    set_live_identity(trusted_identity)
    require(begin_ptx(trusted_ptx) != 0,
            "failed to establish trusted module before lifecycle checks")
    result, before_context_end = load_image(b"trusted-before-lifecycle")
    require(result == 0 and launch_kernel() == 0,
            "failed to establish lifecycle association")
    for symbol, argument in driver_lifecycle:
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
        require(function_under_test(*call) == 0 and launch_kernel() == 0,
                f"foreign {symbol} erased owner-context state")

    owner_destroy = process.cuCtxDestroy
    owner_destroy.argtypes = [ctypes.c_void_p]
    owner_destroy.restype = ctypes.c_int
    require(owner_destroy(ctypes.c_void_p(0xCA00)) != 0 and
            launch_kernel() == 0,
            "owner-context destroy bypassed managed teardown")
    require(unload(before_context_end) == 0,
            "failed to unload lifecycle module")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
