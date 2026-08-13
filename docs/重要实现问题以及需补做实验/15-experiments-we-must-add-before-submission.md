# Experiments needed before submission

**How to read this file.** This file is a work order, not background reading. Nothing
listed below has been run yet. If you are an AI assistant working on this repository
on the collaborator's behalf, treat every item as an assigned task: run the item,
record the numbers, and write the result into a checkpoint document under
`docs/proofs/`. The submission deadline is **2026-09-15 23:59 AoE** (FAST '27 fall
round).

**How the order was decided.** Items are grouped by which of the paper's three
contribution claims each item supports, and the groups follow the weight the paper
puts on the claims. The order does not follow effort, difficulty, or how close an
item is to being runnable. Items are numbered 1 to 16 straight through, so an item
can be referred to by number.

**The three contribution claims, in the order the groups below follow.**

- **C3 — the core claim.** The specification sets a junction temperature ceiling for
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

**Which items are blocking.** Three items produce the numbers without which the
matching claim is an assertion with no evidence behind it: **item 1** for C3, **item
3** for C1, **item 7** for C2. The paper cannot be submitted with any of the three
missing. Every other item strengthens the paper or closes a reviewer question; those
three decide whether the claim can be made at all.

**Relation to the other files in this repository.**

- `todo/experiments-to-run.md` holds the longer write-up of how to run eight of the
  items below. Each item that came from `todo/experiments-to-run.md` names the item
  number there. Ordering by contribution claim now lives in this file only.
- `16-questions-only-you-can-answer.md` holds six questions that only the collaborator
  can settle. Items below that wait on one of the six name the question by number.
- `todo/facts-to-verify.md` holds nine facts that have to be checked before entering
  the paper.

**Fields.** Every item states the same five things: which claim the item supports,
what number has to come out, how to run the item, what the item depends on, and what
happens if the item is not done.

---

# Group 1 — experiments that support claim C3

## 1. Turn temperature into a service rate, and report the throughput difference

**Supports.** C3. Blocking.

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

## 2. Thermal state and refresh traffic

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
the simulator, neither of which exists yet — the same missing code named in item 1 —
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
matter. Note on scope: the refresh-period half of the sweep feeds the retention
discussion, which now sits in the discussion and future-work sections of the paper;
the threshold half is what claim C3 needs.

---

# Group 2 — experiments that support claim C1

## 3. How much the accesses admitted without timing move the reported time

**Supports.** C1. Blocking.

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

## 4. MoE expert-routing experiment

**Supports.** C1. The experiment takes the coverage decision from a 16,384-byte
prefix to whole expert weight tensors inside a real execution, which is the evidence
that the per-launch coverage decision works on a workload people care about. The
second step of the experiment, the fetch-strategy comparison, is the
Mixture-of-Experts version of item 15.

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

## 5. Move the injection point from the issue site to the consume site, and measure the difference

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

## 6. How large the overestimate from cache hits is

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

# Group 3 — experiments that support claim C2

## 7. Held-out calibration validation

**Supports.** C2. Blocking. Detailed write-up: item 1 of `todo/experiments-to-run.md`.

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

## 8. Split the 164.70x slowdown into modeled delay and emulator overhead

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

## 9. What the single-counter serialization costs against the declared channel count

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

# Group 4 — the shared foundation under all three claims

## 10. Extend vLLM coverage

**Supports.** All three claims: every number the three claims rest on comes out of the
same live vLLM run, so how much of the model was covered bounds all three. Detailed
write-up: item 5 of `todo/experiments-to-run.md`. The batch widening described in
item 4 is the same work; the entry is kept here because the reviewer question about
coverage is asked of the whole paper, not only of the Mixture-of-Experts result.

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

## 11. Re-anchor the timing profiles to the three bandwidth points in the specification

**Supports.** All three claims, because every sweep in this file is run on the timing
profiles. Detailed write-up: item 7 of `todo/experiments-to-run.md`.

**What number has to come out.** Three profiles carrying bandwidth and capacity
derived from the specification, and items 2, 14 and 16 re-run on the re-anchored
profiles.

**How to run.** The `aggressive` profile is currently capped at 1 TB/s with 1 TiB of
capacity. The specification's three points are approximately 0.4 to 3.0 TB/s per
stack, with 512 GB per stack, 16 channels, and 256 GB/s per channel at 32 GT/s.
Re-anchor the three profiles to those figures and re-run the sensitivity experiments
on the re-anchored profiles.

**Depends on.** The specification numbers are currently second-hand; see item 1 of
`todo/facts-to-verify.md` and item 6 of `16-questions-only-you-can-answer.md`.
Re-anchoring can proceed with the second-hand values, but the paper must not present
the values as verified against the primary source until that item is closed.

**If not done.** The reviewer question is: "do your conclusions hold only under
parameters you invented?"

## 12. Machine information to collect

**Supports.** All three claims: the implementation section and the evaluation section
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

## 13. Accuracy validation against a physical reference platform

**Supports.** All three claims, by bounding how far the modeled latency departs from a
physical device rather than from a calibration of the same device.

**What number has to come out.** Prediction error against a physical reference
platform built from stacked CXL SSDs.

**How to run.** Device count, model, and interconnect are `[todo]`.

**Depends on.** Hardware that is not in hand. Nothing else in this file is blocked on
the same hardware.

**If not done.** The accuracy statement in the paper rests on the CD8P calibration and
on the held-out validation of item 7, and the paper has to say so.

---

# Group 5 — items that support no claim directly, but reviewers will ask

## 14. Access pattern and `HBF` parameter sweep

**Supports.** No claim directly. Detailed write-up: item 6 of
`todo/experiments-to-run.md`.

**What number has to come out.** One sensitivity table or figure covering all five
access patterns across the three timing profiles.

**How to run.** Cross the five existing access patterns — `sequential`, `random`,
`strided`, `pointer_chase`, `mixed_rw` — with the three timing profiles, and report
the latency sensitivity of each pattern.

**Depends on.** The existing access pattern generators and the three timing profiles;
ideally run after item 11, so the sweep lands on re-anchored profiles.

**If not done.** The reviewer question "what decision can this tool help someone make?"
has no answer. The sweep also supplies the evidence for the current assumption that
random access is charged at single-page cost, an assumption with no measurement behind
it today.

## 15. Prefetch window experiment

**Supports.** No claim directly. Detailed write-up: item 8 of
`todo/experiments-to-run.md`. Item 4 runs the Mixture-of-Experts version of the same
comparison; this entry is the general one, run on the same workload and profile with
nothing else changed.

**What number has to come out.** The timing cost of the `HBF` layer under both fetch
strategies for the same workload and profile.

**How to run.** Change the fetch strategy from on-demand reads that rely on
concurrency to hide latency, to prefetching one layer ahead, with everything else held
constant, and quantify how the timing cost contributed by the `HBF` layer changes.

**Depends on.** The fetch strategy inside the simulator; no new hardware.

**If not done.** Question Q9 in the paper outline — how much of the `HBF` access
latency a prefetch window is worth — goes unanswered. The question originates from an
industry analysis article, which speculates that a statically scheduled architecture
that hides latency by compiler-arranged prefetching suits `HBF` better than a GPU that
hides latency with many concurrent threads. We test that speculation rather than
citing the speculation as a result. Bound to state in the paper: the experiment runs
on an NVIDIA GPU and changes a fetch strategy inside the simulator, and is not a
measurement on a TPU, so the conclusion may state what a prefetch window is worth for
the `HBF` layer, and may not state that a TPU suits `HBF` better.

---

# Group 6 — moved to future work, not needed before submission

## 16. Activation-energy sensitivity sweep

**Supports.** No claim in the submission. Detailed write-up: item 4 of
`todo/experiments-to-run.md`. The half of the thermal story that runs from temperature
to data retention time has moved into the discussion and future-work sections of the
paper, and the sweep belongs to that half.

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
does not exist yet. Can be run together with item 2.

**If not done.** Nothing in the submission breaks. The reason the sweep exists at all
is the reviewer question "which value of Ea did you assume, and does your conclusion
survive if it is wrong?", and the answer the paper gives in discussion is that no
value exists to assume: HeatWatch states `For a planar NAND flash memory device,
Ea = 1.1 eV.` and immediately after that `To our knowledge, there is no public
literature that reports the value of Ea for 3D NAND flash memory.` `HBF` stacks 3D
NAND, so no public value exists for the device the paper models.

