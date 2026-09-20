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
Every logical page has a 64-bit monotonic mapping generation in the isolated
page-mapping domain.  Every real mapping update increments it and fails closed
before mutation at overflow.  Maintenance captures both source PPA and
generation.  After the real program callback, commit requires both to match,
then increments the generation, updates the mapping, and invalidates the old
page.  A failed read leaves no destination.  Failed program or stale compare
invalidates the allocated destination and retains the old source.  An erase
failure happens after mapping commit, retains the new mapping, and reports
`FAILED_AFTER_COMMIT_NEEDS_RECONCILE`.

Destination pages come from the existing finite GC write frontier.  The source
block receives an explicit maintenance pin before the native read and holds it
until terminal cleanup or an atomic transition to erase ownership.  Existing
GC eligibility rejects pinned blocks.  The source block is erased only when all
written pages are invalid, it has no active
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

- `AB-PROCESS-GENERATION-02`: default backend A versus final generation/pin
  backend B with maintenance off.  Both the 8x1 24-request fixture and Q1
  4-channel x 16-die 12-request fixture matched request/status, media and
  reported completion time, native command order, and placement at zero-ns
  tolerance; both stderr logs were empty.
- `backend-generation-cpp-test-v1`: maintenance-off foreground completion
  equality; native read/program/generation-CAS/retire/erase; monotonic source
  and committed generations; a real future-arrival foreground MQSim write
  interleaved with maintenance and won exactly once while stale maintenance
  discarded its destination; source/new mapping readability;
  read, program, stale-CAS, and post-commit erase failures; source/destination
  readability; unique parent completion; real transaction IDs; 1 GiB finite
  workspace with one channel and all 16 dies, including die 15.
- `backend-generation-service-test-v2`: final generation backend process protocol, horizon
  advancement, explicit
  stack placement to channel 0/die 15, parent/native IDs, lifecycle, age commit,
  trigger, one-page coverage, deadline result, and finish conservation.
- `backend-generation-build-v1` plus the capability-only incremental
  `backend-generation-build-v2`: isolated source recreation and binary build
  passed with at most two compile threads.  Binary SHA-256 is
  `c64610b0f281397975e649b32f44161b00240f12d6a49b9b4d4776b1f554c257`.
- `BACKEND-INDEPENDENT-REBUILD01`: a fresh, separate build directory recreated
  all 179 patched source files byte-for-byte, linked exactly one isolated MQSim
  archive and no default archive, and completed configuration/build.  Its ELF
  hash differs only because absolute source/build paths change the build ID.
  `AB-PROCESS-INDEPENDENT-REBUILD01` then passed the same maintenance-off 8x1
  and 4x16 process comparisons at zero-ns tolerance.
- `Q1-WEIGHT-MAINT-PILOT03`: the legacy 16 KiB finite-region profile completed
  3,712 HBF foreground reads, 320 parametric-HBM reads and 64 committed
  maintenance jobs through the actual 10 s coordinator/thermal loop.
- `OCP4K-LOOP-PILOT01`: the 4 KiB/full-logical-capacity profile completed
  7,100 HBF reads, 160 parametric-HBM reads and 64 committed maintenance jobs
  through the actual 6 s loop.  All 7,260 foreground requests completed once;
  the engine finished with no foreground or maintenance work pending.
- `A2-PILOT03-LOOP01`: real foreground MQSim completed the frozen 4,032
  PILOT03 requests, while the counterfactual wrapper replayed the fixed 64-job
  maintenance bundle on ideal independent resources.  The actual backend
  issued and committed zero maintenance jobs and its current mapping was not
  mutated.  Ten foreground requests completed 620 ns earlier than the actual
  shared-maintenance PILOT03 baseline; completion count, censoring, P95
  latency, peak temperature and energy were unchanged.  This is executed
  counterfactual evidence, not an additional backend capability.
- The prior v11 PPA-token source and binary remain immutable under
  `points/BACKEND-V11-FROZEN`; they are historical evidence only.

## Geometry and capacity boundary

The two executed profiles are not interchangeable:

- The historical 16 KiB profile has one channel per HBF stack, 16 MQSim dies
  per channel, one plane per die, and a finite 1 GiB/stack allocator region.
- The OCP study profile has 4 KiB pages and instantiates four logical
  512 GiB stacks.  Each stack has 16 channels, one MQSim die per channel,
  16 planes per die, 256 pages/block and 2,048 blocks/plane.  The actual
  service therefore creates 1,024 parallel channel/plane units across four
  stacks.  The full-capacity probe covered all 1,024 configured
  `(stack, thermal-die, projected-plane)` tuples and completed real
  read/program/commit maintenance.

The OCP 16-bank value is represented as
`BANK_AS_MQSIM_PLANE_V1_SCENARIO_PROJECTION_NOT_PRODUCT_IDENTITY`.  A channel's
position within its configured stack supplies thermal `die0..die15`; the
native MQSim die field remains zero because there is one die per channel.
This projection is explicit configuration, not an observation that an OCP bank
is universally a NAND plane.

CWDP selects channel/die/plane from the backend logical page.  MQSim's
page-level FTL then allocates a physical block and page from its own write
frontier.  `GEOMETRY4K-PHYSICAL-BLOCK02` observed 256 serial first touches in
one plane occupy physical block 0 pages 0..255, followed by block 5 page 0;
block 5 was observed rather than prescribed because MQSim reserves other
frontiers.  This consumes the configured pages/block value, but it is not the
complete OCP zone/direct block-addressing protocol.

The 512 GiB/stack figure is a full *logical namespace* instantiation.  MQSim
has a finite allocator and spare destinations inside that instantiated
geometry, but neither OCP material nor this run establishes target-device
physical spare, bad-block reserve or overprovisioning.  The 256 pages/block
value and bank-to-plane projection remain scenario assumptions.

## Downstream identity and energy boundary

The service emits `maintenance_request_id` on real maintenance child
transactions.  The downstream `ActivityEnergyLedger._source` now consumes
that actual field first and retains `maintenance_id` only as a legacy
fallback.  Fixed source-classification tests pass, and the OCP4K loop records
320 maintenance activity rows as `HBF_MAINTENANCE`.

PILOT03 was produced before that downstream repair, so its 260 maintenance and
other backend-background rows remain immutably labelled
`BACKEND_BACKGROUND`.  The repair changes only source classification:
PILOT03's per-window component joules are unchanged, which is why its A3
full-2 mm paired replay remains comparable.  Energy coefficients are still
engineering assumptions.

Payload validation, die-wide refresh, HBM refresh, ECC/RBER, read-disturb,
wear-life prediction, zone-standard conformance, and cross-process checkpoint
restore are `UNAVAILABLE`.  Run artifacts and transcripts are isolated and
restartable, but engine state is not serialized; no checkpoint claim is made.
The JSON experiment service intentionally accepts foreground reads only because
the campaign workload is read-only weights; host write is not a campaign
feature.  The lower-level engine does support writes, and the fixed safety test
uses a real scheduled foreground write and real NAND program to validate stale
generation rejection.  The maintenance path does not reuse MQSim's GC LPA
barrier, whose upstream release path approximates waiting writes.  A source
block pin prevents GC erase/reuse, and the generation prevents PPA ABA from
being accepted as the captured logical version.
Neither successful logical-capacity instantiation nor the completed loops
validate product throughput, calibrated maintenance energy, physical spare, or
payload preservation.

The actual maintenance path and the A2 replay must remain distinct.  In
`Q1-WEIGHT-MAINT-PILOT03` and `OCP4K-LOOP-PILOT01`, the isolated engine
receives maintenance commands, schedules native reads and destination programs,
commits mapping generations, and returns `age_reset_ns` only after commit.
In `A2-PILOT03-LOOP01`, foreground still uses that real engine, but
maintenance is disabled at the service wrapper.  Fixed source phases, energy,
terminal facts and virtual age facts are replayed without native commands,
resource ownership or mapping mutation; their mapping/age semantics are
`UNKNOWN_REPLAY`.  A2 therefore measures one bounded ideal-resource
counterfactual and does not implement or validate an independent scheduler.
