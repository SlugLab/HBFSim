# Rate-driven thermal-control assumptions

Status: engineering interpretation contract for the `eq3_rate_thermal`
fluid-feedback path. This document describes the implementation at the current
source revision; it is not a product thermal limit specification or a model
freeze.

## What the loop represents

The new loop accepts synthetic model-weight read demand as integer bytes in
20 ms windows. A persistent per-channel FIFO serves those bytes under a
per-stack budget and the declared 96 GB/s/channel service ceiling. Only served
bytes produce incremental heat: 40 pJ/B is assigned to the mapped HBF array die
and 10 pJ/B to that stack's base. The unchanged full coupled RC network then
advances one window. Temperatures and completed-window fluid facts determine a
budget for the following window. No decision changes service or energy already
recorded in the current window.

The service and delay observations are `MODELLED_FLUID`. They are not MQSim
commands, NAND completions, fabric completions, or native backend latency. The
capacity-utilization fact passed to the existing feedback policy is
`served_bytes / physical_window_capacity`; its saturation flag means the fluid
channel envelope was filled. The byte-weighted P95 is quantized to window ends.
The policy receives that P95, but the present rate-only profile has no latency
target, so it does not use the P95 as a pass/fail target.

The workload intentionally permits offered demand above the 1.536 TB/s per
stack service ceiling. This encodes the user-selected sustained-pressure
question. Excess bytes remain in FIFO order instead of being discarded or
reported as achieved bandwidth. The frozen per-stack control target is the
integer value

```
min(mean active offered B/s per stack, 0.8 * 1.536e12 B/s).
```

The initial and maximum budget is one physical-capacity window
(30,720,000,000 B per 20 ms); the minimum is 0.10 of that value and the control
step is 0.05. A workload's final 10 s has zero new arrivals, but existing
backlog continues to receive service and therefore continues to generate read
energy. “Recovery” means the offered-load source has stopped. It does not
guarantee a pure passive-cooling interval or complete queue drainage.

## Research thermal guard

`ThermalService` derives a stack temperature from the hottest thermal entity
owned by that stack. The configured research thresholds are:

| Entity | Light | Severe | Shutdown |
|---|---:|---:|---:|
| HBF/HBM | 353.15 K (80 °C) | 363.15 K (90 °C) | 378.15 K (105 °C) |
| GPU | 363.15 K (90 °C) | 373.15 K (100 °C) | 383.15 K (110 °C) |

These thresholds are research guardrails. They do not certify safe operation,
throttling behavior, reliability, or shutdown behavior of a product. A GPU
guard state is propagated to package memory stacks when it is more restrictive
than their own state. State changes take effect after 20 ms. Recovery uses a
100 ms dwell and 2 K hysteresis; escalation is not blocked by recovery dwell.
The thermal domain still fails above 400 K rather than clamping the result.

The three existing policies retain their original behavior:

- `guard_only` applies Severe and Shutdown protection but has no ordinary or
  Light rate limiter. It returns to the maximum budget after protection clears.
- `thermal_hysteresis_guard` applies the existing Light budget of 0.5 of the
  baseline, and applies a zero budget for Severe or Shutdown.
- `read_rate_feedback_thermal_guard_v1` applies the same Light, Severe and
  Shutdown protection, then evaluates completed-window rate facts. It holds a
  target that is met within the configured 5% tolerance; delivery above the
  target plus the 10% smoothing margin can reduce the budget by one step. When
  demand and backlog remain, the gate limited service, and the modelled fluid
  capacity is not saturated, it can increase by one step. It holds when the
  demand is insufficient or the evidence does not support an increase. Its
  existing rollback rule reduces a prior increase when delivery fails to
  improve while backlog or quantized delay worsens. After an emergency zero
  budget, Normal state resumes from the minimum and can step upward using the
  explicit modelled fluid spare-capacity fact.

The words “capacity utilization,” “saturation,” and “delay” in this policy path
refer to the fluid experiment. They must not be restated as native NAND or
fabric measurements.

## Power and temperature interpretation

The 50 pJ/B coefficient is a user-confirmed scenario assumption anchored as
40 pJ/B array plus 10 pJ/B base. It gives 76.8 W at the OCP Grade 2 effective
rate of 1.536 TB/s; the coefficient is not renormalized to force 80 W at that
rate. It is applied to served bytes once, with no energy assigned to queued
bytes.

Idle HBF power, channel activation power, GPU self power, host ingress power,
and external-GDDR thermal input are unknown or omitted in this path. GPU power
is zero *incremental* input, not a claim that a physical GPU consumes zero
power. The initial 300 K is a common simulation reference. Reported values are
conditional incremental-heating results, not predicted absolute product
operating temperatures. Retry, ECC, retention age, maintenance and native
backend latency are also unavailable in this fluid path. The retained P2
spatial error prevents treating these runs as a thermal model freeze.

## Four-topology, three-axis coverage

The axes are configuration identity, thermal behavior, and system behavior.
Evidence from the native CPU campaign and the new fluid campaign remains
separate.

| Topology | Configuration axis | Thermal axis | System-behavior axis |
|---|---|---|---|
| Q1 mixed-direct | Existing mixed HBM/HBF configuration and channel-to-die scenario map | New fluid path supports the full mixed 2 mm coupled network and per-die/base served-byte sources | New rate/control study supports direct per-stack fluid FIFO service. This is modelled service, not native MQSim. Older native CPU receipts remain separate evidence. |
| Q2 4+4 relay | Existing relay configuration is retained | The mixed package network exists, but the new fluid path has no partner-endpoint energy or shared relay arbitration | Only the older native CPU path provides relay routing/admission evidence. New fluid results must not be reported as relay-system results. |
| Q3 four-pair DASH | Existing DASH configuration is retained | The mixed package network exists, but the new fluid path has no DASH shared-endpoint arbitration or partner-energy accounting | Only the older native CPU path provides DASH routing/admission evidence. New fluid results must not be reported as DASH-system results. |
| Q4 8-HBF direct plus external physical GDDR | Existing all-HBF package configuration is supported; GDDR remains physically external | New fluid path supports the full generated 8-HBF 2 mm coupled package network. External GDDR temperature is unavailable | New rate/control study supports direct per-stack fluid FIFO service. External-GDDR service and temperature are outside this loop; older native CPU evidence remains separate. |

The older OCP4K CPU campaign snapshot records completed Q1–Q4 native points,
including relay and DASH modes, but is explicitly a partial campaign snapshot
and is not interchangeable with the new model-size fluid matrix. The new study
therefore closes the requested rate-to-temperature loop for Q1 and Q4 while
preserving the Q2/Q3 system-capability gap. The user's current question concerns
read-rate pressure and thermal throttling, so closing it does not require
rewriting MQSim arbitration or inventing relay/DASH fluid behavior.

## Source anchors

- `experiments/eq3_maintenance/thermal_client.py`: hotspot guard, thresholds,
  action delay, dwell, hysteresis and GPU guard propagation.
- `experiments/eq3_maintenance/read_rate_policy.py`: the three budget policies,
  target/tolerance logic, recovery and rollback behavior.
- `experiments/eq3_rate_thermal/fluid_service.py`: persistent FIFO cohorts,
  integer capacity, max-min service, delay histograms and conservation.
- `experiments/eq3_rate_thermal/run_controlled.py`: completed-window causality,
  modelled-fluid fact adapter, served-byte energy mapping and output labels.
- `experiments/eq3_rate_thermal/model_workloads.py`: official model-size metadata,
  continuous/equal-mean burst offered demand and exact integer-byte generation.
- `eq3_thermal/plans/isolated-maintenance-campaign-v1/campaign-ocp4k-v3/stage/COMPLETED48_SNAPSHOT.json`:
  retained partial native CPU campaign snapshot across Q1–Q4.
