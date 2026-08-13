# Questions for the collaborator

Every item below needs an answer or a decision from you: why a parameter was set
to the value it holds, whether a constraint is deliberate, whether a document can
be obtained through a channel we do not have. Nothing here can be settled by
running a program or by reading a source we already hold.

**Which file or directory holds what.**

- **This file** — questions only you can answer.
- **`docs/EXPERIMENTS-NEEDED.md`** — the work order, and the list to work from.
  Sixteen items in six groups, ordered by which contribution claim each one
  supports: the thermal-throttling claim first, then coverage accounting, then
  the timing model, then shared foundation work, then items a reviewer will ask
  about, then work already moved to future work. Each item names the claim it
  supports, the number it must produce, how to run it, what it depends on, and
  what happens if it is skipped.
- **`todo/experiments-to-run.md`** — the longer method notes behind eight of
  those items, kept as reference rather than as a second list.
- **`todo/facts-to-verify.md`** — nine facts that must be checked before they
  enter the paper.
- **`docs/可能潜在代码实现问题请求审查/`** — six points raised by reading the
  code. Each one states what the code does, where we may have read it wrong, and
  what we would like you to confirm. These are review requests, not defect
  reports: a point may well turn out to be our misreading.

Where an item below depends on something in those three files, the item names the
file and the item number rather than repeating the text.

Each item keeps two kinds of statement apart: what was verified first-hand in this
repository, with file path, line number, and the value quoted as written; and what
we suggest. A suggestion is a proposal for you to accept, modify, or reject.
Nothing in any suggestion has been applied to the repository.

## 1. Where the two temperature thresholds in `simulated-warning` come from

**What we found.** `/root/hbfsim/HBFSim/configs/thermal/scenarios.json` lines 13
and 14 give the `simulated-warning` scenario `"gpu_threshold_c": 83.0` and
`"ssd_threshold_c": 77.0`.

Both values are numbers the CD8P SSD reports about itself through SMART.
`/root/hbfsim/HBFSim/configs/thermal/gpu-cd8p-logp-live.json` lines 35 and 36
record `"warning_temperature_c": 77.0` and `"critical_temperature_c": 83.0`, and
`/root/hbfsim/HBFSim/docs/proofs/2026-08-10-live-gpu-cd8p-thermal.md` lines 57
and 58 record the same two values as read off the device: `a 77 degrees C warning
threshold (wctemp=350 K)` and `an 83 degrees C critical threshold (cctemp=356 K)`.

**Why it is a problem.** The threshold applied on the GPU side, 83.0, is the CD8P
SSD's critical temperature. The value has no connection to the GPU. Separately,
`simulate_overheat.py` does not read those two fields out of the calibration
file; the values in `scenarios.json` were copied in by hand, so nothing keeps the
two files in agreement if either one changes.

**What we suggest.** A GPU throttling threshold should come from the GPU or from
the device being modeled. Two candidate sources: `nvidia-smi -q -d TEMPERATURE`,
which reports the card's own slowdown and shutdown temperatures; or the
thresholds the specification defines for `HBF`, which is what the simulator
represents.

**What we need from you.** Was writing 83.0 on the GPU side deliberate, or was
the value copied across by mistake? If deliberate, what is the basis for the
value? Neither 83.0 nor 77.0 has been changed, and neither will be changed until
you answer.

## 2. The SSD-side extrapolation multiplier of 14

**What we found.** In the same scenario file,
`/root/hbfsim/HBFSim/configs/thermal/scenarios.json` line 11 sets
`"ssd_heat_multiplier": 14.0`, while line 10 sets `"gpu_heat_multiplier": 1.35`
on the GPU side.

The measured temperature rise on the SSD side is 3.185 degrees C: ambient 34.0
to a steady state of 37.18531186555492. Multiplied by 14, the asymptote is 78.59
degrees C, which sits 1.6 degrees C above the 77 degrees C threshold, and there
is no measured data anywhere near 77 degrees C. The GPU side measures a rise of
45.578 degrees C, from 28.0 to 73.5780194293642; multiplied by 1.35, the
asymptote is 89.53 degrees C, a far milder extrapolation.
`/root/hbfsim/HBFSim/scripts/thermal/simulate_overheat.py` lines 10 to 16 scale
the steady-state rise by the multiplier and leave the time constant at its
calibrated value.

**Why it is a problem.** A reviewer can dispose of the number in one sentence: a
measured rise of 3 degrees C extrapolated fourteen times cannot support a
threshold-crossing time, because no measurement exists in the region where the
crossing is claimed to happen.

**What we suggest.** Either drop the `simulated-warning` scenario, or reduce the
multiplier to a value the measurement can carry and state the basis for the
reduced value in the paper.

**What we need from you.** How was 14.0 arrived at, and is there a basis for the
value that we have not seen?

## 3. Media parameters on the detailed MQSim path cannot be changed after construction

**What we found.** `/root/hbfsim/HBFSim/src/mqsim_adapter/mqsim_online.cpp`
sets the media parameters of MQSim once, in `configure_mqsim` at lines 35 to 77,
with the read and write latencies at lines 61 to 66. Those parameters take effect
when `SSD_Device` is constructed at line 126, and there is no route to change any
of them afterwards. MQSim is the detailed flash device simulator this project
adapts and runs as the reference path; the paper outline calls the route through
MQSim the detailed reference path.

**Why it is a problem.** The third challenge in the paper outline, labelled C3
there, now reads: the specification sets a junction temperature ceiling for `HBF`
— junction temperature is the temperature inside the chip at the point where heat
is actually generated, normally higher than the temperature of the package
surface — and above that ceiling the device holds the temperature down by slowing
service, limiting the rate, and finally refusing new commands, so the rate
sustainable over a long run is below the peak the vendor quotes. The paper's
claim is that we compute that sustainable rate inside a real execution. Producing
the number requires the service rate to change with temperature.

The fast path can do so. The fast path is the timing model that runs on the GPU
itself and holds the kernel in a spin-wait on `%globaltimer` until the modeled
delay has elapsed; the delay is set in
`/root/hbfsim/HBFSim/src/cuda_runtime/device/hbf_device.cu` at lines 413 to 423
for the parameterized model and at lines 364 to 374 for the measured-curve model,
and the rate follows from `base_latency` plus
`fast_transfer_ns(bytes, aggregate_bandwidth_bytes_per_s)`. Making those two
quantities depend on temperature is enough. The detailed MQSim path cannot follow.

**What we suggest.** Three options, with their costs.

1. Let only the fast path vary with temperature, and state in the paper that the
   detailed MQSim path runs at a fixed temperature, together with what that means
   for the consistency check between the two paths.
2. Rebuild the MQSim instance whenever the temperature crosses into a different
   thermal state. Cost: the queue state held at the moment of the rebuild is lost.
3. Change MQSim so media parameters can be updated while the simulation runs.
   Cost and risk are for you to estimate.

**What we need from you.** Which of the three options do you prefer, and how large
a change is option 3?

## 4. There is no consistency check between the fast path and the detailed MQSim path

**What we found.** Line 130 of
`/root/hbfsim/HBFSim/paper/35-FAST27论文大纲与逻辑线.md` ends with the sentence
`而两条路径之间必须有可检验的一致性` — the two paths must agree in a way that can
be checked.

Searching `src/`, `include/` and `tests/` for the words consistency, agreement,
cross_check and divergence returns one hit,
`/root/hbfsim/HBFSim/src/cuda_runtime/coverage.cpp`, where the word carries an
unrelated meaning. `/root/hbfsim/HBFSim/tests/cpu/calibrator_test.cpp` is 63
lines in total and asserts four things: behaviour during the warm-up period, the
determinism of sampling, the monotonic relation between fast-path latency across
the three timing profiles, and the calibration statistics. Not one assertion puts
the output of the fast path and the output of the detailed MQSim path side by
side.

**Why it is a problem.** The paper outline promises the check, and the code does
not contain the check.

**What we suggest.** Add a consistency check and track the check as an experiment
item in `todo/experiments-to-run.md`.

**What we need from you.** Was the check deliberately left for a later stage, or
was the check missed?

## 5. The thermal work is not connected to the timing path

**What we found.** `/root/hbfsim/HBFSim/scripts/thermal/` holds four scripts:
`collect.py` samples real telemetry, `fit_logp.py` fits a first-order thermal
response, `gpu_heat.py` is a sustained BF16 matrix-multiply heater, and
`simulate_overheat.py` extrapolates the calibration result to the named scenarios
and computes the threshold-crossing time. `/root/hbfsim/HBFSim/configs/thermal/`
holds two configuration files, and `/root/hbfsim/HBFSim/CMakeLists.txt` lines 273
to 278 define a test named `thermal_logp`.

Searching `src/` and `include/` for thermal, temperature, junction, retention,
arrhenius and activation_energy returns zero hits. No line of code turns a
temperature reading into an effect on access latency.

**Why it is a problem.** Challenge C3 asserts that temperature changes the answer,
and the mechanism that would make temperature change the answer is not
implemented. Item 3 of `todo/experiments-to-run.md` and item 4 of the same file
both depend on the missing mechanism.

**What we suggest.** The place to implement the connection is the fast path, at
the two code locations named in item 3 above.

**What we need from you.** Who writes this, and by what date? Under the
pre-registration discipline we also need to agree in advance on the fallback: if
the mechanism is not running by that date, what weaker statement does challenge
C3 become?

## 6. The specification text has still not been obtained

**What we found.** Recorded in full as item 1 of `todo/facts-to-verify.md`.
Challenge C3 now rests entirely on the four thermal states and their thresholds,
and the only support for those values is one Chinese-language secondary article,
`/root/hbfsim/HBFSim/docs/ref_article/semiinsights2026-hbf-standard-release-cn.pdf`.
`opencompute.org` returns HTTP 403 to command-line requests and a bot-check page
to reading proxies, and neither the SanDisk nor the SK hynix press release
contains a download link.

**Why it is a problem.** Every threshold that carries challenge C3 would have to
be marked second-hand in the paper.

**What we suggest.** Obtaining the document needs a browser session and most
likely an OCP account.

**What we need from you.** Does your institution have an OCP membership route to
the specification? If the specification cannot be obtained that way, can the
SK hynix or SanDisk presentation material from FMS 2026 serve as a second source?

## 7. Whether the injector stays at the `PTX` layer or moves to the `SASS` layer

**What we found.** The injector writes the delay into `PTX`, the intermediate text
the compiler emits before the driver turns it into machine code for a particular
architecture. Line 32 of
`/root/hbfsim/HBFSim/docs/superpowers/specs/2026-08-09-hbfsim-hybrid-design.md`
lists `Rewriting SASS in cubin-only kernels.` among the things this project does
not do, and gives no reason for the boundary. `SASS` is the machine code the
driver finally executes; a `cubin` is the compiled form a kernel arrives in when
no `PTX` text ships with it.

**Why we are asking now.** NVBit (MICRO-52, 2019, DOI `10.1145/3352460.3358307`)
works at the `SASS` layer and can instrument a module that reaches it as an
already-compiled binary, with no source and no intermediate text required.
Section 6.1 of the NVBit paper measures precompiled libraries at 74% to 96% of
executed instructions on machine-learning workloads, 88% on average. On our side,
one real vLLM run made 23,210 coverage decisions — one before every kernel launch,
deciding whether that launch touches memory registered as `HBF` and whether its
accesses can be timed — and 10,584 of them fell on modules that reached us as
already-compiled binaries, with 0 accesses successfully timed. A reviewer will ask
why we did not use the official tool that already solves this.

**The facts on both sides, each checked first-hand.** The `SASS` layer is strictly
better on information availability: of the four quantities the timing model needs,
the distance between the issue of a load and the use of its result is visible only
there. It is also better on the addresses of bulk tensor transfers — the NVBit
v1.8 release package carries `core/nvbit.h` lines 82 and 83, line 402, lines 477
and 497, and a compilable `tools/mem_trace_tma` example. Against that: moving over
fixes only the smallest of the four error terms we currently carry; NVBit saves
registers to memory for every thread before each instrumentation call, which costs
cycles and disturbs cache locality; NVBit does not guarantee the application keeps
working correctly when original instructions are deleted; and line 44 of the v1.8
`README.md` states `CUDA driver version: <= 575.xx`, while our validation platform
runs driver 595.84. That last point has never been tested on our machine.

**What we need from you.** Three questions. First, what was behind the boundary on
line 32 — was `SASS` rewriting excluded for cost, for tool-chain reasons, or for
something else? Second, do you accept keeping the injector at the `PTX` layer and
using `SASS` only as a measuring instrument, that is, using NVBit's read-only
interface to measure how large the coverage gap is without moving the injector?
Third, is it worth half a day to run one NVBit smoke test, so that whether driver
595.84 works becomes a footnote backed by a measurement rather than an open
question?

The full comparison is in `/root/hbfsim/49-PTX还是SASS与NVBit对照实验.md`, and the
same decision is listed as discussion item three in
`/root/hbfsim/HBFSim/paper/35-FAST27论文大纲与逻辑线.md`.
