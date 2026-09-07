# Phase-two implementation checkpoint — ongoing

## Routing continuation batch — 2026-09-07

At execution HEAD `aa5af1d125bbd5c758a0393a099e51e17e7fff51`, four additional
fixed-token 16-member captures returned successfully in 644.265 seconds
combined. The unchanged guarded controller was reused with fresh output paths.
Together with attempt001 there are five captures / 80 generated requests and
149,760 routing events. All 16 members have byte-identical raw route arrays and
identical generated tokens across all five captures; relevant source hashes match.
All owned cleanup receipts pass; a final host query showed no GPU processes and
0 MiB used. This is fixed-input repeatability, not five formal replicates,
natural-prompt coverage, live concurrency, source-origin certification or G9 closure.

In parallel, 29 previously missing B=1/2/4/16 composition groups completed in
54.807 child-process seconds; the two existing B=8 groups were reused.
Independent standard-library arithmetic reconstructed frequency, union, entropy,
Gini, Jaccard and reuse distances. Per B and real/shuffled series, the complete
16-member set supplies 43,008 expert accesses. Mean real union at B=1/2/4/8/16
is 8.0000/13.6272/20.7835/30.7515/40.7321. All results remain PROJECTED and
TRACE_COMPOSED; shared groups are not independent repetitions, and finite reuse
distance is not a cache hit. Byte-identical capture inputs permit reusing these
statistics without repeated postprocessing.

Evidence and exact commands: `results/batches/20260907-routing-continuation/`
(`README.zh.md`, `gpu-progress.json`, `cpu-progress.json`,
`capture-comparison.json`, `composition-summary.json`, `readiness.json`).
The original matrix has 2,848 conditions / 20,485 planned repeats, of which
13,105 repeats are minimum. Formal DONE remains 0: G2/G5, exclusive physical
storage, matched compute-only inputs, dense matching and formal registrations
remain open. No ideal sweep or unchanged failing diagnostic was rerun.


## Current actual checkpoint — HF012, P7, C6 and routing_capture-01469 (2026-09-07)

The original EQ4/G9 `routing_capture-01469` cell now has one bounded real
capture pilot at source commit
`5d73bdd9317ce43cc3eacd80cdfbffeebed5cee0`. One loaded Qwen model served 16
sequential single-sequence requests in 175.133469447 s. The fixed 32-token
control slabs cover token IDs 1000 through 1511; each request generated eight
tokens. The capture retained request IDs 0 through 15, 16 distinct output
sequences and 70 hash-bound artifacts. It contains 29,952 routing events
(24,576 prefill and 5,376 decode), 239,616 expert accesses and 718,848 tensor
accesses. The independent bounded arithmetic review passed 12 checks in
20.845955406 s at 365,980 KiB maximum RSS: all 16 prompts, request IDs, output
sequences and `[39,48,8]` route arrays are distinct, and the eight retained
evidence files in the arithmetic receipt matched their hashes. Its status remains
`ARITHMETIC_CONSISTENCY_ONLY_COMPLETE_UNVALIDATED`; evidence is
`results/gold/hf-routing-runner/sixteen-member-arithmetic-review-attempt-001/`
(manifest SHA256
`fbd4e2965aa955b9b354efe18d7093798c46b2a0d9e2e4a89082349189eb9826`).

The two fixed seed-0 index groups were processed as two offline B=8
waves. The CPU stages took 4.239253894/4.437069891 s and reached
618,516/620,624 KiB maximum RSS. Each wave retained 336 B8 step-layer rows,
2,688 decode routes and 21,504 expert accesses, giving 5,376 routes and 43,008
expert accesses per real or shuffled series across both waves. The execution
status is `PROJECTED_TWO_B8_WAVES_COMPLETE_NOT_CAPTURE_CERTIFIED`; its manifest
SHA256 is `186f4434ad4786aa91a9f7c340d45d88162d9b30cd95d4ba6248efd6f28f4f19`
and execution SHA256 is
`628439232d9459b848d02f2bf6b48c259d7dc0e1060d99944395c09675375dcc`.
The independent descriptive-statistics pass completed in 0.517574897 s
at 67,896 KiB maximum RSS; its nine retained evidence files were hash verified.

| Pooled series | Mean unique-expert union | Mean previous-step Jaccard | Mean entropy, bits |
| --- | ---: | ---: | ---: |
| Real | 30.751488 | 0.550686 | 4.601714 |
| Shuffled | 31.568452 | 0.516446 | 4.650080 |
| Uniform analytical null | 51.619907 | — | — |

Both observed series contain 5,750 first accesses and 37,258 finite
reuse-distance accesses out of 43,008. Finite reuse is not a cache-hit claim.
The statistics status is `PROJECTED_TRACE_COMPOSED_DESCRIPTIVE_STATISTICS`;
its manifest SHA256 is
`0ae0105b327788edcf86518dee638f55a10fd0ae314084bff31f273a1942bfdb`.
The combined experiment and source-review bundle is
`results/gold/hf-routing-runner/sixteen-member-data-review-attempt-001/`
(manifest SHA256
`75d9fecdbca90aa10b543ff33a339b8d14c2054ba3c8c8bbb6541e8400b9caae`).
The 16 model calls were sequential and the two B=8 waves are trace composed,
without live scheduler timestamps. The prompts are a synthetic token control
set. This closes one bounded pilot for the named original cell; it does not
complete G9, its five repeats, natural-prompt coverage, live B=8 concurrency,
latency/performance validation, capture origin, scientific validation or any
formal admission. Formal DONE remains 0.

This update joins observations through the exact source and HEAD identities in
their referenced receipts. HF012 returned all three real
native/capture/repeat arms in 451.929812513 s with identical generated tokens.
Native did not capture routing. Capture and repeat each retain 384 router-call
events (48 layers x eight forwards), eight saves and 39 token slots: 32 prefill
plus seven decode. Their joined 39x48x8 route arrays match exactly. The seven
decode slots x 48 layers form 336 decode nodes and 335 adjacent intervals.
These remain `PROVISIONAL_TRIPLET_RETURNED_UNVALIDATED` observations, not
source-origin, hardware-timing or formal evidence.

The P7 route-horizon adapter, replay and CLI controls passed their seven focused
implementation tests; the later private-reader regression also passed. The
first two attempts produced no valid cell. Attempt001 rejected the 20,893,685-byte
routing trace under the old small-metadata reader. After that bounded reader was
fixed, attempt002 retained the complete HF012/input bridge but native MQSim exited
before its first header with `std::bad_alloc` under the 2 GiB address-space cap.
A separate 56 GiB-derived, zero-request capacity/init probe completed its
header and finish handshake in 0.359337893 s with 164,192 KiB maximum RSS and
all request counters zero. Attempt003 then completed all six projected cells in
54.779875 s (real 29.103271656 s, shuffled 25.255706970 s; peak RSS
228,192/228,952 KiB). Real cells each transferred 14,080,278,528 bytes; shuffled cells each
transferred 12,324,962,304 bytes. Every cell issued zero prefetch bytes and zero
extra bytes. The retained configuration has a 384-expert cache (48 layers x eight routed
experts). Independent review checked 288 prediction nodes x eight candidates in
each series: all 2,304 candidates were resident and ready, and one-layer-ahead
requests/nodes matched on-demand item for item. Zero prefetch is therefore the
bounded condition's structural result, not an implementation omission. The lower
residual relative to `none` reflects batched same-layer demand issue versus
serial miss service, not prefetch benefit. This remains a diagnostic capacity
configuration, not measured residency, generation speedup or validated model
capacity. The independent standard-library accounting recheck passed in
0.516548358 s while preserving all claim boundaries as false. The explicit rho=1/32 capacity-sensitivity diagnostic subsequently completed
all six real/shuffled policy cells in 110.927016 s.  Unlike the retained
rho=1/16 condition, each ahead cell issued 2,304 actual prefetch requests.
Real-order residual time changed from 453,507,660 ns on-demand to 311,849,604 ns
ahead while traffic increased from 25,291,653,120 to 35,823,550,464 bytes;
shuffled residual changed from 442,698,120 to 268,609,120 ns while traffic
increased to 34,068,234,240 bytes.  The independent semantic reconstruction
classified real requests as 918 useful, 270 late, 1,041 evicted, 3 terminal and
72 horizon-censored; shuffled counts were 1,104/270/865/2/63.  These categories
conserve the actual 2,304 requests in each series.  This is an extra capacity
sensitivity result, not an original rho-matrix cell, a parameter selected for a
win, generation speedup, hardware cache residency or a causal real-route
advantage.  The 8 GiB field is the retained profile's cache budget, not the
physical 96 GiB GPU capacity; the 56 GiB media profile is the smallest
4 GiB-aligned diagnostic capacity covering the 54 GiB dense expert extent.
The six-cell execution is
`results/gold/hf-routing-runner/route-horizon-hf012-rho1-32-sensitivity-attempt-004/execution.json`
(SHA256 `96e8936854585b5e9f5d21e957199064fb638daae68be4e2e44dc36180a3a858`).
The independent semantic review is under
`route-horizon-hf012-rho1-32-semantic-review-attempt-004/` with manifest SHA256
`19dd4ef136d1d2747df39bc52ad14b320c87b1c266a7e9ad0d5aa2c080a1d4a9`.

The requested rho=1/2 and rho=1 rows then completed their 12 projected
real/shuffled policy cells in 79.638914 s. Together with the earlier rho=1/16
attempt003 receipt, the three requested rho settings now have 18 projected
cells; these are joined across two executions rather than presented as one
common-profile run. In both new rows every policy recorded 1,860 cache hits and
828 misses, while all ahead-prefetch and eviction counts remained zero. Real
`none`/on-demand/ahead residuals were 151,496,400/139,740,180/139,740,180 ns
with 7,813,988,352 traffic bytes per policy. Shuffled residuals were
149,861,820/135,854,820/135,854,820 ns with 7,804,551,168 bytes per policy; one
terminal cold miss was not issued, so 828 misses must not be equated with 828
issued requests. Independent accounting passed in 0.667704454/0.768508366 s and
the two semantic reconstructions passed in 0.3069178/0.2975702 s. They confirm
`PREFETCH_TRIGGER_NOT_OBSERVED`, not a prefetch implementation failure or a
benefit. The run is
`results/gold/hf-routing-runner/route-horizon-hf012-original-rho-matrix-attempt-005/`
(execution SHA256
`b30845024e76b2b92c94486740a7874b0816c79cf4f3a2355b52e8086ae87278`);
accounting evidence is under
`route-horizon-hf012-original-rho-data-review-attempt-005/` (execution SHA256
`c924d932ffb850160886f8f6a19e34f188395ed72745ce8634080ae0fec7de57`).
The combined semantic review is
`route-horizon-hf012-original-rho-semantic-review-attempt-005/` (manifest
SHA256
`185386445c3c116772fa05d9b8643efc55cefaf9e8ac0a26458034edc659c12e`).
The rho=1/2 and rho=1 profile inputs have SHA256
`e731c674ceaf654785681152cde422f75c4236ddea762d982aeea56dd71d4b98`
and `a57b0bdbfca39bac414cda8a3d5461d6f7f80bf288de1d61f8982bfee8704a43`;
their `hbm_cache_bytes` fields are 28,991,029,248 and 57,982,058,496 bytes,
while attempt003 retained 8,589,934,592 bytes. The backing
`capacity_bytes` remains 60,129,542,144 bytes in all three cases. Python replay
uses each budget's `page_aligned_effective_bytes` (3,623,878,656 bytes at
rho=1/16), and the standalone service does not allocate GPU cache from the HBM
field. The later profiles therefore do not retroactively replace attempt003's
input or turn the joined receipts into one same-profile execution.
All 18 observations remain projected prefix-media replay rather than measured
GPU consumption, generation completion, scientific validation or formal cells.

C6 now retains the actual globaltimer-compatible future image at
`future-delay-attempt-004` and a separate ordinary native image at
`native-control-attempt-001`. Their mapping states remain `COLLECTED_NOT_PROVEN`
and `COLLECTED_NATIVE_CONTROL_NOT_PROVEN`. The one-line host
`unique_ptr<Journal>` call repair passed its focused host build. GPU002 stopped
at the existing foreign-GPU guard without touching that process; fresh GPU003
then completed 67 launches in 9.237038743 s. Independent arithmetic matched all
2,112 outputs, 1,440 issued/ready/consumed requests, 45 groups and 2,880 traces.
All futureD lanes were ready before work began: prework medians
309,376/316,856 ns exceeded D=20,000 ns, while K0/K4096 work medians were both
7,040 ns. This is `NON_IDENTIFYING` for the selected W, not G5 or overlap
closure.

A later single-active-lane fixed-work slice made K an actual optimized-work
control.  Its seven focused CPU checks and serial target build passed; the
accepted K=0/K=4096 PTX images were transformed and four distinct cubins were
assembled and disassembled.  Independent SASS review found the exact four-byte
work marker in every image (0 or 4096) and retained the 4,096-instruction
dependent IMAD chain between load and wait/consumer, while leaving mapping
`NOT_PROVEN`.  The two sequential GPU children completed 68 launches in
9.129740622 s.  Independent arithmetic checked 2,112 outputs, 46
issue/ready/consume groups and 92 traces, plus native/inactive-lane controls and
owned cleanup.  K=4096 lane-zero W medians were 6,976/6,928/7,008 ns for
native/future0/futureD versus 0 ns medians at K=0, so work separation is now
observed.  All 20 measured D=20,000 ns samples still expired before work began
(minimum arrival-to-work 35,776 ns at K=0 and 46,304 ns at K=4096), leaving the
capture `NON_IDENTIFYING_PREWORK_COVERS_DELAY`.  It does not establish physical
load completion, scoreboard semantics, overlap, G5 or complete C6.3/C6.4.
The combined SASS/GPU independent review is retained at
`results/gold/timing-future-unit/c6-single-lane-data-review-attempt-001/`
(manifest SHA256
`e44c84dff7482d01582f77fa6fc978ddc6c0423e6e903fcca3d8fd43a17cc816`).


The rho=1/32 latency sensitivity then completed 12 additional projected cells
at 8,000 and 12,000 ns in 215.956931 s. The 10,000 ns point is the retained
attempt004 result and was not rerun. Independent accounting and semantic
reconstruction passed; every ahead point issued 2,304 prefetch requests.

| Series | Modeled read latency | Ahead residual | Useful / late requests |
| --- | ---: | ---: | ---: |
| Real | 8,000 ns | 208,838,276 ns | 1,141 / 47 |
| Real, retained attempt004 | 10,000 ns | 311,849,604 ns | 918 / 270 |
| Real | 12,000 ns | 430,620,304 ns | 770 / 418 |
| Shuffled | 8,000 ns | 178,958,228 ns | 1,333 / 41 |
| Shuffled, retained attempt004 | 10,000 ns | 268,609,120 ns | 1,104 / 270 |
| Shuffled | 12,000 ns | 377,884,984 ns | 931 / 443 |

Ahead traffic remained 35,823,550,464 bytes for real and 34,068,234,240 bytes
for shuffled. This is descriptive PROJECTED sensitivity: the baseline004 and
new006 receipts retain different `hf_route_horizon_inputs.py` and
`run_prefetch.py` versions (11 of 13 tools match), so the three points do not
establish a same-source causal slope. Inventory/budget/routes/horizon inputs
match; only modeled profile latency differs among those inputs. There is no
generation-completion, GPU-residency, parameter-selection, scientific or
speedup claim. The 17-file independent review is
`results/gold/hf-routing-runner/route-horizon-hf012-latency-sensitivity-semantic-review-attempt-006/`
(manifest SHA256 `898519dc98430337a3fc1a5af73a14302b367da5e214c92d81aadae0b02ef921`).


The next ordinary-future correctness experiment actually exercised executed
overwrite, false overwrite followed by consume, and unused-future exit.
Seven focused CPU methods, one host build and independently reviewed new
PTX/cubin mapping preceded a single three-launch GPU run in 4.452103516 s at
source commit `a61e0ef1decf709587f103055cbda0d4f41d6139`.
Independent recomputation matched all 96 outputs, including 32 retained exit
sentinels, 96 issued/ready futures, 32 consumes, 64 drains, three groups and
192 traces; pending/error/overflow remained zero. Each lane issued once and
terminated once on its executed path. The exact retained cubin loaded
successfully; observable cleanup succeeded and owned processes were reaped.
Context destruction still uses a void API, with completion
`UNOBSERVABLE_VOID_API`. This is `CAPTURED_UNVALIDATED` correctness evidence;
it does not change the non-identifying D/W result or close full lifecycle,
C6.3, G5 or native-completion timing. The nine-file independent review is
`results/gold/timing-future-unit/c6-future-lifecycle-data-review-attempt-001/`
(manifest SHA256 `a0a8408140731ecff8201bd89f96bc61d10ece85317e2a2955bb66ab9ee95240`).

The explicit single-block K1/W1/low known-delay ABBA diagnostic subsequently
ran once in 5.679440482 s at source commit
`7ce8c2ca9a2733fe4b0b1462a11191ee3cd4ea29`. Two focused CPU methods and
direct host compile/link passed. The prior helper/plugin/PTX and libraries
stayed byte-identical; no CMake dependency rebuild changed that experiment.
One D500 warmup preceded four measured A(0), B(500), B(500), A(0) launches.
Independent review found one row/eight ordered events per measured launch,
fixed module/context/allocation identity, epochs 2/3/4/5 and checksum 1.

| Adjacent pair | Wait delta | Other chain work/bookkeeping delta | Whole-chain delta | CUDA Event delta |
| --- | ---: | ---: | ---: | ---: |
| Launch 0 D0 to launch 1 D500 | +512 ns | +32 ns | +544 ns | -12,608.0513 ns |
| Launch 3 D0 to launch 2 D500 | +512 ns | +64 ns | +576 ns | +54,208.0402 ns |

The two chain errors from requested 500 ns are 44/76 ns: descriptive mean
absolute error 60 ns and P95 error 76 ns against the unchanged 100/200 ns limits.
That two-pair diagnostic flag is true; matrix-wide G2 remains open. The other
chain interval includes instrumentation/bookkeeping, and the separate CUDA
Event measurement does not track these lane deltas consistently. This sample
does not establish causality, exclude clock/DVFS effects or prove a scientific
timing result. The actual capture is
`results/gold/known-delay/per-chain-single-block-abba-attempt-001/`; raw SHA256
`796097261f0bcadc28ce17f7b0326352f68a9e561d2a8792744e27426b32b613`.
The uploaded independent review is `results/gold/known-delay/per-chain-single-block-abba-data-review-attempt-001/`
(manifest SHA256 `5afc4ae4a770ad2e232acacd95a78a8a176c6dcef5438fad4842a101e2d16979`).

G2, C6.3, G5, overlap and formal admission remain open; formal DONE is 0.

The dated HF010, route-only, known-delay and earlier C6 sections below are
historical evidence. They do not supersede this current checkpoint.

P0/P1/P6 and bounded CPU portions of P2/P3/P7 are verified. This is an interim
checkpoint, not completion of P0–P8 or permission to launch a formal matrix.
Default-OFF ordinary TIMING future admission is implemented. Guarded native-image
four-case and conditional-consumer correctness observations are captured but
unvalidated. Optimized control-word proof and full GPU gold remain open; TMA
support remains uninstalled. Known-delay, read-only
acquisition, exact-arrival conversion, routing capture closure and native causal
prefetch tools now pass their bounded CPU/compile controls and independent
reviews. Physical storage acquisition remains blocked. The storage pairing adapter now passes
19 tests and review. Host GPU access is available outside the sandbox; initial
known-delay controls ran and exposed a timing defect. No formal handler is active.

## Provenance

- Working checkout: `/root/hbfsim-exp/eval-base-integration`, inside the
  user-authorized experiment container.
- Starting runtime SHA: `fc829992ecdc3ca68881656722b67a31067c5d33`.
- Pre-document source checkpoint HEAD: `5d73bdd9317ce43cc3eacd80cdfbffeebed5cee0`.
  HF012, the P7 capacity/latency replays, C6 lifecycle, single-block
  known-delay acquisition and the routing-capture pilot each retain their own
  source and execution receipts;
  this checkpoint does not retrospectively change those origins. Formal timing,
  performance, physical storage and matrix gates remain open.
  See the [current actual checkpoint](50-run-status.md#actual-checkpoint-hf012-p7-c6-and-routing_capture-01469-2026-09-07).
- Local branch: `eval/eq1-eq4-implementation`; no push, merge or rebase.
- Async donor S: `f4dc28b2671c01939d98e4a968e6fb37b2e364d9`.
- Capacity/routing donor X: `37144843906b3bd71f3fbac1fecc6b5080d82b95`.
- Remote fetch confirmed the frozen refs. Exact graph evidence is in
  `results/gold/integration/`; baseline provenance in `results/gold/base/`.

## Reuse, reimplementation and rejected donor changes

The reviewed phase-one documents and figure/schema tooling were preserved.
The project knowledge pack contains eleven documents and the function map.
Optional parser/async syntax and CPU future-state concepts reuse selected S
files. Parser coverage, finite completion/deadline ordering, predicated
may/must consumption, register-clobber drains and conservative store-alias
drains were repaired against failing tests before integration. General CFG,
unknown def/use families, ordinary async copies and TMA remain closed in this
ordinary-load analysis subset.

The CPU oracle is not the device future ABI. Wholesale S control/request/API
layout replacement, per-lane replacement of base synchronous coalescing,
unsupported architecture instructions and unproven capacity-frame lifetime
were rejected. X's patch-equivalent inventory/placement/serial replay files
were not duplicated. See [donor map](50-integration/sm120-donor-map.md).

## Gold and validation

| Gate | Current evidence | Limit |
|---|---|---|
| GOLD-0 | Exact frozen base 42/42 CPU; original pipeline 20/20 | CPU configuration only |
| GOLD-1 | C6.2 complete opt-in plugin/loader;17 typed actual-plugin assembly controls,49 loader scenarios; all64 CPU checks and18 default-OFF checks closed | Ordinary scalar TIMING subset; no GPU or TMA proof |
| GOLD-2 | CPU future oracle/counterexamples pass; one one-warp four-case GPU correctness diagnostic captured | `CAPTURED_UNVALIDATED`; live-JIT mapping, overlap and full GPU semantic gold remain open |
| GOLD-3 | Representative synchronous SM120 image assembled/disassembled; immutable cache 9/9 controls | Resolver/load/next-address mapping recorded; future/TMA mapping NOT PROVEN |
| GOLD-4 | Current-source rebuild and20/20 CPU/compile checks; ten new guarded D0 K64 controls and one D500 acquired | Corrected local wait measures512ns; full-chain D500 still fails fixed G2. D0 run-mean SD121.024us/access; no full gate closure |
| GOLD-5 | Optional MQSim observer, 12 concurrent replay controls, 10 provenance controls and three-cell CPU pilot pass | CPU media conservation; collector fixtures pass, physical acquisition and hardware causal gold absent |
| GOLD-6 | Real GGUF metadata inventory; 10 negative/accounting tests; three accounting controls | Actual GPU allocator/cache conservation NOT RUN |

Recorded gold directories: `results/gold/base`, `async-counterexample`,
`ptx-parser`, `future-state`, `future-analysis`, `phase-cpu`, `phase-media`,
`inventory`, `mqsim-observation`, `concurrent-replay`, `replay-wrapper`, `scheduler`,
`storage-split`, `storage-collector`, `routing-metrics`, `routing-capture`,
`mqsim-horizon`, `mqsim-service`, `prefetch-replay`, `storage-arrivals`,
`known-delay`, `sass-audit` and `phase-causal-known-delay`.
CPU analysis and oracle units received separate specification and quality
reviews. Scheduler specification/quality reviews pass, and its 46-test final
suite passed in 39.26 s. Concurrent media replay and its manifest wrapper also
pass both reviews. The clock correction passed55/55 CPU tests in33.44s. The later reviewed C6.1 phase passes64/64 in25.54s, recorded in `results/gold/timing-future-unit/c6-emitter/attempt-026/`. The unchanged pipeline
previously passed 20/20 in 4.848 s at the causal/media checkpoint.

Additional targeted CPU gates: storage collector 34/34 (4.122 s), exact-arrival
conversion 8/8, routing metrics 6/6, bounded MQSim clock 4/4 CTests, native
transport 3/3 CTests, causal core 9/9 analytical cases, native client 3/3,
causal CLI 4/4, routing capture closure 26/26 and SASS cache 9/9.
Known-delay runner controls pass 20/20 in 3.676 s, including finite clock-only waits, sparse SM IDs,
exact matrix binding, contamination and coverage negatives. The separate
CUDA-13/g++-13 build and existing helper ABI/PTX tests are compile-only evidence.
Those compile controls launched no kernel. Subsequent explicitly guarded
host-GPU runs are recorded separately below.

The three synthetic media controls issue/complete 12 reads and 196,608 bytes
each. Fixed QD1, fixed QD4 and closed-loop QD4 reached their declared peak QD,
with complete wrapper wall times 0.192/0.174/0.215 s. These are CPU infrastructure
pilots with PROJECTED service, not hardware/model-fidelity evidence. Raw inputs,
commands and hashes are under `results/gold/concurrent-replay/pilot-3-cell/`.

After the 55-test phase passed, a durable three-policy native MQSim pilot ran
once per policy. All source inventory/routes/20-us compute intervals are
synthetic and the outer artifacts remain MOCK. None/on-demand issue nine
requests and 110,592 bytes each; one-layer-ahead issues ten and 122,880 bytes,
including its final useless prefetch. All 28 requests drain and reconcile with
84 native observations. End-to-end CPU wall time is 0.670 s. The policy timing
values in `results/gold/prefetch-replay/pilot-3-policy/summary.json` are scenario
outputs, not real Qwen or serving speedup. Exact commands, inputs and causal/
capacity/traffic checks are retained beside that summary.

A separate three-cell storage pairing pilot used synthetic syscall ledgers and
the native MQSim executable. Each arm reconciles three requests/12,288 bytes.
Fixed-observed, closed-observed and closed-policy complete in
0.488/0.448/0.511 s. The closed-observed arm replays actual fixture timestamps;
the closed-policy arm independently replenishes QD. All outer artifacts are
MOCK; no physical SSD call or fidelity claim is present. See
`results/gold/storage-pair/pilot-3-cell/summary.json`.

Scheduler execution is opt-in and bounded by `--max-runs`. It preserves attempts,
guards the requested resource class, verifies frozen gold receipts, validates
complete artifacts through the same strict exporter before atomically writing
DONE, and never treats project locks as control over foreign processes. No
scientific task registry has been registered or formal matrix launched yet.

## Hardware control development history

These records preserve the scope of each earlier unit. For later actual native
C6 and ABBA results, use the current checkpoint linked above.

C5 infrastructure now passes independent spec and quality reviews. The exact
64-byte token, separate 32-byte metadata, host v4 capability/lifecycle binding,
disabled loader and compiled issue/poll/wait helpers preserve shared ABI4 and
the synchronous path. Five admission/accounting counterexamples are closed.
Focused CPU/compile checks pass 18/18; default-off checks pass 7/7. The final CPU
phase passed 59/60 during an unrelated worker import RED, then that sole failed
target passed its focused recheck. Frozen source and evidence:
`results/gold/timing-future-unit/handoff/attempt-001/`.
Commit `1f0f45b` subsequently closes private C6.1 emission: real setup and byte
spans, typed native bits carried through actual wait returns, executed predicate
state and drains, finite producer allocation and full-span native-store refusal.
Independent reviews pass; final CPU phase64/64 in25.54s and29 direct/helper-linked
PTX programs assemble. The default-OFF helper matches C5 byte-for-byte. Evidence:
`results/gold/timing-future-unit/c6-emitter/handoff/attempt-005/`.
C6.2 is now committed at `49ee96b`. Explicit ON plus CUDA13/sm120 connects the
complete emitter/helper/manifest/loader unit. It checks actual grid/block and
PTX req/max bounds, fixed producer limits and finite cumulative trace capacity.
The module owns65,536x64-byte trace records; launch reservations conservatively
budget three records per static producer per actual lane, with no refund after
unproved enqueue failure. Checked activation publishes enable last; retirement
synchronizes the owning domain, clears enable before alias/config, and quarantines
unproved clears. Ordinary driver and mapped runtime launches are supported;
cooperative, opaque, graph, extras, capacity and unproved model families reject.

Both independent reviews pass. They closed comment/prefix parameter metadata,
coexisting immutable kernel-image subsets and cooperative runtime alias defects.
The actual-plugin tests cover17 typed controls and49 loader scenarios. A fresh
CPU phase passes63/64 in25.93s; a reviewed test-selector correction closes the
sole failure in0.08s, preserving a negative unsafe-identity mutation. No runtime
change was needed for that phase failure. A separate fresh default-OFF build
passes18/18 in18.30s, with its helper byte-identical to C5. Evidence:
`results/gold/timing-future-unit/c6-unit/closure-attempt-001/`,
`handoff/attempt-003/`, `phase-attempt-001/`, `source-guard-attempt-001/` and
`off-phase-attempt-001/` under that same C6 unit root. Optimized native dependency
mapping and GPU gold remain open; no scientific receipt or formal cell was added.

The first ordinary-future SASS research bundle is now retained at
`results/gold/timing-future-unit/c6-mapping/source-audit-attempt-001/`. It records
one archived u32 native/wait-return/consumer register chain. Missing useful
independent work, shared output addresses and unresolved clock/native-completion
ordering prevent semantic approval. Its post-hoc archived-byte receipt is not a
new contemporaneous build record; mapping remains `NOT_PROVEN` and no GPU ran.

A later ordinary-u32 diagnostic did run on HEAD
`e96be28ae4b0be5dacc4f5944c64a554572bef9c`. One block of32 lanes passed all
four fixed same/distinct-page and dense/sparse output checks (128 outputs total).
Its final counters record72 issued,72 model-ready,72 consumed,38 groups issued
and completed,144 trace records, and zero pending, terminal-error and overflow.
Observable module unload, range unregister and frees succeeded; the void context
destroy records called with completion unobservable. The controller duration was
8.308445783 s and its terminal state is `CAPTURED_UNVALIDATED`. The execution
record SHA256 is `cc2fb688d8d0c85278a17a7b68980a10fa715313949cc7ca84dd35f37d727efc`;
the raw diagnostic SHA256 is
`346dbac1219b6191052668518e97c3eb886f62b27a77cc47824470f1040775fa`.
Live driver JIT remains unbound to the prior optimized cubin. This closes only
the narrow output/conservation acquisition, not C6.3, G5, overlap,
native-completion timing, the complete C6.4 lifecycle or TMA.

Initial GPU-unavailable evidence came from the sandbox. Host execution was
verified on 2026-09-05, without a driver repair. The GPU_EXCLUSIVE guard retained
start/periodic/end process snapshots for each actual attempt.

D0 K1 checksum/coverage controls passed. Ten independent D0 K64 repeats then
completed with identical kernel/helper/profile hashes. Their per-run mean delta
standard deviation was 27,811 ns/access; this is not a G2 pass. A single D500 K64
point failed the frozen 100-ns mean / 200-ns P95 absolute-error limits, with
407,185 / 415,088 ns respectively. Its synthetic wait stamps include host-control
reads inside the timed interval (median 615,808 ns for requested 500 ns).
The remaining positive-delay points stopped. Commit `13a416a` now uses cached
scalar delay/timeout in a clock-only helper, with the same pre/post safety checks
for D0 and positive D. The targeted controls and full 55-test CPU phase pass.
A new frozen control plan at `results/gold/known-delay/clock-controls/` stopped
before its first kernel: host preflight found a foreign llama-server using
89,792 MiB. The corrected hardware behavior is unverified; thresholds and the
default resolver remain unchanged.

One old D0 repeat was conservatively marked contaminated when NVML sampled its
own unreaped child. A deterministic CPU counterexample reproduced the loss of
zombie identity. The narrow observation fix preserves exact boot/PID/start
matching; unknown/reused identities still reject. Two new controls, all 46
scheduler tests and independent review pass. The old attempt remains excluded,
and its replacement has a new directory. These standalone controls are outside
the formal scheduler run tree.

Evidence: `results/gold/known-delay/gpu-controls/`, especially
`d0-noise-summary.json`, `d500-k64-r1/raw.analysis.json` and
`d500-k64-r1/diagnostic-spans.json`; CPU repair evidence is under
`results/gold/scheduler/gpu-zombie-*`.

## Model, storage and resource development history

The dated units below retain their original conclusions. HF010 generation and
the subsequent route-only experiment now supplement them; timed HF-to-P7 and
physical storage validation remain open.

The existing `/root/hbfsim-exp/phase3/models/Qwen3-30B-A3B-f16.gguf`
contains 579 tensors, 48 layers, E=128 and k=8. All 6,144 expert identities
reconcile at 9,437,184 bytes each: 57,982,058,496 eligible bytes and
3,107,774,464 resident non-offloaded bytes. Config/tensor metadata is hashed;
weight payloads are not reread or rehashed. The initial inventory contract
supports the verified qwen3moe F16/F32 packed layout and rejects other layouts.

The project HF safetensors view now passes a current bounded metadata refresh.
The tool passes 26 CPU controls and both reviews; the real run takes 1.822 s and
reads exactly 19,912,432 metadata bytes. Its 16 shards contain 18,867 BF16 tensors,
6,144 experts and 3,082,186,752 resident non-offloaded bytes. The complete frozen
bundle and a subsequent current-input check pass. Receipt:
`results/manifests/hf-qwen3-30b-a3b-metadata-20260905/`. Historical payload hashes
retain their original provenance. No model load, new payload hash or real routing
capture is claimed; the HF and GGUF identities are not interchangeable.

The HF inventory/budget adapter now passes 13 controls and both reviews, with
metadata 26/26, GGUF 10/10, routing 6/6 and prefetch 4/4 regressions. Real frozen
adaptation took 2.371 s and produced a 9,625,635-byte inventory without opening
checkpoint payloads. HF-specific hypothetical rho 1/16, 1/2 and 1 controls pass
with 384/3072/6144 whole experts. Evidence:
`results/gold/hf-inventory-adapter/`; normalized file:
`results/manifests/hf-qwen3-30b-a3b-evaluation-inventory-20260905.json`.
HF route/projection joins remain pending; this adds no capture or live cache gold.

The HF worker protocol helpers pass 10 CPU controls, and the private runtime
observer passes 10 additional controls and both reviews. It binds actual
execution config aliases, raw model identity, per-layer backends/callbacks and
usable KV allocation, with detached report values. Evidence:
`results/gold/hf-routing-runner/runtime-contract-attempt-001/`.
No worker entry point, actual generation or capture-origin proof is enabled.

The owned route-memory scope also passes 11 CPU controls and both reviews.
It prevents collision fallback into preexisting buffers, retains partially
initialized owned handles, and verifies teardown/restoration without broad
namespace deletion. The combined adapter directory passes 57/57 CPU tests in
0.236 s; evidence is `results/gold/hf-routing-runner/helpers-phase-attempt-001/`.
No POSIX segment or inference runtime was used by this helper phase. Real worker
construction/generation, source/cache checks and parent triplet remain pending.

The selected runtime source freezer passes15 CPU controls and both reviews;
shared metadata compatibility passes27/27 after bounding individual reads to
1MiB. A real131-artifact source/metadata snapshot and current recheck passed in
0.289s at2026-09-05T17:46:01Z. It saved7,961,389 bytes plus separate interpreter
identity under `results/gold/hf-routing-runner/runtime-sources-real-attempt-001/`.
No inference package was imported. The snapshot excludes complete native binary
authentication and capture-origin claims; import/cache observation, worker
execution and the parent native/capture/repeat closure remain pending.

The passive runtime import/cache observer is now committed at `c12d3b0`, with
11 CPU tests and both reviews. It reconciles loaded module origins and effective
private destinations without runtime imports or cache-creation calls. Module
substitution, active log redirection and live Triton manager overrides are
rejected. Source/import controls pass26/26 and existing adapters57/57; evidence
is `results/gold/hf-routing-runner/imports-phase-attempt-001/`. Actual controlled
imports, worker execution/cleanup and parent triplet validation remain pending.

The private owned-request body is committed at `6444b71`. It composes the
reviewed helpers around one generated return, freezes raw JSON/route copies
before trace materialization, and independently records cleanup and restoration.
Native accepts the installed scheduler's absent reader field; capture/repeat
require the actual owned reader. Removed singleton fields, output ancestry
changes and final status-write failure retain failed diagnostics and cannot
return provisional success. Both reviews pass; related phase controls pass
38/38 plus57/57 CPU tests (5.180 +0.260s suites). Evidence:
`results/gold/hf-routing-runner/loaded-arm-phase-attempt-001/`. All generated
objects are CPU fixtures; no real inference import, model load, generation or
POSIX segment occurred. Controlled import entrypoint, selected tuning inputs,
parent resource/exit/current-input checks and independent triplet validation
remain unfinished. The body has no CLI and writes no completion marker.

The optional `MOE_TUNING_V1` runtime-source extension is committed at `cf37966`.
It adds exactly two selector/override sources and preserves byte-identical
validation of the base131-artifact contract. Eighteen source tests and both
reviews pass. The real133-artifact snapshot/current recheck at
2026-09-05T18:49:21Z passed in0.328s, freezing7,998,813 source-metadata bytes;
all131 older buffers match. Evidence:
`results/gold/hf-routing-runner/runtime-sources-tuning-real-attempt-001/`.
Selected tuning JSON/absence and actual loaded tuning state remain separate
unfinished gates; no inference import or GPU execution occurred.

The selected MoE input freezer is committed at `351cd8a`; the related HF
receipt-provenance correction is `4a7b1ee`. The receipt's validated evidence
fields now determine MOCK attribution even when runtime-source evidence is real.
Both reviews pass. Related phase regression passes107/107 CPU tests (50 tuning,
worker, source/import controls in7.513s;57 adapter controls in0.272s). Evidence:
`results/gold/hf-routing-runner/tuning-phase-attempt-001/`.

Real selected-input acquisition at2026-09-05T19:05:18Z passed in2.373s and found
no packaged tuning JSON for the derived E=128,N=768 and the GPU name declared
from the dated14:13 probe. It freezes that exact absence, with no tuning-file
bytes read, and rechecks the133-artifact runtime-source snapshot unchanged.
Evidence: `results/gold/hf-routing-runner/tuning-inputs-real-attempt-001/`.
No current GPU query or loaded configuration observation occurred. The later
worker must reconcile its actual device, weights and override/batch state before
claiming the installed-default configuration path. The parent/import/exit and
independent triplet-validation gates remain unfinished.

The owned subprocess helper now has a default-false `bootstrap_no_site` keyword
(`3f62bf1`). Explicit true runs the existing identity-acknowledgement wrapper
with `-S -B`, preserving default argv, resource checks and exact owned cleanup.
Three real CPU subprocess controls and both reviews pass; the existing46-test
scheduler suite passes in38.819s. Evidence:
`results/gold/hf-routing-runner/bootstrap-attempt-001/` and
`bootstrap-phase-attempt-001/`. No real HF worker is exposed by this option;
the final controlled import/target process remains a separate pending unit.

The frozen-route decoder (`0a7619b`) accepts the bounded NPY v1/v2 integer
subset without importing NumPy or allocating from an advertised shape. It
validates exact protocol geometry/dtype, payload length, expert bounds and
top-k uniqueness, returning detached tuples. Six targeted tests and both reviews
pass;29 decoder/owned-body/protocol controls pass in3.536s. Evidence:
`results/gold/hf-routing-runner/route-array-phase-attempt-001/`.
This is a primitive for later token/trace/triplet reconciliation; it provides no
capture-origin, model execution or scientific receipt.

Budgets deduct actual inventory resident bytes plus explicitly supplied KV,
workspace and reserve inputs. Requested raw rho, whole-expert achieved rho,
unused bytes and legacy ratio are separate. The three CPU controls use
hypothetical total device budgets, not observed GPU capacity.

The selected storage candidate is
`results/storage-inputs/read-only-benchmark.bin`. It was not created or read:
the project resides on root NVMe `/dev/nvme4n1p2`, and exclusivity is unproven.
Sandbox queries cannot communicate with the GPU, but host queries and real
controls succeeded on RTX PRO 6000 Blackwell Server Edition (595.84), UUID
`GPU-f07ea2df-1b6f-9a02-b534-5090abf3c174`, 97,887 MiB total. No driver or GPU
settings were changed. The root-SSD restriction remains unchanged.

Every matrix condition now has one of the five requested resource classes.
Formal GPU timing requires exclusivity; routing capture without timing is
shared-safe; physical SSD collection is storage-exclusive; three-arm timing
requires both. Offline calculations remain CPU-only. Current status is in
[run status](50-run-status.md), identity evidence in `results/manifests/`.

## Readiness and remaining work

| EQ | Status | Remaining |
|---|---|---|
| EQ1 | BLOCKED | Known-delay and collector tools pass bounded CPU/compile controls; full-chain G2 failure/noise, exclusive SSD and paired scientific validation remain |
| EQ2 | PARTIAL | Opt-in ordinary TIMING CPU/compile controls and bounded native-image GPU correctness observations exist; control-word proof, D/W timing, full GPU gold and TMA lifecycle remain open |
| EQ3 | PARTIAL | Real inventory/budgets and native media replay available; bounded layer-synchronous controller verified; real compute input and actual capacity/cache gates remain |
| EQ4 | PARTIAL | HF010 real diagnostic routes, single-member metrics and causal route-only inventory/budget experiment completed; authentic origins, real device intervals and timed projection validation remain open |

No EQ is ready for formal measured performance claims. Do not claim full GPU
async semantics, TMA overlap, full cp.async coverage, SASS-preserved ordering,
SSD ground truth fidelity, live-serving speedup, observed rho/cache behavior,
or completed minimum experiments. Thermal remains outside the main EQ work;
no thermal runtime stack was added.

## Recorded verification commands

These are historical command records, not a request to repeat the suites.
The latest bounded executions and observed costs are in the current checkpoint.

Run from `/root/hbfsim-exp/eval-base-integration`:

```sh
cmake -S . -B build-eval-implementation -G Ninja -DCMAKE_BUILD_TYPE=Debug -DHBFSIM_ENABLE_CUDA=OFF -DHBFSIM_ENABLE_MQSIM=ON -DHBFSIM_ENABLE_EVAL_TOOLS=ON -DHBFSIM_ENABLE_LLM_TESTS=OFF -DBUILD_TESTING=ON -DCMAKE_DISABLE_FIND_PACKAGE_PkgConfig=ON
cmake --build build-eval-implementation -j4
ctest --test-dir build-eval-implementation --output-on-failure -j1
python -m unittest discover -s scripts/eval -p test_eval_pipeline.py
python -m unittest discover -s scripts/eval -p test_inventory_checkpoint.py
python -m unittest discover -s scripts/eval -p test_run_matrix.py
python -m unittest discover -s scripts/eval -p test_replay_arrivals.py
python scripts/eval/inventory_checkpoint.py /root/hbfsim-exp/phase3/models/Qwen3-30B-A3B-f16.gguf --output results/manifests/qwen3-30b-a3b-inventory.json
python -m unittest discover -s scripts/eval -p test_storage_split.py
python -m unittest discover -s scripts/eval -p test_collect_storage.py
python -m unittest discover -s scripts/eval -p test_routing_metrics.py
python -m unittest discover -s scripts/eval -p test_prefetch_replay.py
python -m unittest discover -s scripts/eval -p test_mqsim_service_client.py
python -m unittest discover -s scripts/eval -p test_run_prefetch.py
python -m unittest discover -s scripts/eval -p test_storage_arrivals.py
python -m unittest discover -s scripts/eval -p test_replay_storage_pair.py
python -m unittest discover -s scripts/eval -p test_verify_hf_metadata.py
python scripts/eval/verify_hf_metadata.py validate --out results/manifests/hf-qwen3-30b-a3b-metadata-20260905
HBFSIM_DELAY_COMPILE_BUILD=build-eval-known-delay-gcc13 python -m unittest discover -s scripts/eval -p test_run_gpu_delay.py
python -m unittest discover -s scripts/eval -p test_audit_sass_mapping.py
python scripts/eval/run_matrix.py --status
```

The exact known-delay configure/build/helper checks and representative mapping
are in [known-delay design](50-integration/known-delay-harness.md) and
`results/gold/known-delay/`. The successful cached audit command is:

```sh
python3 scripts/eval/audit_sass_mapping.py --cubin results/gold/known-delay/sass-control/kernel.cubin --ptx results/gold/known-delay/sass-control/transformed.ptx --build-manifest results/gold/known-delay/sass-control/build-manifest.json --cuda-bin /usr/local/cuda-13.0/bin --out results/gold/sass-audit/representative-known-delay
```

C6.2 exact configure/build/test argv and compiler caches are retained in
`results/gold/timing-future-unit/c6-unit/phase-attempt-001/commands.json` and
`off-phase-attempt-001/commands.json`. Those runs used new
`build-eval-c6-unit-cpu` and `build-eval-c6-unit-off`; preserve the frozen older
builds. The ON actual-plugin and corrected phase checks were:

```sh
ctest --test-dir build-eval-c6-unit-cuda --output-on-failure -R '^(timing_future_unit_plugin|timing_future_unit_loader|timing_future_plugin|timing_future_loader)$'
ctest --test-dir build-eval-c6-unit-cpu --output-on-failure
ctest --test-dir build-eval-c6-unit-cpu --output-on-failure -R '^launch_gate_symbols$'
ctest --test-dir build-eval-c6-unit-off --output-on-failure -R '^(range_table|timing_binding|context_lifecycle|coverage_gate|module_identity|ptx_transform|ptx_async_copy_coverage|coverage_manifest_flow|device_range_validation|cuda_module_association|timing_gate_binding|timing_future_unit_plugin|ptxpass_plugin|unsupported_kernel_ptx|device_helper_ptx|mqsim_online|mqsim_queue_depth|mqsim_benchmark)$'
```

Each three-cell replay manifest records the exact C++ command. Repeat a cell
through `scripts/eval/replay_arrivals.py` with its frozen profile/arrival inputs,
`--source-kind synthetic_control`, the recorded arrival mode, current validated
binary, and a **new** `--out` directory. Do not overwrite the original pilot.

Execution logs preserve exact output paths, temporary-directory overrides,
compiler/CMake options, failures and durations. Continue the outstanding CPU
implementation, then update this checkpoint. Formal pilots and the minimum
matrix remain behind their applicable gates; the ideal matrix requires review.

Resumed hardware result,2026-09-06: see [current known-delay diagnosis](50-integration/known-delay-harness.md#resumed-hardware-check-2026-09-06). The old clock-loop defect is not reproduced in the new local wait stamps, but critical-chain error remains far above G2. No formal runs were added.


The passive loaded-MoE tuning observer is committed at `5a518a2`. It retains
the source-bound preconstruction aliases, validates actual registered BF16
Parameter geometry and raw unquantized descriptors after the existing runtime
observer, and closes with a stored-state-only pass after tensor metadata reads.
The final report records the last observed environment getter state. Both the
batch configuration flag and installed mode must be exactly false, with the
source-defined initialization/override bookkeeping still at its None baseline.
This rejects an activated or partially initialized mode even when its flag is
false. The old stale-environment-report quality failure and new batch-state
failure are preserved before their fixes. All22 targeted tests pass; independent
SPEC passes25 including3 additional report-boundary controls, and independent
QUALITY approves the same frozen sources. Evidence:
`results/gold/hf-routing-runner/resume-tuning-attempt-001/` and
`resume-tuning-spec-attempt-001/`. No inference library/model/GPU/SHM was used by
these tests. Integration into the owned request is the next unit; the helper
does not establish effective kernel configuration, native-binary/cache/device
authentication or scientific validation.


Owned HF request composition is committed at `ffd928e`. Frozen tuning inputs
are validated before output creation/runtime callbacks. Source-bound retention
precedes construction; loaded tuning observation follows the runtime contract
observer and precedes sampling/generation. The input binding records selected
tuning identities and the declared device, and `runtime-tuning.json` preserves
the observed report. Both new failure stages retain existing cleanup and primary
plus cleanup diagnostics. All17 focused controls pass; independent SPEC and
QUALITY reviews pass the same frozen files. The related CPU phase passes82 eval
HF and31 adapter HF tests. Evidence:
`results/gold/hf-routing-runner/loaded-tuning-integration-attempt-001/` and
`loaded-tuning-phase-attempt-001/`. No real HF arm, model load, GPU acquisition or
scientific validation is implied by these fixture controls. Controlled startup,
the guarded three-process parent and independent frozen capture validation
remain incomplete.


The declared startup environment/passive observation correction is committed at
`5561e4e`. Preparation now sets numeric visibility0 for the explicitly narrowed
single-physical-GPU unit, PCI bus ordering, the two installed vLLM import-time
constants, empty plugin selection and the supported TVM DLPack opt-out. The
observer requires `_LIB` to be absent, rejecting even None or a private-looking
library object, and reports `DISABLED_BY_DECLARED_CONFIGURATION`. The remaining
environment, origin, cache and forbidden-import checks are preserved. All12
observer and11 protocol controls pass; independent SPEC and QUALITY pass, then
83 eval-HF plus32 adapter-HF phase tests pass. Evidence:
`results/gold/hf-routing-runner/startup-environment-attempt-001/` and
`startup-environment-phase-attempt-001/`. The helper does not authenticate GPU
topology: fresh parent single-device/no-MIG inventory, physical-UUID guard and
dual CUDA/vLLM identity checks remain mandatory in the later owned entrypoint.
No real runtime import, model load or capture ran during these CPU controls.
The supplemental seven-source binding and controlled worker/parent/validator
remain the next implementation units.


The fixed supplemental startup source binding is committed at `3833dac6`.
`hf_startup_sources.py` freezes exactly seven selected Python/stub files under
the validated MOE_TUNING_V1 root and joins their ancestor identities to the
primary snapshot. Primary and supplemental source metadata share the existing
128 MiB budget. Frozen validation opens no original paths; a descriptor-anchored
reader separately rereads the same seven files. The 131/133 primary formats remain
unchanged. Directory descriptors and no-follow opens reject changed roots and
ancestor redirection at the read boundary; identities and paths are rechecked
after reading. This narrow reader replaces the shared snapshot helper only in
this unit because that helper re-resolves its confinement root.
Independent SPEC and QUALITY reviews pass after correcting negative tests that
previously failed at unrelated gates. The initial fixture failure is not semantic
RED evidence; subsequent bounded mutation controls are labeled as such. The later
root-redirection defect has genuine RED evidence: six pre-fix collect/recheck
failures, followed by successful directory-race and descriptor-cleanup controls.
The final 15 focused tests and 41 related source/tuning tests pass.
Evidence is under `results/gold/hf-routing-runner/startup-sources-attempt-004/`
and `startup-sources-phase-attempt-001/`.

The read-only real acquisition in `startup-sources-real-attempt-002/` matches
all seven previously audited source hashes: 551,080 supplemental bytes,
8,549,893 combined bytes. The new primary snapshot and
supplemental inputs both pass their current-file checks. The original primary
correctly failed its check because filesystem device IDs changed from66312 to
66311. Diagnostic comparison found1042 device fields changed, with all133 artifact
hashes, interpreter hash and every other manifest field unchanged. An independent
observation in `runtime-sources-tuning-real-attempt-002/` records the new identity;
old evidence and the failed startup acquisition remain preserved. No identity
normalization or automatic within-run refresh was added. This binds selected
source metadata only; it neither imports inference libraries nor authenticates
all runtime native binaries or checkpoint payloads. Controlled worker/parent and
independent raw-token/route/trace validation remain open. No real HF arm or formal
matrix row ran, and scientific_validation_passed remains false.


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

Actual guarded diagnostic runs and their failures are now recorded in the
[latest standalone checkpoint](50-run-status.md#standalone-real-experiment-checkpoint-2026-09-06).
HF008 started2026-09-06T18:03:21.357470Z on c7ee007 and completed after
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
K1 hardware controls fail G2. Formal acceptance and COMPLETE remain open.

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


## Implemented formal measurement path, 2026-09-07, first D0 slice

Server backup stream PID2916359 completed before edits; server SHA256 receipt is retained in
`results/batches/20260907-formal-matrix-resume/server-transfer.sha256`. Old data retained.
Source updates77c60ab/b13d3b3 add scoped timing repairs, original-matrix coverage export and
a real D0 producer/independent validator under the existing scheduler.

Strict sealed coverage: **30/20485 full planned runs**,
**30/13105 minimum runs**. Three original
conditions gpu_delay-00001..00003 each have10 independent process repeats; three separate
pilots do not count. All30 formal attempts completed; signed noise and negative findings
are retained. Full G2 and every nonzero fidelity threshold remain unevaluated by theseD0
receipts. The complete task remains PARTIAL.

Machine evidence: `matrix-coverage.d0-30.csv`, `formal-d0-export-001/` (90metrics/30runs),
`d0-30-statistics.json`, `d0-progress.json` in the batch. No six-figure completion is claimed.
Remaining D0=27conditions/270repeats requires larger-grid pilots and lossless raw storage;
straight uncompressed scaling50–60GB exceeds available workspace headroom.

New independent diagnostics: original1TiB MQSim init129requests and historicalHF012 prefix
three-policy replay1492requests each pass bounded resource/accounting checks; no physical
SSD or fullgeneration result. Two-blockK1 ABBA chainMAE/P95=536/652ns fails100/200ns limits
while localwaitdelta=512ns. C6 observer scan preparation is implemented; current matchedD20
acquisition retained as CAPTURED_UNVALIDATED, with G5/overlap still open.

External gaps: natural-input dataset/split/length/seed and16prompt/B32–128 conflict await
user contract amendment; no authorized exclusive physicalSSD testfile. Unattempted rows
remain PLANNED with explicit missing gates/handlers. No old outputs are overwritten.
