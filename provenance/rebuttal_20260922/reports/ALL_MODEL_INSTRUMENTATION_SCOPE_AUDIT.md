# All-model registration versus instrumentation scope

Status: **READ-ONLY AUDIT OF FROZEN EPOCH 3320**.

This audit distinguishes three different facts:

1. all 147 unique learned-parameter CUDA storages were registered as timing-backed address ranges;
2. four unique transformed PTX module identities were active;
3. the whole-process launch coverage stream observed a modeled address for only 32 storages.

The third fact is a launch-address lower bound. It is not proof that the other 115 storages were never accessed.

## Architecture: general mechanism versus this experiment

HBFSim's framework is not implemented as one hand-written patch per operator:

- Storage registration records CUDA address intervals. The all-model adapter discovers finalized unique parameter storages and calls `register_storage(address, bytes)` for each selected interval; see `all-model-binding-preflight-v1/adapter-overlay/hbfsim_loader.py:350-374`.
- The PTX transformer is a general synchronous PTX memory-access rewriter with unsupported-opcode accounting, rather than an OLMoE-specific source rewrite; see `first-fault-compact-source-v2/src/ptxpass_hbf/transform.cpp:207-245,358-380` and manifest construction in `plugin.cpp:397-454`.
- A transformed module is trusted only when its load transaction and embedded identity agree in the live CUDA domain. The module hook then initializes control state, associates the identity, and registers it for accounting; see `launch_gate.cpp:3188-3239`.
- Triton exact binding hashes the original PTX payload and asks the native binder for the matching variant; see `runtime-atomic-sidecar-v1/adapters/vllm/triton_binding.py:98-150`. The gate accepts an original-to-patched `CUfunction` alias only when the patched function has a live identity and timing binding; see `launch_gate.cpp:3159-3168`.

This specific experiment had a narrower staged set. The frozen pass manifest contains four entries, all named `fused_moe_kernel`, all `instrumented=true`, with zero unsupported instructions. The Triton receipt binds fused-MoE functions to those four identities; its only non-fused entry, `_copy_page_indices_kernel`, reports `variant_not_found`. Therefore the framework is generic, while epoch 3320's proven transformed kernel set is fused-MoE only. Registering all weights did not automatically create transformed variants for every operator used by the model.

## Frozen observed partition

The streaming classifier report has SHA-256
`db353fb461085d62df7e1179cee4c618e6a61f6b518c6e459b95eef5903b6f69`.
It classifies 147 storages as follows:

| Storage family | Classification | Storages | Registered bytes |
|---|---|---:|---:|
| expert `w13_weight` | OBSERVED_MODELED | 16 | 8,589,934,592 |
| expert `w2_weight` | OBSERVED_MODELED | 16 | 4,294,967,296 |
| packed attention `qkv_proj.weight` | NO_RECORDED_MATCH | 16 | 402,653,184 |
| attention `o_proj.weight` | NO_RECORDED_MATCH | 16 | 134,217,728 |
| MoE router `mlp.gate.weight` | NO_RECORDED_MATCH | 16 | 4,194,304 |
| RMSNorm parameters | NO_RECORDED_MATCH | 65 | 266,240 |
| token embedding | NO_RECORDED_MATCH | 1 | 206,045,184 |
| LM head | NO_RECORDED_MATCH | 1 | 206,045,184 |

Thus 32 expert storages (12 GiB) have an observed modeled launch address. The remaining 115 storages total 953,421,824 bytes, but their classification remains a dynamic coverage gap, not a registration gap.

The frozen `coverage.jsonl` has 10,331 launch decisions. Exactly 2,304 records have a nonzero address and `modeled=true`; every one is `fused_moe_kernel`. There are no recorded kernel names containing GEMM or GEMV. This does not prove that GEMM/GEMV did not execute: it proves only that this coverage artifact did not identify such a kernel by name or attribute one of its launch parameters to a registered storage.

Examples of recorded opaque paths include 2,340 RMSNorm-family launches and 31 `indexSelectSmallIndex` launches with `modeled=false`, `opaque_unmodeled_timing=true`, and `address=0`. Here zero is the default “no attributable inspected address” field in the opaque decision. It is not a real weight address of zero.

## Why NO_RECORDED_MATCH cannot answer “was it accessed?”

The gate enumerates launch parameters through CUDA parameter metadata. It reads a value only when the parameter width equals one pointer and otherwise treats wider aggregates as opaque; see `launch_gate.cpp:1981-2031`. The coverage gate checks the directly visible pointer-width argument values against registered ranges; see `coverage.cpp:459-477,533-638`.

All direct parameter slots are inspected for authorization, but the durable decision schema contains only one scalar `address` field. When multiple registered pointers occur, later matching slots overwrite that field before the single launch record is written (`coverage.cpp:550-557`; writer schema `coverage_writer.cpp:9-26`). Consequently one launch can be attributed to at most one storage by the offline classifier. Pointers hidden inside descriptors, wider aggregates, or a library's internal launch are not a complete per-storage access trace.

The restored OLMoE source confirms these weights have model execution paths:

- packed QKV and output projection are constructed at `olmoe.py:155-173` and called at `olmoe.py:211-216`;
- router gating is called before fused experts at `olmoe.py:114`;
- token embedding is called at `olmoe.py:283-299`;
- the LM head is passed to the logits processor at `olmoe.py:462-490`;
- unquantized linear layers dispatch through `dispatch_unquantized_gemm` at `linear.py:243-249`; embedding uses `F.embedding` at `vocab_parallel_embedding.py:63-72`.

These source paths establish that the model contains and invokes those operator abstractions. The frozen coverage artifact does not identify the concrete library kernel used for QKV, output projection, router, or LM head, so this audit does not label any particular cuBLAS kernel as observed, opaque, or absent.

## Permitted conclusion

Epoch 3320 registered the complete declared set of 147 learned BF16 parameter storages and closed aggregate in-range accounting for the request. It directly demonstrates modeled launch-address observations for the 32 expert storages used by the four fused-MoE PTX identities. For the other 115 registered storages, current evidence says **NO_RECORDED_MATCH / UNKNOWN dynamic per-storage coverage**. It is incorrect to restate the run as “all registered weights were dynamically timing-modeled,” and it is also incorrect to restate NO_RECORDED_MATCH as “the weights were not accessed.”

Frozen evidence:

- `result.json` SHA-256: `0c61252a530f3db0b6433ae5c7e7a727a0a6807cd6c0e058d2725ef6ad4f13f9`
- `coverage.jsonl` SHA-256: `3e8f66b2276bb35c2761426cbca1f994e168162924d4036b5c2213a206e5f87f`
- `pass-manifests.jsonl` SHA-256: `087536fe262b3438b127b8452bf9784ec71dffa30c4af239cd1c6cc6bd6cfb0b`
- `triton-bindings.jsonl` SHA-256: `56e52bcd62771f8156305b4365c2f4a5a2959cbd4cf8760d9652595a20cc01de`
- derived validation SHA-256: `c7b7a50b971b36c2b9c104a5831d027dc419b1cf48e7570aee2948301583f713`
