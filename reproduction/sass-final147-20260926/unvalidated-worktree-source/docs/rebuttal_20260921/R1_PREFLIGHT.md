# R1 dynamic access accounting preflight

Experiment ID: `REBUTTAL-20260921-R1-v1`

Status: `AUTHORIZED_PREPARATION`; GPU execution remains gated on the model,
instrumentation, exclusive-GPU, and metadata checks below.  The user's
2026-09-21 rebuttal instruction authorizes R1--R3 and the five page-size
checks on the single giga RTX 5090.  This record is the run record requested
by the user; it does not modify or replace an older project ledger.

## Question and claim

The historical 24/2304 value is a launch-level statistic.  R1 measures a
separate denominator at the supported PTX load sites for one real fused-MoE
request: dynamic supported accesses, their byte spans, intersection with one
registered final expert-weight storage, modeled admission, terminal service,
and output correctness.  It does not claim whole-program memory coverage.

## Frozen environment

- Host: giga `threadripper`; GPU UUID
  `GPU-45044e90-a553-930c-b950-ba660acb37fc`, one RTX 5090 only.
- Source base: `eabc5c2c0820ac0d84c2f16ea3460b219f11ff83` in the isolated
  `rebuttal/access-accounting-20260921` worktree.
- Driver 610.57.04; vLLM `0.19.2rc1.dev134+gfe9c3d6c5.cu130`;
  torch `2.11.0+cu130`; CUDA runtime 13.0; Triton 3.6.0;
  flashinfer-python/cubin 0.6.8.post1.  No package, driver, CUDA, Triton, or
  vLLM upgrade is permitted.
- Candidate: `allenai/OLMoE-1B-7B-0924`, immutable revision
  `6d84c48581ece794365f2b8e9cfb043c68ade9c5`, BF16,
  `OlmoeForCausalLM`, 16 layers, 64 experts, top-8.  The installed vLLM
  implementation calls `FusedMoE`.  Actual kernel/PTX evidence is still a
  mandatory runtime gate.

## Workload and controls

One deterministic request per arm: batch 1, 128 input token IDs, 16 output
tokens, seed 0, greedy decoding, eager mode, TP=1, PP=1, no speculative
decoding, prefix cache, CPU offload, or swap.  All arms use identical token
IDs, finalized storage, registered interval, build, and profile.

Arms are native, instrumented zero-delay, and HBF injected.  HBF injected uses
the repository's existing nominal profile; it will not be tuned to enlarge an
effect.  R1 registers exactly 16 KiB from a finalized storage proved by the
actual fused-MoE kernel binding.  Packed/repacked weights, scales, and metadata
are treated according to the storage actually consumed, never inferred from a
parameter name alone.

## Counter contract

Accounting is default-off and request-scoped.  It snapshots the actual bound
CUDA modules after GPU work completes.  It never loads a second PTX module to
read zeroed counters.  The measured denominator is explicitly named
`resolve-instrumented supported-site dynamic accesses` unless manifests prove
a broader set.

Each executing lane counts one logical supported access and its original byte
width.  In-range bytes are the byte-span intersection with the registered
range; a cross-boundary access is not counted at full width.  Modeled counts
begin only after real admission to the service path.  Ready completions,
failed service, unsupported in-range accesses, native out-of-range accesses,
translation failure, trace overflow, and trace drop remain separate.  Retry or
transport splitting must not create another logical access count.

Opaque modules list kernel/module identities.  Possible overlap is `UNKNOWN`
unless argument or independent evidence proves otherwise.  Unknown paths are
not placed in the out-of-range denominator.

## Resource and interference gates

The existing thermal campaign is CPU-only and remains untouched.  The current
dense vLLM Kubernetes workload may be paused only after build and non-GPU
checks are ready; record its deployment/controller, pod identity, command, and
pre-stop GPU state so it can be restored.  Stop only that workload, never k3s
or unrelated pods.  Before every GPU arm record UUID, memory, temperature,
power, and processes.  Require at least 6 GiB free after model load; otherwise
stop and record `BLOCKED_BY_MEMORY_OR_MODEL`.

## Acceptance and aborts

Accept R1 only when a real OLMoE request produces a fused-MoE kernel with exact
PTX/binding evidence, the supported denominator and in-range denominator are
nonzero, modeled and completed accounting closes without failed/unsupported
loss, overflow/drop are zero, and terminal status plus output token IDs and
checksum match native and zero-delay arms.  Snapshot failure is
`INCOMPLETE`, never zero.  A cubin-only/opaque fused-MoE path, OOM, storage
identity ambiguity, backend mismatch, or non-closing accounting blocks R1.
R2 and R3 do not run while R1 is blocked, per the user's requested order.

