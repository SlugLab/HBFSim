# Isolated geometry and thermal evidence

Status date: 2026-09-20. This is a receipt-backed engineering status report.
It does not create a new thermal model, rerun P2, open blind data, or promote a
conditional pilot into product validation. Evidence labels follow the project
registry: `SPECIFIED`, `DOC_DERIVED`, `SCENARIO_ASSUMPTION`, and
`CONDITIONAL_ENGINEERING` remain distinct.

## Current conclusion

The isolated chain now closes a real 4 KiB MQSim request and maintenance path,
full logical HBF capacity, native command/physical-address observation,
activity-energy accounting, and the persistent complete 2 mm thermal service.
Beyond the original mixed-direct pilot, a frozen v3 snapshot contains 48
completed points: 12 each for mixed-direct, relay, DASH, and 8HBF direct. A1
source-domain response, A2 ideal-maintenance replay, and A3 full-2 mm replay
have also completed.

These results establish interface and discrete-engine behavior. They do not
establish target-product NAND organization, calibrated power, ECC/retention
failure probability, token throughput, complete OCP zone/direct addressing,
live GPU behavior, P2 reference qualification, or `MODEL_FREEZE`.

## Page, plane, block, and capacity

The registered primary source is OCP *High Bandwidth Flash High-Level Base Die
Specification* v0.7.0, 3 August 2026,
`docs/HBF_OCP/ocp2026-hbf-architecture-specification-v0-7-0.pdf`, SHA-256
`307531eb8053f00cbeccbc907ddff0a9c4fe6f9d0066a077ce33b0ac99312da3`.

| Property | Source status | Executed representation and evidence |
|---|---|---|
| Page | OCP §4.1 specifies 4 KiB NAND pages; reads may not cross a 4 KiB page and Core writes use 4 KiB | Native 4096-byte requests are accepted. The stack map rejects a 16 KiB request under this profile and rejects the first page beyond the logical namespace. |
| Capacity | OCP Table 3 specifies 512 GiB and 16 dies/cube; §4.3 specifies 16 host channels | Four-stack probe instantiated 2 TiB logical capacity: 134,217,728 pages/stack, 64 channels total, one MQSim die/channel, and 16 thermal-die identities/stack. |
| Bank/plane | OCP specifies 16 banks/channel but does not define universal bank=plane identity | `BANK_AS_MQSIM_PLANE_V1` maps 16 banks to 16 MQSim planes/die as a `SCENARIO_ASSUMPTION`. It is not a product fact. |
| Block | OCP §5.7 leaves R3/pages per NAND block product-defined | `pages_per_block=256` is a visible `SCENARIO_ASSUMPTION`, producing 2048 simulated blocks/plane at full logical capacity. |
| Physical allocation | MQSim page-level FTL | CWDP selects channel/die/plane; the FTL dynamically selects physical block/page. Logical block-like ordinals must not be reported as physical addresses. |

`GEOMETRY4K-FULLCAP01` completed 1024/1024 real 4 KiB reads and covered all
`4 stack × 16 channel-derived thermal die × 16 projected plane` tuples. Four
maintenance operations completed native read, destination program, mapping
commit, source retirement, and final completion. Peak RSS was 21,511,976 KiB
and wall time 12.31 s.

`GEOMETRY4K-BOUNDARY01` retained a failed assertion caused by assuming that a
logical ordinal predetermined physical block/page. It is classified
`NOT_A_BACKEND_BUG_TEST_ASSUMPTION`. Its valid subchecks confirmed completion
of global logical page 536,870,911 and rejection of the first out-of-range
stack page.

`GEOMETRY4K-PHYSICAL-BLOCK02` then issued 257 serial first-touch pages to one
channel/die/plane. Physical block 0 received pages 0 through 255; allocation
257 entered observed block 5 at page 0. The new block number was not assumed,
because MQSim reserves other work-front blocks. This proves the 256-page block
parameter has a real allocator consumer. It does not implement or validate the
complete OCP zone/direct block-address protocol.

### Maintenance scale versus capacity

The completed OCP4K loop committed 64 one-page maintenance jobs. At 4 KiB/page
this is 262,144 B (256 KiB). The four 512 GiB stacks contain 536,870,912
addressable-equivalent 4 KiB pages, so the maintenance sample covers
`64 / 536,870,912 = 1.1920929e-7` of aggregate logical pages, or
`0.0000119209%`. With the observed equal 16-job distribution, the same fraction
holds per stack.

This is lifecycle and contention evidence, not full-capacity refresh coverage,
retention qualification, steady maintenance-rate calibration, or lifetime
evidence. Physical spare, bad blocks, overprovisioning, and product R3 remain
unknown. Compared with the old 16 KiB pilot, 64 jobs now cover 256 KiB rather
than 1 MiB; old/new maintenance volume is not byte-equivalent.

## Connected thermal evidence

The persistent thermal service uses the complete 2 mm, 64,512-node network,
255 package entities, 275 sensors, 20 ms windows, and one reused sparse
factorization. GPU, every HBF/HBM base and die, shared package materials, and
the common cooling boundary remain in one network. Activity energy is assigned
to components before each causal advance; there is no full-field output in the
service path.

The completed 4 KiB mixed-direct pilot `OCP4K-LOOP-PILOT01` is
`CONDITIONAL_ENGINEERING_COMPOSITION`:

- 7,260/7,260 requests completed: 7,100 HBF and 160 parametric HBM;
- 64/64 actual shared-engine maintenance jobs committed, with zero pending or
  censored requests;
- 300 windows over 6 s, total activity energy 800.0040984343195 J;
- peak temperature 387.61992164551265 K (114.4699 °C);
- wall time 83.05 s and maximum child RSS 21,510,776 KiB.

HBM remains a parameterized service, energy coefficients remain
`SCENARIO_ASSUMPTION`, and token/s is `UNAVAILABLE`. This pilot remains a
separate single-loop receipt; the partial four-topology campaign evidence is
reported below and is not backfilled into the pilot.

The earlier 16 KiB `Q1-WEIGHT-MAINT-PILOT03` remains historical engineering
evidence: 4,032/4,032 requests, 64 maintenance jobs, 500 windows/10 s,
1600.0025647932162 J, and peak 396.0463965549566 K. It is not OCP page-aligned
and is not retroactively relabeled.

## GPU guard source and observed margin

AMD's official AMD-SMI example for comparable MI300X/MI350X devices reports
100 °C hotspot slowdown and 110 °C shutdown. Those values are `PROXY`, not a
specification for a future HBF-equipped GPU. The current 90 °C guard is a
`SCENARIO_ASSUMPTION`: a 10 K early-warning margin. The modeled geometric GPU
hotspot is not a calibrated hardware sensor.

The OCP4K raw thermal ledger places its global peak and GPU hotspot at the same
387.61992164551265 K value at 4 s. Its 114.4699 °C peak is 4.4699 K above the
110 °C proxy. PILOT03's 122.8964 °C peak is 12.8964 K above it. Numerical completion inside the
300–400 K material domain is therefore not thermal-protection success. In
these points the memory gate cannot reduce the independent prescribed GPU heat
source. Read throttling-to-GPU-utilization/power feedback is not implemented,
and no such coefficient is inferred from these results.

The frozen completed-48 v3 snapshot uses the prescribed 200 W external GPU
source. Other queued/OAT and final-campaign points require their own receipts;
the completed 48 do not stand in for all 66. Power levels must not be confused
with the 100/110 °C guard proxy. The failed
`campaign-ocp4k-v2` startup followed a confirmed SLC serialization/input typo
before a completed scientific point. It is an engineering startup failure,
not a thermal-model failure and not four-topology evidence. The repaired
zero-work native startup receipt is
`campaign-ocp4k-v2/SLC-STARTUP-FIXED02/DONE.json`.

## A1, A2, and A3 coupling evidence

| Item | Actual result | Supported interpretation | Limit |
|---|---|---|---|
| A1 source-domain response | Nine source groups plus zero source, 500 windows and 125,500 rows. Entity-mean linear-superposition max error `8.6061e-11 K`; zero-source deviation `1.1084e-11 K`. HBF/HBM full peaks were about 335.18–335.40 K while their own-source peaks were about 300.0001–300.0008 K. | On the unchanged complete C/G network, cross-source package coupling dominates those memory-domain peaks for this prescribed input. | Source ablation retains passive paths; it is not edge deletion, a disconnected package, nodewise hotspot decomposition, or policy reclosure. Coefficients remain assumptions. |
| A2 ideal-maintenance replay | 4,032/4,032 foreground completions and 64/64 fixed replay jobs. Ten requests completed 620 ns earlier; completion count, censoring, P95 latency, peak temperature, and total energy were unchanged at stored precision. | Localized contention exists in the frozen legacy point. | Actual backend issued zero maintenance jobs in the replay; mappings were not mutated and age/commit facts are `UNKNOWN_REPLAY`. This is not an implemented independent-maintenance scheduler and is limited to legacy16K input. |
| A3 full-2 mm replay | 500 × 275 sensor comparisons; max sensor delta `4.7180e-12 K`, global-range delta `2.6148e-12 K`, cumulative-energy delta `4.4048e-11 J`; 32,320,512 node rows streamed. | Persistent service and old sparse runner are discretely equivalent for the immutable PILOT03 energy windows. | Same equation and Eigen family, 2 mm only: not an independent physical reference, P2 rerun, 1 mm qualification, or closed-control comparison. |

## Four-topology status on three independent axes

The frozen v3 completed-48 snapshot contains 12 points per topology. All 48
are `FUNCTIONAL_PASS`, have zero censored requests and conserved fabric
resources, and complete the declared coupled package trajectory. All remain
`CONDITIONAL_NOT_MODEL_PASS`. The remaining 18 points and final 66-point
aggregate are still pending receipts. Small interface fixtures remain separate.

| Topology | Configuration axis | Thermal axis | System-behavior axis |
|---|---|---|---|
| 8HBF direct + external GDDR | `COMPLETED48_CONFIG_EXECUTED`; 12 points, 8HBF full logical capacity, 2,048 actual native channel/die/plane tuples | `COMPLETED48_COUPLED_THERMAL_EXECUTED_CONDITIONAL`; GPU 343.714--387.600 K, HBF 307.786--328.344 K; GDDR is outside the package thermal domain | `COMPLETED48_FUNCTIONAL_PASS`; balanced eight-stack traffic, zero censoring/leak. External-GDDR service and temperature remain `UNAVAILABLE` |
| Mixed-direct | `COMPLETED48_CONFIG_EXECUTED`; 12 points, 4HBF full logical capacity + 4 parametric HBM, 1,024 actual HBF tuples | `COMPLETED48_COUPLED_THERMAL_EXECUTED_CONDITIONAL`; GPU 343.715--387.620 K, HBF 307.789--328.349 K, HBM 307.883--328.559 K | `COMPLETED48_FUNCTIONAL_PASS`; actual MQSim HBF plus parametric HBM, zero final backlog |
| 4+4 relay | `COMPLETED48_CONFIG_EXECUTED`; 12 paired-route points and 1,024 actual HBF tuples | `COMPLETED48_COUPLED_THERMAL_EXECUTED_CONDITIONAL`; full declared trajectories completed | `COMPLETED48_FUNCTIONAL_PASS`; actual MQSim HBF media plus external relay fabric, zero censoring/leak |
| Four-pair DASH | `COMPLETED48_CONFIG_EXECUTED`; 12 dual-route points and 1,024 actual HBF tuples | `COMPLETED48_COUPLED_THERMAL_EXECUTED_CONDITIONAL`; full declared trajectories completed | `COMPLETED48_FUNCTIONAL_PASS`; Stress representative exercised 3,552 HBF direct and 3,548 HBF relay completions |

Configuration, thermal, and system-behavior axes are independent. A pass in
one column does not promote either of the others. In these 48 points the fixed
200 W GPU source contributes 99.999473%--99.999887% of recorded activity
energy, while model-payload coverage is only 0.002985%--0.019999%. Scene
duration and fixed GPU heat therefore dominate the thermal result.

The geometry is OCP-oriented, but the performance model is not aligned to an
OCP target speed grade. Observed HBF active delivery is 0.758--3.454 MB/s; the
native configuration's nominal rate is 25.6 GB/s per stack, its whole-engine
cap is 512 GB/s, and the external fabric uses a 1.6 TB/s scenario target. The
1.536/3.072 TB/s product rates are not implemented. See
[READ_BANDWIDTH_ALIGNMENT_STATUS.md](READ_BANDWIDTH_ALIGNMENT_STATUS.md).

## Scientific state and evidence paths

P2 remains unfrozen. The old P2 spatial/reference qualification failures and
400.911 K domain evidence remain intact; `MODEL_FREEZE=false`. Blind inputs
remain sealed and were not read for this report. No new solver or reference run
was started.

Primary receipts:

- geometry and allocation:
  `eq3_thermal/plans/isolated-maintenance-campaign-v1/points/GEOMETRY4K-FULLCAP01`,
  `GEOMETRY4K-BOUNDARY01`, and `GEOMETRY4K-PHYSICAL-BLOCK02`;
- closed 4 KiB pilot: `points/OCP4K-LOOP-PILOT01`;
- historical loop: `points/Q1-WEIGHT-MAINT-PILOT03`;
- A1: `points/A1-DOMAIN-ANALYSIS01/result.json`;
- A2: `points/A2-PILOT03-LOOP01/POSTCHECK.json`;
- A3: `points/A3-PILOT03-REPLAY02/RUN_RECEIPT.json`;
- source audits: `docs/eq3_thermal/OCP_PAGE_CAPACITY_ALIGNMENT.md` and
  `docs/eq3_thermal/GPU_GUARD_SOURCE_AUDIT.md`;
- frozen partial v3 evidence:
  `campaign-ocp4k-v3/stage/COMPLETED48_INTERPRETATION.md`,
  `COMPLETED48_SNAPSHOT.json`, and `COMPLETED48_COMPACT.json`.

The v3 completed-48 snapshot is evidence only for its frozen IDs. The final 18
points and campaign-level conclusion wait for their receipts; v2 remains a
startup/input failure rather than a thermal-model failure.
