# EQ3 four-topology v2 status

Status date: 2026-09-20. This table separates configuration, thermal, and system
behavior evidence. A pass on one axis does not promote either other axis.

## Evidence receipts

- `native-d5-fixed`: retained `FAILED`. Four C++ tests passed; the service and
  client failures were test-only (an incorrect error-string assertion and a
  missing explicit isolated artifact root, followed by an unbound test local).
  The backend had correctly rejected the overlapping channel map.
- `native-d5-fixed-v2`: `PASS`, 10 service plus 3 client tests, 0 failures.
- `basic-components-fixed`: `PASS`, 30 tests total: 7 parameterized-HBM,
  11 fabric, 6 BasicSystem, and 6 spatial-diagnostic checks.
- `basic-four-topology-actual`: `PASS`, one fresh actual MQSim service process
  per topology, with retained source/derived profiles and maps, transcripts,
  native command observations, results, and summary.

All receipts are under
`eq3_thermal/plans/decision-execution-v2/points/<receipt>/` in the outer
workspace. They are fixed CPU engineering evidence, not a research experiment.

## Three independent axes

| Topology | Configuration coverage | Thermal-model validation | System-topology behavior |
|---|---|---|---|
| 8HBF direct + external physical GDDR | `ENGINEERING_FIXTURE_PASS`: derived 8-channel profile and eight explicit one-channel/one-die HBF groups; research capacity/geometry incomplete | `NOT_VALIDATED`; BasicSystem fabric has no thermal consumer; GDDR temperature `UNAVAILABLE` by scope | `BASIC_CPU_HBF_PATH_PASS`: actual MQSim 24/24 requests, 3 per HBF, bounded-bank waits and zero final owners. External GDDR identity retained, but GDDR service `UNAVAILABLE`; production host/live GPU not connected |
| 4HBF + 4HBM mixed-direct | `ENGINEERING_FIXTURE_PASS`: derived 4-channel/four-group HBF map; HBF `pair=null`, no relay link; explicit four parameterized HBM stacks | `NOT_VALIDATED`; no HBM/fabric activity-to-power or package thermal coupling | `BASIC_CPU_FIXTURE_PASS`: 24/24 total, 3 per stack; HBF uses actual MQSim, HBM is `PARAMETRIC_HBM_SCENARIO`; all banks/links released. Real DRAM, production host/live GPU not connected |
| 4+4 relay | `ENGINEERING_FIXTURE_PASS`: four explicit one-to-one pairs and topology-matched four-group HBF map; research profile incomplete | `NOT_VALIDATED`; relay/base energy and hotspot mapping are UNKNOWN/unconnected | `BASIC_CPU_FIXTURE_PASS`: 24/24 total, HBF relay is sequential pair-private relay then shared paired-HBM GPU link; HBM-local and relay share bounded HBM banks/link; zero final owners. Timings are scenario assumptions |
| Four-pair DASH | `ENGINEERING_FIXTURE_PASS`: four explicit pairs, direct/relay selection, topology-matched four-group HBF map; research profile incomplete | `NOT_VALIDATED`; dual-path source/base/PHY power and hotspots unvalidated | `BASIC_CPU_FIXTURE_PASS`: 28/28 total; 4 alternating direct/relay requests per HBF plus 3 local requests per HBM; four-pair mapping, backpressure, unique completion and zero final owners pass. No calibrated policy, production host, or live GPU |

## Shared limits

- HBF media commands and physical channel/die/plane facts come from actual
  MQSim. The package stack identity comes from the explicit channel-group map,
  not an address guess.
- HBM is a parameterized FIFO timing scenario and is not a real DRAM backend.
  Refresh remains `UNSUPPORTED_CAPABILITY`; die/plane remain `UNKNOWN`.
- Base banks and package links are `SCENARIO_ASSUMPTION`. Unknown operation/link
  energy remains `UNKNOWN`, never zero-filled.
- BasicSystem records external arrival/wait, backend completion, fabric
  completion, and final completion separately. It rejects composition when raw
  MQSim completion differs from its generic bandwidth-bounded reported time.
- The optional chain is default off. The production host service, calibrated
  12.8–24.5 TB/s/capacity claims, thermal solver coupling, and live GPU path are
  not validated by these receipts.
