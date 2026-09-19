# HBFSim: simulating High-Bandwidth Flash while the workload runs on a real GPU

[![License: Apache-2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](https://opensource.org/licenses/Apache-2.0)
[![CI](https://github.com/SlugLab/HBFSim/actions/workflows/ci.yml/badge.svg)](https://github.com/SlugLab/HBFSim/actions/workflows/ci.yml)
[![arXiv:2609.09800](https://img.shields.io/badge/arXiv-2609.09800-b31b1b.svg)](https://arxiv.org/abs/2609.09800)

**English** | [中文](README.zh-CN.md)

HBFSim is an evaluation platform for High-Bandwidth Flash (**HBF**), a memory tier that
stacks NAND flash inside the accelerator package, one tier below high-bandwidth memory. HBF
parts cannot be bought today: the first technical specification was published by SanDisk and
SK hynix through the Open Compute Project on August 3, 2026, and the first inference devices
are expected to sample in early 2027. HBFSim lets an application execute normally on a real
GPU and applies HBF timing, capacity, and thermal effects to that application during
execution, instead of recording an access sequence and replaying the recorded sequence
afterwards.

🚀 [Quick start](#quick-start) \
⚙️ [How it works](#how-it-works) \
📊 [What HBFSim has measured](#what-hbfsim-has-measured) \
📄 [Paper on arXiv](https://arxiv.org/abs/2609.09800) \
📝 [Open work in TODO.md](TODO.md)

## The question HBFSim answers

Serving a large language model is limited by memory capacity, and a designer choosing how
much HBF capacity to buy, or which tensors to place in HBF, cannot wait for silicon. Three
existing methods do not settle that decision:

- a storage simulator that replays a recorded access sequence never executes the workload;
- a GPU simulator does not run the real compute kernels; and
- a cycle-accurate simulator cannot finish one large language model inference run.

HBFSim applies HBF timing, capacity, and thermal effects to a real inference workload while
that workload executes on a real GPU. Timing comes from measurements of a real device rather
than from a parameter sheet, and junction temperature sets both the rate HBF sustains and
the retention deadline that forces refresh writes.

## Quick start

HBFSim needs native Linux, CMake 3.25 or newer, Ninja, a compiler with C++20 support,
OpenSSL, and Python 3. The CUDA components need CUDA 12.8 or newer. The media simulator and
the benchmark of the media simulator build without CUDA, which is the configuration below.

```bash
git clone https://github.com/SlugLab/HBFSim.git
cd HBFSim

HBFSIM_ENABLE_CUDA=OFF HBFSIM_ENABLE_MQSIM=ON ./scripts/bootstrap.sh
cmake --build build -j"$(nproc)"
ctest --test-dir build --output-on-failure
```

Configure succeeds, all 152 build targets build, and 31 of the 34 tests pass on a machine
without CUDA. [TODO.md](TODO.md) records the three tests that do not pass and the reason for
each of those three.

<div align="center">
  <img src="docs/assets/hbfsim-architecture.png" alt="HBFSim architecture: a host setup and registry, a real GPU running vLLM through a device ABI, a high-bandwidth memory tier with a native path and a frame cache, a host capacity service with a backing file and a prefetcher, and the modeled HBF behaviour" width="900">
  <p><em>What HBFSim does, read from left to right. The host setup and registry rewrites a module with the PTX pass, then records which address ranges are registered as HBF. A real GPU runs vLLM on Qwen3-30B through a device ABI. Inside the high-bandwidth memory tier, an access to an unregistered address stays on the native path, while an access to a registered address is served from a frame cache. The host capacity service supplies exact page bytes from a backing file, with a prefetcher loading the following pages. On the right, HBFSim computes the modeled HBF behaviour: an online MQSim reference model produces timing; junction temperature sets both the rate HBF sustains and the retention deadline; an approaching deadline forces refresh work, which is a read followed by a rewrite; a service policy sets rate and admission; the evidence output records bytes and wear.</em></p>
</div>

## How it works

Three mechanisms carry the design.

1. **Explicitly registered address ranges define intent.** Only an address the application
   or the runtime has registered is treated as HBF. An ordinary pointer into high-bandwidth
   memory stays on the original fast path and is left alone.

2. **Rewriting PTX provides visibility.** An interception path derived from bpftime and eGPU
   automatically rewrites the supported global load and store instructions, so no CUDA
   kernel has to be edited by hand. PTX is the intermediate code the compiler of NVIDIA
   emits. A coverage gate rejects a kernel launch outright once an HBF pointer reaches code
   whose behaviour cannot be proven safe, rather than letting the launch through quietly.
   The build checks that the resulting module is self-contained and assembles the module
   with CUDA 12.8 `ptxas`; this is still static proof, not evidence that delay has been
   injected on a live GPU.

3. **A detailed path and a fast path work together.** The online, media-only MQSim path is
   the detailed reference model. A calibrated GPU-local model carries the common path, and
   sampled requests keep the GPU-local model anchored to MQSim. MQSim is used as a
   flash-media model, not as an SSD host-stack model.

HBFSim reports four kinds of time separately: modeled device time, host service time,
wall-clock time, and the overhead of HBFSim itself. That separation matters, because HBFSim
can be functionally correct while the software overhead of HBFSim is larger than the device
delay HBFSim is modeling.

## Two modes

| Mode | What changes | Question answered |
|---|---|---|
| **Timing-only** | Data stays in ordinary GPU memory, and HBFSim injects modeled delay for registered accesses only. | How sensitive is this workload to HBF latency, bandwidth, and contention? |
| **Capacity** | Registered data is backed by a file and staged through a bounded page cache held in GPU memory. | Can this workload still run when the working set is larger than GPU memory, and what cache behaviour results? |

Both modes use the same explicitly registered ranges, the same PTX coverage rules, the same
named HBF profiles, and the same reporting model. In capacity mode, every registered file
range in one context shares one bounded high-bandwidth memory (**HBM**) page cache: a hit
resolves to a resident HBM frame, a miss loads the backing page and contributes one modeled
media read, and a dirty eviction contributes a modeled media program before the bytes of
that page return to the backing file.

## What HBFSim has measured

- **Six calibration breakpoints match exactly.** The measured P50 latency and the modeled
  value agree point by point at 1, 4, 16, 64, 256, and 512 contiguous 4 KiB pages, on an
  NVIDIA RTX PRO 6000 Blackwell Server Edition with driver 595.84. This is a deterministic
  calibration check, not a cross-validation: all six points took part in the fit, and none
  of the six was held out. Source: [vmem tuning
  proof](docs/proofs/2026-08-11-cd8p-vmem-tuning.md).

- **The fast path is 20.8x faster than the detailed reference path.** On the same
  deterministic Qwen3-30B case, the reference path took 44.469 s and the fast path took
  2.014352 s, with both runs generating identical token identifiers. The ratio of 20.8x
  holds between two wall-clock times of HBFSim, and is not a prediction of HBF hardware
  performance. Source: [hybrid completion proof](docs/proofs/2026-08-11-hybrid-complete.md).

- **Delay injected into an unmodified vLLM leaves the output unchanged, token by token.**
  With vLLM 0.15.1 serving Qwen3-30B, 24 of 2,304 `fused_moe_kernel` launches were modeled
  launches, and the token identifiers matched the baseline. Only the first 16,384 bytes of
  one tensor were registered, out of the 61,064,245,248 bytes of that tensor. Source: [exact
  live delay proof](docs/proofs/2026-08-11-vllm-exact-live-delay.md).

- **A 110 GiB logical range ran on a 97,887 MiB GPU.** A 110 GiB logical address range with
  a 2 GiB HBM cache completed all 128 accesses, produced checksum `14245581564465502923`,
  which is the checksum the baseline produced, and admitted zero unsafe launches. This is a
  sparse logical-capacity proof, and makes no claim that 110 GiB was physically read.
  Source: [hybrid completion proof](docs/proofs/2026-08-11-hybrid-complete.md).

- **Temperature changes the behaviour of one GPU.** The same BF16 8192x8192 matrix multiply
  reached 379.117 TFLOP/s in a cold exclusive run that went from 35 to 70 degrees C, against
  348.427 TFLOP/s in a hot exclusive run that went from 61 to 85 degrees C, a difference of
  -8.10%. Source: [thermal proof](docs/proofs/2026-08-10-live-gpu-cd8p-thermal.md).

- **The timing model is calibrated from a real device.** The calibration source is a Dell DC
  NVMe CD8P E3.S 1.92TB attached over PCIe 5.0 32 GT/s x4. The Dell CD8P is an ordinary PCIe
  NVMe endpoint, not a CXL endpoint.

Builds, CPU tests, MQSim regressions, and successful PTX assembly are not live GPU proof.

## HBFSim next to the alternatives

Each alternative below is good at something HBFSim does not attempt. A storage simulator
replaying a recorded access sequence is cheap and repeatable, and needs no accelerator. A
GPU simulator exposes microarchitectural detail HBFSim never observes. A cycle-accurate
simulator is the reference for correctness on a small kernel. A vendor parameter sheet is
the only description available for a part nobody outside the vendor has measured.

| Method | Executes the real workload | Runs on real hardware | Models the medium | Models temperature |
|---|:---:|:---:|:---:|:---:|
| Storage simulator replaying a recorded access sequence | ✗ | ✗ | ✓ | ✗ |
| GPU simulator that does not run the real compute kernels | ✗ | ✗ | ✗ | ✗ |
| Cycle-accurate simulator | ✓ | ✗ | ✓ | ✗ |
| Vendor parameter sheet | ✗ | ✗ | ✗ | ✗ |
| HBFSim | ✓ | ✓ | ✓ | ✓ |

The rows name methods rather than specific products, so one particular tool may add one of
the four capabilities. A cycle-accurate simulator does execute the workload, but cannot
finish one large language model inference run, so the check mark in the first column does
not settle that row.

## Named HBF profiles and build options

| Profile | Page | Read | Program | Channels | Queue depth | Aggregate cap |
|---|---:|---:|---:|---:|---:|---:|
| `conservative` | 16 KiB | 20 us | 200 us | 16 | 64 | 128 GB/s |
| `nominal` | 16 KiB | 10 us | 100 us | 32 | 128 | 512 GB/s |
| `aggressive` | 16 KiB | 5 us | 50 us | 64 | 256 | 1 TB/s |

A fourth profile, `cd8p-vmem-p50`, is not synthetic: the values of `cd8p-vmem-p50` are
calibrated from the measured latency curve of the Dell CD8P, and that curve measures a
complete software path, software overhead included. The three profiles in the table are
stated assumptions for design-space exploration; the Open Compute Project HBF specification
being available does not establish that the three profiles are calibrated to HBF silicon.
All four profiles live in `configs/profiles/`, checked by the typed loader against
`configs/schema/hbf-profile.schema.json`.

| Build option | Default | Purpose |
|---|---|---|
| `HBFSIM_ENABLE_CUDA` | `ON` | Build CUDA instrumentation and runtime components |
| `HBFSIM_ENABLE_MQSIM` | `ON` | Build the MQSim-backed host service |
| `HBFSIM_ENABLE_LLM_TESTS` | `OFF` | Enable llama.cpp and vLLM integration tests |
| `HBFSIM_ENABLE_EVAL_TOOLS` | `OFF` | Build offline evaluation models and replay tools |

## Repository layout

- `src/` — all C++ and CUDA production code, in ten components; the three largest are the
  CUDA interception runtime, the PTX pass, and the host service.
- `include/hbfsim/` — public headers and the contracts that cross the host and device
  boundary.
- `adapters/` — three integration adapters: `llama_cpp/`, `vllm/`, and `vllm_capacity/`.
- `benchmarks/` — measurement drivers: CUDA microbenchmarks, the MQSim media benchmark,
  prefetch, and trace replay.
- `configs/` — JSON fixtures: the named HBF profiles, three parameter sweeps, thermal
  fixtures, two JSON Schemas.
- `scripts/` — the bootstrap entry point, the Python evaluation harness, and the thermal
  calibration pipeline.
- `tests/` — CPU tests, integration tests, GPU tests, and PTX fixtures.
- `tools/` — standalone thermal configuration tooling, Python standard library only.
- `patches/` — the out-of-tree patches applied to the two pinned submodules.
- `third_party/` — the two pinned submodules: bpftime and MQSim.
- `cmake/` — submodule pinning, PTX embedding, and the patched MQSim build.
- `docs/` — design specifications, implementation plans, proof checkpoints, evaluation
  runbooks, reference papers.
- `paper/` — a submodule needed neither to build nor to test HBFSim; `.gitmodules` marks
  `paper/` inactive, and `scripts/bootstrap.sh` initializes only the two build dependencies.

## Documentation

- [`docs/proofs/`](docs/proofs/) — the checkpoint documents that hold every experiment
  number, each with the commands and the boundaries of the claim.
- [`docs/eval/`](docs/eval/) — the evaluation plan, the workload methodology, and the
  reproduction runbooks.
- [`docs/skills/`](docs/skills/) — a reading order plus one document per subsystem, for
  someone new to the code.
- [`docs/reference/`](docs/reference/) — reference notes, including the CUDA architecture
  compatibility audit.
- [`TODO.md`](TODO.md) — the open roadmap. Each item names the evidence that would close it.

## Paper and citation

The paper describing HBFSim is [arXiv:2609.09800](https://arxiv.org/abs/2609.09800).

```bibtex
@article{hu2026hbfsim,
  title   = {HBFSim: Fast and Faithful Simulation of High-Bandwidth Flash Under Real GPU Execution},
  author  = {Hu, Yanpeng and Yang, Yiwei and Zhu, Yuanwu and Zheng, Yusheng and Zhang, Wei and Quinn, Andi},
  journal = {arXiv preprint arXiv:2609.09800},
  year    = {2026}
}
```

`CITATION.cff` carries the same entry, which is what makes GitHub render a "Cite this
repository" button on the repository page.

## Contributing

[`CONTRIBUTING.md`](CONTRIBUTING.md) states what a contribution needs, and the one rule
that matters most here: a number may only enter the repository together with the checkpoint
document that produced it. Bug reports and feature requests go through the templates in
[`.github/ISSUE_TEMPLATE/`](.github/ISSUE_TEMPLATE/), and a pull request follows
[`.github/pull_request_template.md`](.github/pull_request_template.md). Build and test with
the quick start above before opening a pull request, and say which of the four kinds of time
a new measurement refers to. Participation is governed by the [Code of
Conduct](CODE_OF_CONDUCT.md). Report a vulnerability through [`SECURITY.md`](SECURITY.md)
rather than in a public issue.

## License

HBFSim is released under the Apache License 2.0. The full text is in [`LICENSE`](LICENSE).

## Acknowledgements

HBFSim builds on two projects, kept as pinned submodules. bpftime, released under the MIT
license, supplies the interception path the PTX rewriting is derived from. MQSim, released
under an MIT-style license by the SAFARI Research Group at ETH Zurich, supplies the flash
media model HBFSim drives online through an out-of-tree patch.

