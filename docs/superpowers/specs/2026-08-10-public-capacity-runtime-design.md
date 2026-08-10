# Public Capacity Runtime Design

Date: 2026-08-10
Branch: `hybrid`

## 1. Decision

One HBFSim context owns one bounded HBM cache and one parent worker. All
file-backed capacity mappings in that context share those resources. Requests
are routed by the synthetic media-page intervals already assigned by
`RangeTable`; backing-file offsets are never used as cross-file identities.

This preserves a single, profile-controlled VRAM budget while allowing up to
the existing 64 registered ranges to refer to different files and overlapping
file offsets.

## 2. Alternatives Rejected

- A worker and frame pool per mapping would multiply the configured cache
  budget and allow multiple workers to race for the same shared handoff.
- Restricting a context to one file mapping would simplify routing but add an
  undocumented limitation to the public API and block sharded model weights.

## 3. Ownership

`hbfsim_context` retains the validated `Profile` and may lazily create one
`CapacityRuntime`. The runtime declares and constructs, in ownership order:

1. the CUDA VMM driver;
2. one mapped `VmmFramePool` sized by `profile.hbm_cache_bytes`;
3. one `HbmCache` keyed by global synthetic media page;
4. a fixed-capacity mapping registry;
5. the routed page service and pinned page-sized bounce storage; and
6. the `CapacityWorker`, declared last so it stops and joins first.

Each registry entry owns a `BackingStore`, an unbacked `VmmRange`, permissions,
the logical length, the assigned range ID, and the half-open synthetic media
page interval. The registry converts a global media page into the mapping-local
page expected by `BackingStore`.

The worker makes the context's exact `CUcontext` current before advertising
readiness. Context construction or the first map fails closed if the worker
cannot establish that CUDA domain. Frame transfers use synchronous checked
driver copies through one pinned bounce page; serialization in the page
service makes one bounce page sufficient.

## 4. Map Transaction

`hbfsim_map_file` accepts only capacity mode and validates path, offset,
length, permissions, arithmetic and VMM geometry, current CUDA context/device,
profile geometry, and backing-file bounds. A non-page-multiple final extent is
valid; accesses remain bounded by the registered logical length. It then:

1. opens the bounded backing extent and reserves an unbacked VMM range;
2. lazily creates or reuses the context capacity runtime;
3. reserves a fixed registry slot without exposing it to the worker;
4. calls `RangeTable::add`, which supplies the assigned range ID and synthetic
   media interval to the transaction;
5. asks launch-gate ABI v2 to stage the range under the exclusive launch lock;
6. activates the registry slot and publishes the shared range from the gate's
   acknowledgement callback; and
7. returns the logical CUDA address only after both publications succeed.

Any failure before publication releases the VMM reservation, backing store,
and registry slot. Publication is the no-fail point: the worker-visible mapping
must exist before the device-visible range count is released.

## 5. Request, Cache, and Media Flow

The device helper already converts an address to a global synthetic media page.
For capacity ranges, the daemon must consult the parent page service before it
submits media work to MQSim. Modeling first would incorrectly charge a flash
read for an HBM-cache hit and incorrectly program flash for every store hit.

The daemon therefore creates the generation-stamped capacity handoff as its
prepare step. The single parent worker claims it and asks the routed page
service to resolve that global page. The response contains the terminal status,
frame address, and an explicit media plan:

- a read or write hit has no media action;
- a clean miss has one read action for the requested global page;
- a miss that evicts a dirty page has a program action for the victim followed
  by a read action for the requested page; and
- flush has one program action for each dirty page it persists.

The fixed 64-byte `PageEntry` ABI carries the plan without growing shared
memory: `logical_page` remains the requested page, `checksum` carries the
victim page when present, the low 32 bits of `reserved1` carry action flags,
and the high 32 bits carry the victim range ID. `reserved0` remains the
generation-tagged completion/status word. The requested read keeps the
original request's range ID; the victim program uses its separately returned
range ID, because a shared cache may evict a page from another mapping.

The daemon submits zero, one, or two internal media requests to the same timing
engine used by ordinary requests. Internal requests have collision-free IDs
derived from the original ticket and action index. A dirty-victim program must
complete before the dependent read is submitted. Other outstanding requests
remain free to interleave, so MQSim still observes channel, die, and queue
contention. The original GPU completion accumulates the constituent modeled
times and is published only after the final required media action and the
parent copy both succeed.

On a miss, the router selects the unique active mapping interval, performs the
checked backing I/O, and copies through the pinned bounce page. Physical file
and CUDA-copy time is reported as service/emulator overhead, not media time.
A write miss performs read-for-ownership and marks the frame dirty; a write hit
only marks the resident frame dirty. Completion uses only the exact
`(ticket, request_id)` CAS, so a daemon timeout or slot reuse cannot receive an
old frame.

Public capacity remains fail-closed until this prepare-before-model pipeline is
implemented. Enabling only read-only mappings early is also prohibited because
cache hits would still have incorrect timing under the old model-first order.

## 6. Flush and Unregister

`hbfsim_flush` first verifies daemon and CUDA-domain health, then serializes
with page resolution and flushes all dirty frames. Flush submits its program
actions through the same daemon timing engine and waits for their exact
completions before committing eviction. A successful return means the
frame-to-host copy, checked backing write, modeled program work, and `fdatasync`
all completed. Any failure leaves the dirty cache state retryable and returns
the corresponding public error.

`hbfsim_unregister` identifies the exact logical VMM base. Under launch-gate
ABI v2 it takes the exclusive launch mutation boundary and synchronizes the
owner device. Before the acknowledgement publishes range removal, it flushes
dirty pages belonging to the mapping and deactivates its router entry. It then
removes the `RangeTable` record and releases the logical VMM range and backing
store. Failure before the no-fail publication point leaves the mapping active.

Context destruction closes public-operation admission, verifies the exact CUDA
domain, retires launches, flushes capacity state, stops and joins the worker,
and only then destroys services, cache frames, VMM ranges, and the shared
control mapping. A liveness, flush, synchronization, or cleanup failure follows
the existing quarantine policy rather than freeing addresses that a kernel
could still reach.

## 7. Concurrency and Failure Rules

- Mapping registry lookup and mutation are synchronized; no worker can observe
  a partially constructed or removed entry.
- Global media intervals never overlap and are not reused within a context.
- Public map/unregister/flush operations participate in existing context
  admission and use the context process mutex for lifecycle mutation.
- Copy, I/O, unsupported permission, missing mapping, daemon loss, timeout, and
  CUDA-domain errors are terminal and fail closed.
- `stop()` and destruction do not silently discard dirty pages; persistence
  requires a successful flush or the context is quarantined.
- No direct CPU modification of an active writable backing extent is supported.

## 8. Verification Gates

Implementation proceeds in three reviewable slices:

1. **Routed service:** CPU tests cover two backing files with overlapping file
   offsets, global-page routing, shared eviction, wrong-range rejection,
   timeout/reuse, dirty rollback, and concurrent registry mutation safety.
2. **Capacity timing coordinator:** CPU/MQSim tests prove prepare-before-model,
   zero media work on hits, one read on clean miss, ordered program-then-read
   on dirty eviction, interleaving with unrelated requests, timeout, and exact
   modeled-time accumulation.
3. **Public lifecycle:** fake-CUDA VMM/context/copy support proves two mappings,
   transactional rollback, exact logical pointers, read-for-ownership, dirty
   eviction, retryable flush failure, unregister, context teardown quarantine,
   and launch-gate rejection. A small test profile avoids allocating the
   nominal 8 GiB cache.

Every slice must pass CPU, TSAN, CUDA static/PTX/fake-driver, and MQSim matrices.
Real GPU execution remains a separate proof gate and is prohibited on the
current host until the previously observed Xid 31 fault is externally cleared.

## 9. Completion Boundary

Public capacity is not complete merely because `hbfsim_map_file` returns a
pointer. Completion requires transactional multi-file routing, bounded shared
HBM residency, exact backing bytes after eviction, launch-safe unregister,
clean teardown, and the stated verification matrices. Live GPU, llama.cpp, and
vLLM claims require their separate proof runs.
