# Nothing compares the fast path against the detailed MQSim path

All code quoted here was read from the remote branch `origin/hybrid`. This point
covers the same subject as item 4 of `docs/QUESTIONS-FOR-COLLABORATOR.md`;
answering either place answers both.

The two routes have fixed names in this project. The fast path is the timing model
that runs on the GPU itself and holds the kernel in a spin-wait until the modeled
delay has passed. The detailed MQSim path is the route through MQSim, the detailed
flash device simulator this project adapts and runs as the reference.

## What we read in the code

Searching `src/`, `include/` and `tests/` on `origin/hybrid` for `consistency`,
`cross_check`, `agreement` and `divergence` returns one hit, in
`src/cuda_runtime/coverage.cpp`, where the word carries an unrelated meaning.

`tests/cpu/calibrator_test.cpp` is 63 lines in total and asserts four things:
behaviour during the warm-up period, the determinism of the sampling, the
monotonic relation between fast-path latency across the three timing profiles, and
the calibration statistics. No assertion puts an output of the fast path and an
output of the detailed MQSim path side by side.

## Why it looks questionable to us

The paper outline `paper/35-FAST27论文大纲与逻辑线.md` promises this agreement in
three separate places, all checked against the current file:

- Line 109, in the contribution list: `(d) 详细路径与快速路径两档时序模型及其一致性`.
- Line 221, in the design response to the timing challenge: the invariant is
  stated as `延迟不再由公式算出，而是从真实硬件量出来的曲线上取，并且可以逐点与详细
  参考路径对照`, and the same paragraph ends with `这一段要同时给出两条路径的结果
  一致性与 20.8x 的差距`.
- Line 287, in the evaluation question list: `同时报两条路径的结果一致性`.

An earlier draft of this file cited a sentence at line 130 reading
`而两条路径之间必须有可检验的一致性`. That sentence was replaced when the
motivation section was rewritten and is no longer in the outline; the three
citations above are the current wording. The point itself is unchanged: a
document promises something the code does not yet contain, rather than the code
doing something unexpected.

## Which direction the effect goes

None on any modeled number: nothing here changes a latency. What is missing is the
evidence that the fast path reproduces the detailed MQSim path. Without it, the
argument for using the fast path in the experiments rests on the design rather than
on a measurement, and a reviewer is entitled to ask for the measurement.

## Where we may have read it wrong

The check may exist under wording we did not search for, or may live outside
`src/`, `include/` and `tests/` — in a script, or in a notebook. It may also be
deliberately scheduled for a later stage, after the timing model stops changing,
since a check written now would have to be rewritten.

## What we would like you to confirm

1. Was the check left for a later stage, or was it missed?
2. If it is to be written: which quantity should be compared — per-access latency,
   the distribution over a run, or total kernel time — and what counts as
   agreement? A criterion we invent on our own would end up being the criterion the
   paper reports, so we would rather have yours.
