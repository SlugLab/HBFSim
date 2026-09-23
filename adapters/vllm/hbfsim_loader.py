"""vLLM loader plugin for physically backed HBF timing ranges."""

from __future__ import annotations

import copy
import ctypes
import dataclasses
import json
import os
import pathlib
import re
from collections.abc import Callable, Mapping
from typing import Any

from vllm.config.load import LoadConfig
from vllm.model_executor.model_loader import register_model_loader
from vllm.model_executor.model_loader.base_loader import BaseModelLoader
from vllm.model_executor.model_loader.default_loader import DefaultModelLoader


HBFSIM_OK = 0
HBFSIM_INVALID_ARGUMENT = 1
HBFSIM_VLLM_ABI_VERSION = 2
_TIMING_MODELS = {"reference": 0, "fast": 1, "hybrid": 2}
_UINTPTR_LIMIT = (1 << (ctypes.sizeof(ctypes.c_void_p) * 8)) - 1
_REGISTERED = False
_STRICT_GATE_CAPABILITIES = 0x3
_STRICT_BRIDGE_CAPABILITIES = 0x1


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
    weight_selection: str = "all"
    include_patterns: tuple[str, ...] = ()
    exclude_patterns: tuple[str, ...] = ()
    accept_storage_closure: bool = False
    instrumentation_policy: str | None = None

    def __post_init__(self) -> None:
        # Direct construction is a supported legacy API.  Normalize the old
        # regex/cap knobs here as well as in from_mapping so they cannot
        # silently expand to the new default all/strict policy.
        if not isinstance(self.include_patterns, (list, tuple)) or not all(
                isinstance(item, str) for item in self.include_patterns):
            raise ValueError("include_patterns must be a list of strings")
        if not isinstance(self.exclude_patterns, (list, tuple)) or not all(
                isinstance(item, str) for item in self.exclude_patterns):
            raise ValueError("exclude_patterns must be a list of strings")
        if not isinstance(self.accept_storage_closure, bool):
            raise ValueError("accept_storage_closure must be boolean")
        legacy = bool(self.parameter_regex or self.max_bytes_per_storage)
        if self.parameter_regex:
            normalized_legacy = (
                self.weight_selection == "include" and
                tuple(self.include_patterns) == (self.parameter_regex,))
            if (self.include_patterns or self.weight_selection != "all") and not normalized_legacy:
                raise ValueError(
                    "legacy parameter_regex cannot be combined with new selection")
            object.__setattr__(self, "weight_selection", "include")
            object.__setattr__(self, "include_patterns",
                               (self.parameter_regex,))
        else:
            object.__setattr__(self, "include_patterns",
                               tuple(self.include_patterns))
        object.__setattr__(self, "exclude_patterns",
                           tuple(self.exclude_patterns))
        if self.instrumentation_policy is None:
            object.__setattr__(
                self, "instrumentation_policy",
                "partial" if legacy else "strict")
        if self.weight_selection not in {"all", "include", "off"}:
            raise ValueError("weight_selection must be all, include, or off")
        if self.instrumentation_policy not in {"strict", "partial"}:
            raise ValueError(
                "instrumentation_policy must be strict or partial")
        if self.weight_selection == "include" and not self.include_patterns:
            raise ValueError("include selection requires include_patterns")
        if self.weight_selection != "include" and self.include_patterns:
            raise ValueError(
                "include_patterns require weight_selection=include")
        if self.max_bytes_per_storage < 0:
            raise ValueError("max_bytes_per_storage must be nonnegative")
        for pattern in (self.parameter_regex, *self.include_patterns,
                        *self.exclude_patterns):
            try:
                re.compile(pattern)
            except re.error as error:
                raise ValueError(
                    f"invalid weight selection pattern: {error}") from error

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
        explicit_selection = "weight_selection" in raw
        weight_selection = str(raw.get(
            "weight_selection", "include" if parameter_regex else "all"))
        raw_includes = raw.get(
            "include_patterns", (parameter_regex,) if parameter_regex else ())
        raw_excludes = raw.get("exclude_patterns", ())
        if (not isinstance(raw_includes, (list, tuple)) or
                not all(isinstance(item, str) for item in raw_includes)):
            raise ValueError("include_patterns must be a list of strings")
        if (not isinstance(raw_excludes, (list, tuple)) or
                not all(isinstance(item, str) for item in raw_excludes)):
            raise ValueError("exclude_patterns must be a list of strings")
        for key in ("accept_storage_closure", "require_modeled_accesses",
                    "allow_opaque_timing"):
            if key in raw and not isinstance(raw[key], bool):
                raise ValueError(f"{key} must be boolean")
        include_patterns = tuple(raw_includes)
        exclude_patterns = tuple(raw_excludes)
        legacy_partial = bool(parameter_regex or max_bytes)
        instrumentation_policy = str(raw.get(
            "instrumentation_policy", "partial" if legacy_partial else "strict"))
        if weight_selection != "off" and (not profile or not report):
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
            for pattern in (*include_patterns, *exclude_patterns):
                re.compile(pattern)
        except re.error as error:
            raise ValueError(f"invalid weight selection pattern: {error}") from error
        if weight_selection not in {"all", "include", "off"}:
            raise ValueError("weight_selection must be all, include, or off")
        if instrumentation_policy not in {"strict", "partial"}:
            raise ValueError(
                "instrumentation_policy must be strict or partial")
        if weight_selection == "include" and not include_patterns:
            raise ValueError("include selection requires include_patterns")
        if weight_selection != "include" and include_patterns:
            raise ValueError(
                "include_patterns require weight_selection=include")
        if explicit_selection and parameter_regex:
            raise ValueError(
                "legacy parameter_regex cannot be combined with weight_selection")
        if max_bytes < 0:
            raise ValueError("max_bytes_per_storage must be nonnegative")
        if timing_model not in _TIMING_MODELS:
            raise ValueError(
                "timing_model must be reference, fast, or hybrid"
            )
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
            weight_selection=weight_selection,
            include_patterns=include_patterns,
            exclude_patterns=exclude_patterns,
            accept_storage_closure=bool(raw.get(
                "accept_storage_closure", False)),
            instrumentation_policy=instrumentation_policy,
        )


class _NativeOptions(ctypes.Structure):
    _fields_ = [
        ("profile_path", ctypes.c_char_p),
        ("report_dir", ctypes.c_char_p),
        ("ring_capacity", ctypes.c_uint32),
        ("timing_model", ctypes.c_uint32),
        ("request_timeout_ns", ctypes.c_uint64),
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
        options = _NativeOptions(
            config.profile_path.encode(),
            config.report_dir.encode(),
            config.ring_capacity,
            _TIMING_MODELS[config.timing_model],
            config.request_timeout_ns,
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


@dataclasses.dataclass
class _Storage:
    device: int
    address: int
    size: int
    aliases: list[str]
    tensors: list[dict[str, Any]]

    @property
    def end(self) -> int:
        return self.address + self.size


def _device_index(parameter: Any) -> int:
    index = parameter.device.index
    if index is not None:
        return int(index)
    import torch

    return int(torch.cuda.current_device())


def _discover_storages(model: Any) -> tuple[list[_Storage], int, bool]:
    unique: dict[tuple[int, int, int], _Storage] = {}
    parameter_count = 0
    devices: set[int] = set()
    try:
        parameters = model.named_parameters(recurse=True, remove_duplicate=False)
        alias_complete = True
    except TypeError:
        parameters = model.named_parameters(recurse=True)
        alias_complete = False
    for name, parameter in parameters:
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
            unique[key] = _Storage(device, address, size, [], [])
        unique[key].aliases.append(name)
        shape = [int(value) for value in getattr(parameter, "shape", ())]
        stride = [int(value) for value in parameter.stride()] if hasattr(
            parameter, "stride") else []
        offset = int(parameter.storage_offset()) if hasattr(
            parameter, "storage_offset") else 0
        element_size = int(parameter.element_size()) if hasattr(
            parameter, "element_size") else 0
        unique[key].tensors.append({
            "name": name,
            "shape": shape,
            "stride": stride,
            "dtype": str(getattr(parameter, "dtype", "unknown")),
            "storage_offset": offset,
            "element_size": element_size,
            "requires_grad": bool(getattr(parameter, "requires_grad", False)),
        })
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
    return storages, parameter_count, alias_complete


def _write_manifest(path: pathlib.Path, manifest: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    try:
        temporary.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _activate_instrumentation_policy(config: TimingConfig) -> None:
    if config.weight_selection == "off":
        return
    requested = str(config.instrumentation_policy)
    current = os.environ.get("HBFSIM_INSTRUMENTATION_POLICY")
    if current not in (None, "", requested):
        raise HbfSimError(
            "conflicting process-global instrumentation policy: "
            f"existing={current} requested={requested}")
    os.environ["HBFSIM_INSTRUMENTATION_POLICY"] = requested


def _require_strict_runtime_capabilities() -> None:
    process = ctypes.CDLL(None)
    requirements = (
        ("hbfsim_instrumentation_policy_capabilities_v1",
         _STRICT_GATE_CAPABILITIES),
        ("bpftime_nv_strict_bridge_capabilities_v1",
         _STRICT_BRIDGE_CAPABILITIES),
    )
    for symbol, required in requirements:
        try:
            function = getattr(process, symbol)
        except AttributeError as error:
            raise HbfSimError(
                f"strict instrumentation runtime capability missing: {symbol}"
            ) from error
        function.argtypes = []
        function.restype = ctypes.c_uint64
        observed = int(function())
        if observed & required != required:
            raise HbfSimError(
                "strict instrumentation runtime capability mismatch: "
                f"{symbol} required=0x{required:x} observed=0x{observed:x}")


def register_model_storages(
    model: Any,
    config: TimingConfig,
    session_factory: Callable[[TimingConfig], Any] = NativeTimingSession,
) -> dict[str, Any]:
    pathlib.Path(config.report_dir).mkdir(parents=True, exist_ok=True)
    if config.weight_selection == "off":
        manifest = {
            "schema_version": 2,
            "status": "DISABLED",
            "mode": "native",
            "parameter_count": None,
            "discovered_storage_count": None,
            "selection": {"mode": "off"},
            "storages": [],
        }
        _write_manifest(pathlib.Path(config.report_dir) / "registration.json",
                        manifest)
        return manifest
    storages, parameter_count, alias_complete = _discover_storages(model)
    if config.instrumentation_policy == "strict" and not alias_complete:
        raise HbfSimError(
            "strict instrumentation requires remove_duplicate=False support")
    try:
        includes = tuple(re.compile(item) for item in config.include_patterns)
        excludes = tuple(re.compile(item) for item in config.exclude_patterns)
        selected = []
        closure = []
        for storage in storages:
            candidates = {
                alias for alias in storage.aliases
                if config.weight_selection == "all" or
                any(pattern.search(alias) for pattern in includes)
            }
            excluded = {
                alias for alias in storage.aliases
                if any(pattern.search(alias) for pattern in excludes)
            }
            included = candidates - excluded
            if not included:
                continue
            if excluded:
                raise HbfSimError(
                    "selected storage also contains explicitly excluded aliases: " +
                    ", ".join(sorted(excluded)))
            extras = set(storage.aliases) - included
            if extras and not config.accept_storage_closure:
                raise HbfSimError(
                    "selection requires storage closure for aliases: " +
                    ", ".join(storage.aliases))
            selected.append(storage)
            closure.extend(sorted(extras))
        if not selected:
            raise HbfSimError("weight selection matched no CUDA storages")
        registered = [
            (storage, min(storage.size, config.max_bytes_per_storage)
             if config.max_bytes_per_storage else storage.size)
            for storage in selected
        ]
        if (config.instrumentation_policy == "strict" and
                any(size < storage.size for storage, size in registered)):
            raise HbfSimError(
                "strict instrumentation forbids truncated storage binding")
        _activate_instrumentation_policy(config)
        if config.instrumentation_policy == "strict":
            _require_strict_runtime_capabilities()
        session = session_factory(config)
        for storage, bytes_to_register in registered:
            session.register_storage(storage.address, bytes_to_register)
        manifest = {
            "schema_version": 2,
            "status": "REGISTERED_PENDING_INSTRUMENTATION",
            "mode": "timing_backed",
            "device": storages[0].device,
            "parameter_count": parameter_count,
            "alias_discovery_complete": alias_complete,
            "discovered_storage_count": len(storages),
            "unique_storage_count": len(registered),
            "registered_bytes": sum(size for _, size in registered),
            "profile_path": config.profile_path,
            "timing_model": config.timing_model,
            "selection": {
                "mode": config.weight_selection,
                "include_patterns": list(config.include_patterns),
                "exclude_patterns": list(config.exclude_patterns),
                "accept_storage_closure": config.accept_storage_closure,
                "storage_closure_aliases": closure,
                "instrumentation_policy": config.instrumentation_policy,
                "parameter_regex": config.parameter_regex,
                "max_bytes_per_storage": config.max_bytes_per_storage,
            },
            "storages": [
                {
                    "address": item.address,
                    "bytes": registered_bytes,
                    "storage_bytes": item.size,
                    "aliases": item.aliases,
                    "tensors": item.tensors,
                }
                for item, registered_bytes in registered
            ],
        }
        _write_manifest(pathlib.Path(config.report_dir) / "registration.json",
                        manifest)
        setattr(model, "_hbfsim_timing_session", session)
        return manifest
    except Exception:
        if "session" in locals():
            session.close()
        raise


def close_model_session(model: Any) -> None:
    session = getattr(model, "_hbfsim_timing_session", None)
    if session is None:
        return
    session.close()
    delattr(model, "_hbfsim_timing_session")


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
        register_model_storages(model, self._timing_config)
        return model


def register() -> None:
    """vLLM general-plugin entry point; safe to call repeatedly."""
    global _REGISTERED
    if _REGISTERED:
        return
    register_model_loader("hbfsim")(HbfSimModelLoader)
    _REGISTERED = True
