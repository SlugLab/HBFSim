#!/usr/bin/env python3

import pathlib
import re
import subprocess
import sys


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def function_body(source: str, signature: str) -> str:
    start = source.find(signature)
    require(start >= 0, f"missing source function: {signature}")
    brace = source.find("{", start)
    require(brace >= 0, f"missing function body: {signature}")
    depth = 0
    for index in range(brace, len(source)):
        if source[index] == "{":
            depth += 1
        elif source[index] == "}":
            depth -= 1
            if depth == 0:
                return source[brace:index + 1]
    raise RuntimeError(f"unterminated function body: {signature}")


def main() -> int:
    library = pathlib.Path(sys.argv[1])
    output = subprocess.run(
        [sys.argv[2], "-D", "--defined-only", str(library)],
        check=True, text=True, capture_output=True
    ).stdout
    exported = {line.split()[-1] for line in output.splitlines() if line.split()}
    toolkit_version = tuple(
        int(part) for part in sys.argv[5].split(".")[:2])
    required = {
        "cuLaunchKernel", "cuLaunchKernel_ptsz",
        "cuLaunchKernelEx", "cuLaunchKernelEx_ptsz",
        "cuLaunchCooperativeKernel", "cuLaunchCooperativeKernel_ptsz",
        "cuLaunchCooperativeKernelMultiDevice",
        "cuLaunch", "cuLaunchGrid", "cuLaunchGridAsync",
        "cuGraphLaunch", "cuGraphLaunch_ptsz",
        "cudaLaunchKernel", "cudaLaunchKernel_ptsz",
        "cudaLaunchKernelExC", "cudaLaunchKernelExC_ptsz",
        "cudaLaunchCooperativeKernel", "cudaLaunchCooperativeKernel_ptsz",
        "cudaGraphLaunch", "cudaGraphLaunch_ptsz",
        "__cudaLaunchKernel", "__cudaLaunchKernel_ptsz",
        "cuGetProcAddress", "cuGetProcAddress_v2",
        "cudaGetDriverEntryPoint", "cudaGetDriverEntryPoint_ptsz",
        "cudaGetDriverEntryPointByVersion",
        "cudaGetDriverEntryPointByVersion_ptsz",
        "cuModuleLoadDataEx", "cuModuleUnload",
        "hbfsim_begin_module_load_from_ptx", "hbfsim_end_module_load",
        "cuCtxDestroy", "cuCtxDestroy_v2", "cuCtxDetach",
        "cuDevicePrimaryCtxReset", "cuDevicePrimaryCtxReset_v2",
        "cuDevicePrimaryCtxRelease", "cuDevicePrimaryCtxRelease_v2",
        "cudaDeviceReset",
    }
    if toolkit_version < (13, 0):
        required.update({"cudaLaunchCooperativeKernelMultiDevice",
                         "cudaThreadExit"})
    if toolkit_version >= (12, 4):
        required.add("cuGreenCtxDestroy")
    missing = required - exported
    if missing:
        raise RuntimeError(f"missing launch interceptors: {sorted(missing)}")
    require("hbfsim_coverage_add_range" not in exported,
            "launch gate exports a range-registration bypass")

    dynamic = subprocess.run(
        [sys.argv[6], "-d", str(library)],
        check=True, text=True, capture_output=True
    ).stdout
    require("libcudart.so" not in dynamic,
            "launch gate has a direct libcudart dependency; runtime forwarding "
            "could cross CUDA runtime providers")

    source = pathlib.Path(sys.argv[3]).read_text()
    require("hbfsim_coverage_add_range" not in source,
            "launch gate source retains a range-registration bypass")
    context_source = pathlib.Path(sys.argv[4]).read_text()
    require("launch_gate_api_v3->quarantine_retire !=" in context_source and
            "launch_gate_api_v2->quarantine_retire !=" in context_source,
            "context accepts a launch-gate API without quarantine_retire")
    require("register_range_with_policy != nullptr" in context_source,
            "context accepts v3 without policy-aware range registration")
    require("hbfsim_expect_module_identity" not in source and
            "discard_expectations" not in source and
            "expected_" not in source,
            "launch gate retains process-global module expectations")
    require("interposed_wrapper_address" in source,
            "driver-entry APIs do not use an explicit local wrapper map")
    lookup_map = function_body(source, "interposed_wrapper_address(")
    require('{"cudaLaunch' not in lookup_map and
            '{"__cudaLaunch' not in lookup_map,
            "driver-entry lookup map contains invalid runtime API names")
    require("RTLD_DEFAULT" not in source,
            "lookup substitution must not rediscover wrappers via RTLD_DEFAULT")
    driver_resolver = function_body(source, "driver_symbol(")
    raw_resolver = function_body(source, "raw_dlsym(")
    runtime_resolver = function_body(
        source, "void* runtime_symbol(const char* name)\n{")
    driver_synchronize = function_body(
        source, "CUresult synchronize_driver_context()\n{")
    require("raw_dlsym(driver, name)" in driver_resolver and
            "RTLD_NOW | RTLD_LOCAL" in driver_resolver and
            'environment_or("HBFSIM_CUDA_DRIVER", "libcuda.so.1")' in
            driver_resolver and
            'dlvsym(RTLD_NEXT, "dlsym", "GLIBC_2.2.5")' in raw_resolver,
            "gated driver forwarding does not use an explicit raw local handle")
    require("raw_dlsym(RTLD_NEXT, name)" in runtime_resolver,
            "runtime forwarding is not relative to the preload link-map entry")
    require('driver_symbol("cuCtxSynchronize")' in driver_synchronize and
            "CUDA_ERROR_NOT_INITIALIZED" in driver_synchronize and
            "::cudaDeviceSynchronize" not in source,
            "range mutation does not synchronize the active driver context")
    require(re.search(r"(?<![A-Za-z0-9_])dlsym\(RTLD_NEXT", source) is None,
            "runtime forwarding bypasses the provider-neutral resolver")
    driver_lifecycle = {
        "cuModuleLoadDataEx", "cuModuleUnload", "cuCtxDestroy",
        "cuCtxDestroy_v2", "cuCtxDetach", "cuDevicePrimaryCtxReset",
        "cuDevicePrimaryCtxReset_v2", "cuDevicePrimaryCtxRelease",
        "cuDevicePrimaryCtxRelease_v2",
    }
    if toolkit_version >= (12, 4):
        driver_lifecycle.add("cuGreenCtxDestroy")
    for symbol in driver_lifecycle:
        require(f'"{symbol}"' in lookup_map,
                f"driver-entry lookup can bypass lifecycle hook: {symbol}")
    for symbol in required:
        if "Launch" in symbol:
            require(f'"{symbol}"' in source or f'({symbol})' in source,
                    f"launch wrapper is absent from substitution logic: {symbol}")
    require('"__hbfsim_module_identity"' in source and
            'driver_symbol("cuModuleGetGlobal_v2")' in source and
            'driver_symbol("cuMemcpyDtoH_v2")' in source,
            "module load does not verify the live transformed identity")
    handle_id = function_body(source, "handle_id(")
    require("module_identities().lookup" in handle_id and
            "live_module_identity" not in handle_id and
            "__hbfsim_module_identity" not in handle_id,
            "launch authorization still trusts an embedded identity directly")
    module_load = function_body(source, "cuModuleLoadDataEx(")
    require("module_load_transactions().take" in module_load and
            module_load.find("module_load_transactions().take") <
            module_load.find('driver_symbol("cuModuleLoadDataEx")') and
            "live_module_identity" in module_load and
            "module_identities().associate" in module_load,
            "module load does not bind successful exact pass provenance")
    require("lifecycle_transition_mutex" in module_load,
            "module load is not serialized with lifecycle transitions")
    module_unload = function_body(source, "cuModuleUnload(")
    require("result == CUDA_SUCCESS" in module_unload and
            "module_identities().erase" in module_unload,
            "module unload does not erase association only after success")
    require("lifecycle_transition_mutex" in module_unload,
            "module unload is not serialized with lifecycle transitions")
    for symbol in required & {
        "cuCtxDestroy", "cuCtxDestroy_v2", "cuCtxDetach",
        "cuGreenCtxDestroy",
        "cudaDeviceReset", "cudaThreadExit",
    }:
        lifecycle_body = function_body(source, f"{symbol}(")
        require("lifecycle_transition_mutex" in lifecycle_body,
                f"{symbol} is not serialized with owner activation")
        require("erase_context_state" in lifecycle_body and
                ("result == CUDA_SUCCESS" in lifecycle_body or
                 "result == cudaSuccess" in lifecycle_body),
                f"{symbol} does not invalidate exact-domain state only after success")
    for symbol in required & {
        "cuDevicePrimaryCtxReset", "cuDevicePrimaryCtxReset_v2",
        "cuDevicePrimaryCtxRelease", "cuDevicePrimaryCtxRelease_v2",
    }:
        lifecycle_body = function_body(source, f"{symbol}(")
        require("lifecycle_transition_mutex" in lifecycle_body,
                f"{symbol} is not serialized with owner activation")
        require("module_identities().clear" not in lifecycle_body and
                "timing_bindings().clear" not in lifecycle_body,
                f"{symbol} globally clears ambiguous primary-context state")
    activation = function_body(source, "activate_timing_owner(")
    activation_lock = function_body(source, "activation_transition_lock(")
    require("activation_transition_lock" in activation and
            "lifecycle_transition_mutex" in activation_lock,
            "owner activation is not serialized with lifecycle transitions")
    require("current_cuda_domain" in activation and
            "live_domain->context != cuda_context" in activation and
            "live_domain->device != device_ordinal" in activation,
            "owner activation does not revalidate its exact live CUDA domain")
    require("activation_guard" in activation and
            "finish_activation" in activation and
            activation.find("activation_guard") <
            activation.find("timing_bindings().activate") <
            activation.find("finish_activation"),
            "owner activation does not establish a fresh range epoch")
    runtime_multi = function_body(
        source, "cudaLaunchCooperativeKernelMultiDevice(")
    require("RuntimeLaunchScope scope;" in runtime_multi,
            "runtime multi-device launch lacks RuntimeLaunchScope")
    driver_multi = function_body(
        source, "cuLaunchCooperativeKernelMultiDevice(")
    require("runtime_launch_in_progress" in driver_multi,
            "driver multi-device launch does not bypass a gated runtime launch")
    require(driver_multi.find("runtime_launch_in_progress") <
            driver_multi.find("launch_guard()"),
            "driver multi-device runtime bypass must precede range locking")
    driver_ex_forward = function_body(source, "forward_driver_ex(")
    require("driver_symbol(symbol)" in driver_ex_forward,
            "public driver Ex forwarding does not use the frozen local driver")
    require('extern "C" void* dlsym(' not in source and
            "remember_private_launch_ex_original" not in source and
            "exact_private_launch_ex_original" not in source,
            "launch gate must not replace handle-specific dlsym results; "
            "capacity callers enforce pointer provenance before opaque launches")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
