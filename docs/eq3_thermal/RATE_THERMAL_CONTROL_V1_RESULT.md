# EQ3 rate-driven coupled thermal control v1

> **Status: COMPLETE_CONDITIONAL_SIMULATION.**
>
> All 41 indexed executions completed: two topology pilots and the strict
> 39-point main whitelist. Execution/accounting and all registered conservation
> checks passed. Results remain conditional fluid-service and incremental-heat
> simulations; they are not native-backend throughput or a thermal model freeze.

## Scope and evidence boundary

This stage answers the user-authorized question: how synthetic stored-weight
read pressure, modelled service budgets and thermal guards interact in the full
coupled package RC network. It uses an isolated, default-disconnected fluid
byte service. It does not issue MQSim commands and does not claim native NAND,
fabric or product throughput.

The frozen stage inputs are:

- Environment: `eq3-thermal-cpu-v1`, one CPU process, one OMP/BLAS thread,
  4 GiB address-space limit per point and no GPU use.
- Source revision: `cf22dadc0611a1a6d16d59eb8af6f5ec1b541a0a`.
- Thermal executable SHA-256:
  `6e84ef0798070a74e468a137d80c68c3b2789c2643b3115493b17afe78febe25`.
- 41 indexed points: two 8 s active + 4 s recovery pilots and 39 main points
  with 20 s active + 10 s recovery.
- Main design: two direct topologies, three model extents, continuous and
  200 ms-period/100 ms-on equal-mean burst demand, and three existing control
  strategies at 16 full stored-weight scans/s.
- User-confirmed amendment: three additional all-HBF, 235B, continuous,
  32-scan/s points. These match the per-stack offered pressure of four HBFs at
  16 scans/s; they do not replace the original 36 points.
- Incremental read energy: 40 pJ/B array plus 10 pJ/B base. Idle, GPU
  background and external-GDDR power remain unknown or omitted.
- Initial thermal reference: 300 K; declared domain 300–400 K; 20 ms thermal
  and control windows.

The source of record for this frozen design is
`eq3_thermal/plans/rate-thermal-control-v1/{PREFLIGHT.json,RUN_INDEX.json}`.
`REPORT_CONTRACT.md` was registered before main results and governs the final
comparisons. Main analysis used the `RUN_INDEX_PHASE_MAIN_WHITELIST` at analysis
commit `6a24d08`, which excluded both pilots and prevented directory discovery
from mixing diagnostic and main results.

The workload uniformly stripes offered weight bytes across the declared stacks
and channels, using integer carry and a rotating remainder; cumulative channel
counts differ by at most one byte. This is an explicit workload assumption for
the rate-pressure study. It is not evidence that a real LLM stores or accesses
weights with that placement.

## Actual interface closure

| Interface | Actual producer | Actual consumer | Enablement | Current capability and gap |
|---|---|---|---|---|
| Model-sized offered-byte windows | `experiments/eq3_rate_thermal/model_workloads.py::build_workload` | `run_controlled.py`, then `FluidService.advance` | Explicit `--workload`; isolated runner only | Exact integer-byte, 20 ms continuous or equal-mean burst demand over 4/8 stacks × 16 channels. Offered demand may exceed capacity and is not achieved throughput. |
| Per-channel FIFO service and queue facts | `experiments/eq3_rate_thermal/fluid_service.py::FluidService` | `run_controlled.py` energy mapping and policy fact adapter | Constructed only by `run_controlled.py`; no default MQSim registration | Persistent backlog, channel capacity, stack budget, byte conservation and window-quantized byte-weighted delay. No command lifecycle, NAND timing, fabric delivery or endpoint-group arbitration. |
| Served bytes to component energy | `run_controlled.py::_served_energy` using the frozen profile channel map and 40/10 pJ/B | Existing `ThermalService.advance`, which sends component `ENERGY` before `ADVANCE` | Explicit `--profile`, `--model-dir` and `--thermal-binary` | Actual mapped die/base sources in the unchanged coupled network. Queued bytes consume no read energy. Idle/channel-activation/host-ingress/GPU/GDDR energy is unavailable. |
| Coupled temperature and guard facts | Existing persistent thermal service plus `experiments/eq3_maintenance/thermal_client.py` | `run_controlled.py` output and existing `ReadRatePolicy.evaluate` | Explicit isolated thermal binary and model paths | Full registered 2 mm package network, 20 ms causal advance, HBF 80/90/105 °C and GPU 90/100/110 °C research guards, 20 ms action delay, 100 ms recovery dwell and 2 K hysteresis. These are research limits, not product guarantees. |
| Future-window byte budget | Existing `experiments/eq3_maintenance/read_rate_policy.py::ReadRatePolicy` | Next `FluidService.advance` call | Explicit `--strategy` among the three frozen policies | Completed-window facts affect only the next window. Fluid utilization and saturation are explicitly modelled facts, not native backend observations. Existing policy code is reused unchanged. |
| Point orchestration and immutable receipts | `prepare_control_stage.py` and `launch_control_stage.py` | `run_controlled.py`, stage index and later derived reporting | Explicit stage invocation; output directory must be new | Input/source/model hashes, STARTED manifest, JSONL facts and DONE/FAILED receipts. It does not enable the path in default MQSim or HBFSim runs. |

Every interface above has an actual runtime consumer in this isolated stage.
The new fluid path is therefore connected for Q1/Q4 direct thermal-control
experiments, while remaining default-off and separate from native MQSim.

## Pilot release evidence

The registered `PILOT_REVIEW.json` reports
`PILOT_REVIEW_PASS_MAIN_RELEASED`. These are engineering release checks, not
main-matrix conclusions:

| Pilot | Final backlog | Peak coupled temperature | Served-to-thermal energy error | Wall time | Output |
|---|---:|---:|---:|---:|---:|
| mixed-direct, 235B, continuous, 16 scans/s, feedback | 19,685,794,447,360 B | 362.690441 K | 3.5470e-11 J | 35.675 s | 90,688,829 B |
| all-HBF direct, 235B, continuous, 16 scans/s, feedback | 0 B | 363.191949 K | 3.0468e-11 J | 21.518 s | 132,306,653 B |

Both pilots had zero byte-conservation error. Backlog in the mixed pilot was
registered as expected saturation evidence rather than an execution failure.
The pilot review raised the evidence-based per-point output allowance and
projected the main wall time; it did not change the scientific inputs. The
pilots use shorter time windows and different package geometries, so they are
diagnostic release evidence and are not pooled with the main matrix.

## Main completion and acceptance

The stage-level execution completed **41/41 points with no failed point**. The
strict analysis completed **39/39 main points**, 39 policy-pair comparisons and
six registered cross-topology comparisons. All byte, delay-histogram and
50 pJ/B energy checks passed. The steps used for acceptance were:

1. Freeze the completed main index and enumerate every COMPLETED, FAILED,
   DOMAIN_FAILURE or resource-blocked point. Do not silently omit a strategy.
2. Verify source, profile, workload, scenario, model and executable hashes
   against `RUN_INDEX.json` for every completed point.
3. Verify per-window and cumulative offered = served + backlog conservation and
   served-byte energy = thermal input within the registered tolerance.
4. Preserve declared-domain failures as failures. Do not clamp, truncate or
   widen 400 K.
5. Generate every aggregate table and plot solely from immutable point outputs;
   no thermal solve may be repeated for reporting.

Main completion receipt: `rate-thermal-control-v1/DONE.json`  
Completed/failed counts: **41 completed / 0 failed**, including two pilots  
Frozen RUN_INDEX SHA-256:
`e4cd532b875e3de09e5cba8521d644bd660a90bec026a7d617c5559ec422f0da`  
Strict analysis receipt: `MAIN-ANALYSIS01/ANALYSIS_RECEIPT.json`

## Required final measurements

The final report must show values separately for active and full observation
windows. A zero-arrival recovery interval may still serve backlog and generate
heat, so it is not automatically a passive-cooling interval.

The six equal-per-stack-pressure high-load rows are the most direct saturation
comparison. `Active rate` covers the fixed 20 s active interval; delivered,
backlog, delay, final temperature and energy cover the full 30 s observation.
TB uses decimal bytes. `Late CV/zero` covers the preregistered 10–20 s active
second half.

| Topology / strategy | Active rate TB/s | Active delivered TB | Full delivered TB | Final backlog TB | Fluid P95/P99 s | Peak/final K | Energy J | Late CV / zero fraction |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| 4-stack guard only | 3.4775 | 69.550 | 101.069 | 49.391 | 15.56 / 17.38 | 365.519 / 361.914 | 5,053.440 | 0.690 / 0.238 |
| 4-stack thermal hysteresis | 3.2456 | 64.911 | 95.017 | 55.443 | 16.42 / 17.28 | 363.166 / 362.159 | 4,750.848 | 0.0866 / 0 |
| 4-stack rate feedback | 3.2052 | 64.103 | 93.853 | 56.607 | 16.48 / 17.56 | 363.162 / 361.507 | 4,692.634 | 0.125 / 0 |
| 8-stack guard only | 6.1686 | 123.372 | 178.176 | 122.744 | 17.14 / 17.96 | 365.551 / 363.307 | 8,908.800 | 1.119 / 0.556 |
| 8-stack thermal hysteresis | 6.0518 | 121.037 | 176.210 | 124.710 | 17.34 / 18.12 | 363.247 / 361.653 | 8,810.496 | 0.352 / 0.110 |
| 8-stack rate feedback | 5.8872 | 117.744 | 169.230 | 131.690 | 17.62 / 18.62 | 363.222 / 357.791 | 8,461.517 | 0.403 / 0.126 |

“Delay” means byte-weighted, 20 ms-window-quantized fluid delay. Outstanding
bytes are censored and must be reported alongside the quantiles. Backend
latency remains `UNKNOWN`.

## Frozen comparisons

### Same offered demand: four versus eight HBF stacks at 16 scans/s

Compare Q1 mixed-direct and Q4 all-HBF direct only when model, pattern, policy,
weight extent, total offered bytes and observation window match. Report service,
backlog, temperature, energy and delay together.

At 235B continuous 16 scans/s, the eight-stack configuration halves offered
bytes per stack and completes all 150.460 TB under every policy; the four-stack
configuration remains backlogged by 49.391–56.607 TB. Relative to four stacks,
eight stacks deliver 49.391, 55.443 and 56.607 TB more for guard-only,
hysteresis and feedback, while peak temperature changes by only +0.037,
+0.083 and +0.065 K. This comparison simultaneously changes package geometry,
thermal network, source count and per-stack pressure. It cannot isolate a
causal “stack-count effect.”

### Same per-stack saturated pressure

Compare four-HBF 235B continuous 16 scans/s with eight-HBF 235B continuous
32 scans/s for each of the three policies. The amendment equalizes offered
pressure per HBF stack; it does not make the geometries or coupled neighbors
identical.

All six rows remain saturated at 30 s. Eight stacks receive twice the total
offered bytes and may dissipate roughly twice the aggregate served-byte power;
equal per-stack offered pressure does not mean equal package power. Guard-only
delivers more and leaves less backlog, but its late-window service oscillation
and peak temperature are larger. In four stacks, feedback lowers peak only
0.0048 K relative to hysteresis while delivering 1.164 TB less. In eight
stacks, it lowers peak 0.0252 K while delivering 6.980 TB less and increases
late CV and zero-window fraction. No one policy dominates throughput,
temperature and stability.

### Continuous versus equal-mean burst

Paired workloads have exactly equal total offered bytes. Compare transient
peak/final temperature, served bytes, backlog, delay and second-half rate
stability. Do not infer a burst benefit from temperature alone.

All 7B and 72B points deliver every offered byte and end with zero backlog.
Continuous input is identical across the three policies: aggregate active
rates are 0.243700 TB/s for 7B and 2.326599 TB/s for 72B. At 7B, bursts preserve
delivery and latency but raise peak by 0.2915 K in mixed and 0.1457 K in
all-HBF. At 72B, bursts raise guard-only/hysteresis peaks by 2.7831 K in mixed
and 1.3906 K in all-HBF. Feedback reduces those burst increments to 0.2421 K
and 0.1206 K, with a 100 ms increase in fluid P99. At 235B/16 scans/s, burst
and continuous pairs have identical final delivery/backlog; peak differences
are negligible (below 0.008 K in all-HBF), while P99 rises 20–60 ms. Persistent
saturation and budget control largely mask the original burst shape.

### Guard and feedback behavior

For every policy, report guard-state durations, zero-budget fraction, active
and second-half throughput stability, backlog and recovery service. Lower
temperature with less completed work or more censored backlog is a tradeoff,
not an unconditional improvement.

The high-pressure table reports the preregistered late CV and zero-service
fractions. Guard-only trades higher delivery for sharper stop/restart behavior
and about 2.3 K higher peak than the smoother policies. Temperature reduction
coincides with lower completed work and greater censored backlog, so it is not
scored as an unconditional benefit.

Uniform input does not guarantee uniform temperature. In mixed continuous 72B,
all stacks deliver exactly 11,632,992,583,680 B with no backlog, yet inner
stacks peak at 348.241 K and outer stacks at 341.801 K, a 6.440 K spread. The
stage-level `STACK_DISTRIBUTION_REVIEW.json` verifies zero cumulative offered-
byte imbalance across stacks for all 39 points. This lower-pressure temperature
spread therefore reflects the registered geometry and thermal coupling rather
than an input stripe imbalance. The analogous 7B
spread is 0.675 K. At mixed continuous 235B, peak spreads are 0.611 K for
guard-only, 8.055 K for hysteresis and 8.152 K for feedback; feedback per-stack
delivery ranges from 22.764 to 24.163 TB. The generated all-HBF geometry is
symmetric: even at 235B/32 scans/s its peak spread is below 1.5e-10 K.

## Four-topology, three-axis status

| Topology | Configuration | Thermal behavior | System behavior |
|---|---|---|---|
| Q1 mixed-direct | Registered mixed HBM/HBF configuration and frozen channel map | New fluid stage closes served-byte sources through the full mixed 2 mm coupled network | Direct per-stack FIFO/control behavior completed as modelled fluid. Native MQSim evidence remains a separate earlier result. |
| Q2 4+4 relay | Existing relay configuration remains available | Mixed package network exists, but this new fluid stage does not account for partner-endpoint energy | Shared relay endpoint arbitration is unavailable in fluid v1. Retain prior native CPU routing/admission evidence; do not relabel Q1 fluid results as Q2. |
| Q3 four-pair DASH | Existing DASH configuration remains available | Mixed package network exists, but this new fluid stage does not account for DASH partner sources | Shared DASH endpoint arbitration is unavailable in fluid v1. Retain prior native CPU routing/admission evidence; do not relabel Q1 fluid results as Q3. |
| Q4 8-HBF direct plus external physical GDDR | Registered all-HBF package model; physical GDDR remains external | New fluid stage closes served-byte sources through the full generated 8-HBF 2 mm coupled package network | Direct per-stack FIFO/control behavior completed as modelled fluid. External-GDDR service and temperature are unavailable. |

This division is deliberate. The current research question is rate-driven
package heating and thermal throttling; it does not require rewriting MQSim or
inventing relay/DASH fluid arbitration.

## Scientific limits that remain after completion

- Model-size scans are synthetic full stored-weight scans, not token/s or an
  inference execution trace. The 235B all-expert scan is not the 22B active MoE
  footprint per token.
- Served rate is produced by the fluid capacity model. It is not measured
  MQSim, NAND, link or fabric throughput, and 20 ms bins cannot resolve
  command-level or sub-bin peaks.
- The 40/10 pJ/B envelope is a user-confirmed conditional assumption rather
  than measured HBF energy. Unknown idle, GPU background, external GDDR and
  host power prevent an absolute product-temperature or safety claim.
- Maintenance, ECC, retry, 24-hour retention and native backend latency are not
  generated by this path.
- The retained P2 adjacent-grid evidence fails the original 0.25 K spatial
  criterion, including a reported 2-to-1 mm maximum difference of 2.069 K.
  The historical 400.911 K development-domain failure remains failed. Blind
  data remains unopened and `MODEL_FREEZE=false`.

## Execution identity, resources and derived artifacts

The matrix ran for 2,305.48 s and retained 10,565,045,495 B. The largest point
used 350,312,325 B of output and 75.71 s wall time, within the evidence-adjusted
413,458,291 B point and 5,400 s stage limits. The maximum recorded peak was
365.563817 K at the all-HBF 235B/16-scan burst guard-only point. All points
remained inside the declared 300–400 K domain.

Read-only analysis and documentation commits continued during the serial
matrix. Consequently, point manifests contain five Git HEAD values. The
stage-level `RUNTIME_IDENTITY_REVIEW.json` finds exactly one executed consumer
source-hash set, one thermal-binary hash and one environment ID across all 41
points. Git HEAD differences therefore record analysis/document history and do
not indicate a runtime-algorithm change; `RUN_INDEX.json::source_locks` is the
execution identity authority.

Small, reviewable commits relevant to this stage include:

- `277e9d9`: isolated fluid service, model-sized workload and controlled runner.
- `cf22dad`: bounded input preparation and serial campaign launcher.
- `9705c65`, `adbf400`, `6a24d08`, `af53461`: derived analyzer, stability
  reporting, strict RUN_INDEX whitelist and offered/temperature figures.
- `c6cecc9`: rate-thermal control assumptions and capability boundary.

The final derived bundle is
`rate-thermal-control-v1/MAIN-ANALYSIS01/`. Its receipt reports 13 grouped
trajectory figures, 13 three-policy per-stack figures and one campaign summary
figure. `CONTROLLED_CAMPAIGN_ANALYSIS.json` contains all 39 point rows, policy
costs and cross-topology comparisons; `KEY_INTERPRETATION.md` is the concise
review. Analysis started zero thermal solves, so every figure is reproducible
from retained raw JSONL and the whitelisted analysis source.
