# User-authorized iterative engineering validation

2026-09-19 user clarification: “如果在逐次验证下效果变好可以持续验证”.
This authorizes evidence-driven small engineering iterations, not a formal
parameter scan, GPU load or a self-signed experiment approval record.

Budget correction: the first phase used9 independent numerical configurations,
not12. Three pre-solver setup failures are separately logged. The previously
prepared four-point batch remains NOT_RUN/NOT_APPROVED; it is not being split
into batches. The user's new direction selects a sequential one-factor numerical
convergence question instead. No new physical or controller parameter is varied.

## Next single check, preregistered before execution

Same stock3DICE binary240b598c…27244a7, same40mm homogeneous scenario,1mm grid,
same complete4s training power trace and0.5s observations. Only solver dt changes
from0.05s to0.025s. Compare consecutive deltas against existing0.1s→0.05s outputs
at identical timestamps, after hash/input/sample-count checks.

Continue eligibility: all four quantities—region aggregate MAE, region maximum
difference, grid-hotspot MAE and maximum difference—shrink to at most80% of the
previous difference (zero differences stay within1e-12). This is an engineering
improvement rule, not a claim of absolute/physical accuracy. RC acceptance remains
FAILED; time-step improvement alone cannot fix spatial model coarse-graining.

One CPU process, OMP/BLAS1, <=300s wall, <=4GiB address space, <=1GiB output;
overall original CPU-hour/RAM/disk boundaries remain. No GPU, fit, threshold,
power, topology, cooling or semantic change. Failed checks stop numerical
expansion and trigger diagnosis. New raw directories only; preserve failures.
Do not proceed merely because the result better supports a desired story.

## Observed first step and continuation stopping rule

0.05→0.025s run completed: region MAE difference0.182556K (previous0.347097K),
region max0.672K (previous1.280K), hotspot MAE0.531375K (previous1.012125K),
hotspot max0.910K (previous1.766K). All improvement ratios0.515–0.526 pass.
Receipt: workspace runs/p2-iterative-train-1mm-dt25ms plus p2-iterative-checks.

Before subsequent runs, fix a precision stopping target: region and grid-hotspot
maximum consecutive-step differences <=0.25K, alongside the same <=0.8 relative
improvement rule. This reserves numerical-reference margin below the existing
1K/2K RC acceptance goals; it is not a proven bound on continuum error.
Next permissible step is0.0125s, then0.00625s only if the preceding improvement
rule passes and precision target is not yet reached. No parameter fitting or
other axis changes. These two steps fit the remaining original configuration
allocation; further continuation, if needed, retains the user's new adaptive
engineering authorization and unchanged resource boundaries, not formal-matrix
approval. Stop immediately on failed validation or a budget/safety condition.

## Result: precision stopping target reached

| Refinement | Region MAE difference K | Region max difference K | Grid-hotspot max difference K |
| --- | --- | --- | --- |
| 100→50ms | 0.347097 | 1.280 | 1.766 |
| 50→25ms | 0.182556 | 0.672 | 0.910 |
| 25→12.5ms | 0.093917 | 0.345 | 0.462 |
| 12.5→6.25ms | 0.047819 | 0.175 | 0.232 |

All three newly executed steps passed the predefined improvement check. Stop
halving dt now because both maximum differences reached<=0.25K, not because of
an arbitrary run count. This is relative timestep convergence at1mm spatial
mesh for the fixed training trace; no claim of converged geometry/physical
parameters or improved RC fit. The viewed heldout was not used by these steps.
Actual total:12 numerical configurations plus3 pre-solver setup failures.
New raw directories: p2-iterative-train-1mm-dt25ms,
p2-iterative-train-1mm-dt12p5ms, p2-iterative-train-1mm-dt6p25ms. Each preserves
input and solver output hashes, commands, CPU/RSS and exit receipts. Read-only
delta analyses are under p2-iterative-checks. No old raw output overwritten.
