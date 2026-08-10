# Live GPU and Dell CD8P Hardware Checkpoint

Date: 2026-08-10

Branch: `hybrid`

HBFSim implementation under test: `b3b2b19f69539a4376e877da6c702fc28d248ed8`

## Result at a glance

| Gate | Result |
|---|---|
| Direct capacity runtime on a real GPU | **Passed** |
| GPU BF16 GEMM workload | **Passed**, stable scalar checksum |
| Dell CD8P read-only media baseline | **Passed**, zero writes issued |
| GPU thermal slowdown observation | **Observed** after a warm-start run |
| vLLM Qwen3-30B-A3B throughput | **Not obtained**; 0/4 prompts completed |
| Public API + automatic PTX + MQSim on a real GPU | **Not proven** |
| llama.cpp, larger-than-VRAM, and calibrated HBF thermal model | **Not proven** |

This checkpoint deliberately separates hardware facts from HBFSim claims. The
CUDA capacity smoke exercises real CUDA VMM frames and copies, but it enters
the internal capacity runtime directly. The GEMM and vLLM runs are unmodified
hardware workloads. They are not evidence of automatic PTX rewriting, live
delay injection, public `hbfsim_map_file` use by an application, or MQSim
timing in a running kernel.

## Hardware identity and topology

The live GPU was:

```text
NVIDIA RTX PRO 6000 Blackwell Server Edition
GPU UUID GPU-f07ea2df-1b6f-9a02-b534-5090abf3c174
driver 595.84, compute capability 12.0, 97,887 MiB
PCI BDF 0000:8a:00.0
```

The requested SSD was positively identified from block sysfs, NVMe identify,
and PCI configuration space:

```text
/dev/nvme1n2
Dell DC NVMe CD8P E3.S 1.92TB
serial 7EU0A01P0XK1, firmware 2.0.0
PCI BDF 0000:d8:00.0, KIOXIA 1e0f:002c
PCIe 5.0, 32 GT/s x4, NUMA node 1
```

The CD8P is a conventional PCIe NVMe endpoint, not a CXL endpoint. The host's
separate CXL device appears as `mem0` and committed RAM `region0`; no result in
this artifact relabels the CD8P as a CXL SSD. The namespace had no filesystem,
mount, or holder. All fio invocations below used both `--readonly` and
`--rw=read`; their reports show zero writes.

Initial CD8P SMART data reported 35 degrees C, zero critical warnings, zero
media errors, a 77 degrees C warning threshold (`wctemp=350 K`), and an
83 degrees C critical threshold (`cctemp=356 K`).

## Real-GPU capacity-runtime smoke

The generated live-test target was built from the CUDA-enabled static tree and
run with its small temporary backing file on `/dev/shm` because the host root
filesystem had no free blocks:

```bash
cmake --build build-verify-worker-cuda13 \
  --target capacity_runtime_live_test -j2
TMPDIR=/dev/shm timeout 30s \
  ./build-verify-worker-cuda13/tests/gpu/capacity_runtime_live_test
```

Captured result:

```text
capacity runtime logical cache cap: 32768 bytes in two 16384-byte frames
capacity runtime live smoke passed
```

This proves real CUDA context creation, a real VMM frame pool, host-to-device
page fill, a CUDA sentinel kernel write, device-to-host dirty flush, and an
exact backing-byte check. GPU temperature changed from 27 to 28 degrees C.
It does not exercise bpftime interception or the public capacity API.

## Read-only CD8P media baseline

The reproducible fio shape was:

```bash
fio --name=cd8p-seq-read \
  --filename=/dev/nvme1n2 --readonly --rw=read \
  --bs=128k --iodepth=64 --ioengine=io_uring --direct=1 \
  --time_based=1 --runtime=20 --size=64G --offset=0 \
  --group_reporting=1 --eta=never
```

| Measurement | CD8P alone | Concurrent with GPU GEMM |
|---|---:|---:|
| Duration | 20.002 s | 20.002 s |
| Read bandwidth | 7,586 MB/s | 7,587 MB/s |
| Read IOPS | 57.9k | 57.9k |
| Average completion latency | 1,100.14 us | 1,100.24 us |
| p99 completion latency | 1,270 us | 1,270 us |
| Data read | 152 GB | 152 GB |
| Device utilization | 99.28% | 99.48% |
| Writes issued | 0 | 0 |
| SMART temperature | 36 to 38 degrees C | 35 to 37 degrees C |

Within the resolution of these single runs, concurrent GPU compute did not
materially change CD8P sequential-read throughput or p99 latency. This is a
media and system-interference baseline, not HBFSim model validation.

## GPU workload and thermal comparison

The GPU workload used PyTorch 2.9.1+cu128 to repeat BF16 `8192 x 8192` GEMMs
for at least 20 seconds. CUDA events measured device time; each run used seed
zero and reported `c[0,0]` as a scalar checksum. This does not establish
bit-exact equality of the full output matrix.

```python
import time
import torch

torch.manual_seed(0)
n = 8192
a = torch.randn((n, n), device="cuda", dtype=torch.bfloat16)
b = torch.randn((n, n), device="cuda", dtype=torch.bfloat16)
for _ in range(3):
    c = torch.mm(a, b)
torch.cuda.synchronize()

start = torch.cuda.Event(enable_timing=True)
stop = torch.cuda.Event(enable_timing=True)
start.record()
iterations = 0
elapsed_ms = 0.0
while elapsed_ms < 20_000:
    for _ in range(20):
        c = torch.mm(a, b)
        iterations += 1
    stop.record()
    stop.synchronize()
    elapsed_ms = start.elapsed_time(stop)

flops = 2.0 * n * n * n * iterations
print(flops / (elapsed_ms / 1000) / 1e12, float(c[0, 0]))
```

For reproduction, save the preceding body as `/dev/shm/hbfsim-gemm.py`. Run it
alone for an isolated measurement. For the concurrent measurement, start the
documented fio command first, start the Python program immediately afterward,
sample while either PID is live, and then wait for both:

```bash
fio --name=cd8p-seq-read-concurrent \
  --filename=/dev/nvme1n2 --readonly --rw=read \
  --bs=128k --iodepth=64 --ioengine=io_uring --direct=1 \
  --time_based=1 --runtime=20 --size=64G --offset=0 \
  --group_reporting=1 --eta=never &
fio_pid=$!

python3 /dev/shm/hbfsim-gemm.py &
gemm_pid=$!

while kill -0 "$gemm_pid" 2>/dev/null || kill -0 "$fio_pid" 2>/dev/null; do
  date -u +%Y-%m-%dT%H:%M:%SZ
  nvidia-smi \
    --query-gpu=temperature.gpu,power.draw,utilization.gpu,clocks.sm,clocks.mem,memory.used \
    --format=csv,noheader,nounits
  nvme smart-log /dev/nvme1n2 -o json |
    jq '{temperature_c:(.temperature-273),critical_warning,media_errors}'
  sleep 2
done

wait "$gemm_pid"
wait "$fio_pid"
nvidia-smi -q -d PERFORMANCE
```

| Mode | Start to peak GPU temperature | Throughput | Change from cold isolated | Checksum |
|---|---:|---:|---:|---:|
| Cold isolated GPU | 35 to 70 degrees C | 379.117 TFLOP/s | baseline | 66.5 |
| GPU + CD8P read | 51 to 83 degrees C | 371.601 TFLOP/s | -1.98% | 66.5 |
| Warm isolated GPU | 61 to 85 degrees C | 348.427 TFLOP/s | -8.10% | 66.5 |

The warm isolated run is the thermal comparison. Its sampled SM clock fell from
about 2,062 MHz to 1,642 MHz. Immediately before it, the driver's cumulative
`SW Thermal Slowdown` counter was zero; after it, the counter was 8,279,678 us.
No hardware thermal-slowdown or power-brake event was reported. The workload
kept the same scalar checksum; the software thermal-slowdown event coincided
with lower clock and throughput.

Because the concurrent run started warmer than the cold baseline, its 1.98%
difference cannot be assigned entirely to storage interference. The paired
CD8P results show essentially unchanged SSD throughput, while the warm GPU
comparison shows that a hotter starting state was sufficient to coincide with
a larger performance loss in this run.

## vLLM boundary found during the live run

The installed stack was vLLM 0.15.1, PyTorch 2.9.1+cu128, and a local 57 GB
Qwen3-30B-A3B model. A four-request random workload used 32 input and 32 output
tokens, BF16, eager execution, a 256-token model limit, and seed zero.

The exact attempted command was:

```bash
export TMPDIR=/dev/shm
export VLLM_CACHE_ROOT=/dev/shm/hbfsim-vllm-cache
export TORCHINDUCTOR_CACHE_DIR=/dev/shm/hbfsim-torchinductor
export HF_HUB_OFFLINE=1
export TRANSFORMERS_OFFLINE=1

timeout 600s vllm bench throughput \
  --backend vllm \
  --dataset-name random \
  --random-input-len 32 \
  --random-output-len 32 \
  --num-prompts 4 \
  --model /home/victoryang00/Qwen3-30B-A3B \
  --tokenizer /home/victoryang00/Qwen3-30B-A3B \
  --dtype bfloat16 \
  --max-model-len 256 \
  --gpu-memory-utilization 0.85 \
  --enforce-eager \
  --seed 0 \
  --disable-log-stats
```

vLLM resolved `Qwen3MoeForCausalLM`; weight loading completed in 48.389 s and
consumed 56.88 GiB of GPU memory. After requests were submitted, however, the
run remained at 0/4 prompts for 2 minutes 3 seconds. GPU utilization stayed at
zero while `VLLM::EngineCore` used roughly one CPU core. The run was stopped
with SIGINT and exited 130, so it produced no valid latency or throughput.
This is a concrete vLLM/Triton-MoE blocker, not an HBFSim performance result.

## Safety and post-run checks

- The CD8P namespace received reads only; fio reported no writes.
- CD8P SMART ended with zero critical warnings and zero media errors.
- No new NVIDIA Xid, NVMe I/O, PCIe, or AER warning was logged during the
  checkpoint window.
- No fio, vLLM, live-test, or GPU benchmark process remained running.
- GPU memory returned to zero MiB used after stopping vLLM.

The next valid HBF performance comparison requires a workload adapter whose
HBF pointer coverage passes the launch gate, followed by baseline and HBF runs
with identical inputs and a bit-exact output gate. This checkpoint does not
substitute these hardware baselines for that missing end-to-end proof.
