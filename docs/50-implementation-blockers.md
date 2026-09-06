# Evaluation implementation blockers

Status is specific to this checkout and this execution; historical PASS records
do not close the current gold gates.

| ID | State | Evidence and impact | Next action |
|---|---|---|---|
| KNOWN-DELAY | OPEN - CURRENT HARDWARE G2 FAILED | Earlier K64 D500 mean/P95 error was 280.364/286.042 us. New K1/W1 hardware D0 and D500 triplets ran with unchanged inputs; D500 mean/P95 error is 141,964.170/154,644 ns against unchanged 100/200 ns limits, although all 188 local waits are 512 ns. Evidence: `results/gold/known-delay/chain-length-diagnostic-attempt-001/`. | Diagnose the residual full-chain timing cost using a bounded implementation change before the next declared comparison. K1 is not a fix; local wait excess does not explain the chain error. No exact counter-contention cause or G2 closure is established. |
| GPU-BUSY | NOT PRESENT AT RESUMED CHECK | Read-only observation2026-09-06 found no foreign compute processes and0MiB GPU memory use; the subsequent known-delay guard permitted all completed arms. Prior busy evidence remains historical. Evidence: `results/gold/hf-routing-runner/resume-resource-observation-001/` and resumed control guard logs. | Recheck under the existing guard before every later launch; this observation is not a future reservation. All resumed control children exited. |
| WORKSPACE | RESOLVED | `/root/hbfsim-exp` is an experiment container, not a Git root. After user-authorized branch discovery, `/root/hbfsim-exp/eval-base-integration` contains the exact frozen base and all required 49-audit files. | Use only this nested checkout for implementation. |
| BASE-CONFIG | RESOLVED | Fresh configure without the frozen libbpf-discovery exclusion fails. Reproducing `CMAKE_DISABLE_FIND_PACKAGE_PkgConfig=ON` builds the exact base and passes 42/42 CPU tests; existing evaluation pipeline passes 20/20. | Preserve frozen options and baseline evidence in `results/gold/base/frozen-config/`; no production build workaround was introduced. |
| GPU-DRIVER | RESOLVED FOR HOST EXECUTION | Sandbox NVML queries fail, but authorized host queries and real guarded controls succeed on GPU-f07ea2df-1b6f-9a02-b534-5090abf3c174, RTX PRO 6000 Blackwell Server Edition, driver595.84. Evidence: `results/gold/known-delay/gpu-controls/*/raw.gpu.jsonl`. | Run GPU work through the host execution boundary with unchanged resource guards; no system repair. |
| ASYNC-INTEGRATION | C6.2 CPU/COMPILE CLOSED; SASS/GPU GOLD OPEN | Commit `49ee96b` closes opt-in plugin/helper/loader/identity, actual geometry and trace-budget admission. Both reviews pass; all64 CPU checks close after one test-only selector correction, and18 default-OFF checks pass with unchanged helper bytes. Actual-plugin typed17 and loader49 scenarios are CPU/compile evidence. | Complete C6.3 optimized dependency mapping, then guarded C6.4 ordinary GPU gold. Keep TMA/capacity/reference/hybrid/empirical families closed. |
| FUTURE-SASS | NOT_PROVEN | One archived optimized u32 control exposes the native/wait-return/consumer register chain, but contains no useful independent work or unique multi-lane outputs. Clock/native-completion ordering still needs scoreboard/control/call evidence. Audit: `results/gold/timing-future-unit/c6-mapping/source-audit-attempt-001/`. | Build a bounded gold control with retained work/unique outputs and validate exact dependencies. No hardware defect or semantic PASS is inferred from instruction order alone. |
| STORAGE-TARGET | BLOCKED_STORAGE_BUSY | User authorizes selecting a safe project path. Selected candidate is `results/storage-inputs/read-only-benchmark.bin`, not created. Project filesystem shares root disk `/dev/nvme4n1p2` (Lexar ARES 4TB); no exclusive SSD I/O-path evidence. Identity and mount evidence: `results/manifests/storage-device.json`. | Keep physical acquisition closed; continue CPU replay/tooling. No payload I/O, benchmark-file initialization, writes, trim or format performed. |
| CHECKPOINT | METADATA VERIFIED | Found project-local `/root/hbfsim-exp/phase3/models/Qwen3-30B-A3B-f16.gguf`, a 61,095,802,848-byte allocated file. Embedded metadata: 48 layers, E=128, k=8. Inventory reconciles 579 tensors, 6,144 experts and 57,982,058,496 eligible bytes. | Use `results/manifests/qwen3-30b-a3b-inventory.json`; payload hash/GPU load not performed. Actual GPU fast-tier capacity and live cache gold remain unverified. |
| HF-CHECKPOINT | METADATA VERIFIED; USED IN REAL DIAGNOSTICS | The current `results/manifests/hf-qwen3-30b-a3b-metadata-20260906-resume001/` receipt and retained runtime/startup/tuning snapshots were used in guarded attempts001-003. Their postchecks pass. HF view remains 16 shards, 18867 BF16 tensors, 6144 experts. | Retain these bindings for the next fresh diagnostic; all three attempts stopped before model construction. Do not substitute GGUF identity or claim fresh weight-payload hashes. |
| HF-STARTUP | IMPORT ENVIRONMENT CPU CORRECTED; REAL TUNING RETRY OPEN | UUID fix `f5d7592` passed actual Torch/vLLM gates in002. Passive Transformers fix `3a7cb61` passed both independent reviews and31/31 related CPU tests; actual003 runtime-imports records all pinned versions, then fails at `tuning-retention` with `HF tuning runtime: environment differs from prepared allowlist`, before model construction. | The full-import query identified exactly three library-created environment values; fix `b136c68` passed independent reviews and 54 related tests without changing startup library lookup. Run the fresh guarded diagnostic to exercise the correction in the real request. Preserve UNVALIDATED status and all failed evidence; owned-origin validation/publication still precedes formal acceptance or COMPLETE. |
| ROUTING-INPUT | OPEN | Bounded routing capture/materialization, trace composition and native causal projection tools now pass CPU controls. No authenticated real route trace or matched compute-only timing trace was found in the project search. | Keep synthetic controls MOCK. Obtain current real capture/native/repeat evidence and matching compute input before scientific projection validation. |
| CAPACITY-LIFETIME | OPEN FOR CAPACITY/TMA | Consumer-held frame leases and dynamic TMA lifecycle remain unproven. A complete kernel-local FAST-scalar TIMING future can retain ABI4; capacity lifetime is not its blocker. | Implement the complete timing capability/identity/loader/helper/transform unit separately; keep capacity/reference/hybrid/empirical/TMA future admission closed. |
| FORMAL-HANDLERS | OPEN | Scheduler infrastructure passes its gates, but scientific producer/validator registrations and required real gold receipts are not yet frozen for matrix cells. | Complete bounded tools and paired input adapters, then register only supported gate-bound tasks. All formal replicates remain planned. |
| STORAGE-FIT | OPEN | Split/view/profile receipt infrastructure passes CPU checks; no physical calibration, fit receipt or heldout comparison has run. | Freeze actual source inputs and authorized device before collection; never label nominal-profile CPU replay VALIDATED_MODEL. |

Starting runtime SHA: `fc829992ecdc3ca68881656722b67a31067c5d33`.
Remote fetch succeeded; origin base, async donor, and capacity donor still match
the frozen audit. Source and graph evidence are in `results/gold/integration/`.

No experiment result is implied by this blocker log. Failed configuration is
not a test PASS, GPU tools on disk are not GPU availability, and unverified
inventory is not measured model identity.

Known-delay read-only lifecycle design: `results/gold/known-delay/resume-lifetime-design-attempt-001/report.json` binds the reviewed sources. Registration/enqueue/unregister ownership supports investigating immutable metadata, but same-generation unregister/compaction defeats module-bind-only snapshots and dynamic liveness checks cannot move to admission without a new proof. Recommended first diagnostic is disjoint observed per-chain counters/traces with all existing safety checks, geometry and G2 limits unchanged. It is not implemented and supplies no new hardware evidence.


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

Actual guarded diagnostic runs and their failures are now recorded in the
[latest standalone checkpoint](50-run-status.md#standalone-real-experiment-checkpoint-2026-09-06).
All three HF attempts stopped before model construction; 003 passed passive
runtime observation and failed at tuning-retention environment validation.
The correction for the three observed import-time settings is committed at
`b136c68b8c1e3c619babc8f445f8d295079e86bd`; the next action is guarded diagnostic attempt004. K1 hardware controls still fail G2. Formal acceptance and COMPLETE remain
open; diagnostic execution continues under the existing resource guard.

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
