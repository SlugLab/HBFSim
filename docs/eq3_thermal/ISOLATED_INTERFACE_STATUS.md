# Isolated EQ3 interface status

Status date: 2026-09-20. This document distinguishes a connected producer and
consumer from a completed engineering run, and both from physical or scientific
validation. Parameters marked `SCENARIO_ASSUMPTION` are not device calibration.

## Interface, producer, consumer, and actual evidence

| Interface | Actual producer | Actual consumer | Enablement | Capability and executed evidence | Remaining gap |
|---|---|---|---|---|---|
| Campaign configuration and arrivals | `campaign_inputs.py` and point-local request/maintenance inputs | `run_point.py` -> `ClosedLoopCoordinator` | Explicit isolated point CLI; default unchanged | Legacy 16 KiB finite-region and OCP 4 KiB/full-logical-capacity profiles are explicit. PILOT03 and OCP4K loops completed | Conditional engineering inputs; no product throughput or token-rate claim |
| Stack/page placement | `MqsimStackMapAdapter`, explicit channel groups and persistent stack-local page map | Maintenance service and foreground `try_submit()` | `--stack-map` plus matching profile | Stable HBF stack identity comes from configured physical channel groups, never address guessing. OCP pilot used 4 KiB single-page requests and 16 channels per HBF stack | HBM placement is parametric; mixed HBM/KV placement, migration, adaptive placement and multi-page requests remain unsupported |
| Foreground MQSim admission/completion | `MaintenanceMqsimService`, inherited `try_submit()` and `until()` | `ClosedLoopCoordinator` | Experimental binary passed explicitly | One engine owns foreground and maintenance. Raw MQSim completion must equal reported completion before fabric composition. PILOT03 completed 3,712 HBF reads; OCP4K completed 7,100 | Arbitrary raw/reported overlap composition and production host/live-GPU consumers are not connected |
| Startup foreground program/read | Standalone startup_write_probe using existing isolated Write/Read API and rolling refill | Same engine/FTL/TSU/PHY, native observer and concurrency analyzer | Explicit separate caller only; main campaign unchanged | 16,384 writes and reads conserved, QD256 mean63.255/64 active die resources;64-page write/read/maintenance lifecycle passed | Engineering finite trace; no full model upload, payload-byte integrity or host-ingress energy claim |
| Native command observation | Experimental command observer | `ActivityEnergyLedger.native()` and native timeline writer | `--native-command-observations on`; default off | Real request -> transaction -> command IDs, phase, bytes and actual channel/die/plane/block/page are retained. Unknown stays `UNKNOWN` | Read/program/transfer power coefficients remain scenario assumptions |
| Native maintenance lifecycle | Isolated unit emits due/queue/read/program/commit/retire/erase/fail and native children | Service client, coordinator timeline and energy ledger | Explicit one-page jobs; isolated binary only | Same FTL, block manager, TSU and PHY as foreground. Generation CAS, source-block pin, GC pin rejection, finite destination allocation and real foreground-write interleaving pass. PILOT03 and OCP4K each issued and committed 64 actual shared-engine jobs | `METADATA_VERSION_VALIDITY` only; payload equality, die-wide/HBM refresh, ECC/RBER and checkpoint restore unavailable |
| Maintenance age | Committed maintenance completion supplies `age_reset_ns` | Coordinator page-age record and output | Only after one-page mapping commit | Age resets only after commit and covers exactly one page; initial age is input metadata in real seconds | Metadata-only bookkeeping; no retention/ECC/failure-probability model |
| Parametric HBM media | `tools/eq3_basic_hbm.py::BasicHbm` | Coordinator, energy ledger and fabric source-ready | Explicit HBM config | PILOT03 completed 320 HBM requests; OCP4K completed 160 on the common horizon | Not a DRAM backend; service die/plane and HBM maintenance unavailable |
| Two-bank package fabric | `tools/eq3_basic_fabric.py::BasicFabric` | Coordinator final delivery, energy ledger and resource probe | Explicit topology config; default off | Direct/relay/link events, bounded banks and release share the event horizon. The frozen v3 completed-48 snapshot exercises mixed-direct, relay, DASH direct+relay, and 8HBF direct without pending fabric ownership | Latency/bandwidth/energy are scenario assumptions; this is not product-speed alignment |
| Activity-to-energy | Native commands, maintenance IDs, HBM/fabric facts and prescribed GPU source | `ActivityEnergyLedger` | Explicit energy profile | Disjoint scopes are written per half-open window. `_source` now prioritizes actual `maintenance_request_id`, with legacy fallback; fixed tests pass. OCP4K raw has 320 `HBF_MAINTENANCE` rows | PILOT03 predates the repair and retains 260 maintenance/background rows as `BACKEND_BACKGROUND`; window totals and joules are unchanged. Coefficients remain assumptions |
| Energy-to-temperature | `ActivityEnergyLedger.flush()` | Persistent full-2 mm `ThermalService.advance()` | Explicit service and locked hashes | PILOT03 ran 500 windows/10 s and OCP4K 300 windows/6 s on the 64,512-node/275-sensor model | Engineering composition, not a new P2 freeze or product calibration |
| Thermal guard/rate policy | Thermal states plus completed-window resource/backlog facts | `ReadRatePolicy`, byte-token ledger and admission gate | Profile-controlled; module default off | In 16 completed three-policy groups, P0/P1 selected workload outcomes are equal. P2 preserves Stress delivery/backlog but adds 299.773--399.350 ms to p95 in this sparse input | The 48-point snapshot is partial and GPU-dominated; final 18 receipts, retry and UECC behavior remain outstanding |
| Resource observation | Native/energy busy intervals plus `BasicFabric.resource_state()` | Coordinator resource timeline and rate-policy window facts | Injected point-local probe; absent facts remain `None` | Both completed loops retained backend busy fractions and instantaneous bank/link ownership without adding scheduler events | Engineering observation adapter, not a public MQSim scheduler API or calibrated utilization model |
| Event horizon | Arrival, backend/fabric, maintenance and thermal-window times | `ClosedLoopCoordinator` via `run_point.py` | Explicit construction | Existing `until(horizon)`, no host sleep. All 48 frozen completed points drain with zero censoring and conserved fabric resources | Three refresh attempts exposed a late-maintenance scheduling bug and are excluded; fixed rerun receipts remain outstanding |
| Output contract | Coordinator and energy ledger | Point-local CSV/JSON and analysis tools | Per point | One time base; effective and physical bytes are separate. A frozen 48-ID snapshot and compact interpretation now exist | Final 66-point aggregation and the remaining 18 point receipts are not yet available |
| A2 ideal-independent replay | Frozen PILOT03 64-job bundle and `ideal_maintenance_replay.py` | Full coordinator loop with real foreground MQSim and wrapper-owned replay facts | `A2-PILOT03-LOOP01`; actual backend maintenance explicitly disabled | COMPLETED counterfactual: real foreground completed 4,032/4,032; wrapper replayed 64/64 fixed jobs, phases, energy and virtual age facts. Ten requests completed 620 ns earlier; P95 latency, completion count, peak temperature and total energy were unchanged | Not actual maintenance execution: backend issued/committed zero jobs, current mapping was not mutated, and replay mapping/age facts remain `UNKNOWN_REPLAY`. Result is limited to the frozen legacy16K point |
| A3 full-2 mm paired replay | PILOT03 `WINDOW_TOTAL` -> replay converter -> old sparse runner | Streaming reducer and immutable service `thermal.csv` | `A3-PILOT03-REPLAY02` only | PASS: 500x275 sensors; max sensor delta `4.7180e-12 K`, global-range delta `2.6148e-12 K`, cumulative-energy delta `4.4048e-11 J`; 32,320,512 node rows streamed | `FULL_2MM_DISCRETE_EQUIVALENCE` only; same equation/Eigen family is not independent physical reference and does not reclose control |
| A1 source-domain response | Nine source-group runs plus zero-source on unchanged C/G | `A1-DOMAIN-ANALYSIS01` | Point-local replay | COMPLETE: 500 windows, 125,500 rows; entity-mean superposition error `8.6061e-11 K`, zero-source deviation `1.1084e-11 K` | Source ablation, not a disconnected network or nodewise hotspot decomposition |
| Process/build isolation | Default, final isolated, and independent-rebuild services | Fixed process A/B harness | Maintenance off for A/B | Final generation/pin binary passed zero-ns A/B for 8x1 and 4x16. Fresh-path build used byte-identical patched sources and one isolated MQSim archive; its A/B also passed | Maintenance-off A/B does not prove maintenance-on performance/product correctness |
| External GDDR/live GPU | No service producer | Identity/limit reports only | Unavailable | 8HBF may retain physical external-GDDR identity outside package thermal scope | GDDR service/board temperature, token rate and live GPU remain `UNAVAILABLE` |

## Executed geometry profiles remain separate

PILOT03 is the historical 16 KiB finite-region result: four HBF stacks, one
channel per stack, 16 MQSim dies per channel and one plane per die. It completed
4,032 requests (3,712 HBF and 320 HBM), 64 maintenance jobs and the full 10 s
thermal loop. Its fixed workspace is not evidence for a 512 GiB stack.

OCP4K is separate. It instantiated four logical 512 GiB HBF stacks, 16 channels
per stack, one MQSim die per channel, 16 planes per die, 256 pages/block and
2,048 blocks/plane. It completed 7,260 requests (7,100 HBF and 160 HBM) and 64
maintenance jobs in the 6 s loop. Wall time was 83.05 s; child peak RSS was
21,510,776 KiB (about 20.52 GiB). Full *logical* capacity does not establish
target physical spare, bad-block reserve or overprovisioning.

The 16 OCP banks use `BANK_AS_MQSIM_PLANE_V1`, a one-to-one engineering
projection. CWDP selects channel/die/plane from logical address bits; MQSim's
page-level FTL separately allocates physical block/page. The physical-boundary
probe observed 256 first touches fill block 0 pages 0..255 and the next enter
block 5 page 0. This is not the complete OCP zone/direct block-address protocol,
and logical block/page ordinals cannot be reported as native physical ones.

## Four-topology status for the new geometry

The frozen v3 snapshot contains 48 completed points: 12 for each topology. It
covers all W1 Safe/Near/Stress policy triples and all W2 Stress policy triples.
This is a partial campaign result, not the final 66-point aggregation; the
remaining 18 points require their own completion receipts. Earlier
`basic-four-topology-actual` fixtures remain interface tests and are not mixed
into these counts.

| Topology | Configuration axis | Thermal axis | System-behavior axis |
|---|---|---|---|
| 8HBF direct + external GDDR | `COMPLETED48_CONFIG_EXECUTED`; 12 points, eight full-logical-capacity HBF stacks and 2,048 observed native channel/die/plane tuples | `COMPLETED48_COUPLED_THERMAL_EXECUTED_CONDITIONAL`; HBF package trajectory completed, while external GDDR remains outside the thermal domain | `COMPLETED48_FUNCTIONAL_PASS`; balanced eight-stack requests, zero censoring/leak. External-GDDR service and temperature remain `UNAVAILABLE` |
| Mixed-direct | `COMPLETED48_CONFIG_EXECUTED`; 12 points, 4HBF + 4 parametric HBM and 1,024 observed HBF tuples | `COMPLETED48_COUPLED_THERMAL_EXECUTED_CONDITIONAL`; full declared package trajectories completed | `COMPLETED48_FUNCTIONAL_PASS`; actual MQSim HBF plus parametric HBM, unique completion and zero final backlog |
| 4+4 relay | `COMPLETED48_CONFIG_EXECUTED`; 12 points with paired HBF-to-HBM package routes | `COMPLETED48_COUPLED_THERMAL_EXECUTED_CONDITIONAL` | `COMPLETED48_FUNCTIONAL_PASS`; actual HBF NAND path plus external relay fabric, zero censoring/leak |
| Four-pair DASH | `COMPLETED48_CONFIG_EXECUTED`; 12 points with both HBF direct and relay paths | `COMPLETED48_COUPLED_THERMAL_EXECUTED_CONDITIONAL` | `COMPLETED48_FUNCTIONAL_PASS`; Stress representative completed 3,552 HBF direct and 3,548 HBF relay requests |

Every completed point remains `CONDITIONAL_NOT_MODEL_PASS`; configuration,
thermal execution and system behavior are still separate axes. The fixed 200 W
GPU source contributes more than 99.9994% of recorded activity energy, and HBF
active delivery is only 0.758--3.454 MB/s. Geometry alignment therefore does
not establish target-speed or saturation behavior. See
[READ_BANDWIDTH_ALIGNMENT_STATUS.md](READ_BANDWIDTH_ALIGNMENT_STATUS.md).

## Evidence locations

- Generation/pin/write race: `points/backend-generation-cpp-test-v1` and
  `points/backend-generation-service-test-v2`.
- Final and independent A/B: `points/AB-PROCESS-GENERATION-02` and
  `points/AB-PROCESS-INDEPENDENT-REBUILD01`.
- 16 KiB loop: `points/Q1-WEIGHT-MAINT-PILOT03`.
- 4 KiB loop: `points/OCP4K-LOOP-PILOT01`.
- A3: `points/A3-PILOT03-REPLAY02/RUN_RECEIPT.json`.
- A1: `points/A1-DOMAIN-ANALYSIS01/result.json`.
- A2 ideal replay: `points/A2-PILOT03-LOOP01/POSTCHECK.json`; its actual
  shared-maintenance baseline is `points/Q1-WEIGHT-MAINT-PILOT03`.
- Source-label repair: `points/energy-fixed-v2/result.json`.
- Frozen partial v3 interpretation:
  `campaign-ocp4k-v3/stage/COMPLETED48_INTERPRETATION.md` and
  `COMPLETED48_COMPACT.json`.

All point paths are under
`eq3_thermal/plans/isolated-maintenance-campaign-v1/`. The repaired uppercase
`SLC` startup row and its zero-work receipt remain separate startup evidence.
The completed-48 snapshot promotes only the bounded axes shown above; final
campaign conclusions wait for the remaining 18 receipts and frozen aggregate.

## Backend isolation and capability evidence for the final report

The maintenance implementation is confined to
`experiments/eq3_maintenance/backend`: its own patch series, copied MQSim
source, headers, CMake graph, archives and executable. The default MQSim source
and build graph are not modified, and each tested process links one engine
archive. Final maintenance-off process A/B tests match default foreground
lifecycle, native order/placement and completion times at zero-ns tolerance;
an independent-path rebuild reproduced the patched source identity and passed
the same A/B cases.

Within that isolated engine, maintenance shares the real FTL, block manager,
TSU and PHY with foreground work. Fixed tests cover native read -> destination
program -> generation-CAS commit, source pinning, GC exclusion, finite
allocation, terminal cleanup/failure states and a scheduled foreground write
that makes stale maintenance lose exactly once. PILOT03 and OCP4K then show
64/64 actual shared-engine maintenance completions in complete CPU loops.
Capability remains `METADATA_VERSION_VALIDITY`: there is no payload equality,
ECC/RBER, target physical-spare, HBM refresh or checkpoint claim.

A2 is a separate counterfactual. Its foreground requests run on real MQSim, but
its 64 maintenance jobs are fixed replay facts on ideal independent resources;
the current engine issues no maintenance and mutates no mapping. The observed
620 ns improvement for ten requests is therefore evidence of localized shared
resource contention in the frozen legacy16K input, not evidence that an
independent maintenance scheduler has been implemented.
