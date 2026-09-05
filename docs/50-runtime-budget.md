# Runtime budget — bounded controls only

The matrix estimates remain planning values. Known-delay GPU controls now run, but
G2 failed and no physical-storage pilot has run. This checkpoint cannot replace
the planning values with validated per-cell
runtime estimates or predict a validated completion date.

| Observed CPU verification | Wall time | Boundary |
|---|---:|---|
| Frozen base build | 21.38 s | CPU configuration, 4 build jobs |
| Frozen base CTest | 30.16 s | 42/42 tests, serial CTest |
| C2–C4 phase regression | 28.70 s | 48/48 tests, serial CTest |
| Media phase regression | 34.49 s | 50/50 tests, serial CTest |
| Causal/media phase regression | 31.25 s | 55/55 tests, serial CTest |
| Clock-correction phase regression | 33.44 s | 55/55 CPU tests |
| Known-delay runner | 3.676 s | 20/20 CPU/compile controls; no GPU launch |
| Storage pair adapter | 8.965 s | 19/19 CPU fixtures with native replay |
| Scheduler final regression | 39.26 s | 46/46 CPU fixture tests |
| Storage collector | 4.122 s | 34/34 CPU/in-memory fixtures; no SSD payload |
| Routing capture closure | 0.195 s | 26/26 adapter CPU fixtures; no real capture |
| Causal prefetch CLI | 0.627 s | 4/4 fixture tests, including three native MQSim policy invocations |
| Three causal policies | 0.670 s total | MOCK inputs, 28 native MQSim requests, no hardware timing |
| Three storage pairing controls | 0.488 / 0.448 / 0.511 s | MOCK ledgers, native replay; 3 requests each, no SSD payload |
| Three media controls | 0.192 / 0.174 / 0.215 s | Fixed QD1 / fixed QD4 / closed-loop QD4; 12 synthetic reads each |
| Existing evaluation pipeline | 4.848 s | 20/20 tests |
| HF metadata verifier compatibility | 2.137 s | 27/27 CPU controls including1MiB read chunks; no real weights or GPU |
| Real HF metadata refresh | 1.822 s | 19,912,432 metadata bytes, no tensor payload |
| HF inventory adapter | 2.455 s | 13/13 CPU controls; published frozen metadata only |
| Real HF frozen adaptation | 2.371 s | 9,625,635-byte normalized inventory; no checkpoint reads |
| Three HF capacity controls | 5.434 s | Hypothetical rho 1/16, 1/2, 1, including frozen validation/publication; no GPU |
| C5 focused closure | 3.17 s | 18/18 CPU/fake-driver/optimized compile checks; no GPU |
| C5 default-off compatibility | 1.64 s | 7/7 checks; no future implementation in OFF helper |
| C5 CPU phase | 25.49 s + 0.35 s | 59/60 then sole concurrent worker-import failure rechecked PASS |
| C6.1 final CPU phase | 25.54 s | 64/64 exact reviewed-source tests; public mode closed |
| C6.1 focused CPU/compile | 6.21 s | 9/9 checks including29 direct and helper-linked PTX fixtures; no GPU |
| HF runtime configuration observer | 0.059 s | 10/10 CPU object controls; no runtime import or GPU |
| HF owned shared-memory scope | 0.005 s | 11/11 fake namespace/descriptor controls; no POSIX allocation |
| HF helper adapter phase | 0.236 s suite / 0.416 s process | 57/57 CPU tests; no model construction or generation |
| HF selected runtime source unit | 0.680 s | 15/15 CPU fixture controls; no runtime imports |
| Real selected runtime source snapshot | 0.289 s | 131 artifacts /7,961,389 bytes plus separate interpreter identity; original-input recheck, no runtime imports |
| HF passive import/cache observer | 0.847 s | 11/11 fake-module controls; no inference imports |
| HF source/import plus adapter phase | 1.528 + 0.455 s suites /2.334 s processes | 26/26 plus57/57 CPU tests; no GPU or model execution |
| HF worker protocol | 0.033 s | 10/10 CPU controls; runtime worker not yet implemented |
| Real checkpoint metadata inventory | See execution JSON | Header/tensor metadata only, no payload transfer |

Evidence: `results/gold/base/frozen-config/execution.json`,
`results/gold/phase-cpu/execution.json`,
`results/gold/phase-cpu/eval-pipeline.json`, and
`results/gold/inventory/execution.json`. Wall time above is not CPU-core time.
The media phase and scheduler execution records are in `results/gold/phase-media/`
and `results/gold/scheduler/`. The newest full CPU phase is
`results/gold/timing-future-unit/c6-emitter/attempt-026/tests.json`. Three-cell inputs, exact commands and wrapper
durations are in `results/gold/concurrent-replay/pilot-3-cell/summary.json`.
The durable causal pilot is in `results/gold/prefetch-replay/pilot-3-policy/`;
its runtime applies only to the small synthetic input.
These tiny CPU controls establish workflow/conservation; they cannot replace
the 60-second planning allowance for realistic storage/decode workloads.
Three real-inventory accounting controls at requested rho 1/16, 1/2 and 1 pass;
these use explicitly hypothetical capacity inputs and are not hardware pilots.

The unchanged minimum planning estimate is 10.44 GPU-hours / 122.02 CPU-core-hours
for 1,477 conditions / 13,105 replicates; the phase-one document suggests a 2x
buffer. These figures exclude implementation, repairs, model transfers and
human review. See [execution plan](49-eval-audit/execution-plan.md).

Known-delay triplets took approximately 8 s each including process/setup/guard
overhead. Those measurements are tied to a failed G2 implementation and do not
update the formal matrix estimate. Exact durations are in the D0 execution logs.

The corrected clock-control attempt stopped at preflight on foreign GPU use;
its 0.315-s refusal is not a benchmark runtime.

Next validated update requires completed implementation gates, an exclusive
read-only SSD path, and passing applicable small pilots.
Do not extrapolate from this CPU-test table to formal GPU/SSD runtime.
