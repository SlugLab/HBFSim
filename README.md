# HBFSim

HBFSim is a hybrid High-Bandwidth Flash (HBF) workload emulator for CUDA
applications. It combines automatic PTX memory-operation rewriting from the
bpftime/eGPU lineage with an online, media-only MQSim timing backend. The target
runtime supports explicitly registered HBF ranges, live delay injection, a
GPU-local fast model, and file-backed capacity beyond VRAM.

The included HBF profiles are synthetic design-space points. They are not
vendor specifications or a claim that HBF is a finalized commercial standard.

## Project status

The `hybrid` branch is under active development. The repository currently
contains a working simulator foundation, not yet the complete live GPU system.

| Component | Status |
|---|---|
| Pinned bpftime and MQSim dependencies | Implemented |
| Validated named HBF profiles | Implemented |
| Fixed-layout request/completion protocol and page state machine | Implemented |
| Incremental media-only MQSim HBF interface | Implemented and regression-tested |
| MQSim trace-equivalence path | Implemented and regression-tested |
| Automatic rewriting of supported PTX global loads/stores | Initial pass implemented |
| Reproducible MQSim media benchmark | Implemented |
| bpftime CUDA module interception and fail-closed launch gate | In progress |
| Explicit runtime range registration and live GPU delay injection | Planned |
| File-backed capacity mode and hybrid GPU timing model | Planned |
| CUDA fault matrix, llama.cpp, and vLLM proof runs | Planned |

CPU/MQSim tests or successful PTX assembly do not constitute proof of live GPU
delay, over-VRAM capacity emulation, llama.cpp, or vLLM support.

## Architecture

```text
CUDA workload: microbench / llama.cpp / vLLM
                         |
                         v
          bpftime CUDA interception and coverage gate
                         |
                         v
               HBFSim automatic PTX pass
               - supported ld.global forms
               - supported st.global forms
               - preserve predicates
               - reject unsupported HBF use
                         |
                         v
          explicit HBF range lookup on the GPU
               /                         \
              v                           v
       HBM cache hit              shared request ring
                                            |
                                            v
                                    HBF host service
                                   /                \
                                  v                  v
                         online MQSim model    backing-page I/O
```

The online MQSim adapter bypasses NVMe, PCIe, SATA, and host-driver events. It
retains MQSim's event engine, flash mapping, transaction scheduling, NAND
timing, contention, and channel behavior. This makes it a media reference for
the intended HBF path rather than an SSD host-stack benchmark.

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
