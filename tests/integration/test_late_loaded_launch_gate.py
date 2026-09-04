#!/usr/bin/env python3
"""Prove an explicitly late-loaded gate can retire a capacity range."""

from __future__ import annotations

import ctypes
import pathlib
import sys


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


Publish = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_void_p)
Activate = ctypes.CFUNCTYPE(
    ctypes.c_int,
    ctypes.c_size_t,
    ctypes.c_size_t,
    ctypes.c_size_t,
    ctypes.c_int,
    ctypes.POINTER(ctypes.c_uint64),
)
Register = ctypes.CFUNCTYPE(
    ctypes.c_int,
    ctypes.c_size_t,
    ctypes.c_uint64,
    ctypes.c_size_t,
    ctypes.c_size_t,
    Publish,
    ctypes.c_void_p,
)
RegisterWithPolicy = ctypes.CFUNCTYPE(
    ctypes.c_int,
    ctypes.c_size_t,
    ctypes.c_uint64,
    ctypes.c_size_t,
    ctypes.c_size_t,
    ctypes.c_uint32,
    Publish,
    ctypes.c_void_p,
)
BeginRetire = ctypes.CFUNCTYPE(
    ctypes.c_int,
    ctypes.c_size_t,
    ctypes.c_uint64,
    ctypes.POINTER(ctypes.c_size_t),
)
FinishRetire = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_size_t)


class GateApiV3(ctypes.Structure):
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
        ("register_range_with_policy", RegisterWithPolicy),
    ]


def main() -> int:
    gate_path = pathlib.Path(sys.argv[1]).resolve(strict=True)
    fake_path = pathlib.Path(sys.argv[2]).resolve(strict=True)

    # Load the framework/driver provider first, then the gate. This is the
    # ordering used by the E6 Python adapter and intentionally gives the gate
    # no libcudart object after it for an RTLD_NEXT lookup.
    fake = ctypes.CDLL(str(fake_path), mode=ctypes.RTLD_GLOBAL)
    gate = ctypes.CDLL(str(gate_path), mode=ctypes.RTLD_GLOBAL)
    fake.fakeCudaSetCurrentDomain.argtypes = (ctypes.c_size_t, ctypes.c_int)
    fake.fakeCudaResetLifecycleCounts.argtypes = ()
    fake.fakeCudaSynchronizeCount.argtypes = ()
    fake.fakeCudaSynchronizeCount.restype = ctypes.c_int
    fake.fakeCudaSetCurrentDomain(0xCA00, 3)
    fake.fakeCudaResetLifecycleCounts()

    getter = gate.hbfsim_launch_gate_get_api
    getter.argtypes = (ctypes.c_uint32,)
    getter.restype = ctypes.POINTER(GateApiV3)
    pointer = getter(3)
    require(bool(pointer), "late-loaded gate v3 API is unavailable")
    api = pointer.contents
    require(
        api.abi_version == 3 and api.struct_bytes == ctypes.sizeof(GateApiV3),
        "late-loaded gate v3 ABI is malformed",
    )

    owner = 0xA000
    generation = ctypes.c_uint64()
    require(
        api.activate(owner, 0xFEED0000, 0xCA00, 3, ctypes.byref(generation))
        == 0
        and generation.value != 0,
        "late-loaded gate activation failed",
    )
    publish = Publish(lambda state: 0)
    begin = 0x100000
    end = begin + 0x2000
    require(
        api.register_range_with_policy(
            owner, generation.value, begin, end, 2, publish, None
        )
        == 0,
        "late-loaded gate capacity registration failed",
    )
    require(
        api.unregister_range(
            owner, generation.value, begin, end, publish, None
        )
        == 0,
        "late-loaded gate capacity unregistration failed",
    )
    require(
        fake.fakeCudaSynchronizeCount() == 1,
        "capacity unregistration did not synchronize the active driver context",
    )
    token = ctypes.c_size_t()
    require(
        api.begin_retire(owner, generation.value, ctypes.byref(token)) == 0
        and token.value != 0
        and api.invalidate_retire(token.value) == 0
        and api.finish_retire(token.value) == 0,
        "late-loaded gate owner retirement failed",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
