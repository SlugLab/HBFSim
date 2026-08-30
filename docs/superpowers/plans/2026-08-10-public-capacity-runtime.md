# Public Capacity Runtime Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement transactional multi-file `hbfsim_map_file`, `hbfsim_flush`, and `hbfsim_unregister` over one context-owned HBM cache, with cache-aware MQSim timing and fail-closed teardown.

**Architecture:** A synchronized backing router maps the global synthetic media pages assigned by `RangeTable` to per-file local pages. The daemon prepares capacity requests through the parent worker before submitting zero, one, or two dependent media actions to MQSim. A lazily created context runtime owns the VMM frame pool, cache, mappings, pinned bounce page, routed service, and worker.

**Tech Stack:** C++20, CUDA Driver/Runtime APIs, CUDA VMM, POSIX file I/O, CMake/CTest, fake `libcuda`, ThreadSanitizer, bpftime PTX static checks, MQSim online engine.

---

### Task 1: Routed Multi-File Backing Service

**Files:**
- Create: `src/host_service/capacity_backing_router.hpp`
- Create: `src/host_service/capacity_backing_router.cpp`
- Create: `tests/cpu/capacity_backing_router_test.cpp`
- Modify: `src/cuda_runtime/hbm_cache.hpp`
- Modify: `src/cuda_runtime/hbm_cache.cpp`
- Modify: `tests/cpu/hbm_cache_test.cpp`
- Modify: `src/host_service/capacity_page_service.hpp`
- Modify: `src/host_service/capacity_page_service.cpp`
- Modify: `tests/cpu/capacity_page_service_test.cpp`
- Modify: `CMakeLists.txt`

- [ ] **Step 1: Write failing router tests**

Create two deterministic files whose registered file offsets both begin at
zero, but whose synthetic page intervals are `[0, 2)` and `[2, 4)`. Test staged
entries are invisible, activation is no-fail, global pages select exact bytes,
read-only writes return `Unsupported`, an unmapped page returns `IoError`, and
deactivation waits for admitted readers.

```cpp
auto first = router.stage(1, 0, 2, false, first_store);
auto second = router.stage(2, 2, 2, true, second_store);
CHECK(router.read_page(0, page_bytes).status == RequestStatus::IoError);
CHECK(router.activate(first));
CHECK(router.activate(second));
CHECK(router.read_page(0, page_bytes).bytes == first_page_zero);
CHECK(router.read_page(2, page_bytes).bytes == second_page_zero);
CHECK(router.write_page(0, page_bytes, changed) ==
      RequestStatus::Unsupported);
CHECK(router.write_page(2, page_bytes, changed) == RequestStatus::Ready);
```

- [ ] **Step 2: Verify RED**

Run: `cmake --build build --target hbfsim_capacity_backing_router_tests`

Expected: compilation fails because `capacity_backing_router.hpp` does not
exist.

- [ ] **Step 3: Implement the fixed-capacity router**

Use an array of 64 entries, a mutex, per-entry admission counters, and a
condition variable. Keep the interface independent of CUDA:

```cpp
struct RoutedPage {
    RequestStatus status{RequestStatus::IoError};
    std::uint32_t range_id{0};
    std::vector<std::byte> bytes;
};

class CapacityBackingRouter {
  public:
    using Token = std::uint32_t;
    Token stage(std::uint32_t range_id, std::uint64_t first_page,
                std::uint64_t page_count, bool writable,
                std::shared_ptr<BackingStore> backing);
    bool activate(Token token) noexcept;
    void cancel(Token token) noexcept;
    RequestStatus deactivate(std::uint32_t range_id);
    RoutedPage read_page(std::uint64_t global_page,
                         std::size_t page_bytes);
    RequestStatus write_page(std::uint64_t global_page,
                             std::size_t page_bytes,
                             std::span<const std::byte> bytes);
    RequestStatus flush(std::optional<std::uint32_t> range_id = std::nullopt);
};
```

Reject zero IDs/counts, overflow, overlapping synthetic intervals, duplicate
IDs, missing stores, and activation of stale tokens. Convert to a local page by
subtracting `first_page`; never derive identity from the file offset.

- [ ] **Step 4: Route `CapacityPageService` through an interface**

Replace the fixed `BackingStore&` member with callbacks while retaining a
delegating single-store constructor for existing tests:

```cpp
struct CapacityBackingIo {
    std::function<RoutedPage(std::uint64_t, std::size_t)> read_page;
    std::function<RequestStatus(std::uint64_t, std::size_t,
                                std::span<const std::byte>)> write_page;
    std::function<RequestStatus()> flush;
};
```

The cache continues to use global pages. Preserve dirty-eviction rollback on
copy, write, and sync failure. Add
`begin_eviction_in_range(first_page, page_count, dirty_only)` so mapping-specific
flush never evicts or writes another mapping; apply the same generation checks
as ordinary clock eviction.

- [ ] **Step 5: Verify GREEN and concurrency**

Run:

```bash
cmake --build build --target hbfsim_capacity_backing_router_tests hbfsim_capacity_page_service_tests hbfsim_hbm_cache_tests -j2
ctest --test-dir build -R 'capacity_backing_router|capacity_page_service|hbm_cache' --output-on-failure
```

Expected: all pass, including overlapping file offsets and concurrent
deactivation.

- [ ] **Step 6: Commit**

```bash
git add CMakeLists.txt src/host_service src/cuda_runtime/hbm_cache.* tests/cpu/capacity_backing_router_test.cpp tests/cpu/capacity_page_service_test.cpp tests/cpu/hbm_cache_test.cpp
git commit -m "feat: route capacity pages across backing files"
```

### Task 2: Cache-Aware Capacity Timing Coordinator

**Files:**
- Modify: `src/host_service/control_layout.hpp`
- Modify: `src/host_service/capacity_page_service.hpp`
- Modify: `src/host_service/capacity_page_service.cpp`
- Modify: `src/host_service/capacity_worker.cpp`
- Modify: `src/host_service/request_dispatcher.hpp`
- Modify: `src/host_service/request_dispatcher.cpp`
- Modify: `src/host_service/main.cpp`
- Modify: `tests/cpu/capacity_handoff_test.cpp`
- Create: `tests/cpu/capacity_dispatch_test.cpp`
- Modify: `tests/integration/mqsim_online_test.cpp`
- Modify: `CMakeLists.txt`

- [ ] **Step 1: Write failing media-plan ABI tests**

Define and test these flags without changing `sizeof(PageEntry)`:

```cpp
enum CapacityMediaFlags : std::uint64_t {
    CapacityMediaNone = 0,
    CapacityMediaRead = 1ULL << 0,
    CapacityMediaProgram = 1ULL << 1,
};

struct CapacityMediaPlan {
    std::uint64_t flags{CapacityMediaNone};
    std::uint64_t program_page{0};
    std::uint32_t program_range_id{0};
};

static_assert(sizeof(PageEntry) == 64);
```

Test exact-ticket publication and retrieval of no action, read, and ordered
program-plus-read plans. A stale completer must not publish plan bits into a
reused slot.

- [ ] **Step 2: Verify RED**

Run: `ctest --test-dir build -R 'capacity_handoff|capacity_dispatch' --output-on-failure`

Expected: compile failure because media-plan fields and dispatcher preparation
do not exist.

- [ ] **Step 3: Make page resolution return media intent**

Extend the result consistently:

```cpp
struct CapacityResolveResult {
    RequestStatus status{RequestStatus::IoError};
    std::uint64_t frame_address{0};
    CapacityMediaPlan media;
};
```

A hit returns no flags. A clean miss returns `CapacityMediaRead`. A dirty
victim miss returns both flags, the victim global page, and the victim's range
ID from the router. Invalid flag/page/range combinations are converted to
`IoError` before shared publication. Pack flags into the low 32 bits of
`PageEntry::reserved1`, the victim range ID into the high 32 bits, and the
victim page into `PageEntry::checksum`.

- [ ] **Step 4: Add prepare-before-model dispatch groups**

Replace the finalize-only capacity path with an optional preparation callback:

```cpp
struct PreparedDispatch {
    HbfCompletion completion;
    std::array<HbfRequest, 2> media_actions{};
    std::uint32_t media_action_count{0};
};

struct Engine {
    std::function<PreparedDispatch(const HbfRequest&)> prepare;
    std::function<void(const HbfRequest&)> submit;
    std::function<std::optional<HbfCompletion>()> run_next_completion;
};
```

Assign every engine submission a daemon-local monotonic nonzero ID and map it
back to the original ticket, so application request IDs cannot collide with
internal actions. Submit action 0 only; after it completes, submit dependent
action 1. Publish the original completion after the last action. With zero
actions, publish the prepared frame immediately with `modeled_ns == 0`.

- [ ] **Step 5: Test ordering and interleaving**

The fake engine must prove:

```cpp
CHECK(hit.submitted_actions == 0);
CHECK(clean_miss.operations == vector{RequestOperation::Read});
CHECK(dirty_miss.operations ==
      vector{RequestOperation::Write, RequestOperation::Read});
CHECK(unrelated_completion_interleaved);
CHECK(dirty_miss.completion.modeled_ns == program_ns + read_ns);
```

Also test engine-ID exhaustion, malformed plans, daemon timeout, action failure,
and generation mismatch fail all admitted work closed.

Add `kRequestFlagExplicitCapacityProgram` for host flush requests. Such a
request bypasses the parent handoff and prepares exactly one program action for
its supplied global page/range. `CapacityPageService::flush` accepts a checked
callback:

```cpp
using ModelProgram =
    std::function<RequestStatus(std::uint32_t range_id,
                                std::uint64_t global_page)>;
RequestStatus flush(const ModelProgram& model_program,
                    std::optional<std::uint32_t> range_id = std::nullopt);
```

For each dirty eviction, copy and write the page, invoke `model_program`, then
call `complete_eviction`. If modeling fails, call `cancel_eviction`; the
already-written identical backing bytes are harmless and the page remains
dirty/retryable.

- [ ] **Step 6: Verify with MQSim**

Run:

```bash
cmake --build build-verify-worker-mqsim -j2
ctest --test-dir build-verify-worker-mqsim -R 'capacity_dispatch|mqsim_online' --output-on-failure
```

Expected: hits submit zero MQSim requests; dirty miss submits program then read
and preserves contention with an unrelated request.

- [ ] **Step 7: Commit**

```bash
git add CMakeLists.txt src/host_service tests/cpu/capacity_handoff_test.cpp tests/cpu/capacity_dispatch_test.cpp tests/integration/mqsim_online_test.cpp
git commit -m "feat: model capacity misses after cache lookup"
```

### Task 3: CUDA Capacity Runtime and Fake Driver

**Files:**
- Create: `src/cuda_runtime/capacity_runtime.hpp`
- Create: `src/cuda_runtime/capacity_runtime.cpp`
- Modify: `src/host_service/capacity_worker.hpp`
- Modify: `src/host_service/capacity_worker.cpp`
- Modify: `tests/integration/fake_cuda_lookup.cpp`
- Create: `tests/integration/capacity_runtime_test.cpp`
- Modify: `CMakeLists.txt`

- [ ] **Step 1: Write failing fake-CUDA runtime tests**

Use a generated 16 KiB-page, 32 KiB-cache profile. Test exact context setup,
two fake VMM frames, one pinned bounce page, host-to-frame and frame-to-host
copies, worker-start failure, partial-construction rollback, and destruction
order. Never allocate the nominal 8 GiB cache.

- [ ] **Step 2: Verify RED**

Run: `cmake --build build-verify-worker-cuda13 --target capacity_runtime_test`

Expected: compilation fails because `CapacityRuntime` and fake VMM entry points
do not exist.

- [ ] **Step 3: Extend fake CUDA deterministically**

Implement `cuCtxSetCurrent`, `cuMemGetAllocationGranularity`,
`cuMemAddressReserve`, `cuMemAddressFree`, `cuMemCreate`, `cuMemRelease`,
`cuMemMap`, `cuMemUnmap`, `cuMemSetAccess`, `cudaHostAlloc`, `cudaFreeHost`,
`cuMemcpyHtoD_v2`, and `cuMemcpyDtoH_v2`. Track allocations in locked maps and
expose failure injection plus live-allocation counts. VMM reserve uses aligned
host storage; map/access are validated state transitions, not real GPU calls.

- [ ] **Step 4: Implement `CapacityRuntime` ownership**

```cpp
class CapacityRuntime {
  public:
    static std::unique_ptr<CapacityRuntime> create(
        const Profile&, host_service::ControlView, std::uintptr_t cuda_context,
        int device_ordinal);
    host_service::CapacityBackingRouter& router() noexcept;
    VmmDriver& vmm() noexcept;
    RequestStatus flush(
        const host_service::ModelProgram& model_program,
        std::optional<std::uint32_t> range_id = std::nullopt);
    void stop();
};

struct CapacityMapping {
    std::uint32_t range_id{0};
    std::uint64_t first_page{0};
    std::uint64_t page_count{0};
    std::shared_ptr<host_service::BackingStore> backing;
    VmmRange logical_range;
    host_service::CapacityBackingRouter::Token router_token{0};
    bool active{false};
};
```

Create the driver, frame pool, shared cache, router callbacks, pinned bounce
page, page service, then worker. Add a worker startup hook with a readiness
condition; it calls `cuCtxSetCurrent` and construction fails before publication
unless the exact context/device is current. Declare the worker last.

- [ ] **Step 5: Verify GREEN and leaks**

Run:

```bash
cmake --build build-verify-worker-cuda13 --target capacity_runtime_test -j2
ctest --test-dir build-verify-worker-cuda13 -R 'capacity_runtime|capacity_worker' --output-on-failure
```

Expected: all injected failures return zero live VMM handles, reservations,
pinned buffers, and worker threads.

- [ ] **Step 6: Commit**

```bash
git add CMakeLists.txt src/cuda_runtime/capacity_runtime.* src/host_service/capacity_worker.* tests/integration
git commit -m "feat: own shared CUDA capacity runtime"
```

### Task 4: Transactional Public Map, Flush, and Unregister

**Files:**
- Modify: `src/cuda_runtime/context.cpp`
- Modify: `src/cuda_runtime/context.hpp`
- Modify: `src/cuda_runtime/range_table.hpp`
- Modify: `src/cuda_runtime/range_table.cpp`
- Modify: `tests/integration/public_cuda_lifecycle_test.cpp`
- Modify: `tests/integration/fake_cuda_lookup.cpp`
- Modify: `CMakeLists.txt`

- [ ] **Step 1: Write failing public API tests**

Generate two four-page files with overlapping file offsets and a small profile.
Test two successful capacity maps return distinct aligned unbacked pointers,
the router returns each file's exact bytes, a relevant launch is accepted only
after trusted instrumentation, flush succeeds, and unregister releases only
the selected mapping. Add failures for wrong mode/permissions, file bounds,
foreign CUDA domain, VMM allocation, gate registration, post-launch map,
flush, device synchronization, and unregister acknowledgement.

- [ ] **Step 2: Verify RED**

Run: `ctest --test-dir build-verify-worker-cuda13 -R public_cuda_capacity --output-on-failure`

Expected: failure because `hbfsim_map_file` returns `HBFSIM_UNSUPPORTED`.

- [ ] **Step 3: Retain profile and capacity mappings in context**

Add context-owned state:

```cpp
std::unique_ptr<hbfsim::Profile> profile;
std::unique_ptr<hbfsim::runtime::CapacityRuntime> capacity;
std::array<std::unique_ptr<hbfsim::runtime::CapacityMapping>,
           hbfsim::host_service::kRangeCapacity> capacity_mappings;
```

Protect lazy creation and mapping mutation with `process_mutex`. Public
operations must first enter `ContextOperation` and validate the exact current
CUDA domain.

- [ ] **Step 4: Implement the map publication transaction**

Open `BackingStore`, reserve `VmmRange`, and reserve an inactive mapping slot
before `RangeTable::add`. In its gate acknowledgement, fill the synthetic page
interval from `SharedRangeRecord::file_offset`, activate the router entry, then
invoke the RangeTable publish callback. Return the pointer only after success.
Rollback all staged resources on any pre-publication error. Reject writable
mapping unless Task 2 timing coordination is active.

- [ ] **Step 5: Implement flush and unregister**

`hbfsim_flush` checks daemon/domain health and calls the runtime flush. For
each `ModelProgram` callback, reserve a normal shared-ring ticket, submit an
`HbfRequest` with `kRequestFlagExplicitCapacityProgram`, the victim range ID,
global page address, page size, and write operation, then wait through the
existing timeout/heartbeat path for its exact completion. For
unregister, locate the exact VMM base and call `RangeTable::remove` using launch
gate ABI v2. Its acknowledgement synchronizes the device, flushes the mapping's
dirty pages through modeled program requests, deactivates the router, and then
publishes removal. After the no-fail publish, release VMM/backing ownership.

- [ ] **Step 6: Integrate context destruction**

After admission closes and launch retirement succeeds, flush capacity state,
stop/join the worker, then destroy mappings and the frame pool before host
control unregistration. Any failure after retirement quarantines the owner and
retains reachable resources, matching the existing timing teardown policy.

- [ ] **Step 7: Verify GREEN**

Run:

```bash
cmake --build build-verify-worker-cuda13 -j2
ctest --test-dir build-verify-worker-cuda13 -R 'public_cuda_capacity|public_cuda_lifecycle|device_range_validation' --output-on-failure
```

Expected: all success, rollback, retry, and quarantine cases pass with zero
real CUDA execution.

- [ ] **Step 8: Commit**

```bash
git add CMakeLists.txt src/cuda_runtime tests/integration/public_cuda_lifecycle_test.cpp tests/integration/fake_cuda_lookup.cpp
git commit -m "feat: expose transactional file-backed capacity"
```

### Task 5: Documentation and Full Proof Matrix

**Files:**
- Modify: `README.md`
- Modify: `docs/superpowers/specs/2026-08-09-hbfsim-hybrid-design.md`
- Modify: `docs/superpowers/plans/2026-08-09-hbfsim-hybrid.md`

- [ ] **Step 1: Update status without live-proof overclaim**

Explain at a high level that registered logical ranges share a bounded HBM
cache and that MQSim sees only cache misses and dirty writebacks. Mark public
fake-driver lifecycle proven, while keeping real GPU, over-VRAM, llama.cpp, and
vLLM proof explicitly pending.

- [ ] **Step 2: Run focused stress and TSAN**

Run:

```bash
for run in $(seq 1 200); do build-verify-worker-cpu/hbfsim_capacity_worker_tests; done
ctest --test-dir build-verify-worker-tsan -R 'capacity_worker|capacity_backing_router|capacity_dispatch' --output-on-failure
```

Expected: 200/200 stress passes and TSAN reports no race.

- [ ] **Step 3: Run all non-live matrices**

Run:

```bash
ctest --test-dir build-verify-worker-cpu --output-on-failure
ctest --test-dir build-verify-worker-cuda13 --output-on-failure
python3 tests/integration/run_ptxpass_json.py build-verify-worker-cuda13/src/ptxpass_hbf/ptxpass_hbf
ctest --test-dir build-verify-worker-mqsim --output-on-failure
git diff --check
```

Expected: every CPU, CUDA-static/PTX/fake-driver, and MQSim test passes; PTX JSON
fixture exits zero; diff check is clean. Do not run a GPU binary or
`nvidia-smi`.

- [ ] **Step 4: Final review and commit**

Run specification review before code-quality review, fix every reported issue,
repeat the affected matrix, then commit:

```bash
git add README.md docs/superpowers/specs/2026-08-09-hbfsim-hybrid-design.md docs/superpowers/plans/2026-08-09-hbfsim-hybrid.md
git commit -m "docs: explain shared HBF capacity runtime"
git push origin hybrid
```
