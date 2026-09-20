# D3 acceptance v3 retrospective

This is a read-only rescore of the immutable canonical D3 development trace.
It did not run a solver, alter raw CSVs, or replace the frozen v1/v2 result.
The reference observation receipt declares `0.001 K` field quantization. The
RC receipt declares no field quantization and emits derived double values, so
v3 uses reference `0.001 K` and candidate `0 K` intervals.

| Method | Aggregate status | Temperature checks | Crossing result | Max TW-MAE K | Max absolute error K | Energy relative residual |
|---|---|---:|---|---:|---:|---:|
| v1 legacy | PASS | legacy full/sensor semantics | legacy full-trajectory probes pass | 0.000260921 worst legacy MAE | 0.000500001 | not separately changed |
| v2 frozen | PASS_WITH_THRESHOLD_AMBIGUITY | 825/825 pass | 47 PASS, 778 THRESHOLD_AMBIGUOUS, 825 NOT_APPLICABLE across repeated window probes | 0.000214699 | 0.000500001 | 3.070e-11 |
| v3 retrospective | PASS | 825/825 pass | 275 PASS, 275 NOT_APPLICABLE after one full-trajectory extraction per sensor/threshold | 0.000214699 | 0.000500001 | 3.070e-11 |

V3 matches crossings globally before assigning half-open windows. Development
has no remaining quantization-indeterminate crossing. This does not convert the
unqualified spatial reference into a qualified reference or a MODEL_FREEZE.
The existing train v3 result remains `INDETERMINATE_QUANTIZATION`: one
`hbm1.die8` 301 K up-crossing interval straddles the 39 s phase boundary.

Evidence is under
`eq3_thermal/plans/isolated-maintenance-campaign-v1/points/D3-DEVELOPMENT-V3/`.
`manifest.json` binds both canonical CSVs, the energy receipt, method and tool;
`v3_score.json` retains all window/crossing records and `result.json` is the
compact comparison above.

