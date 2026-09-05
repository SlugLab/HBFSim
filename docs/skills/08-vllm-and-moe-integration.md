# vLLM and MoE integration

## Purpose

Separate live timing registration and exact Triton binding from offline expert-object replay, and identify the missing inputs for serving-concurrency and whole-model claims.

## Scope

B's vLLM timing adapter and optional capacity replay tools. Real routing capture/staging from X (`37144843906b3bd71f3fbac1fecc6b5080d82b95`) is a donor candidate, not an installed base feature. Snapshot convention: [reading order](00-reading-order.md).

## Key concepts

Finalized tensor storage, original/recompiled PTX variant, registered byte extent, complete expert object, actual active sequence and selected expert weight bytes are different identities/metrics. A batch size configured on `LLM.generate` is not a per-step measurement of active serving concurrency. Selected tensor bytes are not automatically GPU-transferred bytes.

## Important files

[Model loader](../../adapters/vllm/hbfsim_loader.py), [native bridge](../../adapters/vllm/hbfsim_extension.cpp), [Triton binding](../../adapters/vllm/triton_binding.py), [PTX staging](../../adapters/vllm/prepare_triton_ptx.py), [runner](../../adapters/vllm/run.py), [inventory](../../adapters/vllm_capacity/model_inventory.py), [placement](../../adapters/vllm_capacity/placement_policy.py), [replay](../../adapters/vllm_capacity/trace_replay.py), [trace validation](../../adapters/vllm_capacity/trace_validation.py), and [serial timing backend](../../benchmarks/replay/hbf_trace_timing.cpp).

## Important structs/classes/functions

`TimingConfig.from_mapping` validates adapter options. `HbfSimModelLoader.load_model` delegates normal loading before registration. `_discover_storages` and `register_model_storages` select and deduplicate finalized CUDA storage ranges; `NativeTimingSession.register_storage` calls the native bridge. `TritonVariantBinder.on_kernel_load` uses the exact original function, PTX bytes and kernel name; `install_triton_binding` installs that hook. `ModelInventory` reads an existing manifest, validates model/expert/tensor records and supplies `compact_tensor_accesses`. `capacity_geometry` implements legacy ratio placement; `replay_cell` reports cache/access/reuse and modeled demand timing.

## Call path / data path

Live timing: runner configures local environment/report paths → vLLM loads finalized model → loader registers selected storage → Triton hook recovers original PTX and exact variant mapping → launch gate → rewritten supported accesses → timing delay → deterministic generation and reports.

Offline: supplied inventory plus validated route-derived trace → complete expert objects → placement/cache policy → demand pages → optional fast/hybrid/MQSim timing tool → per-cell report. In B's C++ `run_reference`, each page is submitted and completed before the next page; this is serial modeled demand time, not a live compute/memory overlap timeline.

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

The private [owned request body](../../scripts/eval/hf_loaded_arm.py) composes
these helpers around one generated return. Save the copied raw result before
collector callbacks and preserve the primary error through independent cleanup.
Native has no scheduler reader attribute in the installed implementation;
capture/repeat must bind the actual owned reader. Missing singleton fields must
not skip saved-handle cleanup or client shutdown. Final status persistence
failure returns FAILED. Twelve CPU composition tests plus related source/import
and adapter tests pass95/95 after both reviews. This body has no executable
entrypoint, resource ownership gate, current-input recheck or completion marker.

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

## What not to change casually

Storage ownership/lifetime, deduplication, exact PTX identity and teardown, strict capacity policy, tensor byte hashes, object granularity and trace schema. Do not edit installed vLLM/Triton or another user's model/cache. Add capture/budget/concurrent replay in the experiment layer with explicit provenance before changing the production runtime.

## Related docs

[Evaluation protocol](09-evaluation-protocol.md), [coverage policy](03-cuda-memory-semantics.md), [capacity](07-capacity-address-translation.md), [capability audit](../49-eval-audit/current-capability-audit.md), [historical adapter design](../superpowers/specs/2026-08-10-vllm-hbf-timing-adapter-design.md), and [variant-binding design](../superpowers/specs/2026-08-11-vllm-triton-variant-binding-design.md).
