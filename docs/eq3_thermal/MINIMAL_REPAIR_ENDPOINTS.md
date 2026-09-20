# EQ3-MINIMAL-REPAIR-v1: path endpoint admission

Scope: `CpuService` engineering fixture only. This repair does not change MQSim,
the public ABI, event order, request/completion lifecycle, resource ownership,
checkpoint version, service durations, or maintenance policy.

## Evidence and classification

### Relay bypasses a controlled partner endpoint — `CONFIRMED_BUG`

- Invariant: every stack control domain actually traversed by a route must allow
  a job before any resource is reserved or activity energy begins. `Shutdown`
  blocks foreground and maintenance. `Severe` blocks foreground but retains the
  existing maintenance exemption. `Light` limits foreground admission and does
  not consume quota for maintenance.
- Location before repair: `src/eq3_thermal/cpu_service.cpp`, `route()` correctly
  included the paired HBM base/link resources, while `admit()` checked and
  updated only `job.stack`.
- Minimal reproducer: relay topology, target `hbf0=Normal`, paired
  `hbm0=Shutdown`, idle resources, one `hbf0` relay read.
- Actual pre-repair result: test exited 1 with
  `relay bypassed paired HBM Shutdown`.
- Preserved evidence:
  `eq3_thermal/plans/minimal-repair-v1/points/ENDPOINT-PRE-REPRO/` contains the
  source diff, manifest, stdout/stderr, limits, and result. The run used one CPU,
  12 GiB process / 16 GiB task limits, GPU 0, and the 600 s watchdog.

The earliest verified cause was admission using the target stack rather than
the already resolved route endpoints. This was not a missing resource lock or
thermal-model error.

## Minimal repair

`route()` now derives an internal, insertion-ordered, set-deduplicated
`control_endpoints` list alongside its existing resources. A direct request has
its target stack; a relay has the target HBF and paired HBM; external GDDR has
none. `admit()` checks every listed endpoint before resource reservation. A
successful foreground admission updates `next_admit` for every traversed
endpoint currently in `Light`.

The resource list, energy map, service interval, completion reporting, and
event-loop sequence remain unchanged. A blocked job remains in the existing
queue, so normal simulated-time advancement continues to process in-flight
completion, cooling, control transitions, and maintenance.

`report()` additionally derives `admission_blocks` from the current queue. It
reports the request, blocked stack, state/reason, exact Light retry time where
known, pending control-transition time where present, recovery condition,
busy resources, and future arrival boundary. This field is read-only and is not
stored in state or checkpoints; Shutdown/Severe recovery time remains unknown
unless a transition is already pending.

## Fixed regression contract

`tests/eq3_thermal/cpu_service_tests.cpp::path_endpoint_admission` covers:

- paired HBM Shutdown blocks relay with zero reservation and zero HBF/base relay
  energy;
- DASH relay is blocked while a direct route that does not traverse the partner
  remains legal;
- both endpoints in Light, differing endpoint quota times, and quota update on
  the paired HBM endpoint;
- DASH direct and relay share the HBF control endpoint without quota bypass;
- recovery applies before admission at the same timestamp, unique completion,
  blocked checkpoint/resume equivalence, and in-flight drain;
- Light and Severe maintenance exemptions remain unchanged; paired Shutdown
  blocks maintenance without leaking resources;
- blocker reason and unknown recovery time are visible in `admission_blocks`.

`control_pending_contract` separately confirms existing behavior: a sample whose
desired state equals the applied state cancels stale pending work; a more severe
sample replaces a pending Light action; sampling and action at the same timestamp
produce one deterministic transition. It does not change the control algorithm.

The post-repair suite before the last two contract additions passed as
`ENDPOINT-POST2-REGRESSION` (exit 0, 1.38 s). The first post-repair attempt is
retained as a test-fixture diagnosis: its recovery case used `policy=none`, so
automatic recovery was not defined. The corrected case explicitly selects the
existing `hysteresis` policy; no production behavior was changed for that test
failure.

Final frozen-source validation is `THERMAL-FINAL2-BUILD` (exit 0, 9.14 s,
sampled aggregate RSS 503480 KiB) followed by `THERMAL-FINAL2-TEST` (exit 0,
1.53 s): all four thermal/observer/CpuService/actual-MQSim-CPU suites passed.
The CpuService output explicitly reports the endpoint, pending-control, four
topology, maintenance, checkpoint, drain, cooling, and real Dispatcher CPU
fixture checks as passing. The prior `THERMAL-FINAL-TEST` failure is retained:
its binary was built immediately before a one-line test correction from
`advance_to(30 ms)` to `advance_to(30 ms + 1 ns)`. Existing service semantics
process completions at an exact horizon but do not start a new control action
there; the control log still verifies that the action occurred at exactly
30 ms. The incremental rebuild removed this source/build race; no implementation
change followed the frozen build.

## Capability boundary

The current relay/DASH constructor requires four unique HBF-to-HBM pairs.
Multiple distinct HBF stacks sharing one HBM partner are therefore
`NOT_SUPPORTED_BY_CURRENT_TOPOLOGY_INVARIANT`; this repair does not relax that
topology rule. Legal shared-endpoint behavior is tested with DASH direct/relay
paths sharing the HBF upstream/control domain. Supporting a many-to-one pairing
would be a separate topology/arbiter design change and is not implied by this
bug fix.

All observations here remain `ENGINEERING_FIXTURE`. They do not establish real
relay capability, calibrated control temperatures, physical energy, live MQSim
active gating, or GPU validation.
