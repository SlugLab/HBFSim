# EQ3-MINIMAL-REPAIR-v1

Local repair and CPU interface work, based on integrated main `a93c0c1ae2531b4224c00d6917c64c0358878a9a`.
Entry branch `integrate/eq3-main-20260920`, clean. The governance workspace root
is not a Git repository; actual source is `eq3_thermal/integration/main-20260920`.
Immutable baseline `5eb789d5f1a42f0c040ee6fb5a2cdb5ffa0951d5` remains clean.
No reset, push, merge, dependency installation, GPU or research matrix.

USER_CONFIRMED: current request adopts attached taskbook and explicitly permits
minimal local fixes, optional default-off composition interfaces and CPU checks.
Latest policy added to both effective AGENTS.md. DOC_DERIVED evidence below;
physical inference is not substituted for unavailable measurements.

## Issue classification and minimal scope

| Item / source | Invariant and reproduction | Classification / disposition |
|---|---|---|
| `CpuService::Impl::{route,admit}` | HBF Normal + paired HBM Shutdown + free relay resources; original binary starts it, new fixed regression fails with `relay bypassed paired HBM Shutdown` | CONFIRMED_BUG; joint checks on deduplicated actual route domains before any reservation; all successful foreground Light quotas updated |
| `CpuService::Impl::report` | Rejected work needs actionable endpoint/reason without fabricating completion | Read-only `admission_blocks` derived from current queue/control/resources; no checkpoint layout change |
| `control_sample` pending | Existing `desired==applied` already clears pending | NOT_REPRODUCED / ALREADY_FIXED; do not reimplement |
| Escalation dwell | Existing action delay and minimum dwell apply to all transitions; no contrary authoritative emergency contract found | DESIGN_LIMITATION / optional policy decision; retained unchanged |
| CpuService fixed duration/power/base ownership | Fixture explicitly defines whole-request duration and serial base occupancy | DESIGN_LIMITATION; no new NAND simulator, no removed base lock or additive latency rewrite |
| Real MQSim observation | Existing API gives request admission and raw/report completion, not NAND command start/location | DESIGN_LIMITATION; keep unknown die/plane/physical/link bytes, occupancy energy proxy explicit |
| Real MQSim demand gate | Existing horizon API can progress inflight work and cooling before submit | Optional thin nonblocking wrapper; default off, external wait separate, original engine and completion unchanged |
| Real MQSim die maintenance | Backend has no corresponding operation/commit interface | DESIGN_LIMITATION / UNSUPPORTED_CAPABILITY; no fake host writes or duplicate resource ledger |
| Sparse RC failure evidence | Tiny valid-domain initial state leaves domain at first step; frozen v2 reports error but omits last/trial snapshot | CONFIRMED_BUG in diagnostics; local evidence patch, failure remains failure |
| Reference accuracy / low-rise normalized MAE | Historical spatial differences fail0.25K;5% at1K floor is0.05K | NUMERICAL_ACCURACY_LIMIT; old standards retained; common-window diagnostics separate |
| Development400.911K | Independent reference exceeds declared300–400K domain | DOMAIN_FAILURE; preserved, no clamp or changed input |
| Full1mm100s | Pilot-based projected2000–2600s exceeds600s | RESOURCE_BLOCKED; not rerun or split |
| Sparse reuse/order/ownership | Existing sparse solver factors once at immutable config/fixed dt; reference MMD and ownership fixes already present | ALREADY_FIXED / no rewrite |

Source-level equation review: dense `ThermalModel::Impl::factor` implements
C/dt + boundaryG + edge-Laplacian; RHS uses prior thermal state, input W and
boundary W/K×K. Config is immutable per instance and factor cache is keyed by dt;
new config/reset creates a new instance. Sparse runner factors fixed matrix once,
using theta=T−initial-origin and transforming boundary temperature consistently.
No equation or material changes are authorized or made.

## Reuse, workload feasibility and old results

The six previous points are all `mixed_direct` from `tools/eq3_cpu_fixture.py`.
The patch preserves its single controlled endpoint, resource mapping and quotas;
relay-specific additional domains cannot affect that path. Historical Safe/Near/
Stress remain valid *engineering* records and are reused, not relabeled as reruns.
Near had no cooling benefit; Stress completed448/1000, leaving552 foreground and
56 maintenance queued. No gain is manufactured by changing inputs.

The same generator supplies a request each40ms/stack (25/s),20ms read occupancy:
foreground nominal base utilization0.5. HBF16die maintenance60ms every2s adds0.48;
HBM12die×5ms/1s adds0.06. Light80ms permits at most12.5 foreground starts/s
before competing maintenance. These DOC_DERIVED screening numbers explain why
lower temperature can come with backlog; they are not new measurements.

Small fixed2die topology tests do not replace12/16die research geometry. The new
one-node MQSim cooling/input example is ENGINEERING_FIXTURE_REQUEST_OCCUPANCY,
not measured NAND activity, physical calibration or a production scheduler.

## Evidence and reproduction

Private immutable point receipts, commands, prelaunch source patch/new-source
snapshots and raw stdout/stderr are at workspace
`eq3_thermal/plans/minimal-repair-v1/points/`. The adapted existing safety runner
is `run_check.py`, with scope and environment records alongside. Existing core
archives from integrated main are bound once in `build_inputs.json`; fresh EQ3
source is rebuilt in a different build directory. No repeated large raw hashing.
Current source and manifest identities are for this task, not invented historical
observations. Individual point failures are retained even when a test fixture
needs a documented correction.

Additional detailed results and final commit IDs are appended after validation.
See [interface consumers](INTERFACE_CONTRACT_AND_CONSUMERS.md),
[P2 diagnostics](MINIMAL_REPAIR_P2.md), and
[required decisions](USER_DECISIONS_REQUIRED.md).

## Four topologies: final acceptance boundaries

| Topology | Configuration | Thermal research model | System/backend behavior |
|---|---|---|---|
| mixed-direct | Existing generic4+4 and2+6 fixtures;12/16die mainline inputs retained | P2 not frozen; small fixture thermal equations only | CpuService CPU regression PASS; old6points unaffected/reused; actual MQSim request-level gate tested independently, not topology-qualified |
|4+4 relay | Four unique pairs unchanged | Relay/base physical power unqualified | CpuService joint-endpoint gating/Light/resource/energy PASS; actual MQSim relay topology not established |
|four-pair DASH | Direct/relay routes preserved, shared HBF upstream | Research thermal validation incomplete | CpuService legal direct isolation and shared endpoint quota PASS; no new auto-routing; actual MQSim DASH not established |
|8HBF + external physical GDDR | Existing eight-HBF fixture/explicit external GDDR retained | Package excludes GDDR, temperature UNAVAILABLE; research geometry incomplete | CpuService service/energy accounting PASS, nonzero external energy separate; no real GDDR or GPU validation |

Many-HBF-to-one-HBM pairing is rejected by the existing unique-pair invariant.
Tests use legal DASH shared HBF endpoints and HBM/relay shared resources; no
unapproved topology change is hidden in the repair. CPU_TEST_CONNECTED for the
new MQSim gate does not mean PRODUCTION_CONNECTED or real NAND maintenance.

## Local validation failures retained

`ENDPOINT-PRE-REPRO`: required pre-fix failure. `P2-ORIGINAL-DOMAIN-REPRO`:
required missing-snapshot reproduction using frozenv2 binary.
`ENDPOINT-POST-REGRESSION`: new recovery fixture accidentally used policy=none;
corrected fixture to explicit hysteresis, kept production control unchanged.
`THERMAL-FINAL-TEST`: pending-boundary test fix landed after build; incremental
rebuild bound it. MQSim added test initially consumed ordinary next-completion
instead of horizon readiness, so observer had not reached the reported deadline;
corrected caller to existing horizon API, not engine latency.
`THERMAL-FINAL2-TEST`: all4 suites PASS, including actual MQSim off/read_only/
shadow parity, disabled gate parity, defer/inflight handling, actual thermal
cooling→advice→gate→backend submission→unique completion, and all four CpuService
topologies. No policy or physical input was optimized to make these pass.

## Final verification / limits / rollback

- `THERMAL-FINAL2-TEST`:4/4 C++ suites PASS.
- `CORE-RELEVANT-REGRESSION`:10/10 existing MQSim/horizon/service/trace timing
  and protocol tests PASS. No full-main-suite or GPU pass is inferred.
- `FOUR-TOPOLOGY-CONFIG-REGRESSION`:59/59 configuration, geometry export, source
  mapping and fixed workload checks PASS. No research geometry solve.
- `P2-RC-DIAG-TEST2`:15 total,13 PASS,2 optional generated-candidate checks
  skipped (paths absent in integrated checkout); not15 passed plus2 skipped.
  Independent2node hand solution, node reorder, non300K equilibrium, dense
  comparison, finite domain failure, nonfinite trial, input parse rejection,
  write-path refusal and completed-energy1.5J vs unknown failed-step energy pass.
- `P2-COMMON-FIRST4S`: read-only reduction PASS; numerical qualification still
  FAIL (2.710K and1.277K against0.25K); window cannot validate full excitation.

P2-RC-DIAG-TEST retained one test expectation failure: `nan` is rejected by the
existing stream parser as `malformed node record`, before a finite-value check.
Only the assertion was corrected to that actual contract. No parser change.

All steps serial CPU1,OMP/BLAS1,GPU0/cloud0; build parallel1. Largest measured
child RSS2,203,356KiB (~2.10GiB), sampled aggregate peak <=0.51GiB (sampling
is a lower bound, not instantaneous exact aggregate). All invocations <600s,
new task package <4GiB and cumulative retained ~17.28GBdecimal (~16.09GiB),
below20GiB. No baseline/raw deletion. Existing main two blocked groups
(context_lifecycle12GiB allocation, vmem_tuning externalCSV) remain inherited
limitations; no irrelevant rerun or repaired-status claim.

Each point has prelaunch command/source status/diff and resource metadata.
Existing host archive identity bound once at setup; final executable fingerprints
are explicitly POST_VALIDATION_HANDOFF in `VALIDATION_SUMMARY.json`, not claimed
as observations captured before the historical runs. Fresh source was built
in the new task directory; cross-host recreation was not performed.

Local branch `repair/eq3-minimal-v1` starts at integratedmaina93c0c1; commits:
`dfe12d5` rules; `aba746e` endpoint repair; `f2e4ace` optional MQSim gate;
`7153ae6` RC diagnostics and analysis. Documentation-only final commit follows.
Rollback is opt out of the gate (default Off), or revert the relevant local
commit; do not reset baseline/main history. Never reinterpret old relay
fixtures as repaired results; use this task's new evidence.

The feasible nonstructural repair work is complete. Production route-aware
thermal control, real die maintenance, calibrated energy and P2 qualification
remain open for the reasons and specific options in USER_DECISIONS_REQUIRED.md.
