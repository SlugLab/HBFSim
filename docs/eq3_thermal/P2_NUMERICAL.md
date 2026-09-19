# P2 numerical NEW_REFERENCE validation

Status: **reference discretization characterized; current RC validation FAIL**.  
Date: 2026-09-19 UTC. Reference ID: `eq3-p2-new-reference-20260919T054929Z`.

## Answer first

The pinned stock 3D-ICE executable successfully ran a new, small, explicitly
assumed package case at two spatial meshes and two time steps. The 2 mm to 1 mm
spatial comparison at 50 ms changed reported region averages by 0.113 K MAE
(0.629 K maximum) and the grid hotspot by 0.108 K MAE (0.264 K maximum).
Halving the time step from 100 ms to 50 ms changed region averages by 0.347 K
MAE on the fine mesh and the grid hotspot by 1.012 K MAE (1.766 K maximum).
This is useful numerical characterization, but only two levels were run, so it
does not establish an asymptotic convergence order.

The unchanged P1 implicit-Euler core, supplied with a geometry-derived lumped
network, failed the preregistered held-out targets. Across the entire held-out
asynchronous/hot-stack/pulse/cooling trace, aggregate region MAE was 4.944 K;
the worst individual region MAE was 10.325 K; sensor-average hotspot maximum
error was 40.876 K; and source-grid-hotspot maximum error was 52.518 K. The
301 K crossing times both occurred at 1.0 s and therefore met their 1.0 s
allowance, but the temperature targets did not. The overall result is FAIL.

No coefficients were fitted. In particular, this is
`GEOMETRY_DERIVED_NOT_FITTED`, not a calibrated RC. No result-dependent
parameter change was made after seeing either trace.

## Evidence boundary and configuration families

- `TEST_FIXTURE`: the existing `thermal_fixture.json` remains unchanged and is
  limited to software regression.
- `REFERENCE_BENCHMARK`: stock 3D-ICE at commit
  `e0bb6850c5e446363e26936586d625270c87f224`; silicon values (rho 2330
  kg/m3, cp 710 J/kg/K, k 150 W/m/K) and top HTC 1400 W/m2/K reuse the stated
  MFIT example context at commit `4444336d95fe7e9a3bf440c96937831126c6a37f`.
  They are not general package values.
- `PACKAGE_SCENARIO`: a new 40 mm square 4x4 tiled numerical case, with a
  central GPU heat-source region and four HBM/four HBF-labeled heat-source
  regions. Geometry, perfect contact, isotropy, and cooling are CASE
  assumptions. Labels distinguish separate input columns; they do not describe a
  physical HBF product or per-die stack.

The generator uses SI `C = rho * cp * V`. Source-to-spreader and lateral
conductances use face area and center-to-interface path lengths; the top
boundary includes half-spreader conduction in series with convection. The RC
has one common isothermal spreader node. That removes finite lateral spreader
resistance and is an evidence-backed candidate contributor to the mismatch
with distributed 3D-ICE; this bounded comparison did not isolate it as the
sole cause. Geometry-derived coefficients do not make the models equivalent.

The exact sensor mapping is the area-average temperature of each of nine
source floorplan regions. Separately, each 3D-ICE slot retains the source-layer
cell map; the grid hotspot is its maximum with cell-center x/y and
`PACKAGE.source` layer identity. A sensor maximum is not substituted for a
physical grid hotspot.

## Fixed traces and preregistered metrics

The training trace separately stages the GPU class, the HBM class, the HBF
class, a mixed interval, and cooling. The four HBM regions share one schedule
with different amplitudes, as do the four HBF regions; this is class-separated
excitation, not independent excitation of all nine inputs. Across nine input
columns there are at most four nonzero independent pattern rows (GPU, HBM,
HBF, and mixed), so per-stack parameter identifiability is NOT_ESTABLISHED.
The held-out trace is kept as one contiguous split and includes asynchronous
GPU/storage activity, a hot HBF-labeled region, a one-slot pulse, and three
cooling slots. Both engines consume identical per-region watts and interval
boundaries.

Before execution, acceptance was fixed at per-sensor MAE <= 1 K, grid-hotspot
maximum error <= 2 K, and 301 K crossing-time error <= max(two 0.5 s
observation slots, 5% of the reference crossing). A missing crossing passes
only if both models have no crossing. Training comparison was informational:
aggregate MAE 6.731 K and grid-hotspot maximum error 57.464 K, already showing
that this no-fit RC structure was inadequate. It was not changed before the
held-out evaluation.

## Runs, provenance, and resource receipts

Seven independent numerical configurations ran once with one thread each:

| ID | Engine | Split | Space | dt | Result |
| --- | --- | --- | --- | --- | --- |
| R1 | 3D-ICE | train | 2 mm | 100 ms | PASS execution |
| R2 | 3D-ICE | train | 2 mm | 50 ms | PASS execution |
| R3 | 3D-ICE | train | 1 mm | 100 ms | PASS execution |
| R4 | 3D-ICE | train | 1 mm | 50 ms | PASS execution |
| R5 | 3D-ICE | held-out | 1 mm | 50 ms | PASS execution |
| R6 | P1 RC | train | lumped | 50 ms observation | PASS execution; poor agreement |
| R7 | P1 RC | held-out | lumped | 50 ms observation | FAIL acceptance |

There were eight launch attempts. The first R1 launcher attempt failed before
the solver started because a workspace-relative executable path was interpreted
after changing the child working directory. Empty stdout/stderr and a causal
receipt are preserved; the runner now resolves executables before changing
cwd. The identical R1 retry used the eighth attempt. There were no solver
failures and no further retry.

Successful processes consumed 0.102 aggregate CPU seconds and 0.146 aggregate
wall seconds; the recorded process-tree RSS upper bound was 23,352 KiB and the
run tree is 1.6 MiB. These values are receipts for this tiny engineering case,
not forecasts for a complex package.

Raw inputs, per-node maps, axes, region averages/maxima, commands, binary hashes,
timestamps, exit codes, runtimes, and output hashes are under
`eq3_thermal/runs/p2-reference-20260919T054929Z`. The machine summary is
`docs/eq3_thermal/reference_manifest.json`. Raw solver outputs are immutable;
derived comparisons have their own hashes in that manifest.

Representative commands from the workspace root are:

```sh
python3 -B eq3_thermal/worktree/tools/eq3_reference.py generate \
  --case eq3_thermal/worktree/configs/eq3_thermal/reference/package_scenario.json \
  --materials eq3_thermal/worktree/configs/eq3_thermal/reference/materials.json \
  --power eq3_thermal/worktree/configs/eq3_thermal/reference/power_traces.json \
  --trace heldout --mesh fine --step-s 0.05 --output NEW_RUN_DIRECTORY

python3 -B eq3_thermal/worktree/tools/eq3_reference.py run --kind reference \
  --executable eq3_thermal/reference/build/3d-ice-stock-e0bb685-gnu17-longint/bin/3D-ICE-Emulator \
  --run-dir NEW_RUN_DIRECTORY --timeout-s 300

python3 -B eq3_thermal/worktree/tools/eq3_reference.py run --kind rc \
  --executable eq3_thermal/build/p1-core/hbfsim_eq3_thermal_cli \
  --run-dir NEW_RC_RUN_DIRECTORY \
  --power eq3_thermal/worktree/configs/eq3_thermal/reference/power_traces.json \
  --step-s 0.05 --timeout-s 300
```

The runner refuses to overwrite an existing run output.

## Checks and unresolved gaps

Five new generator/parser tests and all eight existing configuration tests pass.
Injected training energy is exactly 199 J in every reference discretization.
The fixed floorplan power vectors therefore agree by construction. Complete
3D-ICE heat-flux plus stored-energy balance was not instrumented, and the
cooling tails did not establish steady state; neither is claimed. Per-cell
maps preserve actual hotspot motion, including the held-out 430.986 K numerical
case hotspot at 3.0 s. That high number is a case-model result, not a safe or
real product temperature.

P2-PHYSICAL-PROXY remains separate, and P2-HBF-SILICON remains unavailable.
This work does not validate physical GPU/HBM/HBF temperature, package cooling,
reliability, runtime integration, maintenance, or closed-loop control. It also
does not reuse or replace any historical floorplan, golden, ROM matrix,
parameter, error claim, or paper conclusion. If historical artifacts become
available, their identity and applicability must be audited independently.

## Bounded next numerical validation (not executed)

The immediate follow-up is reference-only; no RC change is part of it. A
separately approved four-configuration check can run the fixed train and
held-out power traces at 1 mm/25 ms and 0.5 mm/25 ms, once each. This extends
space/time characterization without adjusting a model in response to the
failed held-out result. Exact resource limits and commands belong in the
coordinator's user-facing preflight. Budget correction: the workspace has used
9 of12 independent numerical configurations; three pre-solver setup failures
are logged separately. Fixed tests and bounded engineering checks may continue.
The proposed four-point follow-up is not approved by this report and must not
be split into smaller batches to bypass its user confirmation.

A later engineering design may evaluate per-region spreader nodes and face
conductances as one structural remedy, but the isothermal spreader has not been
proven to be the sole cause and that change is not implemented here. Because
the original held-out trace has now been viewed, it cannot remain the final
blind validation trace for any result-informed model selection or fitting. A
future per-stack calibration design must add a rank-nine training design with
independent excitation of every input, then preregister and preserve a fresh,
untouched final trace. Reuse of the present trace is limited to regression or
explicitly non-blind diagnosis. Changed geometry or material assumptions
require a new reference identity and approval.
