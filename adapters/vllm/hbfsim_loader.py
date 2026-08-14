"""vLLM loader plugin for physically backed HBF timing ranges."""

from __future__ import annotations

import copy
import ctypes
import dataclasses
import hashlib
import json
import os
import pathlib
import re
import subprocess
import time
import weakref
from collections.abc import Callable, Mapping
from typing import Any

from vllm.config.load import LoadConfig
from vllm.model_executor.model_loader import register_model_loader
from vllm.model_executor.model_loader.base_loader import BaseModelLoader
from vllm.model_executor.model_loader.default_loader import DefaultModelLoader


HBFSIM_OK = 0
HBFSIM_INVALID_ARGUMENT = 1
HBFSIM_VLLM_ABI_VERSION = 3
_TIMING_MODELS = {"reference": 0, "fast": 1, "hybrid": 2}
_UINTPTR_LIMIT = (1 << (ctypes.sizeof(ctypes.c_void_p) * 8)) - 1
_REGISTERED = False
_ACTIVE_SESSIONS: weakref.WeakSet[Any] = weakref.WeakSet()


class HbfSimError(RuntimeError):
    """Raised when timing registration cannot be completed safely."""


@dataclasses.dataclass(frozen=True)
class TimingConfig:
    profile_path: str
    report_dir: str
    ring_capacity: int = 64
    request_timeout_ns: int = 1_000_000_000
    underlying_load_format: str = "safetensors"
    default_loader_extra_config: dict[str, Any] = dataclasses.field(
        default_factory=dict
    )
    require_modeled_accesses: bool = True
    allow_opaque_timing: bool = True
    parameter_regex: str = ""
    max_bytes_per_storage: int = 0
    timing_model: str = "hybrid"
    exact_profile_path: str = ""
    exact_cache_condition: str = ""
    exact_cluster_x: int = 0
    exact_cluster_y: int = 0
    exact_cluster_z: int = 0
    exact_preheat: bool = False

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> "TimingConfig":
        allowed = {field.name for field in dataclasses.fields(cls)}
        unexpected = set(raw) - allowed
        if unexpected:
            raise ValueError(
                f"unexpected HBFSim loader configuration keys: "
                f"{sorted(unexpected)}"
            )
        profile = str(raw.get("profile_path", ""))
        report = str(raw.get("report_dir", ""))
        ring = int(raw.get("ring_capacity", 64))
        timeout = int(raw.get("request_timeout_ns", 1_000_000_000))
        underlying = str(raw.get("underlying_load_format", "safetensors"))
        delegate_extra = raw.get("default_loader_extra_config", {})
        parameter_regex = str(raw.get("parameter_regex", ""))
        max_bytes = int(raw.get("max_bytes_per_storage", 0))
        timing_model = str(raw.get("timing_model", "hybrid"))
        exact_profile = str(raw.get("exact_profile_path", ""))
        exact_cache = str(raw.get("exact_cache_condition", ""))
        exact_cluster_x = int(raw.get("exact_cluster_x", 0))
        exact_cluster_y = int(raw.get("exact_cluster_y", 0))
        exact_cluster_z = int(raw.get("exact_cluster_z", 0))
        exact_preheat = bool(raw.get("exact_preheat", False))
        if not profile or not report:
            raise ValueError("profile_path and report_dir are required")
        if ring <= 0 or timeout <= 0:
            raise ValueError(
                "ring_capacity and request_timeout_ns must be positive"
            )
        if not underlying or underlying == "hbfsim":
            raise ValueError("underlying_load_format must not be hbfsim")
        if not isinstance(delegate_extra, dict):
            raise ValueError("default_loader_extra_config must be a mapping")
        try:
            re.compile(parameter_regex)
        except re.error as error:
            raise ValueError(f"invalid parameter_regex: {error}") from error
        if max_bytes < 0:
            raise ValueError("max_bytes_per_storage must be nonnegative")
        if timing_model not in _TIMING_MODELS:
            raise ValueError(
                "timing_model must be reference, fast, or hybrid"
            )
        if exact_profile and (
            exact_cache not in {"warm_l2", "cold"}
            or min(exact_cluster_x, exact_cluster_y, exact_cluster_z) <= 0
        ):
            raise ValueError(
                "exact mode requires a cache condition and positive cluster shape"
            )
        if not exact_profile and (
            exact_cache or exact_cluster_x or exact_cluster_y or exact_cluster_z
        ):
            raise ValueError("exact contract requires exact_profile_path")
        if exact_preheat and not exact_profile:
            raise ValueError("exact_preheat requires exact_profile_path")
        return cls(
            profile_path=profile,
            report_dir=report,
            ring_capacity=ring,
            request_timeout_ns=timeout,
            underlying_load_format=underlying,
            default_loader_extra_config=copy.deepcopy(delegate_extra),
            require_modeled_accesses=bool(
                raw.get("require_modeled_accesses", True)
            ),
            allow_opaque_timing=bool(raw.get("allow_opaque_timing", True)),
            parameter_regex=parameter_regex,
            max_bytes_per_storage=max_bytes,
            timing_model=timing_model,
            exact_profile_path=exact_profile,
            exact_cache_condition=exact_cache,
            exact_cluster_x=exact_cluster_x,
            exact_cluster_y=exact_cluster_y,
            exact_cluster_z=exact_cluster_z,
            exact_preheat=exact_preheat,
        )


class _NativeOptions(ctypes.Structure):
    _fields_ = [
        ("struct_bytes", ctypes.c_uint32),
        ("profile_path", ctypes.c_char_p),
        ("report_dir", ctypes.c_char_p),
        ("ring_capacity", ctypes.c_uint32),
        ("timing_model", ctypes.c_uint32),
        ("request_timeout_ns", ctypes.c_uint64),
        ("exact_profile_path", ctypes.c_char_p),
        ("exact_cache_condition", ctypes.c_uint32),
        ("exact_concurrency_condition", ctypes.c_uint32),
        ("exact_cluster_x", ctypes.c_uint32),
        ("exact_cluster_y", ctypes.c_uint32),
        ("exact_cluster_z", ctypes.c_uint32),
    ]


class NativeTimingSession:
    """Owns one native HBFSim context in a vLLM GPU worker."""

    def __init__(self, config: TimingConfig):
        library_path = os.environ.get("HBFSIM_VLLM_EXTENSION", "")
        if not library_path:
            raise HbfSimError("HBFSIM_VLLM_EXTENSION is required")
        try:
            self._library = ctypes.CDLL(library_path)
        except OSError as error:
            raise HbfSimError(
                f"cannot load HBFSim vLLM extension {library_path}: {error}"
            ) from error
        self._bind()
        if self._library.hbfsim_vllm_abi_version() != HBFSIM_VLLM_ABI_VERSION:
            raise HbfSimError("HBFSim vLLM extension ABI mismatch")
        self._handle = ctypes.c_void_p()
        self._exact_requested = bool(config.exact_profile_path)
        options = _NativeOptions(
            ctypes.sizeof(_NativeOptions),
            config.profile_path.encode(),
            config.report_dir.encode(),
            config.ring_capacity,
            _TIMING_MODELS[config.timing_model],
            config.request_timeout_ns,
            config.exact_profile_path.encode() if config.exact_profile_path
            else None,
            1 if config.exact_cache_condition == "warm_l2" else
            2 if config.exact_cache_condition == "cold" else 0,
            1 if config.exact_profile_path else 0,
            config.exact_cluster_x,
            config.exact_cluster_y,
            config.exact_cluster_z,
        )
        status = self._library.hbfsim_vllm_session_create(
            ctypes.byref(options), ctypes.byref(self._handle)
        )
        if status != HBFSIM_OK or not self._handle.value:
            raise HbfSimError(
                "HBFSim timing session creation failed: "
                f"{self._status(status)} ({status})"
            )

    def _bind(self) -> None:
        self._library.hbfsim_vllm_abi_version.argtypes = []
        self._library.hbfsim_vllm_abi_version.restype = ctypes.c_uint32
        self._library.hbfsim_vllm_session_create.argtypes = [
            ctypes.POINTER(_NativeOptions),
            ctypes.POINTER(ctypes.c_void_p),
        ]
        self._library.hbfsim_vllm_session_create.restype = ctypes.c_int
        self._library.hbfsim_vllm_register_storage.argtypes = [
            ctypes.c_void_p,
            ctypes.c_size_t,
            ctypes.c_size_t,
        ]
        self._library.hbfsim_vllm_register_storage.restype = ctypes.c_int
        self._library.hbfsim_vllm_publish_exact_contract.argtypes = [
            ctypes.c_void_p
        ]
        self._library.hbfsim_vllm_publish_exact_contract.restype = ctypes.c_int
        self._library.hbfsim_vllm_finalize_exact.argtypes = [ctypes.c_void_p]
        self._library.hbfsim_vllm_finalize_exact.restype = ctypes.c_int
        self._library.hbfsim_vllm_session_close.argtypes = [
            ctypes.POINTER(ctypes.c_void_p)
        ]
        self._library.hbfsim_vllm_session_close.restype = ctypes.c_int
        self._library.hbfsim_vllm_status_string.argtypes = [ctypes.c_int]
        self._library.hbfsim_vllm_status_string.restype = ctypes.c_char_p

    def _status(self, status: int) -> str:
        value = self._library.hbfsim_vllm_status_string(status)
        return value.decode() if value else "unknown"

    def register_storage(self, address: int, size: int) -> None:
        if not self._handle.value:
            raise HbfSimError("HBFSim timing session is closed")
        status = self._library.hbfsim_vllm_register_storage(
            self._handle, address, size
        )
        if status != HBFSIM_OK:
            raise HbfSimError(
                f"storage 0x{address:x}+{size} registration failed: "
                f"{self._status(status)} ({status})"
            )

    def publish_exact_contract(self) -> None:
        if not self._exact_requested:
            raise HbfSimError("session was not created for exact fidelity")
        status = self._library.hbfsim_vllm_publish_exact_contract(self._handle)
        if status != HBFSIM_OK:
            raise HbfSimError(
                "HBFSim exact run-contract publication failed: "
                f"{self._status(status)} ({status})"
            )

    def finalize_exact(self) -> None:
        if not self._exact_requested:
            raise HbfSimError("session was not created for exact fidelity")
        status = self._library.hbfsim_vllm_finalize_exact(self._handle)
        if status != HBFSIM_OK:
            raise HbfSimError(
                "HBFSim exact post-run finalization failed: "
                f"{self._status(status)} ({status})"
            )

    def abort(self) -> None:
        if not getattr(self, "_handle", None) or not self._handle.value:
            return
        self._library.hbfsim_vllm_session_close(ctypes.byref(self._handle))

    def close(self) -> None:
        if not getattr(self, "_handle", None) or not self._handle.value:
            return
        status = self._library.hbfsim_vllm_session_close(
            ctypes.byref(self._handle)
        )
        if status != HBFSIM_OK:
            raise HbfSimError(
                f"HBFSim timing session close failed: "
                f"{self._status(status)} ({status})"
            )

    def __del__(self) -> None:
        try:
            self.close()
        except Exception:
            pass


class _NativeExactProbeBackend:
    def __init__(self) -> None:
        build = pathlib.Path(os.environ.get("HBFSIM_BUILD_DIR", ""))
        library_path = pathlib.Path(os.environ.get(
            "HBFSIM_VLLM_EXACT_PROBE_LIBRARY",
            str(build / "libhbfsim_llama_probe.so"),
        ))
        self.ptx_path = pathlib.Path(os.environ.get(
            "HBFSIM_VLLM_EXACT_PROBE_PTX",
            str(build / "hbfsim_llama_probe.ptx"),
        ))
        if not library_path.is_file() or not self.ptx_path.is_file():
            raise HbfSimError("vLLM exact sideband probe artifacts are missing")
        self.library = ctypes.CDLL(str(library_path.resolve()))
        self.function = self.library.hbfsim_llama_probe_function
        self.function.argtypes = []
        self.function.restype = ctypes.c_void_p
        self.launch_function = self.library.hbfsim_llama_launch_probe
        self.launch_function.argtypes = (
            ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint64, ctypes.c_void_p,
        )
        self.launch_function.restype = ctypes.c_int
        try:
            self.bind_function = ctypes.CDLL(None).bpftime_nv_bind_ptx_variant
        except AttributeError as error:
            raise HbfSimError("bpftime exact PTX variant binder is not loaded") \
                from error
        self.bind_function.argtypes = (
            ctypes.c_void_p, ctypes.c_char_p, ctypes.c_size_t, ctypes.c_char_p,
        )
        self.bind_function.restype = ctypes.c_int

    def prepare(self) -> dict[str, Any]:
        payload = self.ptx_path.read_bytes()
        function = self.function()
        if not function:
            raise HbfSimError("vLLM exact probe CUDA function is unavailable")
        result = 1
        attempts = 0
        while result == 1 and attempts <= 100:
            result = int(self.bind_function(
                ctypes.c_void_p(function), payload, len(payload),
                b"hbfsim_llama_probe_kernel",
            ))
            if result == 1 and attempts < 100:
                time.sleep(0.05)
            attempts += 1
        if result != 0:
            raise HbfSimError(
                f"vLLM exact probe PTX binding failed: {result}"
            )
        return {
            "result": "bound",
            "attempts": attempts,
            "ptx_path": str(self.ptx_path.resolve()),
            "ptx_sha256": hashlib.sha256(payload).hexdigest(),
            "original_function": hex(int(function)),
        }

    def launch(self, output: Any, input_: Any) -> None:
        status = self.launch_function(
            ctypes.c_void_p(output.data_ptr()),
            ctypes.c_void_p(input_.data_ptr()), 0, None,
        )
        if status != 0:
            raise HbfSimError(f"vLLM exact probe launch failed: {status}")
        import torch

        torch.cuda.synchronize()


def _probe_tensors(device: int) -> tuple[Any, Any]:
    import torch
    import run_exact_probe

    with torch.cuda.device(device):
        values = [
            value if value < (1 << 63) else value - (1 << 64)
            for value in run_exact_probe.probe_input_values()
        ]
        input_ = torch.tensor(values, dtype=torch.int64, device="cuda")
        output = torch.zeros(
            run_exact_probe.PROBE_THREADS, dtype=torch.int64, device="cuda"
        )
    return input_, output


def _probe_tensor_values(tensor: Any) -> list[int]:
    return [
        int(value) & ((1 << 64) - 1)
        for value in tensor.detach().cpu().tolist()
    ]


def run_one_shot_exact_probe(
    config: TimingConfig,
    *,
    device: int,
    session_factory: Callable[[TimingConfig], Any] = NativeTimingSession,
    probe_factory: Callable[[int], tuple[Any, Any]] | None = None,
    probe_backend: Any | None = None,
    tensor_values: Callable[[Any], list[int]] | None = None,
) -> dict[str, Any]:
    if not config.exact_profile_path:
        raise HbfSimError("one-shot exact probe requires exact mode")
    import run_exact_probe

    if config.exact_preheat:
        preheat_exact_device(config, device)
    input_, output = (probe_factory or _probe_tensors)(device)
    values = tensor_values or _probe_tensor_values
    input_values = values(input_)
    backend = probe_backend or _NativeExactProbeBackend()
    probe_config = dataclasses.replace(
        config,
        ring_capacity=max(
            config.ring_capacity, run_exact_probe.PROBE_RING_CAPACITY
        ),
        request_timeout_ns=max(
            config.request_timeout_ns,
            run_exact_probe.PROBE_REQUEST_TIMEOUT_NS,
        ),
    )
    session = session_factory(probe_config)
    try:
        if len(input_values) != run_exact_probe.PROBE_WORDS:
            raise HbfSimError("one-shot exact probe input shape mismatch")
        session.register_storage(
            int(input_.data_ptr()), run_exact_probe.PROBE_BYTES
        )
        _write_manifest(pathlib.Path(config.report_dir) / "registration.json", {
            "schema_version": 1,
            "mode": "exact_sideband_probe",
            "requested_fidelity": "exact",
            "device": device,
            "registered_bytes": run_exact_probe.PROBE_BYTES,
            "profile_path": config.profile_path,
            "exact_profile_path": config.exact_profile_path,
            "storages": [{
                "address": int(input_.data_ptr()),
                "bytes": run_exact_probe.PROBE_BYTES,
                "aliases": ["__hbfsim_vllm_exact_probe_shadow__"],
            }],
        })
        binding = backend.prepare()
        session.publish_exact_contract()
        backend.launch(output, input_)
        output_values = values(output)
        try:
            expected_output = run_exact_probe.validate_probe_output(
                input_values, output_values
            )
        except RuntimeError as error:
            raise HbfSimError(str(error)) from error
        session.finalize_exact()
        session.close()
    except Exception:
        session.abort()
        raise
    result = {
        "schema_version": 1,
        "status": "passed",
        "scope": "one_shot_sideband_probe",
        "model_graph_fidelity": "native",
        "device": device,
        "registered_bytes": run_exact_probe.PROBE_BYTES,
        "input_sha256": hashlib.sha256(json.dumps(
            input_values, separators=(",", ":")
        ).encode()).hexdigest(),
        "output_sha256": hashlib.sha256(json.dumps(
            output_values, separators=(",", ":")
        ).encode()).hexdigest(),
        "expected_output_sha256": hashlib.sha256(json.dumps(
            expected_output, separators=(",", ":")
        ).encode()).hexdigest(),
        "bit_exact": True,
        "binding": binding,
    }
    _write_manifest(pathlib.Path(config.report_dir) / "exact-probe.json", result)
    return result


@dataclasses.dataclass
class _Storage:
    device: int
    address: int
    size: int
    aliases: list[str]

    @property
    def end(self) -> int:
        return self.address + self.size


def _device_index(parameter: Any) -> int:
    index = parameter.device.index
    if index is not None:
        return int(index)
    import torch

    return int(torch.cuda.current_device())


def _discover_storages(model: Any) -> tuple[list[_Storage], int]:
    unique: dict[tuple[int, int, int], _Storage] = {}
    parameter_count = 0
    devices: set[int] = set()
    for name, parameter in model.named_parameters(recurse=True):
        parameter_count += 1
        if getattr(parameter.device, "type", None) != "cuda":
            raise HbfSimError(f"finalized parameter is not CUDA-backed: {name}")
        device = _device_index(parameter)
        storage = parameter.untyped_storage()
        address = int(storage.data_ptr())
        size = int(storage.nbytes())
        if address <= 0 or size <= 0 or address > _UINTPTR_LIMIT - size:
            raise HbfSimError(f"invalid CUDA storage for parameter: {name}")
        devices.add(device)
        key = (device, address, size)
        if key not in unique:
            unique[key] = _Storage(device, address, size, [])
        unique[key].aliases.append(name)
    if parameter_count == 0 or not unique:
        raise HbfSimError("model has no CUDA parameter storages")
    if len(devices) != 1:
        raise HbfSimError(f"mixed CUDA devices are unsupported: {sorted(devices)}")
    storages = sorted(unique.values(), key=lambda item: (item.device,
                                                         item.address,
                                                         item.size))
    for previous, current in zip(storages, storages[1:]):
        if previous.device == current.device and current.address < previous.end:
            raise HbfSimError(
                "overlapping CUDA storages are unsafe: "
                f"0x{previous.address:x}-0x{previous.end:x} and "
                f"0x{current.address:x}-0x{current.end:x}"
            )
    return storages, parameter_count


def _write_manifest(path: pathlib.Path, manifest: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    try:
        temporary.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _gpu_temperature_c(device: int) -> int:
    import torch

    uuid = str(torch.cuda.get_device_properties(device).uuid)
    identifier = uuid if uuid.startswith("GPU-") else f"GPU-{uuid}"
    completed = subprocess.run(
        ["nvidia-smi", f"--id={identifier}",
         "--query-gpu=temperature.gpu", "--format=csv,noheader,nounits"],
        check=True, capture_output=True, text=True, timeout=5,
    )
    values = [line.strip() for line in completed.stdout.splitlines()
              if line.strip()]
    if len(values) != 1:
        raise HbfSimError("could not read one exact GPU temperature")
    return int(values[0])


def preheat_exact_device(
    config: TimingConfig,
    device: int,
    *,
    temperature_reader: Callable[[int], int] | None = None,
    heat_step: Callable[[int], None] | None = None,
) -> dict[str, Any]:
    if not config.exact_preheat or not config.exact_profile_path:
        raise HbfSimError("exact preheat was not requested")
    profile_path = pathlib.Path(config.exact_profile_path)
    if profile_path.is_symlink() or not profile_path.is_file():
        raise HbfSimError("exact preheat profile is not a regular file")
    try:
        conditions = json.loads(profile_path.read_text())["conditions"]
        minimum = int(conditions["temperature_min_c"])
        maximum = int(conditions["temperature_max_c"])
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
        raise HbfSimError("exact preheat profile has no thermal window") from error
    if minimum < 0 or maximum < minimum:
        raise HbfSimError("exact preheat profile has an invalid thermal window")

    read_temperature = temperature_reader or _gpu_temperature_c
    cleanup: Callable[[], None] | None = None
    if heat_step is None:
        import torch

        with torch.cuda.device(device):
            size = 8192
            left = torch.ones((size, size), device="cuda", dtype=torch.bfloat16)
            right = torch.ones((size, size), device="cuda", dtype=torch.bfloat16)
            result = torch.empty_like(left)

        def default_heat_step(selected_device: int) -> None:
            with torch.cuda.device(selected_device):
                for _ in range(64):
                    torch.mm(left, right, out=result)
                torch.cuda.synchronize(selected_device)

        def cleanup_tensors() -> None:
            nonlocal left, right, result
            del left, right, result
            with torch.cuda.device(device):
                torch.cuda.empty_cache()

        heat_step = default_heat_step
        cleanup = cleanup_tensors

    started = time.monotonic()
    initial = read_temperature(device)
    current = initial
    upper_margin = maximum - 5 if maximum - minimum >= 5 else maximum
    target = min(upper_margin, minimum + 15)
    steps = 0
    try:
        if current > maximum:
            raise HbfSimError(
                f"GPU temperature {current}C exceeds exact maximum {maximum}C"
            )
        while current < target:
            if time.monotonic() - started > 120:
                raise HbfSimError(
                    f"GPU did not reach exact minimum {minimum}C within 120s"
                )
            heat_step(device)
            steps += 1
            current = read_temperature(device)
            if current > maximum:
                raise HbfSimError(
                    f"GPU temperature {current}C exceeds exact maximum {maximum}C"
                )
    finally:
        if cleanup is not None:
            cleanup()

    manifest = {
        "schema_version": 1,
        "status": "passed",
        "device": device,
        "profile_path": str(profile_path.resolve()),
        "temperature_min_c": minimum,
        "temperature_max_c": maximum,
        "target_temperature_c": target,
        "initial_temperature_c": initial,
        "final_temperature_c": current,
        "steps": steps,
        "elapsed_seconds": time.monotonic() - started,
    }
    _write_manifest(pathlib.Path(config.report_dir) / "preheat.json", manifest)
    return manifest


def register_model_storages(
    model: Any,
    config: TimingConfig,
    session_factory: Callable[[TimingConfig], Any] = NativeTimingSession,
) -> dict[str, Any]:
    storages, parameter_count = _discover_storages(model)
    pathlib.Path(config.report_dir).mkdir(parents=True, exist_ok=True)
    session = session_factory(config)
    try:
        matcher = re.compile(config.parameter_regex)
        selected = [
            storage for storage in storages
            if not config.parameter_regex or
            any(matcher.search(alias) for alias in storage.aliases)
        ]
        if not selected:
            raise HbfSimError("parameter_regex matched no CUDA storages")
        registered = [
            (storage, min(storage.size, config.max_bytes_per_storage)
             if config.max_bytes_per_storage else storage.size)
            for storage in selected
        ]
        for storage, bytes_to_register in registered:
            session.register_storage(storage.address, bytes_to_register)
        _ACTIVE_SESSIONS.add(session)
        manifest = {
            "schema_version": 1,
            "mode": "timing_backed",
            "device": storages[0].device,
            "parameter_count": parameter_count,
            "discovered_storage_count": len(storages),
            "unique_storage_count": len(registered),
            "registered_bytes": sum(size for _, size in registered),
            "profile_path": config.profile_path,
            "timing_model": config.timing_model,
            "requested_fidelity": (
                "exact" if config.exact_profile_path else "emulation"
            ),
            "exact_profile_path": config.exact_profile_path,
            "selection": {
                "parameter_regex": config.parameter_regex,
                "max_bytes_per_storage": config.max_bytes_per_storage,
            },
            "storages": [
                {
                    "address": item.address,
                    "bytes": registered_bytes,
                    "storage_bytes": item.size,
                    "aliases": item.aliases,
                }
                for item, registered_bytes in registered
            ],
        }
        _write_manifest(pathlib.Path(config.report_dir) / "registration.json",
                        manifest)
        setattr(model, "_hbfsim_timing_session", session)
        return manifest
    except Exception:
        abort = getattr(session, "abort", None)
        abort() if abort is not None else session.close()
        raise


def close_model_session(model: Any) -> None:
    session = getattr(model, "_hbfsim_timing_session", None)
    if session is None:
        return
    session.close()
    _ACTIVE_SESSIONS.discard(session)
    delattr(model, "_hbfsim_timing_session")


def publish_exact_sessions() -> int:
    sessions = [session for session in _ACTIVE_SESSIONS
                if getattr(session, "_exact_requested", False)]
    if not sessions:
        raise HbfSimError("no active exact vLLM timing sessions")
    for session in sessions:
        session.publish_exact_contract()
    return len(sessions)


def finalize_exact_sessions() -> int:
    sessions = [session for session in _ACTIVE_SESSIONS
                if getattr(session, "_exact_requested", False)]
    if not sessions:
        raise HbfSimError("no active exact vLLM timing sessions")
    for session in sessions:
        session.finalize_exact()
    return len(sessions)


class HbfSimModelLoader(BaseModelLoader):
    """Default vLLM loader followed by finalized-storage registration."""

    def __init__(self, load_config: LoadConfig):
        super().__init__(load_config)
        raw = load_config.model_loader_extra_config
        if not isinstance(raw, Mapping):
            raise ValueError("HBFSim model_loader_extra_config must be a mapping")
        self._timing_config = TimingConfig.from_mapping(raw)
        self._delegate_config = copy.deepcopy(load_config)
        self._delegate_config.load_format = (
            self._timing_config.underlying_load_format
        )
        self._delegate_config.model_loader_extra_config = copy.deepcopy(
            self._timing_config.default_loader_extra_config
        )
        self._delegate = DefaultModelLoader(self._delegate_config)

    def download_model(self, model_config: Any) -> None:
        self._delegate.download_model(model_config)

    def load_weights(self, model: Any, model_config: Any) -> None:
        self._delegate.load_weights(model, model_config)

    def load_model(
        self, vllm_config: Any, model_config: Any, prefix: str = ""
    ) -> Any:
        model = self._delegate.load_model(
            vllm_config=vllm_config,
            model_config=model_config,
            prefix=prefix,
        )
        if self._timing_config.exact_preheat:
            storages, _ = _discover_storages(model)
            preheat_exact_device(
                self._timing_config, storages[0].device
            )
        register_model_storages(model, self._timing_config)
        return model


def register() -> None:
    """vLLM general-plugin entry point; safe to call repeatedly."""
    global _REGISTERED
    if _REGISTERED:
        return
    register_model_loader("hbfsim")(HbfSimModelLoader)
    _REGISTERED = True
