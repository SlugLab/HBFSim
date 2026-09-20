# Prescribed read-rate thermal inputs

`rate_inputs.py` is a default-disconnected, pure converter. It turns an
externally prescribed HBF read-rate schedule into the component-energy windows
already accepted by the thermal service. It issues no NAND or HBM requests and
does not claim the requested rate was achieved by a backend.

```python
from rate_inputs import build_windows

inputs = build_windows(profile, schedule, normalized, step_ns=20_000_000)
```

The approved profile is explicit:

```json
{
  "schema_version": 1,
  "reference_read_Bps": 1600000000000.0,
  "array_j_per_byte": 4e-11,
  "base_j_per_byte": 1e-11,
  "provenance": "SCENARIO_ASSUMPTION_USER_CONFIRMED"
}
```

This maps the 1.6 TB/s scenario endpoint to 64 W array plus 16 W base. These are
incremental read-energy assumptions, not measured product power. Idle power is
unknown and omitted. Rates above the envelope are rejected rather than clamped.

The schedule uses integer-nanosecond, half-open, contiguous intervals covering
`[0, end_ns)`:

```json
{
  "schema_version": 1,
  "end_ns": 40000000,
  "segments": [
    {"start_ns": 0, "end_ns": 20000000,
     "read_Bps": {"hbf0": 384000000000.0}},
    {"start_ns": 20000000, "end_ns": 40000000,
     "read_Bps": {"hbf0": 1600000000000.0}}
  ]
}
```

An omitted discovered stack means zero prescribed read rate for that segment;
it does not mean zero physical idle power. Unknown stack IDs, gaps, overlaps,
negative or non-finite values, and rates above 1.6 TB/s fail explicitly.

Powered HBF stacks, their base component, and their array-die components are
discovered from `normalized.components` using `physical_type`, `device_id`, and
`role`. No stack count, die count, or component-name pattern is assumed. Array
energy is uniform over each stack's discovered dies unless `die_weights` gives
an exact component map summing to one for that stack. Base energy is assigned
only to that stack's discovered `base_die`.

For a channel-locality scenario, the profile may additionally declare an
explicit physical mapping and capacity for each named channel:

```json
{
  "channel_map": {"hbf0": {"c0": "hbf0.die0", "c1": "hbf0.die1"}},
  "channel_capacity_Bps": {"hbf0": {"c0": 400000000000.0,
                                      "c1": 400000000000.0}}
}
```

A segment can then include
`"channel_read_Bps":{"hbf0":{"c0":400000000000.0}}`. If the segment also
declares `read_Bps.hbf0`, its value must equal the channel sum. Each channel is
checked against its own capacity; unknown channels, stacks, or die components
fail and no value is clamped. Multiple channels may map to one die, in which
case their rates accumulate there. Array energy follows the mapped die rates,
while base energy uses the stack total once. Thus concentrated and distributed
channel activity can have equal total joules but different spatial sources.
`active_channel_count` counts positive prescribed channel rates only. Without
explicit channel rates, the older uniform/static-weight path remains available
and is labelled `UNIFORM_ASSUMED_CHANNEL_ACTIVITY_UNKNOWN`; it does not infer
which NAND channels were actually active.

The output contains each window's `component_energy_j` plus per-stack requested
and modelled bytes, source energy, and window-average source power. It records
the entity mapping, weights, parameter provenance, prescribed-rate semantics,
and `ZERO_READ_ONLY_NOT_ZERO_IDLE`. A final partial window is supported; all
segment overlaps are integrated before energy is assigned.

Run the small fixed validation without invoking a thermal solve:

```sh
python3 -B experiments/eq3_rate_thermal/test_rate_inputs.py
```

## Current explicit OCP Grade 2 scenario

The active user-selected interface profile has16channels/stack and96GB/s effective capacity/channel, totaling1.536TB/s. Source: OCP v0.7.0 p16 Tables2/4:64-bit×16GT/s×75%. This is the host-interface effective envelope, not a calibrated NAND bus, individual request simulator or guaranteed achieved throughput. A one-channel/one-thermal-die map is a separate explicit scenario assumption.

The energy anchor remains80W at1.6TB/s,40pJ/B array+10pJ/B base. Thus full OCP Grade2 is76.8W, not80W; there is no hidden coefficient renormalization. Input schedules must obey each channel capacity, aggregate capacity and the approved energy envelope; excess demand is rejected as an invalid prescribed-delivery scenario rather than silently clamped. No queue or backpressure is simulated in this open-loop converter.

`run_rate_thermal.py` consumes explicit profile/schedule/model/binary paths and writes energy windows, complete thermal entity frames, stack temperature CSV and DONE/FAILED receipts. It uses existing ThermalService ENERGY/ADVANCE on the unchanged full coupled network. No MQSim process is created. `plot_rate_thermal.py POINT` reproduces the rate/power/incremental-temperature figure. Initial300K is the model reference; without an idle/GPU background the output is incremental heating, not the product's absolute operating temperature. Existing 300..400K domain checks remain; failure evidence is retained.

Input time segments are integrated exactly across20ms windows. Sources are per actual discovered die and base; temperature uses the existing2mm lateral grid and layered geometry. Unaccessed dies have no incremental read power but remain thermally coupled. Per-page/block/plane heat-source geometry, channel activation/static energy, UCIe scheduling and actual device throughput are not represented.

## Fluid feedback runner

`run_controlled.py` is a separate, default-disconnected closed-loop path. Each
20 ms window enqueues integer offered bytes, serves the persistent per-channel
FIFO under the budget chosen from the preceding completed thermal window, maps
only served bytes to 40/10 pJ/B array/base energy, and advances the unchanged
coupled thermal network. The resulting control decision applies to the next
window. Recovery windows admit zero new bytes while retaining and serving old
backlog.

```sh
python3 experiments/eq3_rate_thermal/run_controlled.py \
  --profile PROFILE.json --model-dir MODEL_DIR --workload WORKLOAD.json \
  --scenario SCENARIO.json --thermal-binary THERMAL_SERVICE \
  --artifact-root ARTIFACT_ROOT --output NEW_OUTPUT \
  --strategy read_rate_feedback_thermal_guard_v1 --address-limit-gib 4
```

The scenario schema is `controlled_scenario.schema.json`. Its explicit target
must equal `min(mean active offered B/s per stack, 0.8 * 1.536 TB/s)`. The
initial and maximum budget is the physical 1.536 TB/s window capacity; minimum
and step are 0.10 and 0.05 of that budget. Existing 20 ms action delay, 100 ms
recovery dwell, 2 K hysteresis and thermal thresholds are reused unchanged.

All delivery, queue and delay facts are labelled `MODELLED_FLUID`. The reported
byte-weighted delay is quantized to thermal-window ends; backend latency remains
`UNKNOWN`. This path creates no MQSim request or fabric completion, has no
maintenance or endpoint-group arbitration, and does not infer retry, ECC, age,
retention, idle power, or GPU self power. It therefore supports only the
mixed-direct and all-HBF thermal studies; relay and DASH behavior remains a
separate capability gap.
