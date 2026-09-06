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

Standalone CPU update,2026-09-05T18:10Z: the passive runtime import/cache
observer passes11 tests and independent reviews. The related source/import
controls pass26/26 and adapter regression57/57. Evidence:
`results/gold/hf-routing-runner/imports-phase-attempt-001/`. No real vLLM/Torch
import, model arm or formal matrix cell was launched.

Standalone CPU update,2026-09-05T18:42:36.162441+00:00: owned HF request
composition passes both reviews and95 related tests (38 worker/source/import,
57 adapters). Evidence: `results/gold/hf-routing-runner/loaded-arm-phase-attempt-001/`.
This uses CPU runtime fixtures, creates no real model arm or POSIX segment, and
changes no formal matrix count. Complete guarded triplet remains unfinished.

Standalone CPU update,2026-09-05T18:49:21Z: the optional MoE source extension
passes18 tests/both reviews; the real133-artifact source snapshot and current
recheck pass with all131 base buffers unchanged. Evidence:
`results/gold/hf-routing-runner/runtime-sources-tuning-real-attempt-001/`.
No selected tuning JSON, loaded kernel state, model arm or matrix cell was acquired.

Standalone CPU update,2026-09-05T19:05:18Z: selected tuning inputs and HF
provenance correction pass both reviews and107 related tests. Real input
acquisition freezes the selected packaged JSON's absence and rechecks the133
runtime-source artifacts. Device name is bound to the older14:13 probe; no new
GPU probe, model arm, effective kernel observation or matrix cell occurred.
Evidence: `results/gold/hf-routing-runner/tuning-inputs-real-attempt-001/` and
`tuning-phase-attempt-001/` in the same parent directory.

Standalone CPU/compile update,2026-09-05T19:28:00Z: C6.2 is committed at
`49ee96b` after both reviews. All64 CPU checks close:63 initial passes plus a
reviewed test-selector correction and one focused recheck. A new default-OFF
build passes18/18, with unchanged C5 helper bytes. Actual-plugin typed17 and
loader49 controls are retained. Evidence:
`results/gold/timing-future-unit/c6-unit/closure-attempt-001/`.
Only explicit complete ON builds admit the bounded ordinary TIMING subset;
optimized SASS dependency and GPU gold remain open. No matrix count changed.

Standalone CPU update,2026-09-05T19:42:10Z: optional owned bootstrap startup
control passes both reviews, three new subprocess tests and46/46 scheduler
regressions (38.819s). Commit `3f62bf1`; evidence:
`results/gold/hf-routing-runner/bootstrap-phase-attempt-001/`.
Default argv is unchanged; true skips intermediate Python site hooks while
preserving identity acknowledgement and owned cleanup. No real HF arm or formal
matrix cell ran.

Standalone CPU update,2026-09-05T19:53:41Z: bounded frozen route decoding is
committed at `0a7619b`, with both reviews and29/29 related CPU controls in3.536s.
Evidence: `results/gold/hf-routing-runner/route-array-phase-attempt-001/`.
No model arm, live routing, GPU probe or formal matrix cell was run.

Standalone hardware update,2026-09-06: ten D0 controls complete; first D500 control is INVALID_GOLD_GATE under unchanged G2. Local waits measure512ns for500ns, while D0 run-mean SD is121.024us/access and D500 chain mean absolute error is280.364us/access. D5000/D20000 did not run; all owned children exited. Evidence: `results/gold/known-delay/resume-clock-controls-attempt-001/`. These are standalone gold controls; formal DONE remains0.

Standalone CPU update,2026-09-06: private loaded-MoE tuning observer committed at `5a518a2`;22 targeted tests pass, independent SPEC25 checks pass, independent QUALITY approves the exact files. Evidence: `results/gold/hf-routing-runner/resume-tuning-attempt-001/`. This is passive fixture validation; no real HF arm or formal cell ran.

Standalone CPU update,2026-09-06: `ffd928e` integrates tuning gates into the private HF request;17 focused tests and both independent reviews pass, followed by82 eval-HF plus31 adapter-HF phase tests. No real HF arm or formal cell ran. Controlled startup compatibility corrections are source-reviewed and being implemented; see the updated HF capture plan.

Standalone CPU update,2026-09-06: `5561e4e` closes the declared startup environment/optional-addon observation correction.12 observer+11 protocol tests, both independent reviews and83+32 related phase tests pass. Seven-source supplemental binding and controlled execution remain unimplemented; no real HF arm or formal cell ran.

Standalone CPU/source update, 2026-09-06: `3833dac6` closes the fixed seven-source startup snapshot after 15 focused tests, independent SPEC/QUALITY and 41 related tests. Real selected-source acquisition matches the audited bytes and current primary/supplemental checks. Controlled worker/parent and independent capture validation remain open; real HF arms and formal DONE remain 0.
