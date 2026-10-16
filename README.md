# HBFSim: simulating High-Bandwidth Flash while the workload runs on a real GPU

[![License: Apache-2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](https://opensource.org/licenses/Apache-2.0)
[![CI](https://github.com/SlugLab/HBFSim/actions/workflows/ci.yml/badge.svg)](https://github.com/SlugLab/HBFSim/actions/workflows/ci.yml)
[![arXiv:2609.09800](https://img.shields.io/badge/arXiv-2609.09800-b31b1b.svg)](https://arxiv.org/abs/2609.09800)

**English** | [中文](README.zh-CN.md)

HBFSim is an open-source High-Bandwidth Flash (**HBF**) simulator, an evaluation platform for
HBF before HBF devices can be bought. HBF is a memory tier that stacks
NAND flash inside the package of a compute chip such as a GPU, one tier below High-Bandwidth
Memory (**HBM**).

HBFSim lets an application execute normally on a real GPU. During execution, HBFSim applies HBF
timing, capacity, and thermal effects to the data registered as HBF, instead of recording an
access sequence and replaying the recorded sequence afterwards. The thermal effects come from the
EQ3 thermal and reliability model in the repository.

HBFSim already runs two examples on a real GPU. With modeled delay injected into vLLM serving
Qwen3-30B, the generated token identifiers match the baseline. A 110 GiB logical address range
completed all 128 accesses on a 97,887 MiB GPU. See [What HBFSim has measured](#what-hbfsim-has-measured)
for the details and limits.

HBFSim is for system designers and researchers who have to decide how much HBF capacity to buy
and which tensors to place in HBF. To run something first, start at [Quick start](#quick-start).
To see why such a simulator is needed, start at [Why HBF matters](#why-hbf-matters).

[Why HBF matters](#why-hbf-matters) · [Why existing methods cannot settle the question](#why-existing-methods-cannot-settle-the-question) · [The HBFSim approach](#the-hbfsim-approach) · [Features](#features) · [Quick start](#quick-start) · [What HBFSim has measured](#what-hbfsim-has-measured) · [Roadmap and TODO](#roadmap-and-todo) · [Paper on arXiv](https://arxiv.org/abs/2609.09800) · [Open work in TODO.md](TODO.md)

Historical documents, logs, and input examples use public path-normalized copies. See [how to configure paths and verify these copies](PUBLIC_COPY_NOTICE.md).

> Part of HBFSim is still experimental, and the [Features](#features) table marks the status of
> each feature: on by default or opt-in. To report a reproducible problem,
> use the [bug report form](https://github.com/SlugLab/HBFSim/issues/new?template=bug_report.yml); for a
> usage question, ask in the [Q&A discussions](https://github.com/SlugLab/HBFSim/discussions/new?category=q-a).

## Why HBF matters

LLM inference needs more and more memory capacity, while HBM is limited in both capacity and
supply. HBF augments HBM by placing NAND flash in the package of the compute chip, at the cost of
limited write endurance and a read latency much longer than that of HBM; data in LLM inference
that is read far more often than written, in an order known in advance, suffers little from these
two weaknesses. How much HBF helps inference is disputed, and every side's conclusion comes from
its own model, with no published measurement on a real device yet.

LLM inference is limited by both the bandwidth and the capacity of GPU memory. In the decode
stage, every generated token requires loading the model parameters and the KV cache from memory
into the compute units. Every computation in the decode stage is memory-bound
([Yuan et al., arXiv:2402.16363](https://arxiv.org/abs/2402.16363);
[Pope et al., arXiv:2211.05102](https://arxiv.org/abs/2211.05102)). Model sizes and context
lengths grow with every generation, which raises the memory capacity needed
([HBF specification](docs/HBF_OCP/ocp2026-hbf-architecture-specification-v0-7-0.pdf), chapter 3).

HBM is limited in both capacity and supply. The HBM4 standard that JEDEC released in April 2025
allows at most 64 GB per stack and at most 2 TB/s of bandwidth
([JEDEC press release](https://www.jedec.org/news/pressreleases/jedec%C2%AE-and-industry-leaders-collaborate-release-jesd270-4-hbm4-standard-advancing)).
The Micron quarterly report for the quarter ended May 28, 2026 states that producing a given
number of HBM bits takes more wafers and cleanroom space than conventional DRAM, and that
AI-driven memory and storage growth is outpacing industry supply
([Micron Form 10-Q](https://www.sec.gov/Archives/edgar/data/723125/000072312526000015/mu-20260528.htm)).
Sandisk, which promotes HBF, describes HBM as constrained by limited capacity, high power
consumption, and steep costs
([Sandisk HBF fact sheet](https://documents.sandisk.com/content/dam/asset-library/en_us/assets/public/sandisk/collateral/company/Sandisk-HBF-Fact-Sheet.pdf),
a vendor claim).

HBF augments HBM with NAND flash. One HBF stack consists of a base die at the bottom with up to 16
NAND dies on top, and connects to the compute chip in the same package through Universal Chiplet
Interconnect Express (**UCIe**), with a channel reach of less than 2 mm (specification chapter 4
and section 5.1.1). SK hynix describes HBF technology as "a new memory layer between HBM and SSDs"
([SK hynix newsroom](https://news.skhynix.com/en/hbf-at-fms-2026/)). When HBF and HBM are used
together, the two capacities differ so much that the specification requires separate memory
management (section 11.1.2).

The first HBF technical specification, v0.7.0, was published by Sandisk and SK hynix through the
Open Compute Project (**OCP**) on August 3, 2026
([specification PDF on OCP](https://www.opencompute.org/documents/ocp-hbf-architecture-specification-v0-7-0-final-pdf),
with a [copy in this repository](docs/HBF_OCP/ocp2026-hbf-architecture-specification-v0-7-0.pdf);
[Sandisk press release](https://www.sandisk.com/company/newsroom/press-releases/2026/2026-08-03-Sandisk-and-sk-hynix-advance-global-standardization-of-hbf)).
The specification states that it is "open and available for the industry to use and adopt". The
acknowledgements section of the specification thanks Google and Tenstorrent for their feedback.

Vendors have published the following capacity, bandwidth, and cost targets. The first two come
from the specification text; the last two are Sandisk claims.

- 8x to 16x the capacity of HBM, with the same read bandwidth (specification chapter 3).
- Three speed grades, with a maximum user bandwidth per stack of 0.384, 1.536, and 3.072 TB/s;
  512 GiB per stack is given as an example capacity (specification section 4.1 and Table 4).
- Cost similar to HBM (Sandisk fact sheet, July 2025).
- A first-generation product with 1.6 TB/s of read bandwidth and 512 GB per 16-die stack
  (Sandisk fact sheet).

HBF inherits two weaknesses of NAND flash.

- **Writes are expensive, and endurance is limited.** The specification states that HBF, unlike
  HBM, is subject to endurance limitations. Writes go in 4 KiB units, consecutively within a
  block, and the device automatically erases a whole block when the host writes page 0 of that
  block. Power-on data retention is guaranteed for 24 hours at 85 °C, and data typically has to be
  refreshed every 24 to 48 hours (specification section 5.4.1, chapter 9, and section 11.5).
- **Read latency is measured in microseconds.** The specification gives no read latency.
  Published studies use HBF read latencies between 1 and 20 μs, and every one of these values
  is an assumption: [TileLens](https://arxiv.org/abs/2607.04031) states 1 to 10 μs, uses 5 μs in
  its main experiments, and sweeps up to 20 μs; the [H³ paper](https://doi.org/10.1109/LCA.2026.3660969)
  by SK hynix researchers assumes 20 μs (according to a Chinese translation of the paper). One
  GPU access to HBM takes a few hundred nanoseconds, for example about 344.2 ns for Hopper reading
  its local HBM on GH200 ([Fusco et al., arXiv:2408.11556](https://arxiv.org/abs/2408.11556)), so
  at the 5 μs of the TileLens main experiments and the 20 μs of H³, an HBF read takes about 15 to
  58 times as long as 344.2 ns.

LLM inference holds a large amount of data that is read far more often than written, in an order
known in advance. Data that is rarely written keeps the write cost low, and a read order known in
advance allows prefetching to hide the read latency.

- **Weights.** Section 13.3.1 of the specification states that a set of parameters has to be
  loaded at every token generation, ranging from several GiB to tens or hundreds of GiB. Section
  13.3.3 notes that a zone mixing weights with KV cache, whose read and write patterns differ, may
  hurt overall endurance and capacity utilization. The specification therefore allows capacity to
  be partitioned by channel, with separate partitions for weights and for KV cache.
- **KV cache for a shared prefix.** Inference often batches requests that share a prefix, such as
  few-shot examples or a system prompt
  ([Hydragen, arXiv:2402.05099](https://arxiv.org/abs/2402.05099)), so a whole batch of requests
  reads the KV cache of the same prefix.
- **MoE expert weights.** In a Hot Chips 2026 tutorial, the GPU IP company Oxmiq Labs used
  Kimi-K3 (2.8T parameters) as an example: about 93% of its roughly 1.56 TB of weights are MoE
  expert weights, and the expert weights are largely write-once (as reported by
  [ServeTheHome](https://www.servethehome.com/oxmiq-labs-hbf-in-ai-compute-at-hot-chips-2026/) and
  [Tom's Hardware](https://www.tomshardware.com/pc-components/ssds/hot-chips-2026-high-bandwidth-flash-promises-massive-bandwidth-and-capacity-but-its-usability-is-extremely-limited-new-memory-format-strikes-a-balance-between-hbm-and-nand-flash)).
- **Layer-by-layer execution order.** A Transformer executes its layers in a fixed order, so while
  computing layer i the program already knows which weights layer i+1 will read. For
  [Llama 3 8B](https://arxiv.org/abs/2407.21783) (BF16) decoding on an
  [H100 SXM5](https://resources.nvidia.com/en-us-gpu-resources/h100-datasheet-24306) (3.35 TB/s
  of memory bandwidth), reading one layer of weights (436,207,616 bytes) takes about 130 μs, time
  that can be used to prefetch the next layer. The 130 μs figure is our estimate from public
  parameters, assuming that small-batch decode is fully bound by weight-read bandwidth; a routed
  MoE expert is not known until the gate of the current layer finishes, so the estimate does not
  apply to routed experts.

HBF devices cannot be bought today. Sandisk plans to sample its first HBF inference products in 2027
(as reported by [StorageReview](https://www.storagereview.com/news/sandisk-tapes-out-its-first-hbf-memory-die-targets-2027-for-inference-product-samples)
from the Sandisk investor day in August 2026). The timeline SK hynix showed at the 2026 Future of
Memory and Storage (**FMS**) conference puts the full specification in early 2027 and HBF samples
in early 2028 (according to an article by Forbes contributor Thomas Coughlin,
[republished on Yahoo](https://ca.news.yahoo.com/high-bandwidth-flash-advances-2026-040711224.html)).
Public reports do not make clear whether the two companies mean the same kind of sample.

Opinions on HBF fall into two camps, with a middle position between them.

- **Supporters.** Sandisk and SK hynix initiated the specification. The H³ paper by SK hynix
  researchers places read-only data, including shared precomputed KV cache, in HBF and the rest
  in HBM. The H³ simulation reports up to 2.69 times the throughput per watt of an HBM-only system
  (according to a Chinese translation of the paper).
- **Skeptics.** In a HotInfra '26 workshop paper, three Micron researchers compare designs with an
  analytical model. The three researchers conclude that on-package LPDDR matches or beats HBF at every operating
  point they evaluate, because the write rate and endurance limits of NAND leave HBF unable to
  absorb the writes from KV cache spill
  ([paper PDF](https://hotinfra.org/2026/papers/hotinfra26-final83.pdf); the authors state that
  the paper does not necessarily reflect the official position of Micron).
- **Middle position.** In its Hot Chips 2026 tutorial, Oxmiq Labs treats HBF as
  a capacity tier that wins only where bandwidth demand is low, for example MoE with small batches
  and long-context sparse KV (as reported by
  [ServeTheHome](https://www.servethehome.com/oxmiq-labs-hbf-in-ai-compute-at-hot-chips-2026/)).

Each side's conclusion comes from its own model, with no published measurement on a real device
yet. The
performance figures in the Sandisk fact sheet come from internal testing and simulation, H³ uses
an in-house simulator, the Micron researchers use a roofline analytical model, and Oxmiq uses a
rack-level simulation. Until real devices exist, deciding which side is right needs an
open-source simulator that favors neither side and runs real workloads; HBFSim is designed to
meet that need.

## Why existing methods cannot settle the question

Consider a system designer with two questions. If the expert weights of Qwen3-30B go into HBF and
HBM keeps only part of the expert weights, how much slower does decode become? How much HBM
capacity and how much HBF capacity should be bought? Serving an LLM is limited by memory
capacity, and a designer choosing how much HBF capacity to buy, or which tensors to place in HBF,
cannot wait for HBF devices.

Each existing method below is good at something HBFSim does not attempt.

- A storage simulator replaying a recorded access sequence is cheap and repeatable, and needs no
  accelerator.
- A GPU simulator exposes microarchitectural detail HBFSim never observes.
- A cycle-accurate simulator is the reference for correctness on a small kernel.
- Parameters published by vendors are the only description available today, because nobody
  outside the vendors has measured an HBF device yet.

None of these methods can tell the designer how much slower decode becomes, or how much of each
capacity to buy. There are six reasons.

1. **A cycle-accurate GPU simulator cannot finish one end-to-end inference in acceptable time.**
   Huerta et al. measured that single-threaded Accel-Sim, a cycle-accurate GPU simulator, needs
   more than five days for lavaMD from the Rodinia benchmark suite for heterogeneous computing,
   10,748,031 times slower than real hardware, and is still 766,423 times slower with 16 threads (2nd Workshop on Computer
   Architecture Modeling and Simulation, 2024, arXiv:2502.14691).
   lavaMD is not even an LLM workload.

   The authors of Accel-Sim 2.0 state that "Tracing every kernel in an LLM is prohibitively
   expensive", so they trace only one transformer block and multiply by the number of layers to
   extrapolate a whole inference. On H100, the latency of one block multiplied by 32 reproduces the
   measured latency of a full Llama-3.1-8B inference within 0.97% for prefill and 3.06% for decode
   (preprint arXiv:2608.22602). Extrapolating from one layer still requires recording a trace
   first, so the method meets the same limit as reason 2: one trace corresponds to one split
   between HBM and HBF capacity.
2. **Trace replay does not execute the workload, and one trace corresponds to one capacity
   split.** A storage simulator replays a recorded access sequence and never executes the
   workload. The trace mode of a GPU simulator replays a recorded GPU instruction trace instead of
   executing kernels on a real GPU, at only 12.5 kilo warp instructions per second (Accel-Sim,
   ISCA 2020).

   Changing the split between HBM and HBF capacity changes which tensors the inference
   framework keeps resident, how the framework partitions the KV cache, and whether migration
   kernels fire at all. None of the instructions that a new split adds or removes appear in a
   trace that was already recorded. The claim about capacity splits is the analysis in the HBFSim
   paper; no experiment has yet recorded one trace for each of two splits and compared the two
   traces.
3. **The specification gives no NAND timing parameters.** The specification contains no NAND page
   read, page program, or erase time, so a simulator has no published values to fill in.
4. **Existing memory-tier and block-device emulators intercept on the host side or at NVMe
   queues.** An interception point is a place outside the program under test through which every
   memory access must pass, and where an experimenter can add waiting time. Cylon (FAST '26)
   intercepts in the virtualization page tables of the host CPU, CXLMemSim intercepts in the host
   kernel with eBPF and CPU performance counters, and FEMU (FAST '18) intercepts at the NVMe
   submission queue.

   SwarmIO (preprint) intercepts at the layer of NVMe queues and doorbells, and keeps its
   submission and completion queues in GPU memory. An HBF access is issued by the load/store units
   of the GPU and is served inside the package, so the access passes through none of the host-side
   interception points and through no NVMe queue. The one route left is to rewrite the code of the
   program under test itself.
5. **Filling parameters into an analytical model gives numbers far from the measured curve.** We
   measured the gap on a real storage path. A scalar model that uses only a fixed read latency
   plus bandwidth computes a latency 10.11% below the measurement at 1 page and 99.93% below the
   measurement at 512 pages (14,096 ns against 20,254,374 ns; source: the proof
   [`docs/proofs/2026-08-11-cd8p-vmem-tuning.md`](docs/proofs/2026-08-11-cd8p-vmem-tuning.md),
   where a proof is a checkpoint document under `docs/proofs/` written after each experiment).
   The measured curve covers the complete software path that uses an NVMe SSD as memory,
   including page-fault and driver overhead; the curve describes neither HBF nor raw NAND.
6. **Temperature changes the speed of the same chip.** The same BF16 8192x8192 matrix multiply on
   the same GPU reached 379.117 TFLOP/s in a cold exclusive run and 348.427 TFLOP/s in a warm
   exclusive run, 8.10% lower; another measurement (the matrix multiply running concurrently with
   fio for 30 seconds) gave -3.72%. Both -8.10% and -3.72% are the GPU
   compute chip slowing itself down, not the HBF service rate, and the two measurements show that
   timing calibrated in one thermal state does not necessarily hold in another thermal state. The
   HBF specification also defines thermal throttling: once the junction temperature reaches a
   threshold, the device lowers its power, read and write responses slow down, and in severe cases
   all operations pause.

### Where the latency of one global load gets hidden

The hard part of injecting delay on a real GPU is choosing which instruction carries the delay.
After a warp issues a global load, the warp does not stop to wait for the result. The warp keeps
issuing later instructions that do not read the result, and stops only at the first instruction
that reads the result. How much of one access latency becomes time the program actually waits
depends on how many independent instructions lie between the load and the first instruction that
reads the loaded value.

```text
LDG R8           global load into register R8
IMAD R9,R8,...   first instruction that reads R8
W = time to issue the independent ALU instructions between LDG and IMAD
L = latency of the real access to GPU memory
D = modeled device time

(1) Real hardware
    warp : LDG R8 | ALU ALU ALU ... ALU | IMAD R9,R8,...
    load :        |<----- L ----->|
    stall at IMAD = max(0, L - W)

(2) Wait at the issue point                      <- main default build
    warp : [========== wait D ==========] LDG R8 | ALU ALU ALU ... ALU | IMAD R9,R8,...
    stall = D, and no ALU instruction issues early

(3) Wait at the first use                        <- HBFSIM_ENABLE_TIMING_FUTURES=ON
    warp : LDG R8 | ALU ALU ALU ... ALU | [wait] IMAD R9,R8,...
    HBF  :        |<------------ D ------------>|
    stall at IMAD = max(0, D - W)
```

The figure uses three symbols. W is the time to issue the independent ALU instructions between the
load and the first instruction that reads the result, L is the real memory access latency, and D
is the modeled device time. The line lengths in the figure are illustrative and do not correspond
to any measured time.

- Timeline (1) is real hardware. When the real access latency fits inside W, the warp does not
  stop at all.
- Timeline (2) waits the whole D in place at the issue point. None of the independent instructions
  between the load and the first instruction that reads the result can issue early, so the entire
  modeled device time becomes waiting time.
- Timeline (3) records an entry at issue time (the paper calls the entry a device future) and
  waits only at the first instruction that reads the result, so the warp stops where real hardware
  stops.

Following the analysis in the [arXiv paper](https://arxiv.org/abs/2609.09800), timeline (3)
(waiting at the first use) has three cases, depending on whether the real access latency L and the
modeled device time D fit inside W.

- Both fit inside W: the simulated run behaves like the real run.
- Only the real access latency fits inside W: the simulated run stalls where the real run does not.
- Neither fits inside W: both runs stall, but for different lengths of time.

Timeline (2) stalls for the whole D in every case.

The length of W cannot be seen at rewrite time, because the compiler backend allocates physical
registers and schedules instructions only after the rewrite. The rewrite therefore cannot compute
in advance how long to wait. Timeline (3) places the wait at the first instruction that reads the
result, and the GPU decides how long the warp stops from the actual W.

**The `main` default build follows timeline (2).** Timeline (3) has to be enabled explicitly with
the build option `HBFSIM_ENABLE_TIMING_FUTURES=ON`, which also requires CUDA 13 and sm_120, and
remains an experimental path. The "Wait at the first use" row of [Features](#features) lists the
coverage of timeline (3) and the conditions for running timeline (3). The repository does not yet contain a
paired measurement of one workload with waiting at the issue point and with waiting at the first
use ([TODO.md](TODO.md#correctness-and-fidelity)).

### HBFSim next to the alternatives

| Method | Executes the real workload | Runs on real hardware | Models the medium | Models temperature |
|---|:---:|:---:|:---:|:---:|
| Storage simulator replaying a recorded access sequence | ✗ | ✗ | ✓ | ✗ |
| GPU simulator that does not run the real compute kernels (replays a recorded GPU instruction trace) | ✗ | ✗ | ✗ | ✗ |
| Cycle-accurate simulator | ✓ | ✗ | ✓ | ✗ |
| Vendor parameter sheet | ✗ | ✗ | ✗ | ✗ |
| HBFSim | ✓ | ✓ | ✓ | ✓ |

- The rows name methods rather than specific products, so one particular tool may add one of the
  four capabilities.
- A cycle-accurate simulator does execute the workload, but cannot finish one end-to-end inference
  in acceptable time and can only extrapolate from one layer. The ✓ in the first column therefore
  does not mean that a cycle-accurate simulator can answer, in acceptable time, how much slower
  decode becomes or how much HBM and HBF capacity to buy.

## The HBFSim approach

HBFSim starts from one judgment in the [arXiv paper](https://arxiv.org/abs/2609.09800): **HBF
need not be evaluated by simulating the GPU. Only the program-visible effects of HBF need to be
modeled, and the modeled device time is then injected into the program running on a real GPU.**

The paper also requires that the injection not destroy the GPU concurrency that would hide
the original access latency; the `main` default build waits at the issue point and does not yet
meet the concurrency requirement (see
[Where the latency of one global load gets hidden](#where-the-latency-of-one-global-load-gets-hidden)).
The paper further describes runtime prefetching, and that implementation is not yet merged
into `main`. The rest of this page describes only the state of `main`.

### How it works

The design has three mechanisms.

1. **Explicitly registered address ranges decide which data counts as HBF.** Only an address the application or the
   runtime has registered is treated as HBF, and registration has to finish before the first
   kernel launch. An ordinary pointer into GPU memory stays on the native path and is left
   alone.

2. **Rewriting PTX lets HBFSim see the global loads and stores inside a kernel.** Parallel Thread
   Execution (**PTX**) is the intermediate code the NVIDIA compiler emits for a GPU kernel; the
   compiler backend then turns PTX into the machine code the GPU executes. An interception path
   derived from bpftime (a user-space eBPF runtime) and eGPU (a framework that injects eBPF programs, compiled to PTX, into
   running GPU kernels) automatically rewrites the supported global load and store instructions, so
   no CUDA kernel has to be edited by hand.

   The supported forms are scalar and vector global loads and stores. Atomics,
   `cp.async`, and texture accesses are not rewritten; HBFSim counts every such access and
   reports the access as unsupported
   ([docs/skills/02-ptx-instrumentation.md](docs/skills/02-ptx-instrumentation.md)).

   For a capacity range, a coverage gate rejects a kernel launch outright once an HBF pointer
   reaches code whose behavior cannot be proven safe. For a timing range in the same situation
   (capacity ranges and timing ranges correspond, respectively, to capacity mode and timing-only
   mode in [Two modes](#two-modes)), the gate admits the launch by default but records the launch
   as unmodeled (`opaque_unmodeled_timing`), so the launch does not count toward modeled coverage.
   Setting `HBFSIM_INSTRUMENTATION_POLICY=strict` makes the gate reject timing-range launches as well
   ([docs/skills/03-cuda-memory-semantics.md](docs/skills/03-cuda-memory-semantics.md)).

   The build checks that the resulting module is self-contained and assembles the module with CUDA
   12.8 `ptxas`. The build check is still static proof, not evidence that delay has been injected
   on a live GPU.

3. **The MQSim reference path and the fast path work together.** MQSim, an SSD simulator from the
   SAFARI Research Group, is used here as a flash-media model, not as an SSD host-stack model; the
   online, media-only MQSim is called the MQSim reference path (timing mode `reference`) and gives
   detailed timing. The fast path (timing mode `fast`) is a GPU-local model whose parameters come from the selected
   profile (the `cd8p-vmem-p50` parameters are calibrated from a measured curve); it carries
   the common accesses and computes latency and bandwidth directly on the GPU, without a round trip
   of request and completion messages. In timing mode `hybrid`, after a configurable warm-up,
   HBFSim sends a sampled share of requests to the MQSim reference path
   ([`docs/proofs/2026-08-11-hybrid-complete.md`](docs/proofs/2026-08-11-hybrid-complete.md)).

   Capacity mode always uses the MQSim reference path, whichever timing mode is selected
   ([docs/skills/07-capacity-address-translation.md](docs/skills/07-capacity-address-translation.md)).
   The fast path and the MQSim reference path have not yet been compared on the same input
   ([TODO.md](TODO.md#validation)).

HBFSim distinguishes four kinds of time: modeled device time, host service time (the time the host
side of HBFSim spends on a request), wall-clock time, and the overhead of HBFSim itself. Modeled device time is the HBF device time that HBFSim
computes, shown as D in the diagram of
[Where the latency of one global load gets hidden](#where-the-latency-of-one-global-load-gets-hidden);
when the program runs on a real GPU, HBFSim injects the modeled device time into the program.
Keeping the four kinds apart matters, because HBFSim can be functionally correct while the
software overhead of HBFSim is larger than the modeled device time.

Current outputs separate some of the four kinds, not all four. The media benchmark and the CUDA
microbenchmarks report modeled device time separately from wall-clock time. Offline trace replay also
reports exposed stall time, but no single output lists all four kinds yet. In the proof
[`docs/proofs/2026-08-11-vllm-exact-live-delay.md`](docs/proofs/2026-08-11-vllm-exact-live-delay.md),
the modeled device time and the per-warp request-path overhead are reported together, not
separately.

The optional [UCIe interface](docs/ucie/README.md) (v0.1.1) connects the host request ring (the
shared ring buffer into which the GPU writes access requests for the host-side daemon to read) to a
configurable multistack MQSim backend, with one MQSim worker per stack; [Features](#features) lists
the details. Enable the UCIe interface explicitly with `HBFSIM_ENABLE_UCIE=ON`; the existing
backend remains the default. The UCIe interface supports reads only. The UCIe interface can be
selected explicitly through the context of the CUDA interception runtime and through the vLLM
loader, but has been tested only on the CPU (the test skips CUDA registration), with no recorded
GPU run.

### Architecture

<div align="center">
  <img src="docs/assets/hbfsim-architecture.png" alt="HBFSim architecture: host-side SASS recovery, a PTX pass, and a registry of HBF address ranges; a real GPU running vLLM, where rewritten accesses wait for the modeled device time through the Device ABI; a GPU memory tier (labeled HBM tier) with a native path and a frame cache; a host capacity service with a backing file and a prefetcher; and HBF modeling with online MQSim, the UCIe multistack interface, and the thermal and reliability model" width="900">
</div>

The parts of the figure fall into three status classes, and the legend shows how each class is
drawn. The first class is part of the default runtime; the second class is an option that must be
turned on explicitly; the third class is an offline model and is not
in the runtime (in the figure, only the Prefetcher belongs to the third class). The D1–D6 labels next to boxes refer to the six subsections of the
Design section in the arXiv paper.

- **Host-side preparation.** The PTX pass rewrites a module, and the coverage gate uses the result
  to decide whether a kernel launch is admitted or rejected; the Registry records which address ranges are registered as HBF. SASS
  recovery is opt-in. SASS is the machine code an NVIDIA GPU actually executes. For a
  closed-source or precompiled kernel that ships SASS without PTX, HBFSim first recovers the
  selected kernel from SASS to PTX and then hands the recovered PTX to the PTX pass.
- **Real GPU.** vLLM serves Qwen3-30B on a real GPU. A rewritten load or store calls device-side
  helper functions that HBFSim embeds in the PTX module, and the Device ABI in the figure is the
  calling interface of those helper functions. The Device ABI checks the Registry to decide whether
  the address is registered as HBF. When a rewritten load or store accesses a registered address, the access waits for the modeled device time; an access in
  a timing range that cannot be rewritten executes as usual, and the kernel launch containing the
  access is recorded as unmodeled (`opaque_unmodeled_timing`).
- **GPU memory tier (labeled HBM tier in the figure).** An access to an unregistered address stays on the native path, while an
  access to a capacity range is served from the frame cache, which is the figure's label for the
  capacity-mode page cache held in GPU memory. The RTX PRO 6000 used in the experiments has GDDR7
  memory, which plays the role of HBM in the experiments.
- **Host capacity service.** The host capacity service supplies exact page bytes from a backing
  file. The Prefetcher belongs to the third class: `main` has only an offline sensitivity model
  that is not built by default, and runtime prefetching is not on `main`.
- **HBF modeling: timing.** The MQSim reference path (labeled Online MQSim in the figure) produces
  timing. UCIe multistack is opt-in. The UCIe multistack interface carries reads over the Advanced eXtensible Interface
  (**AXI**, the protocol layer that the specification runs on top of UCIe), stripes pages across
  stacks, coalesces requests, and runs one MQSim worker per stack. The fast path (timing mode
  `fast`) computes directly on the GPU and is not drawn as a separate box in the figure.
- **HBF modeling: thermal and reliability.** Junction temperature sets the rate HBF
  sustains and the retention deadline, and an approaching deadline forces refresh work (a read
  followed by a rewrite). A service policy sets rate and admission; the evidence output records
  bytes, wear, and mean time between failures (**MTBF**). In the figure, the thermal and
  reliability model, named EQ3 in the repository, consists of five boxes: Junction temp, Service
  policy, Retention, Refresh, and Evidence. EQ3 increments a wear count only on a successful erase and has no
  lifetime model, so EQ3 does not output MTBF yet.

### Two modes

| Mode | What changes | Question answered |
|---|---|---|
| **Timing-only** | Data stays in ordinary GPU memory, and HBFSim injects the modeled device time for registered accesses only. | How sensitive is this workload to HBF latency, bandwidth, and contention? |
| **Capacity** | Registered data is backed by a file and staged through a bounded page cache held in GPU memory. | Can this workload still run when the working set is larger than GPU memory, and what cache behavior results? |

Both modes use the same explicitly registered ranges, the same PTX coverage rules, the same named
HBF profiles (a profile is a JSON file under `configs/profiles/` that sets the read latency,
program latency, channel count, capacity, and other parameters of the simulated HBF, for example
`nominal.json` in the quick start), and the same reporting model.

In capacity mode, every registered file range in one context shares one bounded page cache held
in GPU memory (the repository code and documents call the page cache the HBM cache). A hit
resolves to a resident frame, a miss loads the backing page and contributes one modeled media
read, and a dirty eviction contributes a modeled media program before the bytes of that page
return to the backing file.

Capacity mode has two known limitations. A dirty page is currently written back in place, while
HBF forbids random writes inside a block; [TODO.md](TODO.md#correctness-and-fidelity) records the
in-place write-back as a severity A defect. A page-cache frame can be reassigned before the GPU
has read the frame. The frame race has been measured but not fixed, and no published number is
affected, because no published run reached the eviction path
([docs/51-defect-review-and-fixes.md](docs/51-defect-review-and-fixes.md)).

## Features

The status column uses two values: on by default means that the default build includes the
feature and the runtime can use the feature directly; opt-in means that a build option or a
runtime switch has to be set. The rows for full-model weight registration, the three
adapters, and the issue and Q&A discussion templates cannot be described by a single switch, so
the status cells of those three rows say how far the existing evidence goes and what is still
missing.

| Feature | Description | Status | Evidence |
|---|---|---|---|
| Automatic PTX rewriting and coverage gate | Rewrites supported global loads/stores automatically; counts and reports unsupported accesses; rejects a capacity-range launch that reaches code not provable as safe | On by default | [`docs/proofs/2026-08-11-cd8p-vmem-tuning.md`](docs/proofs/2026-08-11-cd8p-vmem-tuning.md): every pass manifest reports 30 rewritten instructions and 0 unsupported instructions |
| Explicitly registered address ranges | `hbfsim_register_device` registers a span of GPU memory as a timing range; `hbfsim_map_file` maps a file as a capacity range | On by default | [`include/hbfsim/api.h`](include/hbfsim/api.h); usage in the code at the end of this section |
| Timing-only and capacity modes | Inject delay only; or back data with a file and page the data through a page cache in GPU memory | On by default | [`docs/proofs/2026-08-11-vllm-exact-live-delay.md`](docs/proofs/2026-08-11-vllm-exact-live-delay.md), [`docs/proofs/2026-08-11-hybrid-complete.md`](docs/proofs/2026-08-11-hybrid-complete.md) |
| MQSim reference path and fast path | Three timing modes: `reference`, `fast`, and `hybrid` | On by default (`HBFSIM_ENABLE_MQSIM` defaults to `ON`) | [`docs/proofs/2026-08-11-hybrid-complete.md`](docs/proofs/2026-08-11-hybrid-complete.md) |
| UCIe multistack interface (v0.1.1) | AXI read transport, page striping across stacks, request coalescing, and shared-memory worker communication, with event-driven advancement and batched completion handling; one MQSim worker per stack, each stack with its own service limit; reads only | Opt-in (`HBFSIM_ENABLE_UCIE=ON`), validated on the CPU only | [docs/ucie](docs/ucie/README.md), [release notes](docs/releases/v0.1.1.md), [`docs/proofs/2026-10-06-ucie-release-cpu.md`](docs/proofs/2026-10-06-ucie-release-cpu.md) |
| SASS recovery | For a closed-source or precompiled kernel without PTX, recovers the selected kernel from SASS to PTX before instrumentation; the recovery pipeline comes from hetGPU | Opt-in: off in the ordinary entry point (`HBFSIM_ENABLE_SASS_LIFTER=0`), on by default in the research entry point `scripts/run_with_sass_research.sh` | [tools/sass_runtime](tools/sass_runtime/README.md) |
| Full-model weight registration | OLMoE-1B-7B-0924: the native PTX path (rewrites the PTX the compiler emitted, without SASS recovery) registers all 98 supported weight storages, and SASS recovery extends registration to all 147 storages | The 147-storage half depends on SASS recovery, which is opt-in, was run on one fixed request only, and has no clean source rebuild and no cross-machine GPU reproduction. The 98-storage half was also run on one fixed request only; components rebuilt from source have passed only the 8-storage representative run, and the 98-storage run has not been repeated with the rebuilt components | [98-storage results](docs/hbfsim-supported-weights/RESULTS.md), [source rebuild record for the 98-storage half](docs/hbfsim-supported-weights/REBUILT_SOURCE_VALIDATION.md), [147-storage reproduction package](reproduction/sass-final147-20260926/README.md) |
| Three adapters | [`adapters/vllm/`](adapters/vllm/), [`adapters/vllm_capacity/`](adapters/vllm_capacity/), [`adapters/llama_cpp/`](adapters/llama_cpp/) | vLLM and llama.cpp have real-GPU proofs; the offline trace replay of vLLM capacity has CPU fixture tests only | [`docs/proofs/2026-08-11-vllm-exact-live-delay.md`](docs/proofs/2026-08-11-vllm-exact-live-delay.md), llama.cpp section of [`docs/proofs/2026-08-11-hybrid-complete.md`](docs/proofs/2026-08-11-hybrid-complete.md) |
| EQ3 thermal and reliability model | Junction temperature, temperature-dependent service rate, data retention deadlines, and refresh; code in directories such as `src/eq3_thermal/` and `experiments/eq3_system_thermal/` | Opt-in (the module is off by default) | [docs/eq3_thermal](docs/eq3_thermal/README.md), [experiments/eq3_system_thermal](experiments/eq3_system_thermal/README.md) |
| Wait at the first use | Records a device future at issue and waits only at the first instruction that reads the result; covers only loads in timing ranges and uses only the scalar model of the fast path (timing mode `fast`) | Opt-in: the build option `HBFSIM_ENABLE_TIMING_FUTURES` is off by default; 6 wait-semantics cases pass on RTX 5090; no profile shipped in the repository passes the admission check (which requires, among other conditions, `time_scale` 1 and a profile that is not calibrated from measurements; `time_scale` is the factor in a profile that scales up the modeled latency, and `nominal.json` sets `time_scale` to 100), so the shipped profiles cannot exercise the path that waits at the first use | [docs/52-main-gpu-thermal-integration](docs/52-main-gpu-thermal-integration/README.md), [docs/51](docs/51-defect-review-and-fixes.md) |
| PTX target compatibility fix | Both bpftime build paths preserve PTX target features and surface compiler diagnostics (commit `fccfd23`) | On by default | [troubleshooting guide](docs/troubleshooting.md) |
| Issue and Q&A discussion templates | A bug report form and a Q&A form | Provided in the repository; not a build or runtime feature | [bug_report.yml](.github/ISSUE_TEMPLATE/bug_report.yml), [q-a.yml](.github/DISCUSSION_TEMPLATE/q-a.yml), [docs/troubleshooting.md](docs/troubleshooting.md) |

Registering a span of GPU memory as a timing range takes the lines below, taken from
[`benchmarks/cuda/hbf_microbench.cu`](benchmarks/cuda/hbf_microbench.cu) lines 321–330. The full C
API is in [`include/hbfsim/api.h`](include/hbfsim/api.h) lines 92–107.

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

Calling the C API alone does not rewrite a kernel. PTX rewriting is done by the bpftime
interception path, whose entry script is `scripts/run_with_bpftime.sh`, used as
`scripts/run_with_bpftime.sh -- command [args ...]`. Registration has to finish before the first
kernel launch.

## Quick start

For the compilers, dependency versions, and full build commands of the recorded research setup,
see the [RTX 5090 build environment and full build commands](docs/build/rtx-5090-environment.md).
The RTX 5090 machine is used for the OLMoE research builds, and the RTX 5090 document records the
environment as of October 15, 2026. In [What HBFSim has measured](#what-hbfsim-has-measured), the
five GPU results for the calibration breakpoints, the fast path, vLLM, the 110 GiB capacity run,
and temperature come from a different machine: an NVIDIA RTX PRO 6000 Blackwell Server Edition
with driver 595.84.

For a build or runtime problem, use the [troubleshooting guide and bug report form](docs/troubleshooting.md).
A report should include the environment, the command, and the complete first error.

HBFSim needs native Linux, CMake 3.25 or newer, Ninja, a compiler with C++20 support, OpenSSL,
Python 3, and Git. The CUDA components need CUDA 12.8 or newer.

The media simulator and the benchmark of the media simulator build without CUDA, which is the
configuration below.

```bash
git clone https://github.com/SlugLab/HBFSim.git
cd HBFSim

HBFSIM_ENABLE_CUDA=OFF HBFSIM_ENABLE_MQSIM=ON ./scripts/bootstrap.sh
cmake --build build -j"$(nproc)"
ctest --test-dir build --output-on-failure
```

Running the `ctest` command above as written also runs `vmem_tuning`, `run_with_bpftime`, and
`context_lifecycle`, the three tests that CI excludes by name, and `context_lifecycle` still fails
today. To get 31 of 31, use the CI form
`ctest --test-dir build --output-on-failure -E 'vmem_tuning|run_with_bpftime|context_lifecycle'`
(sources: [.github/workflows/ci.yml](.github/workflows/ci.yml) and [TODO.md](TODO.md#validation)).

On September 19, 2026, at commit `eee734a`, on a machine without CUDA, configure succeeded and all
152 build targets built. Of the 34 tests, CI excludes three by name (`vmem_tuning` and
`run_with_bpftime` depend on a specific environment, while `context_lifecycle` is an open
defect), and the other 31 all passed.
[TODO.md](TODO.md) records why CI excluded `vmem_tuning`, `run_with_bpftime`, and
`context_lifecycle` at that time. After September 19, 2026, `vmem_tuning` was changed to skip its
byte-for-byte reproduction subtest when the external calibration CSV is missing, so the missing
CSV no longer makes the test fail (see [Roadmap and TODO](#roadmap-and-todo)).

If the machine has the libbpf development package and clang installed while CUDA is turned off,
configure fails on a `.cu` target with
`CMake can not determine linker language for target: hbfsim_llama_probe_module`. The workaround
is to call cmake directly with `-DCMAKE_DISABLE_FIND_PACKAGE_PkgConfig=ON`, an option that
`scripts/bootstrap.sh` does not pass. The full command is in
[docs/eval/runbooks/environment_setup.md](docs/eval/runbooks/environment_setup.md).

The default branch is `main`, so the `git clone` above needs no `--branch` argument.

SASS recovery is experimental and is **off by default** in the ordinary entry point. The
[SASS runtime tools](tools/sass_runtime/) explain how to opt in and how to report the layers whose
bindings actually ran. Native PTX instrumentation is independent of the SASS recovery switch.

### See one run

The media benchmark runs without a GPU. The media benchmark drives the MQSim reference path with
deterministic sequential requests and emits one JSON document. The output below came
from a build of commit `0a0ae42` (September 19, 2026) and is abridged: the top-level fields
`schema_version`, `profile`, and `workload` are removed, and several objects are collapsed onto
one line.

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

`capacity_bytes` is 64 GiB, smaller than the 1 TiB written in `nominal.json`, because without
`--capacity-bytes` the benchmark caps capacity at 16 blocks per plane. `hbm_cache_bytes` is capped
as well, at 64 MiB. `nominal.json` holds synthetic parameters, not measured ones.

Read the two fields `modeled` and `wall` in `timing_ns` together. `timing_ns.modeled` is the modeled device time;
`timing_ns.wall` is the wall-clock time HBFSim took to compute the modeled device time.

The media benchmark reports `timing_ns.modeled` and `timing_ns.wall` separately (the
corresponding fields in the CUDA microbenchmark `hbf_microbench` are `timing.modeled_ns` and
`wall_ns`), because a simulator can be functionally correct while its own overhead exceeds the
modeled device time.

## What HBFSim has measured

Each item below names its source document. Unless stated otherwise, the GPU experiments ran on an
NVIDIA RTX PRO 6000 Blackwell Server Edition with driver 595.84. The RTX PRO 6000 has GDDR7
memory, not HBM, and the GDDR7 memory plays the role of HBM in the experiments
([docs/eval/capacity_qwen3_30b_a3b.md](docs/eval/capacity_qwen3_30b_a3b.md)).

- **Six calibration breakpoints match exactly.** The measurement is the vmem software path on the
  Dell CD8P, which uses an NVMe SSD as memory: the P50 latency at 1, 4, 16, 64, 256, and 512
  contiguous 4 KiB pages forms a calibration curve. With the P50 curve loaded into HBFSim, a kernel
  ran on the RTX PRO 6000 in six cases that read 1, 4, 16, 64, 256, and 512 pages in turn, and
  HBFSim injected delay according to the calibration curve (the proof calls this step real-GPU
  replay). Every case matched the modeled total of the source data exactly, with checksums equal to
  the baseline and zero unsafe launches (an unsafe launch is a kernel launch in which an HBF address
  reaches code that HBFSim cannot prove safe).

  The match is a deterministic calibration check, not a cross-validation: all six points took part
  in the fit, and none of the six was held out. Source:
  [`docs/proofs/2026-08-11-cd8p-vmem-tuning.md`](docs/proofs/2026-08-11-cd8p-vmem-tuning.md).

- **The fast path is about 22 times faster than the MQSim reference path.** On the same
  deterministic Qwen3-30B case, the run labeled reference in the proof took 44.469 s and the fast path took
  2.014352 s; the factor of about 22 is computed from these two times. Both runs generated
  identical token identifiers.

  The ratio of about 22 holds between two wall-clock times of HBFSim, and is not a prediction of
  HBF device performance. Source: [`docs/proofs/2026-08-11-hybrid-complete.md`](docs/proofs/2026-08-11-hybrid-complete.md).

- **Delay injected into vLLM leaves the output unchanged, token by token.** vLLM 0.15.1 served
  Qwen3-30B with the vLLM source unchanged. HBFSim attaches through a loader plugin and binds 4
  Triton kernel variants to the rewritten functions. Of 2,304 `fused_moe_kernel` launches, 24 were
  modeled launches (launches in which HBFSim injected the modeled device time for the HBF accesses,
  marked `modeled: true` in the coverage record), and the token identifiers matched the baseline.

  The vLLM experiment registered only the first 16,384 bytes of the tensor
  `model.layers.0.mlp.experts.w13_weight`. All weights of the model total 61,064,245,248 bytes, and
  registering all of the weights makes the vLLM profiling pass generate an impractically large
  number of synchronous requests. Source:
  [`docs/proofs/2026-08-11-vllm-exact-live-delay.md`](docs/proofs/2026-08-11-vllm-exact-live-delay.md); the byte count of all
  weights is in [`docs/proofs/2026-08-11-vllm-timing-adapter.md`](docs/proofs/2026-08-11-vllm-timing-adapter.md).

- **A 110 GiB logical range ran on a 97,887 MiB GPU.** A 110 GiB logical address range with a
  2 GiB page cache held in GPU memory completed all 128 accesses, produced checksum
  `14245581564465502923`, which is the checksum the baseline produced, and admitted zero unsafe
  launches.

  The 110 GiB result is a sparse logical-capacity proof, and makes no claim that 110 GiB was
  physically read. Source: [`docs/proofs/2026-08-11-hybrid-complete.md`](docs/proofs/2026-08-11-hybrid-complete.md).

- **Temperature changes the behavior of one GPU.** The same BF16 8192x8192 matrix multiply reached
  379.117 TFLOP/s in a cold exclusive run that went from 35 to 70 °C, against 348.427 TFLOP/s in a
  warm exclusive run (Warm isolated GPU in the proof) that went from 61 to 85 °C, a difference of
  -8.10%. In the warm run the SM clock dropped from about 2,062 MHz to 1,642 MHz. Source:
  [`docs/proofs/2026-08-10-live-gpu-cd8p-thermal.md`](docs/proofs/2026-08-10-live-gpu-cd8p-thermal.md).

  The -8.10% is the GPU compute chip slowing itself down, not the HBF service rate. Another
  measurement (a matrix multiply running concurrently with fio for 30 seconds; see
  [`docs/proofs/2026-08-11-hybrid-complete.md`](docs/proofs/2026-08-11-hybrid-complete.md)) gave
  -3.72%, and
  the thermal configuration file uses -3.72%; see
  [TODO.md](TODO.md#modeling-endurance-and-temperature).

- **The `cd8p-vmem-p50` timing profile is calibrated from a real device.** The calibration source is a Dell DC NVMe
  CD8P E3.S 1.92TB attached over PCIe 5.0 32 GT/s x4. The Dell CD8P is an ordinary PCIe NVMe
  endpoint, not a CXL endpoint ([`docs/proofs/2026-08-10-live-gpu-cd8p-thermal.md`](docs/proofs/2026-08-10-live-gpu-cd8p-thermal.md)).

  Only the `cd8p-vmem-p50` profile comes from the measured CD8P curve. The three named profiles
  `conservative`, `nominal`, and `aggressive` hold synthetic parameters, and the quick start
  example uses the synthetic `nominal.json`.

- **Full-model weight registration and binding.** The model is OLMoE-1B-7B-0924, and both runs
  process one fixed short request only.

  The native PTX path registered all 98 supported weight storages (13,091,213,312 bytes), and the
  instrumented run and the baseline run without HBFSim both returned tokens `[[70, 13]]`; the
  98-storage run was on an RTX 5090. The remaining 49 storages (attention projections, routers,
  and the output head) are not registered as HBF on the native PTX path. The 49 storages execute
  as usual and do not count as HBF coverage. Source:
  [`docs/hbfsim-supported-weights/RESULTS.md`](docs/hbfsim-supported-weights/RESULTS.md).

  With the SASS recovery path added, the 147-storage run (the final147 in the reproduction package
  directory name `reproduction/sass-final147-20260926/` refers to this run) registers all 147
  distinct weight storages (13,838,323,712 bytes) and binds their consumers to the HBF instrumentation path; the
  output is still `[[70, 13]]`; the 147-storage run was also on an RTX 5090
  ([weight integration matrix](reproduction/sass-final147-20260926/evidence-tree/rebuttal_20260921/sass-lifter-20260924/reports/weight-integration-matrix.md)).
  See the [implementation and validation report](reproduction/sass-final147-20260926/evidence-tree/rebuttal_20260921/sass-lifter-20260924/reports/final147-20260926-v1/REPORT.md).

  Neither OLMoE result is general kernel, model, or consumer coverage. Registering complete
  storages does not mean that one short request read every byte or every expert. The
  [SASS final147 reproduction package](reproduction/sass-final147-20260926/README.md) preserves
  the experimental runtime sources, actual interface contracts, build recipes, and fixed-request
  validation evidence of the final147 experiment. No clean source rebuild and no cross-machine GPU
  reproduction were performed for the package.

  The hetGPU pipeline that SASS recovery depends on had 5 upstream PRs. The
  [initial upstream PR review](reproduction/sass-final147-20260926/upstream-review/REVIEW_SUMMARY.md)
  records generality limits separately from the model result; at the time of that review, none of
  the 5 PRs was merged. All 5 PRs were later repaired and merged into the `tmatmul` branch of
  `vickiegpt/Concordia`, and all 107 combined CPU checks passed; see the
  [upstream repair and merge follow-up](reproduction/sass-final147-20260926/upstream-repair-followup/README.md).

Builds, CPU tests, MQSim regressions, and successful PTX assembly are not live GPU proof.

`main` cannot yet give a number for how much slower decode becomes. The evaluation document
states that time per output token (**TPOT**) is not supported and that end-to-end prefill and
decode latency is not yet verified
([docs/eval/capacity_qwen3_30b_a3b.md](docs/eval/capacity_qwen3_30b_a3b.md)). The current results
establish correctness and the ability to inject delay: the injection happens on real kernels, and
the output does not change.

## 4 KiB study baselines and build options

For future paper evaluation, the chosen system input is **2 TiB of HBF across four stacks**,
divided equally by default into 512 GiB per stack. The `nominal` row is the main study baseline.
The table describes a settled design choice, **not runnable profiles or measurements of HBF devices**;
[the detailed design](docs/evaluation/hbf-4k-study-baseline.md) (written in Chinese) specifies
the geometry, per-stack limits, and validation still required.

| Study baseline | Page | Read | Program | Internal channels (total) | Page queue entries (total) | Aggregate cap (total) |
|---|---:|---:|---:|---:|---:|---:|
| `conservative` | 4 KiB | 20 μs | 200 μs | 16 | 1024 | 128 GB/s |
| `nominal` | 4 KiB | 10 μs | 100 μs | 32 | 2048 | 512 GB/s |
| `aggressive` | 4 KiB | 5 μs | 50 μs | 64 | 4096 | 1000 GB/s |

The aggregate cap is the only bandwidth input. For `stack_count = N`, each stack's cap is derived
as `aggregate_cap / N` and enforced independently; `nominal` is 512 / 4 = 128 GB/s per stack, and
a busy stack cannot borrow another stack's unused cap. `capacity_bytes` is total HBF capacity, and
total capacity and the aggregate cap are configured separately; page counts follow from capacity
and page size.

Implementation, validation, and budget approval are still required before the formal matrix. The
existing `configs/profiles/{conservative,nominal,aggressive}.json` files remain **legacy 1 TiB,
16 KiB synthetic profiles** and do not implement the four-stack design. Do not pass one of those
files to a benchmark expecting the new baseline.

A separate existing profile, `cd8p-vmem-p50`, is not synthetic: the values of `cd8p-vmem-p50` are
calibrated from the measured latency curve of the Dell CD8P, including the complete software path
and the overhead of that path. The three new rows are research assumptions; the OCP HBF
specification does not establish their modeled read-unit count or achievable throughput. Existing
legacy JSON profiles live in `configs/profiles/` and are checked by the typed loader against
`configs/schema/hbf-profile.schema.json`.

| Build option | Default | Purpose |
|---|---|---|
| `HBFSIM_ENABLE_CUDA` | `ON` | Build CUDA instrumentation and runtime components |
| `HBFSIM_ENABLE_MQSIM` | `ON` | Build the MQSim-backed host service |
| `HBFSIM_ENABLE_UCIE` | `OFF` | Build the UCIe multistack interface and host backend |
| `HBFSIM_ENABLE_LLM_TESTS` | `OFF` | Enable llama.cpp and vLLM integration tests |
| `HBFSIM_ENABLE_EVAL_TOOLS` | `OFF` | Build offline evaluation models and replay tools |
| `HBFSIM_ENABLE_TIMING_FUTURES` | `OFF` | Build the experimental path that waits at the first use; requires CUDA 13 and sm_120 |

`scripts/bootstrap.sh` passes through only three switches: `HBFSIM_ENABLE_CUDA`,
`HBFSIM_ENABLE_MQSIM`, and `HBFSIM_ENABLE_LLM_TESTS`. Pass the other options directly with
`cmake -D...`. Three more test and diagnostic options, all off by default, are in
[`CMakeLists.txt`](CMakeLists.txt) lines 7–15.

## Roadmap and TODO

[TODO.md](TODO.md) is the public backlog, and each entry in TODO.md names the evidence that would
close the entry. Completed work below is struck through. Unchecked items are not finished yet;
the items with part of the work done are marked as partly done.

**Completed in v0.1.0** ([Done section of TODO.md](TODO.md#done))

- [x] ~~Add the Apache-2.0 license, `CITATION.cff`, the code of conduct, and the security policy~~
- [x] ~~Rename the default branch from `eval_base` to `main`~~
- [x] ~~Stop `git clone --recurse-submodules` from failing for outside users on the private `paper` submodule~~
- [x] ~~Add CPU-only CI, issue and pull request templates, and the documentation link checker~~
- [x] ~~Add the architecture figure, rewrite the README, and add the Chinese README, `TODO.md`, `CONTRIBUTING.md`, and `docs/README.md`~~
- [x] ~~Verify the public clone and the CPU build on a machine holding no credential for the private repository~~
- [x] ~~Release v0.1.0~~

**Completed after v0.1.0**

- [x] ~~v0.1.1: an optional UCIe multistack read interface on the real MQSim backend, validated on the CPU~~ ([release notes](docs/releases/v0.1.1.md))
- [x] ~~Native PTX coverage of all 98 supported weight storages of OLMoE-1B-7B-0924~~ ([results](docs/hbfsim-supported-weights/RESULTS.md))
- [x] ~~SASS recovery path and the final147 reproduction package, registering 147 storages on one fixed request; acknowledgement of hetGPU~~ ([reproduction package](reproduction/sass-final147-20260926/README.md))
- [x] ~~Make SASS recovery off by default; fix PTX target compatibility with compiler diagnostics; add the troubleshooting guide and the Q&A form~~ ([troubleshooting guide](docs/troubleshooting.md))
- [x] ~~Merge the EQ3 thermal and reliability model into `main`~~ ([integration notes](docs/52-main-gpu-thermal-integration/README.md))
- [x] ~~Fix six groups of defects in the timing and instrumentation path in seven commits, and measure wait semantics on an RTX 5090~~ ([defect review and fixes](docs/51-defect-review-and-fixes.md), [RTX 5090 wait-semantics checks](docs/52-main-gpu-thermal-integration/README.md))
- [x] ~~Replace historical documents, logs, and input examples with path-normalized copies, and ship a `SHA256SUMS` checksum list~~ ([public copy notice](PUBLIC_COPY_NOTICE.md))

**Partly done**

- [ ] Wait at the first use. The mechanism is implemented and the wait-semantics checks pass on an RTX 5090, but the build option is off by default. No profile shipped in the repository passes the admission check (the check requires, among other conditions, `time_scale` 1 and a profile that is not calibrated from measurements), so the shipped profiles cannot exercise the path that waits at the first use ([docs/51](docs/51-defect-review-and-fixes.md)). The repository also has no paired measurement against waiting at the issue point yet ([TODO.md](TODO.md#correctness-and-fidelity)).
- [ ] Multistack modeling. Only the optional UCIe read path has one MQSim worker per stack, page striping, and per-stack shares of capacity and bandwidth. There is no write path, no recorded GPU run, and no 2-stack or 8-stack sweep ([TODO.md](TODO.md#modeling-stacks-links-and-topology)).
- [ ] The `vmem_tuning` test no longer fails when the external calibration CSV is missing, but byte-for-byte reproduction still needs the CSV ([TODO.md](TODO.md#validation)).
- [ ] The documentation link checker is in the pull request gate, but does not check external links yet ([TODO.md](TODO.md#repository-and-tooling)).

**Not done yet**

- [ ] Validate against held-out calibration points; all six breakpoints currently take part in the fit ([TODO.md](TODO.md#validation)).
- [ ] Charge a write once per 4 KiB program unit instead of once per store instruction, and stop rewriting evicted pages in place in capacity mode ([TODO.md](TODO.md#correctness-and-fidelity)).
- [ ] Compare the fast path against the MQSim reference path on the same input ([TODO.md](TODO.md#validation)).
- [ ] Record `time_scale` on every reported number, and set `page_bytes` to 4096 in the three named profiles ([TODO.md](TODO.md#correctness-and-fidelity)).
- [ ] Serve a repeated read of the same page from the page buffer, and add a page-residency filter across warps ([TODO.md](TODO.md#correctness-and-fidelity)).
- [ ] Run the formal measurements for the four evaluation questions in the evaluation plan; TODO.md counts 0 questions as formally complete today ([TODO.md](TODO.md#evaluation-campaign)).

## Repository layout

- `src/`: all C++ and CUDA production code, in 11 components. The components are the CUDA
  interception runtime (`cuda_runtime/`), the PTX pass (`ptxpass_hbf/`), the host service
  (`host_service/`), the UCIe multistack backend (`ucie/`), the thermal solver (`eq3_thermal/`), the MQSim adapter (`mqsim_adapter/`), the
  profile loader (`profile/`), prefetch (`prefetch/`), reporting (`reporting/`), the fast-model
  calibrator (`hybrid/`), and the page protocol (`protocol/`). By lines of code, the three largest
  are the CUDA interception runtime, the UCIe multistack backend, and the PTX pass.
- `include/hbfsim/`: public headers and the contracts that cross the host and device boundary.
- `adapters/`: three integration adapters, `llama_cpp/`, `vllm/`, and `vllm_capacity/`.
- `benchmarks/`: measurement drivers for CUDA microbenchmarks, the MQSim media benchmark
  (including the UCIe benchmarks), prefetch, and trace replay.
- `configs/`: JSON fixtures, including the named HBF profiles (UCIe profiles are in
  `profiles/ucie/`), three parameter sweeps, thermal fixtures (`thermal/`, `eq3_thermal/`), the
  `ptxpass/` and `tuning/` directories, and 4 JSON Schemas in `configs/schema/`.
- `scripts/`: the bootstrap entry point, the Python evaluation harness, the thermal calibration
  pipeline, `run_with_bpftime.sh`, `run_with_sass_research.sh`, the UCIe reproduction scripts, and
  the documentation link checker.
- `tests/`: CPU tests, integration tests, GPU tests, and PTX fixtures.
- `tools/`: SASS recovery tools (`sass_runtime/`), native PTX reproduction tools
  (`native_ptx_reproduction/`), and the EQ3 thermal configuration and solver tools.
- `experiments/`: default-off CPU experiments for EQ3 thermal behavior and maintenance
  (`eq3_system_thermal/`, `eq3_rate_thermal/`, `eq3_maintenance/`).
- `reproduction/`: the SASS final147 reproduction package (`sass-final147-20260926/`), plus a
  clean-build receipt and other JSON check records in `aggregate-pass/`.
- `patches/`: the out-of-tree patches applied to the two pinned submodules.
- `third_party/`: the two pinned submodules, bpftime and MQSim.
- `cmake/`: submodule pinning, PTX embedding, and the patched MQSim and bpftime builds.
- `docs/`: design specifications, implementation plans, proofs, evaluation runbooks,
  and reference papers, plus the EQ3 thermal model, UCIe, release notes, the OCP specification,
  the build environment, and troubleshooting.
- `paper/`: a submodule needed neither to build nor to test HBFSim; `.gitmodules` marks `paper/`
  inactive, and `scripts/bootstrap.sh` initializes only the two build dependencies.

## Documentation

- [`docs/README.md`](docs/README.md): the documentation index.
- [`docs/ucie/`](docs/ucie/README.md): UCIe interfaces, multistack configuration, software
  optimizations, and CPU reproduction commands.
- [`docs/releases/v0.1.1.md`](docs/releases/v0.1.1.md): the v0.1.1 implementation changes and
  release build instructions.
- [`docs/proofs/`](docs/proofs/): the proofs for most of the experiments in
  [What HBFSim has measured](#what-hbfsim-has-measured); every proof states the boundaries of its
  claims, and most include the commands that were run; later results are in
  [`docs/hbfsim-supported-weights/`](docs/hbfsim-supported-weights/README.md) and
  [`reproduction/`](reproduction/).
- [`docs/eval/`](docs/eval/): the evaluation plan, the workload methodology, and the reproduction
  runbooks.
- [`docs/eq3_thermal/`](docs/eq3_thermal/README.md): the design and CPU validation status of the
  EQ3 thermal model.
- [`docs/hbfsim-supported-weights/`](docs/hbfsim-supported-weights/README.md): the record of
  native PTX instrumentation of the OLMoE supported weights.
- [`docs/skills/`](docs/skills/): a reading order plus one document per subsystem, for someone new
  to the code.
- [`docs/reference/`](docs/reference/): reference notes, including the CUDA architecture
  compatibility audit.
- [`docs/troubleshooting.md`](docs/troubleshooting.md): error reports and troubleshooting.
- [`TODO.md`](TODO.md): the open roadmap. Each item names the evidence that would close the item.

## Paper and citation

The paper describing HBFSim is [arXiv:2609.09800](https://arxiv.org/abs/2609.09800).

```bibtex
@article{hu2026hbfsim,
  title   = {HBFSim: Fast and Faithful Simulation of High-Bandwidth Flash Under Real GPU Execution},
  author  = {Hu, Yanpeng and Yang, Yiwei and Zhu, Yuanwu and Zheng, Yusheng and Quinn, Andi and Zhang, Wei},
  journal = {arXiv preprint arXiv:2609.09800},
  year    = {2026}
}
```

`CITATION.cff` carries the same entry, which is what makes GitHub render a "Cite this
repository" button on the repository page.

## Contributing

[`CONTRIBUTING.md`](CONTRIBUTING.md) states what a contribution needs, and the one rule that
matters most here: a number may only enter the repository together with the proof that produced
it.

Bug reports and feature requests go through the templates in
[`.github/ISSUE_TEMPLATE/`](.github/ISSUE_TEMPLATE/), a usage question can go to the
[Q&A discussions](https://github.com/SlugLab/HBFSim/discussions/new?category=q-a), and a pull
request follows [`.github/pull_request_template.md`](.github/pull_request_template.md).

Build and test with the quick start above before opening a pull request, and say in the pull
request which of the four kinds of time a new measurement refers to.

Participation is governed by the [Code of Conduct](CODE_OF_CONDUCT.md). Report a vulnerability
through [`SECURITY.md`](SECURITY.md) rather than in a public issue.

## License

HBFSim is released under the Apache License 2.0. The full text is in [`LICENSE`](LICENSE).

## Acknowledgements

HBFSim builds on the following projects. bpftime and MQSim are kept as pinned submodules. bpftime,
released under the MIT license, supplies the interception path the PTX rewriting is derived from.
MQSim, released under an MIT-style license by the SAFARI Research Group at ETH Zurich, supplies
the flash media model HBFSim drives online through an out-of-tree patch.

[hetGPU](https://github.com/vickiegpt/Concordia), derived from ZLUDA and released under Apache-2.0
or MIT, provides the SASS-to-PTX recovery pipeline used by HBFSim to recover selected NVIDIA
kernels for subsequent HBF memory-access instrumentation. We thank the hetGPU contributors for
this foundation.
