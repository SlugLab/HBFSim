# SM120 Stage 3 TensorMap and TMA Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add generation-safe TensorMap provenance plus TMA load/store, mixed HBM/HBF tile expansion, mbarrier, bulk-group, unicast, and multicast shadow completion.

**Architecture:** CUDA-driver interposition records structured TensorMap encoder inputs and immutable descriptor hashes. PTX IR recognizes descriptor updates, fences, TMA issues, barrier phases, and bulk groups. A CPU-tested tile expander and async-object state machine drive device helpers that delay observation without allowing shadow completion to precede native completion.

**Tech Stack:** C++20, CUDA 13 Driver API, PTX 9.0 TMA/mbarrier instructions, CUDA device C++, CMake/CTest, Python fake-driver and real-GPU tests.

---

## Stage 3 invariants

- Opaque descriptor bits are hashed, never reverse-engineered.
- Every descriptor has structured provenance and a monotonic generation.
- Unknown, stale, partially constructed, or unfenced descriptors reject exact.
- Mixed HBM/HBF tiles are split by runtime ranges and byte conservation.
- Native and shadow mbarrier conditions are conjunctive.
- Multicast source work may be shared, but target materialization/completion is independent.
- Bulk `.read` wait and full wait have distinct semantics.
- Stage 3 still uses uncalibrated channel labels and cannot complete exact timing admission.

### Task 1: Define TensorMap records and a generation-safe registry

**Files:**
- Create: `include/hbfsim/tensormap.hpp`
- Create: `src/cuda_runtime/tensormap.cpp`
- Create: `tests/cpu/tensormap_registry_test.cpp`
- Modify: `CMakeLists.txt`

- [ ] Write RED tests for tiled/im2col/wide records, raw 128-byte hash lookup,
  replace-address generation, copy/update/fence ordering, duplicate bytes with
  distinct generations, context/device isolation, unload/reset invalidation,
  and concurrent readers.
- [ ] Run `cmake --build build-sm120-exact --target tensormap_registry_test -j2`
  and verify the API is missing.
- [ ] Implement:

```cpp
enum class TensorMapMode { Tiled, Im2col, Im2colWide };
struct TensorMapShape {
    std::uint32_t rank;
    std::array<std::uint64_t, 5> global_dim, global_stride;
    std::array<std::uint32_t, 5> box_dim, element_stride;
};
struct TensorMapRecord {
    std::array<std::byte, 128> descriptor;
    std::array<std::byte, 32> descriptor_sha256;
    std::uintptr_t base_address;
    std::uint64_t generation;
    TensorMapMode mode;
    TensorMapShape shape;
    std::uint32_t element_type, interleave, swizzle, l2_promotion, oob_fill;
};
class TensorMapRegistry {
  public:
    bool publish(std::uintptr_t context, int device, TensorMapRecord);
    std::optional<TensorMapRecord> lookup(std::uintptr_t context, int device,
                                          std::span<const std::byte, 128>) const;
    bool replace_address(std::uintptr_t context, int device,
                         std::span<const std::byte, 128> before,
                         std::span<const std::byte, 128> after,
                         std::uintptr_t new_address);
    void erase_context(std::uintptr_t context); void clear();
};
```

- [ ] Run `ctest --test-dir build-sm120-exact -R '^tensormap_registry$' --output-on-failure`.
- [ ] Commit `feat: track generation-safe TensorMap provenance`.

### Task 2: Interpose CUDA 13 TensorMap host APIs and query variants

**Files:**
- Create: `src/cuda_runtime/tensormap_interpose.cpp`
- Create: `tests/integration/fake_cuda_tensormap.cpp`
- Create: `tests/integration/test_tensormap_interposition.py`
- Modify: `src/cuda_runtime/launch_gate.cpp`
- Modify: `tests/integration/test_cuda_lookup_interposition.py`
- Modify: `tests/integration/test_launch_gate_symbols.py`
- Modify: `CMakeLists.txt`

- [ ] Write RED fake-driver tests for `cuTensorMapEncodeTiled`,
  `cuTensorMapEncodeIm2col`, `cuTensorMapEncodeIm2colWide`, and
  `cuTensorMapReplaceAddress`, including direct calls, `cuGetProcAddress`,
  `_v2`, ByVersion, failure/no-publish, context reset, and output-byte mutation.
- [ ] Run the new Python test and confirm missing exported wrappers.
- [ ] Implement wrappers with the installed CUDA 13 header signatures. Call the
  real function first; only `CUDA_SUCCESS` publishes the exact structured
  inputs plus the 128 returned bytes. Query interposition returns the same
  wrappers and never exports internal registry pointers.
- [ ] Run `ctest --test-dir build-sm120-exact -R '^(tensormap_interposition|cuda_lookup_interposition|launch_gate_symbols)$' --output-on-failure`.
- [ ] Commit `feat: capture CUDA TensorMap provenance`.

### Task 3: Parse TMA, TensorMap updates, barriers, and bulk groups

**Files:**
- Create: `src/ptxpass_hbf/ptx_async_op.hpp`
- Create: `src/ptxpass_hbf/ptx_async_op.cpp`
- Create: `tests/cpu/ptx_async_op_test.cpp`
- Create: `tests/fixtures/ptx/tma_sm120.ptx`
- Modify: `src/ptxpass_hbf/ptx_ir.cpp`
- Modify: `CMakeLists.txt`

- [ ] Write RED tables for every accepted `.1d`–`.5d` tiled TMA direction,
  gather4/scatter4, im2col/wide, CTA/cluster destination, cta_group 1/2,
  multicast masks, cache hints, reductions, `tensormap.replace.tile.*`,
  `fence.proxy.async`, TensorMap fence/acquire forms, mbarrier init/arrive/
  expect_tx/test_wait/try_wait/invalidate, commit_group, wait_group, and
  wait_group.read. Reject malformed operand counts and unsupported modes.
- [ ] Run RED target.
- [ ] Implement typed variants:

```cpp
struct TmaInstruction { TmaDirection direction; TensorMode mode;
  std::uint32_t dimensions; CompletionKind completion; bool multicast;
  std::vector<std::string> coordinates; std::string descriptor, barrier;
};
struct BarrierInstruction { BarrierOp op; std::string address, phase;
  std::optional<std::uint32_t> expected_bytes; };
struct BulkGroupInstruction { BulkGroupOp op; std::uint32_t pending_limit; };
```

- [ ] Run parser tests plus CUDA 13 assembly of the golden fixture.
- [ ] Commit `feat: parse SM120 TensorMap and TMA PTX`.

### Task 4: Analyze descriptor and async-object flow

**Files:**
- Create: `src/ptxpass_hbf/async_object_analysis.hpp`
- Create: `src/ptxpass_hbf/async_object_analysis.cpp`
- Create: `tests/cpu/async_object_analysis_test.cpp`
- Modify: `CMakeLists.txt`

- [ ] Write RED CFG cases for host parameter descriptor, global/shared copy,
  replace fields, release/acquire fences, stale use, phase reuse, ambiguous
  barrier aliases, multicast target keys, uncommitted/committed groups, and
  profile async-object budget excess.
- [ ] Run `cmake --build build-sm120-exact --target async_object_analysis_test -j2`
  and verify compilation fails because the analysis API is missing.
- [ ] Implement generation flow and stable rejection reasons:
  `unknown_tensormap`, `stale_tensormap_generation`,
  `tensormap_fence_missing`, `ambiguous_mbarrier_phase`,
  `bulk_group_unbalanced`, `async_object_budget_exceeded`.
- [ ] Run `ctest --test-dir build-sm120-exact -R '^async_object_analysis$' --output-on-failure`
  and require one passing test with no diagnostics.
- [ ] Commit `feat: analyze TMA async object lifetimes`.

### Task 5: Expand TensorMap tiles and split runtime address spaces

**Files:**
- Create: `include/hbfsim/tma_tile.hpp`
- Create: `src/cuda_runtime/tma_tile.cpp`
- Create: `tests/cpu/tma_tile_test.cpp`
- Create: `tests/cpu/tma_tile_property_test.cpp`
- Modify: `CMakeLists.txt`

- [ ] Write RED reference vectors for dimensions 1–5, OOB fill, interleave,
  swizzle, gather/scatter, im2col/wide-im2col, signed coordinates, page
  crossings, and a tile split across HBM plus two registered HBF ranges.
- [ ] Add deterministic randomized tests requiring in-bounds segments,
  sorted non-overlap, byte conservation, and identical reconstruction.
- [ ] Run `cmake --build build-sm120-exact --target tma_tile_test tma_tile_property_test -j2`
  and verify the missing tile API causes compilation failure.
- [ ] Implement:

```cpp
enum class SegmentSpace { Hbm, Hbf, OobFill };
struct TileSegment { SegmentSpace space; std::uintptr_t global_address;
  std::uint64_t logical_offset, destination_offset, bytes;
  std::uint32_t range_id; };
std::vector<TileSegment> expand_and_split(const TensorMapRecord&,
    std::span<const std::int32_t> coordinates,
    const ImmutableRangeSnapshot&, TmaDirection);
```

  Use checked arithmetic throughout and reject any descriptor/range overflow.
- [ ] Run `ctest --test-dir build-sm120-exact -R '^tma_tile(_property)?$' --output-on-failure`
  and run `ASAN_OPTIONS=detect_leaks=1 build-sm120-exact/tma_tile_property_test`.
- [ ] Commit `feat: split TMA tiles across HBM and HBF`.

### Task 6: Implement mbarrier and bulk-group reference state machines

**Files:**
- Create: `include/hbfsim/tma_async.hpp`
- Create: `src/cuda_runtime/tma_async.cpp`
- Create: `tests/cpu/tma_async_test.cpp`
- Modify: `CMakeLists.txt`

- [ ] Write RED tests for unique `(cluster,target,barrier,phase)` keys,
  expected-byte accounting, arrivals, invalidation/reuse, native-before-shadow,
  shadow-before-native, terminal faults, source-read wait, full destination
  wait, empty groups, pending N, and store source reuse.
- [ ] Run `cmake --build build-sm120-exact --target tma_async_test -j2`
  and verify the missing state-machine API causes compilation failure.
- [ ] Implement explicit `NativePending`, `ShadowPending`, `Ready`, `Faulted`,
  and `Consumed` transitions; `ready` is true only when native and shadow bits
  are both set. Snapshot store bytes into bounded per-CTA staging records.
- [ ] Run `ctest --test-dir build-sm120-exact -R '^tma_async$' --output-on-failure`.
- [ ] Commit `feat: model TMA barriers and bulk groups`.

### Task 7: Publish TensorMap records to the device shadow registry

**Files:**
- Modify: `src/host_service/control_layout.hpp`
- Modify: `src/cuda_runtime/context.hpp`
- Modify: `src/cuda_runtime/context.cpp`
- Modify: `src/cuda_runtime/device/hbf_device.cuh`
- Modify: `src/cuda_runtime/device/hbf_device.cu`
- Create: `tests/cpu/device_tensormap_reference_test.cpp`
- Modify: `tests/cpu/protocol_layout_test.cpp`
- Modify: `CMakeLists.txt`

- [ ] Write RED tests for bounded descriptor slots, generation publication,
  SHA lookup, concurrent reader consistency, device replace/update, and stale
  slot invalidation.
- [ ] Run `cmake --build build-sm120-exact --target device_tensormap_reference_test -j2`
  and verify the shared descriptor ABI is absent.
- [ ] Extend the size/version-tagged shared control with immutable-copy slots
  and acquire/release generation publication. Device update helpers accept only
  pass-proven replace/fence transitions and never decode raw descriptor bits.
- [ ] Run `ctest --test-dir build-sm120-exact -R '^(device_tensormap_reference|protocol_layout|device_helper_abi)$' --output-on-failure`.
- [ ] Commit `feat: publish TensorMap shadow generations`.

### Task 8: Transform TMA issue and completion paths

**Files:**
- Create: `src/ptxpass_hbf/tma_transform.hpp`
- Create: `src/ptxpass_hbf/tma_transform.cpp`
- Create: `tests/cpu/ptx_tma_transform_test.cpp`
- Modify: `src/ptxpass_hbf/transform.cpp`
- Modify: `src/cuda_runtime/device/hbf_device.cu`
- Modify: `CMakeLists.txt`

- [ ] Write RED golden tests requiring issue helpers before native TMA,
  descriptor-generation checks, independent instructions between issue/wait,
  conjunctive mbarrier polling, distinct `.read`/full group waits, source
  snapshots, and no native access to unbacked mixed-capacity addresses.
- [ ] Run `cmake --build build-sm120-exact --target ptx_tma_transform_test -j2`
  and verify TMA helpers are absent from transformed PTX.
- [ ] Emit `__hbfsim_tma_issue`, `__hbfsim_tma_barrier_poll`,
  `__hbfsim_tma_barrier_wait`, `__hbfsim_tma_commit_group`, and
  `__hbfsim_tma_wait_group`. Timing mode executes native TMA then delays only
  observed completion. Capacity mode rewrites resident descriptors or performs
  bounded software materialization at completion.
- [ ] Run `ctest --test-dir build-sm120-exact -R '^ptx_tma_transform$' --output-on-failure`
  and assemble every emitted fixture with `/usr/local/cuda-13.0/bin/ptxas --gpu-name=sm_120`.
- [ ] Commit `feat: instrument TMA issue and completion`.

### Task 9: Implement multicast fanout and per-target completion

**Files:**
- Modify: `src/cuda_runtime/tma_async.cpp`
- Modify: `src/cuda_runtime/device/hbf_device.cu`
- Modify: `tests/cpu/tma_async_test.cpp`
- Create: `tests/cpu/tma_multicast_test.cpp`
- Modify: `CMakeLists.txt`

- [ ] Write RED masks for 1, 2, 4, 8, and 16 target CTAs, shared HBF source
  read, independent target delays/faults, barrier addresses, phases, and local
  destination offsets. Require one source byte count but one materialization
  and terminal completion per target.
- [ ] Run `cmake --build build-sm120-exact --target tma_multicast_test -j2`
  and verify per-target fanout behavior is missing.
- [ ] Implement fanout keyed by `(cluster_id,target_ctarank,mbarrier,phase)`;
  never let one target's native completion satisfy another target.
- [ ] Run `ctest --test-dir build-sm120-exact -R '^(tma_async|tma_multicast)$' --output-on-failure`.
- [ ] Commit `feat: model TMA multicast fanout`.

### Task 10: Bind Stage 3 evidence to exact admission and reports

**Files:**
- Modify: `src/ptxpass_hbf/plugin.cpp`
- Modify: `include/hbfsim/coverage.hpp`
- Modify: `src/cuda_runtime/exact_admission.cpp`
- Modify: `src/reporting/coverage_writer.cpp`
- Modify: `tests/integration/test_ptxpass_plugin.py`
- Modify: `tests/cpu/exact_admission_test.cpp`
- Modify: `tests/cpu/coverage_gate_test.cpp`

- [ ] Write RED schema-v4 manifest assertions for TensorMap parameters,
  descriptor/update/barrier/group instruction IDs, maximum live async objects,
  TMA modes/dimensions/multicast masks, and empty ambiguity list. Reports
  require HBM/HBF/OOB bytes, fanout targets, barrier/group wait, and stale
  generation counts.
- [ ] Run `cmake --build build-sm120-exact --target exact_admission_test coverage_gate_test ptxpass_hbf_plugin -j2`
  followed by `ctest --test-dir build-sm120-exact -R '^(exact_admission|coverage_gate|ptxpass_plugin)$' --output-on-failure`
  and verify schema-v4 assertions fail.
- [ ] Implement schema v4 and reject exact with `tma_transform_missing`,
  `tensormap_provenance_missing`, `tma_async_object_leak`, or
  `mixed_tile_unproven`.
- [ ] Re-run the same CTest filter and require all three tests to pass.
- [ ] Commit `feat: admit exact TMA provenance`.

### Task 11: Prove TMA functional correctness and overlap on SM120

**Files:**
- Create: `benchmarks/cuda/sm120_tma_bench.cu`
- Create: `tests/gpu/sm120_tma_correctness.cu`
- Create: `tests/integration/test_sm120_tma_live.py`
- Modify: `benchmarks/cuda/CMakeLists.txt`
- Modify: `CMakeLists.txt`
- Modify: `docs/sm120-exact-mode.md`

- [ ] Write the harness first. Cover tiled 1D–5D, im2col/wide, load/store,
  unicast/multicast masks, HBM/HBF/mixed, capacity hit/miss, OOB, descriptor
  replace/copy/fence, phase reuse, source reuse, and cluster ranks. Require
  every output byte equal native reference, zero leaks/stale generations, and
  issue-to-wait overlap.
- [ ] Run RED for absent targets.
- [ ] Implement deterministic kernels and JSON artifacts using only allocated
  buffers and registered test ranges.
- [ ] Run:

```bash
cmake --build build-sm120-exact -j2
ctest --test-dir build-sm120-exact \
  -R '^(tensormap_|ptx_async_op|async_object_analysis|tma_tile|tma_async|device_tensormap_reference|ptx_tma_transform|exact_|coverage_|ptxpass_plugin)$' \
  --output-on-failure
python3 tests/integration/test_sm120_tma_live.py --build-dir build-sm120-exact
git diff --check
```

- [ ] Commit `test: prove sm120 TMA semantics`.
