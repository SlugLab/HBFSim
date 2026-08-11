# CD8P vmem tuning and real-GPU proof

Date: 2026-08-11 UTC

## Outcome

HBFSim now has a named timing profile for the complete CD8P-backed vmem path
measured by `nvme-mem2nvm`. The runtime replays its six cumulative P50 read
breakpoints on the GPU through explicit timing ranges and automatic bpftime PTX
rewriting. Every real-GPU case matched its baseline checksum and source modeled
total exactly, with one modeled launch and zero unsafe launches.

This closes an end-to-end calibration and replay gate. It does not claim an
independent prediction on a different workload or that the Dell CD8P is a CXL
device.

## Source provenance and safety boundary

- Source repository checkout: `/home/victoryang00/nvme-mem2nvm`, current HEAD
  `d79c1481697cb4cc188b9c8a3985d6274512bdc4`.
- Source data commit: `bf6ab23c2ef970f3b1410ea88c24621a6f5a86ff`.
- Source CSV:
  `docs/superpowers/results/2026-07-30-vmem-sw-performance.csv`.
- Source SHA256:
  `4fb6d2847c3ce4a09b7f2ce07dcb4cf8254145243c1985bce2848261b8d0724f`.
- Source device capacity: 1,920,383,410,176 bytes.
- Geometry-aligned HBFSim capacity: 1,919,850,381,312 bytes; the
  533,028,864-byte delta is 0.0278%.

The proof did not open `/dev/vmem0` or any raw NVMe namespace. At the proof
checkpoint, `/sys/class/vmem/vmem0/cache_used` and `dirty_bytes` both reported
4,294,967,296 bytes. The GPU benchmark used only a fresh `cudaMalloc` range of
at most 2 MiB and consumed the committed CSV as read-only calibration input.

## Model

The tuner verifies the exact source digest, requires 11 samples for each
target, computes nearest-rank P50/P95, aligns capacity to the MQSim geometry,
and atomically emits:

- `configs/profiles/cd8p-vmem-p50.json`;
- `configs/tuning/cd8p-vmem-comparison.json`.

The runtime control ABI carries six page breakpoints and six cumulative P50
values. A packed atomic state tracks the previous global media page,
read/write operation, and sequential run length. Reads inject
`cumulative(n) - cumulative(n - 1)`; 4 KiB writes use the measured fsync P50 of
408,305 ns. Interpolation uses checked ceiling arithmetic and the last segment
is extrapolated beyond 512 pages. Invalid empirical state returns
`Unsupported` and cannot enter the legacy scalar path.

## Why the empirical curve is needed

The old nominal scalar equation increasingly underpredicts the measured
end-to-end path. Negative error means the model is too fast.

| Pages | Observed P50 ns | Nominal scalar ns | Scalar error | Constant-page ns | Constant error |
|---:|---:|---:|---:|---:|---:|
| 1 | 11,133 | 10,008 | -10.11% | 11,133 | 0.00% |
| 4 | 41,495 | 10,032 | -75.82% | 44,532 | +7.32% |
| 16 | 168,606 | 10,128 | -93.99% | 178,128 | +5.65% |
| 64 | 2,824,351 | 10,512 | -99.63% | 712,512 | -74.77% |
| 256 | 10,767,793 | 12,048 | -99.89% | 2,850,048 | -73.53% |
| 512 | 20,254,374 | 14,096 | -99.93% | 5,700,096 | -71.86% |

The empirical curve has zero error at these points by construction. That is a
deterministic calibration check, not cross-validation.

## Real-GPU replay

Hardware: GPU 0, NVIDIA RTX PRO 6000 Blackwell Server Edition, UUID
`GPU-f07ea2df-1b6f-9a02-b534-5090abf3c174`, driver 595.84, 97,887 MiB.

The kernel used one thread and one volatile byte load from the first byte of
each 4 KiB page. Each tuned process created a fast-mode context, registered the
whole CUDA allocation as an explicit read-only timing range, launched once,
synchronized, read stats, unregistered, and destroyed the context.

| Pages | Baseline/tuned checksum | Modeled ns | Fast | Reference | Modeled launches | Unsafe | Tuned wall ns |
|---:|---:|---:|---:|---:|---:|---:|---:|
| 1 | 90 | 11,133 | 1 | 0 | 1 | 0 | 7,845,022 |
| 4 | 204,150,092 | 41,495 | 4 | 0 | 1 | 0 | 8,583,822 |
| 16 | 1,878,110,302,261,444,384 | 168,606 | 16 | 0 | 1 | 0 | 9,131,229 |
| 64 | 16,593,177,280,001,001,984 | 2,824,351 | 64 | 0 | 1 | 0 | 13,746,789 |
| 256 | 1,382,779,836,794,696,192 | 10,767,793 | 256 | 0 | 1 | 0 | 33,126,462 |
| 512 | 2,017,642,860,456,240,128 | 20,254,374 | 512 | 0 | 1 | 0 | 48,852,987 |

Every pass manifest reports `hbf_vmem_sequential`, 30 rewritten instructions,
zero unsupported instructions, and three inspected parameters. The scalar
output store is also instrumented but remains ordinary HBM because it is
outside the registered input range.

Wall time includes process startup, bpftime attach, context lifecycle, launch,
and synchronization overhead. It is not a projected HBF device latency.

## Reproduction and artifacts

```bash
python3 scripts/tune_vmem_profile.py \
  --input-csv /home/victoryang00/nvme-mem2nvm/docs/superpowers/results/2026-07-30-vmem-sw-performance.csv \
  --base-profile configs/profiles/nominal.json \
  --output-profile configs/profiles/cd8p-vmem-p50.json \
  --output-report configs/tuning/cd8p-vmem-comparison.json \
  --expected-sha256 4fb6d2847c3ce4a09b7f2ce07dcb4cf8254145243c1985bce2848261b8d0724f

cmake --build /dev/shm/hbfsim-vllm-gpu13 \
  --target hbf_vmem_tuning_bench hbfsimd \
           hbfsim_vmem_tuning_probe -j2
HBFSIM_BUILD_DIR=/dev/shm/hbfsim-vllm-gpu13 \
HBFSIM_BPFTIME_BUILD_DIR=/dev/shm/hbfsim-bpftime-variant-gcc14 \
python3 scripts/run_vmem_tuning_bench.py \
  --profile configs/profiles/cd8p-vmem-p50.json \
  --output /mnt/disk2/hbfsim-cd8p-vmem-tuning-20260811/summary.json
```

- Full live artifact directory:
  `/mnt/disk2/hbfsim-cd8p-vmem-tuning-20260811` (25 files, 168 KiB).
- Summary SHA256:
  `d4e7976abdcc9e1e7160ee5cccb3d0e957096a8c90fd9117c5cff8cb942f010e`.
- Profile SHA256:
  `1ed91e062adc9c43a56eb54a62219b4a24e5a67d1f2fd2782570fc7eb3692354`.
- Comparison report SHA256:
  `e161673526d3725600423f966db05d4d6c6cc8fde2c456dcfa6082b379b09c08`.

The full artifact directory contains each baseline/tuned JSON result, coverage
JSONL, pass manifest, daemon durability report, and the consolidated summary.
