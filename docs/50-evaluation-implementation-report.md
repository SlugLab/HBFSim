# Phase-two implementation checkpoint — ongoing

P0/P1/P6 and bounded CPU portions of P2/P3/P7 are verified. This is an interim
checkpoint, not completion of P0–P8 or permission to launch a formal matrix.
Default-OFF ordinary TIMING future admission is implemented; its optimized SASS
and GPU gold remain open, and TMA support remains uninstalled. Known-delay, read-only
acquisition, exact-arrival conversion, routing capture closure and native causal
prefetch tools now pass their bounded CPU/compile controls and independent
reviews. Physical acquisition remains blocked. The storage pairing adapter now passes
19 tests and review. Host GPU access is available outside the sandbox; initial
known-delay controls ran and exposed a timing defect. No formal handler is active.

## Provenance

- Working checkout: `/root/hbfsim-exp/eval-base-integration`, inside the
  user-authorized experiment container.
- Starting runtime SHA: `fc829992ecdc3ca68881656722b67a31067c5d33`.
- Ending committed implementation SHA at this checkpoint: `3833dac6` (fixed supplemental HF startup source binding; C6.2 remains frozen at `49ee96b`).
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
| GOLD-2 | CPU future oracle and counterexamples pass | GPU semantic gold NOT RUN |
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

## Current hardware controls and correction

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

## Model, storage and resources

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
| EQ2 | PARTIAL | Complete opt-in ordinary TIMING unit has CPU/compile closure; optimized SASS/GPU gold and TMA lifecycle remain open |
| EQ3 | PARTIAL | Real inventory/budgets and native media replay available; bounded layer-synchronous controller verified; real compute input and actual capacity/cache gates remain |
| EQ4 | PARTIAL | Routing statistics, capture closure and causal prefetch CPU controls verified; real routing/compute inputs and scientific projection validation remain |

No EQ is ready for formal measured performance claims. Do not claim GPU
async correctness, TMA overlap, full cp.async coverage, SASS-preserved ordering,
SSD ground truth fidelity, live-serving speedup, observed rho/cache behavior,
or completed minimum experiments. Thermal remains outside the main EQ work;
no thermal runtime stack was added.

## Exact verification commands

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


Owned-entrypoint review checkpoint, 2026-09-06: the uncommitted worker and
provisional native/capture/repeat parent have an initial 16-test CPU record, but
independent SPEC review requires corrections before acceptance. The review
confirmed a missing fixed site import path, device checks occurring after vLLM
imports, mutable/deleted acknowledgements accepted by later callbacks,
incomplete prefix/preload checks, signal/failure finalization gaps, missing
after-arm wire/source checks and precise failure stages, discarded device
observations, and unbounded topology-command output. Evidence is
`results/gold/hf-routing-runner/owned-entrypoint-spec-attempt-001/review.json`
and its bounded CPU `diagnostics.json`.

Corrections have been returned uncommitted in a fresh
`owned-entrypoint-attempt-002/`. Its initial 24-test RED records 6 failures and
4 errors; previous attempt-001 is preserved. The final manifest binds23 artifacts
and five successful runs, including focused tests, actual owned child controls,
valid MOCK isolated preflight, isolated import probing and static checks. The
valid preflight reaches its private callback with existing metadata/runtime/
startup/tuning/current-input checks intact. This is not final unit acceptance:
independent SPEC re-review, QUALITY and applicable phase regression remain.
No real HF model arm, routing-origin receipt, scientific receipt or formal
matrix row has been produced. Formal DONE remains 0.


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
