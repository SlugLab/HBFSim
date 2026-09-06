# Evaluation implementation blockers

Status is specific to this checkout and this execution; historical PASS records
do not close the current gold gates.

| ID | State | Evidence and impact | Next action |
|---|---|---|---|
| KNOWN-DELAY | OPEN — CURRENT HARDWARE G2 FAILED | Fresh current-source build and20/20 CPU/compile checks passed. Ten resumed D0 controls have run-mean SD121.024us/access; D500 chain mean/P95 absolute error280.364/286.042us exceeds unchanged G2, although each local wait is512ns for requested500ns. Evidence: `results/gold/known-delay/resume-clock-controls-attempt-001/`. | Preserve failure/noise and diagnose full-chain overhead before more timing cells. D5000/D20000 were not launched; no formal claim closes. |
| GPU-BUSY | NOT PRESENT AT RESUMED CHECK | Read-only observation2026-09-06 found no foreign compute processes and0MiB GPU memory use; the subsequent known-delay guard permitted all completed arms. Prior busy evidence remains historical. Evidence: `results/gold/hf-routing-runner/resume-resource-observation-001/` and resumed control guard logs. | Recheck under the existing guard before every later launch; this observation is not a future reservation. All resumed control children exited. |
| WORKSPACE | RESOLVED | `/root/hbfsim-exp` is an experiment container, not a Git root. After user-authorized branch discovery, `/root/hbfsim-exp/eval-base-integration` contains the exact frozen base and all required 49-audit files. | Use only this nested checkout for implementation. |
| BASE-CONFIG | RESOLVED | Fresh configure without the frozen libbpf-discovery exclusion fails. Reproducing `CMAKE_DISABLE_FIND_PACKAGE_PkgConfig=ON` builds the exact base and passes 42/42 CPU tests; existing evaluation pipeline passes 20/20. | Preserve frozen options and baseline evidence in `results/gold/base/frozen-config/`; no production build workaround was introduced. |
| GPU-DRIVER | RESOLVED FOR HOST EXECUTION | Sandbox NVML queries fail, but authorized host queries and real guarded controls succeed on GPU-f07ea2df-1b6f-9a02-b534-5090abf3c174, RTX PRO 6000 Blackwell Server Edition, driver595.84. Evidence: `results/gold/known-delay/gpu-controls/*/raw.gpu.jsonl`. | Run GPU work through the host execution boundary with unchanged resource guards; no system repair. |
| ASYNC-INTEGRATION | C6.2 CPU/COMPILE CLOSED; SASS/GPU GOLD OPEN | Commit `49ee96b` closes opt-in plugin/helper/loader/identity, actual geometry and trace-budget admission. Both reviews pass; all64 CPU checks close after one test-only selector correction, and18 default-OFF checks pass with unchanged helper bytes. Actual-plugin typed17 and loader49 scenarios are CPU/compile evidence. | Complete C6.3 optimized dependency mapping, then guarded C6.4 ordinary GPU gold. Keep TMA/capacity/reference/hybrid/empirical families closed. |
| FUTURE-SASS | NOT_PROVEN | One archived optimized u32 control exposes the native/wait-return/consumer register chain, but contains no useful independent work or unique multi-lane outputs. Clock/native-completion ordering still needs scoreboard/control/call evidence. Audit: `results/gold/timing-future-unit/c6-mapping/source-audit-attempt-001/`. | Build a bounded gold control with retained work/unique outputs and validate exact dependencies. No hardware defect or semantic PASS is inferred from instruction order alone. |
| STORAGE-TARGET | BLOCKED_STORAGE_BUSY | User authorizes selecting a safe project path. Selected candidate is `results/storage-inputs/read-only-benchmark.bin`, not created. Project filesystem shares root disk `/dev/nvme4n1p2` (Lexar ARES 4TB); no exclusive SSD I/O-path evidence. Identity and mount evidence: `results/manifests/storage-device.json`. | Keep physical acquisition closed; continue CPU replay/tooling. No payload I/O, benchmark-file initialization, writes, trim or format performed. |
| CHECKPOINT | METADATA VERIFIED | Found project-local `/root/hbfsim-exp/phase3/models/Qwen3-30B-A3B-f16.gguf`, a 61,095,802,848-byte allocated file. Embedded metadata: 48 layers, E=128, k=8. Inventory reconciles 579 tensors, 6,144 experts and 57,982,058,496 eligible bytes. | Use `results/manifests/qwen3-30b-a3b-inventory.json`; payload hash/GPU load not performed. Actual GPU fast-tier capacity and live cache gold remain unverified. |
| HF-CHECKPOINT | METADATA VERIFIED | The project HF view `phase3/models/Qwen3-30B-A3B` passes the new verifier: 16 shards, 18,867 BF16 tensors, 6,144 experts, exact index/header/accounting. Read19,912,432 metadata bytes; frozen and second current checks pass. Receipt: `results/manifests/hf-qwen3-30b-a3b-metadata-20260905/`. | Bind this observation plus unchanged historical donor to native/capture/repeat; do not substitute GGUF F16 identity or describe historical payload hashes as freshly verified. GPU capture remains unrun. |
| HF-STARTUP | CONTROLLED ENTRYPOINT OPEN | `ffd928e` closes request tuning gates; `5561e4e` closes fixed startup environment; `3833dac6` closes the seven-source supplemental binding with focused/phase tests, independent reviews and real source-only acquisition. | Implement the guarded worker and native/capture/repeat parent, then independent frozen raw/trace validation. Fresh single-device/no-MIG inventory and dual CUDA/vLLM identity checks remain mandatory; no real HF arm ran. |
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

HF bytecode-cache closure is a required pending entrypoint gate: -B prevents writes but still reads valid .pyc. The planned parent and worker use fresh private sys.pycache_prefix before project imports; the existing intermediate launcher receives a narrowly bound PYTHONPYCACHEPREFIX transport value, while the final isolated worker uses explicit -X and removes that transport environment field before normal runtime checks. Namespace initializer absence must cover source, sourceless bytecode and native loader suffixes. This startup flow is not yet implemented or tested.
