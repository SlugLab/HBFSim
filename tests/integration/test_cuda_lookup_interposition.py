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


def main() -> int:
    gate = str(pathlib.Path(sys.argv[1]).resolve())
    fake = str(pathlib.Path(sys.argv[2]).resolve())
    if os.environ.get("HBFSIM_FAKE_LOOKUP_ACTIVE") != "1":
        environment = os.environ.copy()
        environment["HBFSIM_FAKE_LOOKUP_ACTIVE"] = "1"
        environment["LD_PRELOAD"] = ":".join(
            item for item in (gate, fake, environment.get("LD_PRELOAD", ""))
            if item
        )
        return subprocess.run([sys.executable, __file__, gate, fake],
                              env=environment, check=False).returncode

    process = ctypes.CDLL(None)
    gate_library = ctypes.CDLL(gate)
    expected_legacy = address(gate_library, "cudaLaunchKernel")
    expected_ptsz = address(gate_library, "cudaLaunchKernel_ptsz")
    output = ctypes.c_void_p()
    status = ctypes.c_int()

    driver_legacy = process.cuGetProcAddress
    driver_legacy.argtypes = [ctypes.c_char_p, ctypes.POINTER(ctypes.c_void_p),
                              ctypes.c_int, ctypes.c_uint64]
    driver_legacy.restype = ctypes.c_int
    require(driver_legacy(b"cudaLaunchKernel", ctypes.byref(output), 12080, 0)
            == 0 and output.value == expected_legacy,
            "legacy driver lookup did not return the gated wrapper")
    output.value = None
    require(driver_legacy(b"fail", ctypes.byref(output), 12080, 0) != 0 and
            output.value == 0x1234,
            "failed driver lookup was substituted")

    driver_v2 = process.cuGetProcAddress_v2
    driver_v2.argtypes = driver_legacy.argtypes + [ctypes.POINTER(ctypes.c_int)]
    driver_v2.restype = ctypes.c_int
    output.value = None
    require(driver_v2(b"cudaLaunchKernel", ctypes.byref(output), 12080, 2,
                      ctypes.byref(status)) == 0 and
            output.value == expected_ptsz,
            "v2 driver PTDS lookup did not return the gated PTDS wrapper")

    runtime_names = (
        ("cudaGetDriverEntryPoint", False, False),
        ("cudaGetDriverEntryPoint_ptsz", False, True),
        ("cudaGetDriverEntryPointByVersion", True, False),
        ("cudaGetDriverEntryPointByVersion_ptsz", True, True),
    )
    for lookup_name, versioned, ptsz in runtime_names:
        lookup = getattr(process, lookup_name)
        arguments = [ctypes.c_char_p, ctypes.POINTER(ctypes.c_void_p)]
        if versioned:
            arguments.append(ctypes.c_uint)
        arguments += [ctypes.c_uint64, ctypes.POINTER(ctypes.c_int)]
        lookup.argtypes = arguments
        lookup.restype = ctypes.c_int
        call = [b"cudaLaunchKernel", ctypes.byref(output)]
        if versioned:
            call.append(12080)
        call += [0, ctypes.byref(status)]
        output.value = None
        require(lookup(*call) == 0 and
                output.value == (expected_ptsz if ptsz else expected_legacy),
                f"{lookup_name} returned the wrong gated wrapper")
        failure_call = [b"fail", ctypes.byref(output)]
        if versioned:
            failure_call.append(12080)
        failure_call += [0, ctypes.byref(status)]
        output.value = None
        require(lookup(*failure_call) != 0 and output.value == 0x1234,
                f"{lookup_name} substituted a failed original lookup")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
