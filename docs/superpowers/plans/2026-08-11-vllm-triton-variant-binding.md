# vLLM Triton PTX Variant Binding Implementation Plan

> **Implementation contract:** Execute this plan against
> `docs/superpowers/specs/2026-08-11-vllm-triton-variant-binding-design.md`.

**Goal:** Precisely bind every original Triton `fused_moe_kernel` function to
the rewritten function generated from the same PTX bytes, then prove nonzero
modeled HBF accesses on the real Qwen/vLLM workload.

**Build-space rule:** Put generated builds, caches, models, and large proof
artifacts under `/dev/shm`; keep source and compact proof summaries in Git.

## Task 1: Specify the binding contract with failing tests

**Files:**

- Modify: `tests/integration/test_bpftime_patch.py`
- Modify: `tests/integration/test_bpftime_probe.py`
- Create: `adapters/vllm/tests/test_triton_binding.py`

Add assertions for a digest-and-name variant map, an original-function exact
map, exact lookup before fallback, ambiguous-name rejection, recursive PTX
staging, native hook inputs, manifest output, and the fused-MoE BPF section.
Run the focused pytest set and retain the expected red result before editing
production code.

## Task 2: Implement variant-safe bpftime state

**Files:**

- Modify: `patches/bpftime/0001-exact-module-load-provenance.patch`

Extend the maintained bpftime patch to record rewritten functions by original
PTX SHA256 and kernel name, export a C binder for an original `CUfunction`, and
use the exact original-function map at CUDA driver launch. Track the number of
variants per name and disable ambiguous name fallback. Clear all new maps on
detach. Validate that the patch applies to the pinned bpftime source.

## Task 3: Implement PTX staging and the Triton hook

**Files:**

- Create: `adapters/vllm/triton_binding.py`
- Create: `adapters/vllm/prepare_triton_ptx.py`
- Modify: `adapters/vllm/run.py`
- Modify: `adapters/vllm/README.md`
- Modify: `tests/gpu/coverage_probe.bpf.c`

Install Triton's kernel-load end hook before `LLM` construction. Bind exact
functions through the exported ABI, retry only while late PTX bootstrap is
still becoming ready, and emit a rank-local JSONL manifest. Add a deterministic
stager that preserves all PTX variants and add the `fused_moe_kernel` probe.
Timing proof configuration must fail early when its staging directory is
missing or empty.

## Task 4: Rebuild and verify non-live behavior

Build a fresh patched bpftime tree and HBFSim CUDA tree under `/dev/shm`. Run
the adapter pytest suite, focused patch/probe tests, the full CPU suite, and the
feasible CUDA suite. Re-run capacity and opaque-path tests to ensure the safety
boundary did not regress.

## Task 5: Run live real-GPU proof and benchmark

Capture live NVIDIA identity, software versions, branch/diff state, and NVMe
inventory. Stage the existing Triton PTX cache, run deterministic Qwen3-30B-A3B
baseline and timing workloads, then validate token equality and nonzero modeled
access/delay. Run named-profile comparisons while sampling `nvidia-smi` and
`smartctl` for the Dell DC NVMe CD8P when present. Record throughput, latency,
temperature, modeled/opaque counts, and exact commands in compact proof docs.

If exact binding succeeds but modeled accesses stay zero, stop at that blocker
and do not label the run an HBF timing proof.

## Task 6: Document, commit, and push

Update the high-level README and proof index with the observed claim boundary.
Run final verification from fresh command output, commit implementation and
proof artifacts, push `hybrid`, and verify local `HEAD` equals
`origin/hybrid`.
