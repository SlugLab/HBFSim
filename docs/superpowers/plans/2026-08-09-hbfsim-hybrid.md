# HBFSim Hybrid Live Emulator Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build and validate a live HBF emulator that automatically rewrites GPU PTX, uses MQSim as its detailed flash timing reference, injects delay into running CUDA workloads, and pages file-backed data through an HBM cache for llama.cpp and vLLM.

**Architecture:** bpftime intercepts CUDA modules and runs an HBFSim-owned PTX pass over supported global loads and stores. Injected GPU helpers check explicit HBF ranges, coalesce requests, resolve an HBM page cache, and communicate with a host service through a pinned shared ring. The host service uses a patched build copy of MQSim for detailed timing and a GPU-local calibrated model for unsampled requests.

**Tech Stack:** C++20, CUDA 12.8+, PTX ISA 8.7, CMake 3.25+, CTest, Python 3.11+, pytest, JSON/JSON Schema, bpftime, MQSim, llama.cpp, vLLM, PyTorch, Triton.

## Global Constraints

- Work only in `/root/hbfsim/HBFSim/.worktrees/hybrid` on branch `hybrid`.
- Treat `docs/superpowers/specs/2026-08-09-hbfsim-hybrid-design.md` as the implementation contract.
- Pin bpftime to `ec26daecc8e787fb80fd95dd596a576404a5e36e`.
- Pin MQSim to `51f0f2d3fed92d88ef4a0fa61a38024b07bf9d16`.
- Pin llama.cpp compatibility to `7ba604f1cb61cd14898138e9abc0b4ff2601f180` for the first proof run.
- Pin vLLM compatibility to `f8d03e77416bf90c49acbe50e233275722f02c4b` for the first proof run.
- Use `/usr/local/cuda-12.8` as the primary build toolkit and retain `sm_120` PTX.
- Do not silently execute an HBF pointer in an unsupported or uninstrumented kernel.
- Keep timing-only and capacity-mode outputs bit-exact against each runtime's own baseline.
- Report `modeled_ns`, `wall_ns`, `service_ns`, and `overhead_ns` separately.
- Never treat build or CPU-only tests as live GPU, capacity, timing, or LLM proof.
- Use native Linux. Container recipes may be added only as optional documentation.
- End every task with the named focused commit after its tests pass.

---

## Planned File Map

```text
CMakeLists.txt                         top-level options and targets
cmake/Dependencies.cmake              pinned submodule checks
cmake/MQSimPatchedBuild.cmake         clean build-copy and patch application
configs/profiles/*.json               synthetic HBF profiles
configs/schema/hbf-profile.schema.json profile contract
include/hbfsim/api.h                  public C API
include/hbfsim/protocol.hpp            shared request/completion ABI
include/hbfsim/profile.hpp             typed profile model
include/hbfsim/report.hpp              structured run report types
src/profile/*                          profile parsing and validation
src/protocol/*                         ring and page-directory state machines
src/mqsim_adapter/*                    online MQSim C++ wrapper
patches/mqsim/0001-online-hbf-api.patch isolated MQSim changes
src/ptxpass_hbf/*                      PTX parser, transformer, pass executable
src/cuda_runtime/*                     host API, CUDA VMM, HBM cache, device helpers
src/host_service/*                     daemon, backing store, request dispatcher
src/hybrid/*                           fast model and MQSim calibration
src/reporting/*                        JSON artifacts and timing validity
adapters/llama_cpp/*                   llama.cpp patch and runner
adapters/vllm/*                        vLLM loader plugin and runner
benchmarks/cuda/*                      deterministic CUDA workloads
scripts/*                              build and proof entrypoints
tests/cpu/*                            CPU unit tests
tests/gpu/*                            CUDA semantic and failure tests
tests/integration/*                    MQSim and LLM end-to-end tests
third_party/bpftime                    pinned submodule
third_party/mqsim                      pinned submodule
```

### Task 1: Reproducible Native Build Foundation

**Files:**
- Create: `CMakeLists.txt`
- Create: `cmake/Dependencies.cmake`
- Create: `scripts/bootstrap.sh`
- Create: `tests/cpu/build_smoke.cpp`
- Modify: `.gitmodules`
- Modify: `.gitignore`

**Interfaces:**
- Produces CMake targets `hbfsim_core`, `hbfsim_cpu_tests`, and feature options `HBFSIM_ENABLE_CUDA`, `HBFSIM_ENABLE_MQSIM`, `HBFSIM_ENABLE_LLM_TESTS`.
- Produces pinned source trees at `third_party/bpftime` and `third_party/mqsim`.

- [ ] **Step 1: Add pinned submodules**

```bash
git submodule add https://github.com/eunomia-bpf/bpftime.git third_party/bpftime
git -C third_party/bpftime checkout ec26daecc8e787fb80fd95dd596a576404a5e36e
git submodule add https://github.com/CMU-SAFARI/MQSim.git third_party/mqsim
git -C third_party/mqsim checkout 51f0f2d3fed92d88ef4a0fa61a38024b07bf9d16
```

- [ ] **Step 2: Write the smoke test before defining the library**

```cpp
// tests/cpu/build_smoke.cpp
#include <hbfsim/api.h>
#include <cassert>

int main() {
    assert(hbfsim_abi_version() == 1u);
    return 0;
}
```

- [ ] **Step 3: Configure once and verify the missing API fails**

Run:

```bash
cmake -S . -B build -G Ninja \
  -DHBFSIM_ENABLE_CUDA=OFF -DHBFSIM_ENABLE_MQSIM=OFF
cmake --build build
```

Expected: compilation fails because `hbfsim/api.h` and `hbfsim_abi_version` do not exist.

- [ ] **Step 4: Add the minimal public header and build targets**

```c
// include/hbfsim/api.h
#pragma once
#include <stdint.h>
#ifdef __cplusplus
extern "C" {
#endif
uint32_t hbfsim_abi_version(void);
#ifdef __cplusplus
}
#endif
```

```cpp
// src/api.cpp
#include <hbfsim/api.h>
uint32_t hbfsim_abi_version(void) { return 1u; }
```

`Dependencies.cmake` must stop configuration if either submodule HEAD differs from its required commit. `bootstrap.sh` must run `git submodule update --init --recursive`, validate `/usr/local/cuda-12.8/bin/nvcc` when CUDA is enabled, and configure Ninja without installing system packages.

- [ ] **Step 5: Build and run the smoke test**

Run:

```bash
cmake -S . -B build -G Ninja \
  -DHBFSIM_ENABLE_CUDA=OFF -DHBFSIM_ENABLE_MQSIM=OFF
cmake --build build
ctest --test-dir build --output-on-failure -R build_smoke
```

Expected: one test passes and dependency commit checks print both pinned hashes.

- [ ] **Step 6: Commit**

```bash
git add .gitignore .gitmodules CMakeLists.txt cmake scripts tests include src/api.cpp third_party
git commit -m "build: bootstrap pinned HBFSim dependencies"
```

### Task 2: Typed Profiles and Named HBF Configurations

**Files:**
- Create: `configs/schema/hbf-profile.schema.json`
- Create: `configs/profiles/conservative.json`
- Create: `configs/profiles/nominal.json`
- Create: `configs/profiles/aggressive.json`
- Create: `include/hbfsim/profile.hpp`
- Create: `src/profile/profile.cpp`
- Create: `tests/cpu/profile_test.cpp`
- Modify: `CMakeLists.txt`

**Interfaces:**
- Produces `hbfsim::Profile load_profile(const std::filesystem::path&)`.
- Produces `void validate_profile(const Profile&)`, throwing `ProfileError` with a stable message.

- [ ] **Step 1: Write failing profile tests**

```cpp
auto p = hbfsim::load_profile("configs/profiles/nominal.json");
CHECK(p.page_bytes == 16384);
CHECK(p.read_latency_ns == 10000);
CHECK(p.program_latency_ns == 100000);
CHECK(p.channels == 32);
CHECK(p.aggregate_bandwidth_bytes_per_s == 512000000000ULL);

p.page_bytes = 12288;
CHECK_THROWS_WITH(hbfsim::validate_profile(p),
                  "page_bytes must be a power of two");
```

- [ ] **Step 2: Run the tests and confirm the parser is absent**

Run: `cmake --build build && ctest --test-dir build -R profile --output-on-failure`

Expected: compile failure for undefined `hbfsim::Profile`.

- [ ] **Step 3: Implement the exact profile type**

```cpp
struct Profile {
    std::string name;
    uint64_t capacity_bytes;
    uint32_t page_bytes;
    uint64_t read_latency_ns;
    uint64_t program_latency_ns;
    uint32_t channels;
    uint32_t dies_per_channel;
    uint32_t planes_per_die;
    uint32_t pages_per_block;
    uint32_t channel_width_bits;
    uint32_t channel_transfer_rate_mtps;
    uint32_t queue_depth;
    uint64_t aggregate_bandwidth_bytes_per_s;
    uint64_t hbm_cache_bytes;
    double reference_sample_rate;
    uint32_t reference_warmup_requests;
    uint32_t time_scale;
    uint64_t timing_tolerance_ns;
};
```

Use the design values exactly: conservative `(16 KiB, 20 us, 200 us, 16, 8, 64, 128 GB/s)`, nominal `(16 KiB, 10 us, 100 us, 32, 8, 128, 512 GB/s)`, and aggressive `(16 KiB, 5 us, 50 us, 64, 8, 256, 1 TB/s)`. Each profile uses 1 TiB capacity, four planes per die, 256 pages per block, an 8-bit channel, and a 1600 MT/s channel transfer rate; derive integral blocks per plane from the remaining geometry and reject a non-integral geometry. Set default cache to 8 GiB, sample rate to `0.01`, warmup to `1024`, reference `time_scale` to `100`, and timing tolerance to `10000 ns` with the 10% rule applied by reporting code.

- [ ] **Step 4: Validate all profiles and rejection cases**

Run: `ctest --test-dir build -R profile --output-on-failure`

Expected: valid profiles pass; zero capacity, non-power-of-two pages, sample rates outside `[0,1]`, and a cache larger than capacity fail with exact messages.

- [ ] **Step 5: Commit**

```bash
git add configs include/hbfsim/profile.hpp src/profile tests/cpu/profile_test.cpp CMakeLists.txt
git commit -m "feat: add validated HBF timing profiles"
```

### Task 3: Shared Protocol, Ring, and Page State Machine

**Files:**
- Create: `include/hbfsim/protocol.hpp`
- Create: `include/hbfsim/page_directory.hpp`
- Create: `src/protocol/page_directory.cpp`
- Create: `tests/cpu/protocol_layout_test.cpp`
- Create: `tests/cpu/page_directory_test.cpp`
- Modify: `CMakeLists.txt`

**Interfaces:**
- Produces fixed-layout `HbfRequest`, `HbfCompletion`, `ControlHeader`, `PageEntry`, and `RequestStatus`.
- Produces `PageDirectory::lookup_or_reserve(page, request_id)` and generation-checked `publish`/`evict` operations.

- [ ] **Step 1: Lock the shared-memory ABI with failing tests**

```cpp
static_assert(sizeof(hbfsim::HbfRequest) == 64);
static_assert(sizeof(hbfsim::HbfCompletion) == 64);
static_assert(std::is_trivially_copyable_v<hbfsim::HbfRequest>);
CHECK(static_cast<uint32_t>(hbfsim::RequestStatus::DaemonLost) == 7u);
```

```cpp
auto miss = directory.lookup_or_reserve(42, 9);
CHECK(miss.owner);
auto waiter = directory.lookup_or_reserve(42, 10);
CHECK_FALSE(waiter.owner);
directory.publish(42, miss.generation, 3);
CHECK(directory.resolve(42)->frame == 3);
CHECK_FALSE(directory.publish(42, miss.generation - 1, 4));
```

- [ ] **Step 2: Verify failures**

Run: `ctest --test-dir build -R 'protocol|page_directory' --output-on-failure`

Expected: compile failure because protocol types do not exist.

- [ ] **Step 3: Implement protocol values and state transitions**

```cpp
enum class RequestStatus : uint32_t {
    Pending = 0, Ready = 1, IoError = 2, CopyError = 3,
    ChecksumError = 4, Timeout = 5, Unsupported = 6, DaemonLost = 7
};

enum class PageState : uint32_t {
    Invalid = 0, Fetching = 1, Valid = 2, Dirty = 3, Writeback = 4
};

struct alignas(64) HbfRequest {
    uint64_t request_id;
    uint64_t sequence;
    uint64_t arrival_ns;
    uint64_t logical_address;
    uint64_t deadline_ns;
    uint32_t bytes;
    uint32_t range_id;
    uint32_t stream_id;
    uint32_t operation;
    uint32_t page_generation;
    uint32_t flags;
};

struct alignas(64) HbfCompletion {
    uint64_t request_id;
    uint64_t modeled_completion_ns;
    uint64_t modeled_ns;
    uint64_t service_ns;
    uint64_t cache_frame_address;
    uint32_t page_generation;
    uint32_t status;
    uint64_t checksum;
    uint64_t reserved;
};
```

The ring uses monotonically increasing 64-bit producer and consumer sequence numbers. A producer may write slot `n` only when `slot.sequence == n`; it publishes with release ordering. The consumer reads with acquire ordering and returns the slot by setting `slot.sequence = n + capacity`.

- [ ] **Step 4: Exercise wraparound, ABA, and ring-full behavior**

Run: `ctest --test-dir build -R 'protocol|page_directory' --output-on-failure`

Expected: tests pass under 100,000 ring wraparound operations and stale generations are rejected.

- [ ] **Step 5: Commit**

```bash
git add include/hbfsim/protocol.hpp include/hbfsim/page_directory.hpp src/protocol tests/cpu CMakeLists.txt
git commit -m "feat: define HBF request and page state protocol"
```

### Task 4: Incremental Online MQSim Adapter

**Files:**
- Create: `cmake/MQSimPatchedBuild.cmake`
- Create: `patches/mqsim/0001-online-hbf-api.patch`
- Create: `include/hbfsim/mqsim_online.hpp`
- Create: `src/mqsim_adapter/mqsim_online.cpp`
- Create: `tests/integration/mqsim_online_test.cpp`
- Modify: `CMakeLists.txt`

**Interfaces:**
- Consumes `Profile` and `HbfRequest`.
- Produces `MqsimOnlineEngine::submit(const HbfRequest&)` and `std::optional<HbfCompletion> MqsimOnlineEngine::run_next_completion()`.

- [ ] **Step 1: Write deterministic online-engine tests**

```cpp
hbfsim::MqsimOnlineEngine engine(profile);
engine.submit(read_request(1, 0, 0x1000, 16384));
engine.submit(read_request(2, 100, 0x2000, 16384));
auto first = engine.run_next_completion().value();
auto second = engine.run_next_completion().value();
CHECK(first.request_id == 1);
CHECK(second.modeled_completion_ns >= first.modeled_completion_ns);
CHECK(engine.pending() == 0);
```

- [ ] **Step 2: Confirm unmodified MQSim cannot satisfy the API**

Run: `cmake -S . -B build -DHBFSIM_ENABLE_MQSIM=ON && cmake --build build`

Expected: compile failure because incremental engine and HBF host interface symbols are absent.

- [ ] **Step 3: Create a clean patched MQSim build copy**

`MQSimPatchedBuild.cmake` must copy `third_party/mqsim` to `build/_deps/mqsim-hbf-src`, run `git apply --check` and `git apply` there, and compile that copy. It must never dirty the submodule.

The patch must add these focused MQSim changes:

```cpp
// src/sim/Engine.h in the patched copy
void Initialize_objects_once();
bool Run_next_event();
bool Has_pending_events() const;
```

```cpp
// src/ssd/Host_Interface_HBF.h in the patched copy
using HbfCompletionCallback = std::function<void(User_Request *, sim_time_type)>;
void Submit_hbf_request(User_Request *request, HbfCompletionCallback callback);
```

The HBF host interface segments requests using MQSim's existing page and sector rules, broadcasts them to the existing data-cache/FTL path, and invokes the callback from `Handle_serviced_request`. It does not create PCIe, NVMe, or SATA events. Add `HostInterface_Types::HBF` and construct `Host_Interface_HBF` from `SSD_Device`.

- [ ] **Step 4: Implement the HBFSim wrapper**

```cpp
class MqsimOnlineEngine {
public:
    explicit MqsimOnlineEngine(const Profile &profile);
    void submit(const HbfRequest &request);
    std::optional<HbfCompletion> run_next_completion();
    size_t pending() const noexcept;
};
```

Convert byte address to 512-byte sectors with checked arithmetic. Preserve request arrival order by `(arrival_ns, sequence)`. Map profile channels, dies, page latency, program latency, queue depth, capacity, and bandwidth cap into MQSim device parameters.

- [ ] **Step 5: Validate against standalone trace mode**

Run:

```bash
cmake --build build --target mqsim_online_test
ctest --test-dir build -R mqsim_online --output-on-failure
```

Expected: fixed read-only request sequences produce identical completion ordering and per-request media latency in the online wrapper and a generated MQSim trace run, after excluding the standalone NVMe/PCIe delay.

- [ ] **Step 6: Commit**

```bash
git add cmake/MQSimPatchedBuild.cmake patches/mqsim include/hbfsim/mqsim_online.hpp src/mqsim_adapter tests/integration/mqsim_online_test.cpp CMakeLists.txt
git commit -m "feat: add incremental MQSim HBF interface"
```

### Task 5: Automatic PTX Memory Rewriter

**Files:**
- Create: `src/ptxpass_hbf/ptx_memory_op.hpp`
- Create: `src/ptxpass_hbf/ptx_memory_op.cpp`
- Create: `src/ptxpass_hbf/transform.hpp`
- Create: `src/ptxpass_hbf/transform.cpp`
- Create: `src/ptxpass_hbf/main.cpp`
- Create: `configs/ptxpass/hbf-memory.json`
- Create: `tests/cpu/ptx_transform_test.cpp`
- Create: `tests/fixtures/ptx/*.ptx`
- Modify: `CMakeLists.txt`

**Interfaces:**
- Produces `parse_memory_op(std::string_view) -> std::optional<PtxMemoryOp>`.
- Produces `transform_ptx(const TransformRequest&) -> TransformResult` with transformed PTX and coverage manifest.
- Produces executable `ptxpass_hbf` using bpftime's JSON stdin/stdout pass contract.

- [ ] **Step 1: Write golden tests for actual PTX forms**

```cpp
auto op = parse_memory_op("@%p1 ld.global.v2.u32 {%r4,%r5}, [%rd8+16];");
REQUIRE(op);
CHECK(op->predicate == "@%p1");
CHECK(op->kind == AccessKind::Read);
CHECK(op->bytes == 8);
CHECK(op->base_register == "%rd8");
CHECK(op->offset == 16);
```

Golden fixtures must cover scalar/vector widths, `.nc`, negative offsets, predication, alignment qualifiers, comments, multiline function declarations, and helper exclusion. Rejection fixtures cover atomics, generic space, texture, surface, malformed addresses, and inline SASS.

- [ ] **Step 2: Run and verify the parser is missing**

Run: `ctest --test-dir build -R ptx_transform --output-on-failure`

Expected: compile failure for `parse_memory_op`.

- [ ] **Step 3: Implement typed parsing and transformation**

```cpp
struct PtxMemoryOp {
    std::string predicate;
    AccessKind kind;
    std::string opcode;
    std::string address_space;
    uint32_t bytes;
    std::string base_register;
    int64_t offset;
    std::string original_line;
};

struct TransformResult {
    std::string output_ptx;
    CoverageManifest coverage;
    bool modified;
};
```

For each supported instruction, declare a unique 64-bit scratch address and 32-bit status register in the containing function, copy/add the original effective address, call `__hbfsim_resolve`, branch to `__hbfsim_fault` on non-ready status, and emit the original instruction with only its address operand replaced. Prefix every injected instruction with the original predicate. Do not instrument functions whose names begin `__hbfsim_` or `__bpftime_`.

- [ ] **Step 4: Implement bpftime JSON pass I/O**

Input must accept `full_ptx`, `to_patch_kernel`, `global_ebpf_map_info_symbol`, and `ebpf_communication_data_symbol`. Output must contain `output_ptx`, `modified`, and a `coverage` object. Invalid JSON returns bpftime's configuration-error exit code and writes a single-line diagnostic to stderr.

- [ ] **Step 5: Run golden and standalone pass tests**

Run:

```bash
ctest --test-dir build -R ptx_transform --output-on-failure
python3 tests/integration/run_ptxpass_json.py build/src/ptxpass_hbf/ptxpass_hbf
```

Expected: every supported fixture compiles through NVIDIA's PTX compiler for `sm_120`; unsupported fixtures are recorded and remain unmodified.

- [ ] **Step 6: Commit**

```bash
git add src/ptxpass_hbf configs/ptxpass tests/cpu/ptx_transform_test.cpp tests/fixtures tests/integration/run_ptxpass_json.py CMakeLists.txt
git commit -m "feat: rewrite registered PTX global accesses"
```

### Task 6: bpftime Module Integration and Coverage Gate

**Files:**
- Create: `include/hbfsim/coverage.hpp`
- Create: `src/cuda_runtime/coverage.cpp`
- Create: `src/cuda_runtime/launch_gate.cpp`
- Create: `src/reporting/coverage_writer.cpp`
- Create: `tests/cpu/coverage_gate_test.cpp`
- Create: `tests/gpu/unsupported_kernel.cu`
- Create: `scripts/run_with_bpftime.sh`
- Modify: `CMakeLists.txt`

**Interfaces:**
- Consumes pass manifests from Task 5 and registered ranges from Task 7.
- Produces `CoverageGate::check_launch(const KernelLaunch&) -> GateDecision`.
- Produces `coverage.json` per run.

- [ ] **Step 1: Write fail-closed gate tests**

```cpp
gate.add_module(instrumented_manifest("safe", {0, 2}));
gate.add_range(0x100000, 0x200000);
CHECK(gate.check_launch(launch("safe", {0x100100})).allowed);
CHECK_FALSE(gate.check_launch(launch("cubin_only", {0x100100})).allowed);
CHECK(gate.check_launch(launch("cubin_only", {0x900000})).allowed);
```

- [ ] **Step 2: Confirm missing gate fails**

Run: `ctest --test-dir build -R coverage_gate --output-on-failure`

Expected: compile failure for `CoverageGate`.

- [ ] **Step 3: Wire the current bpftime pass directory**

`run_with_bpftime.sh` must export:

```bash
export BPFTIME_PTXPASS_DIR="$HBFSIM_ROOT/configs/ptxpass"
export BPFTIME_CUDA_ROOT=/usr/local/cuda-12.8
export LD_PRELOAD="$HBFSIM_ROOT/third_party/bpftime/build/runtime/agent/libbpftime-agent.so:$HBFSIM_ROOT/build/libhbfsim_launch_gate.so"
```

It must preserve any existing `LD_PRELOAD` entries after these two libraries and require an explicit command after `--`.

- [ ] **Step 4: Implement launch inspection**

Interpose `cuLaunchKernel` and `cudaLaunchKernel`, use the transformed PTX parameter metadata to identify pointer parameters, read their argument values, and reject a launch when an HBF logical address is passed to a cubin-only kernel, an unsupported kernel, or an uninstrumented pointer parameter. Emit one JSON decision per launch.

- [ ] **Step 5: Prove safe and unsafe launches**

Run:

```bash
cmake --build build --target unsupported_kernel coverage_gate_test
ctest --test-dir build -R coverage_gate --output-on-failure
scripts/run_with_bpftime.sh -- build/tests/gpu/unsupported_kernel --hbm
scripts/run_with_bpftime.sh -- build/tests/gpu/unsupported_kernel --hbf
```

Expected: HBM mode completes; HBF mode is rejected before kernel execution and `coverage.json` names the kernel and unsupported operation.

- [ ] **Step 6: Commit**

```bash
git add include/hbfsim/coverage.hpp src/cuda_runtime src/reporting tests scripts/run_with_bpftime.sh CMakeLists.txt
git commit -m "feat: enforce fail-closed GPU coverage gate"
```

### Task 7: Host Context, Shared Ring, and Daemon Lifecycle

**Files:**
- Modify: `include/hbfsim/api.h`
- Create: `src/cuda_runtime/context.hpp`
- Create: `src/cuda_runtime/context.cpp`
- Create: `src/host_service/main.cpp`
- Create: `src/host_service/request_dispatcher.hpp`
- Create: `src/host_service/request_dispatcher.cpp`
- Create: `tests/cpu/context_lifecycle_test.cpp`
- Create: `tests/integration/daemon_protocol_test.cpp`
- Modify: `CMakeLists.txt`

**Interfaces:**
- Produces the complete public API declared in the design.
- Produces executable `hbfsimd --profile FILE --control-fd FD --report-dir DIR`.
- Produces a pinned mapped control region containing header, range table, request ring, completion ring, page directory, and heartbeat.

- [ ] **Step 1: Write lifecycle and daemon-loss tests**

```cpp
hbfsim_context *ctx = nullptr;
hbfsim_options opts = test_options("configs/profiles/nominal.json");
CHECK(hbfsim_context_create(&opts, &ctx) == HBFSIM_OK);
CHECK(ctx != nullptr);
kill_daemon_for_test(ctx);
CHECK(wait_for_fault(ctx) == HBFSIM_DAEMON_LOST);
hbfsim_context_destroy(ctx);
```

- [ ] **Step 2: Verify failure before implementation**

Run: `ctest --test-dir build -R 'context_lifecycle|daemon_protocol' --output-on-failure`

Expected: link failure for context API functions.

- [ ] **Step 3: Implement context creation and shared control memory**

Expose these concrete public values before implementing the functions:

```c
enum hbfsim_error {
    HBFSIM_OK = 0,
    HBFSIM_INVALID_ARGUMENT = 1,
    HBFSIM_IO_ERROR = 2,
    HBFSIM_CUDA_ERROR = 3,
    HBFSIM_TIMEOUT = 4,
    HBFSIM_UNSUPPORTED = 5,
    HBFSIM_DAEMON_LOST = 6
};

struct hbfsim_options {
    const char *profile_path;
    const char *report_dir;
    uint32_t mode;
    uint32_t ring_capacity;
    uint64_t request_timeout_ns;
};

struct hbfsim_range_options {
    uint32_t mode;
    uint32_t permissions;
    uint32_t cache_policy;
    uint32_t stream_id;
};
```

Create a sealable memfd, size it, apply shrink/grow/further-seal seals, map it
`MAP_SHARED`, register that exact mapping with
`cudaHostRegisterMapped | cudaHostRegisterPortable`, obtain its device pointer
with `cudaHostGetDevicePointer`, initialize slot sequences, then fork/exec
`hbfsimd` with the inherited memfd. `cudaHostAllocMapped` is not suitable here:
its allocation has no file descriptor that survives `exec`, so an exec'd
daemon could not map the same pages. CUDA registration or device-pointer lookup
failure returns `HBFSIM_CUDA_ERROR` without a pageable fallback. A separately
named CPU-only test seam skips CUDA registration and cannot count as live CUDA
proof. The daemon rejects a non-memfd, missing seals, or any size, magic, ABI,
capacity, or offset mismatch. The child closes every inherited descriptor
other than standard input/output/error and the control fd, using a raw close
loop if `close_range` is unavailable or fails. The daemon updates
`heartbeat_ns` at least every 10 ms. The first heartbeat is published only
after profile, timing-engine, and dispatcher construction and therefore serves
as startup readiness; the context allows 10 seconds for this initialization,
separate from the per-request timeout. The parent never clears close-on-exec
on its memfd. Before exec, the child installs a parent-death `SIGKILL`, verifies
the expected parent, clears close-on-exec only on its copy, and uses an
environment with loader, bpftime, coverage, and pass-instrumentation variables
removed. Context destruction sets shutdown and
keeps the `SIGTERM`/`SIGKILL` signaling schedule within 5 seconds, then
synchronously reaps a normally schedulable child. A child stuck in
kernel-uninterruptible sleep can delay that final reap beyond the signaling
bound; no detached reaper is used. CUDA synchronization and unregister follow
reap and are not included in a total destroy deadline because the CUDA runtime
exposes no cancellable synchronization deadline.

- [ ] **Step 4: Implement bounded request dispatch**

Request reservation atomically requires both the request slot and the matching
completion slot to be free, stamps the reservation sequence into
`HbfRequest.sequence`, and returns that sequence as the ticket. Each waiter
polls and consumes only its ticket's completion slot; consuming advances that
slot by the ring capacity so wraparound cannot reuse it early. The dispatcher
drains all currently visible descriptors in reservation order and submits the
whole batch to `MqsimOnlineEngine` before advancing completions, then publishes
each terminal result to its exact ticket. Submit, completion, or malformed
result failure terminally completes every accepted, outstanding, and queued
ticket with `IO_ERROR` before publishing the global fault. A ring-full producer
waits with bounded backoff and returns `HBFSIM_TIMEOUT` at the configured
deadline. If `MqsimOnlineEngine::run_next_completion()` returns no value while
tickets remain outstanding, treat that as the same terminal failure because
MQSim has no remaining event capable of completing them.

Use one lock-free admission word with a closed bit and an in-flight reservation
count. Fault handling closes admission atomically, waits for reservations that
already passed the gate to release-publish their request slots, then drains and
fails those tickets. A producer that checked the gate but did not win admission
before closure must be rejected; a producer that already reserved a ticket
must become visible and receive its exact terminal completion before the
global fault is release-published.

Task 7 leaves `hbfsim_register_device`, `hbfsim_map_file`, and
`hbfsim_unregister` as argument-validating, fail-closed `HBFSIM_UNSUPPORTED`
stubs. Task 8 implements timing-range registration and Task 9 implements file
mapping and capacity-mode unregister behavior.

- [ ] **Step 5: Run lifecycle, wraparound, and crash tests**

Run: `ctest --test-dir build -R 'context_lifecycle|daemon_protocol' --output-on-failure`

Expected: normal shutdown, forced daemon death, ring wraparound, and timeout all terminate without leaked children or blocked tests.

- [ ] **Step 6: Commit**

```bash
git add include/hbfsim/api.h src/cuda_runtime src/host_service tests CMakeLists.txt
git commit -m "feat: add HBF host service and shared transport"
```

### Task 8: GPU Timing-Only Resolver and Live Delay

**Files:**
- Create: `src/cuda_runtime/device/hbf_device.cuh`
- Create: `src/cuda_runtime/device/hbf_device.cu`
- Create: `src/cuda_runtime/range_table.cpp`
- Create: `tests/gpu/timing_semantics.cu`
- Create: `tests/integration/timing_report_test.py`
- Modify: `src/ptxpass_hbf/transform.cpp`
- Modify: `CMakeLists.txt`

**Interfaces:**
- Produces PTX-callable `__hbfsim_resolve(uint64_t address, uint32_t bytes, uint32_t operation, uint32_t *status) -> uint64_t`.
- Produces timing-only registration through `hbfsim_register_device`.

- [ ] **Step 1: Write baseline-versus-instrumented CUDA checks**

```cpp
for (size_t i = 0; i < count; ++i) {
    CHECK(instrumented[i] == baseline[i]);
}
CHECK(report.requests > 0);
CHECK(report.wall_ns >= report.modeled_ns * report.time_scale);
```

Cover predicated false lanes, scalar and vector loads, stores, aligned and unaligned addresses, and an HBM pointer outside all ranges.

- [ ] **Step 2: Run and verify the helper is unresolved**

Run: `cmake --build build --target timing_semantics`

Expected: PTX link failure for `__hbfsim_resolve`.

- [ ] **Step 3: Implement device range lookup and request waiting**

Use a sorted table of at most 64 immutable ranges. Return the original address immediately when no range matches. For a matched timing range, elect one leader per `(warp, page)`, enqueue one request, wait using exponential `nanosleep` bounded by the context deadline, broadcast status, and return the original address. Poll heartbeat and convert a stale heartbeat to `DaemonLost`.

- [ ] **Step 4: Add system-scope publication ordering**

The host must finish all report/completion writes before a system release store to status. The GPU must use a volatile system acquire load before consuming completion fields. Add a stress test with 1,000,000 completions and verify no request observes default completion data after `Ready`.

- [ ] **Step 5: Run live sm_120 proof**

Run:

```bash
cmake -S . -B build-gpu -G Ninja \
  -DHBFSIM_ENABLE_CUDA=ON -DCMAKE_CUDA_COMPILER=/usr/local/cuda-12.8/bin/nvcc \
  -DCMAKE_CUDA_ARCHITECTURES=120
cmake --build build-gpu --target timing_semantics
scripts/run_with_bpftime.sh -- build-gpu/tests/gpu/timing_semantics
python3 -m pytest tests/integration/timing_report_test.py -q
```

Expected: values are bit-exact, requests are observed, scaled wall delay covers modeled delay, and out-of-range HBM accesses add no HBF requests.

- [ ] **Step 6: Commit**

```bash
git add src/cuda_runtime src/ptxpass_hbf/transform.cpp tests CMakeLists.txt
git commit -m "feat: inject live HBF delay into CUDA loads"
```

### Task 9: File-Backed Capacity and HBM Page Cache

**Files:**
- Create: `src/cuda_runtime/vmm.hpp`
- Create: `src/cuda_runtime/vmm.cpp`
- Create: `src/cuda_runtime/hbm_cache.hpp`
- Create: `src/cuda_runtime/hbm_cache.cpp`
- Create: `src/host_service/backing_store.hpp`
- Create: `src/host_service/backing_store.cpp`
- Create: `tests/cpu/backing_store_test.cpp`
- Create: `tests/gpu/capacity_semantics.cu`
- Modify: `src/cuda_runtime/device/hbf_device.cu`
- Modify: `include/hbfsim/api.h`
- Modify: `CMakeLists.txt`

**Interfaces:**
- Implements `hbfsim_map_file`, `hbfsim_flush`, and `hbfsim_unregister`.
- Produces `VmmRange`, `HbmCache`, `BackingStore`, and clock eviction.

- [ ] **Step 1: Write backing and eviction tests**

```cpp
auto file = DeterministicBackingStore::create(temp_path, 8 * page_bytes, 0x1234);
auto a = file.read_page(3);
auto b = file.read_page(3);
CHECK(a == b);
cache.publish(3, frame0);
cache.mark_dirty(3);
CHECK(cache.evict_one().logical_page == 3);
```

The GPU test maps eight logical pages through two HBM frames, reads every byte twice across eviction, and compares a 64-bit reduction with the CPU reference.

- [ ] **Step 2: Verify capacity API is absent**

Run: `ctest --test-dir build -R backing_store --output-on-failure`

Expected: compile or link failure for `BackingStore` and VMM API.

- [ ] **Step 3: Implement unbacked logical ranges and clean reads**

Reserve aligned addresses with `cuMemAddressReserve` but do not map physical CUDA memory at those addresses. Preallocate cache frames with `cuMemCreate`/`cuMemMap` in a distinct mapped cache interval. On miss, read exactly one logical page into a pinned bounce buffer, issue `cuMemcpyHtoDAsync` on the service stream, verify completion, then publish the frame mapping and modeled deadline.

- [ ] **Step 4: Implement writes and flush**

A partial write miss performs read-for-ownership. Dirty eviction transitions `Dirty -> Writeback`, submits a modeled program request, copies the frame to pinned host memory, uses checked `pwrite`, calls `fdatasync` during explicit flush, and only then returns the frame to `Invalid`. Short I/O yields `IoError`; hash mismatch yields `ChecksumError`.

- [ ] **Step 5: Run CPU and GPU capacity tests**

Run:

```bash
ctest --test-dir build -R backing_store --output-on-failure
cmake --build build-gpu --target capacity_semantics
scripts/run_with_bpftime.sh -- build-gpu/tests/gpu/capacity_semantics
```

Expected: clean refetch and dirty writeback are bit-exact after repeated eviction; unsupported atomics are rejected before touching the unbacked address.

- [ ] **Step 6: Commit**

```bash
git add include/hbfsim/api.h src/cuda_runtime src/host_service tests CMakeLists.txt
git commit -m "feat: add file-backed HBF capacity cache"
```

### Task 10: GPU Fast Model and Hybrid Calibration

**Files:**
- Create: `include/hbfsim/hybrid_model.hpp`
- Create: `src/hybrid/fast_model.cuh`
- Create: `src/hybrid/fast_model.cu`
- Create: `src/hybrid/calibrator.hpp`
- Create: `src/hybrid/calibrator.cpp`
- Create: `tests/cpu/calibrator_test.cpp`
- Create: `tests/gpu/hybrid_model_test.cu`
- Modify: `src/host_service/request_dispatcher.cpp`
- Modify: `src/cuda_runtime/device/hbf_device.cu`
- Modify: `CMakeLists.txt`

**Interfaces:**
- Produces operation/size/queue/locality buckets and `CalibrationTable`.
- Produces device function `fast_completion_ns(const FastRequest&, FastState*)`.
- Implements warmup `1024`, unseen-class sampling, and steady-state sample rate `0.01` by default.

- [ ] **Step 1: Write sampling and monotonicity tests**

```cpp
HybridSampler sampler(1024, 0.01, 7);
for (uint64_t i = 0; i < 1024; ++i) CHECK(sampler.reference(i, read_class));
CHECK(sampler.reference(1024, unseen_write_class));
CHECK(sampler.reference_count_after(100000, read_class) >= 1900);

CHECK(fast_latency(conservative, req) > fast_latency(nominal, req));
CHECK(fast_latency(nominal, req) > fast_latency(aggressive, req));
```

- [ ] **Step 2: Run and verify model types are missing**

Run: `ctest --test-dir build -R calibrator --output-on-failure`

Expected: compile failure for `HybridSampler`.

- [ ] **Step 3: Implement deterministic sampling and table publication**

Use a seeded counter-based hash over `(range_id, request_sequence, access_class)` so sampling is reproducible without mutable PRNG contention. The calibrator records count, mean, variance, p50, p95, and p99 modeled latency for every class. Publish a double-buffered table with generation and system release/acquire ordering.

- [ ] **Step 4: Implement bounded per-channel GPU queues**

Map logical page to channel with `page % channels`. Compute service start as the maximum of arrival and the channel tail. Add media latency and transfer time, then enforce the aggregate bandwidth token bucket. Queue depth overflow applies delay rather than dropping requests.

- [ ] **Step 5: Compare reference, fast, and hybrid**

Run:

```bash
ctest --test-dir build -R calibrator --output-on-failure
cmake --build build-gpu --target hybrid_model_test
scripts/run_with_bpftime.sh -- build-gpu/tests/gpu/hybrid_model_test
```

Expected: deterministic sample decisions; controlled traces stay within 15% p50 and 20% p95 of MQSim after warmup; profile latency ordering and bandwidth caps hold.

- [ ] **Step 6: Commit**

```bash
git add include/hbfsim/hybrid_model.hpp src/hybrid src/host_service/request_dispatcher.cpp src/cuda_runtime/device/hbf_device.cu tests CMakeLists.txt
git commit -m "feat: add MQSim-calibrated hybrid GPU model"
```

### Task 11: Complete CUDA Benchmark and Fault Matrix

**Files:**
- Create: `benchmarks/cuda/hbf_microbench.cu`
- Create: `benchmarks/cuda/reference.cpp`
- Create: `benchmarks/cuda/CMakeLists.txt`
- Create: `scripts/run_microbench.py`
- Create: `tests/integration/test_microbench_matrix.py`
- Create: `tests/integration/test_fault_matrix.py`
- Create: `tests/integration/test_over_vram.py`
- Modify: `CMakeLists.txt`

**Interfaces:**
- Produces CLI `hbf_microbench --pattern NAME --bytes N --iterations N --mode NAME --seed N --output FILE`.
- Produces one JSON result per case and a matrix summary.

- [ ] **Step 1: Define failing matrix assertions**

```python
assert result["checksum"] == result["baseline_checksum"]
assert result["coverage"]["unsafe_launches"] == 0
assert result["timing"]["modeled_ns"] > 0
assert result["requests"]["completed"] == result["requests"]["submitted"]
```

Patterns are `sequential`, `random`, `strided`, `pointer_chase`, and `mixed_rw`. Modes are `baseline`, `timing`, `reference`, `fast`, `hybrid`, and `capacity`.

- [ ] **Step 2: Run and confirm benchmark executable is absent**

Run: `python3 -m pytest tests/integration/test_microbench_matrix.py -q`

Expected: failure because `hbf_microbench` does not exist.

- [ ] **Step 3: Implement deterministic workloads and runner**

Use counter-generated data and a fixed permutation per seed. The CPU reference must compute the same 64-bit checksum without reading GPU output assumptions. Save GPU model, driver, CUDA runtime, profile hash, executable hash, git commit, and command line.

- [ ] **Step 4: Implement fault injection cases**

Add environment-controlled hooks for daemon death after request 100, ring capacity 4, host copy failure, backing short read, checksum corruption, timeout, and unsupported atomic access. Every case must end within 30 seconds and return its expected structured status.

- [ ] **Step 5: Run the full proof matrix including over-VRAM**

Run:

```bash
python3 scripts/run_microbench.py --profile configs/profiles/nominal.json --all
python3 -m pytest tests/integration/test_microbench_matrix.py tests/integration/test_fault_matrix.py -q
python3 -m pytest tests/integration/test_over_vram.py -q --logical-bytes 110G --cache-bytes 2G
```

Expected: all checksums match; failures terminate; the 110 GiB logical dataset is accessed across its full range on the 96 GiB-class GPU with a 2 GiB cache.

- [ ] **Step 6: Commit**

```bash
git add benchmarks scripts/run_microbench.py tests/integration CMakeLists.txt
git commit -m "test: prove HBF timing capacity and failure modes"
```

### Task 12: TinyLlama llama.cpp Adapter

**Files:**
- Create: `adapters/llama_cpp/0001-hbfsim-buffer-type.patch`
- Create: `adapters/llama_cpp/build.sh`
- Create: `adapters/llama_cpp/run.py`
- Create: `adapters/llama_cpp/compatibility.json`
- Create: `scripts/fetch_tinyllama.py`
- Create: `tests/integration/test_llama_cpp.py`

**Interfaces:**
- Consumes the public HBFSim C API.
- Produces a llama.cpp `ggml_backend_buffer_type_t` that maps GGUF tensor-data offsets through `hbfsim_map_file` while leaving metadata, workspace, and KV cache in HBM.
- Produces baseline/timing/capacity token and report artifacts.

- [ ] **Step 1: Pin and verify the llama.cpp integration target**

`compatibility.json` must contain commit `7ba604f1cb61cd14898138e9abc0b4ff2601f180`, CUDA architecture `120`, and the exact patch SHA-256. `build.sh` must clone or verify that revision, apply the patch with `git apply --check`, and build with `GGML_CUDA=ON`, `/usr/local/cuda-12.8/bin/nvcc`, and retained compute_120 PTX.

- [ ] **Step 2: Write the token-equality test first**

```python
baseline = run_llama("baseline", prompt="The capital of France is", tokens=32)
timing = run_llama("timing", prompt="The capital of France is", tokens=32)
capacity = run_llama("capacity", prompt="The capital of France is", tokens=32)
assert timing.token_ids == baseline.token_ids
assert capacity.token_ids == baseline.token_ids
assert capacity.coverage["unsafe_launches"] == 0
```

- [ ] **Step 3: Confirm the adapter build is absent**

Run: `python3 -m pytest tests/integration/test_llama_cpp.py -q`

Expected: failure because the patched llama.cpp binary is unavailable.

- [ ] **Step 4: Implement the HBF buffer type patch**

Patch current llama.cpp integration points under `ggml/src/ggml-cuda` and `src/llama-model.cpp`. The buffer allocator must use a normal CUDA allocation for timing mode and `hbfsim_map_file` for capacity mode. It must derive each tensor's file offset from the GGUF mapping and register only immutable model tensor data. Expose selection through `LLAMA_HBFSIM_CONFIG` and reject capacity mode when coverage is incomplete.

- [ ] **Step 5: Fetch and convert TinyLlama reproducibly**

`fetch_tinyllama.py` must use `huggingface-cli download TinyLlama/TinyLlama-1.1B-Chat-v1.0`, save downloaded file hashes, and run llama.cpp's `convert_hf_to_gguf.py` to create an F16 GGUF. Model files remain under ignored `artifacts/models/`; their hashes enter run manifests.

- [ ] **Step 6: Run llama.cpp baseline, timing, and capacity proof**

Run:

```bash
python3 scripts/fetch_tinyllama.py --output artifacts/models/tinyllama
adapters/llama_cpp/build.sh
python3 -m pytest tests/integration/test_llama_cpp.py -q
```

Expected: three 32-token greedy sequences are identical; HBF requests are nonzero; capacity coverage has no unsafe launch; all artifacts are written.

- [ ] **Step 7: Commit**

```bash
git add adapters/llama_cpp scripts/fetch_tinyllama.py tests/integration/test_llama_cpp.py
git commit -m "feat: run TinyLlama llama.cpp from HBF"
```

### Task 13: TinyLlama vLLM Adapter and Opaque-Kernel Gate

**Files:**
- Create: `adapters/vllm/hbfsim_loader.py`
- Create: `adapters/vllm/hbfsim_extension.cpp`
- Create: `adapters/vllm/build.sh`
- Create: `adapters/vllm/run.py`
- Create: `adapters/vllm/compatibility.json`
- Create: `tests/integration/test_vllm.py`
- Create: `tests/integration/test_vllm_opaque_gate.py`
- Modify: `CMakeLists.txt`

**Interfaces:**
- Produces `HbfSimModelLoader`, a vLLM `BaseModelLoader` plugin registered through `vllm.model_executor.model_loader.register_model_loader`.
- Produces Python bindings `map_safetensor(path, offset, length, readonly=True)` and `register_tensor(tensor)`.

- [ ] **Step 1: Pin and inspect vLLM compatibility**

`compatibility.json` must contain commit `f8d03e77416bf90c49acbe50e233275722f02c4b`, Python version, PyTorch version, CUDA 12.8, compute capability 12.0, and adapter source hashes. `build.sh` verifies the commit and builds vLLM/custom operators from source with PTX retained.

- [ ] **Step 2: Write baseline, HBF, and rejection tests**

```python
baseline = run_vllm("baseline", prompt="The capital of France is", tokens=32)
timing = run_vllm("timing", prompt="The capital of France is", tokens=32)
capacity = run_vllm("capacity", prompt="The capital of France is", tokens=32)
assert timing.token_ids == baseline.token_ids
assert capacity.token_ids == baseline.token_ids
assert capacity.coverage["unsafe_launches"] == 0
```

The opaque-gate test intentionally selects a precompiled backend and must fail before kernel launch with status `UNSUPPORTED` and the kernel/module named in `coverage.json`.

- [ ] **Step 3: Confirm the loader plugin is unavailable**

Run: `python3 -m pytest tests/integration/test_vllm.py -q`

Expected: import failure for `HbfSimModelLoader`.

- [ ] **Step 4: Implement the loader using current vLLM APIs**

Subclass `BaseModelLoader` from `vllm/model_executor/model_loader/base_loader.py`. Reuse path discovery and safetensors indexing from `default_loader.py` and `weight_utils.py`, but map immutable weight extents through the HBFSim extension. Register the class using the loader registry in `vllm/model_executor/model_loader/__init__.py` without editing installed vLLM source.

- [ ] **Step 5: Force an instrumentable validation backend**

Use the Triton attention backend and source-built PTX-producing linear kernels for every HBF-consuming operation. Leave KV cache and temporary tensors as ordinary CUDA tensors. If cuBLAS, cuBLASLt, precompiled FlashAttention, or another opaque kernel receives an HBF logical pointer, the launch gate rejects the run. Do not replace this rejection with implicit HBM staging.

- [ ] **Step 6: Run vLLM proof and opaque rejection**

Run:

```bash
adapters/vllm/build.sh
python3 -m pytest tests/integration/test_vllm.py tests/integration/test_vllm_opaque_gate.py -q
```

Expected: baseline/timing/capacity greedy token sequences match; the supported capacity run has no unsafe launch; the forced opaque configuration is rejected before execution.

- [ ] **Step 7: Commit**

```bash
git add adapters/vllm tests/integration/test_vllm.py tests/integration/test_vllm_opaque_gate.py CMakeLists.txt
git commit -m "feat: run TinyLlama vLLM from HBF"
```

### Task 14: Reporting, Reproduction Commands, and Final Proof Gate

**Files:**
- Create: `include/hbfsim/report.hpp`
- Create: `src/reporting/report.cpp`
- Create: `scripts/run_all_proofs.sh`
- Create: `scripts/validate_report.py`
- Create: `README.md`
- Create: `docs/results-schema.md`
- Create: `tests/cpu/report_test.cpp`
- Modify: `CMakeLists.txt`

**Interfaces:**
- Produces versioned `run.json`, `coverage.json`, `requests.jsonl`, `mqsim.json`, `cache.json`, and `tokens.json` artifacts.
- Produces one command that runs every required proof gate.

- [ ] **Step 1: Write report schema tests**

```cpp
RunReport r;
r.modeled_ns = 10000;
r.wall_ns = 11000;
r.service_ns = 3000;
r.time_scale = 1;
r.timing_tolerance_ns = 10000;
CHECK(r.timing_valid());
r.wall_ns = 25000;
CHECK_FALSE(r.timing_valid());
CHECK(r.validity == "emulator-overhead-limited");
```

- [ ] **Step 2: Verify missing report API fails**

Run: `ctest --test-dir build -R report --output-on-failure`

Expected: compile failure for `RunReport`.

- [ ] **Step 3: Implement exact artifact validation**

`validate_report.py` must reject missing dependency commits, model hashes, GPU identity, profile hash, request counts, coverage decisions, timing fields, cache counts, token IDs, or a nonzero unsafe launch count. Timing validity uses tolerance `max(profile.timing_tolerance_ns, modeled_ns * 0.10)`.

- [ ] **Step 4: Document native commands and proof boundaries**

README must contain separate commands for CPU tests, GPU semantic tests, each microbenchmark mode, over-VRAM capacity, llama.cpp, vLLM, and the opaque-kernel rejection. It must state that the three bundled profiles are synthetic and explain `modeled_ns`, `wall_ns`, `service_ns`, and `overhead_ns`.

- [ ] **Step 5: Run all verification from a clean tree**

Run:

```bash
git status --short
scripts/run_all_proofs.sh --cuda-root /usr/local/cuda-12.8 \
  --profile configs/profiles/nominal.json \
  --model-dir artifacts/models/tinyllama \
  --output artifacts/final-proof
python3 scripts/validate_report.py artifacts/final-proof
```

Expected:

- clean status before generated ignored artifacts;
- all CPU and GPU tests pass;
- timing and capacity microbench checksums match;
- 110 GiB logical capacity proof passes with 2 GiB cache;
- llama.cpp and vLLM token sequences match their own baselines;
- coverage reports zero unsafe launches;
- every report validates.

- [ ] **Step 6: Commit documentation and reports code**

```bash
git add include/hbfsim/report.hpp src/reporting scripts README.md docs/results-schema.md tests/cpu/report_test.cpp CMakeLists.txt
git commit -m "docs: add reproducible HBFSim proof workflow"
```

- [ ] **Step 7: Verify branch and push only after all gates pass**

Run:

```bash
git status --short
git log --oneline --decorate main..hybrid
git push -u origin hybrid
```

Expected: worktree is clean, all implementation commits are present, and remote branch `hybrid` points to the verified local HEAD.
