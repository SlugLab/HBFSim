# MQSim online service

## Purpose

Explain how host requests enter the media model, how admission and completion are bounded, and which timing quantities the adapter actually represents.

## Scope

B's media-only adapter, daemon dispatch and standalone benchmark. It does not include a physical SSD collector, generic fio-arrival importer, complete SSD host stack or concurrent decode timeline. Snapshot convention: [reading order](00-reading-order.md).

## Key concepts

`MqsimOnlineEngine` is an incremental event engine. Submitted requests may be staged, scheduled, waiting for device admission, in the device, or completed but not yet returned. Configured queue depth limits device admission; it is not identical to all pending requests. A bandwidth cursor bounds reported completion time; that bound alone does not prove corresponding resources remain occupied until the adjusted deadline.

## Important files

[Public engine interface](../../include/hbfsim/mqsim_online.hpp), [adapter implementation](../../src/mqsim_adapter/mqsim_online.cpp), [daemon main](../../src/host_service/main.cpp), [dispatcher](../../src/host_service/request_dispatcher.cpp), [profile parser](../../src/profile/profile.cpp), [patched build](../../cmake/MQSimPatchedBuild.cmake), [MQSim patch](../../patches/mqsim/0001-online-hbf-api.patch), [media benchmark](../../benchmarks/mqsim/hbf_mqsim_bench.cpp), and [serial replay](../../benchmarks/replay/hbf_trace_timing.cpp).

## Important structs/classes/functions

- `configure_mqsim` configures the HBF host interface, NAND timings, mapping, channel transfer and die/plane geometry; chip-per-channel is one and preconditioning is disabled in this path.
- `MqsimOnlineEngine::submit` validates nonempty 512-byte-aligned requests, capacity and modeled arrival order, then stages a descriptor.
- `Impl::flush_staged` orders staged arrivals by arrival time and sequence and registers events.
- `Impl::submit_to_device` / `dispatch_to_device` enforce admission, submit to the HBF interface and create completions.
- `Impl::release_admission_slots` schedules queued work at the current simulation time without re-entering request segmentation inside a completion callback.
- `run_next_completion` advances events until a completion exists; `pending` counts unreturned requests; `current_time_ns` exposes the model clock.
- `RequestDispatcher::poll_once` preserves the original ticket while assigning distinct internal engine IDs and accumulating required media actions.

## Call path / data path

GPU request → daemon `prepare_host_dispatch` → optional parent capacity handoff → zero/one/two media actions → `MqsimOnlineEngine::submit` → arrival injector → bounded admission → MQSim flash events → completion callback → dispatcher → shared completion → GPU wait.

A timing request normally contributes one media action. A capacity hit contributes none; a clean miss contributes a read; a dirty eviction contributes a victim program followed by the requested read. `prepare_capacity_media_dispatch` preserves the victim's range ID, which can differ from the demand range. Physical backing/copy preparation occurs before these modeled actions are submitted in B.

## CPU-side vs GPU-side execution context

MQSim, daemon dispatch and offline benchmarks run on CPU. The parent capacity worker separately performs file/CUDA copies. The GPU receives modeled duration and waits on its own clock. The daemon raises a request's submitted arrival to at least `engine.current_time_ns()`; do not silently compare that scheduler input with an unrelated host wall timestamp.

## Invariants

Admission never counts a re-admitted request twice. Requests preserve bytes, address, original identity and terminal status through internal action mapping. A dirty program precedes its dependent read; completion is not published until all required actions finish. `nullopt` from the dispatch engine's next-completion function with work outstanding is terminal, not permission to poll forever. `configure_mqsim` and the profile parser remain the only shared model configuration path.

## Supported behavior

Incremental online media modeling, bounded device QD, configured NAND/channel/die/plane geometry, synthetic arrivals, trace/online comparison, and a reported aggregate-bandwidth completion lower bound. `run_mqsim_trace` reads its explicit trace format. Its historical trace operation convention is 0=write/1=read; `RequestOperation` is 0=read/1=write, so the reader deliberately converts it.

## Explicitly unsupported behavior

Media-only output is not whole-SSD NVMe/PCIe/software latency or physical HBF ground truth. The current adapter uses global MQSim simulator/configuration state; do not assume multiple independent concurrent engine instances are isolated within one process. There is no general fio importer or complete device preconditioning equivalence. The bandwidth completion ceiling is not evidence of a modeled interface occupancy queue.

## Common failure modes

Dropping the QD-admission fix; confusing pending count with achieved in-device depth; reversing trace read/write encoding; using a closed-loop QD workload as if it had the same arrivals as an open-loop replay; interpreting whole-SSD latency as NAND tR; claiming channel×die×plane count is measured independent-sense hardware; or calling serial page-latency sum live decode time.

## Tests proving the behavior

Existing [online/trace equivalence](../../tests/integration/mqsim_online_test.cpp), [QD admission](../../tests/integration/mqsim_queue_depth_test.cpp), [benchmark interface](../../tests/integration/test_mqsim_benchmark.py), [capacity dispatch](../../tests/cpu/capacity_dispatch_test.cpp), [daemon protocol](../../tests/integration/daemon_protocol_test.cpp), and [trace timing](../../tests/integration/test_trace_timing.py) tests establish their fixture behavior when executed. GOLD-0 records current CPU outcomes separately; no SSD/GPU acquisition was performed for this document.

## What not to change casually

Admission accounting, non-reentrant re-admission, exact request-ID maps, operation encoding and profile units. The build applies the pinned patch to a build-local MQSim copy; do not edit the dependency checkout or implement a second timing engine to accommodate an experiment. Keep interface ceilings and sustainable completion rate as separate concepts.

## Related docs

[Architecture](01-hbfsim-architecture.md), [capacity](07-capacity-address-translation.md), [evaluation protocol](09-evaluation-protocol.md), [hardware/storage contract](../49-eval-audit/hardware-groundtruth-contract.md), and [source ledger B10–B13](../49-eval-audit/source-ledger.md).

Phase-two observation support is explicitly enabled with
`MqsimOnlineEngine::enable_observations` before submission. The default emits
no diagnostic events. `take_observations` drains arrival, device-admission and
media-completion events without modifying `HbfCompletion` or scheduling.
Admission means handoff to the MQSim HBF interface, not NAND command start.
Completion observations keep the raw callback time and bandwidth-bounded
reported completion separate. QD counts held admission slots, including slots
reserved for non-reentrant re-admission. Callers drain diagnostics after each
returned completion; this is CPU evidence, not physical SSD acquisition.

The independent evaluation executable `hbf_concurrent_trace_timing` stages all
fixed arrivals before advancing events. Its separate `closed_loop_qd` mode
replenishes a completed software slot at the reported completion time; it does
not pretend the two policies have identical actual arrivals. Per-request output
keeps admission queue delay, media-service span, interface-bound delay and
consume residual separate, with held-slot QD duration accounting. Resource
selection stays in the shared profile/address mapper. Unmapped N requires a
separate `PROJECTED_ANALYTICAL` model; this tool does not fabricate that point.
These media replay results remain PROJECTED and are not a causal decode DAG,
physical SSD measurement, calibrated model claim or live-serving trace.

`run_next_completion_until(deadline_ns)` is a separate clock-coordination API:
it returns a completion only when the reported deadline has arrived, or stops
exactly at the caller's horizon with no completion. A null result can retain
pending work. Optional no-op MQSim markers let external compute events issue
new requests before the next media completion; they do not create I/O. After
using this API, an empty legacy poll is a no-op so cancelled marker timestamps
cannot move the idle clock. Legacy-only callers retain the original behavior.
The [horizon tests](../../tests/integration/mqsim_horizon_test.cpp) cover bounded
advance, mixed API use, bandwidth readiness and existing service parity.
