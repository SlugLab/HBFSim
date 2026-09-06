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


Current input identity refresh, 2026-09-06: the resumed filesystem reports
device66311 instead of66312. The old frozen metadata bundle still validates as
historical evidence, but its current-input check correctly rejects the changed
identity. The independent refresh at
`results/manifests/hf-qwen3-30b-a3b-metadata-20260906-resume001/` passes both frozen
and current checks. All metadata artifact hashes and every input field except
391 device fields are unchanged. Metadata identity remains
`6bd086d9258aeec88aa3294df6133c289c0a8d5b557f7ce03b5c7dc6644d7494`;
the new receipt is `bfde7f1cc25631404328ded67b52dccb2c5d630bf3aa52acfa02d76b8e47c104` and
COMPLETE is `e1d2bf670f5ea939a50784ee1f92947cf1f015c55f780be1efbfc458f2ab9781`. This COMPLETE is the existing
metadata-only marker, not routing capture or scientific completion.

The current runtime primary is
`results/gold/hf-routing-runner/runtime-sources-tuning-real-attempt-002/`, bound by
`ed7a35bd19eb8938a97269a80e88ff817d7d8ca8fde8a7d5fb1ac58c27baabd4`.
Its133 source/metadata hashes and interpreter hash match the old observation;
1042 device fields changed and all other fields remained equal. Startup seven
sources are in `startup-sources-real-attempt-002/`, manifest
`416819ba83141fadc881b88a1b6cd54fa2771f24e2084903d02a4a8a4addef50`.
Selected tuning is independently rebound in `tuning-inputs-real-attempt-002/`,
manifest `73854fbec45375ccc1390dc6f60fc2ae298754d322bb892be1cc7ed9a7bf0e5d`. It still records
INSTALLED_DEFAULTS for the absent E=128,N=768 packaged configuration, with the
same declared NVIDIA RTX PRO 6000 Blackwell Server Edition name and geometry.
Actual CUDA/vLLM device authentication remains an owned-worker gate.

These are new observations with explicit links to their predecessors. No old
artifact was replaced, no device identity was normalized away, and within-run
input drift still fails. The refresh read bounded metadata and file headers;
weight payload hashes remain historical. No inference import, GPU execution,
real HF arm, scientific receipt or formal row was produced. Evidence:
`results/gold/hf-routing-runner/metadata-identity-refresh-attempt-001/` and
`runtime-source-drift-diagnostic-attempt-001/`.


Historical owned-entrypoint review checkpoint, 2026-09-06: the uncommitted worker and
provisional native/capture/repeat parent had an initial 16-test CPU record, but
independent SPEC review required corrections before acceptance. The review
confirmed a missing fixed site import path, device checks occurring after vLLM
imports, mutable/deleted acknowledgements accepted by later callbacks,
incomplete prefix/preload checks, signal/failure finalization gaps, missing
after-arm wire/source checks and precise failure stages, discarded device
observations, and unbounded topology-command output. Evidence is
`results/gold/hf-routing-runner/owned-entrypoint-spec-attempt-001/review.json`
and its bounded CPU `diagnostics.json`.

Corrections were returned uncommitted in a fresh
`owned-entrypoint-attempt-002/`. Its initial 24-test RED records 6 failures and
4 errors; previous attempt-001 is preserved. The final manifest binds23 artifacts
and five successful runs, including focused tests, actual owned child controls,
valid MOCK isolated preflight, isolated import probing and static checks. The
valid preflight reaches its private callback with existing metadata/runtime/
startup/tuning/current-input checks intact. That record was not final unit acceptance:
independent SPEC re-review, QUALITY and applicable phase regression were still pending.
No real HF model arm, routing-origin receipt, scientific receipt or formal
matrix row has been produced. Formal DONE remains 0.


Owned-entrypoint CPU acceptance, 2026-09-06: committed at `e724af0a396164cd59d6a7578aec966f69e52346`.
`hf_owned_worker.execute_owned` implements isolated child startup, finite source
and retained-input binding, immutable ownership acknowledgement checks and staged
device/runtime gates before the existing loaded arm. `hf_routing_runner.run_triplet`
owns three fresh sequential processes under one resource guard, validates the
bootstrap record, preserves attempted-arm and independent postcheck diagnostics,
and accounts for signals through its explicit durable-status acceptance cutoff.

Frozen attempt-006 passed SPEC and independent QUALITY review and 40 focused
CPU controls (14.528s unittest /15.099798s process). Its related phase passed
137 evaluation HF, 32 adapter HF and 41 metadata controls, 210 total. Attempt-007
then normalized CRLF to LF only, retaining exact before/after transformation and
AST-equivalence evidence; the normalization passed SPEC/QUALITY review and a new
210-test phase binding the final source bytes. These suites overlap and their
counts must not be added as independent coverage. Evidence is in
`results/gold/hf-routing-runner/owned-entrypoint-attempt-006/`,
`owned-entrypoint-attempt-007/`, `owned-entrypoint-phase-attempt-001/` and
`owned-entrypoint-phase-attempt-002/`. Earlier attempts and findings remain
historical evidence; their smaller passing subsets were not final acceptance.

This unit returns only `PROVISIONAL_TRIPLET_RETURNED_UNVALIDATED` with
`scientific_validation_passed=false`. Validation used CPU/MOCK controls; no real
HF model arm, authenticated routing-origin receipt, scientific receipt or formal
matrix row was produced. The pure frozen trace verifier and subsequent owned-origin
publication wrapper remain unimplemented. The current resource guard ends before
outer finalization; complete guarded validation/publication is a later integration.
Formal DONE remains 0.

Frozen trace-verifier design checkpoint, 2026-09-06: the future pure verifier
must independently derive events from retained route arrays and tensor metadata,
compare native/capture/repeat tokens and capture/repeat routes, and retain only
a ROUTE_CONSISTENCY_ONLY boundary. Runtime/tuning hashes absent from its inputs
are shared declarations, not recomputed observations. Owned process provenance,
actual device/runtime/cleanup evidence and publication remain a later wrapper.

The existing metadata helper is not by itself an exact donor-type guard.
`results/gold/hf-routing-runner/frozen-semantics-design-attempt-001/report.json`
records a passing frozen baseline followed by four in-memory donor mutations:
equal-valued configuration/tensor-size/tensor-shape floats and one omitted
configuration key all retain the helper's summary. This is helper-level MOCK
diagnostic evidence, not a resealed altered bundle or full `_unpack` acceptance.
Original metadata and source bytes stayed unchanged; no inference, GPU or weight
payload read occurred. The new trace verifier needs a narrow exact-type and
required-field guard before the unchanged ModelInventory/legacy consistency
helpers. It must decode evidence as UTF-8 explicitly, propagate effective MOCK
from arm declarations with metadata imposing a mandatory floor, and compare
trace paths to an explicit expected attempt path without filesystem reads.
The pure verifier and subsequent owned publication wrapper are not yet implemented.
