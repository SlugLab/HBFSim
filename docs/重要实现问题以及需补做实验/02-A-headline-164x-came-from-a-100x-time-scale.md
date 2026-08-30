# The 164.70x figure was produced under a profile that multiplies every delay by 100

**Severity: A.** The number is the headline result of the live run, and the factor that
sets its size does not appear in the document that reports it. A reviewer who opens
`configs/profiles/nominal.json` finds the factor in one line.

**How sure we are.** Read from the primary source: the profile values, the code lines
that apply them, and the two timing figures in the proof document. **Needs your
confirmation:** that the run reporting 164.70x used `configs/profiles/nominal.json`. We
read that binding from the run configuration, and you know the run.

**Does this conflict with the OCP specification? No.** But it does interact with item 15
of `15-experiments-we-must-add-before-submission.md` (item 11 before that document was
renumbered), which asks for the timing profiles to be re-anchored to
the three bandwidth points of the specification, 0.384, 1.536 and 3.072 TB/s (Table 4,
page 16). A profile with a scaling factor of 100 does not correspond to any of the
three.

All code quoted here was read from the remote branch `origin/hybrid`.

## What we read in the code

`configs/profiles/nominal.json` sets a page size of 16 KiB, `read_latency_ns` of 10,000
and `time_scale` of 100. `src/cuda_runtime/device/hbf_device.cu` lines 385 to 389
multiply the two directly, so **each charged access costs 10,000 × 100 = 1,000,000 ns,
one millisecond**, rather than the 10,000 ns the latency field alone suggests.

`docs/proofs/2026-08-11-vllm-exact-live-delay.md` reports baseline generation of
0.269996 s against 44.469084 s with timing injection, a ratio of 164.70x, over 24 timed
launches of `fused_moe_kernel`. The document does not mention `time_scale`.

## Why it looks questionable to us

The 164.70x figure is the number a reader remembers from that run, and two different
statements can be attached to it:

- "our tool can impose a delay large enough to dominate a real inference run, and the
  output is still bit-identical" — for which the scaling factor is irrelevant and the
  number is a demonstration;
- "this is roughly what running these weights on HBF would cost" — for which the scaling
  factor changes the number by two orders of magnitude.

As written, the proof document supports the first statement and a reader can easily take
it for the second. The fix may be one sentence rather than a rerun, which is why we are
asking rather than changing anything.

## Which direction the effect goes

The reported slowdown is larger than the calibrated media parameters alone would produce,
by whatever share of the 44.2 s of extra time came from charged accesses. We cannot
divide the 44.2 s between charged delay and the overhead of the instrumentation itself,
because the three request counters were not exported for that run — see entry
`05-A-accounting-unit-is-the-kernel-launch.md` — and because no zero-delay baseline run
exists, which is item 12 of `15-experiments-we-must-add-before-submission.md`
(item 8 before that document was renumbered).

## Where we may have read it wrong

1. `time_scale` may be a deliberate and documented control for making the injected
   effect observable in a short run, in which case nothing about the code is wrong and
   only the write-up needs the factor stated.
2. The run may have used a different profile than the one we believe it used.
3. The 16 KiB page size of `nominal` puts that profile on the parameterized model rather
   than the measured curve, so the measured `cd8p-vmem-p50` breakpoints are not involved
   in the 164.70x figure at all; if you intend the paper's headline numbers to come from
   the measured curve, that is a separate decision from the scaling factor.

## What we would like you to confirm

1. Which profile did the 164.70x run use?
2. If the run used `nominal`, do you want the factor of 100 written into the proof
   document and into the paper wherever 164.70x appears, or do you want the run repeated
   on a profile with `time_scale` of 1?
3. What is `time_scale` for? Knowing the intent decides whether the factor is a bound to
   disclose or a parameter to remove from the headline result.

Please tell us which of the points here you agree with, which ones you think we have
read wrong, and which ones you intend to change.
