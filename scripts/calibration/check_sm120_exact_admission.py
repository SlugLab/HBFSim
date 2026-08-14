#!/usr/bin/env python3

"""Read-only SM120 exact admission dry-run for one kernel and AOT bundle."""

from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import os
import pathlib
import re
import sys
from typing import NoReturn


MAX_JSON_BYTES = 16 * 1024 * 1024
MAX_CUBIN_BYTES = 1024 * 1024 * 1024
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
MODULE_ID_RE = re.compile(r"^ptx:sha256:([0-9a-f]{64})$")
TARGETS = frozenset(("sm_120", "sm_120a", "sm_120f"))
VALIDATION_CLASSES = (
    "ordinary_load", "ordinary_store", "tma_load", "tma_store",
    "unicast", "multicast", "mixed_hbm_hbf",
)


class AdmissionError(RuntimeError):
    def __init__(self, code: int, message: str):
        super().__init__(message)
        self.code = code


class Parser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        raise AdmissionError(64, message)


def fail(code: int, message: str) -> NoReturn:
    raise AdmissionError(code, message)


def sha256_file(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as source:
            while block := source.read(1024 * 1024):
                digest.update(block)
    except OSError as error:
        fail(66, f"unable to hash {path.name}: {error}")
    return digest.hexdigest()


def regular_file(value: str, label: str,
                 maximum: int = MAX_JSON_BYTES) -> pathlib.Path:
    path = pathlib.Path(value)
    try:
        if path.is_symlink():
            fail(66, f"{label} must not be a symlink")
        resolved = path.resolve(strict=True)
        if not resolved.is_file() or not os.access(resolved, os.R_OK):
            fail(66, f"{label} must be a readable regular file")
        if resolved.stat().st_size > maximum:
            fail(66, f"{label} exceeds the size limit")
        return resolved
    except OSError as error:
        fail(66, f"unable to resolve {label}: {error}")


def bundle_directory(value: str) -> pathlib.Path:
    path = pathlib.Path(value)
    try:
        if path.is_symlink():
            fail(66, "bundle must not be a symlink")
        resolved = path.resolve(strict=True)
        if not resolved.is_dir():
            fail(66, "bundle is not a directory")
        expected = {"original.ptx", "transformed.ptx", "module.cubin",
                    "module.sass", "artifact.json"}
        members = {item.name for item in resolved.iterdir()}
        if members != expected:
            fail(66, "bundle layout is incomplete or contains extra files")
        for name in expected:
            member = resolved / name
            if member.is_symlink() or not member.is_file():
                fail(66, f"unsafe bundle member: {name}")
        limits = {
            "original.ptx": MAX_JSON_BYTES * 4,
            "transformed.ptx": MAX_JSON_BYTES * 4,
            "module.cubin": MAX_CUBIN_BYTES,
            "module.sass": MAX_CUBIN_BYTES,
            "artifact.json": MAX_JSON_BYTES,
        }
        for name, maximum in limits.items():
            size = (resolved / name).stat().st_size
            if size == 0 or size > maximum:
                fail(66, f"bundle member has an unsafe size: {name}")
        return resolved
    except OSError as error:
        fail(66, f"unable to resolve bundle: {error}")


def json_file(path: pathlib.Path, label: str):
    try:
        return json.loads(path.read_text())
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        fail(64, f"invalid {label}: {error}")


def exact_keys(value: object, required: set[str], label: str) -> dict:
    if not isinstance(value, dict) or set(value) != required:
        fail(64, f"{label} object shape mismatch")
    return value


def string(value: object, label: str) -> str:
    if not isinstance(value, str) or not value:
        fail(64, f"invalid {label}")
    return value


def integer(value: object, label: str, *, positive: bool = False) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0 or (
            positive and value == 0):
        fail(64, f"invalid {label}")
    return value


def number(value: object, label: str) -> float:
    if (not isinstance(value, (int, float)) or isinstance(value, bool) or
            value < 0 or value != value or value in (float("inf"), float("-inf"))):
        fail(64, f"invalid {label}")
    return float(value)


def sha256(value: object, label: str) -> str:
    text = string(value, label)
    if SHA256_RE.fullmatch(text) is None:
        fail(64, f"invalid {label}")
    return text


def validate_kernel(value: object, label: str) -> dict:
    fields = {
        "name", "registers", "spill_store_bytes", "spill_load_bytes",
        "static_shared_bytes", "max_dynamic_shared_bytes", "block_threads",
        "occupancy_blocks_per_sm",
    }
    result = exact_keys(value, fields, label)
    string(result["name"], f"{label}.name")
    integer(result["registers"], f"{label}.registers", positive=True)
    for name in ("spill_store_bytes", "spill_load_bytes", "static_shared_bytes",
                 "max_dynamic_shared_bytes"):
        integer(result[name], f"{label}.{name}")
    block = integer(result["block_threads"], f"{label}.block_threads",
                    positive=True)
    if block > 1024:
        fail(64, f"invalid {label}.block_threads")
    integer(result["occupancy_blocks_per_sm"],
            f"{label}.occupancy_blocks_per_sm", positive=True)
    return result


def validate_module(value: object, label: str) -> dict:
    fields = {
        "module_id", "ptx_target", "original_ptx_sha256",
        "transformed_ptx_sha256", "cubin_sha256", "sass_sha256", "kernels",
    }
    result = exact_keys(value, fields, label)
    if MODULE_ID_RE.fullmatch(string(result["module_id"],
                                     f"{label}.module_id")) is None:
        fail(64, f"invalid {label}.module_id")
    if string(result["ptx_target"], f"{label}.ptx_target") not in TARGETS:
        fail(64, f"invalid {label}.ptx_target")
    for name in ("original_ptx_sha256", "transformed_ptx_sha256",
                 "cubin_sha256", "sass_sha256"):
        sha256(result[name], f"{label}.{name}")
    kernels = result["kernels"]
    if not isinstance(kernels, list) or not kernels:
        fail(64, f"invalid {label}.kernels")
    names: set[str] = set()
    for index, kernel in enumerate(kernels):
        record = validate_kernel(kernel, f"{label}.kernels[{index}]")
        if record["name"] in names:
            fail(64, f"duplicate kernel in {label}")
        names.add(record["name"])
    return result


def validate_dataset(value: object, label: str) -> dict:
    result = exact_keys(value, {"manifest_sha256", "case_ids"}, label)
    sha256(result["manifest_sha256"], f"{label}.manifest_sha256")
    cases = result["case_ids"]
    if (not isinstance(cases, list) or not cases or
            any(not isinstance(case, str) or not case for case in cases) or
            len(set(cases)) != len(cases)):
        fail(64, f"invalid {label}.case_ids")
    return result


def validate_calibration(value: object) -> dict:
    calibration = exact_keys(value, {
        "label_semantics", "gnic", "gpc", "routing", "metric_names",
        "raw_training_sha256", "raw_holdout_sha256", "fitted_case_ids",
        "residuals", "counter_thresholds",
    }, "calibration")
    if calibration["label_semantics"] != "contention_equivalent":
        fail(64, "calibration labels must be contention-equivalent")
    for name, count in (("gnic", 4), ("gpc", 2)):
        queue = exact_keys(calibration[name], {
            "count", "depth", "arbitration", "service_ns_by_class",
        }, f"calibration.{name}")
        if integer(queue["count"], f"calibration.{name}.count") != count:
            fail(64, f"calibration.{name} queue count mismatch")
        integer(queue["depth"], f"calibration.{name}.depth", positive=True)
        if queue["arbitration"] not in ("fifo", "round_robin"):
            fail(64, f"invalid calibration.{name}.arbitration")
        services = queue["service_ns_by_class"]
        if (not isinstance(services, list) or len(services) != 7 or
                any(not isinstance(item, int) or isinstance(item, bool) or
                    item <= 0 for item in services)):
            fail(64, f"invalid calibration.{name} services")
    routing = exact_keys(calibration["routing"], {
        "version", "program_sha256", "inputs", "smsp_proxy_lut",
        "gnic_lut", "gpc_lut",
    }, "calibration.routing")
    integer(routing["version"], "calibration.routing.version", positive=True)
    sha256(routing["program_sha256"], "calibration.routing.program_sha256")
    if routing["inputs"] != [
            "smid", "warpid", "cta_shape", "resident_warps",
            "cluster_ctarank", "operation"]:
        fail(64, "calibration routing inputs mismatch")
    for name, bound in (("smsp_proxy_lut", None), ("gnic_lut", 4),
                        ("gpc_lut", 2)):
        lut = routing[name]
        if (not isinstance(lut, list) or not lut or
                any(not isinstance(item, int) or isinstance(item, bool) or
                    item < 0 or (bound is not None and item >= bound)
                    for item in lut)):
            fail(64, f"invalid calibration routing {name}")
    for name in ("raw_training_sha256", "raw_holdout_sha256"):
        sha256(calibration[name], f"calibration.{name}")
    if calibration["raw_training_sha256"] == calibration["raw_holdout_sha256"]:
        fail(64, "calibration training and holdout hashes overlap")
    metrics = calibration["metric_names"]
    if (not isinstance(metrics, list) or not metrics or
            any(not isinstance(item, str) or not item for item in metrics) or
            len(metrics) != len(set(metrics))):
        fail(64, "invalid calibration metrics")
    thresholds = calibration["counter_thresholds"]
    if not isinstance(thresholds, list) or len(thresholds) != len(metrics):
        fail(64, "invalid calibration counter thresholds")
    threshold_metrics = set()
    for index, item in enumerate(thresholds):
        record = exact_keys(item, {"metric", "max_error_percent"},
                            f"calibration.counter_thresholds[{index}]")
        threshold_metrics.add(string(record["metric"], "counter metric"))
        if number(record["max_error_percent"], "counter threshold") > 10:
            fail(64, "calibration counter threshold exceeds limit")
    if threshold_metrics != set(metrics):
        fail(64, "calibration metric threshold set mismatch")
    return calibration


def validate_profile(value: object) -> dict:
    if not isinstance(value, dict):
        fail(64, "profile object shape mismatch")
    schema = integer(value.get("schema_version"), "schema_version",
                     positive=True)
    root_fields = {
        "schema_version", "profile_id", "target", "toolchain", "conditions",
        "thresholds", "limits", "modules", "validation", "calibration",
    }
    profile = exact_keys(value, root_fields, "profile")
    if schema != 2:
        fail(64, "unsupported exact profile schema")
    validate_calibration(profile["calibration"])
    string(profile["profile_id"], "profile_id")
    target = exact_keys(profile["target"], {
        "gpu_name", "gpu_uuid", "pci_vendor_id", "pci_device_id",
        "compute_capability_major", "compute_capability_minor", "driver_version",
    }, "target")
    string(target["gpu_name"], "target.gpu_name")
    string(target["gpu_uuid"], "target.gpu_uuid")
    for name in ("pci_vendor_id", "pci_device_id", "driver_version"):
        integer(target[name], f"target.{name}", positive=True)
    if (integer(target["compute_capability_major"],
                "target.compute_capability_major", positive=True) != 12 or
            integer(target["compute_capability_minor"],
                    "target.compute_capability_minor") != 0):
        fail(64, "exact profile target is not compute capability 12.0")
    toolchain = exact_keys(profile["toolchain"], {
        "cuda_version", "ptxas_version", "nvdisasm_version",
        "cuobjdump_version", "ncu_version",
    }, "toolchain")
    for name, value_text in toolchain.items():
        string(value_text, f"toolchain.{name}")
    conditions = exact_keys(profile["conditions"], {
        "sm_clock_mhz", "memory_clock_mhz", "power_limit_mw",
        "temperature_min_c", "temperature_max_c", "cache_condition",
        "concurrency_condition", "cluster_shape",
    }, "conditions")
    for name in ("sm_clock_mhz", "memory_clock_mhz", "power_limit_mw"):
        integer(conditions[name], f"conditions.{name}", positive=True)
    minimum = integer(conditions["temperature_min_c"],
                      "conditions.temperature_min_c")
    maximum = integer(conditions["temperature_max_c"],
                      "conditions.temperature_max_c")
    if minimum > maximum:
        fail(64, "invalid temperature interval")
    string(conditions["cache_condition"], "conditions.cache_condition")
    string(conditions["concurrency_condition"],
           "conditions.concurrency_condition")
    shape = exact_keys(conditions["cluster_shape"], {"x", "y", "z"},
                       "conditions.cluster_shape")
    for name in ("x", "y", "z"):
        integer(shape[name], f"conditions.cluster_shape.{name}", positive=True)
    thresholds = exact_keys(profile["thresholds"], {
        "p50_percent", "p95_percent", "counter_percent",
    }, "thresholds")
    if (number(thresholds["p50_percent"], "thresholds.p50_percent") > 5 or
            number(thresholds["p95_percent"], "thresholds.p95_percent") > 10 or
            number(thresholds["counter_percent"],
                   "thresholds.counter_percent") > 10):
        fail(64, "exact threshold exceeds its limit")
    limits = exact_keys(profile["limits"], {
        "max_thread_futures", "max_warp_futures", "max_cta_futures",
        "max_cluster_futures", "max_thread_async_objects",
        "max_warp_async_objects", "max_cta_async_objects",
        "max_cluster_async_objects",
    }, "limits")
    future = [integer(limits[name], f"limits.{name}", positive=True) for name in (
        "max_thread_futures", "max_warp_futures", "max_cta_futures",
        "max_cluster_futures")]
    objects = [integer(limits[name], f"limits.{name}", positive=True) for name in (
        "max_thread_async_objects", "max_warp_async_objects",
        "max_cta_async_objects", "max_cluster_async_objects")]
    if future != sorted(future) or objects != sorted(objects):
        fail(64, "exact limits are not nondecreasing")
    modules = profile["modules"]
    if not isinstance(modules, list) or not modules:
        fail(64, "profile requires modules")
    module_ids: set[str] = set()
    for index, module in enumerate(modules):
        record = validate_module(module, f"modules[{index}]")
        if record["module_id"] in module_ids:
            fail(64, "duplicate module ID")
        module_ids.add(record["module_id"])
    validation = exact_keys(profile["validation"], {
        "status", "training", "holdout", "classes",
    }, "validation")
    if validation["status"] not in ("pending", "passed", "failed"):
        fail(64, "invalid validation status")
    training = validate_dataset(validation["training"], "validation.training")
    holdout = validate_dataset(validation["holdout"], "validation.holdout")
    if (training["manifest_sha256"] == holdout["manifest_sha256"] or
            set(training["case_ids"]) & set(holdout["case_ids"])):
        fail(64, "training and holdout validation overlap")
    classes = validation["classes"]
    if not isinstance(classes, list):
        fail(64, "validation classes must be an array")
    names: set[str] = set()
    all_passed = True
    for index, item in enumerate(classes):
        record = exact_keys(item, {
            "operation_class", "passed", "p50_error_percent",
            "p95_error_percent", "counter_error_percent",
        }, f"validation.classes[{index}]")
        name = string(record["operation_class"], "operation_class")
        if name in names:
            fail(64, "duplicate validation class")
        names.add(name)
        if not isinstance(record["passed"], bool):
            fail(64, "invalid validation passed flag")
        p50 = number(record["p50_error_percent"], "p50 error")
        p95 = number(record["p95_error_percent"], "p95 error")
        counter = number(record["counter_error_percent"], "counter error")
        all_passed = (all_passed and record["passed"] and
                      p50 <= thresholds["p50_percent"] and
                      p95 <= thresholds["p95_percent"] and
                      counter <= thresholds["counter_percent"])
    if names != set(VALIDATION_CLASSES):
        fail(64, "validation classes are incomplete or unknown")
    if validation["status"] == "passed" and not all_passed:
        fail(64, "passed validation contains a failing class")
    if validation["status"] == "failed" and all_passed:
        fail(64, "failed validation contains no failing class")
    return profile


def validate_artifact(value: object) -> dict:
    artifact = exact_keys(value, {
        "schema_version", "module_id", "ptx_target", "toolchain", "hashes",
        "kernels",
    }, "artifact")
    if integer(artifact["schema_version"], "artifact.schema_version",
               positive=True) != 1:
        fail(64, "unsupported artifact schema")
    if MODULE_ID_RE.fullmatch(string(artifact["module_id"],
                                     "artifact.module_id")) is None:
        fail(64, "invalid artifact module ID")
    if string(artifact["ptx_target"], "artifact.ptx_target") not in TARGETS:
        fail(64, "invalid artifact target")
    toolchain = exact_keys(artifact["toolchain"], {
        "cuda_release", "ptxas_version", "nvdisasm_version",
        "cuobjdump_version",
    }, "artifact.toolchain")
    for name, value_text in toolchain.items():
        string(value_text, f"artifact.toolchain.{name}")
    hashes = exact_keys(artifact["hashes"], {
        "original_ptx_sha256", "transformed_ptx_sha256", "cubin_sha256",
        "sass_sha256",
    }, "artifact.hashes")
    for name, value_text in hashes.items():
        sha256(value_text, f"artifact.hashes.{name}")
    if not isinstance(artifact["kernels"], list) or not artifact["kernels"]:
        fail(64, "artifact requires kernels")
    names: set[str] = set()
    for index, item in enumerate(artifact["kernels"]):
        record = validate_kernel(item, f"artifact.kernels[{index}]")
        if record["name"] in names:
            fail(64, "duplicate artifact kernel")
        names.add(record["name"])
    return artifact


def validate_environment(value: object) -> dict:
    fields = {
        "gpu_name", "gpu_uuid", "pci_bus_id", "pci_vendor_id",
        "pci_device_id", "compute_capability_major", "compute_capability_minor",
        "cuda_driver_version", "sm_clock_mhz", "memory_clock_mhz",
        "power_limit_mw", "temperature_c", "current_process_is_exclusive",
        "captured_unix_ns",
    }
    environment = exact_keys(value, fields, "environment")
    for name in ("gpu_name", "gpu_uuid", "pci_bus_id"):
        string(environment[name], f"environment.{name}")
    for name in (
        "pci_vendor_id", "pci_device_id", "compute_capability_major",
        "cuda_driver_version", "sm_clock_mhz", "memory_clock_mhz",
        "power_limit_mw", "captured_unix_ns",
    ):
        integer(environment[name], f"environment.{name}", positive=True)
    integer(environment["compute_capability_minor"],
            "environment.compute_capability_minor")
    integer(environment["temperature_c"], "environment.temperature_c")
    if not isinstance(environment["current_process_is_exclusive"], bool):
        fail(64, "invalid environment.current_process_is_exclusive")
    return environment


def validate_contract(value: object) -> dict:
    contract = exact_keys(value, {
        "cache_condition", "concurrency_condition", "cluster_shape",
        "cache_condition_epoch", "latest_relevant_mutation_epoch",
    }, "run contract")
    string(contract["cache_condition"], "run_contract.cache_condition")
    string(contract["concurrency_condition"],
           "run_contract.concurrency_condition")
    shape = exact_keys(contract["cluster_shape"], {"x", "y", "z"},
                       "run_contract.cluster_shape")
    for name in ("x", "y", "z"):
        integer(shape[name], f"run_contract.cluster_shape.{name}", positive=True)
    integer(contract["cache_condition_epoch"],
            "run_contract.cache_condition_epoch")
    integer(contract["latest_relevant_mutation_epoch"],
            "run_contract.latest_relevant_mutation_epoch")
    return contract


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


def c_string(value) -> str:
    return bytes(value).split(b"\0", 1)[0].decode(errors="replace")


def collect_live(gate_path: pathlib.Path) -> tuple[dict | None, dict]:
    try:
        cuda = ctypes.CDLL("libcuda.so.1", mode=ctypes.RTLD_LOCAL)
        gate = ctypes.CDLL(str(gate_path), mode=ctypes.RTLD_LOCAL)
    except OSError as error:
        fail(70, f"unable to load CUDA exact environment provider: {error}")
    cuda.cuInit.argtypes = [ctypes.c_uint]
    cuda.cuInit.restype = ctypes.c_int
    cuda.cuDeviceGetCount.argtypes = [ctypes.POINTER(ctypes.c_int)]
    cuda.cuDeviceGetCount.restype = ctypes.c_int
    cuda.cuDeviceGet.argtypes = [ctypes.POINTER(ctypes.c_int), ctypes.c_int]
    cuda.cuDeviceGet.restype = ctypes.c_int
    cuda.cuDeviceGetAttribute.argtypes = [ctypes.POINTER(ctypes.c_int),
                                          ctypes.c_int, ctypes.c_int]
    cuda.cuDeviceGetAttribute.restype = ctypes.c_int
    if cuda.cuInit(0) != 0:
        return None, {"error_code": 2, "operation": "cuInit"}
    count = ctypes.c_int()
    if cuda.cuDeviceGetCount(ctypes.byref(count)) != 0:
        return None, {"error_code": 4, "operation": "cuDeviceGetCount"}
    selected: int | None = None
    for ordinal in range(count.value):
        device = ctypes.c_int()
        major = ctypes.c_int()
        minor = ctypes.c_int()
        if (cuda.cuDeviceGet(ctypes.byref(device), ordinal) == 0 and
                cuda.cuDeviceGetAttribute(ctypes.byref(major), 75,
                                          device.value) == 0 and
                cuda.cuDeviceGetAttribute(ctypes.byref(minor), 76,
                                          device.value) == 0 and
                (major.value, minor.value) == (12, 0)):
            selected = device.value
            break
    if selected is None:
        return None, {"error_code": 5, "operation": "select_sm120_device"}
    create_name = "cuCtxCreate_v4" if hasattr(cuda, "cuCtxCreate_v4") else "cuCtxCreate_v2"
    create = getattr(cuda, create_name)
    context = ctypes.c_void_p()
    if create_name == "cuCtxCreate_v4":
        create.argtypes = [ctypes.POINTER(ctypes.c_void_p), ctypes.c_void_p,
                           ctypes.c_uint, ctypes.c_int]
        create.restype = ctypes.c_int
        status = create(ctypes.byref(context), None, 0, selected)
    else:
        create.argtypes = [ctypes.POINTER(ctypes.c_void_p), ctypes.c_uint,
                           ctypes.c_int]
        create.restype = ctypes.c_int
        status = create(ctypes.byref(context), 0, selected)
    if status != 0:
        return None, {"error_code": 4, "native_status": status,
                      "operation": create_name}
    destroy = cuda.cuCtxDestroy_v2
    destroy.argtypes = [ctypes.c_void_p]
    destroy.restype = ctypes.c_int
    try:
        collect = gate.hbfsim_collect_exact_environment_v1
        collect.argtypes = [ctypes.POINTER(Snapshot), ctypes.c_size_t]
        collect.restype = ctypes.c_int
        snapshot = Snapshot()
        status = collect(ctypes.byref(snapshot), ctypes.sizeof(snapshot))
    except AttributeError as error:
        fail(70, f"launch gate lacks exact environment ABI: {error}")
    finally:
        destroy(context)
    diagnostics = {
        "error_code": snapshot.error_code,
        "native_status": snapshot.native_status,
        "operation": c_string(snapshot.operation),
    }
    if status != 0:
        return None, diagnostics
    return {
        "gpu_name": c_string(snapshot.gpu_name),
        "gpu_uuid": c_string(snapshot.gpu_uuid),
        "pci_bus_id": c_string(snapshot.pci_bus_id),
        "pci_vendor_id": snapshot.pci_vendor_id,
        "pci_device_id": snapshot.pci_device_id,
        "compute_capability_major": snapshot.compute_capability_major,
        "compute_capability_minor": snapshot.compute_capability_minor,
        "cuda_driver_version": snapshot.cuda_driver_version,
        "sm_clock_mhz": snapshot.sm_clock_mhz,
        "memory_clock_mhz": snapshot.memory_clock_mhz,
        "power_limit_mw": snapshot.power_limit_mw,
        "temperature_c": snapshot.temperature_c,
        "current_process_is_exclusive": bool(
            snapshot.current_process_is_exclusive),
        "captured_unix_ns": snapshot.captured_unix_ns,
    }, diagnostics


def validate_aot_authorization(gate_path: pathlib.Path, bundle: pathlib.Path,
                               artifact_bytes: bytes) -> bool:
    try:
        gate = ctypes.CDLL(str(gate_path), mode=ctypes.RTLD_LOCAL)
        begin = gate.hbfsim_begin_module_load_from_aot
        end = gate.hbfsim_end_module_load
    except (OSError, AttributeError) as error:
        fail(70, f"launch gate lacks AOT provenance ABI: {error}")
    cubin = (bundle / "module.cubin").read_bytes()
    cubin_storage = ctypes.create_string_buffer(cubin)
    artifact_storage = ctypes.create_string_buffer(artifact_bytes)
    begin.argtypes = [ctypes.c_void_p, ctypes.c_size_t, ctypes.c_char_p,
                      ctypes.c_size_t]
    begin.restype = ctypes.c_uint64
    end.argtypes = [ctypes.c_uint64]
    end.restype = None
    token = begin(cubin_storage, len(cubin), artifact_storage,
                  len(artifact_bytes))
    if token != 0:
        end(token)
    return token != 0


def pass_record(path: pathlib.Path, module_id: str, kernel: str) -> dict | None:
    try:
        records = [json.loads(line) for line in path.read_text().splitlines()
                   if line.strip()]
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        fail(64, f"invalid pass manifest: {error}")
    matches = [record for record in records
               if isinstance(record, dict) and
               record.get("module_id") == module_id and
               record.get("kernel") == kernel]
    if len(matches) != 1:
        return None
    return matches[0]


def kernel_by_name(records: list[dict], name: str) -> dict | None:
    matches = [record for record in records if record.get("name") == name]
    return matches[0] if len(matches) == 1 else None


def add(reasons: list[str], mismatch: bool, reason: str) -> None:
    if mismatch and reason not in reasons:
        reasons.append(reason)


def parser() -> argparse.ArgumentParser:
    result = Parser(description=__doc__)
    result.add_argument("--profile", required=True)
    result.add_argument("--bundle", required=True)
    result.add_argument("--pass-manifest", required=True)
    result.add_argument("--training-manifest", required=True)
    result.add_argument("--holdout-manifest", required=True)
    result.add_argument("--kernel", required=True)
    result.add_argument("--run-contract", required=True)
    result.add_argument("--launch-gate", required=True)
    result.add_argument("--environment-json")
    result.add_argument("--load-mode", choices=("aot", "jit"), default="aot")
    return result


def evaluate(args: argparse.Namespace) -> tuple[int, dict]:
    profile_path = regular_file(args.profile, "exact profile")
    bundle = bundle_directory(args.bundle)
    manifest_path = regular_file(args.pass_manifest, "pass manifest")
    training_path = regular_file(args.training_manifest, "training manifest")
    holdout_path = regular_file(args.holdout_manifest, "holdout manifest")
    contract_path = regular_file(args.run_contract, "run contract")
    gate_path = regular_file(args.launch_gate, "launch gate")
    profile = validate_profile(json_file(profile_path, "exact profile"))
    artifact_path = bundle / "artifact.json"
    artifact = validate_artifact(json_file(artifact_path,
                                           "artifact manifest"))
    contract = validate_contract(json_file(contract_path, "run contract"))
    environment_diagnostics: dict = {}
    if args.environment_json:
        environment_path = regular_file(args.environment_json,
                                        "environment snapshot")
        live = validate_environment(json_file(environment_path,
                                              "environment snapshot"))
        environment_source = "supplied_snapshot"
    else:
        live, environment_diagnostics = collect_live(gate_path)
        environment_source = "live_launch_gate"

    actual_hashes = {
        "original_ptx_sha256": sha256_file(bundle / "original.ptx"),
        "transformed_ptx_sha256": sha256_file(bundle / "transformed.ptx"),
        "cubin_sha256": sha256_file(bundle / "module.cubin"),
        "sass_sha256": sha256_file(bundle / "module.sass"),
    }
    reasons: list[str] = []
    validation = profile["validation"]
    add(reasons, validation["status"] != "passed", "profile_not_validated")
    classes_complete = (
        {item["operation_class"] for item in validation["classes"]} ==
        set(VALIDATION_CLASSES) and all(
            item["passed"] and
            item["p50_error_percent"] <= profile["thresholds"]["p50_percent"] and
            item["p95_error_percent"] <= profile["thresholds"]["p95_percent"] and
            item["counter_error_percent"] <= profile["thresholds"]["counter_percent"]
            for item in validation["classes"]
        )
    )
    add(reasons, not classes_complete, "validation_class_missing")
    training_matches = (sha256_file(training_path) ==
                        validation["training"]["manifest_sha256"])
    holdout_matches = (sha256_file(holdout_path) ==
                       validation["holdout"]["manifest_sha256"])
    add(reasons, not training_matches, "training_manifest_sha256_mismatch")
    add(reasons, not holdout_matches, "holdout_manifest_sha256_mismatch")
    calibration = profile["calibration"]
    add(reasons, calibration["raw_training_sha256"] !=
        validation["training"]["manifest_sha256"],
        "training_manifest_sha256_mismatch")
    add(reasons, calibration["raw_holdout_sha256"] !=
        validation["holdout"]["manifest_sha256"],
        "holdout_manifest_sha256_mismatch")

    if live is None:
        add(reasons, True, "live_environment_missing")
        live = {
            "gpu_name": "", "gpu_uuid": "", "pci_vendor_id": 0,
            "pci_device_id": 0, "compute_capability_major": 0,
            "compute_capability_minor": 0, "cuda_driver_version": 0,
            "sm_clock_mhz": 0, "memory_clock_mhz": 0, "power_limit_mw": 0,
            "temperature_c": 0, "current_process_is_exclusive": False,
            "captured_unix_ns": 0,
        }
    target = profile["target"]
    add(reasons, live["gpu_name"] != target["gpu_name"], "gpu_name_mismatch")
    add(reasons, live["gpu_uuid"] != target["gpu_uuid"], "gpu_uuid_mismatch")
    add(reasons, (live["pci_vendor_id"], live["pci_device_id"]) !=
        (target["pci_vendor_id"], target["pci_device_id"]),
        "pci_device_mismatch")
    add(reasons, (live["compute_capability_major"],
                  live["compute_capability_minor"]) !=
        (target["compute_capability_major"],
         target["compute_capability_minor"]), "compute_capability_mismatch")
    add(reasons, live["cuda_driver_version"] != target["driver_version"],
        "driver_version_mismatch")
    add(reasons, live["captured_unix_ns"] == 0, "live_environment_missing")

    module_id = artifact["module_id"]
    modules = [module for module in profile["modules"]
               if module["module_id"] == module_id]
    module = modules[0] if len(modules) == 1 else None
    add(reasons, module is None, "module_artifact_missing")
    if module is not None:
        add(reasons, artifact["ptx_target"] != module["ptx_target"],
            "ptx_target_mismatch")
    expected_tools = profile["toolchain"]
    tools = artifact["toolchain"]
    add(reasons, (
        expected_tools["cuda_version"] != tools["cuda_release"] or
        expected_tools["ptxas_version"] != tools["ptxas_version"] or
        expected_tools["nvdisasm_version"] != tools["nvdisasm_version"] or
        expected_tools["cuobjdump_version"] != tools["cuobjdump_version"]
    ), "toolchain_mismatch")

    declared_hashes = artifact["hashes"]
    aot_hashes_match = all(declared_hashes[name] == actual_hashes[name]
                           for name in actual_hashes)
    module_identity_matches = (
        MODULE_ID_RE.fullmatch(module_id) is not None and
        module_id == "ptx:sha256:" + declared_hashes["original_ptx_sha256"] and
        bundle.parent.name == declared_hashes["original_ptx_sha256"] and
        bundle.name == artifact["ptx_target"]
    )
    aot_authorization_verified = (
        args.load_mode == "aot" and validate_aot_authorization(
            gate_path, bundle, artifact_path.read_bytes())
    )
    aot_verified = (aot_authorization_verified and aot_hashes_match and
                    module_identity_matches)
    add(reasons, not aot_verified, "aot_evidence_missing")
    if module is not None:
        for field, reason in (
            ("original_ptx_sha256", "original_ptx_sha256_mismatch"),
            ("transformed_ptx_sha256", "transformed_ptx_sha256_mismatch"),
            ("cubin_sha256", "cubin_sha256_mismatch"),
            ("sass_sha256", "sass_sha256_mismatch"),
        ):
            add(reasons, module[field] != actual_hashes[field] or
                declared_hashes[field] != actual_hashes[field], reason)
        expected_kernel = kernel_by_name(module["kernels"], args.kernel)
        loaded_kernel = kernel_by_name(artifact["kernels"], args.kernel)
        add(reasons, expected_kernel is None or loaded_kernel is None,
            "kernel_resource_missing")
        if expected_kernel is not None and loaded_kernel is not None:
            add(reasons, expected_kernel["registers"] !=
                loaded_kernel["registers"], "register_count_mismatch")
            add(reasons, any(expected_kernel[name] != loaded_kernel[name]
                             for name in ("spill_store_bytes",
                                          "spill_load_bytes")),
                "spill_bytes_mismatch")
            add(reasons, any(expected_kernel[name] != loaded_kernel[name]
                             for name in ("static_shared_bytes",
                                          "max_dynamic_shared_bytes")),
                "shared_memory_mismatch")
            add(reasons, any(expected_kernel[name] != loaded_kernel[name]
                             for name in ("block_threads",
                                          "occupancy_blocks_per_sm")),
                "occupancy_tier_mismatch")

    conditions = profile["conditions"]
    add(reasons, live["sm_clock_mhz"] != conditions["sm_clock_mhz"],
        "sm_clock_mismatch")
    add(reasons, live["memory_clock_mhz"] != conditions["memory_clock_mhz"],
        "memory_clock_mismatch")
    add(reasons, live["power_limit_mw"] != conditions["power_limit_mw"],
        "power_limit_mismatch")
    add(reasons, not (conditions["temperature_min_c"] <=
                      live["temperature_c"] <=
                      conditions["temperature_max_c"]),
        "temperature_out_of_range")
    add(reasons, (
        contract["cache_condition"] != conditions["cache_condition"] or
        contract["cache_condition_epoch"] == 0 or
        contract["cache_condition_epoch"] <=
        contract["latest_relevant_mutation_epoch"]
    ), "cache_condition_unproven")
    add(reasons, (
        contract["concurrency_condition"] !=
        conditions["concurrency_condition"] or
        (conditions["concurrency_condition"] == "exclusive_process" and
         not live["current_process_is_exclusive"])
    ), "concurrency_condition_unproven")
    add(reasons, contract["cluster_shape"] != conditions["cluster_shape"],
        "cluster_shape_mismatch")

    manifest = pass_record(manifest_path, module_id, args.kernel)
    maximum_live = (manifest or {}).get("maximum_live_futures", {})
    async_complete = (
        manifest is not None and
        manifest.get("manifest_schema_version") == 3 and
        manifest.get("async_transform_version") == "sm120-future-v1" and
        SHA256_RE.fullmatch(str(manifest.get("ir_sha256", ""))) is not None and
        isinstance(manifest.get("instruction_table"), list) and
        bool(manifest.get("instruction_table")) and
        isinstance(maximum_live, dict) and
        all(isinstance(maximum_live.get(name), int) and
            maximum_live[name] > 0
            for name in ("thread", "warp", "cta", "cluster"))
    )
    pass_evidence = (
        async_complete and
        manifest.get("aot_required_for_exact") is True and
        manifest.get("instrumented") is True and
        manifest.get("cubin_only") is False
    )
    add(reasons, not pass_evidence, "pass_manifest_exact_evidence_missing")
    if pass_evidence:
        add(reasons, bool(manifest.get("ambiguities")),
            "async_transform_ambiguous")
        limits = profile["limits"]
        add(reasons, any((
            maximum_live["thread"] > limits["max_thread_futures"],
            maximum_live["warp"] > limits["max_warp_futures"],
            maximum_live["cta"] > limits["max_cta_futures"],
            maximum_live["cluster"] > limits["max_cluster_futures"],
        )), "future_budget_exceeded")
        add(reasons, (
            manifest.get("original_ptx_sha256") !=
            declared_hashes["original_ptx_sha256"] or
            manifest.get("transformed_ptx_sha256") !=
            declared_hashes["transformed_ptx_sha256"] or
            manifest.get("ptx_target") != artifact["ptx_target"]
        ), "pass_artifact_identity_mismatch")

    validation_passed = (
        validation["status"] == "passed" and classes_complete and
        training_matches and holdout_matches
    )
    allowed = not reasons
    decision = {
        "decision_schema_version": 1,
        "operation": "sm120_exact_admission_dry_run",
        "requested_fidelity": "exact",
        "admitted_fidelity": "calibrated_emulation",
        "allowed": allowed,
        "reason": "exact_admitted" if allowed else "exact_admission_failed",
        "exact_rejection_reasons": reasons,
        "exact_profile_id": profile["profile_id"],
        "module_id": module_id,
        "kernel": args.kernel,
        "cubin_sha256": actual_hashes["cubin_sha256"],
        "sass_sha256": actual_hashes["sass_sha256"],
        "aot_verified": aot_verified,
        "aot_authorization_verified": aot_authorization_verified,
        "validation_passed": validation_passed,
        "post_run_validation_passed": False,
        "routing_program_sha256":
            profile["calibration"]["routing"]["program_sha256"],
        "raw_training_sha256":
            profile["calibration"]["raw_training_sha256"],
        "raw_holdout_sha256":
            profile["calibration"]["raw_holdout_sha256"],
        "environment_source": environment_source,
        "environment_diagnostics": environment_diagnostics,
        "launch_attempted": False,
        "module_loaded": False,
        "scope": "stage4_prelaunch_only",
        "timing_fidelity_proven": False,
    }
    return (0 if allowed else 2), decision


def error_decision(error: AdmissionError) -> dict:
    return {
        "decision_schema_version": 1,
        "operation": "sm120_exact_admission_dry_run",
        "requested_fidelity": "exact",
        "admitted_fidelity": "calibrated_emulation",
        "allowed": False,
        "reason": "admission_input_error",
        "exact_rejection_reasons": [str(error)],
        "aot_verified": False,
        "aot_authorization_verified": False,
        "validation_passed": False,
        "post_run_validation_passed": False,
        "launch_attempted": False,
        "module_loaded": False,
        "scope": "stage1_identity_environment_reproducibility_only",
        "timing_fidelity_proven": False,
    }


def main() -> int:
    try:
        args = parser().parse_args()
        status, decision = evaluate(args)
        print(json.dumps(decision, sort_keys=True, separators=(",", ":")))
        return status
    except AdmissionError as error:
        print(json.dumps(error_decision(error), sort_keys=True,
                         separators=(",", ":")))
        print(f"check_sm120_exact_admission: {error}", file=sys.stderr)
        return error.code
    except (OSError, ValueError, KeyError, TypeError) as error:
        wrapped = AdmissionError(70, str(error))
        print(json.dumps(error_decision(wrapped), sort_keys=True,
                         separators=(",", ":")))
        print(f"check_sm120_exact_admission: {error}", file=sys.stderr)
        return 70


if __name__ == "__main__":
    raise SystemExit(main())
