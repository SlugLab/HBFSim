# EQ3 MQSim interface contract and consumers

Status: CPU interface fixture for `EQ3-MINIMAL-REPAIR-v1`. This document does
not promote P2 to `MODEL_FREEZE`, does not claim live GPU coverage, and does not
turn the engineering occupancy fixture into command-level NAND evidence.

`EQ3-DECISION-EXECUTION-v2` adds an actual JSON-lines CPU-service consumer of
the existing gate and a default-off native MQSim command observer. The new
source is implemented but remains `VALIDATION_PENDING` until the coordinated
isolated build and fixed CPU test finish.

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
| `MqsimSubmissionGateAdapter::try_submit` | Test decision callbacks plus current target clock | `tests/eq3_thermal/mqsim_observer_tests.cpp::{gated,thermal_gate_closed_loop,gate_contract}` and the actual `benchmarks/replay/hbf_mqsim_service.cpp` `try_submit` command; accepted requests go to unchanged `MqsimOnlineEngine::submit()` | `MqsimGateMode::Off` by default; service fixture enables a fixed target-time callback only with `--gate-not-before-ns` | Whole demand request before backend submission; decision, reason, retry target, external wait | `CPU_SERVICE_CONNECTED`, `VALIDATION_PENDING`; no production thermal policy | The service holds no deferred request and performs no retry. Its client must use `until`, deliver any earlier completion, drain returned events, and retry the same request. The CLI policy is explicitly `ENGINEERING_FIXTURE`, not active thermal control. |
| `run_next_completion_until` during gate wait | Existing MQSim event queue and reported-completion readiness marker | `tests/eq3_thermal/mqsim_observer_tests.cpp::{gated,thermal_gate_closed_loop,gate_contract}` and `MqsimObserverAdapter::drain_to_current_time()` | Test caller explicitly advances to returned gate target | Target simulation time; one original completion per call | Existing engine API; gate composition is `CPU_TEST_CONNECTED`, production gate consumer `PRODUCTION_NOT_CONNECTED` | Caller must continue until the target because an earlier in-flight completion can be returned first. No host sleep is involved. |
| Demand backend capability | `MqsimSubmissionGateAdapter::capability(Demand)` | Configuration/preflight checks | Query only | Capability identity | `SUPPORTED` for optional pre-submit gate | This says nothing about stack/path-specific physical control unless the caller has real route metadata. |
| Shadow temperature advice to gate | `ActivityObserver` Shadow model after target-time cooling; explicit fixture thresholds | `tests/eq3_thermal/mqsim_observer_tests.cpp::thermal_gate_closed_loop`, then `MqsimSubmissionGateAdapter` | Shadow plus explicit `ENGINEERING_FIXTURE` policy | One declared thermal component and whole demand request | `CPU_TEST_CONNECTED`; production controller `PRODUCTION_NOT_CONNECTED` | Thresholds, power, and one-node cooling model are engineering inputs, not a product control algorithm or calibrated physical claim. |
| Native MQSim command phases | Patched `NVM_PHY_ONFI_NVDDR2` existing command issue, command/data-in completion, chip-ready, and read-data transfer boundaries | `benchmarks/replay/hbf_mqsim_service.cpp`, with raw assertions in `tests/integration/test_mqsim_service.py::test_native_command_observation_off_on_parity_and_identity` | Off by default; `--native-command-observations on` installs an immutable synchronous sink | Unique command and transaction IDs, optional external request parent, MQSim source/type, logical page, physical channel/chip/die/plane/block/page, backend bytes, native time | `NATIVE_PHASE_OBSERVER_CPU`, `VALIDATION_PENDING` | No stack identity, route identity, calibrated energy, or maintenance parent. A null external parent is preserved for background commands. Callback only appends facts and never advances/re-enters MQSim. |
| Persistent HBF stack placement | `MqsimStackMapAdapter` address bijection plus native command channel observations | `benchmarks/replay/hbf_mqsim_service.cpp`, with raw assertions in `tests/integration/test_mqsim_service.py::test_explicit_eight_hbf_stack_map_reaches_native_channels` | Off by default; explicit `--stack-map` with HBF/direct/CWDP profile-matched channel groups; enabled requests supply `stack`, `stack_local_page`, and `route=direct` | External/backend page, requested/resolved HBF stack, expected and actual native channel/die/plane | `ACTUAL_MQSIM_CHANNEL_PARTITIONED_HBF_STACKS`, `VALIDATION_PENDING` | Same-kind HBF direct, one aligned page only. The small 8-stack/1-die profile is an engineering fixture, not research geometry. Mixed HBM/KV placement, relay/DASH and package links remain unsupported. |
| Native phase energy | No producer | No consumer | Not available | None | `DECLARED_ONLY` is not claimed; capability is absent | Native timestamps and bytes do not supply operation energy. Occupancy-proxy and native-phase evidence must be selected as mutually exclusive energy inputs. |
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

## D5 CPU-service and physical-address boundary

The service's `try_submit` command is the actual existing CPU process and uses
the same `run_next_completion_until` path as `until`. It returns the original
arrival, backend admission time, external wait, explicit disposition/reason,
and target time. It does not retain a deferred request. Default-off
`try_submit` is compared with the existing `submit` command for the same
request observation stream and completion. Enabled testing advances to the
returned target, retries once, and checks that the backend completion remains
unchanged rather than receiving a second delay.

The native command patch is registered as
`patches/mqsim/0003-hbf-command-observer.patch`; the vendored
`third_party/mqsim` tree remains source input rather than the release delta.
When disabled, no sink is installed and no observation IDs are allocated.
When enabled, the engine copies the external request ID into an internal
observation-only field. MQSim assigns transaction IDs lazily when a real
command is observed and assigns one command ID to each native command. A
multi-page request therefore keeps one external parent while exposing multiple
transactions and commands. Multiplane commands may conversely contain multiple
transactions under one command ID. `GC_WL`, mapping, and cache commands retain
their actual MQSim source and a null external request parent unless a real
parent exists; the observer does not infer one.

`MqsimOnlineEngine` configures one MQSim device with
`Flash_Channel_Count=profile.channels`, one chip per channel,
`Die_No_Per_Chip=profile.dies_per_channel`, and
`Plane_No_Per_Die=profile.planes_per_die`. `Input_Stream_Manager_HBF` segments
logical byte extents into page LPAs. Page-level mapping then assigns LPA to
channel/chip/die/plane according to the selected `plane_allocation_scheme`
(default `CWDP`), while `Flash_Block_Manager` owns the page/block allocation.
The base D5 CPU test submits a two-LPA read and requires real command observations
on at least two physical channels. This is evidence for one MQSim device using
multiple channels. With no `--stack-map`, no configured or consumed mapping
connects a channel to a package stack, so `stack=UNKNOWN` and addresses pass
through unchanged. The optional map partitions those existing channels into
explicit, disjoint same-kind HBF groups and applies a persistent global-page
bijection before the unchanged submit/gate. Native command observations then
hard-check the configured expected channel. This is channel-partitioned HBF
stack evidence, not separate MQSim devices, HBM placement, package topology,
relay routing, or balanced mixed-stack service.

## Backend completion and optional fabric composition

MQSim's raw request callback occurs only after its NAND command path, including
native ONFI command and read-data transfer phases. The adapter records that
instant as the `MqsimObservation::Completion.time_ns` media callback. It then
computes the existing serialized aggregate-bandwidth lower bound from
`profile.aggregate_bandwidth_bytes_per_s` and reports
`modeled_completion_ns = max(raw_callback_ns, bandwidth_cursor_ns)`. The
aggregate bound cannot be disabled with zero because profile validation
requires a positive value, and its physical link identity is not encoded in
the profile.

An optional external base/link fabric must therefore start from the raw media
callback only when its data dependency requires media-ready bytes, and combine
its result as `final = max(existing_modeled_completion, fabric_done)`. It must
not add fabric duration to the already bounded reported completion. The
initial composite consumer must require raw callback time to equal the existing
reported completion for every composed request; otherwise it returns
`UNSUPPORTED_COMPOSITION`. This avoids assigning the unidentified generic
aggregate bound to the new package link. Actual NAND and native ONFI channel
timing remain enabled. No current profile switch disables those native
transfers, and this D5 adapter does not add one.

The real maintenance design and its approval boundary are recorded in
`docs/eq3_thermal/MQSIM_DIE_MAINTENANCE_NARROW_DESIGN.md`. Runtime maintenance
capability remains `UNSUPPORTED_CAPABILITY`.
