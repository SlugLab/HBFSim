# Real vLLM exact-PTX live-delay proof

Date: 2026-08-11

Branch: `hybrid`

## Result

A real Qwen3-30B-A3B inference completed on an NVIDIA RTX PRO 6000 Blackwell
Server Edition through vLLM 0.15.1, Triton 3.5.1, the patched bpftime CUDA
attach path, HBFSim's automatic PTX pass, the fail-closed launch gate, and the
online HBF timing service. This is the first repository proof in which a
production vLLM kernel both completed and produced nonzero `modeled: true`
coverage.

The experiment registered an explicit 16 KiB prefix of
`model.layers.0.mlp.experts.w13_weight`. Restricting the experiment to one
named range is intentional: registering all 61 GB of model weights made vLLM's
profiling pass generate an impractically large number of synchronous reference
requests. The narrow range preserves a real application path while keeping the
reference-model experiment bounded.

| Gate | Result |
|---|---:|
| Real vLLM/Qwen workload completed | Yes |
| Exact Triton variants bound | 4 |
| Coverage decisions | 10,339 |
| `fused_moe_kernel` launches | 2,304 |
| Modeled fused-MoE launches | 24 |
| Registered HBF bytes | 16,384 |
| Output token IDs equal to baseline | Yes |
| Baseline generation | 0.269996 s |
| Nominal HBF timing generation | 44.469084 s |
| Observed slowdown | 164.70x |

The slowdown is an end-to-end emulator measurement, not a claim that a future
HBF device would be 164.70x slower. It contains modeled media delay plus the
current per-warp GPU/host request-path overhead. HBFSim reports these together
for this proof; separating them with the calibrated LogP fast path remains
future work.

## Exact path that was proven

Triton caches several kernels named `fused_moe_kernel`. HBFSim hashes each
original PTX variant, rewrites and assembles it separately, then binds the
original `CUfunction` to the patched function with the same PTX digest and
kernel name. Triton 3.5 obtains `cuLaunchKernelEx` through a private
`libcuda.so.1` handle, so the launch gate also interposes that narrow
handle-specific lookup. CUDA's reported parameter layout must match the PTX
manifest exactly before the gate allows a registered pointer to execute.

The final ABI fix treats `.ptr ... .align N` as pointee alignment rather than
kernel-parameter slot alignment. CUDA reported the naturally aligned pointer
offsets, and the corrected manifest matched them.

## Reproduction

First run the baseline to compile the Triton cache, then run:

```bash
export HBFSIM_BUILD_DIR=/dev/shm/hbfsim-vllm-gpu13
export HBFSIM_BPFTIME_BUILD_DIR=/dev/shm/hbfsim-bpftime-variant-gcc14
export HBFSIM_VLLM_CACHE=/dev/shm/hbfsim-vllm-live-cache

adapters/vllm/run_timing.sh \
  --model /home/victoryang00/Qwen3-30B-A3B \
  --profile configs/profiles/nominal.json \
  --report-dir /dev/shm/hbfsim-vllm-exact \
  --num-prompts 1 --input-len 32 --output-len 8 \
  --max-model-len 64 --max-num-batched-tokens 64 \
  --hbf-parameter-regex \
    '^model\.layers\.0\.mlp\.experts\.w13_weight$' \
  --hbf-range-bytes 16384 --seed 0
```

The wrapper exits 70 if no `modeled: true` decision is present. This run exited
zero.

## Dell CD8P read-only comparison

The storage endpoint was positively identified live as `/dev/nvme1n2`, Dell DC
NVMe CD8P E3.S 1.92TB, serial `7EU0A01P0XK1`. It had no filesystem, mount, or
holder. The CD8P is a PCIe 5.0 NVMe device; it is not itself a CXL endpoint, so
this result is a storage/thermal validation input rather than evidence of a CXL
protocol path.

A 35-second read-only fio load ran concurrently with the matched vLLM baseline:

| Metric | Result |
|---|---:|
| Read bandwidth | 7.515 GB/s |
| Read IOPS | 57.34k |
| Mean completion latency | 1.111 ms |
| p99 completion latency | 1.270 ms |
| Bytes read | 263.05 GB |
| Bytes written | 0 |
| CD8P temperature | 36 to 40 degrees C |
| Critical warning / media errors | 0 / 0 |
| Concurrent vLLM generation | 0.239631 s |

The isolated baseline was 0.269996 s. The -11.25% single-run delta is noise in
the faster direction, not evidence of a storage benefit; no degradation from
CD8P contention was observed at this resolution. The earlier hardware proof
records the GPU warm-state thermal result: 85 degrees C, software thermal
slowdown asserted, and BF16 GEMM throughput 8.10% below the cold run.

## Durable artifacts

Raw result, registration, pass-manifest, Triton-binding, baseline, and fio JSON
files are stored in
`docs/proofs/artifacts/2026-08-11-vllm-exact/`. The 3.8 MB coverage stream is
represented by `coverage-summary.json`, which records its exact SHA-256 digest,
counts, reasons, and modeled module identity.

The raw vLLM result files record `097b69a` because the live proof ran from the
working tree based on that commit. The automatic Triton and gate changes were
committed only after the proof and full regression suite completed; the raw
hashes above prevent the post-run documentation step from silently changing
the measurements.
