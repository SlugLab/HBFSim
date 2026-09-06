# vLLM and MoE integration

## Purpose

Separate live timing registration and exact Triton binding from offline expert-object replay, and identify the missing inputs for serving-concurrency and whole-model claims.

## Scope

B's vLLM timing adapter and optional capacity replay tools. Real routing capture/staging from X (`37144843906b3bd71f3fbac1fecc6b5080d82b95`) is a donor candidate, not an installed base feature. Snapshot convention: [reading order](00-reading-order.md).

## Key concepts

Finalized tensor storage, original/recompiled PTX variant, registered byte extent, complete expert object, actual active sequence and selected expert weight bytes are different identities/metrics. A batch size configured on `LLM.generate` is not a per-step measurement of active serving concurrency. Selected tensor bytes are not automatically GPU-transferred bytes.

## Important files

[Model loader](../../adapters/vllm/hbfsim_loader.py), [native bridge](../../adapters/vllm/hbfsim_extension.cpp), [Triton binding](../../adapters/vllm/triton_binding.py), [PTX staging](../../adapters/vllm/prepare_triton_ptx.py), [runner](../../adapters/vllm/run.py), [inventory](../../adapters/vllm_capacity/model_inventory.py), [placement](../../adapters/vllm_capacity/placement_policy.py), [replay](../../adapters/vllm_capacity/trace_replay.py), [trace validation](../../adapters/vllm_capacity/trace_validation.py), and [serial timing backend](../../benchmarks/replay/hbf_trace_timing.cpp).

[Controlled worker](../../scripts/eval/hf_owned_worker.py) and [provisional triplet parent](../../scripts/eval/hf_routing_runner.py) implement the owned HF entrypoints.

## Important structs/classes/functions

`TimingConfig.from_mapping` validates adapter options. `HbfSimModelLoader.load_model` delegates normal loading before registration. `_discover_storages` and `register_model_storages` select and deduplicate finalized CUDA storage ranges; `NativeTimingSession.register_storage` calls the native bridge. `TritonVariantBinder.on_kernel_load` uses the exact original function, PTX bytes and kernel name; `install_triton_binding` installs that hook. `ModelInventory` reads an existing manifest, validates model/expert/tensor records and supplies `compact_tensor_accesses`. `capacity_geometry` implements legacy ratio placement; `replay_cell` reports cache/access/reuse and modeled demand timing.

`capture_project_sources`/`recheck_project_sources` bind finite project inputs. `execute_owned` checks isolated startup, wire/request/ownership and staged runtime gates before `run_loaded_arm`. `run_triplet` owns guarded sequential processes, checks acknowledgement/bootstrap evidence and records provisional or failed finalization.

## Call path / data path

Live timing: runner configures local environment/report paths → vLLM loads finalized model → loader registers selected storage → Triton hook recovers original PTX and exact variant mapping → launch gate → rewritten supported accesses → timing delay → deterministic generation and reports.

Offline: supplied inventory plus validated route-derived trace → complete expert objects → placement/cache policy → demand pages → optional fast/hybrid/MQSim timing tool → per-cell report. In B's C++ `run_reference`, each page is submitted and completed before the next page; this is serial modeled demand time, not a live compute/memory overlap timeline.

Owned HF: isolated parent → fixed source/input freeze → one resource guard → native/capture/repeat owned launch → worker bootstrap and staged gates → existing loaded arm → independent postchecks/cleanup → durable provisional status. Frozen semantics and owned publication remain pending.

## CPU-side vs GPU-side execution context

Loader, hook, inventory and replay orchestration are Python on CPU; the native bridge controls the host HBFSim context. Live vLLM kernels run on GPU. Offline replay and its MQSim backend can run without vLLM/GPU execution. A GPU-origin trace does not make a later CPU replay a measured serving run.

## Invariants

Original Triton functions must bind to rewritten functions from the same PTX digest and name; ambiguous name-only selection is unsafe. Capacity pointers remain strict even though timing-backed opaque paths can run unmodeled. Preserve all baseline/timing token IDs and nonzero modeled-access evidence for the selected subset. Derive E/k/layers/bytes from actual config/inventory, never from a model-name default. Whole-expert policy objects include both w13 and w2 under the inventory's equal-size contract.

## Supported behavior

Selective physically backed timing registration, storage deduplication and registration manifests, exact Triton variant binding, deterministic generation comparison, validated supplied expert inventories, complete-object CLOCK/LRU/Belady offline placement and serial timing replay. Belady's future knowledge is an offline oracle, not an implementable runtime predictor. B's separate prefetch model is also offline and behind optional evaluation tooling.

The phase-two `trace_collector.py` and `routed_capture_compat.py` port only route materialization and explicit reversible callback binding. They preserve request/tensor identity but do not launch vLLM or authenticate capture origin. The validation CLI now keeps missing native/repeat controls INCOMPLETE and CPU fixtures TEST_ONLY; tensor consistency alone cannot produce real-routing gold. `routing_metrics.py` and `run_prefetch.py` consume separate frozen routing and compute inputs as TRACE_COMPOSED projections, never live serving.

The private [HF runtime observer](../../adapters/vllm_capacity/hf_runtime_contract.py)
checks constructed execution objects, config aliases and actual backend classes.
`GPUModelRunner.get_model()` unwraps graph wrappers, so eager verification also
requires `runner.model` to be that raw model. The installed allocator reserves
one null KV block; deduct it when checking usable token capacity. A configured
capture callback or native sampler binding is not proof it executed: greedy
sampling can return from argmax before top-k/top-p sampling. The worker entry
point and independent capture-origin validator remain unfinished.

The private [owned route-memory scope](../../adapters/vllm_capacity/hf_owned_routes.py)
prevents the installed creator's collision fallback from attaching or replacing
an existing segment. Its module-local proxy retains original-class handles,
including partial initialization, and binds cleanup to observed descriptors.
The real reader uses default `create=False,size=0`. Engine shutdown alone does
not close these buffers, and native cleanup swallows errors: verify saved handles
and the exact owned name, then require process exit separately. Name-based unlink
and resource-tracker cleanup are not atomic against hostile namespace replacement.

The private [runtime source freezer](../../scripts/eval/hf_runtime_sources.py)
reads a finite131-artifact installed set, rejects duplicate discoverable package
metadata and source aliases, and rechecks exact frozen identities. Fifteen CPU
controls and a real7,961,389-byte snapshot pass. This does not authenticate all
native binaries or prove runtime import/cache selection. Evidence is in
`results/gold/hf-routing-runner/runtime-sources-real-attempt-001/`.

The optional `MOE_TUNING_V1` source extension adds only the fused-MoE package
initializer and batch-invariant selector, preserving the base snapshot format.
Eighteen source tests and both reviews pass; the real133-artifact snapshot and
recheck preserve all131 base buffers. This extension does not select a tuning
JSON or observe loaded kernel configuration.

The private [import/cache observer](../../scripts/eval/hf_runtime_imports.py)
checks already-loaded modules and effective paths, including actual open log
streams and native Triton environment overrides. It does not call the import-
capable manager descriptors, runtime discovery, CUDA or cache-creation methods.
Eleven CPU tests and both reviews pass; the related source/import plus adapter
phase passes83 tests. This does not yet create a guarded executable worker.

Installed-environment compatibility note, 2026-09-06: before changing startup
observers, consult the retained runtime snapshot and the relevant installed
source text. The fixed interpreter is `/opt/miniconda3/bin/python3.13`; package
metadata records vLLM `0.15.1`, Transformers `5.5.4`, Triton `3.5.1` and Torch
`2.9.1`, while Torch's installed version module records `2.9.1+cu128` and CUDA
`12.8`. These are snapshot-bound inputs, not permission to upgrade the runtime
or proof that a model has executed.

The real Transformers module is the exact already-loaded
`transformers.utils.import_utils._LazyModule`. In the observed state its raw
module dictionary has no `__version__` key; its raw `_objects` is an exact dict
containing the stored string `__version__ == "5.5.4"`. The installed initializer
passes that value through `extra_objects`, explaining why a raw top-level-only
version check rejects this valid installed representation. A passive version
observer must support this narrowly bound stored-state case while retaining
ordinary stored-version handling and the existing exact version/source checks.
Read already-loaded raw dictionaries; do not invoke lazy attribute getters,
perform discovery/imports, materialize a cache entry, or add a generic fallback
for arbitrary objects or packages. For this installed class the version getter
returns `_objects` directly, but other lazy attribute paths can import or cache
values; observing stored state avoids relying on those paths.

Evidence is in
`results/gold/hf-routing-runner/real-transformers-storage-observation-attempt-001/`:
the CPU-only observation found exact loaded-class identity and unchanged module
keys, with GPU visibility disabled and no model constructed. The corrective
observer implementation and its later real pilot must retain their own outcome;
this observation alone is not successful model execution. Reading
`import_utils.py` for implementation guidance does not add that file to the
existing frozen source contract or authenticate all installed dependency code.

The private [owned request body](../../scripts/eval/hf_loaded_arm.py) composes
these helpers around one generated return. Save the copied raw result before
collector callbacks and preserve the primary error through independent cleanup.
Native has no scheduler reader attribute in the installed implementation;
capture/repeat must bind the actual owned reader. Missing singleton fields must
not skip saved-handle cleanup or client shutdown. Final status persistence
failure returns FAILED. Thirteen CPU composition tests preserve metadata-evidence attribution even
when runtime sources are real; related tuning/source/import and adapter
regression passes107/107 after both reviews. This body has no executable
entrypoint, resource ownership gate, current-input recheck or completion marker.

The [selected tuning input freezer](../../scripts/eval/hf_moe_tuning.py) derives
E/N from verified weight geometry and binds one packaged JSON or its absence.
It joins directory identities to the source snapshot and binds the endpoint
before opening. Eight CPU controls pass. The real declared-device candidate has
no matching packaged JSON; this is input evidence, not an observed loaded kernel
configuration. Check actual device/weights and override/batch state separately.

The existing [owned subprocess helper](../../scripts/eval/run_matrix.py) accepts
`bootstrap_no_site=True` for the future HF caller. Its default is unchanged;
explicit true skips intermediate interpreter site hooks and bytecode writes,
retaining the identity acknowledgement, resource checks and owned cleanup.
Three CPU subprocess controls and46 scheduler regressions pass. This helper
does not prepare the target's import paths or authenticate a real HF arm.

The [frozen-array decoder](../../scripts/eval/hf_route_array.py) checks at most
1MiB of NPY bytes with a4096-byte header before decoding the exact protocol
shape/dtype. It rejects malformed/duplicate fields, invalid element counts,
trailing bytes and invalid/duplicate top-k IDs, without a NumPy reader. Six
controls and both reviews pass, with29 related CPU tests in3.536s. The later
validator must still reconcile token identities, every trace event and all three
owned process records; a valid array is not proof of its source.


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

The next execution is one guarded real32-input/8-output native/capture/repeat
diagnostic pilot using the existing runner. Later artifact/source/publication
work gates formal acceptance and COMPLETE; it does not prevent this explicitly
UNVALIDATED trial. Preserve actual failures and raw returns, and choose the next
minimal repair from that evidence. No formal matrix or real scientific PASS is
claimed by the CPU acceptance. Formal DONE remains0.

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

## Explicitly unsupported behavior

B does not supply actual scheduler route capture, general checkpoint scanning, full-model capacity staging, measured active sequences at every decode step, closed whole-device rho budget, concurrent decode projection, or a runtime prefetch producer. External model inventory paths and old Qwen proof runs do not establish a currently available checkpoint. Opaque timing allowances are not full byte coverage.

The current project has distinct GGUF F16 and HF BF16 checkpoint views. A
[metadata verifier](../../scripts/eval/verify_hf_metadata.py) verifies bounded
headers, config/index, complete tensors and file identity without rehashing
weights. `validate_refresh` requires a final completion marker;
`check_current_inputs` rechecks the accepted observation. Historical payload
hashes remain historical evidence. Never
substitute their fingerprints because expert dimensions happen to agree.

The additive [evaluation inventory adapter](../../scripts/eval/evaluation_inventory.py)
normalizes the published HF buffers and feeds `budget_fast_tier`. It retains
shard-local tensor extents, derives KV from explicit head_dim, and carries receipt,
observation, historical-donor and exact-file identities. It does not reopen the
recorded weights/cache. HF route/projection joins remain a separate pending
integration; a valid budget is not observed GPU residency.

## Common failure modes

Same-name Triton specializations being swapped; treating zero rejection count as complete coverage; comparing changed prompts/tokens/dtypes; interpreting legacy fast:HBF ratios as effective rho after KV/workspace/resident weights; substituting synthetic MoE streams for Qwen routing; using a uniform union null as actual routing; or calling composed independent traces live serving concurrency.

## Tests proving the behavior

The [pure frozen trace verifier](../../scripts/eval/hf_frozen_trace_verifier.py)
and [its controls](../../scripts/eval/test_hf_frozen_trace_verifier.py) now pass
15 module methods within the final83 related tests. The acceptance and retained
controller diagnosis above limit this to supplied-buffer consistency.

Existing [loader](../../adapters/vllm/tests/test_hbfsim_loader.py), [runner](../../adapters/vllm/tests/test_run.py), [Triton binding](../../adapters/vllm/tests/test_triton_binding.py), [native extension](../../tests/integration/vllm_extension_test.cpp), [placement policy](../../adapters/vllm_capacity/tests/test_placement_policy.py), [replay](../../adapters/vllm_capacity/tests/test_trace_replay.py), and [replay/timing integration](../../tests/integration/test_trace_replay_timing.py) tests cover local contracts. No vLLM model was loaded for this document. Historical [vLLM timing proof](../proofs/2026-08-11-vllm-timing-adapter.md) and [exact live-delay proof](../proofs/2026-08-11-vllm-exact-live-delay.md) retain their original snapshot and selected-range scope.

The [HF adapter controls](../../scripts/eval/test_evaluation_inventory.py) cover
frozen-only acquisition, receipt/buffer substitution, byte/KV accounting,
exclusive output, and canonical-versus-file identity. See the
[HF integration contract](../50-integration/hf-inventory-adapter-plan.md).

The [runtime observation controls](../../adapters/vllm_capacity/tests/test_hf_runtime_contract.py)
reject hidden execution wrappers, wrong backends/config aliases, missing layer
callbacks, mutable report references and insufficient usable KV capacity. These
tests use CPU object fixtures; they do not import the inference runtime.

The [owned memory controls](../../adapters/vllm_capacity/tests/test_hf_owned_routes.py)
cover collisions, foreign-name refusal, partial initialization, exact cleanup,
missing cleanup APIs, interrupts and module-binding restoration using fake
descriptors/namespaces. The adapter-directory phase passes 57/57 CPU tests.

The [controlled-worker tests](../../scripts/eval/test_hf_owned_worker.py) and [triplet-parent tests](../../scripts/eval/test_hf_routing_runner.py) cover the bounded owned-entrypoint contract with CPU/MOCK controls. Attempt006 passed40 focused tests and210 related phase tests; attempt007 normalized line endings only and phase002 binds210 passing tests to the final bytes. These counts overlap and do not prove a real GPU run.

## What not to change casually

Storage ownership/lifetime, deduplication, exact PTX identity and teardown, strict capacity policy, tensor byte hashes, object granularity and trace schema. Do not edit installed vLLM/Triton or another user's model/cache. Add capture/budget/concurrent replay in the experiment layer with explicit provenance before changing the production runtime.

## Related docs

[Evaluation protocol](09-evaluation-protocol.md), [coverage policy](03-cuda-memory-semantics.md), [capacity](07-capacity-address-translation.md), [capability audit](../49-eval-audit/current-capability-audit.md), [historical adapter design](../superpowers/specs/2026-08-10-vllm-hbf-timing-adapter-design.md), and [variant-binding design](../superpowers/specs/2026-08-11-vllm-triton-variant-binding-design.md).
