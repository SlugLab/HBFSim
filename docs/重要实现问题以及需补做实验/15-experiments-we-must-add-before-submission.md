# Experiments needed before submission

**How to read this file.** This file is a work order, not background reading. Two
items, item 18 and item 19, already have a first set of results, recorded in
`docs/proofs/2026-08-16-density-interleave-nand-sweep.md`; nothing else listed below
has been run yet. If you are an AI assistant working on this repository
on the collaborator's behalf, treat every item as an assigned task: run the item,
record the numbers, and write the result into a checkpoint document under
`docs/proofs/`. The submission deadline is **2026-09-15 23:59 AoE** (FAST '27 fall
round).

**Verification level of every fact quoted below.** A figure tagged `(primary)` was read
off the original document by someone on this project — the OCP `HBF` specification, a
published paper, a vendor page, a proof document under `docs/proofs/`, or the source
tree itself. A figure tagged `(inference)` was computed this round from tagged primary
inputs; it is our own arithmetic, not a figure any source states. An `(inference)`
figure may not be written into the paper as though a source stated it.

**How the order was decided.** Items are ordered by which level of claim each item
supports, and within a level by how much of the claim rests on that item. The items the
core claim rests on come first; the items each of the three challenges C1, C2 and C3
rests on come next, in the order the paper weights the three challenges; then the work
every claim shares; then the sweeps that show how far an answer moves when a device
parameter moves, which support the core claim without being what the core claim rests
on; then the forward-looking scenario and the cost model; then what is future work. The
order does not follow effort, difficulty, or how close an item is to being runnable.
Items are numbered 1 to 23 straight through, so an item can be referred to by number.

**The numbering changed in this revision.** Seven items were added and the whole list
was re-ordered, so every item that existed before has a new number. Other files in this
repository still cite the old numbers; the mapping is:

| Old number | New number | Item |
|---|---|---|
| 1 | 5 | Turn temperature into a service rate |
| 2 | 6 | Thermal state and refresh traffic |
| 3 | 7 | How much the accesses admitted without timing move the reported time |
| 4 | 8 | MoE expert-routing experiment |
| 5 | 9 | Move the injection point from the issue site to the consume site |
| 6 | 10 | How large the overestimate from cache hits is |
| 7 | 11 | Held-out calibration validation |
| 8 | 12 | Split the 164.70x slowdown |
| 9 | 13 | What the single-counter serialization costs |
| 10 | 14 | Extend vLLM coverage |
| 11 | 15 | Re-anchor the timing profiles to the specification |
| 12 | 16 | Machine information to collect |
| 13 | 17 | Accuracy validation against a physical reference platform |
| 14 | 20 | Access pattern and `HBF` parameter sweep |
| 15 | 2 | Prefetch window |
| 16 | 23 | Activation-energy sensitivity sweep |

Items 1, 3, 4, 18, 19, 21 and 22 are new in this revision.

**The claims, in the order the groups below follow.**

- **The core claim.** The bandwidth of `HBF` is an output of a simulation, not a number
  that can be read off a table and configured. Table 2 of the OCP `HBF` specification
  states `Total Effective BW per Cube = 3072 GB/s`, which is 256 GB/s per channel
  across 16 channels at 75% `AXI` link-layer efficiency (primary). That figure
  describes the `UCIe` interface — the die-to-die link between the `HBF` stack and the
  accelerator — and not the rate at which the storage array can supply data. The rate
  the array can supply follows from three quantities: the number of parallel read-out
  units, the page size, and the media latency. The specification never states the
  first of the three. §11.1.1 writes the performance rule as `The maximum performance
  of HBF is achieved when the host system page buffer is managed to transfer "(4KiB *
  N) * all UCIe channels in HBF" at once, where N is the number of banks or planes in
  a die.` and gives no value for N anywhere in the document (primary). Taken with the
  organization Table 3 states, the array-side figure comes to 262.1 GB/s at a
  4 microsecond media latency and 52.4 GB/s at 20 microseconds, which is 11.7 to 58.6
  times below the 3072 GB/s of Table 2 (inference). Every published `HBF` evaluation
  fills the gap with a different assumption, and the assumptions are far enough apart
  to change which way the answer comes out.
- **C3.** The specification sets a junction temperature ceiling for
  `HBF`. Junction temperature is the temperature inside the chip at the point where
  heat is actually generated, normally higher than the temperature of the package
  surface. Above the ceiling, `HBF` stops the temperature from rising further by
  lowering the clock, withholding credits, and finally shutting down. The rate `HBF`
  can sustain over a long run is therefore below the peak the vendor quotes: the three
  bandwidth points in the specification, 0.4 to 3.0 TB/s, are short-term figures. The
  paper claims we compute the sustainable rate during a real execution and apply the
  sustainable rate to every single access.
- **C1.** `HBF` has no interception point outside the program under test, so the
  effect can only be written into the compiled artifact of the program under test.
  Deciding what one access should pay needs four quantities: the byte address of the
  access, the distance between the point where the access is issued and the point
  where the value is consumed, the moment the access actually reaches the media, and
  how many real media requests one memory instruction expands into. No single layer
  holds all four quantities at once, and moving to the `SASS` layer supplies only the
  second quantity. The coverage decision for a kernel launch — whether the launch was
  modeled — therefore has to be taken one launch at a time and put on the record.
- **C2.** How long an access waits is a function of device state, not a property of
  the access itself. When a delay is injected into a real execution, the only thing
  that can be observed is a single sum: the real waiting time holds the modeled media
  latency and the cost of the injection itself at the same time.

**Which items are blocking.** Four items produce the numbers without which the
matching claim is an assertion with no evidence behind it: **item 1** for the core
claim, **item 5** for C3, **item 7** for C1, **item 11** for C2. The paper cannot be
submitted with any of the four missing. Every other item strengthens the paper or
closes a reviewer question; those four decide whether the claim can be made at all.

**Relation to the other files in this repository.**

- `todo/experiments-to-run.md` holds the longer write-up of how to run eight of the
  items below. Each item that came from `todo/experiments-to-run.md` names the item
  number there. Ordering by contribution claim now lives in this file only.
- `docs/proofs/2026-08-16-density-interleave-nand-sweep.md` holds the first results for
  item 18 and item 19, together with the list of what has to be closed before either
  set of results can be quoted in the paper.
- `16-questions-only-you-can-answer.md` holds six questions that only the collaborator
  can settle. Items below that wait on one of the six name the question by number.
- `todo/facts-to-verify.md` holds nine facts that have to be checked before entering
  the paper.

**Fields.** Every item states the same five things: which claim the item supports,
what number has to come out, how to run the item, what the item depends on, and what
happens if the item is not done.

---

# Group 1 — experiments that support the core claim

## 1. Sweep the parallel read-out unit count against the media latency

**Supports.** The core claim. Blocking.

**What number has to come out.** For every one of the 24 combinations in the grid —
parallel read-out unit count per cube in 256, 1024, 1536 and 4,883, crossed with media latency in 1, 2,
4, 5, 10 and 20 microseconds — two figures: the steady-state array read bandwidth, and
the end-to-end throughput of the workload the simulator runs. Third, the place in the
grid where the conclusion changes sign, meaning the boundary on one side of which the
workload runs faster with the weights in `HBF` and on the other side of which the
workload does not.

**How to run.** Two terms first. A **parallel read-out unit** is one place inside the
device that can sense one page out of the storage array independently of the others.
Table 3 of the specification states the organization — `Dies per cube 16 units`,
`Banks per channel 16 units`, `Page size 4096 Bytes`, `Total Cube Size 512 GiB`
(primary) — from which reading the die count alone gives 16 parallel read-out units,
and multiplying dies by banks per channel gives 256 (inference). **Media latency**,
written `tR` in flash datasheets, is the time the storage array needs to sense one page
out of the cells into the page buffer; the specification gives no value for it
(primary).

The identity to implement is `steady-state read bandwidth = parallel read-out units ×
page size ÷ tR`. At 256 units and 4096-byte pages the identity gives 262.1 GB/s at
`tR` = 4 microseconds and 52.4 GB/s at `tR` = 20 microseconds, which is 11.7 to 58.6
times below the 3072 GB/s of Table 2; reaching 3072 GB/s would take 3,000 to 15,000
parallel read-out units (inference).

Where the four read-out unit counts on the first axis come from, and what each of the
four had to be converted from. The identity above computes the bandwidth of one cube —
one stack of dies, the unit Table 2 of the specification counts bandwidth in — so the
first axis is fixed to one unit: the total number of parallel read-out units per cube.
None of the four sources states its figure in that unit, and the assumption each
conversion needs is stated next to it.

| Source | Figure and unit as the source states it | Converted to per cube | Assumption the conversion needs |
|---|---|---|---|
| Table 3 of the specification | `Banks per channel 16 units`, per channel (primary) | 16 × 16 channels = 256 (inference) | one host channel drives one core die |
| The example on page 23 of the specification, §4.6 | `Number of banks/die = 16` together with `Number of dies = 4` (primary) | 16 × 4 × 16 channels = 1024 (inference) | four dies on one channel |
| FlashAccel | 96 planes per die (primary) | 96 × 16 dies = 1536 (inference) | 16 dies per cube |
| The assumption H³ and Micron share | 8 TB/s at `tR` = 20 microseconds, from which the unit count is back-solved (primary for the two inputs) | 8×10¹² × 20×10⁻⁶ ÷ 4096 = 39,062 units, ÷ 8 cubes = 4,883 (inference) | The weakest of the four cells: 4,883 depends on how many cubes were assumed. At 6 cubes the same arithmetic gives 6,510, and neither original states a cube count. |

The specification does not settle how many core dies one host channel drives, and the
three places that touch the question do not agree: §4.3 writes one die per channel, the
example in §4.6 writes four, and the `NCDU` register admits up to sixteen (primary).
That disagreement is the reason the sweep exists — it is a gap in the source document,
not a figure this project failed to look up. The conversion table above has to be
reproduced in the result document, so that a reader can see which per-cube count rests
on which assumption.

Where the six media latency values on the second axis come from. TileLens sweeps 1, 2,
5, 10 and 20 microseconds (primary). The value 4 microseconds is added because it is the
one value with a device-level first-hand source: Kouchi et al., `A 128Gb 1-bit/cell
96-word-line-layer 3D flash memory to improve the random read latency with tProg=75 μs
and tR=4 μs`, IEEE Journal of Solid-State Circuits, which carries both figures in its
title (primary). The value 20 microseconds comes from H³, which states in its own text
that the value is an input assumption expected to improve once the device is
commercial; the H³ original sits behind a subscription wall and only a Chinese
translation was available to us, so 20 microseconds is second-hand on our side.

What the four published evaluations do with the gap, which is the reason this sweep
exists:

- H³ and Micron do not model the storage array at all, and both assume 8 TB/s at
  `tR` = 20 microseconds (primary).
- TileLens applies a fixed 2.5x scalar. Because TileLens sweeps `tR` from 1 microsecond
  to 20 microseconds while holding that scalar fixed, the fixed scalar means the
  read-out unit count is being scaled up by 20x across TileLens's own sweep
  (inference).
- FlashAccel models the array end to end, with 96 read-out units and `tR` = 4
  microseconds written into the model (primary).
- Feeding the assumption H³ and Micron share back through FlashAccel's own area
  parameters, the NAND array die area the assumption requires is 448 mm², against the
  149 mm² FlashAccel actually uses, a factor of 3.01 (inference).

**Depends on.** The timing profiles take bandwidth as an input field today, so the
identity has to become the way a profile computes its bandwidth instead of a value
typed in: see `configs/profiles/`, where the `aggressive` profile currently caps
bandwidth at 1 TB/s with 1 TiB of capacity (item 15). The die-count sweep of item 19
already moves a closely related quantity — dies per channel at fixed capacity and fixed
channel count — and its harness can be reused; item 19 holds the media latency fixed and
reports p50 and p99 latency, whereas this item needs steady-state bandwidth and
end-to-end throughput across a media-latency axis as well.

**If not done.** The core claim has no evidence behind it: the paper says the bandwidth
figure is an output of a simulation while every number in the paper still comes from a
bandwidth value someone typed into a profile, and a reviewer who opens
`configs/profiles/` finds the typed-in value.

## 2. Prefetch window: how much of the media latency the schedule hides

**Supports.** The core claim. Detailed write-up: item 8 of
`todo/experiments-to-run.md`. This item was item 15 of the previous revision, where it
supported no claim directly; the re-ordering moved it here because the paper answers the
latency objection to `HBF` with the lead time the decode schedule provides, and this
item is the direct evidence for that answer. Item 8 runs the Mixture-of-Experts version
of the same comparison; this entry is the general one, run on the same workload and
profile with nothing else changed.

**What number has to come out.** At each media latency value item 1 sweeps — 1, 2, 4, 5,
10 and 20 microseconds — the residual stall fraction: of the media latency, how much is
covered by issuing the read one layer ahead of the layer that needs the data, and how
much is left over as a stall in the real execution. Alongside it, the timing cost of the
`HBF` layer under both fetch strategies for the same workload and profile.

**How to run.** Change the fetch strategy from on-demand reads that rely on
concurrency to hide latency, to prefetching one layer ahead, with everything else held
constant, and quantify how the timing cost contributed by the `HBF` layer changes.

**How much lead time there is to work with.** Every figure in this paragraph is
inference, computed from published model configurations and a published peak bandwidth.
Llama 3 8B has 32 layers, hidden dimension 4096, feed-forward dimension 14336 and 8
key-value heads; in bfloat16 that is 436.2 MB of weights per layer, and on an H100 SXM5
at 3.35 TB/s of HBM bandwidth, reading one layer at peak bandwidth takes 130.2
microseconds. Those 130.2 microseconds are the window available for reading layer i+1
while layer i computes: 6.5 times a 20-microsecond media latency, and 32.6 times a
4-microsecond one. Further ahead the window is longer still, because every address for
all 32 layers is fixed the moment a token starts computing: the last layer's weights are
known 4.04 milliseconds in advance, against a whole-token decode time of 4.17
milliseconds, which is 202 times a 20-microsecond media latency. Against a single GPU
access to its own HBM, measured at 272.8 ns on an H800 (478.8 cycles at 1755 MHz; Luo et
al., `Benchmarking and Dissecting the Nvidia Hopper GPU Architecture`, arXiv:2402.13499,
Table IV and Table III) (primary), the lead time is two to four orders of magnitude
larger.

**The exception has to be measured separately, not folded into the average.** In a
Mixture-of-Experts model the router decides which experts a token activates, and the
decision is taken inside the same block that then uses the experts, so the lead time is
zero rather than short: Pre-gated MoE, arXiv:2308.12066, states in its own text that
selecting the experts and using the experts sit next to each other inside one block
(primary). Of the weight bytes one Mixtral 8x7B token reads, 89.4% cannot have their
addresses known until that layer's router has run; for DeepSeek-V3 the figure is 60.4%
(inference).

**Depends on.** The fetch strategy inside the simulator; no new hardware.

**If not done.** The paper's answer to the latency objection rests on arithmetic with no
measurement behind it, and question Q9 in the paper outline — how much of the `HBF`
access latency a prefetch window is worth — goes unanswered. The question originates
from an industry analysis article, which speculates that a statically scheduled
architecture that hides latency by compiler-arranged prefetching suits `HBF` better than
a GPU that hides latency with many concurrent threads. We test that speculation rather
than citing the speculation as a result. Bound to state in the paper: the experiment
runs on an NVIDIA GPU and changes a fetch strategy inside the simulator, and is not a
measurement on a TPU, so the conclusion may state what a prefetch window is worth for
the `HBF` layer, and may not state that a TPU suits `HBF` better.

## 3. Batch size against the sparsity dividend

**Supports.** The core claim. How much bandwidth the workload demands is the other half
of the comparison item 1 sets up, and for a Mixture-of-Experts model the demand depends
on the serving batch size, not only on how sparse the model is.

**What number has to come out.** On a Mixture-of-Experts model, at batch sizes 1, 8, 32,
128 and 256: the measured fraction of experts a batch actually touches — the union
across the tokens in the batch, not the per-token count — and the weight bytes read per
decode step. Report the measured fraction against the prediction of
`1 − (1 − k/E)^B`, where E is the number of experts, k the number of experts each token
selects and B the batch size, and report the deviation between measurement and
prediction.

**How to run.** Sweep the batch size on the Mixture-of-Experts model already running in
the live vLLM path — Qwen3-30B-A3B, described in item 8 and item 14 — recording for each
decode step which experts were touched. The prediction the measurement is compared
against assumes every token routes independently and uniformly. For DeepSeek-V3's 256
experts with 8 selected per token, the prediction is 3.1% of experts touched at batch 1,
22.4% at batch 8, 63.8% at batch 32 and 98.3% at batch 128 (inference). Under the same
assumption, the ratio of dense to Mixture-of-Experts bytes per decode step is 2.88 at
batch 1, 1.30 at batch 8, 1.04 at batch 32 and 1.00 at batch 128 (inference): the
sparsity advantage is gone by batch 32. Micron's peak benefit point is b=256, and the
operating points Micron evaluates are `batch ∈ {4, 8, 16, 32, 64, 128, 256}` (primary).

**What the measurement is for.** The truth lies between two bounds. If all tokens in a
batch route to the same experts, the batch touches as many experts as a single token
does; if tokens route independently and uniformly, the batch touches the fraction the
formula predicts. The two bounds are 24 times apart (inference), and no model
configuration narrows the gap — only a real routing trace does. That is why the item
measures rather than computes.

**Depends on.** A real routing trace from the live vLLM path. No new hardware.

**If not done.** The paper's capacity argument rests on a sparsity figure that holds at
batch 1 and has disappeared by batch 32, and the batch size at which Micron reports its
peak benefit, b=256, sits inside the range where the sparsity advantage is gone under
the independent-routing assumption. A reviewer who does the arithmetic asks a question
the paper did not answer.

---

# Group 2 — experiments that support claim C3

## 4. Read disturb and retention refresh: the bandwidth and the erase cycles they consume

**Supports.** C3 and the core claim. C3, because the refresh loop and the
temperature-driven retention deadline are the same loop. The core claim, because an
endurance ceiling is a second route by which the sustainable bandwidth turns out to be
an output rather than a configured value.

**What number has to come out.** For a layer of `HBF` that is only ever read: how many
erase cycles ten years of operation consume, and how much of the available bandwidth the
refresh traffic takes away. Sweep two parameters: the read-disturb threshold, meaning
how many reads of a block force that block to be refreshed, and the refresh interval.

**How to run.** Two mechanisms have to be in the model, and the specification writes
both of them out.

First, read disturb. Reading one cell requires putting a pass voltage on the other cells
in the same block so that those cells conduct; the high voltage tunnels electrons in and
pushes the threshold voltage of the other cells upward (Cai, Ghose, Haratsch, Luo and
Mutlu, Proceedings of the IEEE 2017) (primary). The only repair is erase followed by
reprogram, because programming can only add charge and read disturb also shifts in the
add-charge direction, and erase works on a whole block at a time because the transistors
of one block share a single substrate (primary). The specification writes the chain out:
reads past the threshold raise a `CECC`, a block-level refresh must follow, writing the
first page of a block automatically erases the whole block, and `HBF` performs no
garbage collection and moves no data, so the erase cycles are charged to the host (§11.5
and Table 33) (primary).

Second, a refresh floor that does not depend on read or write volume at all: the
specification states a refresh `typically every 24–48 hours` (primary). Against the
specification's own 10-year life, that floor alone costs 1,826 to 3,653 erase cycles on
a device that is never read and never written (inference).

Published read-disturb thresholds, all measured on planar NAND (primary): about 1
million reads per block for single-level cells, about 100,000 for first-generation
two-level cells, and as low as 20,000 for 20 to 24 nanometre modern two-level cells.

The strongest published counter-evidence has to be run inside the same sweep rather than
left out of it: Luo et al., POMACS 2018, measured that a single read disturbs 3D NAND
96.7% less than 20 to 24 nanometre planar NAND, and wrote that 3D NAND read disturb is
weak enough not to need a dedicated correction mechanism (primary).

The line to compute is the average read bandwidth a 512 GiB stack can sustain for ten
years: `MAXPEC × X × capacity ÷ (pages per block × 10 years)`, where X is the
read-disturb threshold in reads per block and `MAXPEC` is the rated erase cycles. At 768
pages per block and `MAXPEC` 100,000, X = 1,000,000 gives 227 GB/s, which is 7.4% of the
rated figure, and X = 3,000,000 gives 680 GB/s, which is 22% (inference).

**One route out that has to be shown not to work, and one that may.** For a dense model
read in full on every token, holding weights in HBM does not help: cutting the reads that
reach NAND by a factor of k requires HBM to hold `1 − 1/k` of the weights, so a factor of
10 requires 90% resident and a factor of 13.5 requires 92.6%, at which point `HBF` holds
7.4% of the model (inference). The two 4 KiB cache buffers the specification gives each
bank come to 2 MiB in total, which is 0.00038% of capacity, and their hit rate on a dense
full read is zero because each byte is consumed once per pass; their actual function is
to merge the 64 host read commands that make up one page into a single array sense
(primary for the buffer count and the 64-command page, inference for the two
percentages). Pinning the hot experts of a Mixture-of-Experts model in HBM is a
different case, and it is the case the sweep should quantify against the line above.

**Depends on.** Neither a retention model nor a read-disturb model exists in the code:
the source search reported in item 5 returns zero hits for `retention` as well as for the
thermal terms. Item 23 sweeps the activation energy that sets the retention deadline and
can be run in the same pass.

**If not done.** The paper reports a bandwidth for a device whose media wears out from
being read, with no statement of how much of the bandwidth the wear-out costs, and no
answer for a reviewer who asks whether a read-only `HBF` layer survives ten years.

## 5. Turn temperature into a service rate, and report the throughput difference

**Supports.** C3. Blocking for C3.

**What number has to come out.** For one workload, the long-run throughput under two
settings: service rate that does not change with temperature, and service rate that
steps down with temperature according to the thermal states in the specification.
Report both throughput figures and the difference between the two figures.

**How to run.** The place to implement the connection is the fast path — the timing
model that runs on the GPU itself and holds the kernel in a spin-wait on
`%globaltimer` until the modeled delay has elapsed. The delay is paid out in
`/root/hbfsim/HBFSim/src/cuda_runtime/device/hbf_device.cu` at lines 413 to 423 for
the parameterized model and at lines 364 to 374 for the measured-curve model, and the
rate follows from `base_latency` plus
`fast_transfer_ns(bytes, aggregate_bandwidth_bytes_per_s)`. Making those two
quantities depend on the current temperature state is the whole change. The measured
thermal calibration in `configs/thermal/gpu-cd8p-logp-live.json` can drive the
temperature trace: GPU-side ambient 28.0 degrees C, steady state 73.5780194293642,
time constant 13.1 s, root-mean-square error in degrees C 2.1870756397312805; compute
throughput falls 0.3280167842295832 TFLOPS per degree C, from 387.7606615946093 cold
to 373.31763045574144 hot, a drop of 3.7247283103636675%, over 1292 samples, with
`"checksum_exact": true`.

**Depends on.** Code that does not exist yet. Searching `src/` and `include/` for
thermal, temperature, junction, retention, arrhenius and activation_energy returns
zero hits: no line of code turns a temperature reading into an effect on access
latency. What already exists is four scripts under
`/root/hbfsim/HBFSim/scripts/thermal/` — `collect.py` samples real telemetry,
`fit_logp.py` fits a first-order thermal response, `gpu_heat.py` is a sustained BF16
matrix-multiply heater, and `simulate_overheat.py` extrapolates the calibration
result to the named scenarios and computes the threshold-crossing time — two
configuration files under `configs/thermal/`, and a test named `thermal_logp` defined
at `CMakeLists.txt` lines 273 to 278.

One hard constraint has to be written into the result. The detailed MQSim path — the
route through the detailed flash device simulator this project adapts and runs as the
reference path — cannot follow temperature at all. `src/mqsim_adapter/mqsim_online.cpp`
sets the media parameters once, in `configure_mqsim` at lines 35 to 77; the parameters
take effect when `SSD_Device` is constructed at line 126, and there is no route to
change any of the parameters afterwards. Which of the three ways out to take is a
decision for the collaborator: see item 3 of `16-questions-only-you-can-answer.md`,
which lists the three options with their costs, and item 4 of the same file, which
records that no consistency check between the fast path and the detailed MQSim path
exists in the code today.

**If not done.** C3, the core claim, has no mechanism behind it: temperature is
asserted to change the answer, and nothing in the code lets temperature change the
answer. Item 5 of `16-questions-only-you-can-answer.md` asks the collaborator who
writes the code and by what date, and asks what weaker statement C3 becomes if the
date passes.

## 6. Thermal state and refresh traffic

**Supports.** C3. Detailed write-up: item 3 of `todo/experiments-to-run.md`.

**What number has to come out.** A curve of threshold against cost, reporting
throughput loss, p99 latency increase, and the fraction of bandwidth consumed by
refresh.

**How to run.** Sweep the LTT and STT thresholds — the temperatures at which the
light and the heavy throttling state defined in the specification are entered — and
sweep the refresh period. Refresh here means the host rewriting data whose retention
deadline is approaching, so that the stored charge returns to full level; the rewrite
is extra write traffic the workload did not ask for. Report the cost in throughput
and in tail latency.

**Depends on.** The thermal state machine and the retention and refresh loop inside
the simulator, neither of which exists yet — the same missing code named in item 5 —
plus a thermal trace. The measured first-order fit is already available: time
constants 13.1 s for the GPU and 12.4 s for the CD8P
(`docs/proofs/2026-08-11-hybrid-complete.md`). Two of the values that define the
scenarios are still waiting on the collaborator: the two temperature thresholds in
the `simulated-warning` scenario (item 1 of `16-questions-only-you-can-answer.md`,
where the GPU-side threshold 83.0 is the CD8P SSD's own critical temperature) and the
SSD-side extrapolation multiplier of 14.0 (item 2 of the same file). The thresholds
themselves currently rest on one Chinese-language secondary article, so every
threshold would have to be marked second-hand in the paper until item 6 of
`16-questions-only-you-can-answer.md` is closed.

**If not done.** The reviewer question "what conclusion actually changes once
temperature is in the model?" has no answer, and temperature is only asserted to
matter. Note on scope: the threshold half of the sweep is what claim C3 needs. The
refresh-period half fed the retention discussion, which sits in the discussion and
future-work sections of the paper; item 4 of this revision moves the retention refresh
cost back into the submission, so the refresh-period half now feeds item 4 and the two
halves should be run in one pass.

---

# Group 3 — experiments that support claim C1

## 7. How much the accesses admitted without timing move the reported time

**Supports.** C1. Blocking for C1.

**What number has to come out.** The same workload run three ways — every covered
access timed, part of the covered accesses timed, which is the situation today, and
no access timed — and the spread in end-to-end time across the three runs.

**How to run.** Two live runs already exist, and the numbers of the two runs must
never be mixed. In the first run, out of 23,210 coverage decisions, 12,626 touched no
registered memory, 10,584 touched registered memory but were admitted without timing —
admitted meaning the access was allowed to proceed and was put on the record as not
timed — because the module was available only as an already compiled binary, and 0
accesses were timed successfully. Source: `docs/proofs/2026-08-11-vllm-timing-adapter.md`.
In the second run, `PTX` was taken out of the Triton cache, rewritten, and put back
before the run, which bound 4 variants exactly; the second run made 10,339 coverage
decisions, launched `fused_moe_kernel` 2,304 times, and timed 24 of those launches
successfully. Source: `docs/proofs/2026-08-11-vllm-exact-live-delay.md`.

**Depends on.** No new hardware. A way to force the two extreme settings on the same
workload: timing for every covered access, and timing for none.

**If not done.** The coverage decision stays a procedure for disclosing honestly what
was and was not modeled, and does not become a solution. The reviewer question left
standing is: 10,584 accesses were admitted without timing, so how far off is the time
being reported?

## 8. MoE expert-routing experiment

**Supports.** C1. The experiment takes the coverage decision from a 16,384-byte
prefix to whole expert weight tensors inside a real execution, which is the evidence
that the per-launch coverage decision works on a workload people care about. The
second step of the experiment, the fetch-strategy comparison, is the
Mixture-of-Experts version of item 2. The batch-size sweep of item 3 runs on the same
model and the same live path and should be collected in the same pass.

**What number has to come out.** The timing cost contributed by the `HBF` layer under
both fetch strategies, together with a statement of how many bytes of expert weights
the run covered.

**How to run.** Two steps.

First, widen the registered range **in batches** to the expert weight tensors of
several layers. Do not attempt full registration in one go. Change the two parameters
`--hbf-parameter-regex` and `--hbf-range-bytes` in `adapters/vllm/run_timing.sh`. The
current command uses `'^model\.layers\.0\.mlp\.experts\.w13_weight$'` and `16384`.
The wrapper exits with code 70 when a run produces no `modeled: true` decision, so a
run that appears to succeed without modeling anything cannot slip into the results.

Second, compare the timing cost contributed by the `HBF` layer under two fetch
strategies. One strategy is the real situation: which expert to read is decided by the
router at run time, so there is no window in which the data could have been fetched in
advance. The other strategy is the ideal situation: assume the identity of the expert
is known ahead of time, so the data can be moved into the buffer before the data is
needed — that interval is what the industry analysis article calls a prefetch window.
The difference between the two strategies is what "the router is not predictable in
advance" costs on this layer of device.

**Known risk.** Once the registered volume grows, the burst of synchronous requests
during the profiling pass may come back. Try one layer of expert weights first,
confirm the burst does not come back, and only then add more layers.

**Depends on.** The vLLM adapter and the same Qwen3-30B-A3B setup as the existing
proof, with no new hardware. The model used in the existing live proof, Qwen3-30B-A3B,
is itself a Mixture-of-Experts model: each layer holds several expert sub-networks,
and every token activates only a few of the sub-networks. The expert kernel is already
wired into the simulator. `fused_moe_kernel` has 4 Triton variants bound, is launched
2,304 times, of which 24 launches carry modeled accesses; the emitted token IDs match
the baseline. Baseline generation takes 0.269996 s, generation with timing injection
takes 44.469084 s. Evidence: `docs/proofs/2026-08-11-vllm-exact-live-delay.md`. So the
model already runs end to end, and what is missing is coverage: today only the first
16,384 bytes of `model.layers.0.mlp.experts.w13_weight` are registered. Registering
all 61,064,245,248 bytes at once has been shown not to work — full registration makes
vLLM's profiling pass emit an impractically large number of synchronous requests.

**If not done.** The industry analysis article marks Mixture-of-Experts as the one
unsolved case on `HBF`: expert weights are read-only and large, which fits `HBF`, but
which expert is activated is decided by the router at run time, so what to read is not
known in advance and prefetching cannot be done. These numbers are ours alone —
nobody else has a tool that can impose this device's timing inside a real execution.
Without the numbers, the paper leaves the one case the field is asking about
unanswered. Source: `docs/ref_article/zhihu2026-hbf-protocol-and-market-analysis-cn.txt`,
an industry analysis opinion piece and therefore a second-hand source, not a
specification.

## 9. Move the injection point from the issue site to the consume site, and measure the difference

**Supports.** C1 and C2. The distance between the point where an access is issued and
the point where the value is consumed is the second of the four quantities named in
C1, and the size of the overestimate the current injection point produces is exactly
the kind of quantity C2 says cannot be read off a single measurement.

**What number has to come out.** End-to-end time for the same workload under two
injection points — at the issue site, which is what the code does today, and at the
consume site — with the difference between the two times standing as the calibration
of how large the overestimate is.

**How to run.** What the code does today has been verified.
`/root/hbfsim/HBFSim/src/ptxpass_hbf/transform.cpp` lines 125 to 192 place the call
before the original memory access instruction, and line 189,
`replace_address(*operation, address)`, replaces the address operand of the original
memory access instruction with the return value of the call. The replacement builds a
real data dependency, so ptxas cannot move the memory access earlier. The current
implementation therefore blocks synchronously at the issue site, which converts a read
that could have been asynchronous and covered by other instructions into a stall in
place.

The design decision to implement and measure: move the injection to the consume site,
meaning immediately before the first use of the destination register, for the
timing-only mode, which charges a delay and hands back the original address. The
capacity mode, which hands back a different address inside the HBM page cache, has to
stay at the issue site, because a correction made after the memory access has already
happened arrives too late.

**Depends on.** A change to the `PTX` pass, plus one bound that has to be stated in
the paper: at the `PTX` layer only an approximation of the consume site can be
reached, because where the hardware finally stalls is decided by the wait-barrier mask
ptxas writes into the control bits. The error of the chosen position therefore has to
be measured and reported, and precision must not be claimed for the position.

**If not done.** Every `HBF` cost the paper reports carries an overestimate of unknown
size, and a reviewer who reads the pass can name the mechanism that produces the
overestimate.

## 10. How large the overestimate from cache hits is

**Supports.** C1.

**What number has to come out.** How much of the charged access volume would never
have reached the media on a real machine, and the resulting overestimate in the
reported `HBF` cost, with the direction of the error stated.

**How to run.** Not decided yet. What is fixed is what has to come out, stated above;
what is open is how a last-level cache hit rate for the covered kernels is obtained on
this GPU. That decision is missing and has to be made before the item can be run.

**Depends on.** Nothing in the code models cache hits. Searching
`/root/hbfsim/HBFSim/src/cuda_runtime/` for `cache_hit`, `l2_`, `hit_rate`,
`page_cache` and `resident` returns zero hits. The timing-only mode today charges
every supported memory access that falls inside the registered range, while on a real
machine only the accesses that miss the last-level cache reach the media.

**If not done.** A known source of overestimate, with a known direction, goes into the
paper unquantified.

---

# Group 4 — experiments that support claim C2

## 11. Held-out calibration validation

**Supports.** C2. Blocking for C2. Detailed write-up: item 1 of
`todo/experiments-to-run.md`.

**What number has to come out.** A table of held-out points with error percentages,
of a magnitude that can be compared with the 7.85%–13.19% error range CXLMemSim
reports.

**How to run.** The `cd8p-vmem-p50` profile currently reproduces measurement exactly
at six breakpoints: 1 page at 11,133 ns, 4 pages at 41,495 ns, 16 pages at
168,606 ns, 64 pages at 2,824,351 ns, 256 pages at 10,767,793 ns, and 512 pages at
20,254,374 ns. All six breakpoints were used to fit the curve and none was held back.
Hold one page count out of the fit, predict the held-out page count with the fitted
empirical curve, and report the error as a percentage against measurement. Measure
additional transfer sizes outside those six page counts as well.

**Depends on.** The CD8P device and the calibration harness that produced the existing
curve. No new hardware.

**If not done.** The claim that the measured curve beats a parameter table plus a
formula rests on nothing an outsider can check. The reviewer question is: "your six
breakpoints have zero error — is that because those six points are the ones you
fitted?" `docs/proofs/2026-08-11-cd8p-vmem-tuning.md` states in its own words that the
zero-error result is a constructed deterministic calibration check, not
cross-validation. The comparison already available is the deviation of the analytical
model, which runs fast by 10.11% at 1 page, 75.82% at 4 pages, 93.99% at 16 pages,
99.63% at 64 pages, 99.89% at 256 pages and 99.93% at 512 pages, and a constant-page
model that is still 71.86% off at 512 pages.

## 12. Split the 164.70x slowdown into modeled delay and emulator overhead

**Supports.** C2. Detailed write-up: item 2 of `todo/experiments-to-run.md`.

**What number has to come out.** How much of the observed time is modeled media delay
and how much is overhead of the emulator itself.

**How to run.** At minimum on a microbenchmark — a small measurement of one operation
or one component rather than a full application — separate the modeled media delay
from the per-warp request-path overhead of the emulator. Two workable methods: an
identity-injection control run, which registers the range, injects zero delay, and
measures what remains; or a `time_scale` sweep, which varies the modeled delay while
the request path stays constant.

**Depends on.** No new hardware. The existing vLLM path plus a microbenchmark that can
be run with injection disabled.

**If not done.** The reviewer question "of that slowdown, how much is the device you
are modeling and how much is your tool being slow?" has no answer. The current figure
is 164.70x, from a baseline generation of 0.269996 s against 44.469084 s with timing
injection. `docs/proofs/2026-08-11-vllm-exact-live-delay.md` states that the two
components are reported together and that separating the two components is future
work. If the split cannot be produced, the qualification from the proof document goes
into the paper verbatim.

## 13. What the single-counter serialization costs against the declared channel count

**Supports.** C2. Serializing all traffic on one counter is a statement about device
state, and C2 is the claim that the waiting time follows device state.

**What number has to come out.** The direction and the size of the deviation caused by
serializing all `HBF` traffic on one counter, measured against the channel count the
timing profiles declare.

**How to run.** Producing the number requires a version of the fast path in which the
counter is kept per channel, so that the single-counter result and the per-channel
result can be compared on the same workload and the same profile.

**Depends on.** A change to the fast path. What the code does today has been verified:
the device-side fast path serializes all `HBF` traffic on one global scalar,
`fast_channel_tail_ns`, declared at `/root/hbfsim/HBFSim/src/host_service/control_layout.hpp`
line 112 and updated with compare-and-swap in
`/root/hbfsim/HBFSim/src/cuda_runtime/device/hbf_device.cu` at lines 349, 355, 393 and
400, while the timing profiles declare 16, 32 or 64 channels with 8 dies each.

**If not done.** A reviewer who opens the source finds a single global counter behind
a paper that describes a many-channel device, and the paper has no statement of what
the simplification costs.

---

# Group 5 — the shared foundation under every claim

## 14. Extend vLLM coverage

**Supports.** Every claim, the core claim included: every number the claims rest on
comes out of the same live vLLM run, so how much of the model was covered bounds all of
them. Detailed write-up: item 5 of `todo/experiments-to-run.md`. The batch widening
described in item 8 is the same work; the entry is kept here because the reviewer
question about coverage is asked of the whole paper, not only of the
Mixture-of-Experts result.

**What number has to come out.** Either coverage reaches one full layer, or the paper
argues explicitly why the selected range is representative.

**How to run.** Coverage is currently one 16,384-byte prefix of
`model.layers.0.mlp.experts.w13_weight`. Extend the coverage to at least one full
layer of weights. Registering all 61,064,245,248 bytes is not a route: per
`docs/proofs/2026-08-11-vllm-exact-live-delay.md`, full registration makes vLLM's
profiling pass emit an impractically large number of synchronous requests. Two
candidate routes are registering in batches and bypassing the profiling pass.

**Depends on.** The vLLM adapter and the same Qwen3-30B-A3B setup as the existing
proof.

**If not done.** The reviewer question is: "you modeled 16 KB out of 61 GB — on what
grounds is this a real workload?" If the extension cannot be done within a week,
convert the extension to a stated limitation in the paper rather than carrying the
item to the deadline.

## 15. Re-anchor the timing profiles to the three bandwidth points in the specification

**Supports.** Every claim, because every sweep in this file is run on the timing
profiles. Detailed write-up: item 7 of `todo/experiments-to-run.md`.

**What number has to come out.** Three profiles carrying bandwidth and capacity
derived from the specification, and items 6, 20 and 23 re-run on the re-anchored
profiles.

**How to run.** The `aggressive` profile is currently capped at 1 TB/s with 1 TiB of
capacity. The specification's three points are approximately 0.4 to 3.0 TB/s per
stack, with 512 GB per stack, 16 channels, and 256 GB/s per channel at 32 GT/s.
Re-anchor the three profiles to those figures and re-run the sensitivity experiments
on the re-anchored profiles.

**What re-anchoring is and is not, after item 1.** The three bandwidth points describe
the `UCIe` interface: Table 2 of the specification states `Total Effective BW per Cube =
3072 GB/s`, which is 256 GB/s per channel across 16 channels at 75% `AXI` link-layer
efficiency (primary). The rate at which the storage array can supply data is a separate
quantity, and item 1 computes it from the read-out unit count, the page size and the
media latency. A profile re-anchored to the interface figure alone therefore states the
interface rate, not the rate `HBF` can deliver; the profile has to carry both, and the
paper has to name which of the two each reported figure is.

**Depends on.** The specification numbers are currently second-hand; see item 1 of
`todo/facts-to-verify.md` and item 6 of `16-questions-only-you-can-answer.md`.
Re-anchoring can proceed with the second-hand values, but the paper must not present
the values as verified against the primary source until that item is closed.

**If not done.** The reviewer question is: "do your conclusions hold only under
parameters you invented?"

## 16. Machine information to collect

**Supports.** Every claim: the implementation section and the evaluation section
both quote the machine, and both sections have to be filled from one collection pass,
not from two separate passes.

**What number has to come out.** Every `[todo]` placeholder in the paper outline that
can only be filled by reading the machine the experiments run on.

**How to run.** Collect all of the following in one pass:

- Host CPU model and core count
- Host memory capacity and frequency
- Measured host memory access latency
- Last-level cache size
- Operating system distribution and version
- Linux kernel version
- GCC version

**Depends on.** Access to the machine the experiments run on. Already known, do not
collect again:

- GPU: NVIDIA RTX PRO 6000 Blackwell Server Edition, driver 595.84, compute
  capability 12.0, 97,887 MiB
- CUDA toolkit 13.0.88; PTX assembled with the ptxas from CUDA 12.8
- Reference device: Dell DC NVMe CD8P E3.S 1.92TB, serial `7EU0A01P0XK1`,
  PCIe 5.0 32 GT/s x4, on NUMA node 1

**If not done.** The paper carries `[todo]` placeholders into the submission, and the
evaluation section cannot state what the numbers were measured on.

## 17. Accuracy validation against a physical reference platform

**Supports.** Every claim, by bounding how far the modeled latency departs from a
physical device rather than from a calibration of the same device.

**What number has to come out.** Prediction error against a physical reference
platform built from stacked CXL SSDs.

**How to run.** Device count, model, and interconnect are `[todo]`.

**Depends on.** Hardware that is not in hand. Nothing else in this file is blocked on
the same hardware.

**If not done.** The accuracy statement in the paper rests on the CD8P calibration and
on the held-out validation of item 11, and the paper has to say so.

---

# Group 6 — sweeps of the device parameters the bandwidth figure depends on

## 18. NAND cell-type sweep

**Supports.** The core claim, as one of the device parameters the bandwidth figure is
an output of. The core claim does not rest on this item — item 1 carries it — which is
why the item sits after the blocking work. A first set of results already exists.

**What number has to come out.** p50 and p99 latency for each of the four cell types,
together with the statement — in the paper, not only in this file — that the per-level
latencies driving the sweep are assumed rather than measured.

**How to run.** Four profiles are already built and wired end to end,
`configs/profiles/nand-sweep/hbf-nand-{slc,mlc,tlc,qlc}.json`, driven by
`scripts/run_density_interleave_nand_sweep.py`. The first results, at 4,096 random
16 KiB reads per case, `--arrival-gap-ns 0`, queue depth 64, seed 42, 1 TiB capacity and
16 channels (primary, `docs/proofs/2026-08-16-density-interleave-nand-sweep.md`):

| technology | read LSB/CSB/MSB/TSB (ns) | P50 (ns) | P99 (ns) |
|---|---|---|---|
| SLC | 25000/25000/25000/25000  | 946,300   | 2,367,640 |
| TLC | 25000/50000/75000/75000  | 948,250   | 3,100,740 |
| MLC | 25000/25000/75000/75000  | 1,696,300 | 4,474,140 |
| QLC | 25000/50000/75000/100000 | 1,994,070 | 5,457,210 |

**Two statements have to travel with that table into the paper.**

First, the four per-level latencies for each cell type are assumed values, extrapolated
by even steps from the MLC and TLC constants MQSim itself ships (75,000 ns read and
750,000 ns program for the slowest level). The values are neither measured nor cited
(primary).

Second, MLC lands above TLC, which reads backwards for "more bits per cell is slower",
and the cause is MQSim's own mapping from physical page to latency level: MLC uses
`pageID % 2`, so half of all physical pages take the slow MSB latency, while TLC uses
the non-uniform formula of Yaakobi et al. 2012, which assigns the slow MSB latency to a
smaller fraction of pages (primary). Because the fraction of pages sitting at the slow
level differs between the two, the MLC, TLC and QLC absolute figures are not a
same-fraction comparison. The one clean comparison among the four is SLC against QLC —
one level against four, the most different in kind — where QLC is 2.1 times the p50 and
2.3 times the p99 of SLC (primary).

QLC required patching MQSim itself, `patches/mqsim/0002-qlc-support.patch`; stock MQSim
ships only SLC, MLC and TLC, and the patch's `pageID % 4` physical-page round robin is
our own simplification in the same spirit as MQSim's existing MLC simplification, not a
published QLC page layout (primary).

**Depends on.** Nothing new to run the sweep again. Before the table enters the paper,
either a citable source for the four per-level latencies is found, or the paper states
plainly that the four values are an assumed step function, following the same discipline
already used for the activation energy in item 23.

**If not done.** Nothing breaks, but the sweep already exists, so leaving it out gives
up a sensitivity axis the paper could have for free. Using the table without the two
statements above would make a QLC latency claim the numbers cannot support.

## 19. Die count and address-interleaving-order sweep

**Supports.** The core claim, as the parallel-unit axis of item 1 measured on the
detailed reference path. The core claim does not rest on this item — item 1 carries it.
A first set of results already exists, and two questions have to be closed before any of
the results can be written as a property of `HBF`.

**What number has to come out.** p50 and p99 latency against dies per channel at fixed
capacity and fixed channel count, and against the address-interleaving order — plus, for
the die-count trend, the name of the code component that produces the trend.

**How to run.** Both sweeps are already wired, `configs/profiles/density-sweep/` and
`configs/profiles/interleave-sweep/`, both driven by
`scripts/run_density_interleave_nand_sweep.py`. First results at 1 TiB capacity and 16
channels, 4,096 random 16 KiB reads, queue depth 64, seed 42 (primary,
`docs/proofs/2026-08-16-density-interleave-nand-sweep.md`):

| dies_per_channel | total dies | capacity/die | P50 (ns) | P99 (ns) |
|---|---|---|---|---|
| 2   | 32   | 32 GiB  | 731,980   | 1,691,920 |
| 4   | 64   | 16 GiB  | 752,940   | 1,800,780 |
| 8   | 128  | 8 GiB   | 771,300   | 1,922,640 |
| 16  | 256  | 4 GiB   | 811,270   | 2,111,440 |
| 32  | 512  | 2 GiB   | 906,970   | 2,405,040 |
| 64  | 1024 | 1 GiB   | 1,089,760 | 2,820,500 |
| 128 | 2048 | 512 MiB | 1,401,070 | 3,369,680 |

Monotonic in both p50 and p99, and checked at three seeds — 42, 7 and 123 — for the two
extremes and the midpoint, where the ordering and roughly the same magnitudes hold: at 2
dies per channel p99 runs 1,663,810 to 1,694,520 ns, and at 128 dies per channel p99
runs 3,348,720 to 3,372,930 ns (primary).

**Two problems have to be solved before the numbers go into the paper.**

First, the direction is the opposite of the naive reading that more dies mean more
parallelism, and the mechanism has not been traced in the code. The candidates named in
the proof document are `Flash_Block_Manager` bookkeeping cost per die, `TSU_OutOfOrder`
per-die scheduling overhead, and the `Address_Mapping_Unit_Page_Level` mapping
arithmetic scaling with die count (primary). Until one of the candidates is confirmed in
the source, the result can be written only as a limitation of the model, not as a
property of `HBF`.

Second, the profiles used for the sweep set a 16,384-byte page while the specification
writes `Page size 4096 Bytes` (primary). Re-run the sweep at 4096 bytes before
reconciling the numbers with the specification's own organization. Item 1 needs the
4096-byte run in any case, because the identity in item 1 uses the specification's page
size.

The interleaving-order half produced an effect below 3% on p99 — CWDP 811,270 and
2,111,440; WCDP 811,270 and 2,111,440; DWCP 810,620 and 2,154,660; PDWC 805,420 and
2,166,520 — against a roughly two-fold p99 swing across the die-count sweep (primary).
The reason is that with uniformly random addresses, which axis strides fastest barely
changes where the requests land, because the requests already spread evenly across
channels whatever the striping order. Re-run the interleaving half under sequential and
strided patterns, where a coarser channel stride keeps consecutive addresses on fewer
channels for longer.

One scope statement has to travel with the interleaving numbers: `plane_allocation_scheme`
is MQSim's own channel, way, die and plane striping order, not the specification's
host-side `AXI` interleaving granularity in bytes, and is used as the closest available
proxy because MQSim's addressing unit is the page (primary).

**Depends on.** Nothing new for the re-runs. Tracing the die-count mechanism means
reading MQSim source.

**If not done.** Both sweeps stay at the level of "the infrastructure can express these
axes and produce a number", which the proof document already states, and neither axis
can be quoted in the paper.

## 20. Access pattern and `HBF` parameter sweep

**Supports.** No claim directly. Detailed write-up: item 6 of
`todo/experiments-to-run.md`.

**What number has to come out.** One sensitivity table or figure covering all five
access patterns across the three timing profiles.

**How to run.** Cross the five existing access patterns — `sequential`, `random`,
`strided`, `pointer_chase`, `mixed_rw` — with the three timing profiles, and report
the latency sensitivity of each pattern.

**Depends on.** The existing access pattern generators and the three timing profiles;
ideally run after item 15, so the sweep lands on re-anchored profiles.

**If not done.** The reviewer question "what decision can this tool help someone make?"
has no answer. The sweep also supplies the evidence for the current assumption that
random access is charged at single-page cost, an assumption with no measurement behind
it today.

---

# Group 7 — forward-looking scenario and cost

## 21. Whether `HBF` suits a vehicle or an edge platform

**Supports.** No claim directly. The item is a forward-looking scenario: a deployment
nobody can measure today gets an answer out of the simulator instead of an opinion. Do
not settle the answer in advance — run the scenario and report what comes out, in either
direction.

**What number has to come out.** For a forward-looking vehicle or edge platform: the
end-to-end throughput and the sustained rate the thermal model allows, at the platform's
power budget, against the same workload run without `HBF`.

**How to run.** Set the model size and the platform parameters with a forward-looking
view rather than with today's figures. Today's figures are the starting point only:
automotive platforms today carry 64 to 128 GB of LPDDR5X at 273 GB/s, and an NVIDIA
Jetson Thor module draws 40 to 130 W for the whole module (primary).

The temperature condition to assume is that the vehicle has air conditioning and the
cabin is temperature-controlled, so the hot end is AEC-Q100 Grade 3 at +85 °C. AEC-Q100
Rev-J (2023-08-11) states four grades, all measured as ambient temperature and all with
a −40 °C low end: Grade 0 is −40 to +150 °C, Grade 1 is −40 to +125 °C, Grade 2 is −40
to +105 °C, Grade 3 is −40 to +85 °C (primary). §9.1 of the `HBF` specification states a
junction temperature range of 0 °C to 105 °C (primary). Grade 3's hot end therefore
falls inside the range the specification declares, which is what makes the scenario
runnable at all; the low end of every AEC-Q100 grade, −40 °C, falls outside that range,
and the air-conditioning assumption is what takes the low end out of the scenario. State
the assumption in the paper wherever the scenario appears.

Energy per bit, for the power budget (primary): flash read 102.4 pJ/bit, HBM read
4.2 pJ/bit, DDR5-7200 9.2 pJ/bit, so flash is 24.4 times HBM.

Retention at the hot end, reported as an interval rather than a single value
(inference): extrapolating from the specification's 24 hours at 85 °C by the Arrhenius
relation gives about 3.64 hours at 105 °C at an activation energy of 1.108 eV, which is
the value back-solved from the AERO paper's stated equivalence that one year at 30 °C
equals thirteen hours at 85 °C; at 0.6 eV the same extrapolation gives about 8.58 hours.
Item 23 sweeps the activation energy and supplies the interval.

**One scope statement is required.** §3 of the specification reads `This document
defines the HBF interface with xPU for datacenter-class deployments` (primary). A
vehicle or edge run therefore sits outside the deployment range the specification
declares, and the paper has to say so where the scenario is presented.

**Depends on.** The thermal model of item 5 and the retention model of item 4. Neither
exists yet, so this item cannot run before those two.

**If not done.** The paper carries no forward-looking scenario, and the question of
whether the tool answers anything outside the datacenter case it was built for goes
unasked.

## 22. Total cost of ownership model

**Supports.** The core claim, indirectly: the cost model takes its bandwidth from item
1, so the cost figure is no better than the bandwidth figure — which is the point the
core claim makes.

**What number has to come out.** Total annual cost per model instance, computed at each
bandwidth point item 1 produces, for three configurations: weights in HBM spread across
more GPUs, weights in in-package LPDDR, and weights in `HBF`.

**How to run.** Build the model as a script under `scripts/`, taking the bandwidth item
1 produces at each grid point as its input and reporting annual cost per model instance.
Two results are already available and can be checked against the model's output; both
are inference:

- Moving one model instance from 16 GPUs to 4 GPUs saves 16,426 USD per year even if the
  GPUs cost nothing, on 4-year amortization at PUE 1.20, because the 17,778 USD of
  electricity saved already exceeds the 1,352 USD of flash material cost. The conclusion
  therefore does not depend on knowing what a GPU costs.
- The in-package LPDDR configuration beats buying more GPUs as soon as one GPU costs
  more than 11,518 USD, on 4-year amortization at PUE 1.20. Every datacenter GPU is far
  above that price.

Inputs, each with its verification level:

- Flash: 512Gb TLC die, average transaction price 21.13 USD on 2026-08-10, which is
  0.3302 USD per GiB (primary).
- DRAM: DDR5 16Gb die, average transaction price 52.73 USD on 2026-08-17, which is
  26.36 USD per GiB (primary). The ratio between the two is 79.9 (inference).
- Two warnings must be printed wherever those two prices are used (primary). Spot prices
  are not contract prices: on the same day the DDR5 quotes ran from 68.50 high to 34.20
  low, a factor of two inside one day. And memory prices are rising steeply through 2026
  — TrendForce published conventional DRAM contract prices up 93 to 98% quarter over
  quarter in the first quarter, with a further 58 to 63% expected in the second.
- Electricity: the US industrial average is 8.71 cents per kWh, May 2026 preliminary
  figure (primary).
- PUE: Google's 2025 fleet-wide figure is 1.09 and the Uptime survey global average is
  1.55 to 1.59 (primary). The two results above use 1.20, so report the sensitivity
  across the published range rather than at one value.
- Rental-price cross-check (primary): CoreWeave's list price for GB200 NVL72 on demand
  is 10.50 USD per GPU-hour, so 16 GPUs running a full year is 1,472,688 USD and 4 GPUs
  is 368,172 USD, a difference of 1,104,516 USD.

**Three counter-figures have to be reported next to the result, not in a footnote.**

First, the flash price above is a die material cost, not a product price. SanDisk's own
statement is that `HBF` gives 8 to 16 times the capacity of HBM at similar bandwidth and
similar cost, and stays close to HBM4 in physical size, power envelope and stack height
(primary). On that statement the product-level price per GB of `HBF` is roughly one
eighth to one sixteenth of HBM per GB, not the NAND die spot price, and the die material
cost of 5,409 USD is a floor on the dies alone (inference).

Second, the rack power ceiling takes part of the saving back. The NVIDIA reference
architecture puts a whole-rack ceiling at 142 kW (primary). At 1.8 kW per GPU plus 549 W
for `HBF`, one GPU with `HBF` is 2.349 kW, so a full 72 GPUs would need 169.1 kW, above
the ceiling, and only 60 fit under 142 kW; the pure-HBM and the in-package-LPDDR
configurations both fit (inference).

Third, the read energy per bit of `HBF` differs by an order of magnitude across the three
sources available (primary): Micron's paper takes 8.5 pJ/bit; a KAIST lecture takes 20.0
and writes 100.0; HAVEN puts contemporary NAND at about 30 and a re-architected `HBF`
between 2 and 16. Recomputed at 20 pJ/bit, the `HBF` layer at 8 TB/s draws 1,280 W
rather than 549 W (inference), which moves the rack arithmetic above again. Report the
cost across the range rather than at one energy figure.

**Depends on.** Item 1, for the bandwidth the model consumes. Nothing else.

**If not done.** The paper argues for a device on capacity grounds without saying what
the device costs to run, and the one figure a datacenter operator would ask for is
missing.

---

# Group 8 — future work, not needed before submission on its own

## 23. Activation-energy sensitivity sweep

**Supports.** No claim directly. Detailed write-up: item 4 of
`todo/experiments-to-run.md`. The half of the thermal story that runs from temperature
to data retention time sits in the discussion and future-work sections of the paper, and
the sweep belongs to that half. One consequence of this revision's re-ordering has to be
noted rather than acted on unilaterally: item 4 puts the retention-refresh cost back
into the submission, and item 21 needs a retention interval rather than a single value,
so if either of those two items is run, this sweep becomes the sensitivity analysis
behind their retention numbers rather than future work (inference — a consequence of the
new ordering, not a decision the collaborator has taken).

**What number has to come out.** If the sweep is run: the range of activation energy
over which the conclusion holds, together with the disclosure that the constant has no
published value for 3D NAND.

**How to run.** Treat the activation energy Ea as a swept parameter rather than a
fixed constant — for example from 0.9 eV to 1.3 eV — and report how sensitive the
retention deadline and the resulting refresh traffic are to Ea. Activation energy is
the material constant in the Arrhenius relation that converts temperature into the
speed at which a failure mechanism proceeds; HeatWatch (HPCA 2018, DOI
`10.1109/HPCA.2018.00050`) gives its Equation 1 as
`AF(T1, T2) = t1 / t2 = exp[ (Ea / kB) * (1/T1 - 1/T2) ]`, with kB the Boltzmann
constant 8.62 × 10^-5 eV/K.

**Depends on.** Ea exposed as a configuration parameter of the retention model, which
does not exist yet. Can be run together with item 4 or item 6.

**If not done.** Nothing in the submission breaks so long as item 4 and item 21 are also
not run. The reason the sweep exists at all
is the reviewer question "which value of Ea did you assume, and does your conclusion
survive if it is wrong?", and the answer the paper gives in discussion is that no
value exists to assume: HeatWatch states `For a planar NAND flash memory device,
Ea = 1.1 eV.` and immediately after that `To our knowledge, there is no public
literature that reports the value of Ea for 3D NAND flash memory.` `HBF` stacks 3D
NAND, so no public value exists for the device the paper models.

