# Run status — implementation in progress

## Actual checkpoint: HF012, P7 requested rho controls and C6 fixed work (2026-09-07)

All records in this section are standalone diagnostics. Formal DONE remains 0.

- HF012 completed native/capture/repeat in 451.929812513 s. All arms returned
  identical generated tokens. Native did not capture routing. Capture/repeat each
  retain 384 router-call events (48 layers x eight forwards), eight saves and 39
  token slots (32 prefill plus seven decode); their joined 39x48x8 route arrays
  match exactly. Seven decode slots x 48 layers form 336 decode nodes and 335
  adjacent intervals.
  Status remains `PROVISIONAL_TRIPLET_RETURNED_UNVALIDATED`; no formal source,
  compute-time, generation-latency or speedup claim follows.
- The P7 route-horizon source passed seven focused implementation tests, and the
  later bounded private-reader regression passed. Attempt001 failed before
  service startup because the 20,893,685-byte routing trace exceeded the old
  small metadata reader. Attempt002 passed the HF012 metadata/input bridge, then
  the first real/none native MQSim process exited before its header with
  `std::bad_alloc` under the 2 GiB address-space cap. Its transcript is empty and
  there is no raw cell to analyze. Both failed attempts are retained.
- A separate 56 GiB-derived, zero-request initialization probe passed its
  header/finish handshake in 0.359337893 s with 164,192 KiB maximum RSS and all
  request counters zero. Attempt003 then completed all six projected cells in
  54.779875 s: real/shuffled stages took 29.103271656/25.255706970 s with
  228,192/228,952 KiB peak RSS. Real residuals were 272,190,600 ns for `none`
  and 254,812,260 ns for both other policies; shuffled residuals were
  236,996,520 ns and 219,656,820 ns. Traffic was 14,080,278,528 bytes
  in every real cell and 12,324,962,304 bytes in every shuffled cell. All cells
  issued zero prefetch and extra bytes. `none` serializes same-layer misses while on-demand batches them;
  one-layer-ahead was inactive at this 384-expert capacity. Independent review
  checked all 2,304 prediction candidates per series as resident and ready and
  matched its requests/nodes to on-demand item for item. These differences
  are not prefetch benefit, generation speedup or hardware validation. The
  independent accounting recheck passed in 0.516548358 s and retained all claim
  boundaries as false. The later explicit rho=1/32 capacity-sensitivity run completed all six
  cells in 110.927016 s. Both ahead cells issued 2,304 prefetch requests;
  independent semantic counts were real 918 useful/270 late/1,041 evicted/3
  terminal/72 horizon and shuffled 1,104/270/865/2/63. Real ahead residual was
  311,849,604 ns with 35,823,550,464 traffic bytes versus on-demand
  453,507,660 ns/25,291,653,120 bytes; shuffled ahead was 268,609,120 ns with
  34,068,234,240 bytes versus 442,698,120 ns/25,291,653,120 bytes. This is an
  extra sensitivity observation, not an original rho cell or generation/cache
  speedup. The retained 8 GiB cache field is distinct from physical GPU
  capacity; the media profile remains the capacity-only 56 GiB diagnostic.
  Evidence is `route-horizon-hf012-rho1-32-sensitivity-attempt-004/`; the
  independent semantic reconstruction is retained in
  `route-horizon-hf012-rho1-32-semantic-review-attempt-004/` under the HF routing
  gold root.
- The requested rho=1/2 and rho=1 rows then completed 12 projected cells in
  79.638914 s. Both rows recorded 1,860 hits, 828 misses, zero prefetch and zero
  evictions in every policy. Real residuals were 151,496,400 ns for `none` and
  139,740,180 ns for on-demand/ahead, with 7,813,988,352 traffic bytes;
  shuffled residuals were 149,861,820 and 135,854,820 ns with 7,804,551,168
  bytes. One shuffled terminal cold miss was not issued. Independent accounting
  passed in 0.667704454/0.768508366 s, and semantic reconstruction passed in
  0.3069178/0.2975702 s with `PREFETCH_TRIGGER_NOT_OBSERVED`. Together with the
  earlier rho=1/16 attempt003 receipt, the three requested rho settings now
  supply 18 projected cells across two executions and receipt-specific profile
  inputs. Attempt003's `hbm_cache_bytes` is 8,589,934,592, while attempt005's
  rho=1/2 and rho=1 values are 28,991,029,248/57,982,058,496; backing
  `capacity_bytes` stays 60,129,542,144. Python replay uses each budget's aligned
  effective bytes, so this is not a single common-profile run. They remain
  prefix-media replay, not measured GPU consumption,
  generation completion, prefetch benefit, scientific validation or formal
  cells. Evidence is
  `route-horizon-hf012-original-rho-matrix-attempt-005/` (execution SHA256
  `b30845024e76b2b92c94486740a7874b0816c79cf4f3a2355b52e8086ae87278`)
  and `route-horizon-hf012-original-rho-data-review-attempt-005/` (execution
  SHA256 `c924d932ffb850160886f8f6a19e34f188395ed72745ce8634080ae0fec7de57`).
  The combined semantic review is
  `route-horizon-hf012-original-rho-semantic-review-attempt-005/` (manifest
  SHA256 `185386445c3c116772fa05d9b8643efc55cefaf9e8ac0a26458034edc659c12e`).
- C6 retains a globaltimer-compatible future mapping (`future-delay-attempt-004`)
  and an independent untransformed native mapping
  (`native-control-attempt-001`), both explicitly unproved. The host compile
  repair changed only `journal.stage` to `journal->stage` and its focused host
  build passed. GPU002 stopped at ResourceBusy while a foreign process owned the
  GPU; no owned kernel ran and no foreign process was modified. Fresh GPU003
  then completed 67 launches in 9.237038743 s. Independent analysis matched
  2,112 outputs, 1,440 issued/ready/consumed requests, 45 groups and 2,880
  traces. All futureD lanes were already ready before work, with prework medians
  309,376/316,856 ns versus D=20,000 ns. K0/K4096 work medians were both
  7,040 ns (ordinary-native controls: 7,072 ns). The chosen W produced no
  duration separation, so this result is `NON_IDENTIFYING` and cannot close G5
  or overlap.
- A subsequent single-active-lane fixed-work build/mapping/GPU slice completed.
  Four distinct optimized cubins retain exact K=0/K=4096 markers; structural
  SASS review finds the 4,096 dependent IMAD chain but keeps mapping
  `NOT_PROVEN`. Two sequential children completed 68 launches in 9.129740622 s.
  Independent analysis checked 2,112 outputs, 46 future groups, 92 traces and
  cleanup. K=4096 W medians were 6,976/6,928/7,008 ns by arm versus K=0 medians
  of zero, but all 20 delayed samples expired before work (minimum prework
  35,776/46,304 ns versus D=20,000 ns). The result remains
  `NON_IDENTIFYING_PREWORK_COVERS_DELAY`; no scoreboard, completion, overlap,
  G5 or full C6 closure follows. The combined independent SASS/GPU report is
  `results/gold/timing-future-unit/c6-single-lane-data-review-attempt-001/`.
- P7 latency006 completed all 12 new 8/12 us projected cells in 215.956931 s;
  10 us reuses attempt004. Both accounting and independent semantic checks
  passed. Useful counts decrease and late counts increase across the retained
  8/10/12 us points; every ahead point issues 2,304 requests. Two source-tool
  versions differ between004 and006, so this is descriptive sensitivity rather
  than a same-source causal slope. The 17-file semantic review is retained at
  `route-horizon-hf012-latency-sensitivity-semantic-review-attempt-006/`.
- C6 lifecycle mapping/build/three-case GPU acquisition completed. The GPU run
  took 4.452103516 s and independently matched 96 outputs, 96 issues/readies,
  32 consumes, 64 drains, three groups and 192 traces with zero pending/error/
  overflow. The unused-exit case retained 32 sentinels. Observable cleanup
  succeeded, the owned runner was reaped, and sampled peak GPU memory was
  28 MiB. Void context destruction remains unobservable. Evidence is
  `results/gold/timing-future-unit/c6-future-lifecycle-data-review-attempt-001/`;
  the capture remains `CAPTURED_UNVALIDATED` and closes no broader gate.
- The single-block K1/W1/low ABBA diagnostic completed once in 5.679440482 s
  after two focused CPU methods and direct host-only compile/link. Each of four
  measured launches retained one row/eight ordered events; the unchanged old
  helper/plugin/PTX were reused explicitly. Independent signed pair deltas were
  wait +512/+512 ns, chain +544/+576 ns and CUDA Event -12,608.0513/+54,208.0402 ns.
  Two-pair descriptive chain MAE/P95 errors 60/76 ns meet the unchanged 100/200 ns
  diagnostic limits. This does not close matrix-wide G2, timing causality or
  scientific validation. The owned runner was reaped without remaining owned
  processes or an uncertain session. Evidence is
  `results/gold/known-delay/per-chain-single-block-abba-attempt-001/`.
- Known-delay G2, C6.3, G5, overlap, native completion timing, physical storage
  and formal registration remain open. No threshold changed.

The HF010 and earlier sections below remain dated history.

Formal scheduler snapshot: 2026-09-05T13:15:22.591549+00:00.
Latest standalone update: HF010 returned three real arms; single-member decode
metrics, native four-case C6 and native conditional-consumer GPU diagnostics ran.
The new per-chain D0/D500 pair still failed G2; see the
[current checkpoint](#actual-checkpoint-hf010-and-native-conditional-consumers-2026-09-06).
Earlier resource and CPU entries below remain historical.

## Actual checkpoint: HF010 and native conditional consumers, 2026-09-06

Implementation checkpoint HEAD is `24d007371fc4ff1f1d2fde2a80d6e4b4d7d20ebc`.
All observations below are standalone diagnostics; formal DONE remains 0.
Older progress entries retain their historical scope.

- HF010 completed real native/capture/repeat generation in 400.154178222 s.
  All three token outputs matched; capture/repeat route arrays matched exactly.
  Supplied-buffer consistency passed for 1,872 route events. Independent review
  is retained in `results/gold/hf-routing-runner/real-diagnostic-triplet-data-review-attempt-010/`.
  This is `UNVALIDATED` capture and `ROUTE_CONSISTENCY_ONLY`, not authenticated
  origin or formal B/A/C publication. Owned children exited; NCCL's teardown
  warning remains separate from observed process cleanup.
- Actual CPU metrics processed only the capture member's 336 decode routes in
  1.214856791 s. All routes matched the source. There were 2,688 expert accesses,
  828 cold and 1,860 previously seen. Mean non-cold reuse distance was 8.658602
  in actual order versus 8.331720 in the seed-0 shuffle. One member's union is
  always 8/128. Evidence and independent recomputation are under
  `results/gold/hf-routing-runner/offline-routing-hf010-data-review-attempt-001/`.
  This `PROJECTED` result supplies no compute timing, concurrent serving, cache
  residency or prefetch speedup. The subsequent route-only join is described below;
  timed HF-to-P7 integration remains open.
- The next CPU experiment generated an exact schema-2 HF inventory, a declared
  rho=1/16 mathematical budget, and a causal route-only ledger. The three stages
  took 3.328824665, 3.229581602 and 4.333797805 s. Both series contain 336 demand
  nodes, 289 predictions, 288 later-demand comparisons and one terminal candidate.
  Real/shuffled overlap counts were 1,191/1,362 (means 4.1354/4.7292 of 8 experts),
  so this short sample does not show an original-order advantage. Each whole expert
  occupies 9,437,184 bytes; current demand plus next-layer candidates require
  75,497,472 or 150,994,944 bytes, within the declared 3,623,878,656-byte capacity.
  This is a capacity comparison, not residency, cache hits or actual prefetch.
  Inputs use one sequence, 64 context tokens, BF16 KV, zero workspace/safety
  reserves; fast bytes are derived mathematically, not measured device capacity.
  Evidence is `results/gold/hf-routing-runner/route-only-opportunity-attempt-001/`;
  ledger SHA is `1aede34f8641c122ef4218dcffea497f47a15babb8d745ac1b41c28adca929f7`.
  Attribution remains `PROJECTED_ROUTE_ONLY_CAUSAL_CONTROL`; the terminal
  candidate is not classified useful. Independent actual reconstruction passed;
  review and analysis are in `route-only-opportunity-data-review-attempt-001/`
  under the same HF routing root.
  Device-time acquisition is a separate next experiment, with no current timing,
  speedup, live serving or G10 claim.
- C6's separately compiled native four-case image ran successfully in
  7.207384349 s: 128 outputs, 72 issued/ready/consumed, 38 groups, 144 traces.
  Its independent review is `c6-correctness/native-image-data-review-attempt-001/`
  under `results/gold/timing-future-unit/`. The earlier driver-JIT acquisition is
  distinct and cannot inherit this native-image binding.
- A new optimized conditional-consumer image was then compiled and inspected.
  The new host target passed seven CPU controls and a bounded serial build.
  Its false/true/mixed GPU acquisition returned `CAPTURED_UNVALIDATED` in
  10.239901762 s, reporting all 96 outputs correct, 96 issued/ready/consumed,
  three groups and 192 traces. Exact cubin SHA is `0804821c83079e92f1a25610cb345c4e98ff2618bbb64d578b084537338e134f`;
  raw SHA is `dfb972af8755926f62f9ba2ef87090760215e83bc437a9b0ffce562e2f3223eb`.
  Evidence is `results/gold/timing-future-unit/c6-correctness/conditional-consumer-attempt-001/`.
  Independent review recomputed all 96 outputs and 192 traces; see
  `conditional-consumer-data-review-attempt-001/` in the same evidence root.
  C6.3, full C6.4,
  D/W, G5, true overlap, native completion, capacity leases and TMA remain open.
- The new 136-byte per-chain known-delay path produced coherent actual rows,
  but G2 still failed. D0 mean absolute noise was 167,310.128 ns; D500 mean/P95
  absolute error was 106,758.213/117,620 ns against unchanged 100/200 ns limits.
  Every D500 local wait was 512 ns. Most paired difference occurred before the
  wait, without identifying a resolver, clock, JIT or scheduling cause.
  Evidence/reviews are under `results/gold/known-delay/per-chain-k1-data-review-attempt-001/`.
  The same-process ABBA follow-up subsequently ran in 4.361914580 s at
  `9b4d8437c65017d2193e7c70898cc683b46a1c6a`, with one context/module and
  fixed allocations across D0/D500/D500/D0. Every wait increment was 512 ns.
  For adjacent pairs (0,1)/(3,2), signed chain means were 1,792/-11,771.915 ns;
  mean absolute errors against 500 ns were 5,083.489/13,193.532 ns, with
  nearest-rank P95 errors 14,796/26,100 ns. CUDA-event deltas were
  -206,048.012/-202,144.146 ns. These are distinct timing endpoints and two
  diagnostic pairs, not a formal G2 replication set. Evidence:
  `results/gold/known-delay/per-chain-abba-attempt-001/`; its actual-data
  independent review is retained in `per-chain-abba-data-review-attempt-001/`
  under the same known-delay root. G2 and causal attribution remain open.
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

## Historical standalone checkpoints

The entries below describe their original acquisition or development phase.
Their pending items are historical; the current checkpoint above supersedes them.

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
matrix row was produced at that checkpoint. The pure frozen trace verifier is
now accepted below; the owned-origin publication wrapper remains unfinished.
The current resource guard covers triplet execution and ends before outer
finalization. Complete guarded validation/publication is a later integration.
Formal DONE remains 0.

Pure frozen-trace CPU acceptance, 2026-09-06: committed at `1ca33140245df77fc7f555eb2a9cc097318c26c5`.
`FrozenTraceArm` and `validate_frozen_trace` check immutable supplied buffers,
strict UTF-8/types/bounds, original protocol hashes, tokens, decoded routes,
independently derived tensor events/pages and summaries. The result remains
`ROUTE_CONSISTENCY_ONLY`, `scientific_validation_passed=false`; it does not
authenticate original processes, current paths or absent runtime/tuning buffers.
Production passed SPEC003 and independent QUALITY003. Parent review closed two
nonblocking tests-only004 notes; final related tests passed83/83 in12.398s
(13.138769422s process). The original phase controller flagged expected root
directory timestamp changes from temporary fixtures; its failed control record
and the separate diagnosis are both retained, without a test rerun. Acceptance:
`results/gold/hf-routing-runner/frozen-trace-verifier-parent-attempt-004/acceptance.json`.

## Standalone real-experiment checkpoint, 2026-09-06

Historical HF008 run: HF008 started2026-09-06T18:03:21.357470Z on c7ee007 and completed after
128.635422801 s, exit1. It passed device/import/preconstruction checks and
actually loaded the BF16 model: stdout records56.88 GiB and39.138985 s for
model loading, then5.18 s for engine initialization. The subsequent
pre-generation tuning-observation rejected an environment mismatch. No raw
tokens/routes returned; capture/repeat did not start. All nine postchecks
passed; owned exit was observed, remaining=[], uncertain=false. The failure
is a post-construction gate failure, not a failed model load. NCCL stderr
warned that destroy_process_group was not called; observed process exit does
not prove graceful group destruction. Source/HEAD remained unchanged.
Execution: `results/gold/hf-routing-runner/real-diagnostic-triplet-controller-attempt-008/execution.json`,
SHA256 `4d4bab1302e0116f2857f4f06c8be72ece25a36baaa4b7a097a749b60730a867`.
Launch MemAvailable22,157,448 kB, CommitLimit70,960,824 kB and
Committed_AS27,573,236 kB. Preserve prior failures; investigate the actual
construction-time environment delta before a fresh guarded run.

Historical HF007 started at 2026-09-06T17:55:45.041093Z on HEAD
`c7ee00758c195798e4554268429f9a63868bcf7a` and stopped after0.566955839 s.
The launch guard returned `ResourceBusy` for foreign GPU PID1291679. `arms=[]`;
no owned child, import, runtime report, model, generated token or route started.
Launch memory was MemAvailable15,724,408 kB, CommitLimit70,960,824 kB and
Committed_AS44,315,632 kB. Evidence:
`results/gold/hf-routing-runner/real-diagnostic-triplet-controller-attempt-007/execution.json`
(SHA256 `419d65deebd44700d73c00af35404bd3f371eb651de96c48e1889537c3bbcc55`).

HF006 previously failed at the input-stage fixed-environment wire copy before
bootstrap/runtime/model. Commit `c7ee00758c195798e4554268429f9a63868bcf7a`
adds the three CPU thread-limit values to the owned worker's `FIXED_ENV`. The
repair passed both reviews, an initial two-failure RED and three focused GREEN
tests; its real bootstrap MOCK subprocess passed in8.932 s suite/9.52 s process.
Those tests establish CPU/MOCK validation. HF008 subsequently exercised
the repaired wire path, real imports and model construction; its later
pre-generation environment mismatch remains open.

HF005 previously failed after303.869927600 s with MemoryError at vllm-device,
before device/import reports or model construction; capture/repeat did not
start. Its metadata-current postcheck also failed with MemoryError while eight
other checks passed. Owned exit was observed, remaining members empty and
uncertain=false. Launch MemAvailable was2,028,416 kB. Evidence:
`results/gold/hf-routing-runner/real-diagnostic-triplet-controller-attempt-005/execution.json`
(SHA256 `9d7b069cb1d7475c24e8329ab8ae0f03028cb5182796ec9903726776faf16a65`).
None of HF005-HF008 changes formal DONE, which remains0.

The default-OFF per-chain layout unit is committed at389e5d8 after independent
SPEC/QUALITY and an existing ABI control in macroOFF/ON configurations. Its
initial RED also contained truncated-test EOF; the failed first GREEN and
corrected full154-line GREEN are retained. Device/benchmark integration is
still absent, and this does not close G2. Evidence is under the known-delay
gold directory in `per-chain-layout-attempt-001`,
`per-chain-layout-phase-attempt-001`, `per-chain-layout-spec-attempt-001`, and
`per-chain-layout-quality-attempt-001`.

C6 now also has one actual bounded GPU correctness acquisition on HEAD
`e96be28ae4b0be5dacc4f5944c64a554572bef9c`. The one-block,32-lane diagnostic
completed all same/distinct-page and dense/sparse cases:128/128 outputs matched,
issued/model-ready/consumed were72/72/72, groups issued/completed were38/38,
trace count was144, and pending/terminal-error/trace-overflow were0. Observable
unload, unregister and allocation releases succeeded; the void context-destroy
API records only that it was called. The controller exited0 after8.308445783 s
with `CAPTURED_UNVALIDATED`. Evidence is the controller execution record
(SHA256 `cc2fb688d8d0c85278a17a7b68980a10fa715313949cc7ca84dd35f37d727efc`)
and `attempt-001/diagnostic/raw.json` (SHA256
`346dbac1219b6191052668518e97c3eb886f62b27a77cc47824470f1040775fa`).

The earlier optimized candidate transform/compile/disassembly retains seed
arithmetic before wait except for the last multiply fused into the postwait
consumer. That mapping remains `NOT_PROVEN`; live driver JIT is not bound to
the earlier cubin, and no decoded-scoreboard, native-completion, overlap, G5,
full C6.3 or full C6.4 claim follows from the correctness acquisition. TMA and
the complete C6.4 lifecycle remain open.


Earlier resource follow-up: attempt004 completed in 39.396977992 s on frozen HEAD
`9b11227f693065c8a668c086767430d4b10c323e`. The launch guard stopped the native
child with `ResourceBusy` after nvidia-smi reported that it could not map a
segment from `libm.so.6`. Only the bootstrap record exists; no device/runtime
report or model return was acquired. Owned process exit was observed, with no
remaining owned members or uncertain session. Capture and repeat did not start.

The 15:57:18 UTC follow-up observed 707,916 kB of available host RAM and another
process using 24,263,672 kB RSS, with substantial memory pressure. nvidia-smi then
ran successfully and showed zero GPU memory use and no compute processes. This
supports waiting for host resources before retrying; it does not establish the
exact failed allocation or validate the new environment correction. Evidence:
`results/gold/hf-routing-runner/real-diagnostic-triplet-controller-attempt-004/{execution,resource-diagnostic}.json`.
Attempt005 subsequently failed as recorded above. The three earlier code-related failures
below remain historical. Formal DONE is still 0.


Three guarded HF diagnostic attempts have actually run with the fixed
32-input/8-output request and the current resume001 metadata. All stopped in
native before model construction; capture and repeat did not start. These are
real runtime attempts, with no generated tokens, route return or formal receipt.

| HF attempt | Native result | Process wall time |
| --- | --- | ---: |
| 001 | FAILED at `torch-device`: Torch UUID representation differed from the parent declaration | 31.992569225 s |
| 002 | Both Torch and vLLM device gates passed; FAILED at `import-observation` on missing `transformers.__version__` | 63.010975611 s |
| 003 | Passive runtime observation passed, including Transformers 5.5.4; FAILED at `tuning-retention`: `HF tuning runtime: environment differs from prepared allowlist` | 447.972067177 s |

The Torch UUID compatibility fix is committed at `f5d7592` and was exercised by
002. The passive Transformers stored-version fix is committed at `3a7cb61`;
independent SPEC/QUALITY and the 31/31 related CPU tests passed, then 003 saved
the actual `RUNTIME_CONFIGURATION` report with all pinned runtime versions.
Attempt003 used frozen HEAD `ae156dd3fb740c4acd0aec92883714a42a35ef76`.
A subsequent guarded preconstruction-only query completed in 109.471545466 s.
The complete runtime import union added exactly `KMP_DUPLICATE_LIB_OK=True`,
`KMP_INIT_AT_FORK=FALSE` and
`LD_LIBRARY_PATH=/opt/miniconda3/lib/python3.13/site-packages/cv2/../../lib64:`;
it modified or removed no existing setting. The installed sklearn/threadpoolctl/
joblib and OpenCV source explains these import-time assignments. The earlier
`CUDA_MODULE_LOADING` hypothesis was not observed. Evidence:
`results/gold/hf-routing-runner/real-runtime-environment-fix-attempt-001/observation.json`.

The minimal correction is committed at `b136c68b8c1e3c619babc8f445f8d295079e86bd` after independent reviews
and 54/54 related CPU tests. It preserves the initial launch environment,
requires the three exact values after import, retains them in the stable tuning
state, and rejects missing/wrong/changed values and unknown extra variables.
At that earlier checkpoint the next action was attempt004; the corrected tuning
gate has not yet passed a real model run. No model was constructed by the query.

All three failed attempts retain nine passing postchecks and observed owned
process exit, no remaining owned members, and no uncertain session. Attempts002
and003 explicitly record `engine_cleanup_boundary=NOT_CONSTRUCTED` and
`raw_return_saved=false`; 001 failed before that worker-status record existed.
Preserve each failure in `results/gold/hf-routing-runner/real-diagnostic-triplet-attempt-00{1,2,3}/`
and its matching `real-diagnostic-triplet-controller-attempt-00{1,2,3}/execution.json`.
The 31-test record is `results/gold/hf-routing-runner/real-transformers-version-phase-attempt-001/`.

Two K=1/W=1/low-occupancy hardware triplets also completed, with 188 observed
chains per arm and all 13 input hashes unchanged from the earlier K64 failure.
These are diagnostic acquisitions; D0's DONE state has `g2_cell_pass=null`.

| K1 point | Acquisition state | Mean absolute error | P95 absolute error |
| --- | --- | ---: | ---: |
| D0 | DONE, zero-wait diagnostic | 48,504.170 ns | 69,504 ns |
| D500 | INVALID_GOLD_GATE | 141,964.170 ns | 154,644 ns |

The D500 target's 188 local waits are all 512 ns, versus zero in matched-zero.
The 12 ns excess over the requested 500 ns cannot explain the complete-chain
error. This measurement does not identify counter contention or another exact
cause, and lowering K does not constitute a fix. The original mean/P95 limits
remain 100/200 ns; G2 stays open. Evidence:
`results/gold/known-delay/chain-length-diagnostic-attempt-001/summary.json` and
the two points' original raw/analysis records. No resampling or threshold change
was used to select these results.

Formal DONE remains 0. Artifact/source/publication integration still gates
formal acceptance and COMPLETE; it does not prevent these explicitly
UNVALIDATED diagnostic trials. Choose subsequent work from the actual failures
and retain the existing resource guards and per-arm 900-second limit.

Historical frozen trace-verifier design checkpoint, 2026-09-06 (implemented above): the pure verifier
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
The pure verifier is now implemented as described above. Owned-origin validation
and final publication remain later work; diagnostic execution may proceed under
the existing guard without promoting its results to validated routing gold.
