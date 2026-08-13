# SM120 Stage 2 PTX IR and Async Futures Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace instruction-local synchronous global-memory resolution with a fail-closed PTX IR and ordinary load/store/atomic shadow futures that issue before completion and wait only at proven dependencies or ordering points.

**Architecture:** A lightweight PTX parser produces functions, basic blocks, typed instructions, predicates, and register def/use sets. A forward dataflow pass assigns stable instruction IDs and future values; transformation emits register-carried issue records and idempotent consumer/drain waits. The device helper splits the current resolver into issue and completion operations while retaining the old resolver only for non-exact emulation.

**Tech Stack:** C++20, CUDA 13 PTX 9.0, CUDA device C++, nlohmann/json, OpenSSL SHA-256, CMake/CTest, Python integration tests.

---

## Stage 2 invariants

- Exact transformation is all-or-nothing per selected kernel; ambiguity is a pass failure.
- The original global operation is not preceded by an unconditional completion wait.
- A load value cannot be consumed before both its native condition and shadow future are ready.
- Store source bytes are captured at issue; release/fence/exit drains the required scope.
- Atomics are one indivisible future and never split into a load plus store.
- Issue-slot throttling and completion waiting are separately counted.
- Every exact future is terminal exactly once and fits the profile's thread/warp/CTA/cluster budgets.
- Stage 2 does not claim TensorMap/TMA or physical channel timing support.

### Task 1: Parse PTX into a stable instruction IR

**Files:**
- Create: `src/ptxpass_hbf/ptx_ir.hpp`
- Create: `src/ptxpass_hbf/ptx_ir.cpp`
- Create: `tests/cpu/ptx_ir_test.cpp`
- Create: `tests/fixtures/ptx/async_cfg.ptx`
- Modify: `CMakeLists.txt`

- [ ] **Step 1: Write the failing parser test**

The fixture contains two basic blocks, a predicated vector load with signed
offset, a cache-qualified volatile store, an acquire load, a release store, an
atomic, a fence, and two returns. Assert exact source locations, opcode,
qualifiers, predicate, ordered operands, label targets, and register def/use
sets:

```cpp
const auto module = hbfsim::ptx::parse_module(read_fixture("async_cfg.ptx"));
const auto& kernel = module.function("async_cfg");
CHECK(kernel.blocks.size() == 3);
CHECK(kernel.instructions.at(4).opcode == "ld.global.acquire.gpu.v2.u32");
CHECK(kernel.instructions.at(4).defs == std::vector({"%r4", "%r5"}));
CHECK(kernel.instructions.at(4).uses == std::vector({"%rd2", "%p1"}));
CHECK(kernel.instructions.at(4).predicate == "@%p1");
CHECK(kernel.instructions.at(4).memory->signed_offset == -16);
CHECK(kernel.instructions.at(8).memory->kind == MemoryKind::AtomicRmw);
```

- [ ] **Step 2: Run RED**

```bash
cmake --build build-sm120-exact --target ptx_ir_test -j2
```

Expected: compilation fails because `ptx_ir.hpp` and the target do not exist.

- [ ] **Step 3: Implement the parser**

Define focused types and preserve the exact original instruction text:

```cpp
enum class MemoryKind { Load, Store, AtomicRmw, None };
struct SourceLocation { std::uint32_t line; std::uint32_t column; };
struct MemoryInstruction {
    MemoryKind kind;
    std::string state_space;
    std::vector<std::string> qualifiers;
    std::vector<std::string> value_operands;
    std::string address_base;
    std::int64_t signed_offset;
    std::uint32_t bytes;
};
struct Instruction {
    std::uint32_t instruction_id;
    SourceLocation location;
    std::string text, opcode, predicate;
    std::vector<std::string> operands, defs, uses, branch_targets;
    std::optional<MemoryInstruction> memory;
};
struct BasicBlock { std::string label; std::vector<std::size_t> instructions; };
struct Function { std::string name; std::vector<Instruction> instructions;
                  std::vector<BasicBlock> blocks; };
struct Module { std::vector<Function> functions;
                const Function& function(std::string_view name) const; };
Module parse_module(std::string_view ptx);
```

Lex braces, declarations, labels, predicates, bracketed address expressions,
and brace-enclosed vector operands without relying on one line per logical
instruction. Reject comments or quoted file directives only when they are
unterminated; preserve them otherwise.

- [ ] **Step 4: Run GREEN and regression parser tests**

```bash
cmake --build build-sm120-exact --target ptx_ir_test ptx_transform_test -j2
ctest --test-dir build-sm120-exact -R '^(ptx_ir|ptx_transform)$' --output-on-failure
```

- [ ] **Step 5: Commit**

```bash
git add src/ptxpass_hbf/ptx_ir.hpp src/ptxpass_hbf/ptx_ir.cpp \
  tests/cpu/ptx_ir_test.cpp tests/fixtures/ptx/async_cfg.ptx CMakeLists.txt
git commit -m "feat: parse PTX into a stable instruction IR"
```

### Task 2: Build CFG, def-use, dominators, and future liveness

**Files:**
- Create: `src/ptxpass_hbf/ptx_analysis.hpp`
- Create: `src/ptxpass_hbf/ptx_analysis.cpp`
- Create: `tests/cpu/ptx_analysis_test.cpp`
- Modify: `CMakeLists.txt`

- [ ] **Step 1: Write RED dataflow tests**

Cover straight-line use, predicated definition, diamond join, loop-carried
definition, multiple consumers, register redefinition, ambiguous reaching
definitions, and unreachable blocks. The key assertions are:

```cpp
const auto analysis = analyze_function(kernel);
CHECK(analysis.first_consumers(load_id) == std::set({use_a, use_b}));
CHECK(analysis.drain_points(store_id) == std::set({release_fence, exit_id}));
CHECK(analysis.maximum_live.thread_futures == 2);
CHECK(analysis.maximum_live.warp_futures == 64);
CHECK_FALSE(analysis.exact_safe(ambiguous_use_id));
CHECK(analysis.reason(ambiguous_use_id) == "ambiguous_future_definition");
```

- [ ] **Step 2: Run RED**

```bash
cmake --build build-sm120-exact --target ptx_analysis_test -j2
```

Expected: missing analysis API.

- [ ] **Step 3: Implement CFG and fixed-point analysis**

Expose immutable results:

```cpp
struct FutureBudgets { std::uint32_t thread_futures, warp_futures,
                       cta_futures, cluster_futures; };
struct FuturePlan {
    std::map<std::uint32_t, std::set<std::uint32_t>> first_consumers;
    std::map<std::uint32_t, std::set<std::uint32_t>> drain_points;
    std::map<std::uint32_t, std::string> rejection_reasons;
    FutureBudgets maximum_live;
    bool exact_safe() const noexcept;
};
FuturePlan analyze_futures(const Function& function);
```

Use classic predecessor/successor construction, iterative dominators, reaching
definitions, backwards register liveness, and forward outstanding-future sets.
A merge containing different future definitions for the same architectural
register is allowed only when the same predicate/phi condition can be emitted;
otherwise record the stable rejection reason.

- [ ] **Step 4: Run GREEN**

```bash
cmake --build build-sm120-exact --target ptx_analysis_test -j2
ctest --test-dir build-sm120-exact -R '^ptx_analysis$' --output-on-failure
```

- [ ] **Step 5: Commit**

```bash
git add src/ptxpass_hbf/ptx_analysis.hpp src/ptxpass_hbf/ptx_analysis.cpp \
  tests/cpu/ptx_analysis_test.cpp CMakeLists.txt
git commit -m "feat: analyze PTX future dependencies"
```

### Task 3: Define the shadow-future ABI and terminal state machine

**Files:**
- Create: `include/hbfsim/shadow_future.hpp`
- Create: `src/cuda_runtime/shadow_future.cpp`
- Create: `tests/cpu/shadow_future_test.cpp`
- Modify: `src/host_service/control_layout.hpp`
- Modify: `include/hbfsim/protocol.hpp`
- Modify: `CMakeLists.txt`

- [ ] **Step 1: Write RED state-machine tests**

Test native bypass, timing-only issued/ready, capacity pending/materialized,
issue throttle, timeout, daemon failure, duplicate terminal completion, and
scope drains. Assert conservation and stable counters:

```cpp
ShadowFuture future = machine.issue(request);
CHECK(future.state == FutureState::Issued);
CHECK(machine.poll(future) == FuturePoll::Pending);
CHECK(machine.complete(future.ticket, completion));
CHECK(machine.wait(future).state == FutureState::Ready);
CHECK_FALSE(machine.complete(future.ticket, completion));
CHECK(machine.counters().terminal_completions == 1);
```

- [ ] **Step 2: Run RED**

```bash
cmake --build build-sm120-exact --target shadow_future_test -j2
```

- [ ] **Step 3: Implement ABI types and the CPU reference machine**

```cpp
enum class FutureState : std::uint32_t {
    Native = 0, Issued = 1, Ready = 2, DeferredMaterialization = 3,
    TerminalError = 4, Consumed = 5,
};
struct ShadowFuture {
    std::uint64_t ticket, original_address, resolved_address, ready_ns;
    std::uint32_t bytes, instruction_id, channel, flags, state, status;
};
struct FutureCounters {
    std::uint64_t issued, issue_throttle_ns, dependency_wait_ns,
                  ordering_wait_ns, terminal_completions, faults;
};
```

Add `instruction_id`, future flags, and issue timestamp to `HbfRequest`, bump
the shared-control ABI, and update every layout/static-assert test in the same
commit. Terminal completion is a compare/exchange transition and cannot be
overwritten.

- [ ] **Step 4: Run GREEN and protocol regressions**

```bash
cmake --build build-sm120-exact --target shadow_future_test \
  hbfsim_protocol_tests hbfsim_device_helper_abi_tests -j2
ctest --test-dir build-sm120-exact \
  -R '^(shadow_future|protocol_layout|device_helper_abi)$' --output-on-failure
```

- [ ] **Step 5: Commit**

```bash
git add include/hbfsim/shadow_future.hpp src/cuda_runtime/shadow_future.cpp \
  src/host_service/control_layout.hpp include/hbfsim/protocol.hpp \
  tests/cpu/shadow_future_test.cpp tests/cpu/protocol_layout_test.cpp \
  tests/cpu/device_helper_abi_test.cpp CMakeLists.txt
git commit -m "feat: define ordinary memory shadow futures"
```

### Task 4: Split the device resolver into issue, poll, and wait

**Files:**
- Modify: `src/cuda_runtime/device/hbf_device.cuh`
- Modify: `src/cuda_runtime/device/hbf_device.cu`
- Create: `tests/cpu/device_future_reference_test.cpp`
- Modify: `tests/integration/test_device_helper_ptx.py`
- Modify: `CMakeLists.txt`

- [ ] **Step 1: Write RED helper ABI tests**

Require these exact PTX-visible symbols and reject a pre-issue completion wait:

```text
.func (.param .align 16 .b8 future[64]) __hbfsim_future_issue(
  .param .b64 address, .param .b32 bytes, .param .b32 operation,
  .param .b32 instruction_id)
.func (.param .b32 ready) __hbfsim_future_poll(
  .param .align 16 .b8 future[64])
.func (.param .align 16 .b8 future[64]) __hbfsim_future_wait(
  .param .align 16 .b8 future[64], .param .b32 wait_kind)
.func __hbfsim_future_fault(.param .b32 status,
  .param .b32 instruction_id)
```

CPU reference vectors cover fast ready-time calculation, reference-ring ticket
reservation, capacity deferred address, timeout, and bounded slot pressure.

- [ ] **Step 2: Run RED**

```bash
cmake --build build-sm120-exact --target hbfsim_device_ptx \
  device_future_reference_test -j2
python3 tests/integration/test_device_helper_ptx.py \
  build-sm120-exact/generated/hbf_device.ptx /usr/local/cuda-13.0/bin/ptxas sm_120
```

- [ ] **Step 3: Implement nonblocking issue**

`__hbfsim_future_issue` returns a fixed 64-byte aligned record matching
`ShadowFuture`. For HBM it returns `Native`; for fast timing it atomically
reserves a modeled ready time but does not sleep; for reference/capacity it
reserves and publishes the ring request but leaves the completion slot for
`poll`/`wait`. Ring-full waiting updates `issue_throttle_ns`; only `wait`
updates dependency/ordering wait counters. Keep `__hbfsim_resolve` as a wrapper
that calls issue then wait for legacy emulation only.

- [ ] **Step 4: Run GREEN and assemble for SM120**

```bash
cmake --build build-sm120-exact --target hbfsim_device_ptx \
  device_future_reference_test -j2
ctest --test-dir build-sm120-exact -R '^device_future_reference$' --output-on-failure
/usr/local/cuda-13.0/bin/ptxas --gpu-name=sm_120 \
  build-sm120-exact/generated/hbf_device.ptx -o /tmp/hbfsim-stage2-helper.cubin
```

- [ ] **Step 5: Commit**

```bash
git add src/cuda_runtime/device/hbf_device.cuh \
  src/cuda_runtime/device/hbf_device.cu \
  tests/cpu/device_future_reference_test.cpp \
  tests/integration/test_device_helper_ptx.py CMakeLists.txt
git commit -m "feat: issue ordinary HBF futures asynchronously"
```

### Task 5: Rewrite loads at proven consumer boundaries

**Files:**
- Create: `src/ptxpass_hbf/future_transform.hpp`
- Create: `src/ptxpass_hbf/future_transform.cpp`
- Create: `tests/cpu/ptx_future_transform_test.cpp`
- Create: `tests/fixtures/ptx/future_loads.ptx`
- Modify: `src/ptxpass_hbf/transform.cpp`
- Modify: `src/ptxpass_hbf/transform.hpp`
- Modify: `CMakeLists.txt`

- [ ] **Step 1: Write RED golden transformations**

Assert issue appears before independent arithmetic, wait appears immediately
before every first consumer, a capacity-deferred load is materialized exactly
once per path, and predicated false paths neither issue nor wait. Verify loops,
diamonds, multiple consumers, vector values, signed offsets, volatile, and
acquire semantics. A deliberately ambiguous fixture must return
`ambiguous_future_definition` and no output PTX.

- [ ] **Step 2: Run RED**

```bash
cmake --build build-sm120-exact --target ptx_future_transform_test -j2
```

- [ ] **Step 3: Emit register-carried load futures**

For each load declare pass-owned ticket/address/ready/flags/status registers.
Emit `__hbfsim_future_issue` at the original load. Execute the native load at
issue for `Native` and timing-only states. Before each first consumer emit
idempotent `__hbfsim_future_wait`; only the `DeferredMaterialization` branch
executes the original typed load using the returned resident address. Acquire
and volatile loads wait at the original architectural ordering boundary.

- [ ] **Step 4: Run GREEN and real ptxas assembly**

```bash
cmake --build build-sm120-exact --target ptx_future_transform_test \
  ptxpass_hbf_plugin -j2
ctest --test-dir build-sm120-exact -R '^ptx_future_transform$' --output-on-failure
python3 tests/integration/test_ptxpass_plugin.py \
  build-sm120-exact/libptxpass_hbf.so src/ptxpass_hbf/plugin.cpp \
  /usr/local/cuda-13.0/bin/ptxas sm_120
```

- [ ] **Step 5: Commit**

```bash
git add src/ptxpass_hbf/future_transform.hpp \
  src/ptxpass_hbf/future_transform.cpp src/ptxpass_hbf/transform.cpp \
  src/ptxpass_hbf/transform.hpp tests/cpu/ptx_future_transform_test.cpp \
  tests/fixtures/ptx/future_loads.ptx CMakeLists.txt
git commit -m "feat: wait for HBF loads at consumers"
```

### Task 6: Rewrite stores, atomics, fences, and exits

**Files:**
- Modify: `src/ptxpass_hbf/future_transform.cpp`
- Create: `tests/fixtures/ptx/future_stores.ptx`
- Modify: `tests/cpu/ptx_future_transform_test.cpp`
- Modify: `src/cuda_runtime/device/hbf_device.cu`

- [ ] **Step 1: Write RED ordering tests**

Prove store source values are copied at issue, later register redefinitions do
not alter deferred bytes, `membar`/`fence`/release/volatile/exit drain the
correct futures, and atomic RMW has one ticket/terminal state. Unsupported
atomic types and ambiguous scopes must fail exact compilation.

- [ ] **Step 2: Run RED**

```bash
cmake --build build-sm120-exact --target ptx_future_transform_test -j2
ctest --test-dir build-sm120-exact -R '^ptx_future_transform$' --output-on-failure
```

- [ ] **Step 3: Implement snapshots and scoped drains**

Copy scalar/vector source operands into pass-owned registers before issue.
Native/timing stores execute at issue; capacity stores emit the typed store
after wait at the next required drain. Atomics use a dedicated
`FutureOperation::AtomicRmw` request and wait at the original result consumer
or ordering point. Kernel return drains every remaining future and traps on a
non-ready terminal status.

- [ ] **Step 4: Run GREEN and assemble**

```bash
cmake --build build-sm120-exact --target ptx_future_transform_test \
  ptxpass_hbf_plugin -j2
ctest --test-dir build-sm120-exact -R '^ptx_future_transform$' --output-on-failure
```

- [ ] **Step 5: Commit**

```bash
git add src/ptxpass_hbf/future_transform.cpp \
  tests/fixtures/ptx/future_stores.ptx \
  tests/cpu/ptx_future_transform_test.cpp \
  src/cuda_runtime/device/hbf_device.cu
git commit -m "feat: drain stores and atomics by PTX ordering"
```

### Task 7: Enforce future budgets and provenance in exact admission

**Files:**
- Modify: `src/ptxpass_hbf/plugin.cpp`
- Modify: `include/hbfsim/coverage.hpp`
- Modify: `src/cuda_runtime/coverage.cpp`
- Modify: `src/cuda_runtime/exact_admission.cpp`
- Modify: `src/reporting/coverage_writer.cpp`
- Modify: `tests/integration/test_ptxpass_plugin.py`
- Modify: `tests/cpu/exact_admission_test.cpp`
- Modify: `tests/cpu/coverage_gate_test.cpp`

- [ ] **Step 1: Write RED manifest/admission assertions**

Manifest schema v3 records the IR hash, instruction table, maximum live future
counts, async-transform version, and empty ambiguity list. Exact admission
requires schema v3 and counts at or below profile limits. Reports include
issued/throttled/waited/drained/leaked counts and reject any nonzero leak.

- [ ] **Step 2: Run RED**

```bash
cmake --build build-sm120-exact --target exact_admission_test \
  coverage_gate_test ptxpass_hbf_plugin -j2
ctest --test-dir build-sm120-exact \
  -R '^(exact_admission|coverage_gate|ptxpass_plugin)$' --output-on-failure
```

- [ ] **Step 3: Implement schema v3 and stable rejections**

Use `future_budget_exceeded`, `async_transform_missing`,
`async_transform_ambiguous`, and `future_leak_detected`. Keep schema-v2
emulation readable, but exact Stage 2 requires v3. Do not infer counts at
runtime when pass evidence is missing.

- [ ] **Step 4: Run GREEN**

```bash
cmake --build build-sm120-exact --target exact_admission_test \
  coverage_gate_test ptxpass_hbf_plugin -j2
ctest --test-dir build-sm120-exact \
  -R '^(exact_admission|coverage_gate|ptxpass_plugin)$' --output-on-failure
```

- [ ] **Step 5: Commit**

```bash
git add src/ptxpass_hbf/plugin.cpp include/hbfsim/coverage.hpp \
  src/cuda_runtime/coverage.cpp src/cuda_runtime/exact_admission.cpp \
  src/reporting/coverage_writer.cpp tests/integration/test_ptxpass_plugin.py \
  tests/cpu/exact_admission_test.cpp tests/cpu/coverage_gate_test.cpp
git commit -m "feat: admit exact ordinary future evidence"
```

### Task 8: Prove ordinary future overlap and bit-exact behavior on SM120

**Files:**
- Create: `benchmarks/cuda/sm120_future_bench.cu`
- Create: `tests/gpu/sm120_future_correctness.cu`
- Create: `tests/integration/test_sm120_future_live.py`
- Modify: `benchmarks/cuda/CMakeLists.txt`
- Modify: `CMakeLists.txt`
- Modify: `docs/sm120-exact-mode.md`

- [ ] **Step 1: Write the live test harness before the benchmark target**

The harness runs native, old synchronous negative control, and future modes for
load/store/atomic, HBM/HBF, timing/capacity hit/miss, predicates, vectors,
branches, source reuse, and fence scopes. It requires byte-identical outputs,
zero unsafe launches/leaks, and timestamps satisfying:

```text
issue < independent_work_end < dependency_wait_end
future_overlap_ns > synchronous_control_overlap_ns
```

It returns 77 only when no CC 12.0 GPU exists; any instrumentation or semantic
failure is nonzero.

- [ ] **Step 2: Run RED**

```bash
cmake --build build-sm120-exact --target sm120_future_bench \
  sm120_future_correctness -j2
python3 tests/integration/test_sm120_future_live.py \
  --build-dir build-sm120-exact
```

- [ ] **Step 3: Implement deterministic kernels and JSON output**

Use ordinary allocated CUDA memory and registered HBF test ranges only. Record
`%globaltimer` issue/independent/wait timestamps, hashes, future counters, and
modeled/host/emulator time. Do not access a raw device or change clocks.

- [ ] **Step 4: Run the Stage 2 proof gate**

```bash
cmake --build build-sm120-exact -j2
ctest --test-dir build-sm120-exact \
  -R '^(ptx_ir|ptx_analysis|shadow_future|device_future_reference|ptx_future_transform|exact_|coverage_|ptxpass_plugin|device_helper_ptx)$' \
  --output-on-failure
python3 tests/integration/test_sm120_future_live.py \
  --build-dir build-sm120-exact
git diff --check
```

Expected: every CPU/static test passes; the live test proves byte identity and
issue-to-use overlap. It does not promote TMA or channel fidelity.

- [ ] **Step 5: Commit**

```bash
git add benchmarks/cuda/sm120_future_bench.cu \
  tests/gpu/sm120_future_correctness.cu \
  tests/integration/test_sm120_future_live.py benchmarks/cuda/CMakeLists.txt \
  CMakeLists.txt docs/sm120-exact-mode.md
git commit -m "test: prove sm120 ordinary future overlap"
```
