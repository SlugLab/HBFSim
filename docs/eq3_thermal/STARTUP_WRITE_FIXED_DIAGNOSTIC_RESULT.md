# Startup write fixed diagnostic result

Date: 2026-09-20
Scope: authorized default-disconnected MQSim caller; no service, coordinator,
scheduler, ABI, thermal solver, or frozen campaign consumer changed.

## Implemented path

`experiments/eq3_maintenance/startup/startup_write_probe.cpp` uses the existing
isolated `MqsimOnlineEngine` APIs to submit bounded foreground write windows,
drain them, read the same logical pages, and optionally exercise maintenance.
It installs the existing native command observer and retains exact request,
stack, channel, die, plane, block, page, phase and completion identities.

The caller emits chronological `native-events.jsonl` for the unchanged
`ActivityEnergyLedger`, equivalent CSV, request observations, maintenance
facts, phase boundaries, conservation summary and create-only `DONE.json`.
Request IDs partition write, verification read and software-lifecycle
maintenance.  No host write is renamed refresh.  Model payload bytes and host
ingress energy remain `UNAVAILABLE`.

## Fixed lifecycle check

`STARTUP-WRITE-FIXED64-01` passed on the 128 MiB four-stack engineering fixture:

- 64/64 foreground programs uniquely completed;
- 64/64 same-page reads uniquely completed;
- all 64 written physical pages were distinct;
- 64/64 maintenance operations committed after the pages were mapped;
- 384 request observations and 1,024 native command-event records were
  conserved;
- peak RSS was 7,588 KiB and internal wall time was 0.0045 s.

The maintenance trigger is explicitly
`SOFTWARE_LIFECYCLE_TEST_ON_FRESHLY_PROGRAMMED_PAGES_NOT_RETENTION_DUE`.
Fresh age begins at each program completion; this result does not model a
24-hour-aged weight page.

## Actual concurrency comparison

The preserved initial paired points use identical 16 GiB finite logical geometry: four stacks,
16 channels/stack, one native MQSim die/channel, 16 planes/die, 16 physical
blocks/plane, 4 KiB pages, unchanged queue depth 256 and unchanged MQSim
TSU/PHY arbitration.  Both issue 16,384 programs then 16,384 same-page reads.

| Result | window 1 | window 256 |
|---|---:|---:|
| Completed write/read operations | 16,384 / 16,384 | 16,384 / 16,384 |
| Peak device outstanding | 1 | 256 |
| Program operations / native commands | 16,384 / 16,384 | 16,384 / 8,192 |
| Maximum overlapping program commands | 1 | 64 |
| Maximum active channel/chip/die resources | 1 | 64 |
| Maximum active channel/chip/die/plane resources | 1 | 192 |
| Distinct program physical resources | 1,024 | 1,024 |
| Simulated write interval | 1,644,314,624 ns | 12,950,656 ns |
| Achieved simulated program rate | 40.81 MB/s | 5.182 GB/s |
| Process wall time | 5.940 s | 5.826 s |
| Peak RSS | 252,168 KiB | 251,100 KiB |
| Retained raw evidence | 59,223,621 B | 53,934,814 B |

The 127-fold simulated-rate change verifies that the bounded window reaches
real parallel resource arbitration.  It is not produced by removing a lock or
changing TSU policy.  Program *operations* count unique native transaction IDs;
program *commands* count command IDs, which may group transactions.  Media
overlap uses half-open `MEDIA_BEGIN..MEDIA_END` intervals, with endings before
starts at an equal timestamp.

The initial window-256 producer used a 256-request batch barrier.  The final
standalone caller now implements `BOUNDED_ROLLING_REFILL_ON_EACH_COMPLETION`
with an explicit pending-ID to record-index map.  A new 16,384-page rolling
point preserved all operation counts and produced the same simulated write
interval, peak 64 program commands, and physical-resource utilization as the
batch result.  This uniform workload completes the last command of each batch
at the same timestamp at which the next batch was admitted, so the older batch
barrier happened not to introduce media-idle gaps.  The native event ordering
and hashes differ, and the rolling implementation now satisfies the producer
lifecycle independently of this workload-specific equality.

Across the complete rolling write interval, including initial fill and final
drain, the time-weighted mean was 63.255 active channel/chip/die resources out
of 64 (98.837%).  Mean active program commands were also 63.255, peak device
outstanding was 256, and mean active channel/chip/die/plane resources were
126.511.  The older batch-window point has the same utilization values.  A
64-page rolling-window-8 check separately reached 8 outstanding requests, 8
overlapping program commands, and a time-weighted mean of 7.971 active die
resources, confirming actual refill and final-drain accounting on a small case.

The caller also accepts `--verify-pages`.  It defaults to all loaded pages; a
smaller value selects evenly stratified page indices including the first and
last loaded page and records loaded versus verified counts.  This changes only
read verification coverage, never the number of actual program operations.

The initial `STARTUP-WRITE-CONCURRENCY-W1-01` is preserved as `INPUT_FAILURE`:
its eight-blocks/plane profile violated the existing MQSim write-fixture safety
contract requiring more than ten blocks/plane.  The paired `-02` points use 16
blocks/plane; no scheduler or solver was changed.  The unstarted paired eight-
block point is marked `NOT_STARTED`.

## Measured cost and model-size limit

A same-geometry 64-page rolling baseline used 214,160 KiB RSS, 214,881 B
raw evidence and 0.164 s.  Comparing it with the 16,384-page rolling result
gives an observed aggregate growth of approximately:

- 2.224 KiB RSS per loaded-and-verified page;
- 3,293 raw-evidence bytes per page;
- 0.303 ms wall time per page.

This aggregate includes caller request/placement records, retained native
events, emitted CSV plus JSONL, and backend mapping state.  It is not attributed
entirely to MQSim history.  Window 1 and 256 have nearly identical RSS at the
same request count, so outstanding queue occupancy is not the main retained-
memory term.

Straight-line diagnostic projections from these two sizes are only capacity
planning estimates:

| metadata extent | 4 KiB pages | projected RSS | projected raw | projected wall | idealized simulated write interval |
|---|---:|---:|---:|---:|---:|
| Qwen2.5-7B, 15,231,233,024 B | 3,718,563 | 8.09 GiB | 12.25 GB | 1,126 s | 2.94 s |
| Qwen2.5-72B, 145,412,407,296 B | 35,501,076 | 75.5 GiB | 116.9 GB | 10,749 s | 28.1 s |
| registered 235B extent, 470,187,269,120 B | 114,791,814 | 243.6 GiB | 378.1 GB | 34,757 s | 90.7 s |

The idealized simulated intervals extrapolate only the observed window-256
media schedule.  Wall/RSS/raw projections assume current all-page verification
and duplicate CSV/JSON evidence and may change at larger allocator occupancy.
They are not experiment results.

A complete 7B program diagnostic may be technically plausible with a finite
namespace that retains adequate spare blocks and a 48 GiB process budget, but
the current all-page verification/output contract projects beyond 600 s and
about 12 GB raw.  It must not be launched without a separate preflight.  A
reviewed bounded variant could program all 3,718,563 pages while verifying a
declared stratified sample (for example 1,024 pages across the loaded range),
streaming sufficient native facts instead of retaining duplicate formats.  It
could claim all observed native program operations and only the explicit read
sample.  It still could not claim real tensor payload transfer or integrity.

The 72B and 235B full-write cases are not reasonable with the present retained
evidence path.  They require a separately reviewed streaming-evidence design;
no core NAND, scheduler, or service lifecycle redesign is indicated by these
results.

## Native-fact thermal replay

`STARTUP-WRITE-ROLLING-N16384-THERMAL01` replayed the immutable rolling-16K
native JSONL through the unchanged `ActivityEnergyLedger` and the existing
255-entity mixed 2 mm RC package.  It did not invoke MQSim or the live
controller.  The model therefore retains all HBF/HBM/GPU components and their
shared cooling paths while applying activity only to the observed hbf0--hbf3
native placements.  GPU incremental power was explicitly zero for this
source-only upload diagnostic; zero does not represent measured idle power.

The replay completed 11 fixed 20 ms thermal windows: one source window and ten
source-free recovery windows.  The observed source energy was 0.04523761664 J:

- startup programs: 0.04105641984 J;
- verification reads: 0.00418119680 J;
- NAND media: 0.04506419200 J;
- command/address/data-in: 0.00017342464 J.

All source rows are foreground.  No maintenance was requested.  In this
frozen trace each `NAND_DATA_OUT` begin and end has the same timestamp, so the
unchanged ledger assigns it zero energy.  The replay does not invent a transfer
duration.  Host ingress, fabric upload, standby/idle energy and tensor payload
integrity remain unavailable.

Energy reconciled independently across activity rows, native source, scope,
write/read stage, 20 ms thermal windows, and the thermal service cumulative
input.  The largest absolute split discrepancy was 2.32e-14 J; thermal-service
input differed from the ledger by 6.94e-18 J.  At 220 ms the service reported
0.04523761664 J input, 0.00358905189 J boundary loss,
0.04164856475 J stored-energy change and 1.54e-13 J residual (maximum relative
residual 3.40e-12).

The hottest HBF endpoint was 300.023686 K at the first 20 ms boundary.  After
200 ms without a source, the hottest HBF was 300.011052 K.  Heat continued to
diffuse through the coupled package: the GPU reached 300.003098 K and the
hottest HBM reached 300.000672 K at 220 ms.  This is a recovery observation,
not a claim that the package returned to its 300 K initial state.

The native program interval ends at 12.950656 ms and all native activity ends
at 14.353536 ms.  Both lie inside the first 20 ms thermal window.  The reported
300.023686 K value is therefore the 20 ms endpoint; the approximately 13 ms
transient peak is unresolved and must not be inferred from this replay.

The point used one CPU at 99%, 345,132 KiB peak RSS, 12.11 s wall time, no GPU,
no swap, a 4 GiB address limit and a 600 s watchdog.  Its result remains
`CONDITIONAL_ENGINEERING_OPEN_LOOP_THERMAL_DIAGNOSTIC`: it is not a full 7B,
72B or 235B upload, controller reclosure, P2 qualification, or ModelFreeze.

## Evidence locations

- `eq3_thermal/plans/isolated-maintenance-campaign-v1/points/STARTUP-WRITE-FIXED64-01`
- `.../STARTUP-WRITE-CONCURRENCY-W1-02`
- `.../STARTUP-WRITE-CONCURRENCY-W256-02`
- `.../STARTUP-WRITE-CONCURRENCY-W256-N64-01`
- `.../STARTUP-WRITE-ROLLING-W8-N64-01`
- `.../STARTUP-WRITE-ROLLING-W256-N16384-01`
- `.../STARTUP-WRITE-ROLLING-N16384-THERMAL01`

The window-256 16,384-page `summary.json` SHA-256 is
`332b99a3f1cb7e9549038502e17970edaa32d756e9c8df6c11e953863cdfee30`;
its chronological `native-events.jsonl` SHA-256 is
`ac6ddd94c6ab83428a8d11d119e5a48089fc4a47dcb0884551b4f60bcd2fa09f`.
The fixed analyzer test distinguishes operations, commands and overlapping
physical resources without invoking MQSim.
