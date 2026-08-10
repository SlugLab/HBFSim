# HBFSim Hybrid Live Emulator Design

Date: 2026-08-09  
Branch: `hybrid`

## 1. Purpose

HBFSim is a live High-Bandwidth Flash (HBF) workload emulator for NVIDIA GPUs. It uses bpftime's current GPU PTX transformation pipeline to identify and rewrite memory operations that target explicitly registered HBF address ranges. It uses an adapted MQSim model as the detailed reference for flash channel, die, queue, and NAND timing. It supports both:

1. timing emulation, where bytes remain physically resident in HBM but HBF delay and contention are injected; and
2. capacity emulation, where logical HBF allocations can exceed VRAM and their backing bytes are paged through a bounded HBM cache.

The first workload suite contains deterministic CUDA microbenchmarks, TinyLlama through llama.cpp, and TinyLlama through vLLM. The primary validation machine is an NVIDIA RTX PRO 6000 Blackwell Server Edition with compute capability 12.0, 97,887 MiB reported memory, driver 595.84, and CUDA 12.8 or newer.

## 2. Goals and Non-Goals

### Goals

- Automatically rewrite supported PTX global loads and stores without source-level changes to workload kernels.
- Select HBF traffic through explicit registered address ranges.
- Inject live delay into the executing workload.
- Preserve detailed MQSim behavior in a reference path.
- Provide a GPU-local fast path and a hybrid sampling mode calibrated by MQSim.
- Emulate logical HBF capacity larger than physical VRAM through an HBM page cache.
- Keep results bit-exact relative to an uninstrumented baseline.
- Report modeled timing separately from emulator overhead.
- Fail closed whenever an HBF address could reach an uninstrumented or unsupported memory operation.

### Non-Goals

- Modeling GPU SM pipelines, instruction scheduling, or cache hierarchy cycle by cycle.
- Rewriting SASS in cubin-only kernels.
- Claiming that the included HBF profiles represent a finalized commercial standard.
- Providing CPU/GPU concurrent coherence for HBF backing files.
- Supporting HBF atomics, texture operations, or surface operations in the first implementation.
- Treating real host file I/O or host-to-device copy latency as physical HBF latency.

## 3. Dependency Boundary

HBFSim pins external dependencies instead of copying their source into first-party directories.

- bpftime is pinned initially to commit `ec26daecc8e787fb80fd95dd596a576404a5e36e` from 2026-08-05. This revision contains the CUDA attach implementation, JSON-driven PTX passes, fatbin extraction and repackaging, GPU maps, and the `kprobe_memcapture` pass used as the starting extension point.
- MQSim is pinned initially to commit `51f0f2d3fed92d88ef4a0fa61a38024b07bf9d16`. HBFSim adds an online request adapter while retaining its event engine and flash backend model.
- llama.cpp and vLLM are external workload dependencies. The tested revisions are recorded in every run manifest and in the repository's compatibility lock file after the first passing integration. They are not silently upgraded.

The HBF PTX pass is owned by HBFSim and built against bpftime's PTX-pass
interfaces. Upstream bpftime changes are kept minimal; any required bpftime
patch is stored as an explicit, reviewable patch and accompanied by an
upstreamable test. HBFSim never builds a locally dirtied bpftime submodule.
Its bpftime build helper copies the pinned source, applies the recorded patch,
and stamps the output with the pinned commit and patch digest. The launch
script refuses an unstamped or mismatched bpftime build.

## 4. Repository Layout

```text
HBFSim/
  cmake/
  configs/
    profiles/
    workloads/
  docs/
  include/hbfsim/
  src/
    ptxpass_hbf/
    cuda_runtime/
    host_service/
    mqsim_adapter/
    profile/
    reporting/
  adapters/
    llama_cpp/
    vllm/
  benchmarks/
    cuda/
    llama_cpp/
    vllm/
  tests/
    cpu/
    gpu/
    integration/
  third_party/
    bpftime/
    mqsim/
```

## 5. System Architecture

```text
llama.cpp / vLLM / CUDA microbenchmark
                  |
                  v
        bpftime CUDA interception
                  |
                  v
       HBFSim PTX transformation pass
       - preserve predicate and type
       - calculate effective address
       - route registered HBF accesses
                  |
                  v
          GPU HBF runtime helper
       +----------+----------------+
       |                           |
       v                           v
  HBM page hit             miss / reference sample
  translate address        shared request ring
       |                           |
       |                           v
       |                    host HBF service
       |                    online MQSim
       |                    backing-page copy
       |                           |
       +---------------------------+
                  |
                  v
      execute original load or store
```

The host service and GPU runtime communicate through fixed-size, pinned,
GPU-visible control memory. The runtime creates a sealable memfd, sizes it,
applies shrink, grow, and further-seal seals, maps it `MAP_SHARED`, and
registers those exact pages with
`cudaHostRegisterMapped | cudaHostRegisterPortable`; it then obtains the GPU
alias with `cudaHostGetDevicePointer`. The inherited memfd survives the daemon
`exec`, so both processes and the GPU observe the same ring pages. This uses
`cudaHostRegisterMapped`, rather than `cudaHostAllocMapped`, because memory
returned by `cudaHostAllocMapped` has no file descriptor that an exec'd daemon
can remap. Registration or device-pointer lookup failure is a hard
`HBFSIM_CUDA_ERROR`; production never falls back to an unregistered mapping.
The daemon accepts only a regular sealed memfd with the required seals, exact
size, magic, ABI version, capacity, and layout offsets.
It publishes the first heartbeat only after constructing the profile,
timing engine, and dispatcher, making that heartbeat the readiness boundary.
The context uses a 10-second startup allowance for this one-time work; request
deadlines remain independently controlled by `request_timeout_ns`.
Bulk page data is copied into preallocated HBM cache frames; the shared ring
carries only descriptors and completion state.

Request reservation also reserves the completion slot at the same ring index.
The reservation sequence is stamped into the request and is the completion
ticket; callers cannot supply or override it. A waiter consumes only the
completion matching its ticket, and the slot cannot be reused after wraparound
until that waiter consumes the terminal result. The daemon drains all request
descriptors currently visible in reservation order and submits the whole batch
to the timing engine before advancing modeled completions. This preserves
overlap and queue contention without requiring waiters to consume a global
completion stream in completion order.

## 6. Public Host API

The initial C ABI is intentionally small:

```c
struct hbfsim_context;
struct hbfsim_options;
struct hbfsim_range_options;

int hbfsim_context_create(const struct hbfsim_options *,
                          struct hbfsim_context **out);
int hbfsim_register_device(struct hbfsim_context *, void *device_ptr,
                           size_t length,
                           const struct hbfsim_range_options *);
int hbfsim_map_file(struct hbfsim_context *, const char *path,
                    uint64_t file_offset, size_t length,
                    const struct hbfsim_range_options *,
                    void **logical_device_ptr_out);
int hbfsim_flush(struct hbfsim_context *);
int hbfsim_unregister(struct hbfsim_context *, void *range_base);
void hbfsim_context_destroy(struct hbfsim_context *);
```

`hbfsim_register_device` enables timing emulation over an existing HBM allocation. `hbfsim_map_file` reserves an unbacked CUDA virtual-address interval and enables capacity emulation. Range registration is immutable while kernels are using the range. A context owns its range table, request ring, page directory, HBM frames, service process connection, and report.

Task 7 establishes context lifecycle and transport. The Task 8 host slice adds
timing-only `hbfsim_register_device`: it accepts only a non-managed device
allocation owned by the context's exact CUDA context/device, rejects overflow
or a range extending beyond that allocation, and publishes a validated record
through a gate-owned transaction. Capacity registration, unregister, and dirty
flush remain Task 9 work and fail closed until that backend exists.

The launch gate exposes a versioned v2 callback table with mode-neutral range
registration and explicit range removal. Context creation claims
one active timing owner and binds the mapped control alias plus a monotonic,
non-wrapping generation to trusted modules in the same CUDA context/device.
Trusted modules with missing or malformed control symbols remain identified but
explicitly unbound; only a launch carrying a registered HBF pointer through
such a module is rejected. A second concurrent owner, a foreign context/device,
or generation exhaustion fails closed. Clean destruction permits a later owner
with a higher generation.

The parent keeps the control memfd close-on-exec throughout daemon spawning.
The child requests `SIGKILL` if its parent dies, verifies that the expected
parent still exists, closes unrelated descriptors, and only then clears its
own close-on-exec flag immediately before `execve`. The daemon exec environment
preserves ordinary configuration while removing loader, bpftime, coverage,
and pass-instrumentation variables so application interposition does not leak
into the host service.

Context destruction keeps the daemon shutdown, `SIGTERM`, and `SIGKILL`
signaling schedule within five seconds and synchronously reaps a normally
schedulable child. A child stuck in kernel-uninterruptible sleep can delay the
final reap beyond that interval, so this is not a hard total destruction or
reap deadline. Public context operations enter through a shared admission
record before dereferencing the opaque context. Destruction closes admission,
rejects new operations, and waits for admitted operations to drain without
holding the admission mutex during teardown or deletion. A wrong-domain
attempt reopens admission for retry; successful teardown removes the record,
while permanent quarantine leaves it closed. For a timing owner, destruction
first verifies that the exact
owner CUDA context/device is current and samples daemon process and heartbeat
liveness. It then takes the gate's exclusive launch lock and marks the binding
retiring even when that sample failed, converting the failure into permanent
quarantine only after launches are quiesced. It rechecks liveness immediately
before synchronizing the device. It invalidates module bindings, requests
daemon shutdown and reaps it, and finally unregisters host control memory
before releasing gate ownership, clearing registered ranges, and unmapping. A
liveness, synchronization,
cleanup, or binding-write failure permanently retains the retiring
owner/generation, registered ranges, and mapping, but releases the synchronization
lock. Relevant launches can therefore acquire the shared guard and fail closed
promptly instead of hanging or bypassing the quarantined binding. These CUDA
operations have no cancellable deadline.

CUDA lifecycle interposition never treats an opaque green-context handle as a
`CUcontext`: it resolves the exact context with `cuCtxFromGreenCtx` first.
Owner activation, trusted module association changes, and every intercepted
context/device/runtime lifecycle transition share one recursive mutex held
across the active-owner check, underlying CUDA call, and exact cleanup.
Recursion permits a runtime reset to enter an interposed driver lifecycle API
on the same thread, while activation from another thread remains excluded.
Activation cannot claim a domain in the interval between a lifecycle precheck
and its cleanup: after taking the transition mutex it re-queries the driver and
requires the live current context/device to exactly match the supplied domain.
Explicit destruction/reset of the active owner context or device is rejected
before reaching CUDA; foreign-device primary-context operations may proceed and
discard only unbound records for that device. The production launch gate has no
direct range-add export; registered ranges enter only through
`hbfsim_register_device` and its owner-checked transaction.

## 7. PTX Transformation Contract

### 7.1 Supported operations

The first pass supports scalar and vector forms of `ld.global`, `ld.global.nc`, and `st.global` for integer, bit, and floating types from 8 through 128 aggregate bits. It supports register-indirect addresses with constant offsets. The same predicate guarding the original instruction guards the injected resolver call and the rewritten instruction.

The pass excludes its own helper functions and bpftime trampoline code to prevent recursive instrumentation. It allocates declared scratch registers or uses a callable helper with a documented PTX ABI. It does not reuse application registers without preserving them.

### 7.2 Rewriting behavior

Conceptually, each supported memory operation becomes:

```text
if original_predicate:
    translated_address, status = hbf_resolve(original_address,
                                             access_bytes,
                                             read_or_write)
    if status != READY:
        hbf_raise_fault(status)
    original_memory_instruction[translated_address]
```

For a non-HBF address, the resolver returns the original address. For timing-only ranges, it waits as required and also returns the original address. For capacity ranges, it returns an address inside the HBM page cache.

The transformer emits a machine-readable coverage manifest for every CUDA module. The manifest records kernel names, PTX targets, rewritten instruction counts, unsupported instruction counts, and modules for which only a cubin was available.

### 7.3 Unsupported operations

`atom.global`, `red.global`, texture, surface, generic-space accesses that cannot be resolved, inline SASS, and unrecognized address expressions are unsupported in an HBF range. They may execute for non-HBF allocations. If static analysis or runtime launch metadata shows that an unsupported or uninstrumented kernel can receive an HBF logical pointer, launch is rejected before execution.

All HBF ranges must be registered before the workload's first approved CUDA
launch. Range registration and launch enqueue share one synchronization
boundary: the gate records the first approved launch before dispatch while the
launch lock remains held, and every later registration attempt fails. This
keeps the range set inspected by the gate identical to the set visible when the
work is enqueued.

Module authorization uses host-side load provenance, not an embedded PTX symbol
alone. The pass writes the exact 256-bit identity into the transformed PTX but
does not publish a process-global expectation. On a module-cache miss, the
patched bpftime load site brackets its one direct `cuModuleLoadDataEx` call
with these optional gate callbacks:

```c
uint64_t hbfsim_begin_module_load_from_ptx(const char *ptx,
                                           size_t size);
void hbfsim_end_module_load(uint64_t token);
```

`hbfsim_begin_module_load_from_ptx` accepts only one canonical 32-byte
`__hbfsim_module_identity` declaration and installs the identity and a nonzero
token in thread-local state. Missing, malformed, duplicate, or nested input
returns zero; a nested begin attempt also cancels the older transaction so the
rejection cannot leave stale trust. bpftime calls `hbfsim_end_module_load`
unconditionally after the load. If both callbacks are absent, standalone
bpftime behavior is unchanged.
If only one callback is present, or if the begin callback is present but
returns zero, bpftime reports the provenance failure and does not execute that
module load.

The gate's `cuModuleLoadDataEx` wrapper atomically takes and clears the current
thread's transaction before it invokes the original driver function. It
associates the returned module only when the original load succeeds and the
identity read from the live module exactly matches the transaction. A missing
original, failed load, missing live identity, or mismatch cancels only that
load's transaction. `hbfsim_end_module_load` cancels a matching transaction
only if no interposed load consumed it. Therefore concurrent loads cannot
consume or cancel one another, including the case where load A fails while
load B succeeds, and no identity can remain pending for a later load.

Launch inspection resolves the `CUmodule` for the launched function and trusts
only the host-side association. A cubin that copies the reserved constant but
was not produced by a fresh trusted pass remains unassociated and is rejected
when it can receive an HBF pointer. A successful `cuModuleUnload` removes the
association; a failed unload leaves it intact. Successful context-destruction
or device-reset operations conservatively clear all module associations because
the first implementation does not index associations by CUDA context. The
covered driver calls are `cuCtxDestroy` and `_v2`, `cuCtxDetach`,
`cuDevicePrimaryCtxReset` and `_v2`, and
`cuDevicePrimaryCtxRelease` and `_v2`. The covered runtime call is
`cudaDeviceReset`; CUDA 12 also covers its deprecated equivalent
`cudaThreadExit`. CUDA 12.4 and newer cover `cuGreenCtxDestroy` because it
releases primary-context resources. Failed lifecycle calls retain associations.
Every driver spelling is substituted through both `cuGetProcAddress` ABIs and
all four `cudaGetDriverEntryPoint` ABIs so queried entry points cannot bypass
cleanup.

PTX compilation-cache hits still reach a module-cache miss and receive a fresh
transaction from the retained patched PTX. A bpftime `module_pool` hit issues no
load and creates no transaction; its already-live association remains valid.
After context destruction or reset, an old module-pool handle has no gate
association and therefore fails closed. A genuine later module reload must
complete a new exact transaction. This prevents stale authorization when CUDA
reuses a module handle while preserving bpftime's valid module-cache reuse for
the lifetime of a loaded context. Hostile code already executing inside the
same process could call the internal callbacks and is outside the threat model;
ordinary workload module bytes and cubin-only inputs cannot self-authorize.

## 8. Range Lookup and Warp Coalescing

The GPU-visible range table is a sorted immutable array of non-overlapping intervals. Each record includes base, length, range identifier, mode, access permissions, logical page size, and cache policy. The first implementation supports at least 64 simultaneously registered ranges.

The resolver uses warp voting to group active lanes that access the same logical HBF page. One elected lane performs the page-directory operation and request-ring submission. Other lanes wait on the same page entry. Requests from different warps are merged by an atomic state transition in the page directory.

The page state machine is:

```text
INVALID -> FETCHING -> VALID -> DIRTY -> WRITEBACK -> INVALID
```

Each entry contains a generation counter. A waiter accepts a completion only when both page number and generation match, preventing an ABA error after frame eviction and reuse.

## 9. Detailed and Fast Timing Models

### 9.1 Online MQSim reference model

MQSim gains an online API that accepts HBF requests and returns completion events:

```text
submit(request_id, arrival_ns, logical_page, bytes, operation, stream_id)
complete(request_id, modeled_completion_ns)
```

The adapter bypasses the NVMe/SATA host protocol and maps logical HBF pages directly across configured channels, chips, dies, planes, and pages. MQSim's event engine, transaction scheduling, flash timing, and contention remain active. A single deterministic service thread owns MQSim state.

Each explicit timing range owns a disjoint page-aligned interval in the
profile's synthetic media address space. Registration fails before publication
when the cumulative rounded extent exceeds `capacity_bytes`. The GPU submits
that media page address and the full profile page size to MQSim, never a raw
CUDA virtual address or a 1--16 byte instruction width. Until a split-request
path is implemented, one instruction that spans two modeled pages fails closed.

Request arrival order is determined by the shared-ring sequence number, while
GPU `globaltimer` deltas provide inter-arrival timing and bound device waits.
The device never subtracts a host `CLOCK_MONOTONIC` heartbeat timestamp from a
GPU timer because their epochs are not guaranteed to match. Instead it records
the last heartbeat value and the local GPU time at which that value changed;
an unchanged value beyond `heartbeat_timeout_ns` becomes `DaemonLost`.

### 9.2 GPU-local fast model

The fast model executes on the GPU and maintains bounded queue state per modeled channel. It applies the configured bandwidth cap, base media latency, queue-depth penalty, read/write distinction, and locality class. Its parameters are updated from reference samples grouped by operation, request size, queue-depth bucket, and locality bucket.

### 9.3 Hybrid policy

Hybrid is the default workload mode. It sends the first 1,024 HBF requests, the first request for every newly observed access class, and a configurable percentage of subsequent requests to the online reference model. The default steady-state sample rate is 1%. All other timing decisions use the GPU-local model.

Capacity misses always contact the host service to obtain bytes, but only sampled misses advance detailed MQSim state. Unsampled misses use the fast timing model while the host performs the backing-page copy.

## 10. Live Delay Injection and Timing Validity

A waiting warp polls a volatile completion word using bounded exponential backoff and GPU sleep instructions where available. The host publishes page data before completion status with system-scope release ordering; the GPU consumes status with system-scope acquire ordering.

For every request, HBFSim records:

- `modeled_ns`: latency computed by MQSim or the GPU-local model;
- `wall_ns`: observed live waiting time;
- `service_ns`: host queue, backing read, and CUDA copy time; and
- `overhead_ns`: wall time not explained by the modeled latency.

Reference validation defaults to a time scale of 100, so a 10 microsecond modeled request produces a 1 millisecond target wait. Workload fast/hybrid runs default to a time scale of 1. The actual values are always stored in the run manifest.

A request is timing-valid only if it is not released before its scaled modeled deadline and host service does not exceed that deadline by more than the configured tolerance. The default tolerance is the larger of 10% or 10 microseconds. Violations do not corrupt data; they mark the affected request and aggregate result as `emulator-overhead-limited`.

## 11. Capacity Emulation

`hbfsim_map_file` reserves an unbacked CUDA virtual-address range. Pointer arithmetic on this range is valid, but direct memory access is not. Consequently, capacity mode is enabled only after the coverage gate proves that every kernel receiving the logical pointer has been safely rewritten.

The host service uses the backing file as the authoritative byte store. It reads through pinned bounce buffers and copies pages into preallocated HBM cache frames. The frame is not published until the copy completes and its optional checksum matches. HBF media delay comes from the timing model, not from the physical file read.

Read-only ranges share clean cached pages. Writable microbenchmark ranges use write-back caching. A partial-page write miss performs a read-for-ownership. Dirty eviction submits a modeled program request and writes the page to the backing file. `hbfsim_flush` blocks until all dirty pages and modeled program operations complete. CPU modification of a registered backing range is prohibited until unregister or context destruction.

The default HBM page cache is 8 GiB on the primary 96 GiB-class validation GPU and is configurable. Cache replacement begins with deterministic clock eviction so repeated runs with the same request order remain reproducible.

## 12. HBF Profiles

All parameters are configurable. Three synthetic profiles ship as design-space points:

| Profile | Page | Read | Program | Channels | Dies/channel | Queue depth | Aggregate cap |
|---|---:|---:|---:|---:|---:|---:|---:|
| conservative | 16 KiB | 20 us | 200 us | 16 | 8 | 64 | 128 GB/s |
| nominal | 16 KiB | 10 us | 100 us | 32 | 8 | 128 | 512 GB/s |
| aggressive | 16 KiB | 5 us | 50 us | 64 | 8 | 256 | 1 TB/s |

Profiles also define capacity, planes per die, blocks per plane, pages per block, bus timing, cache size, eviction policy, sampling rate, time scale, and timing-validity tolerance. Schema validation rejects physically impossible or internally inconsistent values. These profiles are illustrative and are not labeled as vendor specifications.

## 13. Workload Adapters

### 13.1 CUDA microbenchmarks

The suite covers sequential, random, strided, pointer-chase, scalar, vector, predicated, aligned, unaligned, and mixed read/write traffic. Every benchmark has a baseline output and deterministic checksum.

### 13.2 llama.cpp

llama.cpp is built from source for the validation GPU while retaining PTX for compute capability 12.0. The adapter maps GGUF tensor-data regions as read-only HBF ranges. Metadata, temporary workspace, and KV cache remain in HBM by default. Timing mode registers normal device allocations; capacity mode exposes file-backed logical pointers only after the coverage gate passes.

### 13.3 vLLM

vLLM and its CUDA/Triton operators are built from source with PTX retained. HF/safetensors weight data is registered as read-only HBF. The validation configuration uses PTX-producing operators for every operation that consumes an HBF logical pointer. Cubin-only library kernels are permitted only when all of their arguments remain physically backed HBM addresses. A run is rejected if an HBF logical pointer could enter cuBLAS, cuBLASLt, a precompiled attention kernel, or another opaque library kernel.

TinyLlama 1.1B is the reproducible LLM workload. Each runtime is compared only with its own uninstrumented baseline. A larger user-supplied Llama model is supported through configuration but is not required for the initial correctness gate.

## 14. Error Handling

Each request has one of these terminal or non-terminal states:

```text
PENDING, READY, IO_ERROR, COPY_ERROR, CHECKSUM_ERROR,
TIMEOUT, UNSUPPORTED, DAEMON_LOST
```

The GPU wait path has a configurable deadline and observes a host heartbeat. Ring exhaustion applies controlled backpressure and cannot overwrite a live descriptor. If the daemon, MQSim adapter, file reader, CUDA copy, or checksum operation fails, the host writes a global fault word and completes all affected waiters with an error. The workload then exits through a controlled CUDA error path rather than spinning indefinitely.

For a dispatcher or timing-engine submit/completion failure, the daemon first
publishes terminal `IO_ERROR` completions to every accepted, outstanding, and
currently queued request's reserved completion ticket, then release-publishes
the global fault. Thus a waiter that observes the fault also observes its
terminal slot and no waiter depends on another request consuming a global
completion queue.
Admission and reservation quiescence share one lock-free state word: its high
bit closes admission and its remaining bits count reservations that have
passed the gate but have not release-published their request slot. Fault
handling atomically closes admission, waits for that count to reach zero, and
only then drains and fails the queued tickets. A producer paused before its
admission CAS is rejected after closure; one paused after reserving is included
in quiescence and receives its exact terminal completion.
`MqsimOnlineEngine::run_next_completion()` returning no value while requests
remain outstanding is such a terminal completion failure: it means no
simulator event can produce the missing result and must not be retried as a
transient condition.

Every failure report contains the kernel, module, transformed PTX instruction, operation type, logical address, logical page, range identifier, request identifier, page generation, MQSim time, host time, and terminal state.

## 15. Validation Plan

### 15.1 CPU-only tests

- Golden PTX rewrites for every supported type, width, vector form, predicate, and address expression.
- Rejection tests for unsupported memory operations and malformed PTX.
- Range-table, page-directory, generation-counter, eviction, and dirty-flush state-machine tests.
- Deterministic MQSim online submission and completion ordering.
- Profile schema and consistency checks.
- Backing-file short-read, permission, checksum, and writeback failure injection.

### 15.2 GPU microbenchmarks

Each access pattern runs in baseline, timing-only, reference, fast, hybrid, and capacity modes. Output must be bit-exact. Conservative, nominal, and aggressive profiles must produce monotonic latency for controlled single-request tests. Aggregate completed bytes may not exceed the configured bandwidth cap in modeled time.

Failure tests cover ring full, host daemon termination, request timeout, copy error, unsupported PTX, and coverage-gate rejection. No failure test may leave a kernel spinning or a dirty backing file reported as successfully flushed.

### 15.3 Capacity proof

The test creates a deterministic logical dataset larger than the GPU's reported VRAM and accesses pages across its full range through a deliberately smaller HBM cache. Sampled page hashes and a final reduction checksum must match a CPU reference before and after eviction and refetch.

### 15.4 LLM proof

For llama.cpp and vLLM separately, fixed prompt, fixed seed, greedy decoding, fixed token count, and fixed build configuration are used. Baseline and HBFSim token ID sequences must match exactly in timing and capacity modes. Each run preserves its coverage manifest, model hash, dependency commits, profile, request statistics, MQSim statistics, cache statistics, token IDs, and timing-validity summary.

## 16. Completion Criteria

The `hybrid` branch is complete only when all of the following are true:

- All CPU tests pass.
- All required GPU tests pass on the `sm_120` validation GPU.
- Timing-only and capacity modes have bit-exact microbenchmark evidence.
- The larger-than-VRAM capacity test passes.
- TinyLlama llama.cpp timing and capacity runs match their llama.cpp baseline.
- TinyLlama vLLM timing and capacity runs match their vLLM baseline.
- No HBF logical pointer enters an uninstrumented or unsupported kernel.
- Reports distinguish modeled time, wall time, service time, and emulator overhead.
- README commands reproduce build, microbenchmark, llama.cpp, and vLLM runs.
- Dependency revisions and workload/model hashes are recorded.
- The branch is pushed to `https://github.com/SlugLab/HBFSim` only after these gates pass.

Build success or CPU-only tests do not count as live GPU, capacity, timing, or LLM proof.
