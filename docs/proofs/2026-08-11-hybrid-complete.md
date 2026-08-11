# Hybrid HBFSim real-GPU completion checkpoint

Date: 2026-08-11 UTC

This checkpoint closes the fast/hybrid timing, public over-VRAM capacity,
llama.cpp timing-only, and GPU/CD8P LogP validation gates. It deliberately
keeps physical observations separate from extrapolated overheat scenarios.

## Hardware and software

- GPU: NVIDIA RTX PRO 6000 Blackwell Server Edition, driver 595.84, compute
  capability 12.0, 97,887 MiB.
- Flash proxy: `/dev/nvme1n2`, Dell DC NVMe CD8P E3.S 1.92TB, serial
  `7EU0A01P0XK1`.
- CUDA toolkit used for HBFSim and llama.cpp: 13.0.88.
- bpftime commit: `ec26daecc8e787fb80fd95dd596a576404a5e36e` with the repository
  provenance patch.
- llama.cpp commit: `7ba604f1cb61cd14898138e9abc0b4ff2601f180`.

## Timing optimization

The device control ABI now selects `reference`, `fast`, or sampled `hybrid`.
Fast mode computes latency and bandwidth delay on the GPU without a
request/completion round trip. Hybrid mode samples the detailed MQSim path
after a configurable warmup and uses it to calibrate access classes.

On the deterministic Qwen3-30B-A3B vLLM smoke, reference generation took
44.469 s and fast generation took 2.014352 s, a 20.8x emulator speedup. Both
runs produced token IDs `[271, 32313, 11, 1077, 594, 1430, 311, 7071]`.
This is emulator wall time, not a hardware HBF projection.

## Public over-VRAM capacity

The public `hbfsim_map_file` benchmark reserved a 110 GiB logical range and
used a bounded 2 GiB HBM cache. The random sparse workload touched the first
and last logical regions and completed 128 accesses:

- checksum and baseline: `14245581564465502923`;
- requests submitted/completed: 128/128;
- automatic-PTX modeled launches: 1;
- unsafe launches: 0;
- kernel wall time: 178,981,713 ns.

The committed summary is
[`artifacts/2026-08-11-over-vram-summary.json`](artifacts/2026-08-11-over-vram-summary.json).
The backing file was sparse and self-unlinked. This proves a logical span above
VRAM and correct page routing; it does not claim that 110 GiB was physically
read during the 128-access run.

## llama.cpp timing-only adapter

The patch registers CUDA weight buffers as explicit timing ranges and rejects
capacity mode. One GPU `globaltimer` delay probe is enqueued on the llama CUDA
stream per graph. The adapter does not claim that opaque llama kernels were
rewritten.

TinyLlama F16 generated the same deterministic text in baseline and timing
modes. The timing run recorded ten 50 ms GPU injections (500 ms modeled total).
The end-to-end wall times were 1.470 s baseline and 9.792 s timing; startup,
range publication, and gate overhead are intentionally included. The committed
summary is
[`artifacts/2026-08-11-llama-summary.json`](artifacts/2026-08-11-llama-summary.json).

## GPU and CD8P LogP thermal validation

The validation ran BF16 8192x8192 GEMM concurrently with read-only fio for
30 seconds, followed by 20 seconds of cooling and one-second telemetry. The
collector discovered the CD8P by exact model name and refused holders or
mounts. smartmontools was not installed on this host, so the same NVMe SMART
health log was read through `nvme smart-log -o json`.

- raw GPU temperature: 28 to 73 degrees C;
- CD8P SMART temperature: 34 to 37 degrees C;
- CD8P reads: 7.577 GB/s, 57.81k IOPS, 1.103 ms mean completion latency;
- CD8P writes: 0;
- critical warnings/media-error delta: 0/0;
- GPU BF16 sampled performance change: -3.72%, exact scalar checksum.

Following Equation 1 of `2333660.2333670.pdf`, the fitter uses seven samples
with weights `1/2^k`. It then minimizes log-temperature-rise error for a
first-order response. The fitted time constants are 13.1 s for the GPU and
12.4 s for the CD8P. The named calibration is
[`configs/thermal/gpu-cd8p-logp-live.json`](../../configs/thermal/gpu-cd8p-logp-live.json).

The `simulated-warning` profile is virtual. It extrapolates the calibrated
curves and crosses the configured GPU and SSD warning points at 30 s and 42 s,
respectively. No physical device was driven to a warning or critical
temperature.

Full live artifacts remain at:

- `/mnt/disk2/hbfsim-thermal-logp-20260811`
- `/mnt/disk2/hbfsim-llama-proof-20260811`
- `/mnt/disk2/hbfsim-over-vram-proof-20260811-nYKfkJ`
