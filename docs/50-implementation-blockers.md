# Evaluation implementation blockers

## Current blocker delta — 2026-09-07

- **HF012 acquisition:** all three real arms returned identical generated tokens.
  Native did not capture routing; capture/repeat 39x48x8 route arrays match. Their
  seven decode token slots x 48 layers supply 336 nodes and 335 adjacent
  intervals. The triplet remains provisional and does not authenticate origin or
  close timing/performance gates.
- **P7 route-horizon replay:** implementation controls pass. Actual attempt001
  stopped at the old bounded metadata reader; attempt002 passed the reader and
  HF input bridge, then MQSim exited with `std::bad_alloc` before its first
  header under the 2 GiB process limit. Neither of those attempts produced a
  valid policy cell. A
  56 GiB-derived zero-request capacity/init probe subsequently passed
  header/finish with all counters zero; its 164,192 KiB RSS covers initialization
  only. Six-cell attempt003 then completed, but all prefetch and extra bytes were
  zero under the 384-expert cache configuration (48 layers x eight routed
  experts). `none` serializes same-layer misses; on-demand batches them, and the
  inactive one-layer-ahead policy matches on-demand. Independent review checked
  all 2,304 prediction candidates per series as resident and ready and matched
  one-layer-ahead requests/nodes to on-demand item for item. A rho=1/32 run may
  be used only as an explicit non-original-matrix sensitivity diagnostic; it has
  not run and has no result. No prefetch benefit was observed.
  An independent standard-library accounting recheck passed in 0.516548358 s;
  generation completion, GPU consumption, hardware/scientific validation and
  speedup claims all remain false. A smaller-cache diagnostic remains pending
  independent review.
- **C6 future/native control:** globaltimer-compatible future004, native001 and
  the repaired host build are retained with mapping validation still
  `NOT_PROVEN`. GPU002 was rejected by the resource guard while a foreign GPU
  process existed. GPU003 later completed, and its independent arithmetic
  checks pass, but every D=20,000 ns future was ready before work began and
  K0/K4096 work medians were both 7,040 ns. The result is `NON_IDENTIFYING` for
  W/overlap and does not close C6.3 or G5.
- **Gate state:** known-delay G2, C6.3/G5, physical storage, formal handlers and
  formal matrix execution remain open. Formal DONE remains 0.

The table and dated notes below preserve earlier checkpoint history. Where their
"latest" wording differs, this current delta governs.

Status is specific to this checkout and this execution; historical PASS records
do not close the current gold gates.

| ID | State | Evidence and impact | Next action |
|---|---|---|---|
| KNOWN-DELAY | OPEN - CURRENT HARDWARE G2 FAILED | New per-chain K1 D0 mean absolute noise167310.128ns; D500 mean/P95 error106758.213/117620ns against unchanged100/200ns. Local waits512ns. Actual rows and independent reviews: `results/gold/known-delay/per-chain-k1-data-review-attempt-001/`. | Same-process ABBA now captured two pairs: mean absolute error versus500ns is5083.489/13193.532ns, still above original100ns threshold. Wait increment512ns; cause and formal G2 remain open. Evidence `results/gold/known-delay/per-chain-abba-attempt-001/`. |
| HOST-MEMORY | REAL HF010 TRIPLET COMPLETED | HF010 loaded56.88GiB per arm and all3 arms returned. Prior HF005 and first256MiB consistency MemoryErrors remain preserved; consistency later passed at1GiB. | Use existing runtime guards and stage-appropriate finite CPU limits. These observations neither reserve resources nor prove the cause of earlier failures. |
| GPU-BUSY | LATEST GUARDED RUNS RETURNED | HF010, native-image C6, conditional-consumer C6 and per-chain delay attempts acquired through existing guards; earlier foreign-job rejection is retained. | Recheck guard at every new run; never affect foreign work. |
| WORKSPACE | RESOLVED | `/root/hbfsim-exp` is an experiment container, not a Git root. After user-authorized branch discovery, `/root/hbfsim-exp/eval-base-integration` contains the exact frozen base and all required 49-audit files. | Use only this nested checkout for implementation. |
| BASE-CONFIG | RESOLVED | Fresh configure without the frozen libbpf-discovery exclusion fails. Reproducing `CMAKE_DISABLE_FIND_PACKAGE_PkgConfig=ON` builds the exact base and passes 42/42 CPU tests; existing evaluation pipeline passes 20/20. | Preserve frozen options and baseline evidence in `results/gold/base/frozen-config/`; no production build workaround was introduced. |
| GPU-DRIVER | RESOLVED FOR HOST EXECUTION | Sandbox NVML queries fail, but authorized host queries and real guarded controls succeed on GPU-f07ea2df-1b6f-9a02-b534-5090abf3c174, RTX PRO 6000 Blackwell Server Edition, driver595.84. Evidence: `results/gold/known-delay/gpu-controls/*/raw.gpu.jsonl`. | Run GPU work through the host execution boundary with unchanged resource guards; no system repair. |
| ASYNC-INTEGRATION | C6.2 CLOSED; NATIVE DIAGNOSTICS ACQUIRED; FULL GOLD OPEN | Exact native four-case run128outputs/72consumes and new conditional three-case run96outputs/96consumes returned CAPTURED_UNVALIDATED. | Continue trueD0/knownD, D/W and full lifecycle gold. Keep broader TMA/capacity/reference/hybrid/empirical admission closed. |
| FUTURE-SASS | NATIVE IMAGE BOUND; CONTROL PROOF NOT_PROVEN | New conditional cubin0804821c... is passed as the same checked buffer to the driver. Both SASS dumps preserve conditional-first and unconditional-second consumer paths. | Scoreboard/control semantics, physical load completion and actual overlap remain unproved; exact image binding alone does not close C6.3/G5. |
| STORAGE-TARGET | BLOCKED_STORAGE_BUSY | User authorizes selecting a safe project path. Selected candidate is `results/storage-inputs/read-only-benchmark.bin`, not created. Project filesystem shares root disk `/dev/nvme4n1p2` (Lexar ARES 4TB); no exclusive SSD I/O-path evidence. Identity and mount evidence: `results/manifests/storage-device.json`. | Keep physical acquisition closed; continue CPU replay/tooling. No payload I/O, benchmark-file initialization, writes, trim or format performed. |
| CHECKPOINT | METADATA VERIFIED | Found project-local `/root/hbfsim-exp/phase3/models/Qwen3-30B-A3B-f16.gguf`, a 61,095,802,848-byte allocated file. Embedded metadata: 48 layers, E=128, k=8. Inventory reconciles 579 tensors, 6,144 experts and 57,982,058,496 eligible bytes. | Use `results/manifests/qwen3-30b-a3b-inventory.json`; payload hash/GPU load not performed. Actual GPU fast-tier capacity and live cache gold remain unverified. |
| HF-CHECKPOINT | MODEL AND GENERATION OBSERVED | HF010 real16-shard BF16 native/capture/repeat arms returned identical8-token outputs; capture/repeat39x48x8 routes matched. | Retain actual source/model bindings; do not substitute metadata identity or claim fresh payload hashes. |
| HF-STARTUP | SCOPED COMPATIBILITY EXERCISED BY HF010 | Ray compatibility bbe2d0a requires startup absence and actual post-construction RAY_CLIENT_MODE=0; HF010 exercised accepted value and generated all3arms. | Formal source-origin/publication and COMPLETE still require their separate gates. NCCL process-group teardown was not proven by owned process exit. |
| ROUTING-INPUT | REAL DIAGNOSTIC ROUTES; TIMING JOIN OPEN | HF010 consistency passed1872events; actual single-member metrics processed336decode routes. No authenticated origin or matching compute-only CUDA-event trace exists. | Exact HF inventory/budget route-only join has now run:336demands/289predictions/288matches/1terminal, real1191 vs shuffled1362 expert overlaps at rho1/16. Obtain new device timing before timed prefetch; never use JSONL materialization time as compute. |
| CAPACITY-LIFETIME | OPEN FOR CAPACITY/TMA | Consumer-held frame leases and dynamic TMA lifecycle remain unproven. A complete kernel-local FAST-scalar TIMING future can retain ABI4; capacity lifetime is not its blocker. | Implement the complete timing capability/identity/loader/helper/transform unit separately; keep capacity/reference/hybrid/empirical/TMA future admission closed. |
| FORMAL-HANDLERS | OPEN | Scheduler infrastructure passes its gates, but scientific producer/validator registrations and required real gold receipts are not yet frozen for matrix cells. | Complete bounded tools and paired input adapters, then register only supported gate-bound tasks. All formal replicates remain planned. |
| STORAGE-FIT | OPEN | Split/view/profile receipt infrastructure passes CPU checks; no physical calibration, fit receipt or heldout comparison has run. | Freeze actual source inputs and authorized device before collection; never label nominal-profile CPU replay VALIDATED_MODEL. |

Starting runtime SHA: `fc829992ecdc3ca68881656722b67a31067c5d33`.
Remote fetch succeeded; origin base, async donor, and capacity donor still match
the frozen audit. Source and graph evidence are in `results/gold/integration/`.

No experiment result is implied by this blocker log. Failed configuration is
not a test PASS, GPU tools on disk are not GPU availability, and unverified
inventory is not measured model identity.

Known-delay read-only lifecycle design: `results/gold/known-delay/resume-lifetime-design-attempt-001/report.json` binds the reviewed sources. Registration/enqueue/unregister ownership supports investigating immutable metadata, but same-generation unregister/compaction defeats module-bind-only snapshots and dynamic liveness checks cannot move to admission without a new proof. Recommended first diagnostic is disjoint observed per-chain counters/traces with all existing safety checks, geometry and G2 limits unchanged. The per-chain diagnostic was subsequently implemented at c1abccc0/b12b650 and actually ran D0/D500 with coherent rows; G2 remains failed. Its source/actual reviews identify work around the wait without proving a cause. The original module-bind-only snapshot optimization is still not implemented.


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

HF bytecode-cache startup is implemented and covered by bounded CPU controls: -B prevents writes but does not prevent reading valid .pyc. Parent and worker establish fresh private sys.pycache_prefix before project imports; the intermediate launcher receives the narrowly bound PYTHONPYCACHEPREFIX transport, while the final isolated worker uses explicit -X and removes the transport field before runtime checks. Namespace-initializer absence covers source, sourceless bytecode and native loader suffixes. This closes the implemented startup contract's CPU gate; actual runtime/device and scientific capture gates remain open.


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

Actual guarded diagnostic runs and their failures are recorded in the
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
K1 hardware controls still fail G2; formal DONE remains0.

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
