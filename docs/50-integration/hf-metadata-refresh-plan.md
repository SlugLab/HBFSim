# HF metadata refresh implementation plan

> For the implementation worker: use the executing-plans and TDD workflows,
> one implementation agent at a time. This document authorizes no GPU execution,
> tensor-payload reads, cache writes, or legacy-schema changes. The current task
> writes this plan only; implementation and its review are a separate handoff.

**Goal:** Verify the current local HF checkpoint's complete metadata against the
frozen historical inventory, producing an immutable `METADATA_VERIFIED` receipt
that explicitly does not refresh or authenticate weight payload hashes.

**Architecture:** A small standard-library Python tool freezes the donor JSON,
bounded configuration/index/tokenizer bytes, and the prefix/header of each
safetensors shard. It checks tensor/index/layout identities from those exact
snapshots and publishes a separate receipt. Existing `ModelInventory` consumers
continue receiving the unchanged frozen donor inventory; later run manifests
bind both that donor and the new metadata receipt.

**Stack:** Python 3, `os.open`/`os.pread`/`os.fstat`, strict JSON parsing,
SHA-256, existing durable publication helpers, and CPU-only `unittest` controls.
No torch, transformers, CUDA, safetensors tensor loader, network downloader, or
historical inventory-generator invocation is required.

## Current source-grounded facts

The checkpoint directory is
`/root/hbfsim-exp/phase3/models/Qwen3-30B-A3B`. Its model files link through
snapshot `ad44e777bcd18fa416d9da3bd8f70d33ebb85d39` into the existing read-only
cache under
`/home/victoryang00/.cache/huggingface/hub/models--Qwen--Qwen3-30B-A3B`.
The directory name and cache blob names are identity hints, not authentication.

The frozen donor is
`/root/hbfsim-exp/results/hbm-hbf-capacity-qwen/20260901T023111Z/05-model-manifest.json`.
It has schema version 1 and is 15,545,674 bytes. Its historical
`ModelFingerprint` is
`af52de6efe45aa0e0fe9fe393985a25daa16306c440effe493a12ba10e03dda9`.
The definition includes full shard SHA-256 values. A header-only refresh cannot
reissue that value as a newly verified payload fingerprint.

The historical generator was located read-only at
`/root/hbfsim-exp/results/hbm-hbf-capacity-qwen/20260901T023111Z/scripts/model_inventory.py`.
Do not invoke it: its `main` hashes complete shard payloads. Lines 31–32 and
164–165 establish this exact metadata-only donor consistency calculation:

```python
rows = [{"path": f["path"], "size_bytes": f["size_bytes"], "sha256": f["sha256"]}
        for f in donor["files"]]
historical = hashlib.sha256(json.dumps(
    rows, sort_keys=True, separators=(",", ":"), ensure_ascii=False
).encode()).hexdigest()
assert historical == donor["ModelFingerprint"]
```

Preserve the stored list order: seven identity files in generator order, then
sorted shard names. Do not sort the entire list despite the historical prose
definition's word "sorted". This calculation was reproduced exactly from donor
metadata; it reads no current payload. The donor JSON SHA-256 is
`ed64d4c0bd11ee75cb4cd1de4e82ed1b0071fbc4389b8b974e9275382a1e912a`.

The 2026-09-05 grounding probe read exactly 19,912,432 current metadata bytes:
seven small files and 2,330,400 bytes covering all 16 shard prefixes/headers.
It read no tensor payload and wrote nothing to the cache. It observed:

- `Qwen3MoeForCausalLM`, `qwen3_moe`, `torch_dtype=bfloat16`;
- 48 layers, 128 experts/layer, top-k 8, 6,144 experts;
- 18,867 unique index/header tensor names, all header dtype `BF16`;
- 9 MiB per expert: gate/up each `[768,2048]`, down `[2048,768]`;
- hidden size 2,048, 32 attention heads, 4 KV heads, head dimension 128,
  vocabulary size 151,936;
- largest observed header plus prefix: 156,208 bytes.

This was a bounded grounding probe, not execution of the proposed verifier or a
new published verification receipt. The sandbox denied opening `config.json`;
the explicitly authorized host-side read succeeded. An implementation must
report an access failure as such. It must not download, copy the checkpoint,
change cache permissions, or infer a missing checkpoint from sandbox denial.

Relevant current interfaces:

- `adapters/vllm_capacity/model_inventory.py:ModelInventory` reads the legacy
  `ModelFingerprint`, configuration and expert tensor locations. Its basic byte
  accounting is useful compatibility coverage, but it is not a complete current
  safetensors verifier and must not be treated as one.
- `trace_collector.py:JsonlTraceCollector` binds the inventory JSON hash and
  logical inventory identity. It does not bind the current HF checkpoint.
- `trace_validation.py:main` reports route consistency, requires native/repeat
  controls for complete consistency checks, and always leaves scientific
  validation false. A metadata receipt must not upgrade those semantics.

## Files and public interfaces

Create only these implementation files, then update this document with evidence:

| File | Responsibility |
| --- | --- |
| `scripts/eval/verify_hf_metadata.py` | Bounded reads, identity checks, tensor validation, immutable receipt and CLI. |
| `scripts/eval/test_verify_hf_metadata.py` | Deterministic CPU metadata fixtures and adversarial read/mutation controls. |

Keep `ModelInventory`, trace schemas, capture compatibility, generators,
registries, renderers and result-state enums unchanged in this unit.

Use these concrete interfaces; snapshots are byte strings plus plain dictionaries:

```python
def strict_object(raw: bytes) -> dict: ...
def read_metadata(path, *, limit: int, budget: dict, allowed_root) -> dict: ...
def read_safetensors_header(path, *, limit: int, budget: dict, allowed_root) -> dict: ...
def validate_tensor_inventory(config: dict, index: dict, shards: dict,
                              donor: dict) -> dict: ...
def verify(checkpoint, donor, out, test_only=False) -> dict: ...
def validate_refresh(out) -> dict: ...
def check_current_inputs(out) -> dict: ...
```

These are interface declarations, not implementation stubs to commit. The CLI
has `verify`, `validate`, and `check-current` subcommands. `verify` requires
`--checkpoint`, `--donor`, and a new `--out`; `validate` checks only a frozen
bundle; `check-current` repeats bounded current reads and compares them to the
receipt without changing that receipt. A test reader or synthetic control
automatically forces `TEST_ONLY`; it cannot produce a current-checkpoint receipt.

## Read and path contract

Every read spends its allowance **before** allocation/I/O. Fixed limits are part
of the receipt; this unit provides no unlimited-read option.

| Input | Maximum bytes |
| --- | ---: |
| Historical inventory JSON | 32 MiB |
| Each of the seven current configuration/index/tokenizer files | 16 MiB |
| Safetensors JSON header | 16 MiB per shard, plus its 8-byte prefix |
| Current checkpoint metadata read budget | 64 MiB total, including prefixes |
| Donor plus current metadata maximum | 96 MiB total |

The expected file list comes from the frozen donor/index, with exactly the
donor's 16 shards for this checkpoint. Reject unknown shard names, duplicate
entries, absolute names, `..`, nested paths, missing files and extra safetensors
files. Non-model README/git administrative files are outside the read allowlist;
record that scope without opening them. Cap tensors at 25,000 and dimensions at
rank two for this Qwen family. Validate the declared count before building large
cross-product structures; reject boolean, noninteger or nonpositive dimensions.

Small-file reads hash the exact bytes later parsed/frozen. Safetensors reads use
unbuffered positioned I/O exclusively: read exactly bytes `[0,8)`, decode the
little-endian unsigned header length, check the cap and `8+length <= st_size`,
then read exactly `[8,8+length)`. No read, mmap, hash, checksum or loader may touch
`[8+length,st_size)`. Do not use buffered read-ahead on a shard handle.

Capture the checkpoint directory and every symlink hop with `lstat` and
`readlink`, with at most 32 hops. Resolve an explicit path inside the authorized
cache root, then open the terminal file `O_RDONLY|O_NOFOLLOW|O_NONBLOCK` and
reject nonregular files by `fstat` before reading. For this exact donor refresh,
require the terminal realpath and file size to match the donor file record;
do not silently accept a new cache target. Record historical mtime agreement as
a consistency check; a mismatch is metadata drift requiring a separate reviewed
baseline, not implicit permission to update the historical identity.

Check terminal `(device,inode,size,mtime_ns,ctime_ns)` and the complete symlink
chain before/after each read and once more across the whole acquisition. Reject
replacement, truncation, growth, retargeting or in-place mutation. These checks
detect ordinary changes; they do not cryptographically prove payload contents.
Freeze each exact verified snapshot, not a later reread. No output may be created
in the checkpoint/cache tree or outside the experiment checkout. Reject existing
output paths and canonical `results/runs` paths for TEST_ONLY input before mkdir.

## Complete tensor validation

Parse JSON with duplicate-key rejection and rejection of nonfinite constants;
bound input size first and turn decode/depth/encoding failures into diagnostics.
Require object roots. Safetensors `__metadata__`, when present, is a string-to-
string metadata object, not a tensor. Reject unknown tensor descriptor fields.

For every tensor, require a known `BF16` dtype, integer positive shape entries,
two nonnegative integer data offsets, and
`end-start == product(shape)*2`. Offsets are relative to the payload base
`8+header_length`; derive absolute file offsets using that base. Require all
intervals within the stat-declared file and, for this bounded checkpoint
contract, sorted intervals to cover the payload exactly without holes or overlap.
Check each shard separately, not just the global byte sum.

Require global uniqueness, exact equality of index names and header names, and
the exact shard assignment for every index entry. Compare the complete index to
the donor's `weight_map`. Check index `total_size`, total declared payload bytes,
all donor file sizes and donor aggregate tensor/expert/nonexpert byte counts.
Each small metadata file must match its donor SHA-256; tokenizer hashes must also
match the donor's tokenizer records. Shard payload hashes are only cross-checked
for consistency **inside the historical donor**, never claimed freshly computed.

Generate the expected tensor set from the Qwen config, with no optional unparsed
extras. Define `L=num_hidden_layers`, `E=num_experts`, `H=hidden_size`,
`M=moe_intermediate_size`, `A=num_attention_heads`, `K=num_key_value_heads`,
`D=head_dim`, `V=vocab_size`:

| Names for each layer `i` and, where applicable, expert `j` | Expected shape |
| --- | --- |
| `model.layers.i.mlp.experts.j.gate_proj.weight`, `.up_proj.weight` | `[M,H]` |
| `model.layers.i.mlp.experts.j.down_proj.weight` | `[H,M]` |
| `model.layers.i.mlp.gate.weight` | `[E,H]` |
| `model.layers.i.input_layernorm.weight`, `.post_attention_layernorm.weight` | `[H]` |
| `model.layers.i.self_attn.q_proj.weight` | `[A*D,H]` |
| `model.layers.i.self_attn.k_proj.weight`, `.v_proj.weight` | `[K*D,H]` |
| `model.layers.i.self_attn.o_proj.weight` | `[H,A*D]` |
| `model.layers.i.self_attn.q_norm.weight`, `.k_norm.weight` | `[D]` |
| `model.embed_tokens.weight`, `lm_head.weight` | `[V,H]` |
| `model.norm.weight` | `[H]` |

Enforce the current family's `attention_bias=false`, empty `mlp_only_layers`,
`decoder_sparse_step=1`, no shared expert, untied embeddings, and BF16 config
dtype. Reject unsupported variants explicitly rather than guessing a layout.
This yields `3*L*E + 9*L + 3 = 18,867` tensors for the current checkpoint.

Match every donor expert's layer/expert ID, gate/up/down tensor name, dtype,
shape, shard, relative and absolute offsets, and bytes to the current header.
Check all `L*E` expert groups exactly once, gate/up aggregation into legacy `w13`,
the legacy `w2` mapping, top-k bounds, 16-KiB expert-byte alignment used by
`ModelInventory`, and 9-MiB experts for the supplied donor. Then instantiate
`ModelInventory` from the bounded, frozen donor copy as a compatibility check.

## Receipt and publication

Keep the donor byte-for-byte as `legacy-inventory.json`; do not rewrite
`ModelFingerprint` or `source_shard_sha256`. Freeze the seven small files,
individual shard prefixes/headers, and a normalized complete tensor table. No
artifact contains a copied shard payload. Save exact read ranges and byte counts.

Use a new receipt schema, with these distinct fields:

```json
{
  "schema_version": 1,
  "validation": "METADATA_VERIFIED",
  "evidence_class": "CURRENT_LOCAL_HF_METADATA",
  "checkpoint_format": "HF_SAFETENSORS",
  "config_dtype": "bfloat16",
  "tensor_dtype": "BF16",
  "resource_class": "CPU_ONLY",
  "weight_payload_rehashed": false,
  "weight_payload_matches_historical": "NOT_CHECKED",
  "checkpoint_origin_authenticated": false,
  "scientific_validation_passed": false,
  "hardware_validated": false
}
```

Additional required fields are UTC start/end, original checkpoint/cache paths,
every link/terminal identity, limits/read accounting, verifier/interpreter/source
hashes, exact argv/git/environment, artifact hashes and:

- `legacy_inventory_sha256`: hash of the frozen donor bytes;
- `historical_model_fingerprint`: the donor value, explicitly historical;
- `metadata_fingerprint`: canonical hash of config/index/tokenizer content
  hashes, each shard's name/size/header hash, normalized tensor layout, and the
  verification-contract version; this is not a weight-payload fingerprint;
- `observation_fingerprint`: canonical hash of the metadata fingerprint, donor
  hash, current file/link identities, UTC and verifier identity. It distinguishes
  separate observations with identical metadata.

TEST_ONLY fixtures use `evidence_class=TEST_ONLY`; they remain metadata controls.
Do not put metadata validation classes into the performance provenance enum or
the scheduler state machine. No result CSV, scientific PASS, `REAL_QWEN_TRACE`,
`VALIDATED_MODEL` or scheduler DONE is produced.

Reserve a new output directory exclusively. Preserve partial snapshots and a
durable FAILED/INTERRUPTED validation receipt when anything fails. Successful
publication follows independent validation of the exact final artifact byte
snapshot used for hashing, and an exclusive atomic publication marker. Existing
bundles are validated or rejected, never overwritten or silently reused.
`validate_refresh` checks exact artifact inventory, hashes and recomputed tensor
semantics from frozen bytes; updating a claimed summary/hash alone cannot pass.

## Downstream native/capture/repeat binding

The later run wrapper, not this unit or the legacy trace schema, owns a separate
`hf-metadata-binding.json` for each native, capture and repeat arm. It binds:

1. The complete metadata receipt SHA-256, metadata fingerprint and observation
   fingerprint; all three arms must refer to the same accepted observation.
2. The frozen legacy inventory SHA-256 and historical model fingerprint. Pass
   that exact donor copy to `ModelInventory` and `JsonlTraceCollector`.
3. The actual runtime checkpoint root/cache resolution, tokenizer hashes and
   explicit `bfloat16` runtime dtype; plus existing prompt/token/seed/engine,
   version, implementation and environment identities.
4. Bounded `check-current` reports before and after each arm. Any identity drift
   rejects the trial; do not silently refresh between arms or relabel an old arm.

Current route events may continue carrying the historical `model_fingerprint` as
the inventory join key. The wrapper must label that meaning and must not imply
that it is a newly verified current payload hash. A receipt lacking the new
binding cannot silently satisfy the upcoming HF native/capture/repeat workflow.
Existing replay of historical traces remains a separate supported operation.

HF BF16 and GGUF F16 are distinct formats and numerical payloads even when shapes
or byte counts match. A GGUF run cannot use this receipt as its checkpoint proof.
Missing native output agreement, repeat agreement, real capture provenance or
runtime validation remains missing; successful metadata checks cannot fill it.

## Bounded implementation sequence and semantic gold cases

- [ ] **Read boundary RED:** Add tests for short prefix/header, oversized declared
  header, exhausted aggregate budget, FIFO/device rejection, path traversal and
  symlink escape. Use a read spy that fails immediately for any request extending
  beyond `8+declared_header_length`; require zero payload bytes read on success
  and failure. Implement only the bounded readers, then run those tests green.
- [ ] **Identity RED:** Swap a file or retarget a symlink between resolution and
  open, mutate/truncate/grow it during a read, and change an earlier shard after
  later shards are read. Each must reject and retain diagnostics. Implement
  before/after fd, path, chain and whole-acquisition checks; run green.
- [ ] **Tensor RED:** Add duplicate JSON names, missing/extra tensor, duplicate
  across shards, wrong index assignment, missing shard, overlapping intervals,
  holes, out-of-bounds offsets, boolean/negative dimensions, wrong BF16/F16 dtype,
  transposed gate/down shapes, wrong expert IDs and byte totals. Implement the
  full expected-name/shape/offset reconciliation; run green.
- [ ] **Donor RED:** Mutate donor config, fingerprint-associated file records,
  historical shard-hash linkage, one expert's offsets, or tokenizer identity.
  Require rejection; verify the bounded frozen donor still loads through the
  existing `ModelInventory` without changing its bytes or schema.
- [ ] **Publication RED:** Test existing output, TEST_ONLY formal-path aliases,
  failed final validation, extra/missing frozen artifacts, and a resealed false
  tensor summary. Implement independent frozen-byte validation and exclusive
  successful publication; ensure failures never leave `METADATA_VERIFIED`.
- [ ] **Proof-boundary controls:** A synthetic file with the same valid header
  and declared size but different unread payload is not cryptographically
  distinguishable by this tool. The result must still say payload NOT_CHECKED,
  historical fingerprint only, and TEST_ONLY for the fixture. A payload-reader
  hook must receive no calls. Reject a GGUF/F16 receipt substitution and a missing
  or mismatched downstream metadata binding without invoking any model loader.
- [ ] **Focused green:** Run
  `python3 -m unittest discover -s scripts/eval -p test_verify_hf_metadata.py -v`
  and the existing inventory-consuming adapter CPU tests. Save red/green logs
  under `results/gold/hf-metadata/`; no GPU calls or checkpoint generators.
- [ ] **One metadata-only actual invocation:** After targeted tests and review,
  use the explicitly authorized host-side read context for this exact checkpoint
  and donor. Require 16 shards, 18,867 tensors, BF16, 6,144 complete experts, no
  payload reads, and a fresh immutable receipt. Validate that frozen bundle again
  without reopening the live checkpoint. Do not start native/capture/repeat as
  part of this unit and do not commit until the parent task requests integration.

Synthetic full-workflow fixtures must be explicitly TEST_ONLY. Reader-level
fixtures can use small metadata files and fake stat sizes/read spies; avoid
allocating or filling model-sized tensors, creating hardware payload files, or
using real cache mutations as adversarial tests. The later implementation worker
has no need to modify the historical donor or another user's cache.


## Implementation checkpoint

The parent implementation now uses `verify(checkpoint, donor, out, test_only=False)`,
`validate_refresh(out)` and `check_current_inputs(out)`. The immutable donor copy
is named `donor.json`; `tensors.json` contains the normalized complete tensor
table. The receipt uses `status=METADATA_VERIFIED`, separate `evidence` and
`provenance` fields, `metadata_identity_sha256` for content/layout identity, and
`observation_identity_sha256` for the source/file/execution observation. These
names replace the illustrative receipt names above; the claim limits are the same.

Public validation requires `COMPLETE.json`, bound to the exact receipt bytes and
observation identity. The producer first validates the full frozen bundle,
checks current inputs again, and then publishes this marker exclusively. A
receipt alone is provisional. Checkpoint metadata read limits are unchanged;
executable provenance uses a separate 512-MiB bound and 1-MiB streaming reads.

The initial missing-module RED and subsequent adversarial RED/GREEN records are
under `results/gold/routing-capture/hf-*`. The latest parent CPU run passes
23 tests in 1.700 seconds. Independent specification review passes, including
six targeted regressions. Code quality review and real metadata acquisition
remain pending at this checkpoint. This is no GPU/capture/performance proof.
