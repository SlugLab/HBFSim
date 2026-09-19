# EQ3 thermal: staged design and execution contract

User-approved task: HBFSim_EQ3_Thermal_Codex_Prompt.md (2026-09-19).
Frozen baseline: 5eb789d5f1a42f0c040ee6fb5a2cdb5ffa0951d5.
This namespace is the new thermal EQ3; historical thermal EQ2 and capacity EQ3
are not renamed. No change to canonical Overleaf manuscript is made.

## P1 implementation choice

Use a separate C++20 CPU library with explicit SI units, positive heat capacities,
nonnegative symmetric conductances, and implicit Euler integration. The resulting
matrix C/dt+L+Gb is positive definite for positive C and dt; the boundary and
injected energy determine the right-hand side. Numerical validation uses analytic
one-node decay, isolated energy conservation, and time-step refinement. This is
a full-order lumped RC fixture, not a calibrated ROM or HBF silicon prediction.

Alternative reuse of the donor runtime is rejected for this phase: it imports
unrelated protocol/async/TMA changes or unavailable reference dependencies.
A Python-only solver would duplicate the future C++ host adapter; Python is
used only for declarative configuration validation/translation.

The core owns nodes, edges, physical-activity energy intervals, a monotonic target
simulation clock, checkpoints, and simulated sensor snapshots. Logical role and
physical kind remain separate. The provider boundary permits future measured or
replayed inputs. Missing sensors remain unavailable, not zero. No control action
is represented as enforced until a later adapter actually acts on service.

The standalone build has no dependency on CUDA, MQSim or bpftime. The existing
root build and runtime are unchanged; not linking the module is the default
off behavior. A local off mode must also avoid constructing a solver.
No PTX, shared ABI, request protocol, page pin, cache or future changes are allowed.

## Configuration responsibilities

Device facts, data topology, assumed thermal network, activity/power, reliability,
and control are separate JSON documents. Source ledger entries carry units,
definitions, status and provenance. Unknown device fields are null, not zero.
Topology generator validates node ownership, stack totals, relay resource sharing,
capacity and interface arithmetic where fully specified. It does not simulate
data arbitration merely by drawing edges. DSAH→DASH four-pair name mapping is
USER_CONFIRMED; the extension remains assumed architecture, and standard HBM4
does not imply a peer relay.

Thermal fixtures include GPU, stack base and storage dies, interposer and cooling
paths. Their arbitrary numeric RC coefficients are marked ASSUMED_TEST_FIXTURE.
They establish software behavior only. Physical geometry/material derivation,
3D-ICE calibration and scientific parameter sensitivity remain P2 gates.

## Verification and rollout

P0 preserves a detached clean baseline and runs its CPU tests in an external build.
P1 tests use fixed tolerances before execution: analytic time-discretization error
must decrease with step refinement; isolated energy error is floating-point sized.
Off, unavailable sensors, invalid/nonfinite input, window overlaps, completion
timestamps and checkpoint model identity are negative tests. Build again from a
different source/build path; copied binaries never count as reproduction.

P2 performs a bounded donor provenance search without merging it. The user-approved
P2 continuation permits NEW_REFERENCE after historical inputs remain unavailable;
exact historical replay is not a prerequisite for independent numerical validation.
Numerical agreement is not physical calibration. P3 adds only verified
host activity observers and proves off/read_only/shadow parity. Existing request
observations alone cannot establish NAND command/die energy. P4 requires actual
maintenance scheduling/completion/energy/wear, and stops before unapproved ABI
or device-path restructuring. P5 is gated by analytical feasibility, preflight,
frozen parameters, and real trigger/recovery trajectories; no artificial yield
target or predetermined compute-first fraction.

## Resource and evidence contract

At most four aggregate compiler jobs and serial suites; OMP/BLAS threads=1,
8 GiB RAM and 20 GiB task disk budget, GPU compute zero until a specific
version/hash-bound preflight receives explicit user approval, even when devices
are visible. No system installation or driver work. Software unit runs are not
paper-candidate experiments. Every later run retains immutable commands, source
and configuration hashes, exit codes and evidence class. Small local commits
are permitted; no push, merge, external publication or old data rewrite.
