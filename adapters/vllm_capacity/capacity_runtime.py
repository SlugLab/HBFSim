"""Strict one-context ctypes binding for the HBFSim capacity ABI.

This module deliberately has no vLLM or torch import at module import time.  A
worker creates the context only after its CUDA context exists, maps each frozen
safetensors shard once, and owns every mapping until explicit teardown.
"""

from __future__ import annotations

import atexit
import ctypes
import hashlib
import json
import os
import pathlib
import threading
from dataclasses import asdict, dataclass
from typing import Any


HBFSIM_OK = 0
HBFSIM_RANGE_MODE_CAPACITY = 2
HBFSIM_RANGE_READ = 1
HBFSIM_CACHE_POLICY_NONE = 0
HBFSIM_STATS_V2_SCHEMA_VERSION = 2
HBFSIM_VLLM_ABI_VERSION = 2
HBFSIM_LAUNCH_GATE_ABI_VERSION = 3
DEFAULT_RING_CAPACITY = 64
FROZEN_LIBRARY_SHA256 = (
    "5618c6fcc8a0ddd630272dd08f3d518e07f58657bf866c980925c275225a8f04"
)

_LAUNCH_GATE_LOCK = threading.Lock()
_LAUNCH_GATE_LIBRARY: ctypes.CDLL | None = None
_LAUNCH_GATE_IDENTITY: tuple[str, str] | None = None


class HBFSimOptions(ctypes.Structure):
    _fields_ = [
        ("profile_path", ctypes.c_char_p),
        ("report_dir", ctypes.c_char_p),
        ("mode", ctypes.c_uint32),
        ("ring_capacity", ctypes.c_uint32),
        ("request_timeout_ns", ctypes.c_uint64),
    ]


class HBFSimRangeOptions(ctypes.Structure):
    _fields_ = [
        ("mode", ctypes.c_uint32),
        ("permissions", ctypes.c_uint32),
        ("cache_policy", ctypes.c_uint32),
        ("stream_id", ctypes.c_uint32),
    ]


class HBFSimStatsV2(ctypes.Structure):
    _fields_ = [
        ("schema_version", ctypes.c_uint32),
        ("reserved0", ctypes.c_uint32),
        ("valid_fields", ctypes.c_uint64),
        ("requests_total", ctypes.c_uint64),
        ("demand_requests", ctypes.c_uint64),
        ("speculative_requests", ctypes.c_uint64),
        ("modeled_device_time_ns", ctypes.c_uint64),
        ("host_service_time_ns", ctypes.c_uint64),
        ("backing_io_wall_time_ns", ctypes.c_uint64),
        ("h2d_copy_time_ns", ctypes.c_uint64),
        ("dtoh_copy_time_ns", ctypes.c_uint64),
        ("emulator_dispatcher_wall_time_ns", ctypes.c_uint64),
        ("application_wall_time_ns", ctypes.c_uint64),
        ("configured_hbm_cache_bytes", ctypes.c_uint64),
        ("actual_page_aligned_hbm_cache_bytes", ctypes.c_uint64),
        ("hbf_logical_bytes", ctypes.c_uint64),
        ("hbf_actually_accessed_bytes", ctypes.c_uint64),
        ("resident_bytes_current", ctypes.c_uint64),
        ("resident_bytes_peak", ctypes.c_uint64),
        ("free_frames", ctypes.c_uint64),
        ("frame_count", ctypes.c_uint64),
        ("hits", ctypes.c_uint64),
        ("misses", ctypes.c_uint64),
        ("byte_hit_ratio", ctypes.c_double),
        ("page_hit_ratio", ctypes.c_double),
        ("clean_evictions", ctypes.c_uint64),
        ("dirty_evictions", ctypes.c_uint64),
        ("writeback_bytes", ctypes.c_uint64),
        ("hbf_read_bytes", ctypes.c_uint64),
        ("hbf_program_bytes", ctypes.c_uint64),
        ("duplicate_misses", ctypes.c_uint64),
        ("coalesced_misses", ctypes.c_uint64),
        ("in_flight_pages", ctypes.c_uint64),
        ("page_residence_time_ns", ctypes.c_uint64),
        ("completed_residences", ctypes.c_uint64),
        ("engine_outstanding_requests", ctypes.c_uint64),
        ("demand_waiting_time_ns", ctypes.c_uint64),
        ("speculative_waiting_time_ns", ctypes.c_uint64),
        ("demand_exposed_stall_ns", ctypes.c_uint64),
        ("hidden_prefetched_stall_ns", ctypes.c_uint64),
        ("late_prefetch_stall_ns", ctypes.c_uint64),
    ]


@dataclass(frozen=True)
class MappedShard:
    name: str
    path: str
    bytes: int
    logical_address: int


def _required_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"required environment variable is unset: {name}")
    return value


def _validate_ring_capacity(value: int) -> int:
    capacity = int(value)
    if capacity < 2 or capacity > 4096 or capacity & (capacity - 1):
        raise RuntimeError(
            "HBFSIM capacity ring must be a power of two in [2, 4096]: "
            f"{capacity}"
        )
    return capacity


def _ring_capacity_from_environment() -> int:
    return _validate_ring_capacity(
        int(
            os.environ.get(
                "HBFSIM_CAPACITY_RING_CAPACITY", str(DEFAULT_RING_CAPACITY)
            )
        )
    )


def _check_path(path: pathlib.Path, *, kind: str) -> pathlib.Path:
    resolved = path.expanduser().resolve(strict=True)
    if kind == "file" and not resolved.is_file():
        raise RuntimeError(f"expected file: {resolved}")
    if kind == "dir" and not resolved.is_dir():
        raise RuntimeError(f"expected directory: {resolved}")
    return resolved


def _file_sha256(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(4 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def get_launch_gate_library() -> ctypes.CDLL:
    """Load the frozen gate explicitly without process-wide LD_PRELOAD."""
    global _LAUNCH_GATE_LIBRARY, _LAUNCH_GATE_IDENTITY

    path = _check_path(
        pathlib.Path(_required_env("HBFSIM_LAUNCH_GATE_LIBRARY")),
        kind="file",
    )
    expected = _required_env("HBFSIM_LAUNCH_GATE_LIBRARY_SHA256")
    actual = _file_sha256(path)
    if actual != expected:
        raise RuntimeError(
            f"frozen HBFSim launch gate SHA256 mismatch: {actual} != {expected}"
        )
    identity = (str(path), actual)
    with _LAUNCH_GATE_LOCK:
        if _LAUNCH_GATE_LIBRARY is not None:
            if _LAUNCH_GATE_IDENTITY != identity:
                raise RuntimeError(
                    "launch gate identity changed after binding: "
                    f"{_LAUNCH_GATE_IDENTITY!r} != {identity!r}"
                )
            return _LAUNCH_GATE_LIBRARY
        gate = ctypes.CDLL(str(path), mode=ctypes.RTLD_GLOBAL)
        required = (
            "hbfsim_launch_gate_get_api",
            "hbfsim_begin_module_load_from_ptx",
            "hbfsim_end_module_load",
            "cuModuleLoadDataEx",
            "cuLaunchKernel",
        )
        missing = [name for name in required if not hasattr(gate, name)]
        if missing:
            raise RuntimeError(f"launch gate is missing symbols: {missing}")
        getter = gate.hbfsim_launch_gate_get_api
        getter.argtypes = (ctypes.c_uint32,)
        getter.restype = ctypes.c_void_p
        if not getter(HBFSIM_LAUNCH_GATE_ABI_VERSION):
            raise RuntimeError(
                "launch gate does not provide the required capacity ABI"
            )
        _LAUNCH_GATE_LIBRARY = gate
        _LAUNCH_GATE_IDENTITY = identity
        return gate


class CapacityRuntime:
    """Own exactly one HBFSim context and all shard mappings in one worker."""

    def __init__(
        self,
        *,
        library_path: pathlib.Path,
        profile_path: pathlib.Path,
        report_dir: pathlib.Path,
        ring_capacity: int = DEFAULT_RING_CAPACITY,
        request_timeout_ns: int = 300_000_000_000,
    ) -> None:
        self.library_path = _check_path(library_path, kind="file")
        self.profile_path = _check_path(profile_path, kind="file")
        self.report_dir = report_dir.expanduser().resolve()
        self.report_dir.mkdir(parents=True, exist_ok=True)
        self.ring_capacity = _validate_ring_capacity(ring_capacity)
        self.request_timeout_ns = int(request_timeout_ns)
        if self.request_timeout_ns <= 0:
            raise RuntimeError("capacity request timeout must be positive")
        self._lock = threading.RLock()
        self._lib: ctypes.CDLL | None = None
        self._bound_library_sha256: str | None = None
        self._bound_launch_gate_sha256: str | None = None
        self._context = ctypes.c_void_p()
        self._mappings: dict[str, MappedShard] = {}
        self._pointer_provenance_checks = 0
        self._closed = False

    @classmethod
    def from_environment(cls) -> "CapacityRuntime":
        return cls(
            library_path=pathlib.Path(_required_env("HBFSIM_CAPACITY_LIBRARY")),
            profile_path=pathlib.Path(_required_env("HBFSIM_CAPACITY_PROFILE")),
            report_dir=pathlib.Path(_required_env("HBFSIM_CAPACITY_REPORT_DIR")),
            ring_capacity=_ring_capacity_from_environment(),
            request_timeout_ns=int(
                os.environ.get("HBFSIM_CAPACITY_TIMEOUT_NS", "300000000000")
            ),
        )

    @property
    def is_open(self) -> bool:
        return bool(self._context.value) and not self._closed

    @property
    def mappings(self) -> dict[str, MappedShard]:
        with self._lock:
            return dict(self._mappings)

    def _bind(self) -> ctypes.CDLL:
        if self._lib is not None:
            return self._lib
        # The gate must be visible to hbfsim_core's RTLD_DEFAULT ABI lookup,
        # but it must not interpose every unrelated PyTorch/vLLM CUDA launch.
        get_launch_gate_library()
        self._bound_launch_gate_sha256 = _LAUNCH_GATE_IDENTITY[1]
        expected = os.environ.get(
            "HBFSIM_CAPACITY_LIBRARY_SHA256", FROZEN_LIBRARY_SHA256
        )
        actual = _file_sha256(self.library_path)
        if actual != expected:
            raise RuntimeError(
                f"frozen HBFSim library SHA256 mismatch: {actual} != {expected}"
            )
        lib = ctypes.CDLL(str(self.library_path), mode=ctypes.RTLD_LOCAL)
        lib.hbfsim_vllm_abi_version.argtypes = ()
        lib.hbfsim_vllm_abi_version.restype = ctypes.c_uint32
        lib.hbfsim_context_create.argtypes = (
            ctypes.POINTER(HBFSimOptions),
            ctypes.POINTER(ctypes.c_void_p),
        )
        lib.hbfsim_context_create.restype = ctypes.c_int
        lib.hbfsim_map_file.argtypes = (
            ctypes.c_void_p,
            ctypes.c_char_p,
            ctypes.c_uint64,
            ctypes.c_size_t,
            ctypes.POINTER(HBFSimRangeOptions),
            ctypes.POINTER(ctypes.c_void_p),
        )
        lib.hbfsim_map_file.restype = ctypes.c_int
        lib.hbfsim_flush.argtypes = (ctypes.c_void_p,)
        lib.hbfsim_flush.restype = ctypes.c_int
        lib.hbfsim_get_stats_v2.argtypes = (
            ctypes.c_void_p,
            ctypes.POINTER(HBFSimStatsV2),
            ctypes.c_size_t,
        )
        lib.hbfsim_get_stats_v2.restype = ctypes.c_int
        lib.hbfsim_unregister.argtypes = (ctypes.c_void_p, ctypes.c_void_p)
        lib.hbfsim_unregister.restype = ctypes.c_int
        lib.hbfsim_context_destroy.argtypes = (ctypes.c_void_p,)
        lib.hbfsim_context_destroy.restype = None
        self._bound_library_sha256 = actual
        self._lib = lib
        return lib

    @staticmethod
    def _raise_status(operation: str, status: int) -> None:
        if status != HBFSIM_OK:
            raise RuntimeError(f"{operation} failed with HBFSim status {status}")

    def open(self) -> None:
        with self._lock:
            if self.is_open:
                return
            if self._closed:
                raise RuntimeError("capacity runtime cannot be reopened after close")
            # Importing torch here (not at module import) guarantees that callers
            # can establish the worker CUDA context before HBFSim allocates cache.
            import torch

            if not torch.cuda.is_available():
                raise RuntimeError("capacity runtime requires a CUDA worker")
            torch.cuda.init()
            lib = self._bind()
            abi = int(lib.hbfsim_vllm_abi_version())
            if abi != HBFSIM_VLLM_ABI_VERSION:
                raise RuntimeError(
                    f"HBFSim vLLM ABI mismatch: {abi} != {HBFSIM_VLLM_ABI_VERSION}"
                )
            profile = os.fsencode(self.profile_path)
            report = os.fsencode(self.report_dir)
            options = HBFSimOptions(
                profile,
                report,
                0,
                self.ring_capacity,
                self.request_timeout_ns,
            )
            context = ctypes.c_void_p()
            self._raise_status(
                "hbfsim_context_create",
                int(lib.hbfsim_context_create(ctypes.byref(options), ctypes.byref(context))),
            )
            if not context.value:
                raise RuntimeError("HBFSim returned a null context")
            self._context = context

    def map_shards(self, model_root: pathlib.Path, files: list[dict[str, Any]]) -> None:
        """Map every frozen shard once; mappings live until :meth:`close`."""
        with self._lock:
            self.open()
            root = _check_path(model_root, kind="dir")
            lib = self._bind()
            options = HBFSimRangeOptions(
                HBFSIM_RANGE_MODE_CAPACITY,
                HBFSIM_RANGE_READ,
                HBFSIM_CACHE_POLICY_NONE,
                0,
            )
            planned: list[tuple[str, pathlib.Path, int]] = []
            seen: set[str] = set()
            for record in sorted(files, key=lambda item: str(item["path"])):
                name = pathlib.Path(str(record["path"])).name
                if not name.endswith(".safetensors"):
                    continue
                if name in seen:
                    raise RuntimeError(f"duplicate shard record: {name}")
                seen.add(name)
                path = _check_path(root / name, kind="file")
                size = path.stat().st_size
                expected = int(
                    record.get("bytes", record.get("size_bytes", record.get("size", size)))
                )
                if size != expected:
                    raise RuntimeError(
                        f"shard size mismatch for {name}: {size} != {expected}"
                    )
                existing = self._mappings.get(name)
                if existing is not None:
                    if existing.path != str(path) or existing.bytes != size:
                        raise RuntimeError(
                            f"existing shard mapping conflicts with frozen input: {name}"
                        )
                    continue
                planned.append((name, path, size))
            if not seen:
                raise RuntimeError("capacity manifest contains no safetensors shards")

            try:
                for name, path, size in planned:
                    logical = ctypes.c_void_p()
                    self._raise_status(
                        f"hbfsim_map_file({name})",
                        int(
                            lib.hbfsim_map_file(
                                self._context,
                                os.fsencode(path),
                                0,
                                size,
                                ctypes.byref(options),
                                ctypes.byref(logical),
                            )
                        ),
                    )
                    if not logical.value:
                        raise RuntimeError(f"null logical mapping for {name}")
                    self._mappings[name] = MappedShard(
                        name=name,
                        path=str(path),
                        bytes=size,
                        logical_address=int(logical.value),
                    )
            except BaseException as exc:
                try:
                    self.close()
                except BaseException as cleanup_exc:
                    raise RuntimeError(
                        "capacity shard mapping failed and teardown also failed: "
                        f"mapping={exc!r} teardown={cleanup_exc!r}"
                    ) from exc
                raise

    def logical_address(self, shard: str, file_offset: int, length: int) -> int:
        with self._lock:
            mapping = self._mappings.get(pathlib.Path(shard).name)
            if mapping is None:
                raise RuntimeError(f"shard has not been mapped: {shard}")
            if file_offset < 0 or length <= 0 or file_offset + length > mapping.bytes:
                raise RuntimeError(
                    f"out-of-bounds range {file_offset}+{length} for {mapping.name}"
                )
            return mapping.logical_address + file_offset

    def shard_mapping(self, shard: str) -> MappedShard:
        with self._lock:
            mapping = self._mappings.get(pathlib.Path(shard).name)
            if mapping is None:
                raise RuntimeError(f"shard has not been mapped: {shard}")
            return mapping

    def assert_outside_capacity_mappings(
        self, address: int, length: int, *, label: str
    ) -> None:
        """Fail closed if an opaque CUDA argument overlaps any capacity range."""
        start = int(address)
        size = int(length)
        if start <= 0 or size <= 0:
            raise RuntimeError(
                f"invalid opaque CUDA tensor range for {label}: {start}+{size}"
            )
        end = start + size
        with self._lock:
            if not self.is_open or not self._mappings:
                raise RuntimeError(
                    f"capacity mappings are unavailable for pointer check: {label}"
                )
            for mapping in self._mappings.values():
                mapping_start = mapping.logical_address
                mapping_end = mapping_start + mapping.bytes
                if start < mapping_end and mapping_start < end:
                    raise RuntimeError(
                        "capacity pointer reached opaque CUDA launch: "
                        f"{label}={start:#x}+{size} overlaps {mapping.name} "
                        f"[{mapping_start:#x}, {mapping_end:#x})"
                    )
            self._pointer_provenance_checks += 1

    def pointer_provenance_summary(self) -> dict[str, int | bool]:
        with self._lock:
            return {
                "checks": self._pointer_provenance_checks,
                "capacity_mappings_present": bool(self._mappings),
                "all_checked_arguments_outside_capacity_mappings": True,
            }

    def flush(self) -> None:
        with self._lock:
            if not self.is_open:
                raise RuntimeError("flush requested before context creation")
            self._raise_status(
                "hbfsim_flush", int(self._bind().hbfsim_flush(self._context))
            )

    def stats_v2(self) -> dict[str, int | float]:
        with self._lock:
            if not self.is_open:
                raise RuntimeError("stats requested before context creation")
            stats = HBFSimStatsV2()
            self._raise_status(
                "hbfsim_get_stats_v2",
                int(
                    self._bind().hbfsim_get_stats_v2(
                        self._context, ctypes.byref(stats), ctypes.sizeof(stats)
                    )
                ),
            )
            if stats.schema_version != HBFSIM_STATS_V2_SCHEMA_VERSION:
                raise RuntimeError(
                    f"unexpected stats schema {stats.schema_version}"
                )
            return {name: getattr(stats, name) for name, _ in stats._fields_}

    def write_runtime_manifest(self, path: pathlib.Path) -> None:
        with self._lock:
            payload = {
                "schema_version": 1,
                "library_path": str(self.library_path),
                "library_sha256": self._bound_library_sha256,
                "launch_gate_path": (
                    _LAUNCH_GATE_IDENTITY[0]
                    if _LAUNCH_GATE_IDENTITY is not None
                    else None
                ),
                "launch_gate_sha256": self._bound_launch_gate_sha256,
                "launch_gate_loading": "explicit-rtld-global-not-ld-preload",
                "vllm_abi_version": HBFSIM_VLLM_ABI_VERSION,
                "profile_path": str(self.profile_path),
                "report_dir": str(self.report_dir),
                "ring_capacity": self.ring_capacity,
                "one_context": True,
                "mapping_lifetime": "worker-load-through-explicit-teardown",
                "pointer_provenance": self.pointer_provenance_summary(),
                "mappings": [
                    asdict(record)
                    for record in sorted(
                        self._mappings.values(), key=lambda item: item.name
                    )
                ],
            }
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
        temporary.replace(path)

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            lib = self._lib
            context = self._context
            errors: list[str] = []
            if lib is not None and context.value:
                # Flush before unregistering so no completion can refer to a
                # released mapping.  Teardown proceeds even if flush fails.
                try:
                    status = int(lib.hbfsim_flush(context))
                    if status != HBFSIM_OK:
                        errors.append(f"hbfsim_flush status {status}")
                except BaseException as exc:
                    errors.append(f"hbfsim_flush raised {exc!r}")
                try:
                    for mapping in reversed(list(self._mappings.values())):
                        try:
                            status = int(
                                lib.hbfsim_unregister(
                                    context,
                                    ctypes.c_void_p(mapping.logical_address),
                                )
                            )
                            if status != HBFSIM_OK:
                                errors.append(
                                    f"hbfsim_unregister({mapping.name}) status {status}"
                                )
                        except BaseException as exc:
                            errors.append(
                                f"hbfsim_unregister({mapping.name}) raised {exc!r}"
                            )
                finally:
                    try:
                        lib.hbfsim_context_destroy(context)
                    except BaseException as exc:
                        errors.append(f"hbfsim_context_destroy raised {exc!r}")
            self._mappings.clear()
            self._context = ctypes.c_void_p()
            self._closed = True
            if errors:
                raise RuntimeError("capacity teardown failed: " + "; ".join(errors))


_SINGLETON_LOCK = threading.Lock()
_SINGLETON: CapacityRuntime | None = None


def get_capacity_runtime() -> CapacityRuntime:
    global _SINGLETON
    with _SINGLETON_LOCK:
        if _SINGLETON is None:
            _SINGLETON = CapacityRuntime.from_environment()
            atexit.register(_SINGLETON.close)
        return _SINGLETON


def capacity_runtime_if_created() -> CapacityRuntime | None:
    with _SINGLETON_LOCK:
        return _SINGLETON


def reset_capacity_runtime_for_tests() -> None:
    global _SINGLETON
    with _SINGLETON_LOCK:
        if _SINGLETON is not None:
            _SINGLETON.close()
        _SINGLETON = None
