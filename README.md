# HBFSim

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

## Two complementary modes

| Mode | What changes | Primary question |
|---|---|---|
| **Timing-only** | Data stays in normal GPU memory; HBFSim injects modeled delay for registered accesses. | How sensitive is this workload to HBF latency, bandwidth, and contention? |
| **Capacity** | Registered data is backed by a file and staged through an HBM page cache. | Can a workload run with a working set larger than available VRAM, and what cache behavior results? |

Both modes use the same explicit ranges, PTX coverage rules, named HBF profiles,
and reporting model. Timing-only mode isolates delay from paging. Capacity mode
adds page residency, eviction, and backing I/O.

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

The `hybrid` branch is under active development. It contains the simulator
foundation and a statically verified GPU interception path, not yet a complete
live GPU, capacity, or LLM proof.

| Component | Status |
|---|---|
| Pinned bpftime and MQSim dependencies | Implemented |
| Named synthetic HBF profiles | Implemented and validated |
| Request/completion protocol and page state machine | Implemented and tested |
| Incremental media-only MQSim interface and trace equivalence | Implemented and tested |
| Reproducible MQSim media benchmark | Implemented and tested |
| PTX rewriting for supported global loads/stores | Implemented; static PTX checks pass |
| bpftime pass ABI and fail-closed CUDA launch gate | Implemented; static/Release checks pass |
| Live bpftime + GPU interception proof | Blocked on local bpftime/toolchain and GPU recovery |
| Explicit public range API and host service | In progress |
| Live delay injection and timing-only GPU proof | Planned |
| File-backed capacity mode and hybrid fast model | Planned |
| CUDA fault matrix, llama.cpp, and vLLM proof runs | Planned |

Builds, CPU tests, MQSim regressions, and successful PTX assembly are not live
GPU proof. The repository does not yet claim working delay injection, over-VRAM
capacity emulation, llama.cpp, or vLLM execution.

## Requirements

- Native Linux
- Git with submodule support
- CMake 3.25 or newer
- Ninja
- A C++20 compiler
- Python 3
- CUDA 12.8 at `/usr/local/cuda-12.8` for CUDA-enabled bootstrap and `sm_120`
  PTX validation

The current media simulator and its benchmark can be built without a GPU.

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
| `HBFSIM_ENABLE_LLM_TESTS` | `OFF` | Enable future llama.cpp and vLLM integration tests |

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
  build/src/ptxpass_hbf/ptxpass_hbf
```

When CUDA 12.8 is installed, the check assembles the rewritten PTX for
`sm_120` with `ptxas`. The initial pass recognizes selected scalar/vector,
predicated, offset, and cache-qualified global loads and stores. Atomics,
generic-space operations, texture/surface operations, malformed addresses, and
inline SASS remain outside the supported HBF path and must fail closed once the
runtime coverage gate is connected.

## Verification

```bash
cmake --build build -j
ctest --test-dir build --output-on-failure
python3 tests/integration/run_ptxpass_json.py \
  build/src/ptxpass_hbf/ptxpass_hbf
```

The design contract and implementation plan are in:

- `docs/superpowers/specs/2026-08-09-hbfsim-hybrid-design.md`
- `docs/superpowers/plans/2026-08-09-hbfsim-hybrid.md`

## Roadmap

1. Connect the PTX pass to the pinned bpftime CUDA module path and enforce the
   coverage gate.
2. Add the public context API, explicit registered ranges, shared rings, and
   host-service lifecycle.
3. Inject live GPU delay and validate modeled time separately from emulator
   overhead.
4. Add file-backed capacity mode and the calibrated GPU-local hybrid model.
5. Run the deterministic CUDA/fault matrix, then TinyLlama through llama.cpp
   and vLLM with bit-exact output gates.
