# EQ3 parameter-consumer audit

Status: static audit at local `f1eb88d18627665abf3359d605205d52c1931c73`.
Evidence class: **DOC_DERIVED** unless explicitly marked otherwise. This report
does not authorize or report a solver, GPU, network, or experiment run.

The machine-readable companion is
`configs/eq3_thermal/research/consumer_inventory.json`. It enumerates every
scalar or null leaf in the 37 JSON files within the exact scope below: 1,129
leaves, each with `file`, `source_line`, `path`, `default`, `value`, `status`,
`consumer`, and `effect`. Arrays are enumerated by index, including every region
coordinate and every trace power value. The inventory also lists 34 code, CLI,
environment, and algorithm parameters that are not JSON leaves.

## Scope and interpretation

Included JSON:

- all `configs/eq3_thermal/*.json`, `configs/eq3_thermal/reference/*.json`, and
  `configs/eq3_thermal/topologies/*.json`;
- all 19 `configs/profiles/**/*.json` runtime-service profiles;
- both historical `configs/thermal/*.json` files.

Included consumers are the standalone EQ3 thermal core/CLI and generators, the
P2 reference tools, the historical thermal extrapolation script, the profile
reader, existing MQSim adapter/observer, host service, and the profile handoff
in the CUDA runtime. The audit is exhaustive for leaves in those JSON files and
targeted for code/default/CLI/environment consumers in those components. It is
**not** an all-repository configuration audit: unrelated CUDA/PTX/cache settings,
paper-only values, third-party MQSim internals not assigned by the adapter, and
arbitrary files outside the listed globs are outside scope.

“Consumed” means a value reaches an executable check, generated input, numerical
calculation, or existing service path. It does not mean physically calibrated,
accepted, or integrated end to end.

## Coverage summary

| Leaf status | Count | Meaning |
| --- | ---: | --- |
| Existing runtime-service input | 356 | Loaded and used by the existing HBF/MQSim runtime, independently of EQ3 thermal |
| Abstract numerical-reference input | 252 | Changes the current 40 mm NEW_REFERENCE generation/result |
| P1 fixture input | 75 | Changes the standalone uncalibrated RC fixture/model/event generation |
| Historical extrapolation input | 16 | Changes only `scripts/thermal/simulate_overheat.py` output |
| RC-candidate-only input | 1 | Contact resistance changes the failed candidate RC, not stock 3D-ICE |
| Validation-only | 37 | Checked for consistency/schema but not used as a coefficient |
| Declared but not integrated | 21 | Control/reliability declarations with no executable reader |
| Provenance metadata not executed | 148 | Source/environment/family ledger material only |
| Other metadata/output identity | 133 | Labels, notes, descriptions, or output identity only |
| Schema only, not executed | 39 | `topology.schema.json`; the generator does not load it |
| Parsed runtime metadata/unused field | 51 | Parsed profile identity/tolerance or empirical data without an effect in the traced numerical handoff |

The exact status counts and all individual leaves are in the JSON companion;
the grouped table above combines closely related machine statuses for reading.

## Actual thermal and power consumers

### Standalone P1 fixture

`tools/eq3_thermal_config.py` is the only reader of the P1 EQ3 device,
topology, thermal-fixture, and power-fixture JSONs. It validates device fields at
lines 27–49, selects topology/profile identity at lines 52–77, and translates
only the following numerical inputs into model/events:

- `thermal_fixture.json`: `ambient_k`, `initial_k`, every
  `capacity_j_k.*`, and every `conductance_w_k.*` (generator lines 78–109,
  117–125, and 145–157);
- `power_fixture.json`: `operation`, `origin`, `bytes`, `start_s`, `end_s`, and
  `energy_j_per_source` (lines 85–93 and 129–157);
- topology counts/layout/profile selections and optional relay/GDDR selectors
  (lines 57–77 and 112–149);
- selected profile `physical_kind` and `die_count`, which label stacks and set
  thermal-node count (lines 69–77 and 120–128).

The generated text reaches `src/eq3_thermal/text_format.cpp:95-215` and the RC
calculation at `src/eq3_thermal/thermal.cpp:244-295`. Device bandwidth, UCIe,
capacity, and interface operating-point values do **not** reach service timing
or thermal coefficients here. Some are consistency checks only
(`tools/eq3_thermal_config.py:30-49`); source IDs, verification strings, and
notes are metadata.

The fixture is therefore executable but not a research profile. Its arbitrary
capacities, conductances, initial/boundary temperature, and per-source energy
can change output, but remain `UNCALIBRATED_TEST_FIXTURE` by repository evidence.

### P2 abstract numerical reference

`tools/eq3_reference.py` consumes:

- package dimensions, source/spreader thickness, top heat-transfer coefficient,
  ambient, mesh choices, and all region coordinates/sizes/types
  (`tools/eq3_reference.py:59-78`, 81–140, 157–192);
- silicon density, specific heat, and conductivity (lines 43–45, 69–72,
  99–108, and 157–192);
- `slot_s`, indexed `region_order`, and every indexed power vector value
  (lines 47–56, 73–78, 81–93, and 196–214).

The source-to-spreader contact resistance is used only while generating the
lumped candidate RC (`tools/eq3_reference.py:179-183`). Stock 3D-ICE input does
not receive it. The candidate RC acceptance thresholds are hard-coded at
`tools/eq3_reference.py:23-24`; the current repository record says acceptance
is **FAILED**. Consumption must not be read as validation.

`sensor_mapping.regions` is a set-equality check against the hard-coded nine
sensor IDs (`tools/eq3_reference.py:22,63-65`). Quantity/hotspot description
strings do not configure the implementation. Region-average outputs and grid
hotspots are separately computed at lines 296–346.

`environment_lock.json` and `families.json` have no executable solver/runtime
reader. They are provenance/hash inputs in the reference records. The pending
follow-up instead hard-codes 6.25 ms, 0.5 mm, four run identities, and resource
limits at `tools/eq3_reference_followup.py:25-35,63-68,95-139`. It remains an
approval-bound pending plan; this audit did not execute it.

### Thermal core and CLI defaults

- CLI mode defaults to `off` (`src/eq3_thermal/cli.cpp:47-65`); off and
  read-only construct no solver (`src/eq3_thermal/thermal.cpp:545-557`).
- `--step-s` and `--end-s` default internally to zero and must be supplied for
  shadow execution (`src/eq3_thermal/cli.cpp:49-79`).
- `active` is unconditionally rejected as not implemented
  (`src/eq3_thermal/cli.cpp:65-71`, `src/eq3_thermal/thermal.cpp:545-557`).
- Direct intercomponent edges default on
  (`include/hbfsim/eq3_thermal/thermal.hpp:41-48`), but switching them off does
  not remove indirect coupling through shared dynamic sink/interposer nodes.
- The smoke tool hard-codes 10 ms steps and a 0.2 s horizon
  (`tools/eq3_thermal_smoke.py:42-46`); neither is a research-wide default.
- No scientific EQ3 environment variable was found. Reference scripts set only
  thread caps (`tools/eq3_reference.py:236-264` and
  `tools/eq3_reference_followup.py:185-205`).

## Existing runtime service: effective but not thermally integrated

All 19 JSONs under `configs/profiles` are parsed by
`src/profile/profile.cpp:229-300`. The selected file is supplied explicitly as
`--profile` to the host service (`src/host_service/main.cpp:31-66,94-102`); no
inheritance from `configs/eq3_thermal/devices.json` exists.

The existing service consumes geometry, latency, queue depth, aggregate
bandwidth, NAND technology/allocation, sampling, scale, cache, and empirical
timing inputs through `src/mqsim_adapter/mqsim_online.cpp:112-157,190-207` and
`src/cuda_runtime/context.cpp:786-881`. Important hidden defaults/constants are:

- absent NAND technology → SLC; absent plane allocation → CWDP
  (`src/profile/profile.cpp:272-280`);
- absent per-level read/program latencies inherit the flat scalar latency
  (`src/profile/profile.cpp:282-299`);
- MQSim seed 123, no preconditioning, page-level ideal mapping, zero
  overprovisioning, one chip/channel, NVDDR2, no command suspension, zero page
  metadata, and erase latency `10 * program_latency_ns`
  (`src/mqsim_adapter/mqsim_online.cpp:112-157`);
- device cache off, urgent priority, zero initial occupancy
  (`src/mqsim_adapter/mqsim_online.cpp:209-214`);
- request alignment sector 512 B (`src/mqsim_adapter/mqsim_online.cpp:27,420-447`);
- aggregate-bandwidth completion lower bound
  (`src/mqsim_adapter/mqsim_online.cpp:309-325`).

These values can affect existing service results, but the path does not consume
EQ3 thermal temperature, energy, reliability, or controller state.

The observer is opt-in and defaults off
(`include/hbfsim/mqsim_online.hpp:50-53`; `src/mqsim_adapter/mqsim_online.cpp:559-572`).
It records request arrival/admission/completion, bytes, and aggregate outstanding
count (`include/hbfsim/mqsim_online.hpp:14-27`;
`src/mqsim_adapter/mqsim_online.cpp:359-375`). The host service constructs the
engine but never enables or drains observations (`src/host_service/main.cpp:131-157`).
Even when tests enable it, the observer is not physical NAND command/die/plane,
resource-occupancy, or operation-energy evidence.

## Declared or historical values that do not drive EQ3

- Every leaf in `control.json` and `reliability.json` is declared but has no
  executable reader. The OCP 85 °C/24 h condition is metadata, not a threshold
  or retention curve.
- Every leaf in `sources.json` is provenance only. This includes the HeatWatch
  activation energy and intervals: they do not drive aging, retry, ECC,
  maintenance, or service.
- `configs/thermal/gpu-cd8p-logp-live.json` and `scenarios.json` feed only the
  standalone virtual extrapolation at `scripts/thermal/simulate_overheat.py:32-54`.
  They do not feed the EQ3 solver, host service, MQSim, reliability, or control.
- `topology.schema.json` is not loaded. Cross-field checks are independently
  coded in `tools/eq3_thermal_config.py`; schema presence is not runtime
  enforcement.

## Research-candidate inputs currently not consumed

The highest-impact gap is not a missing declaration; it is missing wiring:

1. OCP/Sandisk HBF bandwidth, UCIe, capacity, and product organization and the
   Micron HBM4 operating points do not configure the actual MQSim/service
   profile or energy model.
2. No device/profile value supplies localized read/program/erase/refresh,
   PHY/relay, ECC/retry, idle, or leakage energy to the thermal core.
3. No runtime service event is transformed into `PhysicalActivity` with a
   measured/derived node-energy assignment.
4. No temperature reaches service latency/bandwidth, retention aging, ECC,
   refresh scheduling, wear, or an actuator.
5. `control.json` has no sampling periods, thresholds, hysteresis, actions, or
   consumer. `reliability.json` has no executable aging/RBER/ECC/maintenance
   model. P3–P5 therefore remain **NOT_IMPLEMENTED**, consistent with the
   project handoff.
6. The current abstract reference geometry and power trace are active inputs,
   but do not contain the requested per-die/base-die research geometry. The
   failed RC must not be frozen or reused as an accepted fast model.

The missing end-to-end chain is:

`request/service activity → physical operation + localized energy → coupled temperature → reliability/maintenance/controller → later service/completion`

Existing components cover isolated portions of that chain; no traced consumer
connects all arrows.

## Exact uncovered scope

This audit did not claim or attempt to enumerate every constant in the complete
repository. In particular, it does not inventory generic PTX transformation,
CUDA launch/future, capacity address translation, cache replacement, or GPU
kernel parameters unless they participate in the profile handoff named above.
It does not inspect arbitrary manuscript values or all third-party MQSim source
defaults. It also does not establish physical truth for any consumed value,
re-run tests, validate raw historical measurements, or authorize the pending
reference plan. Those are separate evidence and approval tasks.
