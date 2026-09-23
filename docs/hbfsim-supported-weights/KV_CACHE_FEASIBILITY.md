# KV-cache binding: source-based feasibility assessment

**KV-cache instrumentation is a possible extension, not a validated capability of this weight experiment.** No KV implementation or GPU experiment was added for this assessment.

The current adapter enumerates `named_parameters`, deduplicates their underlying storage and registers selected weights using `HBFSIM_RANGE_READ`. KV tensors are not model parameters and are mutable. Extending the weight-name selector would neither discover the correct allocations nor establish correct write semantics.

## Relevant vLLM 0.15.1 paths

Paths below refer to the frozen Python source used by the successful run, not an unpinned latest release.

| Operation | Source location | Implication |
|---|---|---|
| Allocate KV raw storage | `vllm/v1/worker/gpu_model_runner.py:5565–5595` | Allocates raw CUDA int8 tensors; multiple layers can share a tensor. Register unique underlying allocations, including their complete byte ranges. |
| Construct typed backend views | Same file, `5679–5720`; `vllm/v1/kv_cache_interface.py:65–87` | Preserve dtype, block/page size, padding, shape, stride and sharing aliases. A logical view need not describe contiguous physical storage. |
| Allocate/reuse/free logical blocks | `vllm/v1/core/kv_cache_manager.py:206–255,378–386`; `block_pool.py:300–340,389–422` | Allocation addresses may remain stable while blocks change request ownership and content. Whole-pool registration and request-level attribution are separate problems. |
| Write keys and values | `vllm/v1/attention/backends/flashinfer.py:1292–1311` | `_C_cache_ops.reshape_and_cache_flash` writes through slot mapping. Its actual executed kernel and usable PTX must be identified. |
| Read KV during attention | Same file, `1339–1392,1463–1506` | Paged prefill/decode wrappers consume KV. Actual selected kernels, PTX availability and supported memory instructions remain unverified here. |
| Copy page indices | Same file, `1712–1733` | `_copy_page_indices_kernel` accesses block-table/page-index metadata. Instrumenting it is not evidence of accessing KV payload. |

The existing storage-deduplication, range checking, exact module binding and accounting mechanisms may be reusable. KV requires a distinct registration/lifetime adapter, writable access semantics and validation of actual write and attention-read paths. CUDA graph capture/replay, allocation-pool reuse, prefix caching and shared layers must not leave stale bindings or misleading request attribution.

## Minimal future proof, if this extension is pursued

First record a native request's allocations, backend/layout and actual write/read kernel identities. Establish PTX availability and compatibility for those kernels individually; a wrapper's open source does not prove that its selected binary contains usable PTX. Then validate one allocation through write, prefill read, decode read and block reuse, comparing native output and read/write service accounting. Only after that should graph replay, shared allocations and broader coverage be considered.

Paths with only unavailable cubin or unsupported instructions remain explicitly native/unmodeled. They cannot be counted through a neighboring metadata kernel. The present result therefore supports further engineering investigation but makes no claim that KV is already bound or correctly delayed.
