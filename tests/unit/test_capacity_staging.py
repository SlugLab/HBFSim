from __future__ import annotations

import importlib
import inspect
import textwrap
import types

import pytest


def _module():
    try:
        return importlib.import_module("adapters.vllm_capacity.capacity_staging")
    except ModuleNotFoundError:
        return importlib.import_module("capacity_staging")


def test_stable_unique_and_remap():
    staging = _module()
    values = [7, 2, 7, 4, 2]
    assert staging.stable_unique(values) == (7, 2, 4)
    table = staging.build_remap(values)
    assert staging.remap_nested_ids([[7, 4], [2, 7]], table) == [[0, 2], [1, 0]]


def test_fused_expert_slot_count_pads_repeated_dummy_routes_to_topk():
    staging = _module()

    assert staging.fused_expert_slot_count(1, 8) == 8
    assert staging.fused_expert_slot_count(8, 8) == 8
    assert staging.fused_expert_slot_count(23, 8) == 23


@pytest.mark.parametrize(
    ("active", "minimum", "maximum", "message"),
    [
        (0, 8, 128, "active_experts"),
        (1, 0, 128, "minimum_fused_slots"),
        (1, 8, 4, "exceed workspace"),
    ],
)
def test_fused_expert_slot_count_fails_closed(active, minimum, maximum, message):
    staging = _module()

    with pytest.raises(ValueError, match=message):
        staging.fused_expert_slot_count(active, minimum, max_experts=maximum)


def test_capacity_copy_launch_shape_is_bounded_by_ring_capacity():
    staging = _module()

    assert staging.capacity_copy_launch_shape(1, ring_capacity=64) == (1, 256)
    assert staging.capacity_copy_launch_shape(1 << 30, ring_capacity=64) == (4, 256)
    assert staging.capacity_copy_launch_shape(1 << 30, ring_capacity=4096) == (
        256,
        256,
    )
    assert staging.capacity_copy_launch_shape(1 << 30, ring_capacity=2) == (1, 32)


@pytest.mark.parametrize("ring_capacity", [2, 4, 8, 64, 4096])
def test_capacity_copy_launch_shape_reserves_half_the_ring(ring_capacity):
    staging = _module()
    blocks, threads = staging.capacity_copy_launch_shape(
        1 << 30, ring_capacity=ring_capacity
    )

    active_warps = blocks * (threads // staging.CUDA_WARP_SIZE)
    assert active_warps <= max(1, ring_capacity // 2)


@pytest.mark.parametrize("ring_capacity", [0, 1, 3, 4097])
def test_capacity_copy_launch_shape_rejects_invalid_ring_capacity(ring_capacity):
    staging = _module()

    with pytest.raises(ValueError, match="power of two"):
        staging.capacity_copy_launch_shape(1024, ring_capacity=ring_capacity)


def test_capacity_copy_launch_shape_reads_and_validates_environment(monkeypatch):
    staging = _module()
    monkeypatch.setenv("HBFSIM_CAPACITY_RING_CAPACITY", "not-an-integer")

    with pytest.raises(ValueError, match="invalid HBFSIM_CAPACITY_RING_CAPACITY"):
        staging.capacity_copy_launch_shape(1024)


def test_compare_staged_bytes_is_exact_and_reports_page_differences():
    staging = _module()
    expected = bytes(range(32))

    passed = staging.compare_staged_bytes(expected, expected, page_bytes=8)
    assert passed["status"] == "PASS"
    assert passed["difference_count"] == 0
    assert passed["actual_sha256"] == passed["expected_sha256"]

    actual = bytearray(expected)
    actual[1] ^= 0xFF
    actual[17] ^= 0xFF
    failed = staging.compare_staged_bytes(bytes(actual), expected, page_bytes=8)
    assert failed["status"] == "FAIL"
    assert failed["difference_count"] == 2
    assert failed["first_difference"] == 1
    assert failed["first_differences"] == [1, 17]
    assert failed["differing_pages"] == [0, 2]


@pytest.mark.parametrize(
    ("actual", "expected", "message"),
    [(b"abc", b"ab", "equal length"), (b"ab", b"ab", "at least three")],
)
def test_compare_staged_bytes_rejects_invalid_inputs(actual, expected, message):
    staging = _module()

    with pytest.raises(ValueError, match=message):
        staging.compare_staged_bytes(actual, expected)


def test_duplicate_inflight_and_use_before_ready_are_rejected():
    staging = _module()
    state = staging.StageStateMachine()
    ticket = state.begin(0, [1, 2])
    try:
        state.begin(1, [3])
    except RuntimeError as exc:
        assert "in-flight" in str(exc)
    else:
        raise AssertionError("duplicate in-flight stage was accepted")
    try:
        state.require_ready(ticket)
    except RuntimeError as exc:
        assert "use-before-completion" in str(exc)
    else:
        raise AssertionError("use-before-ready was accepted")


def test_stale_generation_is_rejected():
    staging = _module()
    state = staging.StageStateMachine()
    first = state.begin(0, [1])
    state.mark_ready(first)
    state.complete(first)
    second = state.begin(1, [2])
    try:
        state.mark_ready(first)
    except RuntimeError as exc:
        assert "stale" in str(exc)
    else:
        raise AssertionError("stale generation was accepted")
    state.abort(second)


def test_abort_poison_prevents_workspace_reuse():
    staging = _module()
    state = staging.StageStateMachine()
    ticket = state.begin(0, [1])
    state.abort(ticket, poison=True)
    assert state.state == "poisoned"
    with pytest.raises(RuntimeError, match="poisoned"):
        state.begin(1, [2])


def test_cuda_launch_resolves_through_explicit_gate(monkeypatch):
    staging = _module()

    class FakeLaunch:
        argtypes = None
        restype = None

    class FakeGate:
        cuLaunchKernel = FakeLaunch()

    gate = FakeGate()
    monkeypatch.setattr(staging, "get_launch_gate_library", lambda: gate)
    launch = staging.CudaDriverKernel._interposed_launch_kernel()

    assert launch is gate.cuLaunchKernel
    assert launch.restype is staging.ctypes.c_int
    assert len(launch.argtypes) == 11


def test_cuda_launch_fails_closed_without_explicit_gate_symbol(monkeypatch):
    staging = _module()
    monkeypatch.setattr(staging, "get_launch_gate_library", lambda: object())

    with pytest.raises(RuntimeError, match="explicit HBFSim launch gate"):
        staging.CudaDriverKernel._interposed_launch_kernel()


def test_static_ptx_loader_resolves_through_explicit_gate(monkeypatch):
    staging = _module()

    class FakeCall:
        argtypes = None
        restype = None

    class FakeGate:
        hbfsim_begin_module_load_from_ptx = FakeCall()
        hbfsim_end_module_load = FakeCall()
        cuModuleLoadDataEx = FakeCall()

    gate = FakeGate()
    monkeypatch.setattr(staging, "get_launch_gate_library", lambda: gate)
    begin, end, load = staging.CudaDriverKernel._interposed_module_loader()

    assert begin is gate.hbfsim_begin_module_load_from_ptx
    assert end is gate.hbfsim_end_module_load
    assert load is gate.cuModuleLoadDataEx
    assert begin.restype is staging.ctypes.c_uint64
    assert end.restype is None
    assert load.restype is staging.ctypes.c_int
    assert len(load.argtypes) == 5


def test_static_ptx_loader_fails_closed_without_provenance_gate(monkeypatch):
    staging = _module()
    monkeypatch.setattr(staging, "get_launch_gate_library", lambda: object())

    with pytest.raises(RuntimeError, match="missing module provenance symbols"):
        staging.CudaDriverKernel._interposed_module_loader()


def test_stage_lease_remaps_on_host_before_returning_tensor():
    torch = pytest.importorskip("torch")
    staging = _module()
    ticket = staging.StageTicket(1, 0, (65, 98, 45))
    owner = types.SimpleNamespace(
        state=types.SimpleNamespace(require_ready=lambda observed: None)
    )
    lease = staging.StageLease(
        owner,
        ticket,
        None,
        None,
        staging.build_remap(ticket.experts),
        3,
    )
    topk = torch.tensor([[65, 45, 98], [98, 65, 45]], dtype=torch.int32)

    remapped = lease.remap(topk, device="cpu")

    assert remapped.dtype == torch.int32
    assert remapped.tolist() == [[0, 2, 1], [1, 0, 2]]


def test_stage_lease_rejects_unstaged_id_before_device_copy():
    torch = pytest.importorskip("torch")
    staging = _module()
    ticket = staging.StageTicket(1, 0, (65, 98))
    owner = types.SimpleNamespace(
        state=types.SimpleNamespace(require_ready=lambda observed: None)
    )
    lease = staging.StageLease(
        owner,
        ticket,
        None,
        None,
        staging.build_remap(ticket.experts),
        2,
    )

    with pytest.raises(RuntimeError, match="unstaged expert"):
        lease.remap(torch.tensor([[65, 45]], dtype=torch.int32), device="cpu")


def test_capacity_generation_has_one_final_completion_and_service_barrier():
    staging = _module()
    source = textwrap.dedent(inspect.getsource(staging.CapacityStager.stage))

    inner_loop = source.index(
        "for tensor, destination_tensor, destination_base, destination_offset in copies:"
    )
    launch = source.index("self._kernel.copy(", inner_loop)
    evidence_append = source.index("verification_inputs.append(", launch)
    event_record = source.index("copy_completion.record(stream)", evidence_append)
    event_wait = source.index("copy_completion.synchronize()", event_record)
    service_flush = source.index("runtime.flush()", event_wait)
    verification = source.index("_verify_tensor_copy(", service_flush)

    assert inner_loop < launch < evidence_append < event_record < event_wait
    assert event_wait < service_flush < verification
    assert source.count("copy_completion.record(stream)") == 1
    assert source.count("copy_completion.synchronize()") == 1
    assert "capacity staging tensor failed:" in source
    assert "capacity staging generation completion failed:" in source
