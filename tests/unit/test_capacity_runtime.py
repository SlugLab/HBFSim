from __future__ import annotations

import ctypes
import hashlib
import importlib
import sys
import threading
import types

import pytest


def _module():
    try:
        return importlib.import_module("adapters.vllm_capacity.capacity_runtime")
    except ModuleNotFoundError:
        return importlib.import_module("capacity_runtime")


def test_stats_v2_layout_matches_frozen_header():
    runtime = _module()
    names = [name for name, _ in runtime.HBFSimStatsV2._fields_]
    assert names[:3] == ["schema_version", "reserved0", "valid_fields"]
    assert names[-3:] == [
        "demand_exposed_stall_ns",
        "hidden_prefetched_stall_ns",
        "late_prefetch_stall_ns",
    ]
    assert len(names) == 41


def test_launch_gate_is_hash_checked_and_loaded_explicit_global(
    monkeypatch, tmp_path
):
    runtime = _module()
    gate_path = tmp_path / "libhbfsim_launch_gate.so"
    gate_path.write_bytes(b"frozen-gate")
    digest = hashlib.sha256(gate_path.read_bytes()).hexdigest()
    monkeypatch.setenv("HBFSIM_LAUNCH_GATE_LIBRARY", str(gate_path))
    monkeypatch.setenv("HBFSIM_LAUNCH_GATE_LIBRARY_SHA256", digest)
    monkeypatch.setattr(runtime, "_LAUNCH_GATE_LIBRARY", None)
    monkeypatch.setattr(runtime, "_LAUNCH_GATE_IDENTITY", None)

    class FakeCall:
        argtypes = None
        restype = None

        def __call__(self, version):
            return 1 if version == runtime.HBFSIM_LAUNCH_GATE_ABI_VERSION else 0

    class FakeGate:
        hbfsim_launch_gate_get_api = FakeCall()
        hbfsim_begin_module_load_from_ptx = FakeCall()
        hbfsim_end_module_load = FakeCall()
        cuModuleLoadDataEx = FakeCall()
        cuLaunchKernel = FakeCall()

    gate = FakeGate()
    seen = []

    def fake_cdll(path, *, mode):
        seen.append((path, mode))
        return gate

    monkeypatch.setattr(runtime.ctypes, "CDLL", fake_cdll)

    assert runtime.get_launch_gate_library() is gate
    assert runtime.get_launch_gate_library() is gate
    assert seen == [(str(gate_path.resolve()), ctypes.RTLD_GLOBAL)]
    assert runtime._LAUNCH_GATE_IDENTITY == (str(gate_path.resolve()), digest)


def test_launch_gate_hash_mismatch_fails_before_loading(monkeypatch, tmp_path):
    runtime = _module()
    gate_path = tmp_path / "libhbfsim_launch_gate.so"
    gate_path.write_bytes(b"changed-gate")
    monkeypatch.setenv("HBFSIM_LAUNCH_GATE_LIBRARY", str(gate_path))
    monkeypatch.setenv("HBFSIM_LAUNCH_GATE_LIBRARY_SHA256", "0" * 64)
    monkeypatch.setattr(runtime, "_LAUNCH_GATE_LIBRARY", None)
    monkeypatch.setattr(runtime, "_LAUNCH_GATE_IDENTITY", None)
    monkeypatch.setattr(
        runtime.ctypes,
        "CDLL",
        lambda *args, **kwargs: pytest.fail("hash mismatch reached dlopen"),
    )

    with pytest.raises(RuntimeError, match="launch gate SHA256 mismatch"):
        runtime.get_launch_gate_library()


def test_logical_address_bounds():
    runtime = _module()
    instance = object.__new__(runtime.CapacityRuntime)
    instance._lock = __import__("threading").RLock()
    instance._mappings = {
        "x.safetensors": runtime.MappedShard(
            "x.safetensors", "/x", 100, 1000
        )
    }
    assert instance.logical_address("x.safetensors", 40, 20) == 1040
    try:
        instance.logical_address("x.safetensors", 90, 20)
    except RuntimeError as exc:
        assert "out-of-bounds" in str(exc)
    else:
        raise AssertionError("out-of-bounds logical range was accepted")


def test_ring_capacity_environment_contract(monkeypatch):
    runtime = _module()
    monkeypatch.delenv("HBFSIM_CAPACITY_RING_CAPACITY", raising=False)
    assert runtime._ring_capacity_from_environment() == 64

    for value in (2, 8, 64, 4096):
        monkeypatch.setenv("HBFSIM_CAPACITY_RING_CAPACITY", str(value))
        assert runtime._ring_capacity_from_environment() == value

    for value in (0, 1, 3, 4097):
        monkeypatch.setenv("HBFSIM_CAPACITY_RING_CAPACITY", str(value))
        try:
            runtime._ring_capacity_from_environment()
        except RuntimeError as exc:
            assert "power of two" in str(exc)
        else:
            raise AssertionError(f"invalid ring capacity accepted: {value}")


def test_open_passes_valid_nonzero_ring_to_frozen_abi(monkeypatch, tmp_path):
    runtime = _module()
    observed = {}

    class FakeLib:
        @staticmethod
        def hbfsim_vllm_abi_version():
            return 2

        @staticmethod
        def hbfsim_context_create(options, context):
            typed_options = ctypes.cast(
                options, ctypes.POINTER(runtime.HBFSimOptions)
            ).contents
            observed["ring_capacity"] = typed_options.ring_capacity
            ctypes.cast(context, ctypes.POINTER(ctypes.c_void_p))[0] = (
                ctypes.c_void_p(1234)
            )
            return 0

    fake_cuda = types.SimpleNamespace(
        is_available=lambda: True,
        init=lambda: None,
    )
    monkeypatch.setitem(sys.modules, "torch", types.SimpleNamespace(cuda=fake_cuda))

    instance = object.__new__(runtime.CapacityRuntime)
    instance.profile_path = tmp_path / "profile.json"
    instance.report_dir = tmp_path
    instance.ring_capacity = 64
    instance.request_timeout_ns = 300_000_000_000
    instance._lock = threading.RLock()
    instance._lib = FakeLib()
    instance._context = ctypes.c_void_p()
    instance._mappings = {}
    instance._closed = False
    monkeypatch.setattr(instance, "_bind", lambda: instance._lib)

    instance.open()
    assert observed["ring_capacity"] == 64
    assert instance.is_open


def test_opaque_pointer_provenance_rejects_capacity_overlap():
    runtime = _module()
    instance = object.__new__(runtime.CapacityRuntime)
    instance._lock = threading.RLock()
    instance._context = ctypes.c_void_p(1)
    instance._closed = False
    instance._pointer_provenance_checks = 0
    instance._mappings = {
        "x.safetensors": runtime.MappedShard(
            "x.safetensors", "/x", 100, 1000
        )
    }
    instance.assert_outside_capacity_mappings(2000, 64, label="ordinary")
    assert instance.pointer_provenance_summary()["checks"] == 1
    try:
        instance.assert_outside_capacity_mappings(1050, 16, label="capacity")
    except RuntimeError as exc:
        assert "capacity pointer reached opaque CUDA launch" in str(exc)
    else:
        raise AssertionError("overlapping capacity pointer was accepted")


def test_teardown_failure_is_reported_after_context_destroy():
    runtime = _module()
    calls = []

    class FakeLib:
        @staticmethod
        def hbfsim_flush(context):
            calls.append("flush")
            return 0

        @staticmethod
        def hbfsim_unregister(context, address):
            calls.append("unregister")
            return 7

        @staticmethod
        def hbfsim_context_destroy(context):
            calls.append("destroy")

    instance = object.__new__(runtime.CapacityRuntime)
    instance._lock = threading.RLock()
    instance._lib = FakeLib()
    instance._context = ctypes.c_void_p(1)
    instance._closed = False
    instance._mappings = {
        "x.safetensors": runtime.MappedShard(
            "x.safetensors", "/x", 100, 1000
        )
    }
    try:
        instance.close()
    except RuntimeError as exc:
        assert "hbfsim_unregister" in str(exc)
    else:
        raise AssertionError("teardown failure was hidden")
    assert calls == ["flush", "unregister", "destroy"]
    assert instance._closed is True
    assert not instance._mappings
