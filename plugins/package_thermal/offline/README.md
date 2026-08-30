# External 3D-ICE and reduced-model workflow

This directory is the license and performance boundary between HBFSim and
[3D-ICE 4.0](https://github.com/esl-epfl/3d-ice). HBFSim neither vendors nor
links that GPL-3.0-or-later project. A user-supplied `3D-ICE-Emulator` runs as
an external offline process; the runtime loads only the resulting reduced ROM
or a stable C-ABI plugin.

The checked-in tests generate synthetic fixtures. They prove data plumbing,
strict validation, fitting, held-out evaluation, and linear superposition only.
They do not establish a calibrated GPU/HBM/HBF package or measured HBF silicon
temperature.

## Inputs and evidence

A real sweep needs:

- a package thermal profile with explicit 8Hi or 16Hi physical mappings;
- a solver-validated 3D-ICE `.stk.in` and floorplan templates;
- a geometry JSON containing version/commit, material, mesh, solver settings,
  and a `parameter_provenance_csv` path;
- a provenance CSV classifying every value as `S`, `L`, `C`, or `M`;
- a solver manifest containing the exact 3D-ICE executable SHA-256.

The classes mean specification, literature, calibration/sensitivity, and
measurement, respectively. An unhashed `C` value is sensitivity-only. `M`
requires a measurement dataset hash. Synthetic and assumed inputs must never be
tagged `M`.

Templates may contain exactly one token per configured power node:

```text
{{POWER_VALUES:gpu}}
{{POWER_VALUES:hbm}}
{{POWER_VALUES:hbf.base}}
{{POWER_VALUES:hbf.s0.l0}}
```

Token coverage must equal the profile node set. `build_3dice_model.py` rejects
missing, duplicate, or unknown bindings and requires each rendered case to have
exactly one stack file. The template is responsible for valid 3D-ICE geometry,
materials, inspection points, and temperature-file output; the generator does
not pretend to validate scientific geometry.

All paths serialized into sweep and case JSON are POSIX relative paths. The
runner also accepts legacy Windows separators, normalizes them, and rejects
absolute paths or traversal before resolving a file below the sweep root.

## Generate and run sweeps

Generate one 1 W unit step per source plus square-wave, burst, mixed,
write-heavy, and read-heavy held-out traces:

```bash
python3 plugins/package_thermal/offline/build_3dice_model.py \
  --package-profile package-profile.json \
  --geometry geometry.json \
  --template-dir vetted-3dice-template \
  --output results/sweep \
  --samples 256 \
  --step-watts 1.0
```

Inspect all case commands without requiring an installed solver:

```bash
python3 plugins/package_thermal/offline/run_3dice_sweeps.py \
  --solver /absolute/path/to/3D-ICE-Emulator \
  --solver-manifest solver-manifest.json \
  --plan results/sweep/sweep-plan.json \
  --dry-run
```

Remove `--dry-run` to execute. The runner checks project `esl-epfl/3d-ice`,
version `4.0`, and the executable hash, then invokes the documented form
`3D-ICE-Emulator case.stk` from each case directory. It captures stdout/stderr
and writes `results/sweep/3dice-run-manifest.json`. Missing or mismatched
solvers fail clearly and never affect a normal HBFSim build.

## Normalize and assemble unit steps

For every case, export a CSV whose header is `time_ns` followed by the profile's
exact output-node order. Normalize it with the case power CSV and successful
run manifest:

```bash
python3 plugins/package_thermal/offline/extract_step_responses.py \
  --case results/sweep/cases/unit-000/case.json \
  --power results/sweep/power_traces/unit-000.csv \
  --temperatures results/sweep/cases/unit-000/temperatures.csv \
  --run-manifest results/sweep/3dice-run-manifest.json \
  --output results/golden/unit-000.json
```

Repeat for every unit source and held-out case. Then assemble the transfer
matrix; pass one `--dataset` for every source:

The extracted dataset inherits the case evidence label. `--evidence-label` may
be used only to downgrade that label; attempts to promote synthetic or
literature-parameterized evidence fail closed.

```bash
python3 plugins/package_thermal/offline/assemble_transfer_matrix.py \
  --dataset results/golden/unit-000.json \
  --dataset results/golden/unit-001.json \
  --run-manifest results/sweep/3dice-run-manifest.json \
  --geometry geometry.json \
  --output results/thermal_transfer_matrix
```

The command is intentionally incomplete until all configured sources are
listed; partial source coverage is rejected. Output contains one
`source-<node>.csv` per source and a hash-bearing manifest for (H_{ij}(t)).

## Fit, validate, and decompose

Fit using training traces and all five disjoint held-out kinds:

```bash
python3 plugins/package_thermal/offline/fit_reduced_model.py \
  --training results/golden/unit-000.json \
  --training results/golden/unit-001.json \
  --held-out results/golden/held-square-wave.json \
  --held-out results/golden/held-burst.json \
  --held-out results/golden/held-mixed.json \
  --held-out results/golden/held-write-heavy.json \
  --held-out results/golden/held-read-heavy.json \
  --model-id package-rom-v1 \
  --geometry-sha256 SHA256_OF_GEOMETRY_JSON \
  --solver-identity 3d-ice-4.0-COMMIT-BINARY_SHA256 \
  --output results/package-rom.json
```

The fitted ROM inherits the weakest label among all training and held-out
datasets. An explicit `--evidence-label` can only downgrade it. Fitting is
centered for numerical conditioning; if stabilization is required, the affine
bias is transformed with `A` so the learned zero-power equilibrium is
preserved. The emitted runtime model remains in absolute degrees Celsius.

Validate transient RMSE, maximum error, steady hotspot, stack-gradient, and
threshold-crossing error. The default paper-facing acceptance targets are 1 C
for transient RMSE and steady hotspot error:

```bash
python3 plugins/package_thermal/offline/validate_reduced_model.py \
  --model results/package-rom.json \
  --held-out results/golden/held-square-wave.json \
  --held-out results/golden/held-burst.json \
  --held-out results/golden/held-mixed.json \
  --held-out results/golden/held-write-heavy.json \
  --held-out results/golden/held-read-heavy.json \
  --output results/rom-validation.json
```

For a linear ROM, decompose a trace into GPU-only, HBM-only, and HBF-only
temperature rise and verify their sum against the full coupled response:

```bash
python3 plugins/package_thermal/offline/decompose_rom.py \
  --model results/package-rom.json \
  --dataset results/golden/held-mixed.json \
  --initial-temperature-c 30 \
  --output results/contribution-decomposition.json
```

Finally generate the experiment manifest:

```bash
python3 scripts/package_thermal_manifest.py \
  --output results/manifest.json \
  --device-profile configs/profiles/nominal.json \
  --package-profile package-profile.json \
  --thermal-model results/package-rom.json \
  --model-kind rom \
  --thermal-mode package_rc \
  --thermal-stage read_only \
  --thermal-clock model_time_replay \
  --three-d-ice-version 4.0 \
  --three-d-ice-commit EXACT_COMMIT \
  --command 'exact experiment command'
```

Raw maps, solver logs, full traces, and external checkouts belong in an
experiment-results directory, not this repository.

## Current validation status

No real 3D-ICE executable or calibrated HBF material/geometry dataset was
available on the implementation host. Consequently no 8Hi/16Hi golden sweep,
ROM error claim, cross-talk Go/No-Go result, or sustainable-bandwidth claim is
reported here. The synthetic regression is available as:

```bash
ctest --test-dir build-package -R package_thermal_offline --output-on-failure
```

It exercises generation, dry-run and missing-solver failure, CSV extraction,
all-source transfer assembly, fit, all five held-out classes, C++ ROM loading,
linear decomposition, manifest generation, and malformed-input rejection.
