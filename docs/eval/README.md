# Evaluation on eval_base

`eval_base` is the reproducible research base derived from `hybrid`; it does not replace it. The canonical research plan is [the 2026-09-08 EQ1–EQ4 plan](../49-new-evaluation-plan.md); doc 47 is historical. Historical auxiliary documents remain source provenance; current implementation status is defined here.

- [Overview and old/new experiment mapping](overview.md)
- [Workload methodology and provenance](workload_methodology.md)
- [Aggregation and schema boundaries](aggregation.md)
- [Qwen capacity definitions and metrics](capacity_qwen3_30b_a3b.md)
- [Coverage and fail-closed behavior](capacity_coverage.md)
- [Thermal coverage](thermal_coverage.md) and [integration assessment](thermal_integration_assessment.md)
- [Canonical simulator/tool entry points](hbf_eval_only_simulator_and_framework.md)
- [Build setup](runbooks/environment_setup.md) and [mechanism validation](runbooks/mechanism_validation.md)
- [Integration manifest, branches and test evidence](EVAL_BASE_INTEGRATION.md)

Offline tools are optional and off by default. Runtime prefetch and thermal reliability are deferred. GPU baseline parity: NOT VERIFIED. Old experiments must retain their original source hashes; none become new measurements by being documented here.

Current campaign source is `eval/eq1-eq4-implementation@254d65a66279fbaffc5c185d04fbe41dc8dbba44` plus the recorded document patch. See [current state](../49-eval-audit/current-state-20260908.md), [execution](../49-eval-audit/execution-plan.md) and [matrix](../49-eval-audit/run-matrix.csv). Device-only thermal history remains separate from current runtime integration. Old status below is not a live device check.
