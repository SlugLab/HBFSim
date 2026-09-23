# Supported-weight PTX instrumentation: validated scope

The OLMoE-1B-7B-0924 run registered the complete storage ranges of **98 supported weight storages (13,091,213,312 bytes)** and completed inference with instrumented kernels. This extends the preceding eight-storage representative run to all 16 layers of the supported weight categories.

| Selected category | Storages | Registered bytes |
|---|---:|---:|
| MoE expert projections, w13 and w2 | 32 | 12,884,901,888 |
| Input, post-attention, Q/K, and final normalization | 65 | 266,240 |
| Token embedding | 1 | 206,045,184 |

The remaining 49 model-weight storages—attention projections, routers, and the output head—retain native execution and are not claimed as HBF-covered. KV cache is outside this experiment. MoE execution uses Triton's `fused_moe_kernel`; the preserved native `_moe_C` library supplies auxiliary functionality and is not being presented as a replacement expert implementation.

## Correctness and service accounting

The native and instrumented cases used one request, two input tokens, two output tokens, seed 0, and the same checkpoint. Both returned token IDs `[[70, 13]]`, SHA256 `06df4d1df2181aa0d792324ce4dd007c7f3f6cb63015eb345ea26a3b9e0460a7`.

For accounting epoch 5702, **267,614,976 in-range accesses / 4,279,001,088 intersection bytes** were admitted and completed, with exact equality between all three counters. Overflow, pre-issue and post-issue failures, translation failures, unsupported accesses, and unclassified accesses were zero. Accounting was synchronized and disabled completely before the final snapshot.

All 98 registered storages have address-matched modeled launch records linked to COMPLETE modules with positive completed-service counts. There are 777 such records. The worker exited 0, left no child processes, and the final validator reported `PASS_98_OF_98_ADDRESSED_ACTIVE`.

| Active module | In-range accesses | Intersection bytes |
|---|---:|---:|
| Normalization | 221,952 | 798,720 |
| Embedding | 6,144 | 12,288 |
| MoE variant 5c5463… | 178,257,920 | 2,852,126,720 |
| MoE variant bd090f… | 89,128,960 | 1,426,063,360 |

The other two staged MoE variants and the auxiliary Fill module contributed zero in-range accesses. Service request count is 266,666,807; requests and instrumented accesses are different accounting quantities.

## Interpretation

Complete storage registration does not mean that this short request traversed every byte or every expert. Intersection bytes are accumulated accesses, not unique-byte coverage. Addressed launch evidence plus module-level completion does not provide an independent latency measurement for each storage. Native fallback is excluded from modeled coverage.

Measured load time was 762.933 s and generation time was 1424.058 s. Worker wall time was 2235.056 s (2026-09-23 15:44:19.742–16:21:34.798 UTC). These include framework, host authentication, and simulator overhead and are not a hardware latency-accuracy or speedup result. The outer allowance was 10,800 s; it did not cause termination.

The approximately 5% resource-margin target was briefly undershot: the 30-minute observation showed 3.83% free VRAM. The fixed 4 GiB reservation process had no hot-resize interface. The original run completed without OOM; this deviation is retained rather than claimed as full resource-policy compliance.

## Evidence identities

The original run directory is `results/native-supported-all98-staged-v1` under the giga experiment archive. It contains 76 preserved raw files, indexed by SHA256; the byte inventory totals 20,051,095 bytes.

| Artifact | SHA256 |
|---|---|
| Execution receipt | `092f4729f90418361939786e18fa6a4d4811671f66824eb3c5a5b4b0c22d78b8` |
| Raw inventory | `e6ab3477afe7feb9bdd68c093999a23f3173742ef66a68eb0264b0e47aeb31b9` |
| Result | `ed051170d9748b3e06d08addf056af3b4c3267777a6c0d3a5a68e1879028f479` |
| Registration | `3913704263061e246c411d27ce22e6fd9f7d20c8ed2f3f82695ebb039565a05c` |
| Coverage records | `6b1ac64f8459ee9526054b123d0c7e52cd44bfb38b52d1756dec02d34d411cef` |
| Dynamic coverage validation | `b8fe03ef82b3725087953d1590622f4aeac65b70a06c6a7882457d494d5be156` |

The earlier eight-storage v2 run returned correct output but covered only five storages dynamically. It remains a partial result. The subsequent eight-storage v3 run and this 98-storage run passed after repairing exact native-entry binding. Source reconstruction, clean builds, and publication are separate validation steps; this inference result does not itself certify a newly assembled build.
