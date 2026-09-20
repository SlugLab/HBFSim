# Isolated MQSim fix and limit register

This register separates software defects, backend/model limitations, numerical
qualification limits, domain failures, resource blocks, and claims that were
not reproduced after a bounded fix. Evidence paths are relative to
`eq3_thermal/plans/isolated-maintenance-campaign-v1/` unless a path starts with
`../`. Fixed tests and startup checks establish only their named invariant;
they are not counted as a homogeneous experiment pass.

The machine-readable companion is
`eq3_thermal/plans/isolated-maintenance-campaign-v1/CURRENT_EVIDENCE_INDEX.json`.
It is a receipt index, not a replacement for immutable raw data or the final
campaign aggregation.

## Repair and limitation register

| Item | Classification | Required invariant or capability | Reproduction and evidence | Current state and remaining limit |
|---|---|---|---|---|
| Maintenance source version and ownership | `CONFIRMED_BUG` | A maintenance read must commit only if the logical mapping still has the captured version; its source block must remain unavailable to GC until terminal state | `points/backend-generation-cpp-test-v1/result.json`; `points/backend-generation-service-test-v2/result.json`; `points/BACKEND-GENERATION-FROZEN/result.json` | Fixed in the isolated backend with monotonic generation, overflow failure, source pinning, pinned-GC rejection, and an actual scheduled foreground-write race. The guarantee is `METADATA_VERSION_VALIDITY`; no payload equality is claimed. |
| Maintenance energy source label | `CONFIRMED_BUG` | Actual maintenance events must be categorized from their maintenance identity without changing energy totals | `points/energy-fixed-v2/result.json`; actual OCP loop receipt `points/OCP4K-LOOP-PILOT01/DONE.json` | Fixed producer lookup prefers `maintenance_request_id`, with legacy fallback. The immutable PILOT03 raw keeps its older `BACKEND_BACKGROUND` label; stored joules and A3 remain unchanged. |
| Recovery backlog treated as absent demand | `CONFIRMED_BUG` | Carried queued work remains demand after the arrival interval ends | `points/policy-recovery-demand-repro-v1/result.json`; `points/policy-recovery-demand-fixed-v1/DONE.json`; `points/MAIN-v1-partial-analysis/partial-analysis.md` | Fixed demand accounting uses delivered work plus backlog. The old P2 Stress observations remain valid evidence of that implementation, including 9,074 censored requests, but cannot serve as an unqualified comparison of the intended policies; affected pairs need new campaign IDs. |
| GPU key included in memory-stack coverage | `CONFIRMED_BUG` | The separate GPU thermal entity must not be required to appear in the configured memory stack set | Preserved failure `points/Q1-PILOT01/FAILED.json`; fixed wrapper check `points/thermal-wrapper-fixed-v1/result.json` | Fixed. The first pilot is retained as a wrapper failure, not a thermal-domain result. |
| Lowercase native NAND technology token | `CONFIRMED_BUG` | Generated profiles must use a token accepted by the native MQSim parser | `campaign-ocp4k-v2/PAUSED.json`; `campaign-ocp4k-v2/SLC-STARTUP-FIXED02/DONE.json`; `campaign-ocp4k-v3/PREFLIGHT_VALIDATION.json` | Fixed at source head `9507fe2dd6f2ec1162c7063614ad66d870039bc1`. The v2 attempts completed zero points; they are startup failures, not numerical failures. |
| Default product path isolation | `NOT_REPRODUCED_ALREADY_FIXED` | Maintenance-off operation of the isolated executable must preserve the default service result, and an independent rebuild must link only its private MQSim archive | `points/AB-PROCESS-GENERATION-02/result.json`; `points/BACKEND-INDEPENDENT-REBUILD01/result.json`; `points/AB-PROCESS-INDEPENDENT-REBUILD01/result.json` | The bounded A/B cases are exact and the fresh-path rebuild passed. This supports default-off isolation for the tested request sets, not universal equivalence over all MQSim configurations. |
| Full-capacity identity | `DESIGN_LIMITATION` | Logical addressability, physical spare, bad-block allowance, and endurance capacity must remain distinct | `points/GEOMETRY4K-FULLCAP01/result.json`; `points/GEOMETRY4K-PHYSICAL-BLOCK02/result.json`; preserved assumption failure `points/GEOMETRY4K-BOUNDARY01/FAILED.json` | Actual MQSim accepted 4 HBF stacks x 512 GiB logical capacity with 4 KiB pages. Physical spare and bad-block capacity remain unknown. `BANK_AS_MQSIM_PLANE_V1` and 256 pages/block are study projections, and zone-FTL ordinals are not direct physical block/page identities. |
| HBM, external fabric, and GDDR capability | `DESIGN_LIMITATION` | Each backend and link may claim only behavior it actually produces | `points/Q1-WEIGHT-MAINT-PILOT03/DONE.json`; `points/OCP4K-LOOP-PILOT01/DONE.json` | HBF requests and maintenance use the isolated actual MQSim path. HBM remains a parameterized scenario model; fabric is an external basic model; external GDDR has physical identity but unavailable service and package temperature. Unknown energy inputs remain `UNKNOWN`, not zero. |
| A1 source-group response | `DESIGN_LIMITATION` | Source ablation must preserve the same C/G network and state exactly what was removed | `points/A1-DOMAIN-ANALYSIS01/result.json` | Completed with entity-mean superposition error `8.606e-11 K`. It removes other source inputs; it is not a disconnected physical-domain experiment, nodewise hotspot sum, or control-policy reclosure. |
| A2 actual shared maintenance versus ideal replay | `DESIGN_LIMITATION` | Actual backend maintenance must be distinguished from a counterfactual replay that consumes no current-engine maintenance resources | Baseline `points/Q1-WEIGHT-MAINT-PILOT03/DONE.json`; paired audit `points/A2-PILOT03-LOOP01/POSTCHECK.json` | Baseline issued and committed 64 actual jobs. A2 issued zero backend jobs and replayed 64 fixed facts with `UNKNOWN_REPLAY` mapping/version semantics. Ten of 4,032 foreground completions moved 620 ns earlier; aggregate completion count, p95, stored peak temperature, and total energy were unchanged. This localized outcome does not prove independence generally. |
| A3 thermal replay identity | `NOT_REPRODUCED_ALREADY_FIXED` | A full-window replay must preserve all requested sensor frames under the same discrete equation | Preserved harness failure `points/A3-PILOT03-REPLAY01/FAILED.json`; successful receipt `points/A3-PILOT03-REPLAY02/RUN_RECEIPT.json` | Full 2 mm discrete equivalence passed for 500 frames and 275 sensors; maximum sensor difference was `4.718e-12 K`. Shared equation and Eigen family mean this is not an independent physical reference or P2 qualification. |
| P2 adjacent-grid accuracy | `NUMERICAL_ACCURACY_LIMIT` | Retained reference comparisons must meet the original 0.25 K criterion before reference qualification | `../campaign-v1/index.json`; `../minimal-repair-v1/P2-COMMON-FIRST4S-DIAGNOSTIC.json`; source summary `docs/eq3_thermal/DECISION_EXECUTION_V2_RESULT.md` | The old criterion remains failed; later full-window diagnostics report a 2-to-1 mm maximum difference of 2.069 K. Conditional engineering use does not convert this into reference qualification: `MODEL_FREEZE=false`. |
| Development reference above 400 K | `DOMAIN_FAILURE` | A trajectory outside the declared 300--400 K constant-property domain must fail with evidence | `../campaign-v1/index.json`; `../minimal-repair-v1/points/P2-ORIGINAL-DOMAIN-REPRO/result.json` | The independent 2 mm development reference reached 400.911 K and first crossed at 31.14 s. It remains failed; no clamp, threshold widening, or truncated pass was applied. |
| Original full 1 mm, 100 s attempt | `RESOURCE_BLOCKED` (historical, resolved) | The full reference must run as one qualified trajectory under the point's declared budget | `../campaign-v1/index.json`; `../minimal-repair-v1/points/P2-COMMON-FIRST4S/result.json`; completed R03 receipt `../decision-execution-v2/RUN_INDEX.json`; summary `docs/eq3_thermal/DECISION_EXECUTION_V2_RESULT.md` | The original 4 s prefix took 328.012 s and exceeded that campaign's 600 s budget projection; it was not split to bypass the watchdog. The later approved 3,600 s R03 completed all 5,000 solve frames and 275-sensor observation: 2,763.597 s solve, 833.560 s observation, and 7,735,140 KiB solve peak sampled RSS (about 7.38 GiB). The resource block is resolved; spatial qualification still fails and `MODEL_FREEZE=false`. |

## Evidence inventory by use

| Evidence set | Kind | What is established | What is not established |
|---|---|---|---|
| Generation/pin, source-label, backlog, GPU-key, parser-case receipts | Fixed tests and minimal reproducers | The named software invariant is reproduced and repaired | Campaign performance, physical calibration, or topology-wide benefit |
| Independent rebuild and process A/B receipts | Build reproducibility and bounded compatibility | The isolated source/patch path rebuilds and the tested maintenance-off request sets are exact | Cross-platform reproduction or equivalence for every native configuration |
| PILOT03 | Legacy 16 KiB engineering pilot | One actual closed CPU loop with real shared MQSim maintenance | OCP 4 KiB geometry, P2 qualification, or product performance |
| OCP4K-LOOP-PILOT01 | 4 KiB full-logical-capacity engineering pilot | One actual closed CPU loop using the new logical geometry and mapping projection | Physical spare, calibrated HBM/fabric, external-GDDR temperature, or a formal matrix result |
| A1 | Source-domain ablation | Bounded linear source-response accounting | Network disconnection or policy reclosure |
| A2 | Paired ideal-resource counterfactual | Difference between actual shared maintenance and fixed replay at the frozen PILOT03 input | A general independent-resource performance claim |
| A3 | Paired full-window discrete replay | Same-equation sensor/energy transport equivalence | Independent physical reference, 1 mm result, or P2 model freeze |
| MAIN v1 partial | Superseded legacy campaign fragment | Nine conditional 16 KiB Q1 results and direct evidence of the old recovery bug | Four-topology completion or a valid affected P2 comparison |
| OCP4K v3 | Live campaign | Validated 66-point queue bound to head `9507fe2` | Any aggregate conclusion before the root task freezes and reviews `campaign-ocp4k-v3/STATUS.json` |

## Current campaign boundary

`campaign-ocp4k-v3/` is live and mutable. Its preflight and engineering lock may
be cited as identity evidence, while point completion and aggregate claims must
wait for the root task's final index. Current partial status must not be combined
with the superseded 16 KiB MAIN v1 rows, the standalone pilots, or the A1/A2/A3
ablations and described as one homogeneous pass count.
