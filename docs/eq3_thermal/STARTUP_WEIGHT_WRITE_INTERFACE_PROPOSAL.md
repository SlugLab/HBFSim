# Additive startup weight-write interface proposal

Date: 2026-09-20
Status: design only; no implementation or experiment authorization inferred
Scope: isolated `experiments/eq3_maintenance` service only; default and the
active 66-point matrix remain unchanged.

## Evidence and conclusion

**USER_CONFIRMED:** a future experiment may consider real startup model-weight
writes and a larger weight model.  The current instruction is bounded design
work only.

**DOC_DERIVED:** the lower-level isolated engine already supports real
foreground writes.  `MqsimOnlineEngine::submit` maps
`RequestOperation::Write` to MQSim `UserRequestType::WRITE` in
`experiments/eq3_maintenance/backend/src/mqsim_online_maintenance.cpp`.  The
fixed maintenance backend test uses that path for a scheduled foreground write
and observes the mapping-generation race.  Thus no new NAND write simulator is
needed.

The missing capability is above the engine:

- `hbf_mqsim_maintenance_service.cpp` rejects any `submit` or `try_submit`
  operation other than `read` and always passes operation 0;
- `scripts/eval/mqsim_service.py::MqsimService` has no write-stage contract;
- `closed_loop.py::ClosedLoopCoordinator` accepts only HBF reads and hard-codes
  `operation="read"` at backend admission;
- the run starts observation at time zero, so there is no persistent
  `STARTUP_UPLOAD -> OBSERVATION -> DRAIN` lifecycle.

This is an experimental service/lifecycle capability gap, not a NAND backend
bug.

## Minimal interface

Keep all current behavior byte-for-byte when the new option is off.  Add an
explicit service option such as `--startup-writes on`, default `off`.  With the
option on, expose two additive JSON-lines commands:

```json
{"command":"startup_write","requests":[{"request_id":9000001,
  "stack":"hbf0","stack_local_page":0,"bytes":4096,
  "issue_ns":0,"operation":"write"}]}
{"command":"seal_startup"}
```

`startup_write` must reuse the existing stack map, placement ledger, request
identity, `MqsimOnlineEngine::submit`, completion, native-command observation,
and finish conservation.  It accepts exactly page-aligned writes and assigns
operation `RequestOperation::Write`.  It must not call the maintenance API,
reset age, fabricate payload hashes, or bypass the existing FTL/TSU/PHY.

`seal_startup` succeeds only after all accepted startup writes have returned.
It permanently closes the write command for that process.  Existing `submit`
and `try_submit` remain read-only, so a malformed observation request cannot
become a write.  The opt-in header should explicitly report
`startup_weight_write=ACTUAL_MQSIM_FOREGROUND_PROGRAM_METADATA_PAYLOAD_UNAVAILABLE`;
the default header remains the current `READ_ONLY_MEDIA_SERVICE_NOT_HARDWARE`
contract.

The Python client adds `startup_write(batch)`, uses ordinary `until` calls to
receive every completion, and adds `seal_startup()`.  It records phase, request
IDs, bytes, placement, native program commands, first/last completion, and
conservation in a separate startup receipt.  A unique numeric ID namespace is
shared across startup, reads, and maintenance.

## Ownership, time, and energy

One service process and one MQSim engine must own startup writes, later reads,
and maintenance.  A second process would lose the mappings and would not test
resident data.

The combined absolute timeline is:

`STARTUP_UPLOAD -> optional STARTUP_COOLDOWN -> OBSERVATION -> DRAIN`.

Observation arrivals and maintenance due/deadline values are shifted by the
sealed startup boundary.  The MQSim clock, thermal clock, energy ledger, and
fabric clock must never be reset.  If the scientific question requires an
ambient observation start, cooling is an explicit causal interval; temperature
must not be overwritten with 300 K after upload.

Startup writes do not use the GPU read-output fabric.  The missing host ingress
link remains `UNAVAILABLE` and is not assigned zero energy.  Actual native NAND
facts already flow through `ActivityEnergyLedger.native`: operation type 1 uses
`nand_media_w["1"]`, and command/data-in activity is assigned once to the
stack base.  Those coefficients remain `SCENARIO_ASSUMPTION`.  Program activity
must be labelled `MODEL_WEIGHT_UPLOAD`, either through an optional external-ID
source registry in the ledger or an equivalent phase map; it must not be
labelled refresh or maintenance.

Files/symbols in the smallest implementation are:

| File | Local change |
|---|---|
| `experiments/eq3_maintenance/backend/service/hbf_mqsim_maintenance_service.cpp` | default-off option, `startup_write`, `seal_startup`, operation 1, phase conservation |
| `experiments/eq3_maintenance/backend/client/maintenance_service.py` | opt-in client methods and startup receipt validation |
| `experiments/eq3_maintenance/energy_ledger.py` | optional request-ID source classification; no new energy coefficient |
| new `experiments/eq3_maintenance/startup_upload.py` | bounded batch producer/drainer and phase receipt |
| `experiments/eq3_maintenance/run_point.py` | only in a new campaign version: run upload before observation and bind the shifted absolute times |
| `experiments/eq3_maintenance/closed_loop.py` | accept a nonzero causal start boundary; normal HBF observation requests remain reads |
| fixed service/client/coordinator tests | default-off identity, write/read/maintenance ordering, mapping, energy source and conservation |

No production MQSim public ABI, default binary, thermal solver, fabric
arbitration, or maintenance algorithm needs to change.

## Required invariants and fixed tests

1. With the option off, write commands are rejected and existing read-only
   transcripts/behavior remain identical.
2. An opted-in 4 KiB startup write emits one foreground program lifecycle with
   actual stack/channel/die/plane facts and one completion.
3. A later read of the same logical page uses the same authoritative mapping;
   a later maintenance operation can commit exactly once.
4. `seal_startup` rejects pending writes, duplicate IDs, writes after sealing,
   wrong operation, out-of-range pages, and partial batches before mutation.
5. Startup NAND energy is derived only from native intervals, is assigned once,
   is separated from observation and maintenance, and leaves host-ingress
   energy `UNAVAILABLE`.
6. Absolute time is monotonic across all phases.  Cooling and thermal windows
   continue through the boundary; no state or temperature reset is allowed.
7. Process finish proves request/byte/phase conservation and zero pending work.

Freshly programmed pages are not retention-aged pages.  An immediate
post-upload maintenance check may validate the interface under an explicit
`ENGINEERING_POST_UPLOAD_MAINTENANCE` trigger, but it cannot support a
one-day-retention claim.  A retention experiment needs a declared dwell/age
model or an approved aged-state import mechanism.

## Capacity and bounded-pilot recommendation

The registered official Safetensors payload sizes imply:

| model | payload bytes | 4 KiB pages | pages/stack, four-stack stripe |
|---|---:|---:|---:|
| Qwen2.5-7B-Instruct | 15,231,233,024 | 3,718,563 | 929,640 or 929,641 |
| Qwen2.5-72B-Instruct | 145,412,407,296 | 35,501,076 | 8,875,269 |

These are logical payload extents.  MQSim stores no payload buffer (`Data` is
null) and exposes no payload hash, so even a full-page population run can claim
only logical mapping and native program-command coverage, not that actual model
tensor bytes were loaded or preserved.

Do not start with a full-model upload.  Use two bounded checks after the
interface/lifecycle version is explicitly approved:

1. **Backend interface check:** 64 pages, one maintenance target per current
   target identity.  Write, drain, seal, read, then submit maintenance.  This
   verifies the exact cause of the v3 unmapped failures.  Expected resources are
   dominated by the existing full-capacity engine initialization (the observed
   geometry probe used about 21.5 GiB RSS and 12.2 s); request artifacts should
   remain below 1 MiB.  Use the established 48 GiB address limit and 600 s
   watchdog.
2. **Integrated bounded load:** 16,384 pages (64 MiB), striped across all
   configured stacks/units in bounded batches, followed by reads and the
   maintenance check through the complete thermal loop.  At the observed
   transcript density this is roughly 50--55 MB of protocol evidence.  Stop if
   request conservation, mapping, native energy, or thermal-window causality
   fails.  This is an engineering load pilot, not a full-model-residency claim.

The ideal media-only lower bounds from 100 microseconds/program and 1,024
configured units are about 0.36 s for 7B and 3.47 s for 72B, but they exclude
all scheduling, transfer, JSON round trips, native observations, allocation,
and thermal work and must not be reported as expected runtime.  The current
protocol returns one completion per `until` call.  A full 7B upload therefore
requires at least 3.72 million completion exchanges and, using the existing
full-capacity probe's transcript density only as a storage estimate, roughly
12 GB of transcript; 72B requires 35.5 million exchanges and roughly 116 GB.
Neither is a reasonable 600 s pilot.  A future bulk-completion protocol would
be a separate interface change and must preserve individual command identities.

A larger model does not itself sustain a higher read load.  At fixed request
rate it changes address extent and coverage only.  Sustained activity requires
an independently declared request-rate/scan schedule and must be analyzed as a
new workload input, not inferred from parameter count.

## Approval boundary

The concept of startup writes is user-confirmed, but this concrete implementation
changes the experimental protocol, request lifecycle, phase timing, thermal
initial condition, and result schema.  Under the workspace refactor gate it
requires explicit approval of this interface/lifecycle version before edits.
The 64-page and 16,384-page runs also require their own saved preflight/manifest
under the experiment gate.  The active frozen 66-point campaign must remain on
its current binary and source hash.

## Smaller independent diagnostic alternative

The service/coordinator lifecycle extension above is not required to answer the
first engineering question: whether real foreground programs can populate the
same mappings later read and maintained.  An isolated fixed runner can use the
already public experimental engine API without changing any existing service,
request lifecycle, scheduler, ABI, or controller:

1. construct one `MqsimOnlineEngine` from the selected profile;
2. install the existing native command-observation sink;
3. map a bounded page list with the existing `MqsimStackMapAdapter`;
4. submit real `HbfRequest` writes in bounded batches and drain every
   completion before advancing to reads;
5. submit reads of the same logical pages and drain them;
6. optionally submit the existing one-page maintenance requests and drain them;
7. emit an immutable JSON receipt containing request/completion conservation,
   native program/read phases, physical placement, and maintenance terminal
   facts.

The existing `run_mqsim_trace` function already accepts historical trace
operation 0=write and 1=read, but it submits the whole trace then returns only
final completions.  It does not enable request observations, install the native
command sink, apply the explicit HBF stack map, emit energy facts, or retain a
maintenance follow-on.  The existing `hbf_concurrent_trace_timing` executable
does retain request arrival/admission/media-complete facts for both reads and
writes, but likewise lacks native physical command facts, explicit stack-map
verification, and maintenance.  It is useful as a zero-change A/B reference,
not as the complete startup-write evidence producer.

The smallest complete implementation is therefore a new executable under
`experiments/eq3_maintenance/backend/` linked to the existing isolated adapter.
It calls existing methods only and is absent from all default lookup paths.
Its output can be consumed by a standalone Python diagnostic that feeds native
events to the unchanged `ActivityEnergyLedger`, flushes fixed windows, and
advances the unchanged persistent thermal service.  This is explicitly
`BACKEND_FIXED_TRACE_PLUS_OPEN_LOOP_THERMAL_REPLAY`; temperature does not feed
back into write admission and it is not a main-controller integration result.

For the 64-page check, use the frozen OCP4K full-capacity profile and the exact
current maintenance target pages to maximize identity comparability.  For the
16,384-page heat/load check, either retain that profile or use a separately
declared finite namespace with the same 4 KiB, channel/die/plane and block
geometry; the latter lowers initialization cost but cannot inherit the
full-capacity identity claim.  Both runs retain the earlier 48 GiB/600 s
engineering bounds and require saved manifests.

This independent caller is a new, default-disconnected test tool rather than a
request/event-lifecycle refactor.  Given the explicit user authorization for a
basic startup-write load test, implementing the caller and its fixed tests does
not require an additional refactor decision.  Launching either numerical point
still requires its concrete preflight and resource/identity checks.  Integrating
startup upload into `MaintenanceMqsimService`, `ClosedLoopCoordinator`, policy
timing, or the main campaign remains the separate approval-gated proposal above.
