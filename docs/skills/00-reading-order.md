# Project knowledge pack: reading order

## Purpose

Provide a source-first entry point for changing HBFSim without reconstructing its runtime contract from function names or historical experiment summaries.

## Scope

This pack describes the audited base `fc829992ecdc3ca68881656722b67a31067c5d33` (B), inspected on branch `eval/eq1-eq4-implementation`. It is ordinary version-controlled documentation, with no agent-specific loader format. The async donor `f4dc28b2671c01939d98e4a968e6fb37b2e364d9` (S) has **no integrated production runtime** in this snapshot; its optional CPU parser reuse is identified separately below. Refresh the affected document and function map when implementation changes.

Current phase-two readiness is separate from B capabilities: [fresh GOLD-0 log](../../results/gold/base/frozen-config/ctest.log) records 42/42 CPU tests passing under the frozen configuration; [pipeline log](../../results/gold/base/eval-pipeline.log) records 20 tests passing. The [donor counterexample log](../../results/gold/async-counterexample/red.log) records expected failing semantic cases. C2 adds optional CPU parsing/analysis, not a production future/helper ABI port; see [PTX documentation](02-ptx-instrumentation.md) and the function map. These run outcomes must be read with their manifests/configuration and do not establish GPU readiness.

## Key concepts

“Implemented” means present in B source. “Existing test” identifies test code, not a newly passing execution. Historical proof remains tied to its recorded SHA, configuration and machine. `INFERRED` and `STATIC_REPRODUCED` are audit labels; numerical results use `MEASURED`, `VALIDATED_MODEL`, `PROJECTED` or `MOCK` under the evaluation schema.

## Important files

Read in this order:

1. [New evaluation entry](../49-new-evaluation-plan.md), then every linked [capability](../49-eval-audit/current-capability-audit.md), [async](../49-eval-audit/async-tma-audit.md), [hardware](../49-eval-audit/hardware-groundtruth-contract.md), [source](../49-eval-audit/source-ledger.md), [figure](../49-eval-audit/figure-plan.md), [execution](../49-eval-audit/execution-plan.md), [claim](../49-eval-audit/claim-gates.md), [schema](../49-eval-audit/result-schema.md) and [matrix](../49-eval-audit/run-matrix.csv) artifact.
2. [Architecture](01-hbfsim-architecture.md) and [runtime function map](../architecture/runtime-function-map.md).
3. [PTX](02-ptx-instrumentation.md), [memory semantics](03-cuda-memory-semantics.md), [async/TMA](04-cuda-async-cpasync-tma.md) and [control ABI](05-device-helper-and-control-abi.md).
4. [MQSim](06-mqsim-online-service.md), [capacity](07-capacity-address-translation.md), and [vLLM/MoE](08-vllm-and-moe-integration.md).
5. [Evaluation protocol](09-evaluation-protocol.md) and [server safety](10-server-experiment-safety.md) before acquiring data.

## Important structs/classes/functions

Start from `hbfsim_context_create`, `hbfsim_register_device`, `hbfsim_map_file`, `transform_ptx`, `__hbfsim_resolve`, `RequestDispatcher::poll_once`, and `MqsimOnlineEngine::run_next_completion`. Their ownership and execution contexts are in the function map. `HbmCache` is a legacy class name for the capacity device-memory frame cache; it does not identify the physical GPU memory technology.

## Call path / data path

Read the [architecture diagrams](01-hbfsim-architecture.md#call-path--data-path) in execution order: workload registration and module transformation, launch admission, GPU resolver, optional host/model service, completion, original instruction. Then follow the separate timing and capacity address paths. A profile name alone does not determine which path executes.

## CPU-side vs GPU-side execution context

Transformation, module provenance, range registration, file I/O and MQSim run on CPUs. The embedded resolver runs in the application's GPU threads. The capacity copy worker runs in the parent application's CUDA context; the daemon does not own that context. Offline replay is CPU execution, even if its trace originated on a GPU.

## Invariants

- Preserve exact source provenance; do not promote a donor or historical design into current capability.
- Registration, launch coverage, control generation, embedded PTX and host/device layout form one contract.
- Do not replace missing evidence with a default, zero, extrapolated measurement or historical PASS.
- The approved phase-two sequence starts with GOLD-0, then bounded implementation, targeted and phase regression, pilot, correctness gate, and formal experiments.

## Supported behavior

The pack documents synchronous supported global load/store instrumentation, range policies, timing models, demand capacity paging, offline model tools and current evaluation contracts. It supplies source navigation and proof boundaries, not a certification of every legal CUDA program.

## Explicitly unsupported behavior

B has no deferred-load future ABI, TensorMap lifecycle model, modeled ordinary `cp.async`, runtime speculative prefetch producer, concurrent decode projection, or thermal-to-request timing feedback. Their existence in a different branch or plan is insufficient.

## Common failure modes

[README](../../README.md) contains historical live results; its opening integration note controls their scope. [Doc47](../47-评估主线设计.md) has an older cost-centered E2 superseded by new EQ2. [Doc48](../48-两种模式的定位与SRAM建模.md) correctly distinguishes addresses but its readahead/profile-only SRAM proposal does not describe B. A 110 GiB sparse logical span is not a 110 GiB physical-read experiment. A copied audit link is not proof that its target artifact exists.

## Tests proving the behavior

This documentation task checks headings, local links and named source symbols only. It does not execute GPU or storage workloads. Existing tests are listed per topic; current build-selected test inventory and outcomes belong in the phase-two GOLD-0 artifacts, not in an inferred fixed test count. The audit's historical 42-test run is explicitly an earlier execution record.

## What not to change casually

Do not rewrite old proof documents to look current. Do not merge S wholesale or copy its three future helpers without the complete ABI dependency closure. Keep optional evaluation tools out of default runtime linkage. No new design approval is implied by a historical plan's old stop or push instruction; the current phase-two user contract governs authorized work.

## Related docs

Historical context consulted by topic (goals, architecture, failure rules, file maps and validation sections; not treated as executed plans):

- [Hybrid spec](../superpowers/specs/2026-08-09-hbfsim-hybrid-design.md) and [plan](../superpowers/plans/2026-08-09-hbfsim-hybrid.md).
- [Capacity spec](../superpowers/specs/2026-08-10-public-capacity-runtime-design.md) and [plan](../superpowers/plans/2026-08-10-public-capacity-runtime.md).
- [Exact-load lifecycle plan](../superpowers/plans/2026-08-10-task6-exact-load-lifecycle.md) and [final hardening plan](../superpowers/plans/2026-08-10-task6-final-hardening.md).
- [vLLM spec](../superpowers/specs/2026-08-10-vllm-hbf-timing-adapter-design.md) and [plan](../superpowers/plans/2026-08-10-vllm-hbf-timing-adapter.md).
- [Triton variant spec](../superpowers/specs/2026-08-11-vllm-triton-variant-binding-design.md) and [plan](../superpowers/plans/2026-08-11-vllm-triton-variant-binding.md).
- [Empirical vmem spec](../superpowers/specs/2026-08-11-cd8p-vmem-tuning-design.md) and [plan](../superpowers/plans/2026-08-11-cd8p-vmem-tuning.md).
- [Base integration plan](../superpowers/plans/2026-09-05-eval-base-integration.md), [integration manifest](../eval/EVAL_BASE_INTEGRATION.md), and [known implementation issues](../重要实现问题以及需补做实验/README.md).
