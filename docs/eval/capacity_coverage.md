# Coverage and capacity admission

The runtime and profile behavior comes from frozen hybrid b411422. PR4 plus the comment-safe integration from 80fd29c makes global `cp.async`, bulk/tensor copy, global prefetch and `cp.reduce.async` visible as unsupported. Sync-only forms and shared-to-shared copies stay outside this new global-memory count. Regression coverage includes predication, multiline statements/comments, labels and mixed supported/unsupported instructions when a CUDA helper is present.

Capacity-backed or strict-policy affected launches are refused as `unsupported_operation`. Hybrid's existing non-strict timing classification can allow opaque unmodeled timing and records `modeled:false`; this is not new support and cannot count as fully modeled HBF coverage. No permissive fallback or forced-admission option is introduced. CPU-only transformation requiring the production device helper still fails closed.

| Operation | Kernel/workload evidence | Status |
|---|---|---|
| ordinary supported global load/store | existing transform and CUDA-static fixtures | rewritten within existing boundary |
| cp.async global and bulk tensor families | `probe` CPU fixtures; mixed fixture in CUDA build | unsupported; correct strict/capacity refusal |
| cp.reduce.async global | `probe` fixture | unsupported |
| TMA forms in actual Qwen/Triton kernels | no new live capture on this host | NOT VERIFIED; record exact opcode/kernel and refuse affected capacity launch |

The parser is not a complete PTX IR or formal proof over all legal formatting; the tested forms are explicit. Do not claim general TMA/SM120 exact modeling. Historical Qwen launch coverage in old proof files is tied to those hashes and kernels, not transferred to this branch. Byte-level coverage is planned; launch counts do not imply byte coverage. GPU baseline parity: NOT VERIFIED.
