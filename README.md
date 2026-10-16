# HBFSim

**Run real workloads on a real GPU, and simulate only HBF.**

[![License: Apache-2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)
[![CI](https://github.com/SlugLab/HBFSim/actions/workflows/ci.yml/badge.svg)](https://github.com/SlugLab/HBFSim/actions/workflows/ci.yml)
[![arXiv:2609.09800](https://img.shields.io/badge/arXiv-2609.09800-b31b1b.svg)](https://arxiv.org/abs/2609.09800)

**English** | [简体中文](README.zh-CN.md)

HBFSim is an evaluation platform for High-Bandwidth Flash (**HBF**), a memory tier that
stacks NAND flash inside the accelerator package, one tier below high-bandwidth memory. HBF
parts cannot be bought today: the first technical specification was published by SanDisk and
SK hynix through the Open Compute Project on August 3, 2026, and the first inference devices
are expected to sample in early 2027. HBFSim lets an application execute normally on a real
GPU and applies HBF timing, capacity, and thermal effects to that application during
execution, instead of recording an access sequence and replaying the recorded sequence
afterwards.

🚀 [Quick start](#quickstart) \
⚙️ [How it works](#mechanisms) \
📊 [What HBFSim has measured](#examples) \
📄 [Paper on arXiv](https://arxiv.org/abs/2609.09800) \
📝 [Open work in TODO.md](TODO.md)

<a id="question"></a>
## The question HBFSim answers

Serving a large language model is limited by memory capacity, and a designer choosing how
much HBF capacity to buy, or which tensors to place in HBF, cannot wait for silicon. Three
existing methods do not settle that decision:

- a storage simulator that replays a recorded access sequence never executes the workload;
- a GPU simulator does not run the real compute kernels; and
- a cycle-accurate simulator cannot finish one large language model inference run in an acceptable time, so the simulator can only simulate one layer and extrapolate.

The core judgment of the [paper on arXiv](https://arxiv.org/abs/2609.09800) is that evaluating HBF does not require simulating the GPU; evaluating HBF requires modeling only the program-visible effects of HBF. HBFSim therefore applies HBF timing, capacity, and thermal effects to a real inference workload while that workload executes on a real GPU; HBFSim neither re-simulates the entire GPU in software nor treats a prerecorded access sequence as the workload itself. HBFSim rewrites the PTX (the intermediate code that the NVIDIA compiler generates) of application kernels so that only accesses within address ranges registered as HBF go into the HBF model; computation still runs on the real GPU, and ordinary GPU memory accesses keep their native path.

Timing parameters come from a profile, which is a JSON configuration file under `configs/profiles/`. Only the `cd8p-vmem-p50` profile is calibrated from measurements of a real device: the measurements cover the complete software path on a Dell CD8P NVMe SSD, not HBF. The `conservative`, `nominal`, and `aggressive` profiles are synthetic and serve hypothetical sweeps. In the optional EQ3 thermal and reliability module, junction temperature sets both the rate HBF sustains and the retention deadline that forces refresh writes.

HBFSim also provides:

- Capacity mode, which backs logical data with a file so that logical capacity beyond physical GPU memory can run; sparse accesses to a 110 GiB logical address range have been validated on a GPU with 97,887 MiB of memory;
- an optional UCIe multi-stack backend, currently a read-only interface;
- optional SASS→PTX recovery, which lets precompiled kernels that lack PTX enter the same instrumentation flow;
- adapters for vLLM and llama.cpp.

<a id="quickstart"></a>
## Quick start

For the recorded research setup, see the [RTX 5090 build environment and full build
commands](docs/build/rtx-5090-environment.md). For a build or runtime problem, use the
[troubleshooting guide and report forms](docs/troubleshooting.md).

SASS recovery is experimental and is **off by default** in the user entry point.
The [SASS runtime tools](tools/sass_runtime/) explain how to opt in and how to
report the layers whose bindings actually ran. Native PTX instrumentation is
independent of this option.

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


### See one run

The media benchmark runs without a GPU. It drives the online MQSim reference model with
deterministic sequential requests and emits one JSON document:

```console
$ ./build/hbf_mqsim_bench --profile configs/profiles/nominal.json \
    --requests 1024 --bytes 16384 --operation read --arrival-gap-ns 0
{
  "effective_profile": {
    "aggregate_bandwidth_bytes_per_s": 512000000000,
    "blocks_per_plane": 16,
    "capacity_bytes": 68719476736,
    "channels": 32,
    "hbm_cache_bytes": 67108864,
    "nand_technology": "SLC",
    "plane_allocation_scheme": "CWDP",
    "program_latency_ns": 100000,
    "read_latency_ns": 10000
  },
  "engine": "mqsim-hbf-media-only",
  "latency_ns": { "average": 170115, "p50": 164960, "p99": 329920 },
  "modeled_bandwidth_bytes_per_s": 50852376333.65665,
  "requests": { "completed": 1024, "submitted": 1024 },
  "simulator_requests_per_s": 6557.51045857279,
  "timing_ns": { "modeled": 329920, "wall": 156156823 }
}
```

Read the last two fields together. `timing_ns.modeled` is the device time HBFSim computed;
`timing_ns.wall` is how long the computation itself took. The two are reported separately
everywhere in HBFSim, because an emulator can be functionally correct while its own overhead
exceeds the delay it models.

<details>
<summary>Test scope and CPU build troubleshooting</summary>

CI runs the tests with the additional exclusion list `-E 'vmem_tuning|run_with_bpftime|context_lifecycle'`, so CI does not run all tests. `vmem_tuning` and `run_with_bpftime` involve a specific environment or external inputs, and `context_lifecycle` corresponds to a problem that has not been fixed yet; for details, see the [CI configuration](.github/workflows/ci.yml) and [TODO](TODO.md#validation). When the external calibration CSV is missing, `vmem_tuning` skips the byte-for-byte reproduction subtest that depends on the CSV.

If the libbpf development package and clang are installed, a build without CUDA may report `CMake can not determine linker language for target: hbfsim_llama_probe_module`. Call CMake directly as described in the [environment runbook](docs/eval/runbooks/environment_setup.md) and add `-DCMAKE_DISABLE_FIND_PACKAGE_PkgConfig=ON`; `bootstrap.sh` does not forward this option.

When `--capacity-bytes` is not passed, the media benchmark limits capacity to 16 blocks per plane. The run shown in "See one run" above had an effective capacity of 64 GiB and a cache of 64 MiB, not the full configured values in `nominal.json`. Save `effective_profile` when comparing experiments.

</details>

<a id="hbf-intro"></a>
## Introduction to HBF

HBF stacks NAND flash inside the accelerator package, below HBM and above SSDs. According to Chapter 3 of the [v0.7.0 specification](docs/HBF_OCP/ocp2026-hbf-architecture-specification-v0-7-0.pdf), HBF has 8 to 16 times the capacity of HBM with the same read bandwidth as HBM; Section 4.1 and Table 4 of the specification give **512 GiB** as an example capacity per stack and **3.072 TB/s** as the user bandwidth per stack at the highest speed grade. HBF cannot be bought yet: SanDisk plans to ship samples of its first HBF inference products in 2027 (according to [StorageReview's report on the SanDisk investor day in August 2026](https://www.storagereview.com/news/sandisk-tapes-out-its-first-hbf-memory-die-targets-2027-for-inference-product-samples)), while the timeline SK hynix showed at FMS 2026 places HBF samples in early 2028 (according to an article by Forbes contributor Thomas Coughlin, [reprinted by Yahoo](https://ca.news.yahoo.com/high-bandwidth-flash-advances-2026-040711224.html)).

Temperature slows HBF down: the specification defines thermal throttling, in which the device lowers its power once the junction temperature reaches a threshold, so reads and writes slow down and, in severe cases, operations pause. The specification also guarantees 24 hours of data retention at 85 °C while the device is powered on, and refresh is typically needed every 24 to 48 hours (Chapter 9 and Section 11.5). HBF shares the package with the GPU and HBM, so heat from the GPU raises the temperature of HBF. The thermal state of the GPU also changes performance: we measured the same BF16 8192x8192 matrix multiply running alone on the same GPU at 379.117 TFLOP/s with the GPU cold and at 348.427 TFLOP/s with the GPU hot, 8.10% lower; the drop comes from the GPU compute chip lowering its own clock, not from the HBF rate ([experiment record](docs/proofs/2026-08-10-live-gpu-cd8p-thermal.md)).

Industry evaluations of HBF do not agree, and each draws its conclusions from its own testing or models: SanDisk's HBF fact sheet from July 2025 states that its numbers come from internal testing and simulation; the [H³ paper](https://doi.org/10.1109/LCA.2026.3660969) by SK hynix researchers uses an internal simulator; a HotInfra '26 workshop [paper](https://hotinfra.org/2026/papers/hotinfra26-final83.pdf) by three Micron researchers uses a roofline analytical model, which the authors say does not necessarily represent Micron's official position; Oxmiq Labs used rack-scale simulation at Hot Chips 2026 ([ServeTheHome report](https://www.servethehome.com/oxmiq-labs-hbf-in-ai-compute-at-hot-chips-2026/)). Design decisions such as how much capacity to give HBM versus HBF, or which tensors to place in HBF, cannot wait for the devices to reach the market. HBFSim is used to evaluate the following on real LLM workloads:

- the capacity split between HBM and HBF;
- tensor placement, for read-mostly data such as weights, MoE expert weights, and the KV cache of shared prefixes;
- HBF media parameters, including read and write latency, bandwidth, and the number of stacks;
- slowdown caused by temperature.

<a id="architecture"></a>
## Overall architecture

<div align="center">
  <img src="docs/assets/hbfsim-architecture.png" alt="HBFSim architecture: host-side registration and PTX instrumentation, real GPU execution, GPU memory and the file-backed capacity tier, and online HBF timing and thermal models" width="900">
</div>

**The host side determines which accesses need to be modeled, the real GPU executes the computation, and the HBF model provides media service time and capacity behavior.** In the figure, solid boxes denote default runtime components and dashed boxes denote optional or experimental components; for the specific conditions to enable each component, see [Features and configuration](#configuration).

<a id="methodology"></a>
## Why traditional evaluation methods cannot be reused

**Evaluating HBF cannot mean only swapping in a new set of memory parameters for an existing GPU model, nor substituting a fixed trace for real execution after the capacity configuration changes.** The question we need to answer is how the same inference system runs under a different HBM–HBF configuration, not only how long the same sequence of requests takes in another media model.

| Problem | Why it is not enough |
|---|---|
| Fixed trace | **Replaying an old access sequence with new parameters is not the same as executing a system that uses the new capacity configuration.** |
| Full-GPU cycle-level simulation | The variable under study is HBF; re-simulating the entire GPU should not be a prerequisite for every experiment. |
| CPU / NVMe interception | The interception point is not on the in-package GPU memory-access path, so the wait time measured at the interception point cannot be carried over directly. |
| Fixed latency and peak bandwidth | Fixed latency and peak bandwidth alone cannot account for queueing, stack load, temperature, and refresh feedback during a run. |

### Fixed trace replay cannot generate execution behavior under a new configuration by itself

A change in HBM capacity can change resident tensors, KV allocation, data migration, and kernel invocations. A trace recorded under the original configuration does not automatically gain the instructions that the new configuration needs to execute, nor does it automatically drop migrations that are no longer needed. **Replaying the same trace with new parameters is not the same as rerunning the system with the new capacity configuration.** Re-collecting traces, extending traces, or adding a feedback mechanism requires separately solving how the changes caused by the new configuration are generated. [Analysis in the paper](https://arxiv.org/abs/2609.09800)

### Full-GPU cycle-level simulation turns computation into an evaluation bottleneck too

If the variables under study are HBF capacity, service time, and data placement, full GPU microarchitecture simulation should not be a prerequisite for every experiment. The execution cost of cycle-level simulation limits production-scale models, long runs, and multidimensional parameter sweeps; extracting one Transformer block and extrapolating also cannot directly replace end-to-end execution after the capacity changes. [Accel-Sim 2.0](https://arxiv.org/abs/2608.22602)

### CPU or NVMe interception points are not on the HBF access path

Host page tables, CPU performance counters, and NVMe queues are valid interception locations in their respective systems, but **in-package HBF loads/stores issued by the GPU do not pass through these locations**. Moving the wait time of a host-side I/O directly onto a GPU memory access does not mean that HBF has been simulated. HBFSim places the hook point for supported accesses inside the GPU kernel. [PTX instrumentation design](docs/skills/02-ptx-instrumentation.md)

<div align="center">
  <img src="docs/assets/hbfsim-comparison.png" alt="Three evaluation routes: fixed trace replay computes new timing for the old access sequence; full-GPU cycle-level simulation re-simulates the whole GPU for every experiment; HBFSim runs the workload on a real GPU and models HBF online only for the accesses that actually happen" width="900">
</div>

The choice HBFSim makes is: **keep the real computation and the accesses as they happen, and model only the program-visible effects of HBF.** HBFSim does not replace the whole system with a simpler formula; HBFSim limits the part that must be simulated to HBF and avoids reimplementing GPU execution.

<a id="mechanisms"></a>
## Core mechanisms

### 1. Registration and instrumentation: routing GPU accesses into the HBF model

An application or adapter specifies which data belongs to HBF through explicit address registration. Unregistered addresses keep native memory-access semantics and receive no injected HBF latency; registration must finish before the first launch of the relevant kernel.

HBFSim uses an interception path derived from **bpftime / eGPU** to automatically rewrite supported PTX global loads/stores, with no need to modify CUDA kernels by hand one by one. PTX is the intermediate code generated by the NVIDIA compiler, from which the compiler backend then still generates GPU machine instructions. For precompiled kernels that lack PTX, the optional **SASS→PTX recovery path** hands selected kernels back to the same instrumentation flow.

Before every kernel launch, the **coverage gate** checks whether that execution is within the modelable scope. The coverage gate does not only record "the address is registered"; the coverage gate also distinguishes whether an access actually went through the instrumentation path.

| Access and code situation | Handling |
|---|---|
| Unregistered ordinary GPU memory address | Keeps the native memory-access path |
| Registered address, and the access can be rewritten safely | Enters the corresponding HBF modeling path |
| A Capacity address may reach code that cannot be proven safe | Rejects the kernel launch |
| A Timing address reaches code that cannot be proven safe | Allowed to execute by default, but recorded as `opaque_unmodeled_timing` and not counted toward modeled coverage; the launch is rejected under the `strict` policy |

The supported basic accesses include scalar and vector global loads/stores; uncovered accesses such as atomics, `cp.async`, and texture accesses are reported. For details, see [PTX instrumentation](docs/skills/02-ptx-instrumentation.md), [CUDA memory semantics](docs/skills/03-cuda-memory-semantics.md), and [SASS recovery](tools/sass_runtime/README.md).

### 2. GPU execution and waiting: hideable latency must not all become stalls

After a real GPU issues a load, the GPU can keep executing instructions that do not depend on the load's result, and waits only when a consumer needs the data. If the simulator synchronously waits for the full simulated latency at the issue point of the load, the wait prevents the independent instructions after the load from executing early and changes the original overlap between computation and memory access.

The **experimental dependency-point waiting path** of HBFSim separates issue from consumption: at issue, the path records a device future; at the first instruction that uses the result, the path waits for any simulated service that has not yet finished.

**The default build still uses issue-point waiting; dependency-point waiting is an experimental path that must be enabled explicitly.** Dependency-point waiting requires `HBFSIM_ENABLE_TIMING_FUTURES=ON`, CUDA 13, and `sm_120`, and currently covers loads in Timing ranges and the `fast` scalar model. On an RTX 5090, 6 wait-semantics cases have passed; for the admission conditions of the matching profiles and the limits of the existing configurations, see the [implementation and validation notes](docs/52-main-gpu-thermal-integration/README.md) and the [defect review](docs/51-defect-review-and-fixes.md).

### 3. GPU memory and capacity service: studying "slower memory" and "larger memory" separately

| Mode | Where the data actually lives | What the mode is suited to study |
|---|---|---|
| **Timing-only** | Data stays in real GPU memory, and simulated latency is injected for registered accesses | Sensitivity to latency, bandwidth, and contention |
| **Capacity** | A file backs the logical data, and a bounded page cache in GPU memory holds resident pages | Logical capacity beyond physical GPU memory, paging, and cache behavior |

The two modes share address registration, coverage rules, and profiles. In Capacity mode, file mappings in the same context share one bounded page cache: a hit accesses a resident frame; on a miss, the host capacity service reads the exact page bytes from the backing file and counts a simulated media read; evicting a dirty page involves a write-back to the backing file and a simulated media program.

> **The Capacity eviction path still has known correctness problems:** a frame may be reused before the GPU finishes consuming the frame, and rewriting dirty pages in place also violates the sequential programming constraint of HBF. Experiments that involve eviction or writes should first check the [defect description](docs/51-defect-review-and-fixes.md) and the [fix plan](TODO.md#correctness-and-fidelity). The published 110 GiB sparse-access validation did not go through the eviction path.

Runtime prefetching is separate from the current default path: the repository has an offline prefetch-sensitivity model, built through `HBFSIM_ENABLE_EVAL_TOOLS`; the runtime prefetching described in the paper is not yet in `main`. For details of capacity translation, see the [Capacity design](docs/skills/07-capacity-address-translation.md).

### 4. Timing backends: the detailed reference path and the GPU-local fast path

HBFSim uses **MQSim as a flash media model, not as a model of the SSD host software stack**, and provides three timing modes.

| Timing mode | How the mode works |
|---|---|
| `reference` | Requests go to online MQSim, which uses a detailed media service model |
| `fast` | Computes timing locally on the GPU from the selected profile, avoiding a host round trip per request |
| `hybrid` | Combines the fast path and the reference path; after a configurable warm-up, sends part of the requests to MQSim |

**Capacity mode always uses the reference path.** The parameters of `fast` are determined by the profile: synthetic profiles are for hypothetical sweeps, whereas `cd8p-vmem-p50` comes from a calibration curve of a measured software path, and synthetic profiles must not be confused with `cd8p-vmem-p50`. [Backend implementation and experiments](docs/proofs/2026-08-11-hybrid-complete.md)

The optional **UCIe multi-stack backend** further models AXI read transfers, page striping across stacks, request coalescing, and independent service limits, with one MQSim worker per stack. The UCIe backend is enabled with `HBFSIM_ENABLE_UCIE=ON`, is currently a read-only interface, and has completed CPU-side validation. [UCIe documentation](docs/ucie/README.md) · [v0.1.1 release notes](docs/releases/v0.1.1.md)

### 5. Thermal behavior and reliability: feeding temperature back into media service

The optional **EQ3 module** connects junction temperature, service policy, data retention deadlines, and refresh. Temperature is not just a column at the end of a report: temperature affects the sustainable service rate and also shortens data retention deadlines; the refresh performed to retain data in turn adds read/write traffic and maintenance overhead.

EQ3 records service and maintenance statistics and increments a wear count on each successful erase; there is currently no lifetime model for outputting MTBF. For the module design, how to enable the module, and validation records, see the [EQ3 thermal documentation](docs/eq3_thermal/README.md) and the [system thermal experiments](experiments/eq3_system_thermal/README.md).

### 6. Coverage and reporting: separating device behavior from simulator overhead

When interpreting results, distinguish **modeled device time, host service time, wall-clock time, and the overhead of HBFSim itself**. Modeled device time describes the service of the configured HBF model; wall-clock time may also include instrumentation, request round trips, and host processing, and cannot be taken directly as inference latency on future HBF hardware.

The media benchmark and the CUDA microbenchmarks already output modeled time and wall-clock time separately, and the coverage log records which launches were actually modeled. No report yet splits all four kinds of time in one place, so performance comparisons should be read together with the path used, the profile, and the raw records. [Experiment records](docs/proofs/) · [Evaluation methods](docs/eval/)

<a id="integration"></a>
## Integrating an application

Requires native Linux, CMake ≥ 3.25, Ninja, a compiler with C++20 support, OpenSSL, Python 3, and Git. The GPU components require CUDA ≥ 12.8; for the additional requirements of specific experimental paths, see [Features and configuration](#configuration). For the complete environment setup, see the [RTX 5090 build documentation](docs/build/rtx-5090-environment.md).

### Build the GPU components and connect an application

The following commands use a separate build directory; replace the CUDA path with the actual installation location on your machine.

```bash
HBFSIM_BUILD_DIR="$PWD/build-gpu" \
HBFSIM_ENABLE_CUDA=ON \
HBFSIM_CUDA_ROOT=/usr/local/cuda-12.8 \
  ./scripts/bootstrap.sh

cmake --build build-gpu -j"$(nproc)"
```

An application registers the addresses to be modeled through the [C API](include/hbfsim/api.h), for example registering a span of allocated GPU memory as a Timing range:

```cpp
const hbfsim_range_options range{
    .mode = HBFSIM_RANGE_MODE_TIMING,
    .permissions = HBFSIM_RANGE_READ_WRITE,
    .cache_policy = HBFSIM_CACHE_POLICY_NONE,
    .stream_id = 0,
};
if (hbfsim_register_device(context, data, options.bytes,
                           &range) != HBFSIM_OK) {
    fail("hbfsim_register_device failed");
}
```

The snippet is taken from the [CUDA microbenchmark](benchmarks/cuda/hbf_microbench.cu). **Registering addresses and rewriting kernels are two separate steps: calling the C API alone does not perform instrumentation automatically.** Use `scripts/run_with_bpftime.sh -- command [args ...]` to start an application that has completed registration, and set `HBFSIM_BUILD_DIR` to the build directory in use.

For existing inference frameworks, start from the corresponding adapter: [vLLM Timing](adapters/vllm/README.md), [llama.cpp](adapters/llama_cpp/); [vLLM Capacity](adapters/vllm_capacity/) is an offline capacity trace replay tool and does not load vLLM. Environment requirements and commands for the model and SASS research paths stay in their own documents, to avoid mixing them with the basic build. If you run into problems, see the [troubleshooting notes](docs/troubleshooting.md).

<a id="examples"></a>
## Examples and validation

The public validation of HBFSim covers integration with real inference frameworks, weight registration and consumer binding, logical capacity beyond GPU memory, timing calibration, the fast path, and thermal behavior. **The experiments listed below are representative; they are not all of the project's tests or all of its use cases.** For more run records, see [proofs](docs/proofs/), [OLMoE validation](docs/hbfsim-supported-weights/README.md), and the [reproduction packages](reproduction/).

| Scenario | Representative result | Record |
|---|---|---|
| **Real vLLM inference** | vLLM 0.15.1 serves Qwen3-30B-A3B; modeled latency is injected within the registered region, and the output token IDs match the baseline | [vLLM experiment](docs/proofs/2026-08-11-vllm-exact-live-delay.md) |
| **OLMoE weight integration** | The native PTX path registers 98 supported storages; with SASS recovery, all 147 deduplicated weight storages are registered, and consumer binding and fixed-request validation are completed | [Native PTX](docs/hbfsim-supported-weights/RESULTS.md) · [SASS reproduction package](reproduction/sass-final147-20260926/README.md) |
| **Logical capacity beyond GPU memory** | A 110 GiB logical address range with a 2 GiB GPU page cache; all 128 sparse accesses completed, and the checksum matches the baseline | [Capacity experiment](docs/proofs/2026-08-11-hybrid-complete.md) |
| **Timing calibration** | The six calibration points at 1, 4, 16, 64, 256, and 512 contiguous 4 KiB pages match exactly; the match is a consistency check on fitted points | [Calibration experiment](docs/proofs/2026-08-11-cd8p-vmem-tuning.md) |
| **Simulator fast path** | On the same deterministic Qwen3-30B case, the reference path takes 44.469 s and the fast path takes 2.014352 s; wall-clock time drops to about 1/22, and the outputs match | [Path comparison](docs/proofs/2026-08-11-hybrid-complete.md) |
| **Temperature sensitivity** | The same BF16 matrix multiply reaches 379.117 / 348.427 TFLOP/s in the cold and hot machine states; measured GPU compute throughput drops by 8.10% | [Thermal behavior experiment](docs/proofs/2026-08-10-live-gpu-cd8p-thermal.md) |

Use each result within the scope of its experiment: **the fast-path speedup is a ratio of the wall-clock times of two simulated runs; 110 GiB is a sparse logical-capacity validation; the temperature experiment measures the GPU's own downclocking.** None of the three results is measured performance of an HBF device.

<details>
<summary>Expand experiment configuration, coverage scope, and reproduction notes</summary>

**Experiment hardware.** Except for the OLMoE weight experiment, which ran on an RTX 5090, the other GPU results in the table above come from an NVIDIA RTX PRO 6000 Blackwell Server Edition with driver 595.84. The GDDR7 memory of the RTX PRO 6000 plays the role of the HBM tier of the modeled system in the experiments and is not real HBM. [Environment record](docs/eval/capacity_qwen3_30b_a3b.md)

**vLLM.** The listed Qwen3-30B experiment registers the first 16,384 bytes of `model.layers.0.mlp.experts.w13_weight`. Of 2,304 `fused_moe_kernel` launches, 24 were modeled launches, and 4 Triton kernel variants were bound. The vLLM experiment validates online injection within the specified region and the consistency of the output, and is not equivalent to HBF timing coverage of the whole model. [Record](docs/proofs/2026-08-11-vllm-exact-live-delay.md)

**OLMoE.** The native PTX path registers 98 storages totaling 13,091,213,312 bytes; the remaining 49 storages stay on the native path. The SASS path registers all 147 deduplicated storages totaling 13,838,323,712 bytes, and both fixed short requests return `[[70, 13]]`. Complete registration does not mean that the short requests accessed every byte or every expert, nor does complete registration represent general coverage of arbitrary models and kernels. The full 98-storage experiment has not yet been rerun in full with the rebuilt components, which have been validated on an 8-storage representative case; the final147 reproduction package has not yet completed a clean rebuild or a cross-machine GPU reproduction. [Source rebuild record](docs/hbfsim-supported-weights/REBUILT_SOURCE_VALIDATION.md) · [final147 report](reproduction/sass-final147-20260926/evidence-tree/rebuttal_20260921/sass-lifter-20260924/reports/final147-20260926-v1/REPORT.md)

**Capacity.** The 110 GiB logical range ran on a GPU that reports 97,887 MiB of memory; the checksum of the 128 accesses is `14245581564465502923`, and there were 0 unsafe launches. The capacity experiment did not physically read the entire 110 GiB and did not go through the eviction path. [Record](docs/proofs/2026-08-11-hybrid-complete.md)

**Calibration.** `cd8p-vmem-p50` comes from the complete vmem software path on a Dell CD8P NVMe SSD, including overheads such as page faults and the driver; `cd8p-vmem-p50` is neither an HBF measurement nor raw NAND latency. All six breakpoints took part in the fit; held-out validation and a fast/reference consistency comparison on the same input are still in the [validation plan](TODO.md#validation).

**Thermal behavior.** The -8.10% in the table above comes from a comparison of cold and hot exclusive matrix-multiply runs; another experiment, with a matrix multiply running concurrently with fio, gave -3.72%, and the thermal configuration uses the -3.72% figure. The -8.10% and -3.72% figures correspond to different run conditions and should not substitute for each other. [Related record](docs/proofs/2026-08-11-hybrid-complete.md)

**End-to-end performance.** The evaluation records of the current public `main` have not yet verified complete prefill/decode latency or TPOT, so the run times in the table above cannot be read directly as end-to-end inference predictions on HBF. [Evaluation scope](docs/eval/capacity_qwen3_30b_a3b.md)

</details>

<a id="configuration"></a>
## Features and configuration

The default build provides address registration, supported PTX memory-access rewriting, coverage checks, the two modes Timing / Capacity, and the MQSim reference path plus the GPU-local fast path. The options below control additional components; whether a feature has been validated on a GPU should be judged from the corresponding module documentation.

| Option | Default | Purpose |
|---|---|---|
| `HBFSIM_ENABLE_CUDA` | `ON` | CUDA instrumentation and runtime components |
| `HBFSIM_ENABLE_MQSIM` | `ON` | MQSim host service |
| `HBFSIM_ENABLE_UCIE` | `OFF` | UCIe multi-stack read-only backend; the CPU path is currently validated |
| `HBFSIM_ENABLE_LLM_TESTS` | `OFF` | llama.cpp and vLLM integration tests |
| `HBFSIM_ENABLE_EVAL_TOOLS` | `OFF` | Offline evaluation, prefetch-sensitivity model, and replay tools |
| `HBFSIM_ENABLE_TIMING_FUTURES` | `OFF` | Experimental dependency-point waiting path; requires CUDA 13 and `sm_120` |

`bootstrap.sh` directly forwards three of the options above: CUDA, MQSim, and LLM tests; the other options are configured through `cmake -D...`. There are also test and diagnostic switches; see [CMakeLists.txt](CMakeLists.txt).

**SASS recovery** is a runtime choice: the ordinary entry point defaults to `HBFSIM_ENABLE_SASS_LIFTER=0`, and the research entry point `scripts/run_with_sass_research.sh` enables SASS recovery by default. [Usage notes](tools/sass_runtime/README.md). **EQ3** is likewise an optional module; for how to enable EQ3, see the [module documentation](docs/eq3_thermal/README.md).

<details>
<summary>Profiles, the 4 KiB study baseline, and the current implementation scope</summary>

**Currently available profiles.** `configs/profiles/{conservative,nominal,aggressive}.json` are historical synthetic configurations, each with 1 TiB and 16 KiB pages; `cd8p-vmem-p50` is a calibrated configuration from a measured software path. Configurations are read and checked by a typed loader (`src/profile/profile.cpp`); the field conventions are also described in a [JSON Schema](configs/schema/hbf-profile.schema.json). Capacity, page size, service parameters, and `time_scale` should be saved along with experiment results.

**Planned 4 KiB baseline.** The follow-up study design uses a total capacity of 2 TiB across 4 stacks, with 512 GiB per stack by default. The values below are study parameters; the values are not configurations implemented in the existing JSON files, and they are not measured values from an HBF device.

| Grade | Page | Read / write latency | Total internal channels | Total queue entries | Total bandwidth cap |
|---|---:|---:|---:|---:|---:|
| `conservative` | 4 KiB | 20 / 200 μs | 16 | 1024 | 128 GB/s |
| `nominal` | 4 KiB | 10 / 100 μs | 32 | 2048 | 512 GB/s |
| `aggressive` | 4 KiB | 5 / 50 μs | 64 | 4096 | 1000 GB/s |

Each stack independently enforces 1/4 of the total bandwidth cap and does not borrow unused allowance from other stacks. The program implementation, pre-run validation, and the budget for the formal experiment matrix are still to be done, so the historical JSON files with the same names cannot be used directly to launch the 4 KiB baseline. [Full design](docs/evaluation/hbf-4k-study-baseline.md)

**Wait semantics.** None of the existing profiles meets all admission conditions of the experimental dependency-point waiting path, which include `time_scale=1` and calibration that is not from a measured curve, among others. Turning on the build switch alone is not enough to make the existing profiles use dependency-point waiting; there is also no paired measurement yet of issue-point waiting and dependency-point waiting on the same workload. [Implementation limits](docs/51-defect-review-and-fixes.md)

**Writes and paging.** Timing writes are still timed per store instruction rather than merged into 4 KiB program units; for the in-place dirty-page rewrite of Capacity, see the [correctness TODO](TODO.md#correctness-and-fidelity), and for early frame reuse, see the [defect review](docs/51-defect-review-and-fixes.md). Success with sparse read-only access cannot be extrapolated to correctness in arbitrary writable paging scenarios.

**Extension paths.** The UCIe backend currently supports reads only, with no GPU run records and no 2/8-stack sweeps yet; EQ3 has thermal and maintenance models but no MTBF lifetime prediction. Runtime prefetching in the paper prototype should not automatically be considered a capability of the `main` default build.

</details>

<a id="roadmap"></a>
## Roadmap

This section keeps only the feature milestones that affect usage and research capability; for detailed acceptance criteria, see [TODO.md](TODO.md). For release, documentation cleanup, and repository maintenance records, see the [release notes](docs/releases/v0.1.1.md).

### Done

- [x] **GPU memory-access integration and capacity tier:** explicit address registration, automatic PTX rewriting, launch coverage checks, and the two modes Timing / Capacity.
- [x] **Online media modeling:** the MQSim reference path, the GPU-local fast path, and the hybrid mode, plus the optional EQ3 thermal and maintenance module.
- [x] **Inference framework and weight integration:** vLLM and llama.cpp adapters; 98-storage validation of OLMoE on native PTX and 147-storage fixed-request validation on the optional SASS path.
- [x] **Multi-stack extension:** the UCIe read-only interface, independent MQSim workers, striping, and request coalescing, with CPU-side validation completed.
- [x] **Waiting and compatibility improvements:** the experimental implementation of dependency-point waiting with RTX 5090 semantics checks; fixes for PTX target compatibility and for several groups of timing and instrumentation defects.

### In progress

- [ ] **Improve waiting and concurrency modeling:** provide profiles that meet the admission conditions, complete the issue-point / dependency-point waiting comparison, and advance runtime prefetching.
- [ ] **Fix write and eviction semantics:** model writes in 4 KiB program units, respect the sequential programming constraint, and fix the frame lifetime race.
- [ ] **Improve media data reuse:** introduce a page buffer and cross-warp page-residency filtering, so that repeated accesses are not counted repeatedly as media reads.
- [ ] **Extend multi-stack capability:** add a write path, GPU validation, and capacity and bandwidth sweeps across different stack counts.
- [ ] **Complete validation and measurement:** held-out calibration validation, a fast/reference comparison on the same input, a split of the four kinds of time, and end-to-end prefill/decode plus formal workload evaluation.

<a id="documentation"></a>
## Documentation and code guide

| Goal | Entry point |
|---|---|
| Build, run, and troubleshoot | [Build environment](docs/build/rtx-5090-environment.md) · [Troubleshooting](docs/troubleshooting.md) |
| Understand the internal implementation | [Documentation index](docs/README.md) · [Subsystem reading guide](docs/skills/) |
| Run and reproduce experiments | [Experiment records](docs/proofs/) · [Evaluation runbooks](docs/eval/) · [Reproduction packages](reproduction/) |
| Use extension modules | [UCIe / multi-stack](docs/ucie/README.md) · [EQ3](docs/eq3_thermal/README.md) · [SASS recovery](tools/sass_runtime/README.md) |
| View plans and releases | [TODO](TODO.md) · [v0.1.1](docs/releases/v0.1.1.md) |

Historical documents, logs, and input examples use public copies with normalized paths; for how to fill in paths and verify the copies, see [PUBLIC_COPY_NOTICE.md](PUBLIC_COPY_NOTICE.md).

<details>
<summary>Repository structure</summary>

```text
HBFSim/
├── src/             # CUDA runtime, PTX pass, capacity service, timing and thermal models
├── include/hbfsim/  # public C API and host / device interfaces
├── adapters/        # vLLM and llama.cpp adapters, offline capacity trace replay
├── benchmarks/      # CUDA, MQSim, UCIe and offline evaluation benchmarks
├── configs/         # profiles, thermal configs, sweep parameters and JSON Schemas
├── scripts/         # build, run entry points, evaluation and calibration tools
├── tests/           # CPU / GPU tests and PTX fixtures
├── tools/           # SASS recovery, native PTX reproduction and thermal tools
├── experiments/     # EQ3 system thermal, rate and maintenance experiments
├── reproduction/    # fixed-experiment reproduction packages and acceptance records
├── third_party/     # pinned bpftime and MQSim
├── patches/         # patches for external dependencies
├── cmake/           # dependency builds, version pinning and PTX embedding
└── docs/            # design, experiment, specification, build and troubleshooting docs
```

`paper/` is unrelated to building and testing; bootstrap initializes only the two build dependencies bpftime and MQSim.

</details>

<a id="citation"></a>
## Paper and citation

[HBFSim: Fast and Faithful Simulation of High-Bandwidth Flash Under Real GPU Execution](https://arxiv.org/abs/2609.09800)

```bibtex
@article{hu2026hbfsim,
  title   = {HBFSim: Fast and Faithful Simulation of High-Bandwidth Flash Under Real GPU Execution},
  author  = {Hu, Yanpeng and Yang, Yiwei and Zhu, Yuanwu and Zheng, Yusheng and Quinn, Andi and Zhang, Wei},
  journal = {arXiv preprint arXiv:2609.09800},
  year    = {2026}
}
```

The repository also provides [CITATION.cff](CITATION.cff), so a citation can be obtained through GitHub's "Cite this repository".

<a id="contributing"></a>
## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for the contribution guide. When submitting performance or correctness results, please also provide reproducible commands, the environment, the profile, coverage records, and raw output, and state clearly whether each number is modeled time or wall-clock time.

Usage questions can be discussed in [Q&A](https://github.com/SlugLab/HBFSim/discussions/new?category=q-a); bug reports use the [issue template](https://github.com/SlugLab/HBFSim/issues/new?template=bug_report.yml), and code contributions use the [PR template](.github/pull_request_template.md). Participants are asked to follow the [code of conduct](CODE_OF_CONDUCT.md); report security vulnerabilities privately as described in [SECURITY.md](SECURITY.md).

<a id="acknowledgements"></a>
## License and acknowledgements

HBFSim is licensed under the [Apache License 2.0](LICENSE).

We thank **bpftime / eGPU** for providing the foundation for GPU interception and PTX instrumentation, and **MQSim from the ETH Zurich SAFARI Research Group** for providing the flash media model. bpftime and MQSim are kept as pinned-version dependencies, and each follows its upstream license.

We thank the contributors of [hetGPU](https://github.com/vickiegpt/Concordia) for providing the SASS→PTX recovery pipeline, which allows selected precompiled NVIDIA kernels to enter the memory-access instrumentation flow of HBFSim. hetGPU is derived from ZLUDA and is licensed under Apache-2.0 or MIT.
