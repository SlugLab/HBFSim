# HF inventory adapter implementation plan

> Plan only. The implementation worker should use executing-plans and TDD,
> followed by independent spec and quality reviews. This plan authorizes no
> model loading, GPU work, source-weight reads, cache writes, or commits.

**Goal:** Feed the current HF BF16 metadata and matching routes into capacity
budgets and CPU prefetch projections without borrowing a GGUF identity or layout.

**Architecture:** Add one format-dispatched evaluation-inventory adapter. Keep
the GGUF parser/validator and legacy capture inventory intact; require the
published frozen HF refresh whenever an HF evaluation inventory is consumed.
Carry the same explicit model binding through budget, routing, and projection.

**Tech stack:** Python, existing metadata validators and CPU fixtures, native MQSim for a bounded MOCK pilot.

## Frozen evidence and current interfaces

Paths are relative to `/root/hbfsim-exp/eval-base-integration`; planning read only sources and frozen metadata.
Starting bundle: `results/manifests/hf-qwen3-30b-a3b-metadata-20260905`:

```text
receipt_sha256=d40980fea11f3b9b2ca12c26e282fee40ae196e7e004d10889432416f292c8cb
metadata_identity_sha256=6bd086d9258aeec88aa3294df6133c289c0a8d5b557f7ce03b5c7dc6644d7494
observation_identity_sha256=7db277cf3ca52ad295b50be84f90383ba9210b778333ffd623dd53f731b319b2
legacy_inventory_sha256=ed64d4c0bd11ee75cb4cd1de4e82ed1b0071fbc4389b8b974e9275382a1e912a
historical_model_fingerprint=af52de6efe45aa0e0fe9fe393985a25daa16306c440effe493a12ba10e03dda9
```

Its table has 18,867 BF16 tensors in 16 shards, 48 layers, 128 experts/layer,
top-k 8, and 6,144 experts of 9,437,184 bytes each. Expert bytes are
57,982,058,496; total tensor payload is 61,064,245,248; resident bytes are
3,082,186,752. GGUF's 3,107,774,464 resident bytes differ by 25,587,712.
Equal expert totals establish neither checkpoint nor route identity.

`inventory_checkpoint.validate_inventory` assumes packed GGUF names/extents.
`routing_metrics.load_inventory` additionally accepts the legacy donor format;
retain that route-only path. `budget_fast_tier` stores canonical JSON identity
as `inventory_sha256`, whereas routing and compute inputs use exact-file SHA.
`run_prefetch.prepare` already assigns dense logical addresses independently of
source offsets; no service/controller address-generation change is needed.

## Adapter and immutable input contract

Add `scripts/eval/evaluation_inventory.py` with these bounded entry points:

```python
load_hf_snapshot(bundle)                 # validated receipt + exact artifact bytes
adapt_hf_inventory(snapshot, page_bytes)  # deterministic normalized HF document
validate_evaluation_inventory(inv, *, hf_snapshot=None)
```

Dispatch on exact schema/format: existing schema 1 `GGUF` delegates to the
existing validator; new schema 2 `HF_SAFETENSORS` requires an HF snapshot and
exact recomputation of its normalized document. Reject unknown/missing format
and incompatible schema; never detect HF by model name or dimensions alone.
Legacy donor handling stays solely in the existing routing branch.

`load_hf_snapshot` must accept only a published bundle passing
`verify_hf_metadata.validate_refresh`. Acquire regular bounded metadata files,
check each acquired buffer against the validated receipt/marker/artifact hashes,
and parse those same buffers. Recheck exact artifact set and file identities at
the end; mutation after validation must not supply different derived bytes.
Reuse verifier limits and strict duplicate/nonfinite JSON rejection. Never call
`check_current_inputs`, follow recorded source paths, or open checkpoint shards.

CLI: `evaluation_inventory.py --metadata-refresh BUNDLE --output NEW_JSON --page-bytes 16384`.
Validate completely before atomically publishing an
exclusive new regular file; reject existing destinations and source/input/cache
aliases, confine outputs to this checkout, and refuse formal `results/runs`.
Keep output size bounded at 16 MiB: store the tensor table once, with expert
projection references rather than repeating whole tensor records. No truncation.

HF fields: `format=HF_SAFETENSORS`, `architecture=qwen3_moe`, source metadata
kind, `weight_dtype=BF16`, embedded exact config, full normalized tensor table,
`E/k/layers/kv_shape`, `tensor_bytes`, `eligible_expert_bytes`, resident bytes,
page size, complete sorted expert rows, and conserved page/byte totals.
Each expert references exactly its gate/up/down tensors; `bytes` is their sum
and `packed_logical_pages=ceil(bytes/page_bytes)`. Preserve each tensor's name,
BF16 shape, byte count, source shard, data-relative and file-relative extents,
and historical shard SHA. Validate nonoverlap/bounds per shard, not globally.
Never relabel a shard-local offset as a GGUF offset or a physical MQSim address.

`model_binding` contains format/dtype, exact receipt and COMPLETE-file SHA,
metadata and observation identities, legacy inventory SHA, historical model
fingerprint, and frozen tensor-table/config artifact SHA. Derive all fields from
the accepted snapshot; do not accept caller overrides. Keep historical shard
hash labels historical, `weight_payload_rehashed=false`, payload hash null,
payload identity `HISTORICAL_ONLY_NOT_CURRENTLY_AUTHENTICATED`, origin/hardware
authentication false, `backing_materialized=false`, and science false.
Copy TEST_ONLY/MOCK attribution from any input; it cannot be cleared by flags.

## Budget, route, and projection joins

Add optional `--hf-metadata-refresh` to the three consumer CLIs, required only
for HF. Their pure functions receive the validated snapshot explicitly; a
caller-provided boolean must not waive validation. Preserve GGUF CLI defaults,
return schemas, and numerical results. Freeze exact HF metadata snapshots in
routing/projection output directories and verify them before final publication.

For HF, derive KV layers/heads from `num_hidden_layers/num_key_value_heads` and
both key/value lengths from explicit `head_dim=128`, maximum context 40,960.
Do not infer 64 from `hidden_size/num_attention_heads`. Keep KV element bytes,
active sequences, context, workspace, safety, and fast-tier capacity explicit.
KV accounting remains `sequences * context * layers * kv_heads * (key+value) * kv_element_bytes`;
BF16 KV costs 98,304 bytes per sequence/token here.
Resident deduction uses the table's entire non-offloaded remainder, not file
sizes or padding. This remains logical capacity accounting, not vLLM allocation
or observed residency. Preserve whole-expert packing and legacy-ratio separation.

HF budgets add `model_binding` and `inventory_file_sha256`; retain the existing
canonical meaning of budget `inventory_sha256`. Routing/compute keep their
exact-file inventory hash and also carry `model_binding`. Replay recomputes
the budget with both identities and rejects any cross-format or cross-observation
join, even when dimensions, expert bytes, or canonical JSON values match.

HF member indexes bind the adapted inventory hash and `model_binding`; events
retain `inventory_sha256=legacy_inventory_sha256` and historical
`model_fingerprint`. Compare both on every event. For returned-route events,
also reconcile selected tensor names/shards/extents/dtypes/shapes and bytes
against the bound HF table. Never rewrite old event identities to make a join.
For a runner-produced capture, require the matching immutable capture bundle,
validate it through the public validator specified in
`hf-routing-capture-plan.md`, and bind its manifest/report/marker and member
trace hashes. A label `CAPTURED_ROUTE` alone is not capture authentication.
Explicit synthetic/external inputs remain distinct and cannot inherit a capture
receipt; TEST_ONLY in any event, summary, or manifest forces MOCK.

Projection manifests retain `TRACE_COMPOSED`, CPU_ONLY, PROJECTED (or MOCK),
science false, and `DENSE_SORTED_LAYER_EXPERT_PAGE_PACKING_EXPERIMENT_ONLY`.
Expose source extent references and logical object addresses as separate maps.
Require matching compute-only timing input; collector host materialization
timestamps cannot supply compute intervals, media timings, or deadlines.

## Implementation file map and RED-to-GREEN order

| File | Bounded change |
| --- | --- |
| `scripts/eval/evaluation_inventory.py` (new) | Snapshot, normalized HF adapter, dispatcher, strict derivation and exclusive CLI publication. |
| `scripts/eval/test_evaluation_inventory.py` (new) | Metadata-only fixture and identity/accounting/format/extent/publication regressions. |
| `scripts/eval/budget_fast_tier.py` | Dispatcher, optional HF snapshot, exact HF binding/file hash; existing formula and GGUF output intact. |
| `scripts/eval/routing_metrics.py` | HF branch and donor-to-adapter/capture joins; preserve legacy/GGUF branches and composition algorithms. |
| `scripts/eval/run_prefetch.py` | Validate/freeze HF bundle, compare bindings, retain source references, hash new adapter dependencies. |
| `scripts/eval/test_inventory_checkpoint.py`, `test_routing_metrics.py`, `test_run_prefetch.py` | Preserve old tests; add HF budget, joined routes, and pre-service rejection cases. |

- [ ] First record semantic RED cases: valid HF cannot reach current GGUF
  consumers; then implement the adapter. Test shard offsets overlapping across
  different shards as valid, same-shard overlap/wrong shard or projection as bad.
- [ ] Reject F16 relabeling, missing/duplicate experts, boolean dimensions,
  wrong BF16 shape/bytes, omitted resident tensor, fabricated KV head length,
  unsupported config, altered table/config, absent COMPLETE, and input mutation
  after verification, output collision/escape, and partial publication. Assert
  source/cache/weight opens never occur; preserve a pre-existing output exactly.
- [ ] Budget RED/GREEN: exact HF deductions and KV arithmetic, insufficient
  capacity/context rejection, and equal-expert-total GGUF budget rejection.
  Test file-byte SHA versus canonical JSON SHA without changing legacy meanings.
- [ ] Routing RED/GREEN: donor SHA mismatch despite same fingerprint, different
  metadata observation, changed tensor shard/extent, forged capture label,
  capture trace hash mismatch, and TEST_ONLY relabeling. Accept exact donor joins.
- [ ] Prefetch RED/GREEN: mixed HF/GGUF inventory/budget/routes/compute or missing
  HF metadata rejects before native service startup. Run a tiny MOCK native
  three-policy pilot; verify count/byte conservation, immutable snapshots, and
  unchanged logical-address semantics. Do not synthesize real compute timings.
- [ ] From `scripts/eval`, run focused CPU tests with the existing environment:
  `python -m unittest test_evaluation_inventory test_inventory_checkpoint test_routing_metrics test_run_prefetch`.
  Preserve failing and passing logs under new `results/gold/hf-inventory-adapter/` attempt directories.
  Review frozen-only real adaptation afterward; no GPU or source-weight read.

## Strict unsupported bounds

Initial HF support is the verifier's unquantized BF16 Qwen3-MoE layout with explicit
head dimensions, complete per-expert gate/up/down tensors and full-context KV.
Reject quantized/mixed dtypes, shared/dense-only expert variants, missing KV
fields, sliding-window/rope-scaled accounting, and inferred tensor slicing.
No model loader, legacy donor regeneration, GGUF conversion, capture-schema
rewrite, payload authentication, runtime cache placement, scheduler DONE,
scientific gate, physical storage mapping, or live-serving timing is added.

## Part A implementation checkpoint

Commit `1ee994f` implements the frozen snapshot, HF normalization, exact format
dispatcher, exclusive inventory publication and HF capacity budget. Both snapshot
and disk validation reuse `validate_receipt_contract` and
`validate_frozen_payloads`; consumer buffers cannot omit the full receipt/input
reconciliation. Recorded historical source boundaries are never traversed by
the adapter, and budget input uses the existing bounded no-follow reader.

The 13 adapter controls, 26 metadata controls and 10 GGUF controls pass;
independent spec and quality reviews pass. Existing routing 6/6 and prefetch 4/4
regressions also pass. RED/GREEN evidence is in
`results/gold/hf-inventory-adapter/attempt-001/`.

Real frozen-only adaptation passed in 2.371 seconds. The 9,625,635-byte output is
`results/manifests/hf-qwen3-30b-a3b-evaluation-inventory-20260905.json`, SHA
`c1822bf88f432d3cfd44ef0994730803ab60a963e1d495c8b181054ac75422d6`.
The resulting BF16 KV accounting uses explicit head_dim 128. Three hypothetical
rho controls (1/16, 1/2, 1) cover 384/3072/6144 whole experts with zero padding;
their summary is `results/gold/hf-inventory-adapter/real-frozen-accounting/summary.json`.
These are CPU accounting controls, not GPU allocation/cache measurements.

HF routing/capture joins, projection consumers, snapshot propagation through
those outputs, and the HF-native three-policy control remain the next slice.
Part A does not authorize an HF trace to pass the still-GGUF projection path.
