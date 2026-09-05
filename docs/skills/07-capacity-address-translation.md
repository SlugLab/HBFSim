# Capacity address translation and persistence

## Purpose

Explain the three address spaces and the ownership/publication transactions that make logical capacity pointers usable by supported rewritten accesses.

## Scope

B's public file-backed capacity runtime and demand cache. This is functional address/paging emulation through bounded device-memory frames, not a claim about the physical organization of an HBF SRAM or HBM cache. Snapshot convention: [reading order](00-reading-order.md).

## Key concepts

| Address/identity | Owner and meaning |
|---|---|
| Logical CUDA address | `VmmRange::reserve_logical`; reserved virtual span, not payload-backed at that address |
| Synthetic media page | `RangeTable` assigns nonoverlapping page intervals; `SharedRangeRecord::file_offset` is this synthetic offset |
| Backing-file byte offset | `BackingStore` owns the physical file extent; different files can have the same local offset |
| Resident frame address | `VmmFramePool` allocates device memory; `HbmCache` maps synthetic page to frame |
| Resolved address | `resolved_address` adds the page-local byte offset to a successful frame address |

## Important files

[Public context functions](../../src/cuda_runtime/context.cpp), [capacity owner](../../src/cuda_runtime/capacity_runtime.cpp), [VMM](../../src/cuda_runtime/vmm.cpp), [range table](../../src/cuda_runtime/range_table.cpp), [CLOCK cache](../../src/cuda_runtime/hbm_cache.cpp), [router](../../src/host_service/capacity_backing_router.cpp), [backing store](../../src/host_service/backing_store.cpp), [page service](../../src/host_service/capacity_page_service.cpp), [worker](../../src/host_service/capacity_worker.cpp), and [dispatch](../../src/host_service/request_dispatcher.cpp).

## Important structs/classes/functions

`CapacityRuntime` owns one driver, frame pool, `HbmCache`, router, pinned bounce page, page service and worker per context. `CapacityMapping` owns a backing extent and reserved logical span. `RangeTable::add` assigns range/media identity through a publication callback. `CapacityBackingRouter::stage`/`activate`/`cancel`/`deactivate` control worker-visible routing. `CapacityPageService::resolve` prepares bytes and a media plan. `HbmCache::begin_eviction_locked` implements second-chance CLOCK with generation-checked rollback. `hbfsim_flush` and `hbfsim_unregister` manage checked persistence and retirement.

## Call path / data path

Map: validate mode/permissions/extent/current CUDA domain/daemon capability → open `BackingStore` → create/reuse capacity owner → reserve logical VMM span → stage router entry → launch-gate acknowledgment activates routing and publishes range → return logical pointer.

Access: `__hbfsim_resolve` → synthetic media descriptor → shared ring → daemon capacity handoff → parent `CapacityWorker::run` → `CapacityPageService::resolve`. On miss, select/reclaim a frame, write back a dirty victim if needed, read the requested backing page, copy it to the frame, and publish residency. The returned media plan drives zero/one/two MQSim actions before the original GPU completion. The native rewritten access then uses the resident frame.

Flush/unregister: retire affected launch access and verify liveness/domain → model and persist dirty pages under the existing transaction → deactivate routing/remove the range → release logical reservation only when safe. See implementation for retryable versus quarantine outcomes; failure is not permission to free unresolved state.

## CPU-side vs GPU-side execution context

Cache bookkeeping, router and file operations are CPU work in the parent application. The worker explicitly sets the owning CUDA context; frame copies use the owner's transfer stream and checked completion. The daemon computes media timing without owning application CUDA pointers. The GPU receives the final frame address. Cache hits still traverse the host handoff in B.

## Invariants

One shared frame budget per context; no new pool per mapping. Logical ranges must be covered before launch. Router intervals use synthetic media identity, never raw file offsets as cross-file keys. Copy-ready and modeled-ready must both hold before successful GPU completion. Hits generate zero modeled media actions; dirty eviction uses the victim page/range. Exact ticket/request completion prevents a late worker from publishing into a reused slot. Dirty state cannot be silently discarded on stop; successful persistence requires flush.

## Supported behavior

Read-only and read/write bounded file extents, unbacked logical address reservation, bounded physical frames, multi-file routing, partial final extents with bounded accesses, read-for-ownership on a write miss, write-hit dirty marking, CLOCK eviction and checked lifecycle rollback. The device path uses reference/host service for capacity even if the context's timing-model selection is fast or hybrid.

## Explicitly unsupported behavior

Opaque native consumers of capacity pointers, cross-page single resolver accesses, transparent arbitrary CUDA APIs on unbacked pointers, direct concurrent CPU editing of writable backing bytes, runtime lookahead prefetch, or profile-only SRAM-buffer emulation. `hbm_cache_bytes` is a software frame budget; it does not implement a physical SRAM tier. The timing-only empirical vmem curve does not calibrate this capacity path.

## Common failure modes

Overlapping local file offsets confused with duplicate global pages; cache size multiplied by mapping count; charging a media read on every hit or a program on every store; discarding dirty bytes after copy failure; treating the 32,768 range-table slots as 32,768 router mappings (the router is limited to 64); calling a 110 GiB sparse address proof physical 110 GiB residency; or interpreting host page-cache hits as SSD payload reads.

## Tests proving the behavior

Existing [VMM](../../tests/cpu/vmm_test.cpp), [CLOCK cache](../../tests/cpu/hbm_cache_test.cpp), [backing router](../../tests/cpu/capacity_backing_router_test.cpp), [page service](../../tests/cpu/capacity_page_service_test.cpp), [worker](../../tests/cpu/capacity_worker_test.cpp), [handoff](../../tests/cpu/capacity_handoff_test.cpp), [dispatch](../../tests/cpu/capacity_dispatch_test.cpp), [fake-driver capacity runtime](../../tests/integration/capacity_runtime_test.cpp), and [public lifecycle](../../tests/integration/public_cuda_lifecycle_test.cpp) tests cover component/failure transactions. [GPU smoke source](../../tests/gpu/capacity_runtime_live_test.cu) and [over-VRAM runner test](../../tests/integration/test_over_vram.py) do not themselves establish a new live run. This document executed no payload I/O or GPU test.

## What not to change casually

Ownership/destruction order, parent CUDA domain, one-pool budget, media-plan-before-model decision, generation-stamped handoff, exact dirty bytes and range retirement. New speculative work must share the demand media queue and close ticket, copy, eviction and cancellation accounting before a runtime benefit is claimed.

## Related docs

[Address-flow diagram](01-hbfsim-architecture.md#call-path--data-path), [MQSim](06-mqsim-online-service.md), [ABI](05-device-helper-and-control-abi.md), [historical capacity design](../superpowers/specs/2026-08-10-public-capacity-runtime-design.md), and [doc48 with base-status correction](../48-两种模式的定位与SRAM建模.md).
