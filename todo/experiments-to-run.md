# Experiments to run before submission

**The single list ordered by the paper's contribution claims is now
`docs/EXPERIMENTS-NEEDED.md`.** Work from that file. This file is kept as the longer
record of how to run the eight items below, and `docs/EXPERIMENTS-NEEDED.md` names the
item number here for every item that came from this file. Nothing in this file has
been removed.

Target: FAST '27 fall round, 2026-09-15 23:59 AoE.

Items are ordered by priority. Items 1 to 3 are required: without them the
evaluation chapter does not stand. Every number quoted here is taken from a
checkpoint document under `docs/proofs/`, and the source file is named with each
item. Each item states what to run, which reviewer question it answers, what it
needs, and what counts as done.

## 1. Held-out calibration validation (highest priority)

**What to run.** The `cd8p-vmem-p50` profile currently reproduces measurement
exactly at six breakpoints (1, 4, 16, 64, 256, 512 pages). Measure additional
transfer sizes outside those six page counts, predict them with the fitted
empirical curve, and report the error as a percentage against measurement.

**Question it answers.** "Your six breakpoints have zero error — is that because
those six points are the ones you fitted?" `docs/proofs/2026-08-11-cd8p-vmem-tuning.md`
states in its own words that the zero-error result is a constructed
deterministic calibration check, not cross-validation.

**Needs.** The CD8P device and the calibration harness that produced the
existing curve.

**Done when.** A table of held-out points with error percentages exists, of a
magnitude that can be compared with the 7.85%–13.19% error range CXLMemSim
reports.

## 2. Split the 164.70x slowdown into modeled delay and emulator overhead

**What to run.** At minimum on a microbenchmark, separate the modeled media
delay from the per-warp request-path overhead of the emulator itself. Two
workable methods: an identity-injection control run (register the range, inject
zero delay, measure what remains) or a `time_scale` sweep that varies the
modeled delay while the request path stays constant.

**Question it answers.** "Of that slowdown, how much is the device you are
modeling and how much is your tool being slow?" The current figure is 164.70x,
from a baseline generation of 0.269996 s against 44.469084 s with timing
injection. `docs/proofs/2026-08-11-vllm-exact-live-delay.md` states that the two
components are reported together and that separating them is future work.

**Needs.** No new hardware. The existing vLLM path plus a microbenchmark that
can be run with injection disabled.

**Done when.** The paper can report how much of the observed time is modeled
media delay and how much is emulator overhead. If the split cannot be produced,
the qualification from the proof document goes into the paper verbatim.

## 3. Thermal state and refresh traffic

**What to run.** Sweep the LTT and STT thresholds (the temperatures at which the
spec's light and heavy throttling states are entered) and the refresh period,
and report the cost in throughput and tail latency. Refresh here means the host
rewriting data whose retention deadline is approaching, so the stored charge
returns to full level; that rewrite is extra write traffic the workload did not
ask for.

**Question it answers.** "What conclusion actually changes once temperature is
in the model?" Without this set, temperature is only asserted to matter.

**Needs.** The thermal state machine and the retention/refresh loop in the
simulator, plus a thermal trace. The measured first-order fit is already
available: time constants 13.1 s for the GPU and 12.4 s for the CD8P
(`docs/proofs/2026-08-11-hybrid-complete.md`).

**Done when.** A threshold-versus-cost curve exists, reporting throughput loss,
p99 latency increase, and the fraction of bandwidth consumed by refresh.

## 4. Activation-energy sensitivity sweep (new this round)

**What to run.** Treat the activation energy Ea as a swept parameter rather than
a fixed constant — for example from 0.9 eV to 1.3 eV — and report how sensitive
the retention deadline and the resulting refresh traffic are to it. Activation
energy is the material constant in the Arrhenius relation that converts
temperature into the speed at which a failure mechanism proceeds; HeatWatch
(HPCA 2018, DOI `10.1109/HPCA.2018.00050`) gives its Equation 1 as
`AF(T1, T2) = t1 / t2 = exp[ (Ea / kB) * (1/T1 - 1/T2) ]`, with kB the Boltzmann
constant 8.62 × 10^-5 eV/K.

**Question it answers.** "Which value of Ea did you assume, and does your
conclusion survive if it is wrong?" HeatWatch states `For a planar NAND flash
memory device, Ea = 1.1 eV.` and immediately after that `To our knowledge, there
is no public literature that reports the value of Ea for 3D NAND flash memory.`
HBF stacks 3D NAND, so no public value exists for the device we model.

**Needs.** Ea exposed as a configuration parameter of the retention model. Can
be run together with item 3.

**Done when.** The paper can state the range of Ea over which the conclusion
holds, and discloses that the constant has no published value for 3D NAND.

## 5. Extend vLLM coverage

**What to run.** Coverage is currently one 16,384-byte prefix of
`model.layers.0.mlp.experts.w13_weight`. Extend it to at least one full layer of
weights. Registering all 61,064,245,248 bytes is not a route: per
`docs/proofs/2026-08-11-vllm-exact-live-delay.md`, full registration makes
vLLM's profiling pass emit an impractically large number of synchronous
requests. Two candidate routes are registering in batches and bypassing the
profiling pass.

**Question it answers.** "You modeled 16 KB out of 61 GB — on what grounds is
this a real workload?"

**Needs.** The vLLM adapter and the same Qwen3-30B-A3B setup as the existing
proof.

**Done when.** Either coverage reaches one full layer, or the paper argues
explicitly why the selected range is representative. If it cannot be done within
a week, convert it to a stated limitation rather than carrying it to the
deadline.

## 6. Access pattern and HBF parameter sweep

**What to run.** Cross the five existing access patterns (`sequential`,
`random`, `strided`, `pointer_chase`, `mixed_rw`) with the three timing
profiles, and report the latency sensitivity of each.

**Question it answers.** "What decision can this tool help someone make?" It
also supplies evidence for the current assumption that random access is charged
at single-page cost, which has no measurement behind it today.

**Done when.** One sensitivity table or figure covers all five patterns across
the three profiles.

## 7. Re-anchor the timing profiles to the three bandwidth points in the spec

**What to run.** The `aggressive` profile is currently capped at 1 TB/s with
1 TiB of capacity. The spec's three points are approximately 0.4 to 3.0 TB/s per
stack, with 512 GB per stack, 16 channels, and 256 GB/s per channel at
32 GT/s. Re-anchor the three profiles to those figures and re-run the sensitivity
experiments above.

**Question it answers.** "Do your conclusions hold only under parameters you
invented?"

**Needs.** The spec numbers are currently second-hand; see item 1 of
`facts-to-verify.md`. Re-anchoring can proceed with the second-hand values, but
the paper must not present them as verified against the primary source until
that item is closed.

**Done when.** The three profiles carry spec-derived bandwidth and capacity, and
items 3, 4, and 6 have been re-run on them.

## 8. Prefetch window experiment (new this round)

**What to run.** Change the fetch strategy from on-demand reads that rely on
concurrency to hide latency, to prefetching one layer ahead, with everything
else held constant, and quantify how the timing cost contributed by the HBF
layer changes.

**Question it answers.** How much of the HBF access latency a prefetch window is
worth. This corresponds to Q9 in the paper outline. The question originates from
an industry analysis article, which speculates that a statically scheduled
architecture that hides latency by compiler-arranged prefetching suits HBF
better than a GPU that hides latency with many concurrent threads. We test that
speculation rather than citing it as a result.

**Bound to state.** The experiment runs on an NVIDIA GPU and changes a fetch
strategy inside the simulator. It is not a measurement on a TPU, so the
conclusion may state what a prefetch window is worth for the HBF layer, and may
not state that a TPU suits HBF better.

**Done when.** The timing cost of the HBF layer is reported under both fetch
strategies for the same workload and profile.

