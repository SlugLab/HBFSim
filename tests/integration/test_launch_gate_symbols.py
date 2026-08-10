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
        "hbfsim_expect_module_identity",
    }
    toolkit_major = int(sys.argv[4].split(".", maxsplit=1)[0])
    if toolkit_major < 13:
        required.add("cudaLaunchCooperativeKernelMultiDevice")
    missing = required - exported
    if missing:
        raise RuntimeError(f"missing launch interceptors: {sorted(missing)}")

    source = pathlib.Path(sys.argv[3]).read_text()
    require("interposed_wrapper_address" in source,
            "driver-entry APIs do not use an explicit local wrapper map")
    lookup_map = function_body(source, "interposed_wrapper_address(")
    require('{"cudaLaunch' not in lookup_map and
            '{"__cudaLaunch' not in lookup_map,
            "driver-entry lookup map contains invalid runtime API names")
    require("RTLD_DEFAULT" not in source,
            "lookup substitution must not rediscover wrappers via RTLD_DEFAULT")
    require('"cuModuleLoadDataEx"' in lookup_map and
            '"cuModuleUnload"' in lookup_map,
            "driver-entry lookup can bypass module lifecycle interposition")
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
    require("result != CUDA_SUCCESS" in module_load and
            "discard_expectations" in module_load and
            "live_module_identity" in module_load and
            "module_identities().associate" in module_load,
            "module load does not bind successful exact pass provenance")
    module_unload = function_body(source, "cuModuleUnload(")
    require("result == CUDA_SUCCESS" in module_unload and
            "module_identities().erase" in module_unload,
            "module unload does not erase association only after success")
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
