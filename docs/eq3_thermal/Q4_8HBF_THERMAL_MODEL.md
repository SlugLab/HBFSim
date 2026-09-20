# Q4 eight-HBF package thermal model

## Scope and evidence

The Q4 geometry is an engineering model for the user-confirmed
`EQ3-ISOLATED-MAINTENANCE-CAMPAIGN-v1`.  It reuses the candidate package,
GPU, materials, boundaries, background fill, and existing HBF layer template.
Those preserved sections are `DOC_DERIVED`.  Replacing the four HBM slots with
translated copies of the corresponding HBF templates is `USER_CONFIRMED`.

The generated network is **not** a P2 model freeze and does not inherit the P2
spatial-reference qualification.  External GDDR retains a physical system
identity but has no package geometry and no package temperature in this model.

## Conversion contract

`tools/eq3_q4_all_hbf_profile.py` takes all paths explicitly and writes new
files only.  It rejects an unexpected source inventory, a footprint/orientation
mismatch, an incomplete HBF template, or an existing output.

| Removed slot | Complete template | New stack | Target XY (um) | Footprint (um) |
|---|---|---|---:|---:|
| `hbm0` | `hbf0` | `hbf4` | 16000,48000 | 12000,16000 |
| `hbm1` | `hbf1` | `hbf5` | 0,36000 | 16000,12000 |
| `hbm2` | `hbf2` | `hbf6` | 0,16000 | 16000,12000 |
| `hbm3` | `hbf3` | `hbf7` | 16000,0 | 12000,16000 |

Each copied stack contains attach, one base, 16 bond layers, 16 array dies, and
TIM.  The component identifiers and power groups change to `hbf4..7`; the
material, size, z coordinate, die index, and sensor contract come from its HBF
template.  The original `hbf0..3` stacks remain unchanged.

## Static validation

The immutable point is
`eq3_thermal/plans/isolated-maintenance-campaign-v1/points/Q4-THERMAL-STATIC01`.
Its explicit input and output paths are in `manifest.json`, while
`ENGINEERING_MODEL_LOCK.json` binds the profile, power fixture, model, events,
grid, and sensor files by SHA-256.

The fixed tests pass 5/5.  The 2 mm export contains 40,960 nodes, 119,296 edges,
287 entities, 137 powered components, and 307 sensors.  The 20 ms mapping fixture
assigns 1 W to every powered component: input energy is 2.74 J and emitted RC
event energy is 2.7400000000002223 J.  All preserved profile sections compare
equal by canonical JSON.  This fixture is a `SCENARIO_ASSUMPTION` for component
coverage and energy conservation; it is not a workload or calibrated power
trace.

The static export starts no solver.  The separate locked-input response point
`Q4-THERMAL-RESPONSE02` compares the persistent service with the existing
sparse campaign runner for one 20 ms, 0.02 J input to `hbf4.base`.  It passes
all 307 sensors with a maximum absolute difference of
2.1032064978498966e-12 K.  Campaign and service energy residuals are
2.0384052222373263e-15 J and 2.042923666554895e-15 J.  The run used one CPU,
211200 KiB maximum RSS, and 11.24 s wall time; sparse factorization took
5.1775 s.

`Q4-THERMAL-RESPONSE01` is retained as a pre-solver failure: its manifest
selected an older runner that rejected `--model-sha256`.  RESPONSE02 changes
only the runner path to the already validated identity-aware build; model,
grid, sensors, step, and injected energy remain identical.

## Consumer paths

- Model: `generated_2mm/model.txt`
- Grid/component mapping: `generated_2mm/rc_grid.json`
- Sensors: `generated_2mm/rc_sensors.json`
- Fixture events: `generated_2mm/events.txt`
- Converted profile: `inputs/candidate_profile_q4_all_hbf.json`
- Fixture power: `inputs/q4_mapping_20ms_power.json`

Q1, Q2, and Q3 continue to share the original mixed package model because those
topologies change the fabric graph rather than package geometry.  Only Q4 uses
this all-HBF model.
