# Run status — implementation in progress

Formal scheduler snapshot: 2026-09-05T13:15:22.591549+00:00.
Standalone resource update: 2026-09-05T14:13:02Z; new clock controls blocked before payload by foreign GPU use.
The runner's read-only status command reports the entire frozen matrix;
unit-test fixtures, CPU accounting controls, the three-cell media pilot and
three-policy MOCK causal pilot are not formal experiment runs.

| Run state | Replicates |
|---|---:|
| Planned | 20485 |
| Done | 0 |
| Failed attempts | 0 |
| Blocked attempts | 0 |
| Contaminated attempts | 0 |
| Remaining | 20485 |

There are 2848 conditions; the reviewed minimum contains 1477
conditions / 13105 replicates. Conditions have not been
launched to manufacture blocked-attempt counts. Known-delay G2 and shared-root-SSD
blockers apply to readiness, while missing handlers/gold receipts prevent launch.
See [blockers](50-implementation-blockers.md).

| Resource class | Planned replicates |
|---|---:|
| CPU_ONLY | 10210 |
| GPU_EXCLUSIVE | 6635 |
| GPU_SHARED_SAFE | 120 |
| GPU_STORAGE_EXCLUSIVE | 960 |
| STORAGE_EXCLUSIVE | 2560 |

Evidence: `results/gold/scheduler/status-checkpoint.json`.
Command: `python scripts/eval/run_matrix.py --status` from this checkout.
No minimum or ideal matrix has started. Actual standalone known-delay gold
controls are under `results/gold/known-delay/gpu-controls/`; their DONE/failed/
contaminated acquisition states are not formal scheduler counts. The corrected
implementation has a separate blocked preflight in
`results/gold/known-delay/clock-controls/d0-k64-r1/`; no new GPU timing was acquired.

Standalone CPU update, 2026-09-05T15:58Z: real HF frozen inventory adaptation
and three hypothetical capacity-accounting controls passed under
`results/gold/hf-inventory-adapter/`. These are separate from matrix DONE counts;
HF route/projection integration and real capture remain pending.

Standalone CPU update, 2026-09-05T16:58Z: HF configuration/ownership helpers and
existing adapter regressions pass 57/57 tests. Evidence:
`results/gold/hf-routing-runner/helpers-phase-attempt-001/`. No GPU probe, model
arm, storage payload or formal matrix cell was run by this phase.

Standalone CPU update,2026-09-05T17:46Z: selected installed runtime sources
were frozen and rechecked (131 artifacts /7,961,389 source-metadata bytes). The
source unit passes15/15 tests and metadata compatibility27/27. Evidence:
`results/gold/hf-routing-runner/runtime-sources-real-attempt-001/`. This is
input identity evidence; no inference import, model arm or formal cell ran.

Standalone CPU/compile update,2026-09-05T17:47Z: private C6.1 emission passes
independent reviews,64/64 final CPU regression (25.54s) and29 direct/helper-linked
PTX assembly fixtures. Handoff:
`results/gold/timing-future-unit/c6-emitter/handoff/attempt-005/`. Public future
admission remains closed pending C6.2; no GPU gold or formal cell was run.
