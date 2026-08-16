# Thermal Throttling, Retention, Refresh, and MTBF Design

Date: 2026-08-16

Base: `feature/sm120-exact-stage1` at `f4dc28b`

Target branch: `feature/thermal-reliability`

Implementation boundary: HBFSim profile, CUDA runtime, shared control ABI,
device timing helper, host service, MQSim adapter, reporting, and paper design
section

## 1. Purpose

HBFSim already measures a live GPU and Dell CD8P thermal response and fits a
first-order model, but no temperature state currently changes a modeled HBF
access. It also has no retention budget, refresh traffic, program/erase-cycle
accounting, or temperature-dependent reliability result. The paper therefore
cannot yet treat thermal throttling and retention as implemented contributions.

This design closes that loop. A host-side controller advances a modeled HBF
junction temperature while the real GPU workload executes. The temperature
selects the HBF thermal state and changes the service available to each access.
It also consumes a per-zone retention budget. A zone whose budget reaches its
configured refresh threshold generates read and sequential block-rewrite work
in the same media scheduler as application requests. That refresh work consumes
bandwidth, increments wear, produces heat, and therefore affects later thermal
and retention state.

The design supports both HBFSim timing paths:

- the reference path submits concrete refresh work to the online MQSim media
  model; and
- the fast path consumes an equivalent, deterministically generated refresh
  service debt without crossing host and GPU clock domains.

The resulting output includes temperature, thermal-state residency, refresh,
wear, retention damage, and parameterized MTBF estimates. It must not present
an HBF lifetime as a hardware measurement.

## 2. Source Authority and Claim Boundary

The normative source is OCP *High Bandwidth Flash High-Level Base Die
Specification*, version 0.7.0, dated 2026-08-03. The implementation follows
these requirements from that document:

- operating junction temperature is 0--105 degrees C;
- the device has Normal, Light Throttling, Severe Throttling, and Shutdown
  states;
- RTT, LTT, and STT provide hysteresis and are encoded by MMIO register 0x150;
- Light Throttling reduces performance but continues normal operation;
- Severe Throttling deasserts AXI Ready and withholds credits, so new commands
  are back-pressured;
- commands already in flight at entry to Severe Throttling may complete with a
  regular response or status 0x9;
- Shutdown is observed as a link error;
- powered retention is guaranteed for 24 hours at 85 degrees C;
- periodic refresh is required, but its exact interval is product-specific;
- MAXPEC and AVGPEC are product-specific endurance registers; and
- interleaved refresh and application reads must not target the same die at the
  same time.

The following quantities are model inputs rather than architectural constants:

- light-throttling service multiplier;
- thermal resistance, capacitance/time constant, package coupling, and HBF
  operation energy;
- zone size, block size where the product profile does not supply it, read
  disturb threshold, refresh lead threshold, and MAXPEC;
- retention activation energy; and
- whole-device reliability activation energy and the reference condition for
  the specification's 20-million-hour MTBF value.

The retention activation energy and whole-device failure activation energy are
separate parameters. HBFSim must never reuse one silently as the other. A paper
result may report a sensitivity interval across declared values, not an
unqualified absolute lifetime.

## 3. Success Criteria

The feature is complete only when all of the following hold:

1. A deterministic CPU reference test validates every equation, transition,
   and accounting invariant.
2. The fast and reference paths consume the same refresh plan and agree on
   refresh bytes, block order, PEC increments, and thermal-state transitions.
3. Light Throttling changes modeled latency in both paths.
4. Severe Throttling back-pressures new work rather than rejecting it, while
   already admitted work reaches exactly one terminal completion.
5. Shutdown produces a distinct terminal link-shutdown result and does not
   masquerade as daemon death.
6. Retention damage integrates correctly across a changing temperature trace;
   refresh resets only the refreshed block or zone state.
7. Refresh work competes with application work and changes observed latency in
   both timing paths.
8. Application writes and refresh rewrites update PEC accounting without
   changing application data.
9. A capacity-mode refresh leaves the backing bytes and workload checksum
   unchanged.
10. Every output records model inputs, time scaling, source hashes, and whether
    temperature came from live telemetry or a declared trace.
11. Existing asynchronous load/store, TMA, TensorMap, channel, capacity, and
    fail-closed tests remain passing.
12. The paper compiles and describes only gates demonstrated by artifacts.

## 4. Selected Architecture

The selected approach is a host-side thermal and reliability controller with a
device-visible state snapshot.

```text
real GPU workload
   |
   +-- runtime telemetry publisher ------------------------------+
   |      GPU temperature and power                              |
   |                                                             v
rewritten PTX/TMA --> HBF byte/operation counters --> thermal controller
   |                                                    |
   |                                                    +--> RC temperature
   |                                                    +--> thermal state
   |                                                    +--> retention damage
   |                                                    +--> refresh plan
   |                                                             |
   +------------------ shared control ABI <-----------------------+
   |                                |
   v                                v
device fast scheduler         host reference scheduler
   | refresh service debt          | refresh read/rewrite requests
   +-------------------------------+--> media queue / MQSim
```

Two alternatives are rejected:

1. A script-generated temperature trace alone cannot close the loop because
   refresh traffic cannot change later heat or contention.
2. A fully device-side reliability simulator would duplicate the host media
   state, complicate deterministic refresh ordering, and couple the design to
   unavailable host/GPU clock synchronization.

## 5. Profile and Configuration

The ordinary HBF profile gains an optional `thermal_reliability` object. When
the object is absent, behavior and ABI-visible results remain the current
non-thermal mode. When it is present, every required field is strict and a
missing, non-finite, inconsistent, or out-of-range value rejects context
creation.

The profile contains these groups:

### 5.1 Thermal response

- `ambient_c`
- `initial_hbf_junction_c`
- `tau_seconds`
- `gpu_coupling_ratio`
- `thermal_resistance_c_per_w`
- `idle_power_w`
- `read_energy_j_per_byte`
- `write_energy_j_per_byte`
- `telemetry_period_ms`
- `controller_period_ms`
- `temperature_source`: `live_gpu`, `trace`, or `constant`
- source identity and SHA-256 for trace or measured calibration data

### 5.2 Thermal states

- `rtt_c`, `ltt_c`, `stt_c`, and `shutdown_c`
- enable bits for Light and Severe modes
- `light_service_ppm`
- `normal_service_ppm`, fixed at 1,000,000
- policy for commands already in flight when Severe begins; the initial
  implementation selects regular completion, which the specification permits

Validation requires:

```text
0 <= ambient <= initial <= 105
rtt < ltt < stt < shutdown <= 105
0 < light_service_ppm < 1,000,000
controller_period <= telemetry_period
```

The initial-temperature ordering may be relaxed for an explicit cooling trace,
but the temperature and threshold bounds remain mandatory.

### 5.3 Retention, zones, and refresh

- `zone_bytes`, a multiple of the NAND block size
- `reference_retention_hours`, normally 24
- `reference_retention_temperature_c`, normally 85
- `retention_activation_energy_ev`
- `refresh_damage_threshold`, in `(0, 1]`
- optional product-specific `read_disturb_limit`
- `refresh_quantum_bytes`, a page-aligned divisor of the block size
- `refresh_policy`: deterministic round-robin within channel and die
- `registered_ranges_contain_valid_data`
- `reliability_time_acceleration`, default 1.0

Time acceleration is a disclosed experiment control. It accelerates retention
age only; it does not accelerate wall-clock thermal response or hide its value
in a general `time_scale`. Every report and paper result using a value other
than 1.0 must label the run as accelerated reliability time.

### 5.4 Endurance and MTBF sensitivity

- `max_pec`
- `reference_mtbf_hours`, normally 20,000,000
- `mtbf_reference_temperature_c`
- `mtbf_activation_energy_ev_min`
- `mtbf_activation_energy_ev_max`
- `mtbf_activation_energy_ev_step`

No default activation energy is silently supplied. A profile that requests
MTBF output without a non-empty sensitivity range is invalid.

## 6. Runtime Telemetry and Time Domains

The CUDA runtime process owns the real CUDA context and can identify the active
GPU. It starts a telemetry publisher with the HBFSim context and stops it before
the shared control mapping is retired. The publisher reuses the existing
dynamic NVML loading boundary and adds instantaneous power draw. It publishes:

- GPU identity generation;
- sample sequence and host monotonic timestamp;
- GPU junction temperature in milli-degrees C;
- instantaneous power in mW; and
- a terminal telemetry status.

The host daemon never subtracts a GPU `%globaltimer` value from a host clock.
The thermal controller advances on `std::chrono::steady_clock`; tests inject a
fake monotonic clock. Device code reads only a generation-stamped thermal state
and relative service/debt values. This removes any need for common host/GPU
clock origins.

Live telemetry is fail-closed when requested. A stale, identity-mismatched, or
failed live source ends the thermal run rather than freezing the last value. A
trace or constant source is allowed only when selected explicitly and is
recorded as modeled input.

## 7. Thermal Model and State Machine

For controller interval `dt`, application and refresh counters produce HBF
power:

```text
P_hbf = P_idle
      + E_read  * delta_read_bytes  / dt
      + E_write * delta_write_bytes / dt
```

The target HBF junction temperature is:

```text
T_target = T_ambient
         + coupling * (T_gpu - T_ambient)
         + R_theta * P_hbf
```

The controller advances the first-order response with the exact interval
solution:

```text
T_next = T_target + (T_current - T_target) * exp(-dt / tau)
```

All intermediate calculations use finite checked `long double`; the shared ABI
publishes bounded fixed-point values. Non-finite or out-of-range state is a
terminal model error.

The state machine follows both arrows in Figure 36:

```text
heat: Normal --T >= LTT--> Light --T >= STT--> Severe
cool: Normal <--T <= RTT-- Light <--T <= LTT-- Severe

Severe --T >= shutdown--> Shutdown
```

Thus the LTT--STT interval is the Severe-to-Light hysteresis window and the
RTT--LTT interval is the Light-to-Normal hysteresis window. A large cooling
step that crosses both release thresholds performs both transitions in order
within one controller tick. State transitions are generation stamped.

Normal uses full service. Light scales service capacity by
`light_service_ppm`. A request snapshots its admitted state so a later Severe
transition does not revoke an already admitted command. Severe closes new
thermal admission and waits for recovery; it does not consume a request ring
slot while blocked. Shutdown publishes a distinct terminal link-shutdown
condition. The initial implementation permits in-flight requests to complete
normally and counts them; it does not invent status 0x9 for new requests.

## 8. Zone State and Retention Damage

Global HBF addresses map deterministically to channel, die, block, and zone
using the validated profile geometry. Each zone owns:

- valid-data state;
- accumulated retention damage;
- last damage-update and refresh epochs;
- application and refresh read/write bytes;
- read-disturb count;
- current and maximum block PEC; and
- queued/in-flight refresh state.

At temperature `T` in Kelvin, retention lifetime is:

```text
L(T) = L_ref * exp[(Ea_retention / k_B) * (1/T - 1/T_ref)]
```

where `k_B = 8.617333262145e-5 eV/K`. This makes lifetime longer below the
reference temperature and shorter above it.

For each interval:

```text
damage_next = damage_current
            + reliability_time_acceleration * dt / L(T)
```

The integral, rather than a deadline computed from the latest temperature,
preserves the contribution of the entire temperature history. A zone becomes
refresh-eligible when damage reaches `refresh_damage_threshold` or when its
product-specific read-disturb limit is reached. A successful complete refresh
resets retention damage and read-disturb count for the refreshed block. A
queued, partial, or failed refresh resets nothing.

All zones covered by a registered range are valid at registration when
`registered_ranges_contain_valid_data` is true. Otherwise a zone becomes valid
on its first complete application program. Unregistered capacity never creates
refresh work.

## 9. Refresh Scheduling and Media Contention

Refresh order is deterministic:

1. lowest eligibility epoch;
2. channel round-robin;
3. die round-robin;
4. ascending zone, block, and page; and
5. read quantum before matching rewrite quantum.

The scheduler never issues refresh and application reads to the same die at
the same modeled instant. It interleaves refresh quanta across other dies when
possible and otherwise delays one side. This rule is enforced by a shared
media scheduler above both timing engines.

### 9.1 Reference path

The reference wrapper reserves a disjoint high-bit engine-ID namespace for
background work. Each refresh quantum becomes a media read followed by its
dependent sequential rewrite and is submitted to the same online MQSim engine
as application requests. Background completions are consumed by the wrapper;
application completions remain paired with their original request tickets.
Completion of all quanta in a block commits its refresh and PEC increment.

MQSim remains configured at its base media parameters. The thermal layer scales
the service capacity at admission/completion and contributes refresh queueing;
it does not reconstruct MQSim on state changes or claim that MQSim internally
models temperature.

### 9.2 Fast path

The controller publishes refresh debt in bytes plus a deterministic quantum
and generation. Between device requests the controller reduces debt by service
that would have completed during elapsed host time. At device admission, a
bounded CAS loop claims at most one refresh quantum, converts it to service
using the current thermal capacity, and places it ahead of the application
request in the modeled queue. The debt is relative work, never an absolute host
timestamp.

The fast path reports claimed and background-drained debt separately. Debt
underflow, generation regression, or a counter overflow is terminal.

### 9.3 Consistency gate

For the same request trace, profile, injected telemetry trace, and fake clock,
the two paths must agree exactly on:

- thermal transitions and transition epochs;
- eligible zones and refresh ordering;
- refresh read and rewrite bytes;
- completed block refreshes;
- PEC increments; and
- final retention damage within fixed-point rounding tolerance.

Timing itself is compared against declared tolerance because the reference
path includes MQSim media state that the fast path approximates.

## 10. Endurance, Failure Hazard, and MTBF

A completed block program cycle increments that block's PEC. Application
traffic increments PEC only when the modeled HBF block program/erase lifecycle
completes; individual sub-page PTX stores do not each count as one cycle.
Refresh of a full block increments PEC once. The report exposes maximum,
average, and distribution summaries and flags values that reach `max_pec`.

Whole-device MTBF is a separate sensitivity calculation. For each configured
activation energy `Ea_mtbf`, the failure acceleration relative to reference
temperature is:

```text
AF(T) = exp[(Ea_mtbf / k_B) * (1/T_ref - 1/T)]
lambda(T) = AF(T) / reference_mtbf_hours
```

The run integrates hazard:

```text
H = integral(lambda(T(t)) dt)
P_failure = 1 - exp(-H)
equivalent_MTBF = elapsed_hours / H
```

The report contains the full activation-energy sweep and its minimum/maximum
equivalent MTBF. If elapsed time or hazard is zero, the result is marked
undefined rather than clamped to a headline number. Endurance exhaustion and
temperature-accelerated failure hazard are reported separately.

## 11. Shared ABI, Public API, and Reports

The shared control ABI increments from version 9. New generation-stamped fields
cover:

- telemetry sample and status;
- thermal model state, junction temperature, and service multiplier;
- thermal admission and shutdown;
- HBF application and refresh byte counters;
- refresh debt and scheduler counters;
- state-transition and in-flight counters; and
- reliability-summary publication.

Large per-zone state remains host-owned. The device sees only the counters and
snapshot needed for admission and fast scheduling.

The public C ABI gains a versioned `hbfsim_thermal_stats` structure and
`hbfsim_get_thermal_stats`. Existing structures are not enlarged in place.
Reports include a durable `thermal-reliability-summary.json` with:

- complete profile and calibration identities;
- temperature source and every sample/transition;
- time acceleration;
- state residency and service multipliers;
- application and refresh bytes;
- refresh debt and completed blocks;
- PEC summaries;
- zone damage summaries;
- MTBF sensitivity sweep and cumulative hazard;
- failure, stale telemetry, overflow, and shutdown status; and
- reference/fast consistency evidence.

## 12. Error and Lifecycle Semantics

Context creation rejects an invalid thermal profile before the daemon becomes
ready. The daemon publishes thermal readiness only after the profile,
controller, reliability state, scheduler, and report writer are constructed.

Failures are fail-closed. They include telemetry loss in live mode, invalid
temperature, counter regression/overflow, invalid geometry, impossible state
transition, refresh scheduling failure, media failure, report failure, and
thermal shutdown. The daemon first publishes terminal completions for admitted
application requests, then publishes the global failure state. Background work
cannot leave an application waiter orphaned.

Severe Throttling is not a failure. Waiters remain live, heartbeat continues,
and thermal admission reopens on recovery. A configurable request timeout still
provides the existing liveness bound; a timeout is reported as timeout, not as
status 0x9.

On clean destruction, the runtime stops telemetry publication, drains
application work, either completes or records outstanding refresh work, writes
the durable summary, and then retires the mapping. No report calls a partial
refresh complete.

## 13. Verification Strategy

### 13.1 Unit and property tests

Tests cover:

- profile parsing and fail-closed validation;
- exact first-order RC solutions across variable intervals;
- threshold equality and hysteresis transitions;
- finite fixed-point serialization;
- Arrhenius direction and reference-point identity;
- piecewise temperature-history damage integration;
- zone mapping and valid-data policy;
- refresh eligibility, deterministic ordering, and partial-failure rollback;
- no same-die refresh/read overlap;
- PEC accounting at block lifecycle granularity;
- hazard integration and activation-energy sweep; and
- all arithmetic overflow boundaries.

### 13.2 Host integration tests

A fake clock and telemetry source drive Normal, Light, Severe, recovery, and
Shutdown without heating hardware. Tests demonstrate that Severe consumes no
new ring slot, heartbeat remains live, in-flight requests finish, and recovery
reopens admission.

A scaled test profile makes a zone reach its refresh threshold in seconds.
Reference tests prove refresh requests enter MQSim, compete with application
requests, and update PEC only after full completion.

### 13.3 Device and capacity tests

Device helper reference tests validate state snapshots, service scaling,
severe admission, refresh debt claiming, and shutdown status. When the GPU is
available, a live timing test checks Light versus Normal latency and verifies
bit-exact output. A capacity test refreshes a dirty and a clean block and
compares backing bytes and checksum before and after.

### 13.4 Regression and paper gates

The final gate includes:

- full build with CUDA 13.0 and GCC 13;
- the complete offline CTest suite;
- available live SM120 tests, with external GPU occupancy reported rather than
  hidden;
- llama.cpp and vLLM adapter tests;
- fast/reference thermal consistency fixtures;
- JSON schema validation and durable-report parsing;
- LaTeX compilation of the paper; and
- an independent code review before commit and push.

## 14. Paper Design Section

The paper's Design section remains organized by D1--D3:

- D1 describes explicit ranges, PTX/TMA rewriting, coverage admission, and
  capacity mode.
- D2 describes the empirical curve, online MQSim reference path,
  deterministic sampling, and separate time accounting.
- D3 describes the implemented thermal/reliability loop in this design.

D3 states the chain in causal order: real GPU telemetry and HBF traffic drive
junction temperature; temperature selects the four-state service regime and
integrates retention damage; eligible zones generate refresh read/rewrite
traffic; refresh consumes bandwidth and PEC and contributes heat to the next
interval. MTBF is presented as a parameter sweep, not a measured property of
unavailable HBF silicon.

The section explicitly states:

- the Dell CD8P and GPU measurements calibrate boundary behavior, not HBF
  silicon;
- product-specific inputs remain assumptions;
- Severe back-pressures new commands and status 0x9 is not assigned to them;
- refresh period and activation energy are not fixed by the architecture
  specification; and
- an accelerated retention-time experiment is labeled wherever used.

A double-column architecture figure shows device side, shared ABI, and host
side, with refresh traffic returning to both the media queue and thermal
controller.

## 15. Delivery Decomposition

Implementation proceeds in six test-driven stages:

1. strict profile schema, equations, state machine, zone damage, hazard, and
   unit tests;
2. ABI v10, telemetry publisher, host controller, lifecycle, and reporting;
3. thermal admission and service scaling in synchronous, future, TMA, fast,
   and reference device paths;
4. deterministic refresh planner, MQSim background scheduling, fast refresh
   debt, PEC, and consistency tests;
5. capacity/data-integrity tests, live tests where available, workload
   regressions, and proof artifacts; and
6. the 3.5-page paper Design section, architecture figure, LaTeX build, review,
   commit, and pushes to the HBFSim and Overleaf remotes.

No stage is complete because it compiles alone. A live-only gate blocked by an
external GPU process is recorded as blocked with the exact PID and memory use;
it is not converted into offline proof.

## 16. Excluded Claims

This work does not claim:

- measured HBF junction temperature, service multiplier, activation energy,
  refresh interval, or lifetime;
- that the Dell CD8P is an HBF device;
- that MQSim internally models HBF thermal behavior;
- an absolute device MTBF derived from retention activation energy;
- physical NVIDIA GNIC2TEX/GPCARB/SMSP identities; or
- complete coverage of cubin-only operations that the PTX pass cannot inspect.
