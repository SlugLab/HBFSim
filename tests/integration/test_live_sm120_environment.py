#!/usr/bin/env python3

import ctypes
import pathlib
import sys


CUDA_SUCCESS = 0
CUDA_ERROR_NO_DEVICE = 100
CC_MAJOR = 75
CC_MINOR = 76
SKIP = 77


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


class Snapshot(ctypes.Structure):
    _fields_ = [
        ("abi_version", ctypes.c_uint32),
        ("struct_bytes", ctypes.c_uint32),
        ("error_code", ctypes.c_uint32),
        ("native_status", ctypes.c_int32),
        ("captured_unix_ns", ctypes.c_uint64),
        ("pci_vendor_id", ctypes.c_uint32),
        ("pci_device_id", ctypes.c_uint32),
        ("compute_capability_major", ctypes.c_uint32),
        ("compute_capability_minor", ctypes.c_uint32),
        ("cuda_driver_version", ctypes.c_uint32),
        ("sm_clock_mhz", ctypes.c_uint32),
        ("memory_clock_mhz", ctypes.c_uint32),
        ("power_limit_mw", ctypes.c_uint32),
        ("temperature_c", ctypes.c_uint32),
        ("current_process_is_exclusive", ctypes.c_uint32),
        ("gpu_name", ctypes.c_char * 128),
        ("gpu_uuid", ctypes.c_char * 96),
        ("pci_bus_id", ctypes.c_char * 32),
        ("operation", ctypes.c_char * 64),
    ]


def checked(status: int, operation: str) -> None:
    require(status == CUDA_SUCCESS, f"{operation} failed: {status}")


def main() -> int:
    gate_path = pathlib.Path(sys.argv[1]).resolve()
    require(gate_path.is_file(), f"launch gate is missing: {gate_path}")

    cuda = ctypes.CDLL("libcuda.so.1", mode=ctypes.RTLD_LOCAL)
    cuda.cuInit.argtypes = [ctypes.c_uint]
    cuda.cuInit.restype = ctypes.c_int
    cuda.cuDeviceGetCount.argtypes = [ctypes.POINTER(ctypes.c_int)]
    cuda.cuDeviceGetCount.restype = ctypes.c_int
    cuda.cuDeviceGet.argtypes = [ctypes.POINTER(ctypes.c_int), ctypes.c_int]
    cuda.cuDeviceGet.restype = ctypes.c_int
    cuda.cuDeviceGetAttribute.argtypes = [ctypes.POINTER(ctypes.c_int),
                                          ctypes.c_int, ctypes.c_int]
    cuda.cuDeviceGetAttribute.restype = ctypes.c_int
    init_status = cuda.cuInit(0)
    if init_status == CUDA_ERROR_NO_DEVICE:
        print("SKIP: no CUDA compute-capability 12.0 device")
        return SKIP
    checked(init_status, "cuInit")
    count = ctypes.c_int()
    checked(cuda.cuDeviceGetCount(ctypes.byref(count)), "cuDeviceGetCount")
    selected = None
    for ordinal in range(count.value):
        device = ctypes.c_int()
        checked(cuda.cuDeviceGet(ctypes.byref(device), ordinal), "cuDeviceGet")
        major = ctypes.c_int()
        minor = ctypes.c_int()
        checked(cuda.cuDeviceGetAttribute(ctypes.byref(major), CC_MAJOR,
                                          device.value), "CC major")
        checked(cuda.cuDeviceGetAttribute(ctypes.byref(minor), CC_MINOR,
                                          device.value), "CC minor")
        if (major.value, minor.value) == (12, 0):
            selected = device.value
            break
    if selected is None:
        print("SKIP: no CUDA compute-capability 12.0 device")
        return SKIP

    cuda.cuCtxCreate_v4.argtypes = [ctypes.POINTER(ctypes.c_void_p),
                                    ctypes.c_void_p, ctypes.c_uint,
                                    ctypes.c_int]
    cuda.cuCtxCreate_v4.restype = ctypes.c_int
    cuda.cuCtxDestroy_v2.argtypes = [ctypes.c_void_p]
    cuda.cuCtxDestroy_v2.restype = ctypes.c_int
    context = ctypes.c_void_p()
    checked(cuda.cuCtxCreate_v4(ctypes.byref(context), None, 0, selected),
            "cuCtxCreate_v4")
    try:
        gate = ctypes.CDLL(str(gate_path), mode=ctypes.RTLD_LOCAL)
        collect = gate.hbfsim_collect_exact_environment_v1
        collect.argtypes = [ctypes.POINTER(Snapshot), ctypes.c_size_t]
        collect.restype = ctypes.c_int
        snapshot = Snapshot()
        require(collect(ctypes.byref(snapshot), ctypes.sizeof(snapshot) - 1)
                == -1, "collector accepted a mismatched ABI size")
        snapshot = Snapshot()
        status = collect(ctypes.byref(snapshot), ctypes.sizeof(snapshot))
        operation = snapshot.operation.decode(errors="replace")
        require(status == 0,
                f"live collector rejected target: error={snapshot.error_code} "
                f"native={snapshot.native_status} operation={operation}")
        require(snapshot.abi_version == 1, "wrong snapshot ABI version")
        require(snapshot.struct_bytes == ctypes.sizeof(snapshot),
                "wrong snapshot byte size")
        name = snapshot.gpu_name.decode()
        require("RTX PRO 6000" in name, f"unexpected SM120 product: {name}")
        require((snapshot.compute_capability_major,
                 snapshot.compute_capability_minor) == (12, 0),
                "live snapshot has the wrong compute capability")
        require(snapshot.pci_vendor_id != 0 and snapshot.pci_device_id != 0,
                "live snapshot has zero PCI identity")
        require(snapshot.sm_clock_mhz != 0 and
                snapshot.memory_clock_mhz != 0,
                "live snapshot has zero clocks")
        require(snapshot.power_limit_mw != 0,
                "live snapshot has zero enforced power limit")
        require(snapshot.temperature_c != 0,
                "live snapshot has zero temperature")
        require(snapshot.captured_unix_ns != 0,
                "live snapshot has no timestamp")
        require(snapshot.gpu_uuid.decode().startswith("GPU-"),
                "live snapshot has malformed UUID")
        require(bool(snapshot.pci_bus_id.decode()),
                "live snapshot has no PCI bus ID")
        print(
            f"{name}; cc=12.0; pci={snapshot.pci_bus_id.decode()}; "
            f"sm={snapshot.sm_clock_mhz}MHz; "
            f"mem={snapshot.memory_clock_mhz}MHz; "
            f"exclusive={snapshot.current_process_is_exclusive}"
        )
    finally:
        checked(cuda.cuCtxDestroy_v2(context), "cuCtxDestroy_v2")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
