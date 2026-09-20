# EQ3 MQSim interface contract and consumers

Status: CPU interface fixture for `EQ3-MINIMAL-REPAIR-v1`. This document does
not promote P2 to `MODEL_FREEZE`, does not claim live GPU coverage, and does not
turn the engineering occupancy fixture into command-level NAND evidence.

## Minimal gate design and non-interference argument

`MqsimSubmissionGateAdapter` is a header-only composition boundary around the
existing `MqsimOnlineEngine::submit()` call. It is `Off` by default. Off mode
passes the original request to the engine without changing its arrival or
completion. Enabled mode asks a caller-provided decision function only when the
request's target arrival has been reached. The callback returns `Allow`,
`Defer`, `Blocked`, or `Unsupported`, with an explicit reason for every result
other than `Allow` and a target time for `Defer`.

The adapter does not run the simulator, retry, retain completions, call the
observer, reserve backend resources, or own requests after submission. The
decision callback must not re-enter the engine. A caller handles `Defer` by
using the existing `run_next_completion_until(target)` API, consuming every
completion it returns, draining `MqsimObserverAdapter`, and calling
`try_submit()` again at the target. This preserves the existing MQSim event
ordering and lets in-flight work, observation energy, thermal time, cooling,
and an external controller recover through their existing consumers.

MQSim rejects a newly submitted request whose arrival precedes its current
clock. After an enabled gate delay, the backend copy therefore uses the actual
admission time as its arrival. The result records the original arrival,
backend arrival, and external wait separately. The returned MQSim completion
is never rewritten, and no service delay is appended to it. End-to-end users
must report `external_wait_ns` and MQSim backend latency as distinct terms.
Successful request IDs are remembered only to reject an accidental second
submission through the same wrapper; this does not change backend ownership.

## Interface and actual-consumer matrix

| Interface | Actual producer | Actual consumer | Enablement | Observable granularity | Capability | Remaining gap / regression |
|---|---|---|---|---|---|---|
| `MqsimObservation` | Existing `MqsimOnlineEngine` arrival, device handoff callback, and MQSim completion callback | `tests/eq3_thermal/mqsim_observer_tests.cpp::run`, `gated`, and `thermal_gate_closed_loop` through `MqsimObserverAdapter` then `ActivityObserver` | Explicit `enable_observations()`; automatically requested only for observer modes other than Off | Request arrival, admission, media callback end, separately delayed reported completion, logical bytes, device outstanding count | `CPU_TEST_CONNECTED`; production host service `PRODUCTION_NOT_CONNECTED` | No command ID, stack/die/plane, physical bytes, link bytes, or real operation energy. Fixed off/read-only/shadow test preserves these as unknown. |
| Request occupancy to energy | Test-supplied `ObservedEvent` with `ENGINEERING_FIXTURE_REQUEST_OCCUPANCY` | The same three CPU test functions consume the `ActivityObserver` energy ledger; Shadow test functions also consume its thermal model/advice | ReadOnly or Shadow plus explicit binding | Time between actual MQSim admission and media callback, with explicit component power | `CPU_TEST_CONNECTED`, engineering fixture only; `PRODUCTION_NOT_CONNECTED` | It is not NAND start or measured power. Queue wait has no activity energy. Fixed test checks no fabricated command location/bytes. |
| `MqsimSubmissionGateAdapter::try_submit` | Test decision callbacks plus current target clock | `tests/eq3_thermal/mqsim_observer_tests.cpp::{gated,thermal_gate_closed_loop,gate_contract}`; accepted requests go to unchanged `MqsimOnlineEngine::submit()` | `MqsimGateMode::Off` by default; explicit `Enabled` and callback | Whole demand request before backend submission; decision, reason, retry target, external wait | `CPU_TEST_CONNECTED`; production host service `PRODUCTION_NOT_CONNECTED` | No internal retry, scheduler, routing, resource reservation, or controller policy. Fixed test exercises future arrival, target defer, in-flight completion, observer advancement, recovery, and unique submission. |
| `run_next_completion_until` during gate wait | Existing MQSim event queue and reported-completion readiness marker | `tests/eq3_thermal/mqsim_observer_tests.cpp::{gated,thermal_gate_closed_loop,gate_contract}` and `MqsimObserverAdapter::drain_to_current_time()` | Test caller explicitly advances to returned gate target | Target simulation time; one original completion per call | Existing engine API; gate composition is `CPU_TEST_CONNECTED`, production gate consumer `PRODUCTION_NOT_CONNECTED` | Caller must continue until the target because an earlier in-flight completion can be returned first. No host sleep is involved. |
| Demand backend capability | `MqsimSubmissionGateAdapter::capability(Demand)` | Configuration/preflight checks | Query only | Capability identity | `SUPPORTED` for optional pre-submit gate | This says nothing about stack/path-specific physical control unless the caller has real route metadata. |
| Shadow temperature advice to gate | `ActivityObserver` Shadow model after target-time cooling; explicit fixture thresholds | `tests/eq3_thermal/mqsim_observer_tests.cpp::thermal_gate_closed_loop`, then `MqsimSubmissionGateAdapter` | Shadow plus explicit `ENGINEERING_FIXTURE` policy | One declared thermal component and whole demand request | `CPU_TEST_CONNECTED`; production controller `PRODUCTION_NOT_CONNECTED` | Thresholds, power, and one-node cooling model are engineering inputs, not a product control algorithm or calibrated physical claim. |
| Die-level maintenance submission/completion | No producer in `MqsimOnlineEngine` | Capability/preflight checks only | Query only | None | `UNSUPPORTED_CAPABILITY` | No enqueue/start/end/commit/fail facts, shared arbitration, resource or energy report. A normal host write must not be relabeled as HBF refresh. |
| External GDDR energy | Explicit caller metadata, if available | `ActivityObserver::external_energy_j()` | Explicit binding only | External power integrated over observed activity | Accounting supported | Package temperature is `UNAVAILABLE`; lack of a package node does not mean zero service energy or zero board heat. |

## Fixed CPU checks

`tests/eq3_thermal/mqsim_observer_tests.cpp` contains the source-level regression
for this contract. It compares default-off service with direct MQSim, compares
ReadOnly and Shadow event/completion streams, advances a deferred request with
the existing target-time API while delivering an earlier in-flight completion,
then admits the request once and checks that external wait is not folded into
the unchanged MQSim completion. It also verifies no callback is evaluated
before a future request arrives, blocked work never reaches the backend, and
die-level maintenance remains `UNSUPPORTED_CAPABILITY`. A separate one-node
engineering fixture starts above its explicit Light threshold; the caller
advances MQSim's target clock, drains the observer so boundary cooling occurs,
reconsumes real Shadow advice, submits once after recovery, and receives the
original MQSim completion. This closes the CPU chain without adding a runner or
production scheduling policy.

Final fixed CPU validation is `THERMAL-FINAL2-TEST`: all four thermal suites
passed (13 thermal-core checks plus observer, CPU-service, and actual-MQSim
suites), with the MQSim suite reporting both `CPU_PATH_VERIFIED` and
`MQSIM_GATE` PASS. The immutable evidence is under
`eq3_thermal/plans/minimal-repair-v1/points/THERMAL-FINAL2-TEST/` in the outer
workspace (`result.json`, `stdout.log`, `stderr.log`, manifest and source
snapshot). This was a 0.61 s CTest run; it is a fixed software validation, not
a research experiment or live-GPU result.

The retained earlier `THERMAL-FINAL-TEST` correctly failed the first test
caller version with `observer did not consume wait/completion time`. That
caller used legacy `run_next_completion()` after gate recovery: MQSim may
return a completion carrying a later bandwidth-bounded reported time while the
engine clock is still at the media callback, so the observer's reported
completion remains pending. The repair changed only the test consumer to keep
using `run_next_completion_until()` and drain after each horizon advance. It
did not alter MQSim, the gate, the completion timestamp, or latency semantics.

This task intentionally does not add a real active controller policy. A route-
aware decision producer with validated stack/path identities is still required
before this gate can claim topology-aware active control.
