# Evaluation on eval_base

`eval_base` is the reproducible research base derived from `hybrid`; it does not replace it. The canonical research plan is [doc 47 E1–E4](../47-评估主线设计.md). Historical auxiliary documents remain source provenance; current implementation status is defined here.

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
