# CUDA memory semantics in HBFSim

## Purpose

Explain the memory guarantees actually implemented at registration, resolver and completion boundaries, and identify where a CUDA semantic claim needs additional evidence.

## Scope

Audited base B, as defined in [reading order](00-reading-order.md). The scope is the supported, explicitly registered access subset and its control protocol. It is not a replacement CUDA memory model or proof for arbitrary concurrent kernels.

## Key concepts

Native data access, modeled media work and shared-control publication are separate operations. Timing mode preserves the original device address. Capacity mode returns a frame address after page preparation and media completion. Acquire/release on the control protocol establishes visibility of request/completion records; it does not model NVIDIA scoreboards or make an arbitrary data race valid.

## Important files

[Device resolver](../../src/cuda_runtime/device/hbf_device.cu), [device pure helpers/layout](../../src/cuda_runtime/device/hbf_device.cuh), [host control](../../src/host_service/control_layout.hpp), [range table](../../src/cuda_runtime/range_table.cpp), [launch gate](../../src/cuda_runtime/launch_gate.cpp), [coverage policy](../../src/cuda_runtime/coverage.cpp), and [context lifecycle](../../src/cuda_runtime/context.cpp).

## Important structs/classes/functions

`validate_device_range_with_cuda` checks the pointer's CUDA context, device, allocation bounds, device-memory type and non-managed status. `LaunchRangeSynchronizer` coordinates registration and launch enqueue. `access_supported` checks operation, permission, overflow, range bounds and a single-page access. `media_descriptor` rounds the operation's address to the modeled page. `system_acquire`/`system_release`, `reserve_request` and `wait_for_completion` implement GPU control visibility and exact slot reuse. `CoverageGate::check_launch` distinguishes backed timing pointers from unsafe unbacked capacity pointers.

## Call path / data path

The CPU validates the actual allocation, publishes the range through the launch gate, and binds the module's control alias. The GPU checks generation and range, derives the media page, elects a leader among active lanes accessing the same range/page, and obtains a terminal result. `resolved_address` returns either the original timing pointer or frame plus page offset; the rewritten native opcode then executes. A failed status reaches `__hbfsim_fault` before the access.

For reference completion, the GPU observes and copies the published slot, releases its sequence for reuse and increments the completion counter, then validates the copied request ID and status. On success it waits until its GPU-local arrival plus scaled modeled duration. Host publication that arrives late cannot be undone by the model deadline.

## CPU-side vs GPU-side execution context

The host uses atomic references in shared control memory; device accesses use CUDA system-scope atomics. The CPU ordering reference test runs CPU threads, despite its name. Device `%globaltimer` measures the GPU deadline; host heartbeat values are observed for *change*, with elapsed heartbeat staleness measured in GPU time, not by subtracting host and GPU timestamps.

## Invariants

- Registered timing ranges must be physically backed, in the exact current CUDA domain and inside the allocation.
- Supported rewritten accesses cannot cross a range or modeled page boundary and must satisfy permissions.
- Request and completion slots require matching sequence/ticket identity before reuse.
- A zero module control alias permits ordinary device-memory work only because the launch gate is responsible for refusing unsafe registered consumers.
- Registration after the first admitted launch is restricted by the existing synchronization contract; do not mutate ranges outside it.
- Capacity failure/retirement cannot expose an unbacked pointer to opaque native execution.

## Supported behavior

The original supported load/store opcode, including its predicate and accepted qualifiers, remains in PTX with a new address operand. Timing-backed opaque/unsupported consumers may be admitted under the existing v3 range policy and reported `opaque_unmodeled_timing`; this preserves native backing safety and supplies no modeled-coverage proof. Capacity-unbacked and legacy-strict policies retain rejection. Pure CPU layout and ordering tests can check their corresponding protocol operations.

## Explicitly unsupported behavior

No assertion of exact ordinary-load issue/use overlap, full acquire/release/atomic CUDA semantics across all possible PTX forms, or cycle-accurate GPU scheduling. Global atomic/reduction operations are outside B's modeled access subset. Managed memory and host pointers are not valid timing registrations. Concurrent direct host modification of an active writable backing extent is not supported by the capacity persistence contract.

## Common failure modes

Calling early software completion “native data ready”; calling an opaque allowed launch “modeled”; treating instruction/page coalescing as cross-warp cache residency; or inferring a no-race guarantee from the page service mutex. The mutex protects service state; it is not a general proof of application memory ordering or concurrent frame lifetime for every CUDA workload. Keep claims within tested access/lifecycle shapes.

## Tests proving the behavior

Existing [device ABI/math tests](../../tests/cpu/device_helper_abi_test.cpp), [CPU ordering reference](../../tests/cpu/device_ordering_reference_test.cpp), [range tests](../../tests/cpu/range_table_test.cpp), [coverage gate](../../tests/cpu/coverage_gate_test.cpp), [timing binding](../../tests/cpu/timing_binding_test.cpp), [device range validation](../../tests/integration/device_range_validation_test.cpp), and [public lifecycle](../../tests/integration/public_cuda_lifecycle_test.cpp) cover bounded pieces. Fake-driver and CPU PASS are not a real-GPU memory-ordering proof. No test was rerun for this document.

## What not to change casually

System-scope ordering, completion-before-fault publication, exact generation checks, range permissions, launch/registration locks and retirement quarantine. Do not claim that source-level placement alone fixes native scheduling: EQ2 additionally requires optimized final-cubin mapping and correctness checks.

## Related docs

[PTX instrumentation](02-ptx-instrumentation.md), [async/TMA](04-cuda-async-cpasync-tma.md), [ABI](05-device-helper-and-control-abi.md), [capacity](07-capacity-address-translation.md), and [hardware timing contract](../49-eval-audit/hardware-groundtruth-contract.md).
