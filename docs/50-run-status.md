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
