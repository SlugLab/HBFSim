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


def canonical_ptx(identity: bytes) -> bytes:
    values = ", ".join(f"0x{byte:02x}" for byte in identity)
    return (
        ".version 8.7\n.target sm_120\n.address_size 64\n"
        ".visible .const .align 8 .b8 __hbfsim_module_identity[32] = {"
        f"{values}}};\n.visible .entry kernel() {{ ret; }}\n"
    ).encode()


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
        ("register_timing_range", Register),
        ("begin_retire", BeginRetire),
        ("invalidate_retire", FinishRetire),
        ("finish_retire", FinishRetire),
        ("quarantine_retire", FinishRetire),
    ]


def main() -> int:
    gate = str(pathlib.Path(sys.argv[1]).resolve())
    fake = str(pathlib.Path(sys.argv[2]).resolve())
    if os.environ.get("HBFSIM_TIMING_GATE_TEST") != "1":
        with tempfile.TemporaryDirectory(prefix="hbfsim-timing-gate-") as root:
            environment = os.environ.copy()
            environment["HBFSIM_TIMING_GATE_TEST"] = "1"
            environment["HBFSIM_PASS_MANIFEST_PATH"] = str(
                pathlib.Path(root) / "manifest.jsonl")
            environment["HBFSIM_COVERAGE_PATH"] = str(
                pathlib.Path(root) / "coverage.jsonl")
            environment["LD_PRELOAD"] = ":".join(
                item for item in (gate, fake, environment.get("LD_PRELOAD", ""))
                if item)
            environment["LD_LIBRARY_PATH"] = ":".join(
                item for item in (str(pathlib.Path(fake).parent),
                                  environment.get("LD_LIBRARY_PATH", ""))
                if item)
            no_range_environment = environment.copy()
            no_range_environment["HBFSIM_UNBOUND_NO_RANGE"] = "1"
            no_range = subprocess.run(
                [sys.executable, __file__, gate, fake],
                env=no_range_environment, check=False)
            if no_range.returncode != 0:
                return no_range.returncode
            unbound_environment = environment.copy()
            unbound_environment["HBFSIM_PRECONTEXT_UNBOUND"] = "1"
            unbound = subprocess.run(
                [sys.executable, __file__, gate, fake],
                env=unbound_environment, check=False)
            if unbound.returncode != 0:
                return unbound.returncode
            rollback_environment = environment.copy()
            rollback_environment["HBFSIM_GATE_ROLLBACK"] = "1"
            rollback = subprocess.run(
                [sys.executable, __file__, gate, fake],
                env=rollback_environment, check=False)
            if rollback.returncode != 0:
                return rollback.returncode
            lifecycle_environment = environment.copy()
            lifecycle_environment["HBFSIM_LIFECYCLE_ACTIVATE_RACE"] = "1"
            lifecycle = subprocess.run(
                [sys.executable, __file__, gate, fake],
                env=lifecycle_environment, check=False)
            if lifecycle.returncode != 0:
                return lifecycle.returncode
            nested_environment = environment.copy()
            nested_environment["HBFSIM_NESTED_RUNTIME_RESET"] = "1"
            try:
                nested = subprocess.run(
                    [sys.executable, __file__, gate, fake],
                    env=nested_environment, check=False, timeout=2)
            except subprocess.TimeoutExpired as error:
                raise RuntimeError(
                    "nested runtime/driver lifecycle transition deadlocked") \
                    from error
            if nested.returncode != 0:
                return nested.returncode
            environment.pop("HBFSIM_UNBOUND_NO_RANGE", None)
            environment.pop("HBFSIM_PRECONTEXT_UNBOUND", None)
            environment.pop("HBFSIM_GATE_ROLLBACK", None)
            return subprocess.run(
                [sys.executable, __file__, gate, fake], env=environment,
                check=False).returncode

    identity = bytes([0x5A]) + bytes(31)
    pathlib.Path(os.environ["HBFSIM_PASS_MANIFEST_PATH"]).write_text(
        json.dumps({
            "module_id": "ptx:sha256:" + identity.hex(),
            "kernel": "kernel",
            "ptx_target": "sm_120",
            "instrumented": True,
            "cubin_only": False,
            "parameters": [
                {"index": 0, "offset": 0, "width": 8, "kind": "pointer"}
            ],
            "unsupported_parameters": [],
        }) + "\n")

    process = ctypes.CDLL(None)
    fake_library = ctypes.CDLL(fake)
    getter = process.hbfsim_launch_gate_get_api
    getter.argtypes = [ctypes.c_uint32]
    getter.restype = ctypes.POINTER(GateApi)
    api_pointer = getter(1)
    require(bool(api_pointer), "launch gate v1 API unavailable")
    api = api_pointer.contents
    require(api.abi_version == 1 and api.struct_bytes == ctypes.sizeof(GateApi),
            "launch gate returned malformed v1 API")
    require(not bool(getter(2)), "launch gate accepted an unknown API version")

    fake_library.fakeCudaSetModuleIdentity.argtypes = [
        ctypes.POINTER(ctypes.c_uint8), ctypes.c_size_t]
    identity_buffer = (ctypes.c_uint8 * len(identity)).from_buffer_copy(identity)
    require(fake_library.fakeCudaSetModuleIdentity(
                identity_buffer, len(identity)) == 0,
            "failed to configure fake module identity")
    fake_library.fakeCudaSetCurrentDomain.argtypes = [ctypes.c_size_t,
                                                       ctypes.c_int]
    fake_library.fakeCudaSetControlSymbolsAvailable.argtypes = [ctypes.c_int]
    fake_library.fakeCudaSetControlCopyFailure.argtypes = [ctypes.c_int]
    fake_library.fakeCudaSetControlCopyFailurePosition.argtypes = [ctypes.c_int]
    fake_library.fakeCudaControlAlias.restype = ctypes.c_uint64
    fake_library.fakeCudaControlGeneration.restype = ctypes.c_uint64
    fake_library.fakeCudaSetCurrentDomain(0xCA00, 3)

    if os.environ.get("HBFSIM_NESTED_RUNTIME_RESET") == "1":
        fake_library.fakeCudaSetNestedRuntimeReset.argtypes = [ctypes.c_int]
        fake_library.fakeCudaSetNestedRuntimeReset(1)
        reset = process.cudaDeviceReset
        reset.argtypes = []
        reset.restype = ctypes.c_int
        require(reset() == 0,
                "nested runtime/driver lifecycle reset failed")
        return 0

    if os.environ.get("HBFSIM_LIFECYCLE_ACTIVATE_RACE") == "1":
        fake_library.fakeCudaPauseLifecycle.restype = None
        fake_library.fakeCudaWaitLifecycleEntered.restype = None
        fake_library.fakeCudaReleaseLifecycle.restype = None
        destroy = process.cuCtxDestroy
        destroy.argtypes = [ctypes.c_void_p]
        destroy.restype = ctypes.c_int
        fake_library.fakeCudaPauseLifecycle()
        lifecycle_result = ctypes.c_int(-1)

        def run_lifecycle() -> None:
            lifecycle_result.value = destroy(ctypes.c_void_p(0xCA00))

        lifecycle_thread = threading.Thread(target=run_lifecycle)
        lifecycle_thread.start()
        fake_library.fakeCudaWaitLifecycleEntered()

        generation = ctypes.c_uint64()
        activation_result = ctypes.c_int(-1)
        activation_done = threading.Event()
        arm_activation = process.hbfsim_test_arm_activation_attempt
        arm_activation.restype = None
        wait_activation = process.hbfsim_test_wait_activation_attempt
        wait_activation.restype = ctypes.c_int
        arm_activation()

        def run_activation() -> None:
            activation_result.value = api.activate(
                0xA000, 0xFEED0000, 0xCA00, 3,
                ctypes.byref(generation))
            activation_done.set()

        activation_thread = threading.Thread(target=run_activation)
        activation_thread.start()
        require(wait_activation() == 1,
                "activation did not contend on the lifecycle transition lock")
        activated_while_lifecycle_paused = activation_done.is_set()
        fake_library.fakeCudaReleaseLifecycle()
        lifecycle_thread.join()
        activation_thread.join()
        require(not activated_while_lifecycle_paused,
                "activation completed inside lifecycle precheck/driver gap")
        require(lifecycle_result.value == 0,
                "paused context lifecycle operation failed")
        require(activation_result.value != 0 and generation.value == 0,
                "stale post-lifecycle CUDA domain was activated")
        fake_library.fakeCudaSetCurrentDomain(0xCB00, 4)
        require(api.activate(0xA000, 0xFEED0000, 0xCB00, 4,
                             ctypes.byref(generation)) == 0 and
                generation.value != 0,
                "fresh post-lifecycle CUDA domain did not recover")
        token = ctypes.c_size_t()
        require(api.begin_retire(0xA000, generation.value,
                                 ctypes.byref(token)) == 0 and
                api.invalidate_retire(token.value) == 0 and
                api.finish_retire(token.value) == 0,
                "post-lifecycle owner could not retire cleanly")
        return 0

    begin = process.hbfsim_begin_module_load_from_ptx
    begin.argtypes = [ctypes.c_char_p, ctypes.c_size_t]
    begin.restype = ctypes.c_uint64
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
        ctypes.POINTER(ctypes.c_void_p)]
    launch.restype = ctypes.c_int
    pointer = ctypes.c_size_t(0x1008)
    parameters = (ctypes.c_void_p * 1)(
        ctypes.cast(ctypes.byref(pointer), ctypes.c_void_p))

    def load_trusted(image: bytes) -> ctypes.c_void_p:
        ptx = canonical_ptx(identity)
        require(begin(ptx, len(ptx)) != 0, "trusted load transaction failed")
        module = ctypes.c_void_p()
        storage = ctypes.create_string_buffer(image)
        require(load(ctypes.byref(module), storage, 0, None, None) == 0,
                "fake trusted module load failed")
        return module

    def launch_kernel() -> int:
        return launch(ctypes.c_void_p(0x9000), 1, 1, 1, 1, 1, 1, 0,
                      None, parameters, None)

    if os.environ.get("HBFSIM_UNBOUND_NO_RANGE") == "1":
        fake_library.fakeCudaSetControlSymbolsAvailable(0)
        unbound = load_trusted(b"unbound-without-ranges")
        require(launch_kernel() == 0,
                "unbound trusted module was rejected without a relevant range")
        require(unload(unbound) == 0,
                "failed to unload no-range unbound module")
        return 0

    if os.environ.get("HBFSIM_PRECONTEXT_UNBOUND") == "1":
        fake_library.fakeCudaSetControlSymbolsAvailable(0)
        pre_context_unbound = load_trusted(b"pre-context-unbound")
        generation = ctypes.c_uint64()
        require(api.activate(0xA000, 0xFEED0000, 0xCA00, 3,
                             ctypes.byref(generation)) == 0,
                "unbound pre-context module blocked owner activation")

        @Publish
        def publish_unbound(_state: ctypes.c_void_p) -> int:
            return 0

        require(api.register_timing_range(
                    0xA000, generation.value, 0x1000, 0x2000,
                    publish_unbound, None) == 0,
                "unbound pre-context phase range registration failed")
        require(launch_kernel() != 0,
                "unbound pre-context module launched over a timing range")
        token = ctypes.c_size_t()
        require(api.begin_retire(0xA000, generation.value,
                                 ctypes.byref(token)) == 0,
                "unbound pre-context owner did not quiesce")
        require(api.invalidate_retire(token.value) == 0 and
                api.finish_retire(token.value) == 0,
                "unbound module incorrectly quarantined clean retirement")
        require(unload(pre_context_unbound) == 0,
                "failed to unload pre-context unbound module")
        return 0

    if os.environ.get("HBFSIM_GATE_ROLLBACK") == "1":
        generation = ctypes.c_uint64()
        require(api.activate(0xA000, 0xFEED0000, 0xCA00, 3,
                             ctypes.byref(generation)) == 0,
                "rollback phase activation failed")
        module_for_rollback = load_trusted(b"rollback-module")
        rejected_before = ctypes.c_int(0)
        rejected_after = ctypes.c_int(0)
        accepted = ctypes.c_int(0)

        @Publish
        def reject_before(_state: ctypes.c_void_p) -> int:
            rejected_before.value += 1
            return -1

        @Publish
        def reject_after(_state: ctypes.c_void_p) -> int:
            rejected_after.value += 1
            return -1

        @Publish
        def accept_once(_state: ctypes.c_void_p) -> int:
            accepted.value += 1
            return 0

        require(api.register_timing_range(
                    0xA000, generation.value, 0x3000, 0x4000,
                    reject_before, None) != 0 and rejected_before.value == 1,
                "error-before callback was not invoked exactly once")
        require(api.register_timing_range(
                    0xA000, generation.value, 0x4000, 0x5000,
                    reject_after, None) != 0 and rejected_after.value == 1,
                "error-after callback was not invoked exactly once")
        require(api.register_timing_range(
                    0xA000, generation.value, 0x6000, 0x7000,
                    accept_once, None) == 0 and accepted.value == 1,
                "successful publication was not invoked exactly once")
        fake_library.fakeCudaSetCurrentDomain(0xCB00, 3)
        for address in (0x3008, 0x4008):
            pointer.value = address
            require(launch_kernel() == 0,
                    "rejected publication remained staged in the gate")
        pointer.value = 0x6008
        require(launch_kernel() != 0,
                "acknowledged publication was absent from the gate")
        fake_library.fakeCudaSetCurrentDomain(0xCA00, 3)
        require(unload(module_for_rollback) == 0,
                "failed to unload rollback module")
        return 0

    module = load_trusted(b"timing-before-context")
    require(fake_library.fakeCudaControlAlias() == 0 and
            fake_library.fakeCudaControlGeneration() == 0,
            "pre-context module was not initialized fail-closed")
    generation_a = ctypes.c_uint64()
    require(api.activate(0xA000, 0xFEED0000, 0xCA00, 3,
                         ctypes.byref(generation_a)) == 0 and
            generation_a.value != 0,
            "first timing owner activation failed")
    require(fake_library.fakeCudaControlAlias() == 0xFEED0000 and
            fake_library.fakeCudaControlGeneration() == generation_a.value,
            "pre-context module did not retro-bind")
    for symbol in ("cuDevicePrimaryCtxReset",
                   "cuDevicePrimaryCtxReset_v2",
                   "cuDevicePrimaryCtxRelease",
                   "cuDevicePrimaryCtxRelease_v2"):
        primary_lifecycle = getattr(process, symbol)
        primary_lifecycle.argtypes = [ctypes.c_int]
        primary_lifecycle.restype = ctypes.c_int
        require(primary_lifecycle(3) != 0,
                f"same-owner {symbol} reached the CUDA driver")
        require(primary_lifecycle(4) == 0 and
                fake_library.fakeCudaControlAlias() == 0xFEED0000,
                f"foreign-device {symbol} damaged owner binding")
    runtime_reset = process.cudaDeviceReset
    runtime_reset.restype = ctypes.c_int
    require(runtime_reset() != 0 and
            fake_library.fakeCudaControlAlias() == 0xFEED0000,
            "owner cudaDeviceReset bypassed managed teardown")
    green_destroy = process.cuGreenCtxDestroy
    green_destroy.argtypes = [ctypes.c_void_p]
    green_destroy.restype = ctypes.c_int
    require(green_destroy(ctypes.c_void_p(0xC200)) == 0 and
            fake_library.fakeCudaControlAlias() == 0xFEED0000,
            "foreign green-context destroy damaged owner binding")
    require(green_destroy(ctypes.c_void_p(0xC100)) != 0,
            "mapped owner green context was destroyed outside teardown")
    rejected_generation = ctypes.c_uint64()
    require(api.activate(0xB000, 0xBEEF0000, 0xCB00, 4,
                         ctypes.byref(rejected_generation)) != 0,
            "second active timing owner was accepted")

    published = ctypes.c_int(0)

    @Publish
    def publish(_state: ctypes.c_void_p) -> int:
        published.value += 1
        return 0

    require(api.register_timing_range(
                0xA000, generation_a.value, 0x1000, 0x2000, publish,
                None) == 0 and published.value == 1,
            "range transaction did not publish exactly once")
    require(launch_kernel() == 0, "bound trusted module launch was rejected")
    fake_library.fakeCudaSetCurrentDomain(0xCB00, 3)
    require(launch_kernel() != 0, "foreign CUDA context used a bound module")
    fake_library.fakeCudaSetCurrentDomain(0xCA00, 3)

    require(unload(module) == 0, "failed to unload pre-context module")
    module = load_trusted(b"timing-after-context")
    require(launch_kernel() == 0, "post-context module was not bound")
    require(unload(module) == 0, "failed to unload post-context module")

    fake_library.fakeCudaSetControlSymbolsAvailable(0)
    missing = load_trusted(b"timing-missing-symbol")
    require(launch_kernel() != 0,
            "module missing the exact control symbols was authorized")
    fake_library.fakeCudaSetControlSymbolsAvailable(1)
    require(unload(missing) == 0, "failed to unload missing-symbol module")

    fake_library.fakeCudaSetControlCopyFailure(1)
    copy_failed = load_trusted(b"timing-copy-failure")
    require(launch_kernel() != 0,
            "module with failed control initialization was authorized")
    fake_library.fakeCudaSetControlCopyFailure(0)
    require(unload(copy_failed) == 0, "failed to unload copy-failure module")

    for position in (1, 2):
        fake_library.fakeCudaSetControlCopyFailurePosition(position)
        ordered_failure = load_trusted(
            f"timing-ordered-copy-failure-{position}".encode())
        require(fake_library.fakeCudaControlAlias() == 0,
                "failed binding write exposed a usable control alias")
        require(launch_kernel() != 0,
                "partially initialized control binding was authorized")
        fake_library.fakeCudaSetControlCopyFailurePosition(0)
        require(unload(ordered_failure) == 0,
                "failed to unload ordered-copy-failure module")

    module = load_trusted(b"timing-before-retire")
    retire_token = ctypes.c_size_t()
    require(api.begin_retire(0xA000, generation_a.value,
                             ctypes.byref(retire_token)) == 0 and
            retire_token.value != 0,
            "timing owner did not quiesce")
    require(api.invalidate_retire(retire_token.value) == 0,
            "timing owner invalidation failed")
    blocked_generation = ctypes.c_uint64()
    require(api.activate(0xB000, 0xBEEF0000, 0xCA00, 3,
                         ctypes.byref(blocked_generation)) != 0,
            "new owner activated before cleanup finalized")
    require(api.finish_retire(retire_token.value) == 0,
            "timing owner retirement finalization failed")
    require(fake_library.fakeCudaControlAlias() == 0 and
            fake_library.fakeCudaControlGeneration() == 0,
            "retirement left a dangling module control binding")

    generation_b = ctypes.c_uint64()
    require(api.activate(0xB000, 0xBEEF0000, 0xCA00, 3,
                         ctypes.byref(generation_b)) == 0 and
            generation_b.value > generation_a.value,
            "clean sequential owner activation failed")
    published.value = 0
    require(api.register_timing_range(
                0xB000, generation_b.value, 0x1000, 0x2000, publish,
                None) == 0 and published.value == 1,
            "sequential owner range transaction failed")
    require(launch_kernel() == 0,
            "sequential owner did not receive the live binding")
    require(unload(module) == 0, "failed to unload sequential owner module")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
