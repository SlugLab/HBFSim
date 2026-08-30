# Real-GPU vLLM timing-adapter proof

Date: 2026-08-11

Branch: `hybrid`

## Result

The timing-only adapter successfully loaded and ran Qwen3-30B-A3B on the real
GPU. It registered every finalized CUDA parameter storage, preserved exact
generated token IDs, and classified all intercepted launches. Automatic PTX
rewriting did not activate for this FlashInfer/Triton-MoE execution, so live
HBF delay injection remains blocked and is not claimed here.

| Gate | Result |
|---|---|
| Real GPU and real Qwen3-30B-A3B workload | Passed |
| Finalized storage registration | Passed: 435/435 unique storages |
| Registered bytes | 61,064,245,248 |
| Baseline versus timing output token IDs | Bit-exact |
| Timing-range launch observation | Passed: 10,584 opaque launches |
| Modeled PTX HBF accesses | 0 |
| Live HBF delay injection | Not proven; fail-closed proof gate exited 70 |

Hardware and software were NVIDIA RTX PRO 6000 Blackwell Server Edition,
driver 595.84, vLLM 0.15.1, PyTorch 2.9.1+cu128, Triton 3.5.1, and FlashInfer
0.6.1. The workload used BF16, eager mode, FlashInfer attention, Triton MoE,
four deterministic 32-token prompts, 32 generated tokens per request, seed
zero, and a 256-token model limit.

## Generation comparison

Both measurements used the same in-process V1 engine configuration. This
corrects the earlier baseline that used vLLM multiprocessing and was not a fair
comparison with bpftime's preloaded CUDA state.

| Metric | Baseline | Timing run 1 | Timing run 2 |
|---|---:|---:|---:|
| Generation time | 0.8898 s | 1.4156 s | 1.8269 s |
| Requests/s | 4.4956 | 2.8256 | 2.1895 |
| Total tokens/s | 287.7156 | 180.8394 | 140.1279 |
| Output tokens/s | 143.8578 | 90.4197 | 70.0640 |
| Throughput change | baseline | -37.15% | -51.30% |
| Generated token IDs | 128 tokens | exact match | exact match |

The two timing repeats show that this small workload's observation overhead is
variable. The 37.15% to 51.30% reduction is an end-to-end measurement of
adapter registration, bpftime CUDA interposition, launch-gate inspection, and
coverage logging. It is not an HBF latency result because no modeled access
executed.

## Coverage boundary

The timing report contains 23,210 decisions:

| Decision | Count |
|---|---:|
| Ordinary launch with no registered HBF range | 12,626 |
| `opaque_unmodeled_timing` | 10,584 |
| `modeled: true` | 0 |

No PTX pass manifest was emitted. The pinned bpftime integration observed the
runtime launches, but the production vLLM stack supplied cubins for the
relevant FlashInfer and Triton-MoE kernels. HBFSim permits these read-only,
physically backed timing ranges to execute while labeling them opaque; strict
capacity ranges still fail closed. The wrapper additionally requires a valid
pass manifest and at least one `modeled: true` decision before it returns
success for an instrumentation proof.

## Relationship to SSD and thermal validation

The earlier [GPU and Dell CD8P checkpoint](2026-08-10-live-gpu-cd8p-thermal.md)
remains the storage/thermal reference: the CD8P is a PCIe NVMe endpoint rather
than a CXL endpoint; read-only sequential throughput was about 7,586-7,587
MB/s, and a warm GPU run showed lower GEMM throughput alongside the software
thermal-slowdown counter. Those measurements validate hardware response, not
the HBF timing model. A calibrated LogP/HBF thermal experiment must wait until
the workload has a modeled instrumentation path.

The machine-readable benchmark summary is stored in
[the companion artifact](artifacts/2026-08-11-vllm-timing-summary.json).
