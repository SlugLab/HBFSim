#!/usr/bin/env python3

import pathlib
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
        "cuTensorMapEncodeTiled", "cuTensorMapEncodeIm2col",
        "cuTensorMapEncodeIm2colWide", "cuTensorMapReplaceAddress",
        "hbfsim_begin_module_load_from_ptx",
        "hbfsim_begin_module_load_from_aot", "hbfsim_end_module_load",
        "hbfsim_collect_exact_environment_v1",
        "hbfsim_launch_gate_finalize_exact_v1",
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

    source = pathlib.Path(sys.argv[3]).read_text()
    require("hbfsim_coverage_add_range" not in source,
            "launch gate source retains a range-registration bypass")
    context_source = pathlib.Path(sys.argv[4]).read_text()
    require("LaunchGateApiV4" in context_source and
            "exact_requested ? !valid_v4" in context_source and
            "exact_profile_json == nullptr" in context_source,
            "exact context creation can downgrade below launch-gate v4")
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
    driver_lifecycle = {
        "cuModuleLoadDataEx", "cuModuleUnload", "cuCtxDestroy",
        "cuCtxDestroy_v2", "cuCtxDetach", "cuDevicePrimaryCtxReset",
        "cuDevicePrimaryCtxReset_v2", "cuDevicePrimaryCtxRelease",
        "cuDevicePrimaryCtxRelease_v2",
    }
    tensormap_symbols = {
        "cuTensorMapEncodeTiled", "cuTensorMapEncodeIm2col",
        "cuTensorMapEncodeIm2colWide", "cuTensorMapReplaceAddress",
    }
    if toolkit_version >= (12, 4):
        driver_lifecycle.add("cuGreenCtxDestroy")
    for symbol in driver_lifecycle:
        require(f'"{symbol}"' in lookup_map,
                f"driver-entry lookup can bypass lifecycle hook: {symbol}")
    for symbol in tensormap_symbols:
        require(f'"{symbol}"' in lookup_map,
                f"driver-entry lookup can bypass TensorMap hook: {symbol}")
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
            "aot_load_transactions().take_for_image(image)" in module_load and
            module_load.find("module_load_transactions().take") <
            module_load.find("dlsym(RTLD_NEXT") and
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
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
