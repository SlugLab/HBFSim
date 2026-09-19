# EQ3 campaign sparse-RC static audit

Audit date: 2026-09-19 UTC  
Source revision inspected: `8466487e3eee7de1ef8b8fd7ab3a22a718538b0a`  
Evidence class: `DOC_DERIVED` unless explicitly marked `INFERRED`  
Scope: static inspection of the existing 3,087-node train candidate, sparse
runner, and fixed regression. This audit did not compile, run a test, construct
a solver, or start a research solve/pilot. R02 was left as the sole solver.

## Bound candidate evidence

The inspected files are under
`eq3_thermal/generated/layered-v1/train-4mm-20ms/`:

| File | SHA-256 |
|---|---|
| `model.txt` | `8a76dddc46459d0b380d6bebcfec529e8f5ffbfd8e1b0038f7d8693089d35f6e` |
| `events.txt` | `b1674c3ef83b7373d98eb55317d8109d3e55b00bbb057eefc1cf7b6614cf980e` |
| `rc_grid.json` | `c5af7755cfa70f32c24206546b62f0de62d4a864ccd8ec50780f7a5ccec38806` |
| `reference_grid.json` | `8db7eee7e1fa02993b94dd6dbe4e456f69a3fcf4f8ad8b395eec2f3d50bf0395` |
| `normalized.json` | `aa3a466249be16949e106b8e2cec573efc0ba43e948d62b4386a1d13ff690c73` |

These hashes identify this audit only. They are not a new model freeze or an
acceptance of the old RC result, which remains `FAILED`.

## Candidate matrix and thermal-path invariants

Static parsing of the emitted model found:

- 3,087 nodes, 8,330 edges, coupling enabled;
- every heat capacity finite and strictly positive, range
  `1.2319999999996292e-05` to `0.4415488 J/K`;
- every edge conductance finite and strictly positive, range
  `3.749999999998872e-07` to `4517.647058823622 W/K`;
- all boundary conductances finite and non-negative; 98 nodes have positive
  Robin conductance, with total `5.828486284017786 W/K` and maximum
  `0.3580851010417974 W/K`;
- 8,330 unique unordered endpoint pairs, with no duplicate pair and no
  self-edge; and
- a graph search starting at the 98 positive-Robin nodes reaches all 3,087
  nodes. Thus every emitted node has a conductive path to a declared sink.

The core parser independently rejects non-positive/non-finite heat capacity,
negative/non-finite boundary conductance, self-edges, and
non-positive/non-finite edge conductance (`src/eq3_thermal/thermal.cpp:36-66`).
The sparse runner constructs each edge as `+G` on both diagonals and `-G` on
both symmetric off-diagonals (`tools/eq3_campaign_rc_runner.cpp:251-278`).
Consequently the emitted conductance matrix is symmetric. For any non-zero
vector `x`, the transient matrix has quadratic form

`sum_i (C_i/dt + G_boundary_i) x_i^2 + sum_(i,j) G_ij (x_i-x_j)^2`,

which is strictly positive because `C_i/dt > 0`. The sink-connectivity result
is an additional physical steady-state-path check, not a substitute for an
actual numerical factorization.

### Eight base dies

The normalized candidate declares `hbf0.base` through `hbf3.base` and
`hbm0.base` through `hbm3.base` as eight powered base components. For each:

- `rc_grid.component_cells` maps the component to exactly two RC cells;
- both mapped cells belong to that base's group in `model.txt`;
- both cells are in the graph component reachable from a Robin sink;
- the observation contract contains distinct component `mean` and `hotspot`
  sensors; and
- the complete 100 s event file contains four activities and approximately
  26 J assigned to the base (per-base totals range only by floating-point
  representation from `25.999999999999954` to
  `25.999999999999964 J`).

This establishes static source, state, path, and observation presence. It does
not establish numerical temperature accuracy.

## What the current sparse-runner regression does and does not prove

The fixed two-node regression compares sparse and existing dense temperatures
to 10 decimal places and five energy fields to 9 decimal places
(`tools/test_eq3_campaign_rc_runner.py:69-103`). Inspect-only verifies that a
solver and receipt are not created (`:105-121`). The generated-candidate check
only parses/inspects and checks 3,087 nodes, 20,000 steps, and a structural-nnz
upper bound (`:123-140`). Therefore the following remain unmeasured or
untested for the actual candidate:

- actual 3,087-node symbolic analysis and LDLT factorization success;
- factor fill, factor storage, peak RSS, factorization time, per-solve time,
  output time, and 100 s wall-clock feasibility under the 600 s watchdog;
- an explicit candidate-level numerical symmetry check (static assembly was
  audited, but no matrix was numerically formed in this audit);
- candidate-level agreement with the dense equation;
- base-source activity and base sensor output in a sparse solve;
- coupling-off semantics, duplicate-edge accumulation, and a multi-edge
  analytic fixture;
- negative regression cases for duplicate activity IDs, slot misalignment,
  domain escape, receipt overwrite, and factorization/solve failure; and
- long-horizon time-label and accumulated-energy behavior.

The runner factors one constant matrix once and reuses it for every step
(`tools/eq3_campaign_rc_runner.cpp:367-438`), but its receipt currently reports
only structural matrix nnz and the constant `factorization_count=1`
(`:315-360`). It does not measure factor fill.

For the next runner receipt revision, record at least: actual factor `L` nnz,
an explicitly defined fill ratio, factorization/analyze wall time, total and
maximum solve time, output time, steps completed, frames/bytes written, and
launcher-observed maximum RSS. Retain structural matrix nnz and factorization
count. `INFERRED`: Eigen may not expose exact allocator overhead for the factor;
therefore `L` nnz plus process maximum RSS is stronger evidence than labeling
an nnz-derived byte count as exact factor memory.

## Bounded 4 s prefix resource pilot plan

The original train events within `[0,4 s]` contain four GPU-only activities:

| Interval (s) | Energy (J) | Equivalent constant power (W) |
|---|---:|---:|
| 0.5-1.0 | 25 | 50 |
| 1.5-2.0 | 100 | 200 |
| 2.0-2.5 | 100 | 200 |
| 2.5-3.0 | 100 | 200 |

The other prefix slots are idle. Total declared prefix energy is 325 J. With
the existing 5 ms step and 0.1 s sampling this is 800 steps, 41 frames
including `t0`, and 126,567 node rows plus the CSV header. All four activities
end before 4 s, so no activity needs partial-energy clipping.

The pilot must retain the same 3,087-node model, physics, matrix, 5 ms step,
0.5 s power slots, temperature domain, and original event amplitudes. Its
purpose is limited to actual factorization/fill/RSS/timing, time/energy
integrity, and output feasibility. It does **not** exercise an HBM/HBF array or
base heat source and therefore cannot validate base power mapping or F01
accuracy. A successful resource pilot should proceed to the complete F01 under
the campaign gate; it must not replace F01.

Before launch, bind source/binary/model/event-prefix hashes, use a fresh output
directory, keep one CPU and BLAS/OMP thread, and ensure no overlapping solver.
Require factorization success, one factorization, all 800 steps, finite/in-domain
temperatures, declared/applied energy agreement at 325 J, an independently
checkable energy receipt, and compliance with the 12 GiB process, 16 GiB task,
4 GiB point, and 600 s limits. Separate one-time factorization time from solve
and output time before projecting the 20,000-step F01; a static linear runtime
claim is not evidence of 600 s feasibility.

## RC 7x7 versus reference 16x16 spatial diagnosis

Both grids use the same 63 z intervals. Their lateral discretizations differ:

- RC axes in both x and y are
  `[0,16,20,28,36,44,48,64] mm`, giving 7x7 nonuniform cells with widths
  from 4 to 16 mm. These planes are the geometry-edge union generated by
  `tools/eq3_layered_export.py:23-45`; they preserve declared component edges
  but do not refine component interiors.
- The inspected reference axes are uniform 4 mm from 0 to 64 mm, giving
  16x16 lateral cells.
- The GPU occupies 9 RC x-y cells. Every one of the 112 array dies and all
  eight base dies occupies only two RC x-y cells. Cell counts above two for
  some dies arise from z subdivision, not additional lateral resolution.

`INFERRED`: this large lateral-resolution difference can plausibly dominate
component hotspots and spatial spreading error even if energy conservation is
good. It is a diagnosis candidate, not a confirmed cause until F01 is compared
against a reference that has met the campaign's reference-discretization gate.

Apply the registered diagnostic order first: units/clock/energy, observation
mapping, contact/boundary equivalence, and time integration, then RC spatial
coarsening. If error localization confirms spatial coarsening, the authorized
repair is a **new model ID** produced from the same physical geometry,
materials, sources, and boundaries with added numerical x-y planes (globally
or evidence-driven locally). Preserve this 7x7 candidate and its result. Do not
tune material values, cooling, power, thresholds, topology, or sensor semantics.
Re-run affected train/development RC points only; unchanged reference results
remain reusable subject to their own acceptance gate.
