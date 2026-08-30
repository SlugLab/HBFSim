# vLLM HBF Timing-Only Adapter Implementation Plan

> **Implementation contract:** Execute this plan against
> `docs/superpowers/specs/2026-08-10-vllm-hbf-timing-adapter-design.md`.
> Preserve capacity fail-closed behavior and do not claim opaque timing
> launches as modeled.

**Goal:** Run the real Qwen3-MoE vLLM workload with finalized model-weight
storages registered as physically backed timing HBF ranges, inject delay into
supported PTX/Triton accesses, and report opaque accesses separately.

**Target stack:** C++20, CUDA 12.9, CMake, Python 3.13, pytest, vLLM 0.15.1,
PyTorch 2.9.1+cu128, Triton 3.5.1, FlashInfer 0.6.1, bpftime, MQSim.

**Build-space rule:** The root filesystem is full. New configure/build/cache
artifacts must use explicit directories under `/dev/shm`; source edits and
small proof documents remain in the worktree.

## Task 1: Make coverage decisions range-policy aware

**Files:**

- Modify: `include/hbfsim/coverage.hpp`
- Modify: `src/cuda_runtime/coverage.cpp`
- Modify: `src/reporting/coverage_writer.cpp`
- Modify: `tests/cpu/coverage_gate_test.cpp`

### Step 1: Write failing policy tests

Add tests that register timing-backed and capacity-unbacked ranges and assert:

- supported instrumented access is modeled for both policies;
- missing identity, uninstrumented module, cubin-only module, unsupported
  operation, and opaque aggregate are allowed with
  `opaque_unmodeled_timing` only when all potentially referenced HBF ranges
  are timing-backed;
- the same paths reject capacity-unbacked access;
- uninspectable launches allow timing-only state and reject any active
  capacity state;
- JSONL output contains the range policy and modeled/unmodeled classification.

Run:

```bash
cmake -S . -B /dev/shm/hbfsim-vllm-cpu \
  -DHBFSIM_ENABLE_CUDA=OFF -DHBFSIM_ENABLE_MQSIM=OFF
cmake --build /dev/shm/hbfsim-vllm-cpu --target coverage_gate_test -j2
ctest --test-dir /dev/shm/hbfsim-vllm-cpu -R '^coverage_gate$' \
  --output-on-failure
```

Expected: new assertions fail against the strict range-only gate.

### Step 2: Implement the smallest policy model

Add a stable range policy enum, store policy on every coverage range, and
return a structured allowed-but-unmodeled decision for safe timing-backed
fallbacks. Do not infer policy from pointer values. Mixed or uninspectable
state chooses the capacity-safe decision.

### Step 3: Verify focused and existing CPU tests

Run:

```bash
cmake --build /dev/shm/hbfsim-vllm-cpu -j2
ctest --test-dir /dev/shm/hbfsim-vllm-cpu --output-on-failure
```

Expected: all CPU tests pass and coverage JSON distinguishes modeled,
unmodeled timing, and rejected capacity decisions.

## Task 2: Extend the launch-gate ABI without weakening legacy callers

**Files:**

- Modify: `include/hbfsim/launch_gate_abi.hpp`
- Modify: `src/cuda_runtime/launch_gate.cpp`
- Modify: `src/cuda_runtime/context.hpp`
- Modify: `src/cuda_runtime/context.cpp`
- Modify: `tests/integration/public_cuda_lifecycle_test.cpp`
- Modify: `tests/integration/test_timing_gate_binding.py`
- Modify: `tests/integration/test_cuda_module_association.py`
- Modify: `tests/gpu/unsupported_kernel.cu`

### Step 1: Write failing ABI and behavior tests

Specify a new API version that passes range policy at registration while
retaining the v2 getter/layout. Test that:

- public timing registration reaches the gate as `TIMING_BACKED`;
- public file mapping reaches it as `CAPACITY_UNBACKED`;
- v2 registration retains the old strict policy;
- opaque timing-backed kernel execution succeeds and is reported unmodeled;
- an equivalent capacity-backed opaque launch is rejected before execution;
- retirement clears policy-tagged ranges transactionally.

### Step 2: Implement v3 plus strict v2 compatibility

Expose v2 and v3 API structs from `hbfsim_launch_gate_get_api`. New HBFSim
contexts prefer v3. If only v2 is available, bind in legacy strict mode; never
convert a v2 range into permissive timing behavior. Pass public range mode to
the v3 gate only after validating it.

### Step 3: Run fake-driver and launch-gate tests

Configure a CUDA build in `/dev/shm` using GCC/G++ 13 and CUDA 12.9, then run
the focused lifecycle, binding, symbol, module-association, and unsupported
kernel tests. Existing capacity rejection behavior must remain green.

## Task 3: Add a narrow native timing-session bridge

**Files:**

- Create: `adapters/vllm/hbfsim_extension.cpp`
- Create: `adapters/vllm/hbfsim_extension.h`
- Create: `tests/integration/vllm_extension_test.cpp`
- Modify: `CMakeLists.txt`

### Step 1: Write a failing native bridge test

Test argument validation, context creation, read-only timing registration,
error propagation, idempotent close, and cleanup after registration failure
with the repository fake CUDA/daemon path.

### Step 2: Implement the C ABI bridge

Build `libhbfsim_vllm_extension.so` linked to `hbfsim_core`. Keep opaque
session ownership inside the bridge and expose only create, register, close,
ABI, and status-string calls. A close retires the complete context.

### Step 3: Verify native tests

Run the extension test under the launch-gate preload and confirm all existing
public lifecycle tests still pass.

## Task 4: Implement the vLLM loader and storage registration

**Files:**

- Create: `adapters/vllm/hbfsim_loader.py`
- Create: `adapters/vllm/pyproject.toml`
- Create: `adapters/vllm/tests/test_hbfsim_loader.py`
- Create: `tests/integration/test_vllm.py`

### Step 1: Write failing Python tests with fake vLLM/PyTorch objects

Cover:

- idempotent `hbfsim` loader registration;
- delegation through a copied underlying `LoadConfig`;
- registration only after the delegate returns its finalized model;
- full-storage discovery and exact alias deduplication;
- overlap and mixed-device rejection;
- transactional close after a partial failure;
- session attachment to the model and explicit close;
- registration manifest contents.

Expected: import or missing-class failures occur before implementation.

### Step 2: Implement plugin and session wrappers

Use `ctypes` for the native bridge. Compose `DefaultModelLoader`; do not copy
vLLM safetensors iteration code and do not mutate installed vLLM. Register a
`vllm.general_plugins` entry point so every process runs the idempotent
registration function.

### Step 3: Verify against installed vLLM 0.15.1

Install the adapter package into a disposable `/dev/shm` target directory,
put it on `PYTHONPATH`, restrict `VLLM_PLUGINS` to the HBF plugin, and prove
that `get_model_loader(LoadConfig(load_format="hbfsim", ...))` returns
`HbfSimModelLoader` in a fresh Python process.

## Task 5: Add reproducible baseline and timing runners

**Files:**

- Create: `adapters/vllm/run.py`
- Create: `adapters/vllm/build.sh`
- Create: `adapters/vllm/compatibility.json`
- Create: `adapters/vllm/README.md`
- Create: `tests/integration/test_vllm_opaque_gate.py`
- Modify: `README.md`

### Step 1: Write failing runner/config tests

Validate exact command construction, rank-specific report directories,
GCC/G++ 13 selection, `/dev/shm` cache routing, compatibility mismatch errors,
baseline/timing argument parity, artifact schema, token comparison, and
proof rejection when modeled access count is zero.

### Step 2: Implement build and run entry points

The build script compiles the native bridge and installs plugin metadata into
an explicit disposable target. The runner supports `baseline` and `timing`,
named profiles, fixed random or explicit prompts, deterministic decoding, and
JSON artifacts. Timing mode invokes `scripts/run_with_bpftime.sh`; baseline
does not preload HBF instrumentation.

### Step 3: Verify non-live runner tests

Run all adapter and integration pytest tests without labeling them as live GPU
proof.

## Task 6: Run the real-GPU Qwen3-MoE proof

**Files:**

- Create: `docs/proofs/2026-08-10-vllm-hbf-timing-live.md`
- Create: proof artifacts under the configured external artifact directory
- Modify: `adapters/vllm/compatibility.json` with observed hashes/versions

### Step 1: Revalidate hardware and software identity

Capture `nvidia-smi`, compute capability, CUDA/compiler versions, vLLM,
PyTorch, Triton, FlashInfer, model config/hash, branch commit, and dirty-state
evidence.

### Step 2: Run identical baseline and timing workloads

Use FlashInfer with GCC/G++ 13 and `/dev/shm` caches. Run the fixed
`4 x (32 + 32)` workload for baseline plus nominal timing. If nominal has
nonzero modeled access, run conservative and aggressive profiles after a
controlled warmup.

### Step 3: Validate artifacts and claims

Require:

- identical token IDs for baseline and every timing run;
- nonzero unique registered storages and bytes;
- nonzero modeled requests for a passing HBF timing proof;
- separate opaque-unmodeled counts;
- no capacity unsafe launch;
- complete coverage, timing, registration, compatibility, and performance
  artifacts.

If the workload runs but modeled requests remain zero, report adapter
execution as passing and HBF delay proof as blocked; do not invent a timing
effect.

## Task 7: Final verification, commit, and push

Run formatting/diff checks, focused tests, the full feasible CPU/CUDA suite,
adapter tests, artifact validation, and `git status`. Update README commands
and proof boundaries to match observed results. Commit implementation and
proof separately where useful, push `hybrid`, and verify
`origin/hybrid` equals local `HEAD`.
