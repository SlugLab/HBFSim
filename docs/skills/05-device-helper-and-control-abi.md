# Device helper and control ABI

## Purpose

Identify the shared layouts, publication protocol and load-generation binding that must change together when device helpers change.

## Scope

B uses control ABI **4**. The launch-gate API versions **2/3** are a separate host interface; neither is the shared control ABI. S's control ABI 9 and `DeviceFuture` are not installed here. Snapshot and evidence convention: [reading order](00-reading-order.md).

## Key concepts

The parent creates a sealed memfd, maps it on CPU and registers a GPU alias. The daemon maps the same bytes. Loaded helper modules receive an alias plus generation, scoped to CUDA context/device ownership. Rings use sequence numbers and exact completion identity; a capacity handoff reuses a page-entry layout with explicitly encoded media-plan fields.

## Important files

[Host shared layout](../../src/host_service/control_layout.hpp), [device shared layout](../../src/cuda_runtime/device/hbf_device.cuh), [wire records](../../include/hbfsim/protocol.hpp), [launch-gate ABI](../../include/hbfsim/launch_gate_abi.hpp), [context](../../src/cuda_runtime/context.cpp), [timing binding](../../src/cuda_runtime/timing_binding.cpp), [module identity](../../src/cuda_runtime/module_identity.cpp), [launch interception](../../src/cuda_runtime/launch_gate.cpp), and [embed step](../../cmake/EmbedDevicePtx.cmake).

## Important structs/classes/functions

| Object/function | B responsibility |
|---|---|
| `SharedControlHeader` | 384-byte header: geometry, counters, liveness, generation, timing profile and empirical curve |
| `SharedRangeRecord` | 64-byte registered range and synthetic media interval metadata |
| `HbfRequest`, `HbfCompletion`, `PageEntry` | Each 64 bytes; request, terminal result and page/handoff state |
| `SharedRequestSlot`, `SharedCompletionSlot` | Each 128 bytes, including sequence publication |
| `ResolveResult` | 16-byte address/status/reserved return consumed by injected PTX |
| `ControlView::initialize` / `valid` | Construct and validate offsets, sizes, magic, ABI and ring geometry |
| `ControlView::try_push_request` / `try_pop_request` / `try_publish_completion` | Host-side sequence-ring operations |
| `ControlView::begin_capacity_handoff` / `complete_capacity_handoff` | Exact request/ticket handoff and terminal publication |
| `TimingBindingRegistry::activate` / `ready` / `invalidate` | Bind and retire owner alias/generation in the correct CUDA domain |
| `ModuleLoadTransactionStore::begin` / `take` / `end` | Scoped one-shot PTX identity transfer to a successful module load |
| `initialize_module_control` | Write or clear loaded module helper globals for the active owner |

## Call path / data path

`create_context` → profile validation → control allocation/initialization → CUDA alias and launch-gate activation → daemon startup/heartbeat → registration. The trusted module-load path associates exact PTX identity and initializes `__hbfsim_control` and `__hbfsim_control_generation`. The GPU publishes a request body before its slot sequence; the daemon consumes that sequence, dispatches work and publishes a completion body before the matching completion sequence. The GPU reads the exact completion and releases the slot for a later ring generation.

## CPU-side vs GPU-side execution context

Host fields use atomic operations in `ControlView`; the device mirrors layout in a CUDA-compatible header and uses system-scope acquire/release. Struct equality is checked by host compilation, but actual cross-device visibility needs live validation. Helper PTX is generated at build time, linked into the transformer's data, and injected into the workload module.

## Invariants

Both headers must agree on every size/offset, status and field meaning. B's ring capacity is a power of two in [2,4096]; the shared range table capacity is 32,768. The capacity backing router separately limits active routing entries to 64; do not conflate those capacities. The helper validates magic, ABI, header size, offsets, region size and generation before dereferencing range records. Request IDs and capacity tickets must not be silently reused on exhaustion. Terminal completions are published before a global fault closes admission, preserving exact failure status.

## Supported behavior

Bounded request/completion admission, liveness/deadline checking, range publication, timing-backed versus capacity-unbacked gate policies, transactional module identity, quarantine on unsafe retirement failure, scalar and six-point empirical fast timing metadata. Unsupported ABI/layout/generation is a failure, not a compatibility downgrade.

## Explicitly unsupported behavior

Mixing separately built helper/control versions, treating the reserved marker symbol alone as trusted module identity, or binding by kernel name when multiple PTX variants exist. No future or TensorMap control records are present in B. `ControlHeader` in the generic protocol header is not interchangeable with the larger runtime `SharedControlHeader`.

## Common failure modes

Editing only the CPU header; forgetting regenerated embedded PTX; copying a new helper into an old module; stale CUDA handles after unload/context destruction; interpreting launch-gate v3 as control ABI 3; or allowing a fault/timeout to be converted into a successful address. CPU source layout consistency does not prove that a loaded helper binary came from that source.

## Tests proving the behavior

Existing [device helper ABI](../../tests/cpu/device_helper_abi_test.cpp), [protocol layout](../../tests/cpu/protocol_layout_test.cpp), [capacity handoff](../../tests/cpu/capacity_handoff_test.cpp), [module identity](../../tests/cpu/module_identity_test.cpp), [timing binding](../../tests/cpu/timing_binding_test.cpp), [daemon protocol](../../tests/integration/daemon_protocol_test.cpp), [module association](../../tests/integration/test_cuda_module_association.py), [lookup interposition](../../tests/integration/test_cuda_lookup_interposition.py), and [helper PTX](../../tests/integration/test_device_helper_ptx.py) tests cover separate boundaries. This document records source/layout checks, not new test execution or GPU success.

## What not to change casually

ABI constants, alignment, field ordering, status enum values, publication order, generation ownership, retirement order and exact helper embedding. A selective async port must update host, device, context, loader, pointer registry and transform return layout atomically, then add wrong-version and stale-generation negative tests.

## Related docs

[Memory semantics](03-cuda-memory-semantics.md), [async/TMA](04-cuda-async-cpasync-tma.md), [capacity translation](07-capacity-address-translation.md), [historical final hardening](../superpowers/plans/2026-08-10-task6-final-hardening.md), and [vmem ABI-v4 design](../superpowers/specs/2026-08-11-cd8p-vmem-tuning-design.md).
