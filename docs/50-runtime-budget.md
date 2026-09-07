# Runtime budget — bounded controls only

## Current bounded observations — 2026-09-07

| Observation | Process wall time | Boundary |
| --- | ---: | --- |
| HF012 native/capture/repeat triplet | 451.929812513 s | Three arms returned identical tokens; only capture/repeat routes match; not generation latency or a formal cell |
| P7 route-horizon attempt001 | 0.318749618 s | Failed at bounded routing-trace input read; no service/cell |
| P7 route-horizon attempt002 | 5.136081178 s | HF bridge passed; MQSim `std::bad_alloc` before first header under 2 GiB AS; no cell |
| P7 56 GiB zero-request init probe | 0.359337893 s | 164,192 KiB initialization-only RSS; header/finish all zero; not a replay peak |
| P7 route-horizon six cells, 56 GiB profile | 54.779875 s | Real/shuffled 29.103271656/25.255706970 s; 228,192/228,952 KiB peak RSS; traffic 14,080,278,528/12,324,962,304 bytes per cell; zero prefetch/extra bytes |
| P7 independent six-cell accounting | 0.516548358 s | Standard-library recomputation; all generation/GPU/science/speedup claims remain false |
| P7 rho=1/32 extra sensitivity, six cells | 110.927016 s | Real/shuffled six projected cells; ahead issues 2,304 requests per series; not original rho matrix, generation speedup or hardware residency |
| P7 rho=1/32 independent semantic review | 0.5390376 s | Reconstructed useful/late/evicted/terminal/censored categories from actual raw requests; claim boundaries unchanged |
| P7 requested rho=1/2 and rho=1, 12 cells | 79.638914 s | Four real/shuffled series took 19.823223293/19.525328124/19.777439287/19.467585387 s; all ahead-prefetch counts zero |
| P7 requested-rho independent accounting | 0.667704454 / 0.768508366 s | Two row-level standard-library recomputations passed; projected boundaries retained |
| P7 requested-rho semantic review | 0.3069178 / 0.2975702 s | Both rows classify `PREFETCH_TRIGGER_NOT_OBSERVED`; no GPU/generation/science claim |
| C6 repaired native-control host build | 18.534684581 s | Focused single target only; no GPU or G5 result |
| C6 GPU002 | ResourceBusy preflight | Foreign GPU process present; no owned kernel |
| C6 GPU003 native/future diagnostic | 9.237038743 s | 67 launches; arithmetic/conservation PASS; D expired before work, so `NON_IDENTIFYING` for W/overlap |
| C6 single-lane fixed-work mapping | 11.456092 s | Two transforms, four optimized cubins and four full disassemblies; structural review only, mapping `NOT_PROVEN` |
| C6 single-lane fixed-work GPU | 9.129740622 s | Two children/68 launches; 2,112 outputs, 46 groups and 92 traces pass; D still expires before W |
| C6 single-lane independent analysis | 1.421391600 s | Exact raw/mapping/outer arithmetic and cleanup consistency; no scientific promotion |

The 56 GiB-derived P7 profile passed one zero-request initialization
header-plus-finish capacity probe and six projected cells. The retained
384-expert cache equals 48 layers x eight routed experts; every cell issued zero
prefetch and extra bytes. Independent review found all 2,304 prediction
candidates per series resident and ready and matched one-layer-ahead to
on-demand item for item. `none` uses serial miss service while on-demand batches
same-layer demand, so their residual difference is not a prefetch speedup. The
2 GiB value was an address-space cap. The recorded RSS is observed host-process
memory for these bounded runs; it is not GPU/cache residency, a validated replay
budget or a revised formal runtime estimate. The explicit rho=1/32 smaller-cache sensitivity run subsequently completed in
110.927016 s and exercised the ahead issue path. It is outside the original rho
matrix. Its residual and traffic changes remain projected prefix-service
observations, not generation speedup, measured hardware residency or a
parameter-selection result. The 8 GiB cache field is a retained profile budget,
not the physical GPU capacity. The requested rho=1/2 and rho=1 rows later
completed 12 projected cells in 79.638914 s; both rows recorded zero prefetch and
eviction counts, and ahead matched on-demand. Joined with the earlier rho=1/16
attempt003 receipt, this covers 18 requested-rho projected cells across two
executions and receipt-specific profile inputs. The `hbm_cache_bytes` field is
8 GiB in attempt003 and 28,991,029,248/57,982,058,496 bytes in the two
attempt005 rows. Backing `capacity_bytes` remains 60,129,542,144 bytes; Python
replay instead uses each budget's aligned effective bytes. These are
capacity-only service diagnostics, not the physical 96 GiB GPU or measured
cache residency. Independent accounting and semantic reconstruction passed,
while generation, GPU-consumption, scientific and formal claims remain false.
The observations above do not change the matrix planning totals, G2/G5
thresholds or formal DONE count.

The tables and dated notes below preserve earlier observations.

The matrix estimates remain planning values. Known-delay and bounded C6 GPU
controls plus HF runtime diagnostics have run. G2 remains failed; HF010
completed all three real generation arms in 400.154178222 s. No physical-storage pilot has run. This checkpoint cannot replace
the planning values with validated per-cell
runtime estimates or predict a validated completion date.

| Latest standalone observation | Process wall time | Boundary |
| --- | ---: | --- |
| HF010 native/capture/repeat | 400.154178222 s | Three real cold-load/generation arms plus preparation/guard/cleanup; not generation latency |
| HF010 consistency, second bounded attempt | 5.520027339 s | 1 GiB limit; 487032 KiB RSS; supplied-buffer consistency only |
| HF010 single-member decode metrics | 1.214856791 s | 140860 KiB RSS; 336 decode routes; PROJECTED only |
| HF route-only three CPU controls | 1.370668335 s | 32420 KiB RSS; concrete causal/capacity oracle and negative joins |
| HF schema-2 inventory / mathematical budget / route ledger | 3.328824665 / 3.229581602 / 4.333797805 s | RSS186508/237428/199972 KiB; three 1 GiB/60 s stages; no model/GPU/MQSim |
| C6 native four-case GPU | 7.207384349 s | Exact native image; correctness capture only |
| C6 conditional transform / ptxas / collection | 0.165298894 / 0.316192252 / 1.721340215 s | Actual CPU commands; mapping remains NOT_PROVEN |
| C6 conditional CPU / configure / host build | 0.517359670 / 0.518229592 / 76.464894412 s | Seven controls; single target; build RSS702416 KiB |
| C6 conditional three-case GPU | 10.239901762 s | Guarded acquisition wall time; no kernel/overlap timing claim |
| New per-chain K1 D0 / D500 | 9.703757213 / 9.730148093 s | D0 noise diagnostic; D500 still INVALID_GOLD_GATE |
| ABBA CPU / serial target build | 0.467977310 / 68.672600271 s | Two real shared-oracle controls; RSS25976/670592 KiB |
| Same-process ABBA GPU | 4.361914580 s | Four measured launches after one warmup; two diagnostic pairs; no G2 closure |

The earlier 256 MiB HF010 consistency attempt failed with MemoryError and is
retained. It did not rerun the model. The same verifier passed at 1 GiB.
These observed times do not validate the planning runtime of any formal cell.

| Observed CPU verification | Wall time | Boundary |
|---|---:|---|
| Import-time environment compatibility phase | 30.223 s suite /31.211586910 s process | 54/54 tuning-runtime + loaded-arm + runtime-import tests; no GPU |
| Owned fixed-environment wire repair | 8.932 s suite /9.52 s process | Initial two-failure RED, final3 focused tests and real bootstrap MOCK subprocess; no real import/model |
| Transformers stored-version compatibility phase | 9.664 s suite /10.606893868 s process | 31/31 observer + loaded-arm tests; isolated pinned Python3.13, no GPU |
| Pure frozen HF trace final related tests | 12.398 s suite /13.138769422 s process | 83/83 tests; original controller root-timestamp failure diagnosed separately; no test rerun, no GPU |
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
| HF owned-request related phase | 5.180 +0.260 s suites | 38/38 worker/source/import plus57/57 adapters; MOCK runtime objects only |
| HF tuning source extension | 1.045 s unit /0.328 s real acquisition | 18/18 CPU controls;133 source-metadata artifacts, no inference import |
| HF tuning/worker related phase | 7.513 +0.272 s suites | 50/50 tuning/worker/source/import plus57/57 adapters; CPU fixtures |
| Real selected tuning input | 2.373 s | Exact packaged-file absence plus frozen metadata/source validation; no current GPU query |
| C6.2 CPU phase | 25.93 +0.08 s | 63/64 initial; reviewed source-test selector fix and sole focused recheck close64/64; no runtime change for that failure |
| C6.2 default-OFF regression | 18.30 s | 18/18 CPU/compile checks; fresh CUDA13 build40.82s, no GPU execution |
| C6 one-warp correctness diagnostic | 8.308445783 s | Actual guarded four-case GPU process; `CAPTURED_UNVALIDATED`, not a formal cell or overlap timing |
| Owned no-site bootstrap | 0.297 +38.819 s | Three new CPU subprocess controls plus46/46 existing scheduler tests; no inference import |
| Frozen route decoder phase | 3.536 s | 29/29 decoder/body/protocol CPU controls; six decoder controls and both reviews pass |
| HF worker protocol | 0.033 s | 10/10 historical CPU controls; runtime worker was then unimplemented |
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

Resumed2026-09-06: a fresh current-source known-delay build took58.282s, with20/20 CPU/compile tests taking14.379s process wall. Ten D0 plus one D500 standalone triplets took approximately10.6–11.6s each. D500 failed G2, so these do not supply validated formal-cell runtime estimates. Evidence: `results/gold/known-delay/resume-build-attempt-001/` and `resume-clock-controls-attempt-001/`.

Owned tuning integration,2026-09-06:17 focused controls took7.23s process wall (6.918s unittest); related82/31-test suites took33.080/0.567s process wall. Both independent reviews read the source/evidence without another test run. Evidence: `results/gold/hf-routing-runner/loaded-tuning-phase-attempt-001/`. These CPU validation costs do not revise formal runtime estimates.

Startup environment correction,2026-09-06:12 observer and11 protocol controls took1.58/0.36s process wall. Related83/32-test suites took33.468/0.717s. Source-only independent reviews did not repeat tests. Evidence: `results/gold/hf-routing-runner/startup-environment-phase-attempt-001/`. No formal runtime estimate or GPU execution is derived from these CPU costs.

Startup source binding, 2026-09-06: final focused 15 tests took 2.84s wall and are recorded in `startup-sources-attempt-004/`; the pre-fix six-failure RED took 2.92s. Related 41 tests took 7.941s. Read-only selected-source acquisition/rechecks took 0.318s. Earlier test corrections and labeled sensitivity checks remain in attempts 001–003. These are CPU/source costs, not formal or GPU runtime estimates.

Identity observation refresh, 2026-09-06: new133-input runtime acquisition/recheck took0.328s; seven-source acquisition/recheck0.318s; bounded metadata refresh/current checks 10.225s; tuning rebind/recheck 2.125s. These are source/metadata CPU costs only. No model payload hash or GPU execution is included.

Historical owned-entrypoint review checkpoint, 2026-09-06: initial16 CPU tests were insufficient for unit acceptance; eight SPEC correction groups were returned for re-review in fresh attempt-002, whose final manifest binds23 artifacts/five successful runs. Its24-test initial RED took1.285s (6 failures/4 errors). Frozen semantics helper design probe took3.801s with frozen metadata only; no payload/inference/GPU work. Final focused timings are retained in attempt-002/green/summary.json; independent reviews and phase regression were then pending, and this checkpoint does not estimate real model runtime.

Owned-entrypoint attempt-006 focused CPU controls took14.528s unittest
/15.099798s process for40 tests; static checks took0.046491s and the isolated
startup probe0.249536s. Related phase controls took50.806521s for137 evaluation
HF tests,1.032736s for32 adapter HF tests, and6.953387s for41 metadata tests
(process wall time). These records bind the attempt-006 bytes. Attempt-007
records byte-only CRLF normalization and AST equivalence; final-byte phase costs
in `owned-entrypoint-phase-attempt-002/` are: eval-hf 137 tests/54.427418s, adapter-hf 32 tests/1.085703s, metadata-fixtures 41 tests/7.456933s.
All are CPU/MOCK validation costs; they do not revise real model or formal GPU/SSD runtime estimates.

Pure-verifier attempts001-004 are retained under `results/gold/hf-routing-runner/`.
Attempt001's79-test pass preceded three later SPEC defects; its first RED was only
an absent-module loader error. Final production passed both independent reviews;
attempt004 improved two nonblocking test cases only (2/2,0.416s suite /0.873706055s
process). The final83 tests all returned success. Their controller incorrectly
required root mtime/ctime to remain stable while fixtures create/remove root
children; `frozen-trace-verifier-parent-attempt-004/phase-control-diagnostic.json`
preserves that diagnosis alongside the original CPU_PHASE_FAIL record. Counts
from overlapping suites must not be added. These CPU timings do not estimate
real HF cold-load time or formal-matrix completion.


Observed standalone hardware/runtime diagnostics, 2026-09-06:

| Diagnostic attempt | Process wall time | Actual result |
| --- | ---: | --- |
| HF001 | 31.992569225 s | Native Torch-device failure; model not constructed |
| HF002 | 63.010975611 s | Native import-observation version failure; model not constructed |
| HF003 | 447.972067177 s | Runtime versions observed; native tuning-retention environment failure; model not constructed |
| HF004 | 39.396977992 s | ResourceBusy during launch; bootstrap only, no device report or model |
| HF005 | 303.869927600 s | MemoryError at vllm-device and metadata-current postcheck; eight other postchecks passed; no model |
| HF006 | 20.626008441 s | Input-stage fixed-environment rejection before bootstrap/runtime/model; owned cleanup complete |
| HF007 | 0.566955839 s | ResourceBusy on foreign GPU PID1291679; `arms=[]`, no child/import/model |
| HF008 | 128.635422801 s | Real model loaded56.88 GiB/39.138985 s; engine init5.18 s; failed pre-generation environment observation; nine postchecks passed, owned exit observed |
| C6 one-warp four-case correctness | 8.308445783 s | 128 output checks and bounded conservation acquired; `CAPTURED_UNVALIDATED` |
| Full-import environment query | 109.471545466 s | Exact environment changes observed; no model constructed |
| K1 D0 | 9.261963436 s | Diagnostic acquisition DONE; G2 cell pass is null |
| K1 D500 | 10.071926834 s | INVALID_GOLD_GATE under original limits |

HF wall times include preparation, imports, guard and cleanup; they do not
measure model cold-load or generation latency. K1 times include each complete
native/matched-zero/target triplet. These failed or diagnostic observations do
not revise the formal-matrix planning estimate. Exact execution records are in
`results/gold/hf-routing-runner/real-diagnostic-triplet-controller-attempt-00{1,2,3,4,5,6,7}/execution.json`
and `results/gold/known-delay/chain-length-diagnostic-attempt-001/summary.json`.
The 31-test CPU timing is from `real-transformers-version-phase-attempt-001/`;
it must not be added to overlapping historical test counts. See the
[current standalone checkpoint](50-run-status.md#standalone-real-experiment-checkpoint-2026-09-06)
for outcome boundaries and the next actual repair.

The C6 diagnostic used a 120-second child budget for its first transform, driver
JIT, daemon startup and four cases. Its single observed8.308445783-second process
does not justify shrinking that guard and is not a throughput or formal-cell
estimate. The live JIT image is not bound to the retained optimized cubin, so
the observation supplies no C6.3, overlap, G5 or native-completion timing result.
