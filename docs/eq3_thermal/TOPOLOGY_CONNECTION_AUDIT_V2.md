# EQ3 topology connection audit v2

Status: updated from the original read-only audit after the authorized basic CPU
implementation, 2026-09-20. The original `CpuService` findings remain below;
the additive `BasicSystem` evidence is reported separately and does not rewrite
old results. Connection semantics are `USER_CONFIRMED`; code statements and
retained receipts are `DOC_DERIVED`. Physical interpretation beyond them is a
gap.

## Result

| Requested topology meaning | Current implementation | Verdict |
| --- | --- | --- |
| Eight standalone HBF stacks, each with a base-die SRAM buffer | Legacy `CpuService` still has only a generic base lock. The optional BasicSystem instead configures two bounded banks per HBF, reserves before MQSim submission, holds external work on bank exhaustion, and releases all owners after package delivery. The actual small fixture completed 24/24 HBF requests across eight stacks. | **BASIC CPU FIXTURE MATCH for bounded two-bank ownership and backpressure; capacity/energy are scenario inputs, not validated SRAM hardware. External GDDR service remains UNAVAILABLE.** |
| Side-by-side 4 HBM + 4 HBF, independent pins/buses, full parallel operation, with pin-budget tradeoff | BasicSystem's mixed config explicitly sets every HBF `pair=null` and `relay_link=null`; four HBF stacks use actual MQSim channel partitions and four HBM stacks use independent parameterized media/GPU-link resources. Each stack completed 3 requests (24 total) with all owners released. No pin count, shared package pin budget, or calibrated link power exists. | **BASIC CPU FIXTURE MATCH for independent direct paths; physical pins, real HBM and pin-budget tradeoff remain UNAVAILABLE.** |
| Cascaded HBF behind an HBM base; GPU connects directly only to HBM; HBM direct and HBF relay share the relay-facing path; HBF access is two hops | BasicSystem rejects HBF direct in relay mode. After actual MQSim HBF completion, BasicFabric runs a pair-private HBF→HBM relay stage and then the paired HBM GPU stage. Relay receive and parameterized HBM-local output use the same two HBM banks and GPU-link queue. The actual fixture completed 24/24 requests for four pairs with zero final owners. | **BASIC CPU FIXTURE MATCH for sequential pair-private relay and shared paired-HBM contention; physical timings/energy and any package-global relay bus remain unvalidated.** |
| DASH dual-path read from the same HBF | Legacy `CpuService` retains its whole-base limitation. BasicSystem uses two HBF banks and independent direct/relay drain resources; relay additionally occupies the paired HBM bank/GPU link. The actual fixture alternated four direct/relay requests per HBF plus three local requests per HBM and completed 28/28 with four correct pairs and zero final owners. | **BASIC CPU FIXTURE MATCH for bounded dual-path arbitration; source fill timing, link rates, energy and thermal hotspots remain scenario/unconnected.** |

## Actual route/resource contract

The runtime contract is in `src/eq3_thermal/cpu_service.cpp:109-141`.

| Request | Accepted route | Reserved resources | Controlled endpoints | Energy placement |
| --- | --- | --- | --- | --- |
| HBM | `direct` only | `hbmN:die:D`, `hbmN:base`, `hbmN:gpu-link` | `hbmN` | HBM die + HBM base + GPU |
| HBF direct | `direct` except in `relay` topology | `hbfN:die:D`, `hbfN:base`, `hbfN:upstream`, `hbfN:gpu-link` | `hbfN` | HBF die + HBF base + GPU |
| HBF relay | `relay` only in `relay`/`dash` | HBF die/base/upstream/relay-link + paired HBM base/GPU-link | HBF and paired HBM, deduplicated | HBF die/base + paired HBM relay-base + GPU; no HBM array energy |
| External GDDR | `direct` read/write only in all-HBF | `gddr:service`, `gddr:link` | none | external energy only in `CpuService`; temperature unavailable |

The constructor enforces exactly eight package stack slots, exactly 8 HBF for
all-HBF, and exactly four unique one-to-one HBF/HBM pairs for relay/DASH
(`cpu_service.cpp:74-102`). Many-HBF-to-one-HBM and a shared relay bus across
pairs are rejected by construction. Resource acquisition is atomic at
admission, but the fixture has no channel count or byte-rate service tail.

The opt-in BasicSystem contract is separate:

| Request | Backend | Package resources and completion |
| --- | --- | --- |
| HBF direct | Persistent `stack_local_page` enters a topology-matched MQSim channel group; native request route remains `direct` | Reserve one of two HBF banks before submit; after raw MQSim completion equals reported completion, drain on the HBF direct link |
| HBF relay | Same actual MQSim media path; package route is stored separately and relay topology rejects a package-direct request | Reserve HBF bank, then pair-private relay into a free paired-HBM bank, then serialize on that HBM GPU link |
| HBM local | `PARAMETRIC_HBM_SCENARIO`, not MQSim and not a real DRAM backend | Reserve from the same two HBM banks used by relay receive, then serialize on the same HBM GPU link |

`BasicSystem` retains external arrival/wait, backend media/reported completion,
package completion, and final completion as separate fields. It advances the
existing MQSim `until(horizon)` interface without host sleep. It rejects
composition if MQSim's generic bandwidth bound makes reported completion differ
from the raw callback, so the package fabric is not appended to an unidentified
transfer term. `mark_source_ready` deliberately adds no second source fill.
This consumer is default off and is not connected to the production host
service or thermal solver.

For DASH specifically, this shared-resource rule is stricter than the cited
architecture. Sections IV-B and V-B describe independently accessible,
double-buffered SRAM regions that let different ready chunks from one HBF drain
over its direct and relay paths concurrently. The source still shares TSV fill
and requires buffer/path availability; it does not justify removing all HBF
arbitration. See [DASH, arXiv:2608.14333v1, Sections IV-B and
V-B](https://arxiv.org/html/2608.14333v1#S4.SS2).

The declarative P1 graph is less complete than the runtime contract.
`tools/eq3_thermal_config.py:130-144` declares per-HBM GPU resources and HBF
array/TSV sharing; a relay edge shares the paired HBM GPU resource and marks
`dram_array_access=false`, but the graph reports `arbitration_status` as
`NOT_IMPLEMENTED` (`:158-163`). `CpuService` separately adds paired-HBM base
contention. Drawing the graph is therefore not proof of implemented service.

## Configuration and request evidence

- `configs/eq3_thermal/topologies/mixed_direct_8.json` is currently **2 HBM +
  6 HBF**, not the requested side-by-side 4+4 definition. The engineering
  fixture changes it in memory to 4+4 at
  `tools/eq3_cpu_fixture.py:14-20`; all D4 requests are explicit `direct`
  reads (`tools/eq3_cpu_load_matrix.py:21-30`).
- In each retained D4 `none` run (5/10/25 requests/s/stack), the raw log has
  eight foreground starts at `t=0`: four HBM requests reserve distinct
  `hbmN:{die,base,gpu-link}` resources and four HBF requests reserve distinct
  `hbfN:{die,base,upstream,gpu-link}` resources. `D4_PER_STACK_SERVICE.json`
  also reports symmetric per-stack counts. This supports only the stated CPU
  fixture parallelism; it does not measure pins, buses, or bandwidth.
- D4 covers only `mixed_direct`. It supplies no all-HBF or relay workload
  evidence. The retained four-topology regression is a fixed software suite:
  59 Python tests passed, including topology/IR/export checks. Its C++
  `resource_energy_topologies` fixture checks two requests on `hbf0`, paired
  HBM contention, relay base energy, and external GDDR accounting. It is not a
  throughput or physical topology experiment.
- The cascade contention assertion at
  `tests/eq3_thermal/cpu_service_tests.cpp:263-265` observes a paired HBM direct
  request starting after the HBF relay releases shared HBM base/link resources.
  Endpoint tests also preserve Shutdown/Light admission across both traversed
  control domains.
- The newer `basic-four-topology-actual` receipt uses one fresh actual MQSim
  service process per topology and topology-matched derived maps: eight
  one-channel/one-die HBF groups for all-HBF and four for each 4+4 case. Results
  are 24/24 all-HBF, 24/24 mixed, 24/24 relay, and 28/28 DASH. Every configured
  source has at least three requests, waits become nonzero under the two-bank
  bound, and every final snapshot has null bank/link owners and no unfinished
  request. This supersedes `NOT_IMPLEMENTED` only for the small CPU behavior
  axis; it does not supersede the research/thermal gaps above.

## External GDDR scope discrepancy

The current system fixture and research-layered path preserve the intended
boundary: `CpuService` requires no package `gddr` thermal node and reports
`UNAVAILABLE_OUTSIDE_PACKAGE`; layered IR records an external physical GDDR
with `package_geometry_modeled=false`
(`tools/eq3_layered_ir.py:398-417`). Energy remains separately observable.

The older standalone P1 generator does something different:
`tools/eq3_thermal_config.py:145-149` creates a `gddr` thermal node and a board
proxy edge, while excluding it from the eight stack slots. Its fixed test
explicitly expects that node. That artifact must not be cited as package-only
all-HBF thermal evidence. This is an existing representation mismatch, not a
request to remove or reinterpret retained evidence.

## Remaining closure after the basic implementation

1. Replace the scenario two-bank capacity/timing/energy with sourced parameters
   before treating the implemented bounded ownership as a validated SRAM model.
2. Bind per-stack interface width/rate and a package pin-budget accounting
   layer before making the side-by-side pin-budget claim. Preserve the current
   disjoint-resource fixture as engineering evidence.
3. BasicFabric now models sequential HBF-HBM and HBM-GPU stages with explicit
   scenario rates/latencies and pair-private queues. Source/calibrate those
   values and decide whether a future research topology instead has a
   package-global relay bus.
4. Basic DASH now has two bounded source banks and independent direct/relay
   drain resources. Its source-ready point comes from completed MQSim data-out;
   a separate calibrated shared-TSV fill/power model is still absent.
5. Choose one external-GDDR thermal boundary for future generated artifacts.
   Package-only EQ3 should retain physical identity and external energy while
   keeping GDDR temperature unavailable, consistent with the current user
   decision.

These remaining items are research parameterization, production integration,
or further structural changes. The completed basic fixture does not authorize
or validate them.
