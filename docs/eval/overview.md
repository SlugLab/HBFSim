# Evaluation scope

The canonical plan is [doc 47](../47-评估主线设计.md), selected from docs/eval-mainline 297db90e761784463bcbace4803667d5b9fe0028. Its E1–E4 organization wins over older alternative outlines. It is a research plan, not evidence that every runner exists.

| Canonical experiment | Question | Current entry point / gap |
|---|---|---|
| E1 mechanism fidelity | semantics, coverage, media queueing, held-out hardware parity | CTest, PTX pass, hbf_mqsim_bench; new hardware held-out measurements NOT VERIFIED |
| E2 fidelity cost | reference/fast/hybrid cost and model discrepancy | offline hbf_trace_timing; live wall-time comparison NOT VERIFIED |
| E3 assumptions/projections | HBM-equivalent capacity, parallelism, media latency | capacity trace replay and named profiles; HBF is modeled |
| E4 routed experts/dense | sensitivity to expert union and locality assumptions | supplied real traces or clearly synthetic streams; concurrent serving/dense comparison NOT VERIFIED |

The older capacity plan's EQ1 maps to E1, EQ2 to E2, EQ3 to E3/E4. Its EQ4 thermal study is deferred. Preserve that source as rationale, not a second implementation or competing evaluation chapter. Editorial questions #24–#29 in the source documents remain open; integration does not decide the paper's claims or schedule.

New runtime features are absent by default. Optional offline tools use `HBFSIM_ENABLE_EVAL_TOOLS=ON`. The runtime still uses hybrid's timing/capacity modes, profile loader, MQSim admission, cache and report formats. Historical proof files retain their original dates and hardware limitations.
