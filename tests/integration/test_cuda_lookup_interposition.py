#!/usr/bin/env python3

import ctypes
import os
import pathlib
import subprocess
import sys


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def address(library: ctypes.CDLL, symbol: str) -> int:
    return ctypes.cast(getattr(library, symbol), ctypes.c_void_p).value


def lookup(process: ctypes.CDLL, name: str, symbol: str, flags: int) -> tuple[int, int]:
    function = getattr(process, name)
    output = ctypes.c_void_p()
    status = ctypes.c_int()
    arguments = [ctypes.c_char_p, ctypes.POINTER(ctypes.c_void_p)]
    call = [symbol.encode(), ctypes.byref(output)]
    if name.startswith("cuGetProcAddress"):
        arguments += [ctypes.c_int, ctypes.c_uint64]
        call += [12080, flags]
        if name.endswith("_v2"):
            arguments.append(ctypes.POINTER(ctypes.c_int))
            call.append(ctypes.byref(status))
    else:
        if "ByVersion" in name:
            arguments.append(ctypes.c_uint)
            call.append(12080)
        arguments += [ctypes.c_uint64, ctypes.POINTER(ctypes.c_int)]
        call += [flags, ctypes.byref(status)]
    function.argtypes = arguments
    function.restype = ctypes.c_int
    result = function(*call)
    return result, output.value


def main() -> int:
    gate = str(pathlib.Path(sys.argv[1]).resolve())
    fake = str(pathlib.Path(sys.argv[2]).resolve())
    toolkit_version = tuple(int(part) for part in sys.argv[3].split(".")[:2])
    if os.environ.get("HBFSIM_FAKE_LOOKUP_ACTIVE") != "1":
        environment = os.environ.copy()
        environment["HBFSIM_FAKE_LOOKUP_ACTIVE"] = "1"
        environment["LD_PRELOAD"] = ":".join(
            item for item in (gate, fake, environment.get("LD_PRELOAD", ""))
            if item
        )
        return subprocess.run([sys.executable, __file__, gate, fake,
                               sys.argv[3]],
                              env=environment, check=False).returncode

    process = ctypes.CDLL(None)
    gate_library = ctypes.CDLL(gate)
    fake_library = ctypes.CDLL(fake, mode=os.RTLD_LOCAL)
    process.dlsym.argtypes = [ctypes.c_void_p, ctypes.c_char_p]
    process.dlsym.restype = ctypes.c_void_p
    for symbol in ("cuLaunchKernelEx", "cuLaunchKernelEx_ptsz"):
        output = process.dlsym(fake_library._handle, symbol.encode())
        require(output == address(gate_library, symbol),
                f"handle-specific dlsym bypassed gated wrapper {symbol}")
    output = process.dlsym(fake_library._handle, b"cuFuncGetName")
    require(output == address(fake_library, "cuFuncGetName"),
            "handle-specific dlsym substituted a non-launch symbol")
    launch_symbols = (
        ("cuLaunch", "cuLaunch", "cuLaunch"),
        ("cuLaunchGrid", "cuLaunchGrid", "cuLaunchGrid"),
        ("cuLaunchGridAsync", "cuLaunchGridAsync", "cuLaunchGridAsync"),
        ("cuLaunchKernel", "cuLaunchKernel", "cuLaunchKernel_ptsz"),
        ("cuLaunchKernelEx", "cuLaunchKernelEx", "cuLaunchKernelEx_ptsz"),
        ("cuLaunchCooperativeKernel", "cuLaunchCooperativeKernel",
         "cuLaunchCooperativeKernel_ptsz"),
        ("cuLaunchCooperativeKernelMultiDevice",
         "cuLaunchCooperativeKernelMultiDevice",
         "cuLaunchCooperativeKernelMultiDevice"),
        ("cuGraphLaunch", "cuGraphLaunch", "cuGraphLaunch_ptsz"),
    )
    lifecycle_symbols = [
        "cuModuleLoadDataEx", "cuModuleUnload",
        "cuCtxDestroy", "cuCtxDestroy_v2", "cuCtxDetach",
        "cuDevicePrimaryCtxReset", "cuDevicePrimaryCtxReset_v2",
        "cuDevicePrimaryCtxRelease", "cuDevicePrimaryCtxRelease_v2",
    ]
    if toolkit_version >= (12, 4):
        lifecycle_symbols.append("cuGreenCtxDestroy")
    lookup_apis = (
        ("cuGetProcAddress", False),
        ("cuGetProcAddress_v2", False),
        ("cudaGetDriverEntryPoint", False),
        ("cudaGetDriverEntryPoint_ptsz", True),
        ("cudaGetDriverEntryPointByVersion", False),
        ("cudaGetDriverEntryPointByVersion_ptsz", True),
    )
    for lookup_name, implicit_ptsz in lookup_apis:
        for symbol, legacy_wrapper, ptsz_wrapper in launch_symbols:
            for flags in (0, 2):
                result, output = lookup(process, lookup_name, symbol, flags)
                expected_name = (ptsz_wrapper if implicit_ptsz or flags == 2
                                 else legacy_wrapper)
                require(result == 0 and
                        output == address(gate_library, expected_name),
                        f"{lookup_name}({symbol}, flags={flags}) returned "
                        f"the wrong gated wrapper")
        for symbol in lifecycle_symbols:
            for flags in (0, 2):
                result, output = lookup(process, lookup_name, symbol, flags)
                require(result == 0 and output == address(gate_library, symbol),
                        f"{lookup_name} bypassed lifecycle hook {symbol}")

        result, output = lookup(process, lookup_name, "cuMemcpyDtoH", 0)
        require(result == 0 and output == 0x1234,
                f"{lookup_name} substituted a non-gated driver function")
        result, output = lookup(process, lookup_name, "cudaLaunchKernel", 0)
        require(result != 0 and output == 0x1234,
                f"{lookup_name} accepted an invalid runtime API name: "
                f"result={result}, output={output}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
