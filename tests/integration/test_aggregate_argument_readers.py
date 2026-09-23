#!/usr/bin/env python3
import ctypes
import pathlib
import sys


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def main() -> int:
    gate = ctypes.CDLL(str(pathlib.Path(sys.argv[1]).resolve()))
    read_args = gate.hbfsim_test_read_argument_pointer
    read_args.argtypes = [ctypes.POINTER(ctypes.c_void_p), ctypes.c_size_t,
                          ctypes.c_size_t, ctypes.POINTER(ctypes.c_size_t)]
    read_args.restype = ctypes.c_int
    read_packed = gate.hbfsim_test_read_packed_pointer
    read_packed.argtypes = [ctypes.POINTER(ctypes.c_void_p), ctypes.c_size_t,
                            ctypes.c_size_t, ctypes.POINTER(ctypes.c_size_t)]
    read_packed.restype = ctypes.c_int

    aggregate = (ctypes.c_ubyte * 24)()
    expected = 0x1234_5678_9ABC_DEF0
    ctypes.memmove(ctypes.addressof(aggregate) + 8,
                   ctypes.byref(ctypes.c_size_t(expected)), 8)
    arguments = (ctypes.c_void_p * 1)(ctypes.addressof(aggregate))
    output = ctypes.c_size_t()
    require(read_args(arguments, 0, 8, ctypes.byref(output)) == 0 and
            output.value == expected,
            "kernelParams aggregate field was not read at its proven offset")
    null_arguments = (ctypes.c_void_p * 1)(None)
    require(read_args(null_arguments, 0, 8, ctypes.byref(output)) != 0,
            "null kernelParams aggregate storage was accepted")

    packed = (ctypes.c_ubyte * 40)()
    packed_expected = 0x0FED_CBA9_8765_4321
    ctypes.memmove(ctypes.addressof(packed) + 16,
                   ctypes.byref(ctypes.c_size_t(packed_expected)), 8)
    packed_bytes = ctypes.c_size_t(len(packed))
    extra = (ctypes.c_void_p * 5)(
        1, ctypes.addressof(packed),
        2, ctypes.addressof(packed_bytes),
        0)
    output.value = 0
    require(read_packed(extra, 8, 8, ctypes.byref(output)) == 0 and
            output.value == packed_expected,
            "packed launch aggregate field was not read at parameter+field")

    short_bytes = ctypes.c_size_t(20)
    short_extra = (ctypes.c_void_p * 5)(
        1, ctypes.addressof(packed),
        2, ctypes.addressof(short_bytes),
        0)
    require(read_packed(short_extra, 8, 8, ctypes.byref(output)) != 0,
            "packed launch length truncation was accepted")
    require(read_packed(extra, ctypes.c_size_t(-1).value, 8,
                        ctypes.byref(output)) != 0,
            "packed launch parameter offset overflow was accepted")
    duplicate = (ctypes.c_void_p * 7)(
        1, ctypes.addressof(packed),
        1, ctypes.addressof(packed),
        2, ctypes.addressof(packed_bytes),
        0)
    require(read_packed(duplicate, 0, 0, ctypes.byref(output)) != 0,
            "duplicate packed buffer pointer key was accepted")
    unknown = (ctypes.c_void_p * 3)(3, ctypes.addressof(packed), 0)
    require(read_packed(unknown, 0, 0, ctypes.byref(output)) != 0,
            "unknown packed launch key was accepted")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
