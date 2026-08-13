#!/usr/bin/env python3

import ctypes
import os
import pathlib
import subprocess
import sys


CONTEXT = 0xCA00
DEVICE = 3


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


class AlignedTensorMap:
    def __init__(self) -> None:
        self.storage = ctypes.create_string_buffer(256)
        raw = ctypes.addressof(self.storage)
        self.address = (raw + 63) & ~63
        self.pointer = ctypes.c_void_p(self.address)

    def bytes(self) -> bytes:
        return ctypes.string_at(self.address, 128)

    def fill(self, value: int) -> None:
        ctypes.memset(self.address, value, 128)


def configure(process: ctypes.CDLL) -> None:
    process.cuTensorMapEncodeTiled.argtypes = [
        ctypes.c_void_p, ctypes.c_int, ctypes.c_uint32, ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_uint64), ctypes.POINTER(ctypes.c_uint64),
        ctypes.POINTER(ctypes.c_uint32), ctypes.POINTER(ctypes.c_uint32),
        ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
    ]
    process.cuTensorMapEncodeTiled.restype = ctypes.c_int
    process.cuTensorMapEncodeIm2col.argtypes = [
        ctypes.c_void_p, ctypes.c_int, ctypes.c_uint32, ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_uint64), ctypes.POINTER(ctypes.c_uint64),
        ctypes.POINTER(ctypes.c_int), ctypes.POINTER(ctypes.c_int),
        ctypes.c_uint32, ctypes.c_uint32,
        ctypes.POINTER(ctypes.c_uint32), ctypes.c_int, ctypes.c_int,
        ctypes.c_int, ctypes.c_int,
    ]
    process.cuTensorMapEncodeIm2col.restype = ctypes.c_int
    process.cuTensorMapEncodeIm2colWide.argtypes = [
        ctypes.c_void_p, ctypes.c_int, ctypes.c_uint32, ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_uint64), ctypes.POINTER(ctypes.c_uint64),
        ctypes.c_int, ctypes.c_int, ctypes.c_uint32, ctypes.c_uint32,
        ctypes.POINTER(ctypes.c_uint32), ctypes.c_int, ctypes.c_int,
        ctypes.c_int, ctypes.c_int, ctypes.c_int,
    ]
    process.cuTensorMapEncodeIm2colWide.restype = ctypes.c_int
    process.cuTensorMapReplaceAddress.argtypes = [ctypes.c_void_p,
                                                  ctypes.c_void_p]
    process.cuTensorMapReplaceAddress.restype = ctypes.c_int
    process.hbfsim_tensormap_lookup_for_test.argtypes = [
        ctypes.c_size_t, ctypes.c_int, ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_uint64), ctypes.POINTER(ctypes.c_size_t),
        ctypes.POINTER(ctypes.c_uint32),
    ]
    process.hbfsim_tensormap_lookup_for_test.restype = ctypes.c_int


def lookup(process: ctypes.CDLL, tensor_map: AlignedTensorMap,
           expected_generation: int, expected_address: int,
           expected_mode: int) -> None:
    generation = ctypes.c_uint64()
    address = ctypes.c_size_t()
    mode = ctypes.c_uint32()
    result = process.hbfsim_tensormap_lookup_for_test(
        CONTEXT, DEVICE, tensor_map.pointer, ctypes.byref(generation),
        ctypes.byref(address), ctypes.byref(mode))
    require(result == 0, "TensorMap provenance was not published")
    require((generation.value, address.value, mode.value) ==
            (expected_generation, expected_address, expected_mode),
            "TensorMap provenance fields differ")


def main() -> int:
    gate = str(pathlib.Path(sys.argv[1]).resolve())
    fake = str(pathlib.Path(sys.argv[2]).resolve())
    if os.environ.get("HBFSIM_TENSORMAP_ACTIVE") != "1":
        environment = os.environ.copy()
        environment["HBFSIM_TENSORMAP_ACTIVE"] = "1"
        environment["LD_PRELOAD"] = ":".join(
            item for item in (gate, fake, environment.get("LD_PRELOAD", ""))
            if item)
        return subprocess.run([sys.executable, __file__, gate, fake],
                              env=environment, check=False).returncode

    process = ctypes.CDLL(None)
    configure(process)
    fake_library = ctypes.CDLL(fake)
    fake_library.fakeCudaSetTensorMapFailure.argtypes = [ctypes.c_int]

    dimensions = (ctypes.c_uint64 * 5)(64, 32, 8, 4, 2)
    strides = (ctypes.c_uint64 * 4)(64, 2048, 16384, 65536)
    box = (ctypes.c_uint32 * 5)(16, 8, 1, 1, 1)
    elements = (ctypes.c_uint32 * 5)(1, 1, 1, 1, 1)
    lower = (ctypes.c_int * 3)(-1, -2, -3)
    upper = (ctypes.c_int * 3)(1, 2, 3)

    tiled = AlignedTensorMap()
    result = process.cuTensorMapEncodeTiled(
        tiled.pointer, 7, 5, ctypes.c_void_p(0x100000), dimensions,
        strides, box, elements, 0, 1, 2, 0)
    require(result == 0, "fake tiled encode failed")
    lookup(process, tiled, 1, 0x100000, 0)

    require(process.cuTensorMapReplaceAddress(
        tiled.pointer, ctypes.c_void_p(0x200000)) == 0,
        "fake replace address failed")
    lookup(process, tiled, 2, 0x200000, 0)

    im2col = AlignedTensorMap()
    require(process.cuTensorMapEncodeIm2col(
        im2col.pointer, 7, 5, ctypes.c_void_p(0x300000), dimensions,
        strides, lower, upper, 8, 4, elements, 0, 1, 2, 0) == 0,
        "fake im2col encode failed")
    lookup(process, im2col, 3, 0x300000, 1)

    wide = AlignedTensorMap()
    require(process.cuTensorMapEncodeIm2colWide(
        wide.pointer, 7, 5, ctypes.c_void_p(0x400000), dimensions,
        strides, -2, 2, 8, 4, elements, 0, 1, 1, 2, 0) == 0,
        "fake wide encode failed")
    lookup(process, wide, 4, 0x400000, 2)

    failed = AlignedTensorMap()
    failed.fill(0x5A)
    before = failed.bytes()
    fake_library.fakeCudaSetTensorMapFailure(1)
    require(process.cuTensorMapEncodeTiled(
        failed.pointer, 7, 5, ctypes.c_void_p(0x500000), dimensions,
        strides, box, elements, 0, 1, 2, 0) != 0,
        "failed encode unexpectedly succeeded")
    require(failed.bytes() == before,
            "failed CUDA encode modified the descriptor")
    require(process.hbfsim_tensormap_lookup_for_test(
        CONTEXT, DEVICE, failed.pointer, None, None, None) != 0,
        "failed CUDA encode published provenance")
    fake_library.fakeCudaSetTensorMapFailure(0)

    process.cuModuleLoadDataEx.argtypes = [
        ctypes.POINTER(ctypes.c_void_p), ctypes.c_void_p, ctypes.c_uint,
        ctypes.c_void_p, ctypes.c_void_p]
    process.cuModuleLoadDataEx.restype = ctypes.c_int
    module = ctypes.c_void_p()
    image = ctypes.create_string_buffer(b"plain")
    require(process.cuModuleLoadDataEx(ctypes.byref(module), image, 0,
                                       None, None) == 0,
            "fake module load failed")
    require(process.cuModuleUnload(module) == 0, "fake module unload failed")
    require(process.hbfsim_tensormap_lookup_for_test(
        CONTEXT, DEVICE, wide.pointer, None, None, None) != 0,
        "module unload retained unprovable TensorMap provenance")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
