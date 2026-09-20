# Isolated MQSim maintenance backend

## Evidence classification and scope

- **USER_CONFIRMED:** the isolated experimental fork may add the narrow
  maintenance source, queue visibility, native read/program/erase path,
  destination allocation, version commit, and failure cleanup described by
  `EQ3-ISOLATED-MAINTENANCE-CAMPAIGN-v1`.
- **DOC_DERIVED:** the source base is MQSim
  `51f0f2d3fed92d88ef4a0fa61a38024b07bf9d16` followed by the registered
  HBFSim patches 0001--0003 and local patch 0004.  Exact hashes are in
  `patches/series.json`.
- **SCENARIO_ASSUMPTION:** this is
  `EXPERIMENTAL_OUT_OF_PLACE_PAGE_MAINTENANCE`.  It is not asserted to be a
  universal HBF product/OCP refresh algorithm.
- **Capability:** `METADATA_VERSION_VALIDITY`.  MQSim has no payload buffer or
  payload hash here, so byte equality is `UNAVAILABLE` and is never inferred.

No tracked default file under `third_party/mqsim`, `patches/mqsim`, `include`,
`src`, or the default CMake graph is changed.  The executable is not on a
default lookup path and there is no fallback from the production service.

## Ownership and state transition

One `MqsimOnlineEngine` owns foreground requests and the maintenance unit.  The
unit receives the same FTL address mapper, block manager, TSU, and ONFI PHY as
foreground work.  Its explicit source identity is `HBF_MAINTENANCE`; the
existing TSU policies place it in the existing GC/WL queue class without
changing the scheduler or foreground priority algorithm.  Every native child
transaction retains both maintenance request ID and parent ID.

The successful path is:

`DUE -> QUEUED -> READ -> PROGRAM_DEST -> COMMIT -> RETIRE_OLD -> [RECLAIM_ERASE] -> DONE`

The old mapping remains authoritative through read and destination program.
After the real program callback, commit compares the current source PPA with
the captured source PPA.  Only a match updates the mapping and invalidates the
old page.  This is a PPA compare token, not a monotonic mapping generation;
PPA ABA exclusion is not established.  A failed read leaves no destination.
Failed program or injected stale compare
invalidates the allocated destination and retains the old source.  An erase
failure happens after mapping commit, retains the new mapping, and reports
`FAILED_AFTER_COMMIT_NEEDS_RECONCILE`.

Destination pages come from the existing finite GC write frontier.  The source
block is erased only when all written pages are invalid, it has no active
read/program/erase or GC reference, and it is not a data/GC/translation write
frontier.  A requested erase that is not safe reports
`COMMITTED_RECLAIM_DEFERRED`; it is not treated as free capacity.  Existing
MQSim GC remains responsible for general partially valid-block reclamation.

The isolated patch also closes MQSim online-HBF first-read bookkeeping.  That
path creates metadata for an already populated page by using the data allocator
but has no program command that could clear the allocator's synthetic
in-flight-program count.  Patch 0004 calls the existing program-serviced
bookkeeping hook immediately after that metadata allocation.  It adds no NAND
command, time, or energy and is limited to the isolated fork.  Without this
repair safe erase was always deferred; the preserved failure is
`backend-cpp-test-v5` and the passing repair evidence is
`backend-cpp-test-v6`/`v7`.

## Service contract

The executable preserves `submit`, `try_submit`, `until`, and `finish`.
`maintain` accepts one page:

```json
{
  "command": "maintain",
  "request_id": 101,
  "parent_id": 7001,
  "stack": "hbf0",
  "stack_local_page": 15,
  "due_ns": 120000,
  "deadline_ns": 1120000,
  "trigger_reason": "FIXED_RETENTION_DUE",
  "reclaim_source_block": false,
  "failure_injection": "none"
}
```

An explicit direct-HBF stack map is mandatory.  The persistent stack-local page
mapping supplies the backend LPA; CWDP then supplies exact channel/die/plane.
The service does not guess a stack from an address.  `maintain` only schedules
the due event.  The caller must advance the same engine with `until(horizon)`;
there is no host sleep, inner retry loop, callback re-entry, or second engine.
`until` continues to return only foreground completions.  Maintenance facts are
separate `maintenance_events` and `maintenance_completions` arrays on every
response.

Completion contains status, enqueue/start/end, source and committed versions,
commit/source-retire/erase flags, native transaction IDs, exact stack/local
page, trigger, one-page coverage, deadline result, and `age_reset_ns`.  The last
field is non-null only after mapping commit and covers exactly that page.
`deadline_ns=0` means no deadline.  Fault injection is an engineering test
facility (`none/read/program/stale_commit/erase`), never a measured device
failure model.

The dedicated client is
`client/maintenance_service.py::MaintenanceMqsimService`.  It accepts either
`maintain(job_dict)` or equivalent keyword arguments.  It retains cumulative
`maintenance_events` and `maintenance_completions[id]`; `until` still returns
only a foreground completion.  `finish` enforces zero pending maintenance and
one terminal completion per accepted maintenance ID.

## Verified and unavailable behavior

Fixed CPU evidence:

- `backend-cpp-test-v7`: maintenance-off foreground completion equality;
  native read/program/PPA-compare/retire/erase; distinct source/committed PPA;
  read, program, stale-CAS, and post-commit erase failures; source/destination
  readability; unique parent completion; real transaction IDs; 1 GiB finite
  workspace with one channel and all 16 dies, including die 15.
- `backend-service-test-v2`: frozen build-v11 process protocol, horizon
  advancement, explicit
  stack placement to channel 0/die 15, parent/native IDs, lifecycle, age commit,
  trigger, one-page coverage, deadline result, and finish conservation.
- `backend-build-v11`: isolated source recreation and binary build passed with
  at most two compile threads.  Binary SHA-256 is
  `cf2260d48e00d7b9f3fb81405223f593d4ee4cce44033c66f383a546d10ad7f6`.

Payload validation, die-wide refresh, HBM refresh, ECC/RBER, read-disturb,
wear-life prediction, zone-standard conformance, and cross-process checkpoint
restore are `UNAVAILABLE`.  Run artifacts and transcripts are isolated and
restartable, but engine state is not serialized; no checkpoint claim is made.
The experimental service accepts foreground reads only.  A genuine concurrent
host write is not exposed or tested.  The current maintenance lock uses MQSim's
GC LPA barrier, whose upstream release path treats waiting writes as serviced
without a real program; therefore injected `stale_commit` is test plumbing, not
evidence of real concurrent-write CAS.  The source block also has no explicit
maintenance pin after its read completes.  General mapping changes are caught
by PPA comparison, but PPA reuse/ABA and a GC race are `UNVALIDATED`.  A narrow
follow-up design is a per-LPA monotonic generation plus source-block maintenance
pin and a real interleaved foreground-write test.
The fixed 1 GiB/stack workspace and 16-die mapping are engineering geometry,
not validation of 512 GiB/stack capacity or product throughput.
