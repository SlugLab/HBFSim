# Canonical simulator and evaluation entry points

`hbfsim_core`, `hbfsimd`, the CUDA runtime/PTX pass, existing timing adapters and `hbf_mqsim_bench` remain the simulator baseline. Runtime defaults, profiles, control ABI v4, request dispatcher, MQSim queue admission and report schemas are preserved.

The optional evaluation layer has two distinct inputs, not two competing capacity frameworks:

- `adapters/vllm_capacity/trace_replay.py`: frozen expert-route trace + model inventory + profile → capacity-policy counts and analytic/fast/hybrid/MQSim reports.
- `scripts/run_prefetch_accuracy_sweep.py`: generated seeded pages + assumed predictor/compute/media parameters → synthetic arithmetic JSON/CSV. It does not execute capacity runtime prefetch.

`HBFSIM_ENABLE_EVAL_TOOLS` defaults OFF. The prefetch library is linked only to its standalone benchmark/tests. Trace timing links the existing core without changing it. No new evaluation code is called by libhbfsim or hbfsimd.

Legacy source documents describe a larger capture/staging framework and proposed `Snapshot B`/forced-admission experiment. Those are branch-specific or planned; the current supported entry points are listed above. New serving runners, general statistical aggregation, full Qwen capture and thermal feedback are DEFERRED. Use docs/eval as the current status index and doc 47 for the research plan.
