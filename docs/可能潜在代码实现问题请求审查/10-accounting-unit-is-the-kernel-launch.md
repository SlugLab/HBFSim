# The unit of accounting is a kernel launch, not an access, and there is no denominator

**Severity: A, and first in the list.** In the first live run, 10,584 launches touched
registered memory, 0 accesses were timed, and the media time reported for the run was
0 ns. Arithmetic on the same workload puts the media time that should have been
reported between 0.275 s and 8.97 s. The quantity the tool exists to produce came out
as zero, and nothing in the tool signalled that anything was missing.

**How sure we are.** Read from the primary source: every code line, every line number,
and both proof documents quoted below. Our own inference: the per-token weight volume
and the 0.275 s to 8.97 s range at the end of the first question, which is arithmetic
on top of model parameter counts we took from the model card and did not measure.

**Does this conflict with the OCP specification? No, and the specification is the
reason the problem is hard.** Searching the whole of OCP specification v0.7.0 gives
zero occurrences of `page fault`, `doorbell`, `submission queue`, `completion queue`
and `DMA`, and one occurrence of `interrupt`, on page 99, unrelated to access. There
is no per-access event anywhere outside the program under test, so no external counter
can supply the denominator. That is a property of the device, not a defect in this
code. What is a gap in this code is that the tool does not count what it skipped.

All code quoted here was read from the remote branch `origin/hybrid`.

## What we read in the code

**Accounting happens at two layers, with different units, and the two layers are not
connected.**

The upper layer decides admission, one kernel launch at a time.
`CoverageGate::check_launch` produces one `GateDecision` per launch,
`src/cuda_runtime/coverage.cpp` lines 273 to 459, called from
`src/cuda_runtime/launch_gate.cpp` line 707. The fields written to the log are fixed by
`src/reporting/coverage_writer.cpp` lines 9 to 27: `module_id`, `kernel`, `reason`,
`operation`, `inspected_parameters`, `parameter_index`, `parameter_offset`, `address`,
`range_policy`, `modeled`, `opaque_unmodeled`. There is no grid or block size, no byte
count, and no access count.

The lower layer charges time, one warp-merged request at a time.
`src/cuda_runtime/device/hbf_device.cu` lines 503 to 511 use `__activemask` and
`__match_any_sync` to group the lanes of a warp that fall in the same range and on the
same page, and only the leader of each group issues a request. The counters are
`fast_requests` at lines 375 and 424 and `fast_modeled_ns` at line 425.

When the upper layer decides a launch is not timed, the lower layer is never reached,
and the lower-layer counters only ever count requests that were already charged. **An
access that was skipped appears in no counter at all.**

**Two units are coarser than one launch, and neither was visible in the two live
runs.**

1. **A whole CUDA graph is one record.** The wrappers for `cuGraphLaunch` and
   `cudaGraphLaunch`, `src/cuda_runtime/launch_gate.cpp` lines 1070 to 1101, call
   `opaque_launch("graph_launch")`, defined at lines 1016 to 1021, which goes through
   `uninspectable_launch_decision`, `src/cuda_runtime/coverage.cpp` lines 130 to 147,
   and is admitted when only timing-mode ranges are present. One replay of a graph can
   contain hundreds of kernel nodes and produces one record, and that record carries
   the string `graph_launch` instead of a kernel name. Both vLLM proofs ran in eager
   mode, so neither run went through this path; vLLM enables CUDA graphs by default.
2. **`modeled: true` does not mean the accesses of that launch were charged.** The
   decision compares the pointer value of a kernel parameter against the registered
   ranges, `policy_for(slot.value)` at `src/cuda_runtime/coverage.cpp` lines 262 to 271.
   The real address filter is on the device: `find_range` at
   `src/cuda_runtime/device/hbf_device.cu` lines 492 to 495 calls
   `fail(address, RequestStatus::Ready)` for an address outside every range, which
   proceeds silently and is neither charged nor counted. The second live run registered
   the first 16,384 bytes of `model.layers.0.mlp.experts.w13_weight`, while the tensor
   itself is 805,306,368 bytes
   (`docs/proofs/artifacts/2026-08-11-vllm-exact/registration.json`); 16,384 divided by
   805,306,368 is 1/49,152. Most of the reads of that tensor during the 24 timed
   launches went down the silent path.

**One thing that already exists and is not used.** The three counters `fast_requests`,
`reference_requests` and `fast_modeled_ns` (`src/host_service/control_layout.hpp` lines
113 to 115) have a public accessor, `hbfsim_get_stats` at `include/hbfsim/api.h` line
77, read out at `src/cuda_runtime/context.cpp` lines 1757 to 1762, and printed by both
microbenchmarks, `benchmarks/cuda/hbf_microbench.cu` line 254 and
`benchmarks/cuda/hbf_vmem_tuning_bench.cu` line 138. The vLLM adapter never calls it:
searching `adapters/vllm/` for `fast_requests` and for `hbfsim_get_stats` returns zero
hits, and none of the three numbers appears in
`docs/proofs/artifacts/2026-08-11-vllm-exact/result.json`. So for the run that produced
the 164.70x figure, not even the count of charged requests was kept.

The `unsupported_instructions` count on the PTX rewriting side is a static count of
instructions in the text, made at compile time, and cannot stand in for a count of
executions.

## Question one — how large is the effect

### What can be reported today

Three things: how many launches fell into each decision class, why each launch fell
there, and end-to-end wall-clock time. The numbers of the two live runs must never be
mixed, so both are given.

| | First live run (`docs/proofs/2026-08-11-vllm-timing-adapter.md`) | Second live run (`docs/proofs/2026-08-11-vllm-exact-live-delay.md` and `coverage-summary.json`) |
|---|---|---|
| Coverage decisions | 23,210 | 10,339 |
| Touched no registered memory | 12,626 (54.40%) | 9,171 minus 24 |
| `opaque_unmodeled_timing` | 10,584 (45.60%) | 1,168 |
| `modeled: true` | 0 | 24 |
| Registered bytes | 61,064,245,248 | 16,384 |

One ratio follows, and the ratio holds in units of launches only. In the first live
run, 10,584 launches touched registered memory and 0 were timed, so the share of timed
launches is 0%. In the second live run, at least 1,168 + 24 = 1,192 launches touched
registered memory and 24 were timed, which is 2.01%; counting `fused_moe_kernel` alone,
24 of 2,304 launches, which is 1.04%.

### What cannot be reported today

The share of **accesses** that were skipped. Four quantities are needed and all four
are missing:

1. **How many threads each launch started.** The grid and block dimensions are present
   in the wrapper functions — `src/cuda_runtime/launch_gate.cpp` lines 810 to 814 for
   `driver_launch`, line 837 for `runtime_launch`, line 858 for `kernel_launch` — and
   are dropped there. `KernelLaunch` at `include/hbfsim/coverage.hpp` lines 103 to 107
   carries only `module_id`, `kernel` and `parameters`.
2. **How many global accesses inside a registered range each thread executed.** This
   number exists nowhere. For a launch classified `opaque_unmodeled_timing` the module
   is available only as an already compiled binary, so even a static count of
   instructions cannot be taken.
3. **How many bytes each launch touched.** Not recorded.
4. **The numerator itself.** As above, the vLLM path does not export the counters.

So the honest answer to question one is that **the size cannot be estimated from what
the tool records**. Of the four gaps, gaps 1 and 4 are tens of lines of local work;
gap 2 is the hard one, and until gap 2 is closed, filling the other three converts
"1,168 launches were not timed" into "this many threads were not timed", and a thread
count is not an access count.

### What the size looks like from outside the tool

**This subsection is inference, not a code fact.** The first live run registered
61,064,245,248 bytes and charged 0 ns. Qwen3-30B-A3B is a Mixture-of-Experts model with
about 30.5 billion parameters in total and about 3.3 billion active per token; at two
bytes per parameter in BF16 that is about 61.0 GB of weights, which agrees with the
registered byte count to within 0.1%, and about 6.6 GB read per token, which is
1,611,328 pages of 4 KiB.

- At the `cd8p-vmem-p50` first-page cost of 11,133 ns, fully serial: 17.94 s per token.
- At the parallelism the profile declares, 32 channels times 8 dies, so 256 ways: about
  70 ms per token.
- At the top bandwidth point of the OCP specification, 3.072 TB/s (Table 4, page 16),
  as pure bandwidth: about 2.15 ms per token.

The first live run generated 128 tokens, with measured generation time between
1.4156 s and 1.8269 s. So if the weights really sat on HBF, the media reads alone
should account for something between 0.275 s and 8.97 s, and the tool reported 0 s.
This is not a discrepancy of a few percent in the quantity being measured; the quantity
being measured came out as zero.

## Question two — how hard is the correction

Five routes, split by whether the route can produce a real denominator. The first two
are cheap and cannot; the next three can.

### Route 0 — export the counters that already exist

Call `hbfsim_get_stats` from the vLLM adapter and write `fast_requests`,
`reference_requests` and `fast_modeled_ns` into `result.json`.

Tens of lines, a few hours, no GPU needed to write it and one run to collect it. **Does
not give a denominator, but gives the numerator**, which does not exist today and which
every other route needs.

### Route 1 — count inside the rewritten instructions

Two changes. First, add a counter on the silent path at
`src/cuda_runtime/device/hbf_device.cu` lines 494 to 495, where an address outside every
range is let through. Second, carry the grid and block dimensions from the three
wrapper functions into `KernelLaunch` and `GateDecision` and write them to the coverage
log.

Cost: the first change touches `SharedControlHeader`, which is pinned by
`static_assert(sizeof(SharedControlHeader) == 384)` at
`src/host_service/control_layout.hpp` line 155 and by a row of `offsetof` assertions on
the device side around `src/cuda_runtime/device/hbf_device.cuh` line 166, so
`kControlAbiVersion` goes from 4 to 5. The second change touches five families of
wrapper macros. One to two working days, writable without a GPU, one GPU run to collect.

**Does not give a denominator.** Route 1 counts executions of instructions that were
already rewritten. It cannot see inside a module available only as a compiled binary —
which is all 10,584 launches of the first live run — and it cannot see the `cp.async`
and bulk tensor copy forms described in entry
`01-cp-async-and-bulk-tensor-copy-unmatched.md`, which match neither regular expression.
Route 1 measures the known half more finely and says nothing about the unknown half.

### Route 3 — the vendor's own performance counters

Nsight Compute and CUPTI expose per-kernel counts of executed global load and store
instructions. Candidate metric names are `smsp__inst_executed_op_global_ld.sum`,
`smsp__inst_executed_op_global_st.sum`, and at sector granularity
`l1tex__t_sectors_pipe_lsu_mem_global_op_ld.sum`. **We could not verify these metric
names**: this machine has no CUDA toolchain — `which ncu nvcc nvidia-smi nvdisasm
cuobjdump` returns nothing — so the names have to be checked with `ncu --query-metrics`
on your machine before any of them is used as a number.

Strengths: vendor-supported, follows the installed driver, no instruction rewriting.
Weakness: no address filtering, so the denominator is "global accesses executed by the
whole workload", not "accesses that landed in a registered range". Nsight Compute
replays kernels to collect these metrics, and the resulting interference has to be
measured rather than assumed.

Half a day to find out whether the metrics exist, one to two days to use them.

### Route 2 — a read-only census with NVBit

NVBit is a binary instrumentation framework for NVIDIA GPUs that works at the SASS
layer, so it can instrument a module that exists only as a compiled binary. Use
`nvbit_get_instrs` (`core/nvbit.h` line 346 of the release package, verified) together
with `getMemorySpace()`, `isLoad()`, `isStore()`, `isTMAMem()` and `getSize()` (same
file, lines 78 to 84) to count how many global memory instructions each kernel actually
executed. The release package ships `tools/instr_count`, 299 plus 67 lines, as a
compilable template; changing it to count global accesses only and aggregate by kernel
name is about 300 to 400 lines.

Four things to know before starting.

- **Which denominator this gives.** Without address filtering, the denominator is again
  "global accesses executed by the whole workload". That is still a usable and
  defensible denominator — the NVBit paper reports coverage of 74% to 96%, mean 88%, in
  exactly this form — and it is an upper bound on the accesses we could have missed.
- **A strict denominator needs coexistence.** Filtering by address requires NVBit and
  HBFSim in the same process, because the range base addresses only exist inside an
  HBFSim run. Then the `cuModuleLoadDataEx` wrapper of the launch gate, the PTX
  rewriting through bpftime, and NVBit's own module interception all sit on the module
  load path at once. `/root/hbfsim/49-PTX还是SASS与NVBit对照实验.md` section 4.3 puts
  that at 1.5 to 3 weeks, with hangs rather than clean errors as the failure mode. **We
  do not recommend opening this before 2026-09-15.**
- **Overhead.** The NVBit paper section 6.2 measures 36.4x on average and 112x worst
  case for full instrumentation, dropping to 2.3x with a sampling scheme that
  instruments one launch per distinct grid size. The sampling scheme does not hold for a
  Mixture-of-Experts model, because it assumes launches with the same grid size execute
  the same number of instructions, and expert routing is data-dependent — which is the
  very variability being measured.
- **One untested gate.** The NVBit 1.8 release `README.md` line 59 states
  `CUDA driver version: <= 575.xx`, and the verification platform runs driver 595.84.
  The same README states support up to SM 12.1 on line 54 and CUDA >= 12.0 on line 58,
  so the package contradicts itself. **This has not been tested, and it decides whether
  the whole route exists.** Half a day of machine time settles it either way.

### Route 4 — a static count

Disassemble the cubin with `nvdisasm` and count global memory instructions. **No
denominator**: a static instruction count cannot be turned into a dynamic execution
count, because loop trip counts and expert routing are data-dependent. What a static
count does give is the composition of the coverage gap — what share of the global
accesses in the binary take forms our regular expressions do not handle.

### The five routes side by side

| Route | Effort | GPU needed | Real denominator |
|---|---|---|---|
| 0 — export existing counters | Hours | To collect | No; gives the numerator |
| 1 — count in rewritten instructions | 1–2 days | To collect | No; covers only the known half |
| 3 — vendor performance counters | Half a day to test, 1–2 days to use | Throughout | Yes, whole-workload granularity |
| 2 — NVBit read-only census | Half a day smoke test, 3 days to 2 weeks | Throughout | Yes, same granularity, plus a TMA breakdown |
| 2 with address filtering | 1.5–3 weeks, hang risk | Throughout | Yes, strict, but not before the deadline |
| 4 — static count | 1–2 days | No | No |

**Our suggested order: route 0, then route 3 with `ncu --query-metrics` first, then the
half-day NVBit smoke test, then route 1. Do not open the coexistence route.**

## Question three — what this does to simulation accuracy

### Is the reported time right

**The direction on the missed accesses alone is clear: an access that is skipped runs
at the speed of the GPU's own device memory, so the media time is undercounted.** What
one skipped warp-merged access costs is the modeled page service time minus a real
device memory access:

- Under the `cd8p-vmem-p50` profile with 4 KiB pages: 11,133 ns for the first page and
  (41,495 − 11,133) / 3 = 10,120.67 ns as the marginal cost inside a run, against a real
  4 KiB device memory read of a few hundred nanoseconds. **About 10 µs per skipped
  access; 100,000 skipped accesses is about 1 s of missing time.**
- Under the `nominal` profile with 16 KiB pages, `read_latency_ns` 10,000 and
  `time_scale` 100, multiplied directly at `src/cuda_runtime/device/hbf_device.cu` lines
  385 to 389: **each charge is 10,000 × 100 = 1,000,000 ns, one millisecond. Under that
  profile, 1,000 skipped accesses is 1 s of missing time.** The run that reported 164.70x
  used the `nominal` profile; that is its own entry,
  `13-nominal-profile-time-scale-100.md`.

### Do we know how far off we are

**No, and this is the more serious half of question three.** Three layers, each worse
than the one before.

1. **The denominator does not exist, so "how far off" cannot even be written down as a
   quantity.** All four required numbers are missing, as set out under question one.
2. **The errors do not all point the same way, so even the direction is not safe.** At
   least three effects act at once, with opposite signs and unmeasured sizes:
   - skipped accesses are charged nothing — **reported time too low**;
   - the instrumentation costs time by itself — **reported time too high**. Evidence: in
     the first live run, `modeled: true` was 0, so not one nanosecond of modeled delay
     was injected, and end-to-end throughput still fell by between 37.15% and 51.30%;
   - every charged access pays for a whole page with no residency or cache filter
     (`media_descriptor` at `src/cuda_runtime/device/hbf_device.cuh` lines 274 to 288
     sets `bytes` to `range.page_bytes`), while on hardware only a last-level cache miss
     reaches the media — **reported time too high**, by an upper bound we put at about
     128 times, which is our own inference and not a measurement. See entry
     `03-no-page-residency-filter-across-warps.md`.

   **Added together, today's output is neither an upper bound nor a lower bound on the
   real cost. The sign is unknown.**
3. **The zero-delay baseline does not exist, so the second effect cannot be subtracted.**
   The 164.70x figure compares an instrumented run with delay injected against a clean
   uninstrumented run. The baseline that would isolate the device is an instrumented run
   with the delay set to zero, and that run has not been made. This is item 8 of
   `docs/EXPERIMENTS-NEEDED.md`.

### What the paper can claim while the denominator is missing

**This subsection is our judgement, not a statement about the code.**

Can be claimed:

- **Existence and mechanism.** Delay is injected in place inside a production inference
  stack that was not modified, and the emitted tokens are bit-identical. Both proofs
  record `Output token IDs equal to baseline` as yes, and neither statement depends on a
  denominator. This is the claim that stands on its own evidence.
- **Per-kernel case results**, stated for the kernels that were actually charged.
- **Coverage, provided the unit is written as launches and not as accesses**, with the
  three decision classes reported as they are, including "45.60% admitted without
  timing, 0 timed". Measuring the gap and printing it is stronger than passing over it;
  the 88% figure in the NVBit paper section 6.1 is used in exactly that way.

Cannot be claimed:

- **Any accuracy statement with an error bound.** A sentence of the form "HBFSim
  reproduces hardware to within X%" cannot be written, because there is no denominator
  and no single direction.
- **Any comparison across configurations.** For example "how end-to-end time differs
  across the three bandwidth points", or "how much a different page size helps": the
  share of uncharged accesses differs between configurations and has not been measured,
  so the difference between two configurations contains an unknown term. This one is easy
  to miss, because comparing the tool against itself looks safe.
- **Any one-sided bound.** Not even "this is a lower bound on the real cost", for the
  reason in point 2 above.

Why this matters for review rather than only for us: the comparable tools — FEMU
(FAST '18), Cylon (FAST '26), Quartz (Middleware '15), Accel-Sim (ISCA '20) — all
validate against real hardware, and HBF does not sample until early 2027, so we have no
hardware anchor to begin with. Without a hardware anchor **and** without a coverage
denominator, we can neither say that we match hardware nor say how far from hardware we
are. One of the two missing is survivable; both is not. **That is why we would put the
denominator ahead of raising coverage in the time left**: a gap that is measured and
disclosed can go into the paper, a gap that is half closed by an unknown amount cannot.

## Where we may have read it wrong

1. **The per-launch unit may be exactly the intended contract.** Claim C1 as written in
   `docs/EXPERIMENTS-NEEDED.md` says the coverage decision is taken one launch at a time
   and put on the record. Under that reading, the unit is deliberate and what is missing
   is only the disclosure of how much each record covers — which is a smaller change than
   anything in question two.
2. **The counter export may exist under another name.** We searched `adapters/vllm/` for
   `fast_requests` and `hbfsim_get_stats` only. An indirect call, or a different
   spelling, would not have shown up.
3. **The CUDA graph path may be a deliberate choice** given that the measured runs are
   in eager mode. If graphs are never going to be enabled in the measured configuration,
   the coarse record costs nothing, and the only thing needed is a line in the paper
   saying the measured configuration runs eager.

## What we would like you to confirm

1. Is the per-launch unit deliberate, and is the missing count of skipped accesses
   something you consider in scope before 2026-09-15?
2. May we add the `hbfsim_get_stats` call to the vLLM adapter (route 0)? It is tens of
   lines and it changes nothing about how time is charged.
3. Which denominator route do you want: the vendor performance counters (route 3) or
   the NVBit census (route 2)? We suggest spending half a day on route 3 first, because
   route 3 has no equivalent of NVBit's untested `<= 575.xx` driver limit against the
   installed 595.84.
4. Can you run `ncu --query-metrics` on the experiment machine and send us whether
   `smsp__inst_executed_op_global_ld.sum` and `smsp__inst_executed_op_global_st.sum`
   exist there? We cannot check the metric names from this machine.
5. If neither denominator route works, do you agree that coverage is reported in units
   of launches, with the unit stated in the text, and that no accuracy bound is claimed?

Please tell us which of the points here you agree with, which ones you think we have
read wrong, and which ones you intend to change.


