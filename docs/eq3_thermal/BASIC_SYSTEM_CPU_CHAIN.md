# Basic four-topology CPU chain

Status: `USER_AUTHORIZED_ENGINEERING_FIXTURE`, default off. This is a small
composition consumer, not a production daemon, research geometry, calibrated
performance model, or thermal closure.

`tools/eq3_basic_system.py::BasicSystem` connects the existing
`MqsimService`, `BasicHbm`, and `BasicFabric` APIs. It owns no backend command
or completion. Before submitting either backend it reserves one of the two
source base banks. A failed reservation leaves the request in the external
waiting list. The eventual backend submission timestamp and external wait are
recorded separately from the original arrival.

HBF requests use persistent `stack` plus `stack_local_page` placement. The
MQSim request always carries backend `route=direct`, because that field selects
native HBF media placement. The requested package route remains in the system
record and is consumed only by the fabric. HBM local work reserves an HBM base
bank before entering the parameterized HBM media server. That bank pool is the
same pool used by the paired relay receive path.

After reserving its HBF source bank, the coordinator uses the existing
nonblocking `try_submit` gate. A `Defer` result keeps that bounded reservation,
adds the target to the event horizon, and retries the same backend ID once the
target is reached. The gate owns no request and the backend receives it once.
Blocked or unsupported decisions stop this fixture as
`UNSUPPORTED_COMPOSITION`; they are not converted into fabricated service.

The coordinator advances through `MqsimService.until(horizon)`. Its horizon is
the earliest known external arrival, HBM event, or fabric event. When only an
unknown-time MQSim completion remains, it supplies the maximum horizon and the
existing service returns the earliest actual completion without advancing past
it. At a returned timestamp the coordinator publishes backend completions,
advances the fabric, consumes package completions, and then retries external
waiters. There is no host sleep, background thread, callback re-entry, or
second completion lifecycle.

For HBF, the observed raw MQSim media callback must equal the unchanged
reported completion. A difference means the generic aggregate-bandwidth bound
is active, so the coordinator returns `UNSUPPORTED_COMPOSITION` rather than
counting an unidentified transfer segment again. On equality,
`mark_source_ready()` starts the package fabric from that timestamp and final
delivery is `max(backend_reported, fabric_completion)`.

The topology policy is explicit:

| Mode | Legal paths |
|---|---|
| `all_hbf_direct` | HBF direct only; physical external GDDR identity is retained, while GDDR service and temperature are `UNAVAILABLE` |
| `mixed_direct` | configured HBF direct and HBM direct |
| `relay` | HBF relay through its paired HBM base plus HBM local direct; an HBF direct GPU path is rejected |
| `dash` | each configured HBF may select direct or paired relay; HBM local remains direct |

The fixed tests use eight HBF stacks for all-HBF, and four HBF/four HBM stacks
for the other modes. Every configured source stack receives at least three
requests (DASH alternates four HBF direct/relay requests), so the two-bank
limit produces real external backpressure. They check per-stack counts, unique final completion,
backend/package route separation, finite-bank waiting, shared HBM resources,
and a final read-only ownership snapshot with no bank or link owner. These are
software fixtures. Capacity and the 12.8–24.5 TB/s target remain unvalidated;
HBM is `PARAMETRIC_HBM_SCENARIO`, missing energy remains `UNKNOWN`, and the new
fabric is not connected to the thermal model. The existing separate thermal
gate fixture remains the only basic CPU thermal-control chain.

`tools/eq3_basic_system_actual.py` is the explicit actual-service runner. Given
an isolated `hbf_mqsim_service` binary, a base small profile and its explicit
eight-stack map, it creates a new artifact directory. Per case it preserves
byte-for-byte source copies and hashes, then derives a matching profile/map:
eight one-channel/one-die HBF groups for all-HBF and four such groups for the
three 4+4 modes. It runs one fresh MQSim process for each fixed topology,
preserves every service transcript/native event, and writes a case result plus
a four-case summary. It stops on the first failed composition
and retains that failure record; it does not turn the cases into a research
matrix or overwrite an existing artifact directory.
