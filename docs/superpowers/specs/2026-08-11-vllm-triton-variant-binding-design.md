# vLLM Triton PTX Variant Binding Design

**Date:** 2026-08-11

**Status:** Approved

## Goal

Turn the real Qwen3-MoE `fused_moe_kernel` launches from
`opaque_unmodeled_timing` into exactly identified, PTX-rewritten HBF launches.
The result must preserve generated token IDs, retain capacity-mode fail-closed
behavior, and attribute delay only to launches whose original Triton function
is bound to the rewritten function from the same PTX variant.

## Problem

Triton 3.5.1 compiles PTX into cubin and passes the cubin to
`cuModuleLoadData`. The existing bpftime fallback associates rewritten CUDA
functions by kernel name. Qwen loads four different PTX specializations named
`fused_moe_kernel`, so a name-only map can overwrite one specialization with
another and is unsafe.

Triton's public runtime load hooks expose the original `CUfunction`, kernel
name, compilation hash, and metadata group containing the PTX path. This is
the earliest stable point where HBFSim can recover the exact PTX that produced
an opaque cubin-loaded function without editing installed Triton or vLLM.

## Exact Binding Model

bpftime records rewritten functions by `(SHA256(original PTX), kernel name)`.
The vLLM adapter installs a Triton kernel-load end hook before model creation.
For every relevant load it reads the PTX from the metadata group and calls a
narrow native binder with:

- the original Triton `CUfunction`;
- the kernel name;
- the exact original PTX bytes.

The binder hashes those bytes, finds the matching rewritten function, and
stores `original CUfunction -> rewritten CUfunction`. The launch interceptor
consults this exact map first. A same-name fallback is legal only when the name
has exactly one rewritten variant; an ambiguous name is never guessed.

The hook writes a JSONL binding manifest containing the Triton hash, PTX
digest, kernel name, original-function identity, result, and source path. It
does not include model data or pointer contents.

## PTX Preparation

bpftime's late-PTX bootstrap consumes a flat directory. A preparation command
recursively scans the configured Triton cache, deduplicates files by PTX
digest, retains all same-name variants, copies them into a digest-addressed
staging directory, and writes a manifest. Timing proof startup points
`BPFTIME_CUDA_LATE_PTX_DIR` at this staging directory.

The HBF BPF object includes a probe section for `fused_moe_kernel`, causing the
existing automatic PTX rewriting pass to instrument the staged variants.

## Safety and Failure Policy

- Exact binding is required before a launch may be classified as modeled.
- Missing, failed, or ambiguous bindings never fall back to an arbitrary
  same-name specialization.
- A timing-backed range may execute through its original function and is
  reported as `opaque_unmodeled_timing`.
- A capacity-unbacked range remains rejected unless exact rewritten coverage
  is proven.
- A proof requested with `require_modeled_accesses` fails if binding or modeled
  access counts are zero.
- Binding state is cleared with the bpftime CUDA attachment/session state so
  stale `CUfunction` values cannot cross process lifetimes.

## Validation

Unit and integration tests cover digest preservation, same-name variants,
exact-map precedence, ambiguity rejection, Triton hook behavior, staging, and
the fused-MoE probe. Existing capacity and opaque-path tests must remain green.

The live gate uses the real Qwen3-30B-A3B vLLM workload and requires:

1. all baseline and timing token IDs are identical;
2. all four observed `fused_moe_kernel` PTX variants bind exactly;
3. modeled HBF access and injected-delay counters are nonzero;
4. ambiguous/name-only substitutions are zero;
5. thermal and CXL-SSD benchmark artifacts report GPU telemetry, Dell DC NVMe
   CD8P SMART telemetry when present, throughput, latency, and profile/config
   identity.

Absence of the target SSD or unavailable SMART fields is reported as a host
capability boundary, never synthesized.
