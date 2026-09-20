# EQ3 topology connection audit v2

Status: read-only audit, 2026-09-20. No route, scheduler, physical parameter,
threshold, or solver was changed or run. The requested connection semantics are
`USER_CONFIRMED`; code statements and retained results below are `DOC_DERIVED`.
Any suggested physical interpretation beyond them is explicitly a gap.

## Result

| Requested topology meaning | Current implementation | Verdict |
| --- | --- | --- |
| Eight standalone HBF stacks, each with a base-die SRAM buffer | `CpuService` requires eight HBF stacks for `all_hbf_direct`, gives every HBF an independent `die`, `base`, `upstream`, and `gpu-link` reservation, and requires an external physical GDDR identity. It has no SRAM/buffer object, capacity, occupancy, queue, hit/miss behavior, or buffer energy. The generic `base` resource serializes the whole fixture transaction and is not evidence of an SRAM buffer. | **PARTIAL: independent stack/base path exists; base SRAM buffer is NOT_IMPLEMENTED.** |
| Side-by-side 4 HBM + 4 HBF, independent pins/buses, full parallel operation, with pin-budget tradeoff | The D4 fixture overrides the repository's default `mixed_direct_8` count from 2+6 to 4+4. Each stack gets uniquely named base/link resources. Retained D4 raw logs show all eight first requests starting at `t=0` with disjoint stack resources, so the fixture permits eight-way parallel starts. There is no pin count, bus width, shared package pin budget, bandwidth-derived service time, or pin-area/power tradeoff in `CpuService`. | **PARTIAL: fixture-level independent parallel resources are observed; physical pin/bus budget and tradeoff are UNAVAILABLE.** |
| Cascaded HBF behind an HBM base; GPU connects directly only to HBM; HBM direct and HBF relay share the relay-facing path; HBF access is two hops | In `relay`, HBM accepts only `direct`; HBF rejects `direct` and accepts `relay`. A relay reserves the HBF die/base/upstream/relay-link plus its paired HBM base and HBM GPU link. Therefore an HBM direct request and its paired HBF relay contend for both the HBM base and HBM GPU link. Forwarding heat is charged to the HBM base, not an HBM DRAM die, and both HBF/HBM control endpoints gate admission. `link_bytes` is multiplied by two. The two hops are not two sequential link services: the fixture applies one transaction duration and holds all resources together. Each pair has a private `hbfN:relay-link`; no package-wide shared relay bus or bandwidth is represented. | **FUNCTIONAL FIXTURE MATCH for unique HBM/HBF pairs and shared paired-HBM contention; DESIGN_LIMITATION for sequential two-hop latency, link bandwidth/queues, and any cross-pair shared relay bus.** |
| DASH dual-path read from the same HBF | The paper's HBF base die has independently accessible SRAM transfer regions/banks. Ready chunks may drain concurrently through the direct GPU-HBF path and the relay path; the HBM relay SRAM is also double-buffered. Current `CpuService` assigns both routes the same `hbfN:base` and `hbfN:upstream` resources, so a direct and relay request from the same HBF serialize before their distinct GPU-facing links matter. | **DESIGN_LIMITATION: current whole-base/upstream locking cannot represent the paper's same-HBF dual-path concurrency.** |

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

## Minimal closure items (no implementation authorization inferred)

1. Add explicit, default-off base-buffer descriptors for all-HBF if SRAM
   capacity/queue/energy is required; a generic base lock must not be renamed
   as a validated SRAM model.
2. Bind per-stack interface width/rate and a package pin-budget accounting
   layer before making the side-by-side pin-budget claim. Preserve the current
   disjoint-resource fixture as engineering evidence.
3. For cascade timing claims, model GPU-HBM and HBM-HBF links as sequential
   resources with explicit byte-rate/latency queues. Decide separately whether
   the relay bus is private per pair or shared package-wide; current code is
   private per pair.
4. For DASH, represent the two HBF-side SRAM transfer banks/regions and the
   shared-TSV fill constraint so direct and relay drains can overlap only when
   different ready banks and both paths are available. Simply dropping the
   existing base/upstream locks would overstate the paper's concurrency.
5. Choose one external-GDDR thermal boundary for future generated artifacts.
   Package-only EQ3 should retain physical identity and external energy while
   keeping GDDR temperature unavailable, consistent with the current user
   decision.

Items 1-4 affect scheduling, resource ownership, timing, or module
responsibility and therefore require an approved minimal design before code
changes under the active `AGENTS.md` gate.
