# Evaluation protocol and evidence boundaries

## Purpose

Connect experiment outputs to the four approved evaluation questions, deterministic correctness checks, analytical oracles and hardware controls before formal acquisition.

## Scope

[New evaluation plan](../49-new-evaluation-plan.md) and the approved phase-two implementation contract supersede the old cost-centered E2. This document identifies existing tooling and required gates, without treating planned runners or the run matrix as measured data. Production source snapshot convention: [reading order](00-reading-order.md).

## Key concepts

EQ1 tests observable delay/storage service fidelity; EQ2 tests async issue/completion/consume behavior; EQ3 projects the residency/media-service feasibility boundary; EQ4 tests how sparsity, actual concurrency and prefetch policy change that boundary. Gold consists of deterministic semantic invariants, an analytical oracle and stable hardware controls. A parser success or schema-valid CSV is not a scientific claim gate.

## Important files

[Hardware contract](../49-eval-audit/hardware-groundtruth-contract.md), [claim gates](../49-eval-audit/claim-gates.md), [result schema](../49-eval-audit/result-schema.md), [figure contract](../49-eval-audit/figure-plan.md), [execution plan](../49-eval-audit/execution-plan.md), [condition matrix](../49-eval-audit/run-matrix.csv), [validator](../../scripts/eval/validate_results.py), [renderer](../../scripts/eval/render_figures.py), [mock generator](../../scripts/eval/generate_mock.py), [matrix generator](../../scripts/eval/generate_run_matrix.py), and [pipeline tests](../../scripts/eval/test_eval_pipeline.py).

## Important structs/classes/functions

`read_rows` normalizes CSV/JSON rows; `validate_rows` checks schema and values; `validate_manifest` verifies provenance/raw references and hashes; `load_validated` coordinates strict loading. `metric_points`/`summarize` define renderer grouping; `plot_lines`/`plot_heatmap` and `render` use the same data-driven plotting path for preview and final artifacts. `capacity_geometry`, `replay_cell` and C++ `run_reference` are existing offline metric producers, whose limitations must survive export.

## Call path / data path

Freeze exact source/config/input/device/split → GOLD-0 baseline → minimal mechanism implementation → targeted tests → phase regression → small pilot → correctness and noise gates → authorized formal collection → immutable raw artifacts/manifests → exporter/schema validation → statistics/figure rendering → manual source/claim review. Hardware or mechanism blockers do not prevent unrelated CPU-only work, but dependent experiments remain blocked.

## CPU-side vs GPU-side execution context

Semantic/static tests, MQSim, inventory, offline replay and rendering run on CPU. GPU benchmark stamps and CUDA Events observe physical execution; SSD collectors observe the explicitly selected physical backing path. Model output can still be PROJECTED when computed during a real GPU run. Export the modeled time, host service time, device timing and wall time separately.

## Invariants

- `MEASURED`: directly observed physical quantity; `VALIDATED_MODEL`: frozen calibration plus independent heldout pass within a stated domain; `PROJECTED`: hypothetical/extrapolated/trace-composed output; `MOCK`: layout fixture with mandatory watermark.
- Each row identifies an exact run/condition/replicate/metric. Preserve SHA, profile/input/checkpoint hashes, source function and raw artifact chain; missing/unsupported results belong in status records, not fake zero metrics.
- Freeze heldout before tuning. Six fitted breakpoints prove calibration consistency only.
- EQ1 three arms use matched T0, THW and TSIM; THW must not add the SSD's modeled delay again. Verify physical backing and cache state.
- EQ2 records issue enter/return, wait enter/exit and consume; compare isolated residual with `max(0,D-W)` while separately reporting total exposed stall and host/native/copy overhead.
- Effective fast capacity subtracts resident nonoffload weights, KV, workspace and reserve exactly once. Report requested and whole-object-rounded achieved rho; negative budget is infeasible, not clamped success.
- Sustainable service is completed bytes over the stated steady-state interval. Analytically computed `N * page_bytes / tR` is a projection, not a measured/simulated completion rate.
- Speculative traffic must occupy the same modeled media resources as demand, with promotion, timely/late/useless and eviction accounting. No future-route leakage.

## Supported behavior

Existing schema/mock/matrix/renderer infrastructure, CPU semantic regression, MQSim synthetic arrivals, supplied-inventory serial replay, and standalone offline prefetch model. The strict renderer rejects MOCK and missing/inconsistent required cells. These capabilities make infrastructure reviewable; they do not mean EQ1–EQ4 are complete.

## Explicitly unsupported behavior

No labeling hypothetical HBF as a measured device; GDDR7 as HBM; SSD as HBF ground truth; serial stall sums as live decode time; configured batch size as actual active sequences; synthetic routes as real Qwen routing; or offline prefetch gain as runtime gain. Missing heatmap cells cannot be interpolated into experiments. Absent crossings must remain absent. Statistical confidence intervals do not remove uncertainty in hypothetical model assumptions.

## Common failure modes

Mixing time_scale=100 history with formal time_scale=1; contaminating heldout; comparing unmatched arrivals; deriving E/k from a model name; using layers/requests from one run as independent replicates; relative error against a near-zero noisy delta; rendering only passing points while hiding failures; or adjusting plot logic to improve an unwanted result.

## Tests proving the behavior

[PipelineTests](../../scripts/eval/test_eval_pipeline.py) supplies schema, renderer and rejection tests. Component tests appear in each topic document. The current GOLD-0 run and phase-specific records are separate from this documentation validation: see [base execution](../../results/gold/base/frozen-config/execution.json), [base test log](../../results/gold/base/frozen-config/ctest.log), and [pipeline log](../../results/gold/base/eval-pipeline.log). Formal GPU/storage gates require their own newly acquired raw evidence. Thresholds, repeat counts and validity rules are authoritative in the linked claim/hardware contracts; do not duplicate and drift them in runners.

## What not to change casually

Four-EQ scope, paired input/arrival definitions, evidence classes, heldout split, units, completeness rules, grouping keys and common renderer. Keep optional tools default OFF and new experiment mechanisms isolated until their gate is closed. Complete P0–P8 and the minimum-run readiness report before advancing to the ideal matrix without the prescribed review.

## Related docs

[Server safety](10-server-experiment-safety.md), [async/TMA](04-cuda-async-cpasync-tma.md), [MQSim](06-mqsim-online-service.md), [vLLM/MoE](08-vllm-and-moe-integration.md), [legacy evaluation methodology](../eval/workload_methodology.md), and [new evaluation entry](../49-new-evaluation-plan.md).
