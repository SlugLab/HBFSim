# HBFSim

On `eval_base`, start with the [Evaluation integration and reproduction index](docs/eval/README.md). The proof results below remain tied to their original source snapshots; current validation and deferred features are recorded in the integration manifest.

HBFSim is a live workload emulator for studying a simple systems question:

> What would GPU applications look like if they could directly access a large,
> flash-backed memory tier with much higher bandwidth than conventional storage,
> but higher latency than HBM?

The project calls that hypothetical tier **High-Bandwidth Flash (HBF)**. HBFSim
does not assume a particular vendor device or finalized HBF standard. Instead,
it provides named, synthetic profiles so researchers can explore the design
space before hardware exists.

## The high-level idea

Most storage simulators replay traces after an application has finished, while
most GPU memory simulators do not execute the original CUDA workload. HBFSim is
designed to keep the workload live: the application runs normally, selected GPU
memory operations are identified automatically, and HBF timing or capacity
effects are applied during execution.

Three ideas make this possible:

1. **Explicit HBF ranges define intent.** Only addresses registered by the
   application or runtime are treated as HBF. Ordinary HBM pointers remain on
   the native fast path.
2. **PTX rewriting provides visibility.** A bpftime/eGPU-derived interception
   path rewrites supported global loads and stores so HBF accesses can be
   resolved without modifying each CUDA kernel by hand. A coverage gate rejects
   any HBF pointer that reaches code whose behavior cannot be proven safe.
3. **Detailed and fast timing models work together.** An online, media-only
   MQSim path is the detailed reference model. A calibrated GPU-local model is
   intended to handle the common path cheaply, while sampled requests keep it
   anchored to MQSim.

The result is intended to preserve application semantics while changing where
data comes from and how long access takes.

### Calibrating the model from a real vmem path

Named synthetic profiles remain useful for design-space exploration, but they
do not capture the non-linear cost of a complete software-backed memory path.
The `cd8p-vmem-p50` profile is calibrated from the committed
`nvme-mem2nvm` cold-fault measurements on a Dell CD8P. It records cumulative
P50 latency at 1, 4, 16, 64, 256, and 512 contiguous 4 KiB pages rather than
reducing the path to one constant page latency or bandwidth number.

For a sequential GPU access, HBFSim derives the current page's marginal delay
from that cumulative curve and serializes it on the GPU-local fast timing
channel. Random, reverse, repeated, or cross-operation accesses start a new
burst. Legacy profiles still use the scalar latency/bandwidth model; malformed
empirical metadata fails closed instead of falling back silently.

This is a calibration of the complete measured vmem path, including its
software overhead. The CD8P is PCIe NVMe, not a physical CXL endpoint, and an
exact fit at the six source breakpoints is not an independent prediction. The
[CD8P vmem proof](docs/proofs/2026-08-11-cd8p-vmem-tuning.md) records the source
hash, scalar-model error, and a real-GPU automatic-PTX replay of all six points.

## Two complementary modes

| Mode | What changes | Primary question |
|---|---|---|
| **Timing-only** | Data stays in normal GPU memory; HBFSim injects modeled delay for registered accesses. | How sensitive is this workload to HBF latency, bandwidth, and contention? |
| **Capacity** | Registered data is backed by a file and staged through an HBM page cache. | Can a workload run with a working set larger than available VRAM, and what cache behavior results? |

Both modes use the same explicit ranges, PTX coverage rules, named HBF profiles,
and reporting model. Timing-only mode isolates delay from paging. Capacity mode
adds page residency, eviction, and backing I/O.

In capacity mode, all registered file ranges in a context share one bounded HBM
page cache. A cache hit resolves directly to its resident HBM frame. A miss
loads the backing page and contributes one modeled media read; a dirty eviction
contributes a modeled media program before its bytes return to the backing
file. MQSim therefore sees the HBF media work caused by misses and dirty
writebacks, rather than every GPU load and store.

## Intended end-to-end path

```text
CUDA workload (microbenchmark, llama.cpp, or vLLM)
                         |
                         v
       explicit HBF ranges + fail-closed coverage gate
                         |
                         v
          automatic PTX load/store instrumentation
                         |
                         v
             GPU range lookup and page resolver
                  /                    \
                 v                      v
       HBM / HBM-cache hit       shared request ring
                                         |
                                         v
                                HBF host service
                                /              \
                               v                v
                    online MQSim timing   file backing store
```

MQSim is used as a flash-media model, not as an SSD host-stack model. The HBF
adapter bypasses NVMe, PCIe, SATA, and host-driver events while retaining flash
mapping, transaction scheduling, NAND timing, queueing, contention, and channel
behavior.

HBFSim reports modeled device time separately from host service time, wall-clock
time, and emulator overhead. This separation is essential: a live emulator can
be functionally correct while its own software overhead is larger than the
device delay it is trying to model.

## What HBFSim is meant to evaluate

- sensitivity to HBF read/program latency, channel count, queue depth, and
  aggregate bandwidth;
- the benefit of an HBM cache in front of a much larger flash-backed tier;
- detailed MQSim timing versus a calibrated fast model;
- correctness and failure behavior when only part of a CUDA workload can be
  instrumented; and
- end-to-end effects on deterministic llama.cpp and vLLM inference, including
  bit-exact token checks against each runtime's own baseline.

## Project status

The `hybrid` branch now has real-GPU proof for automatic timing injection,
public file-backed capacity beyond physical VRAM, deterministic vLLM and
llama.cpp workloads, and GPU/CD8P thermal calibration. The table distinguishes
those live gates from narrower CPU, fake-driver, and static PTX checks.

| Component | Status |
|---|---|
| Pinned bpftime and MQSim dependencies | Implemented |
| Named synthetic HBF profiles | Implemented and validated |
| Request/completion protocol and page state machine | Implemented and tested |
| Incremental media-only MQSim interface and trace equivalence | Implemented and tested |
| Reproducible MQSim media benchmark | Implemented and tested |
| PTX rewriting for supported global loads/stores | Implemented; static PTX checks pass |
| bpftime pass ABI and fail-closed CUDA launch gate | Implemented; static/Release checks pass |
| Live bpftime + GPU interception proof | Passed on real vLLM Triton `cuLaunchKernelEx` variants |
| Timing-only host range registration and host service | Implemented; CPU/static checks pass |
| PTX resolver helper | Implemented; self-contained PTX and CUDA 12.8 assembly checks pass |
| vLLM timing-only adapter | Real Qwen3-30B-A3B execution passed with selective named ranges and bit-exact tokens |
| Live timing-only GPU delay proof | Passed: 24 modeled fused-MoE launches on an explicit 16 KiB weight range |
| File-backed capacity mode | Public `map`/`flush`/`unregister`, multi-file routing, shared bounded cache, MQSim miss/writeback timing, and checked teardown pass CPU/fake-driver tests |
| Direct real-GPU capacity-runtime smoke | Passed on RTX PRO 6000: VMM frame fill, CUDA kernel write, dirty flush, and backing-byte check |
| Hybrid fast model | Implemented with named `reference`, `fast`, and sampled `hybrid` modes |
| Public/PTX real-GPU capacity and over-VRAM proof | Passed with a 110 GiB logical range and a 2 GiB HBM cache |
| CUDA fault matrix and llama.cpp proof runs | Passed; TinyLlama timing-only injection preserves deterministic output |
| GPU and Dell CD8P thermal validation | Calibrated LogP profile from live BF16 heating and read-only SMART/fio telemetry |

Builds, CPU tests, MQSim regressions, and successful PTX assembly are not live
GPU proof. The newer live results below close the earlier timing scalability,
over-VRAM capacity, llama.cpp, and LogP calibration proof gates, while keeping
their boundaries explicit. The complete non-live
checkpoint and exact commands are recorded
in [the 2026-08-10 non-live proof artifact](docs/proofs/2026-08-10-capacity-runtime-non-live.md).
A separate [live hardware checkpoint](docs/proofs/2026-08-10-live-gpu-cd8p-thermal.md)
records the bounded real-GPU smoke, GPU thermal response, and read-only Dell
CD8P media baseline without treating them as end-to-end HBF workload proof.
The [real-GPU vLLM adapter proof](docs/proofs/2026-08-11-vllm-timing-adapter.md)
records bit-exact Qwen execution, registration coverage, performance, and the
cubin-only instrumentation blocker.
The [exact Triton live-delay proof](docs/proofs/2026-08-11-vllm-exact-live-delay.md)
supersedes that blocker with automatic variant rewriting, nonzero modeled
coverage, a matched baseline, and a read-only Dell CD8P comparison.

### Latest live benchmark

The validated Qwen3-30B-A3B smoke used one 32-token prompt and generated eight
tokens. A matched baseline took 0.270 s; the detailed reference path took
44.469 s, while the fast path completed in 2.014 s and preserved the exact
token IDs. Fast mode is therefore about 20.8x faster than the reference
emulator on this proof shape. These wall-clock ratios characterize the
emulator, not projected HBF hardware.

The public capacity benchmark also completed a 110 GiB logical workload on a
97,887 MiB RTX PRO 6000 using a 2 GiB HBM cache. All 128 sampled accesses
matched the baseline checksum, all 128 page requests completed, and no unsafe
launch was admitted. This is a sparse logical-capacity proof: it demonstrates
address span and page routing beyond VRAM, not that 110 GiB of payload was
resident or read during the short run.

The llama.cpp timing-only adapter ran TinyLlama-1.1B-Chat-v1.0 F16 with ten
50 ms GPU delay injections. Baseline and timing runs both generated
`The author suggests that the fastest route`; the repository runner treats any
semantic mismatch or zero-injection run as a failure.

A fresh concurrent, read-only Dell CD8P run sustained 7.577 GB/s and 57.81k
IOPS with zero writes. GPU temperature rose from 28 to 73 degrees C and CD8P
SMART temperature rose from 34 to 37 degrees C. The fitted first-order LogP
profile has time constants of 13.1 s (GPU) and 12.4 s (SSD); GPU BF16
throughput changed by -3.72% across the sampled heating run with an exact
scalar checksum. The CD8P is PCIe NVMe rather than a CXL endpoint; it is the
physical flash/thermal proxy for the proposed HBF/CXL-attached storage tier.
The consolidated evidence and proof boundaries are recorded in
[the 2026-08-11 hybrid completion checkpoint](docs/proofs/2026-08-11-hybrid-complete.md).

### Reproduce the live paths

The microbenchmark covers automatic PTX rewriting, all access patterns,
timing modes, public file-backed capacity, and over-VRAM logical spans:

```bash
python3 scripts/run_microbench.py --help
```

The end-to-end CD8P-vmem calibration has its own exact-breakpoint runner. It
does not open `/dev/vmem0` or the raw NVMe namespace:

```bash
cmake --build /dev/shm/hbfsim-vllm-gpu13 \
  --target hbf_vmem_tuning_bench hbfsimd \
           hbfsim_vmem_tuning_probe -j2
HBFSIM_BUILD_DIR=/dev/shm/hbfsim-vllm-gpu13 \
HBFSIM_BPFTIME_BUILD_DIR=/dev/shm/hbfsim-bpftime-variant-gcc14 \
python3 scripts/run_vmem_tuning_bench.py \
  --profile configs/profiles/cd8p-vmem-p50.json \
  --output /path/to/cd8p-vmem-summary.json
```

The pinned llama.cpp adapter and deterministic comparison are driven by:

```bash
HBFSIM_BUILD_DIR=/dev/shm/hbfsim-release-gpu13 \
  adapters/llama_cpp/build.sh
python3 adapters/llama_cpp/run.py --mode compare \
  --llama-cli /dev/shm/hbfsim-llama-build/bin/llama-cli \
  --model /path/to/tinyllama-f16.gguf \
  --hbf-build /dev/shm/hbfsim-release-gpu13 \
  --profile configs/profiles/nominal.json \
  --report-dir /path/to/report
```

Thermal validation discovers the CD8P by exact model name, refuses mounted or
held namespaces, and uses read-only fio. The simulated warning profile
extrapolates the calibration without driving hardware to unsafe temperatures:

```bash
python3 scripts/thermal/collect.py --output /path/to/thermal-proof
python3 scripts/thermal/fit_logp.py \
  --input /path/to/thermal-proof --output /path/to/profile.json
python3 scripts/thermal/simulate_overheat.py \
  --calibration configs/thermal/gpu-cd8p-logp-live.json \
  --scenarios configs/thermal/scenarios.json \
  --profile simulated-warning --output /path/to/simulation.json
```

## Requirements

- Native Linux
- Git with submodule support
- CMake 3.25 or newer
- Ninja
- A C++20 compiler
- Python 3
- CUDA 13.0 at `/usr/local/cuda-13.0` for the validated Blackwell build;
  CUDA 12.8 remains the minimum supported toolkit for PTX validation

The current media simulator and its benchmark can be built without a GPU.

The capacity runtime owns the logical CUDA VMM ranges, one shared HBM frame
pool, the clock cache, backing-file router, bounce page, page service, and
parent worker. Public `hbfsim_map_file`, `hbfsim_flush`, and
`hbfsim_unregister` use transactional publication and checked rollback. Dirty
teardown failures quarantine the owner so a relevant launch fails closed
instead of bypassing unresolved state. These properties have CPU,
CUDA-static/PTX, fake-driver, MQSim, and real-GPU coverage. The 110 GiB
logical-range run is the public API plus automatic PTX capacity gate; it
complements rather than replaces the failure-injection tests.

## Clone and build

```bash
git clone --branch hybrid --recurse-submodules \
  https://github.com/SlugLab/HBFSim.git
cd HBFSim

HBFSIM_ENABLE_CUDA=OFF \
HBFSIM_ENABLE_MQSIM=ON \
./scripts/bootstrap.sh

cmake --build build -j
ctest --test-dir build --output-on-failure
```

The build checks that the submodules are at the required revisions:

- bpftime: `ec26daecc8e787fb80fd95dd596a576404a5e36e`
- MQSim: `51f0f2d3fed92d88ef4a0fa61a38024b07bf9d16`

HBFSim copies MQSim into `build/_deps/mqsim-hbf-src`, checks and applies
`patches/mqsim/0001-online-hbf-api.patch`, then compiles the patched copy. The
MQSim submodule remains clean.

Build options:

| Option | Default | Purpose |
|---|---:|---|
| `HBFSIM_ENABLE_CUDA` | `ON` | Enable CUDA-facing components and toolkit validation |
| `HBFSIM_ENABLE_MQSIM` | `ON` | Build the online MQSim backend and media benchmark |
| `HBFSIM_ENABLE_LLM_TESTS` | `OFF` | Enable environment-dependent llama.cpp and vLLM integration tests |

### Selecting a CUDA architecture

The CUDA device helper does not use Blackwell-only instructions. A build can
therefore select one numeric baseline architecture of compute capability 7.0
or newer through `CMAKE_CUDA_ARCHITECTURES`; `120` remains the default and the
reference target. A single architecture is required because HBFSim embeds the
helper PTX into each instrumented module. Compiling and assembling another
target is not proof that the live host/GPU control protocol is portable to that
platform: CUDA does not guarantee that GPU atomics to mapped page-locked host
memory are atomic from the host's point of view. See
[`docs/cuda-architecture-compatibility.md`](docs/重要实现问题以及需补做实验/cuda-problems-from-new-collaborator/cuda-architecture-compatibility.md)
for the feature audit, support levels, and validation requirements.

CUDA Toolkit can be kept in a user directory without installing a Linux driver
or changing the shell environment. For example:

```bash
env \
  CUDAToolkit_ROOT="$HOME/opt/cuda-12.8" \
  CUDACXX="$HOME/opt/cuda-12.8/bin/nvcc" \
  PATH="$HOME/opt/cuda-12.8/bin:$PATH" \
  cmake -S . -B build-sm89 -G Ninja \
    -DHBFSIM_ENABLE_CUDA=ON \
    -DHBFSIM_ENABLE_MQSIM=ON \
    -DCMAKE_CUDA_ARCHITECTURES=89 \
    -DCUDAToolkit_ROOT="$HOME/opt/cuda-12.8" \
    -DCMAKE_CUDA_COMPILER="$HOME/opt/cuda-12.8/bin/nvcc"
```

## Run the MQSim media benchmark

The current benchmark submits deterministic sequential requests through
`MqsimOnlineEngine` and emits one JSON document:

```bash
./build/hbf_mqsim_bench \
  --profile configs/profiles/nominal.json \
  --requests 4096 \
  --bytes 16384 \
  --operation read \
  --arrival-gap-ns 0 \
  > mqsim-nominal-read.json
```

Available workload controls:

| Argument | Values | Default |
|---|---|---:|
| `--profile` | Path to a profile JSON file | Required |
| `--requests` | Positive request count | `1024` |
| `--bytes` | Non-zero multiple of 512 | `16384` |
| `--operation` | `read`, `write`, or alternating `mixed` | `read` |
| `--arrival-gap-ns` | Modeled gap between submissions | `0` |
| `--capacity-bytes` | Effective reference-model capacity | Auto: 16 blocks/plane |

The benchmark reports:

- average, p50, and p99 modeled request latency;
- modeled makespan and modeled bandwidth;
- host wall time and simulator requests per second; and
- the effective MQSim profile and completed-request count.

By default, the benchmark chooses the smaller of the profile capacity and a
geometry with 16 blocks per plane. This keeps MQSim's reference mapping tables
reasonably sized while leaving enough free blocks for its write/GC guard. It
does not change the selected profile's NAND latency, channel count, queue depth,
or bandwidth cap. Set `--capacity-bytes` explicitly when capacity geometry is
part of the experiment; write and mixed runs reject geometries with ten or
fewer blocks per plane instead of stalling.

To run the benchmark regression gate:

```bash
python3 tests/integration/test_mqsim_benchmark.py
```

This benchmark is media-only. The complete CUDA benchmark will additionally
measure live injected delay, semantic checksums, coverage, cache behavior,
fault handling, and over-VRAM capacity after those runtime components land.

## Named HBF profiles

| Profile | Page | Read | Program | Channels | Queue depth | Aggregate cap |
|---|---:|---:|---:|---:|---:|---:|
| `conservative` | 16 KiB | 20 us | 200 us | 16 | 64 | 128 GB/s |
| `nominal` | 16 KiB | 10 us | 100 us | 32 | 128 | 512 GB/s |
| `aggressive` | 16 KiB | 5 us | 50 us | 64 | 256 | 1 TB/s |

Profiles live in `configs/profiles/` and are checked by the typed loader. The
schema is `configs/schema/hbf-profile.schema.json`.

## MQSim trace input

`hbfsim::run_mqsim_trace` accepts MQSim's five-column ASCII request format:

```text
arrival_ns device start_sector sector_count operation
```

For the HBF media path, `device` must be `0`; operation `0` is a write and `1`
is a read. Addresses and sizes use 512-byte sectors. The adapter rejects
non-monotonic arrivals, overflow, unsupported operations, and out-of-capacity
requests.

## PTX rewriting pass

The standalone pass consumes bpftime-style JSON on standard input and emits
transformed PTX plus a coverage manifest on standard output. Its configuration
is in `configs/ptxpass/hbf-memory.json`.

Run its integration check with:

```bash
python3 tests/integration/run_ptxpass_json.py \
  build/src/ptxpass_hbf/ptxpass_hbf sm_120 "$(command -v ptxas)"
```

When CUDA 12.8 is installed, the check assembles rewritten PTX for the
single architecture selected at configure time with `ptxas`. The initial pass
recognizes selected scalar/vector,
predicated, offset, and cache-qualified global loads and stores. Atomics,
generic-space operations, texture/surface operations, malformed addresses, and
inline SASS remain outside the supported HBF path. The runtime coverage gate
rejects a relevant launch when those operations could consume an HBF pointer;
this behavior has static/fake-driver coverage but no live-GPU proof yet.

For a modified module, the pass now embeds one PTX-callable resolver directly
into that module. The helper validates control ABI v2 and the exact control
generation, searches at most 64 sorted explicit ranges, coalesces matching
lanes by warp and page, and exchanges timing requests with the host through
system-scope ordered rings. Each range is assigned a page-aligned synthetic
media interval within the selected profile's capacity, so MQSim sees bounded
HBF page addresses rather than process-specific GPU virtual addresses.
Out-of-range HBM addresses remain unchanged, while an access spanning two HBF
pages is rejected until split-access support exists.

Only a CUDA-enabled build contains the production helper PTX. A CPU-only pass
therefore rejects a module that would require instrumentation instead of
emitting unresolved or user-supplied resolver symbols. Repeated per-kernel
passes accept an existing helper only when the plugin can authenticate the
entire module as one it previously emitted. The build checks that the resulting
module is self-contained and assembles it with CUDA 12.8 `ptxas`; this is still
static proof, not evidence that delay has been injected on a live GPU.

## Verification

```bash
cmake --build build -j
ctest --test-dir build --output-on-failure

cmake -S . -B build-gpu-static -G Ninja \
  -DHBFSIM_ENABLE_CUDA=ON \
  -DHBFSIM_ENABLE_MQSIM=OFF \
  -DCMAKE_CUDA_COMPILER=/usr/local/cuda-12.8/bin/nvcc \
  -DCMAKE_CUDA_ARCHITECTURES=120
cmake --build build-gpu-static -j
ctest --test-dir build-gpu-static --output-on-failure
python3 tests/integration/run_ptxpass_json.py \
  build-gpu-static/src/ptxpass_hbf/ptxpass_hbf sm_120 \
  /usr/local/cuda-12.8/bin/ptxas
```

The design contract and implementation plan are in:

- `docs/superpowers/specs/2026-08-09-hbfsim-hybrid-design.md`
- `docs/superpowers/plans/2026-08-09-hbfsim-hybrid.md`

## Roadmap

1. Calibrate a sampled GPU-local LogP path against MQSim so modeled delay can
   be separated from current request-path overhead.
2. Validate the file-backed cache with public automatic PTX and an over-VRAM
   workload, then add the calibrated GPU-local hybrid model.
3. Run the deterministic CUDA fault matrix and TinyLlama through llama.cpp,
   then extend the existing GPU/CD8P thermal checkpoint to the calibrated path.
