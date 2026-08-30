# Phase-II 3D-ICE campaign

`phase2_3dice_campaign.py` implements the minimum geometry/boundary/mesh study
needed before producing a new 10 ms scientific golden dataset.

Geometry arms are explicit:

- `g0_legacy_fixed_envelope` preserves the original 775 µm closure and its
  377/129 µm 8Hi/16Hi top-mold asymmetry.
- `g1_constant_top_interface` keeps die, bond, base, substrate, 4 µm filler,
  50 µm TIM, and 75 µm cap thicknesses constant; total height grows from
  527 µm (8Hi) to 775 µm (16Hi).
- `g2_fixed_envelope_explicit` fixes total height at 775 µm while retaining the
  explicit TIM/cap, leaving 252/4 µm filler for 8Hi/16Hi.

All unpublished package values remain `C` sensitivity evidence.  The script
records all three conductivity axes, volumetric heat capacity, thickness,
boundary, mesh, power normalization, solver identity, commands, and hashes.
It deduplicates physically identical steady cases shared by multiple studies.

The stock SuperLU-MT U-storage estimate is insufficient for the 16Hi/1 mm
reference mesh. `patches/3d-ice-superlu-u-fill-80.patch` raises only that
preallocation growth factor from 50 to 80 in a separate solver build.  The
original solver remains the golden solver; the patched binary is used only for
the next-finer mesh reference and receives its own executable hash.

`patches/3d-ice-nonuniform-bottom-sink.patch` fixes an independent 3D-ICE 4.0
non-uniform-grid defect found by the B2 arm.  The thermal matrix uses the proper
series combination of half-cell conduction and bottom-sink convection, but the
ambient source vector used half-cell conduction alone.  That mismatch drove a
nominal top+bottom case to thousands of degrees.  The patch makes the ambient
source use the same conductance formula as the matrix; it is experiment-tool
code and is never linked into HBFSim.

The required mesh sequence is 4000/2000/1000 µm (coarse/medium/fine), with an
optional 500 µm stress arm.  The 2000 µm candidate is accepted only if its
comparison with the next-finer 1000 µm mesh is within 0.5 °C at the HBF hotspot
and 2% in thermal resistance.  A 10 ms golden may be prepared only after that
result is known; no performance timestep is selected by this script.

The `prepare-timestep` command renders one scaled held-mixed trace at
10/25/50/100 ms for both heights.  Its summary compares every common timestamp
against 10 ms, including all-node RMSE/max error, hotspot RMSE, and
80/90/100/105 °C crossing classification/time.  The 50 ms flag covers thermal
fidelity only; sustainable-bandwidth equivalence remains a later acceptance
gate.  `prepare-golden` keeps 1 W unit-response traces unscaled and applies the
same documented 300/95/53.72 W peak-shape scaling only to held-out traces.

After the 10 ms campaign completes, `fit_phase2_rom.py` materializes strict
dataset JSON, keeps unit cases and five held-out classes disjoint, fits separate
8Hi/16Hi models with the corrected equilibrium-preserving fitter, enforces
RMSE/steady/max-all-node error at 1 °C, checks threshold classification, and
records linear source decomposition plus every tool/model/data hash.

## Host-side observability

Phase-II profiles may opt into a per-thermal-step CSV without changing the
shared CPU/GPU control ABI:

```json
"timeline": {"enabled": true}
```

The default is disabled.  When enabled, `hbfsimd` writes
`package-thermal-timeline.csv` beside `package-thermal.json`.  Rows include the
model and host timestamps, source/node powers, every node temperature, HBF
hotspot, raw/effective policy, debounce and dwell counters, actual gate/scale,
shadow counterfactual gate/scale, MQSim event/read/program/erase activity, and
host-sampled request/ring counters.  GDDR7 board telemetry remains in
`P_accelerator`; `P_gpu` and `P_gddr` are intentionally empty when the profile
declares `gpu_plus_gddr_lumped`, because those components are not separately
observable.

`thermal_blocked_requests`, gate episodes/duration, delayed-request count, and
sampled peak queue depth are also added to the aggregate JSON without changing
the meaning of existing fields.  Exact per-request admission wait and retry
counts are emitted as empty CSV cells / JSON `null` until client-side request
timestamps and retry counters exist.  The report records that limitation
explicitly; gate duration is not substituted for request wait time.

`analyze_phase2_timeline.py` consumes a timeline, its experiment manifest, and
the exact ROM artifact.  It derives the dominant thermal time constant from the
ROM eigenvalues, requires at least `5*tau` warm-up, and writes
`source-power-summary.json`, `stationarity.json`, and
`closed-loop-analysis.json`.  It fails closed on missing columns, missing
manifest evidence, unstable ROMs, insufficient duration, or absent active gate
transitions.  Queue/temperature slopes, served/offered tolerance, causal window,
power/activity drops, and accelerator-stationarity thresholds are recorded as
class-C analysis criteria and remain command-line configurable.
