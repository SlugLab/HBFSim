# Native MQSim and aggregate topology-service semantic comparison

Date: 2026-09-20. This is a bounded evidence comparison. It did not start a
thermal solve, rebuild MQSim, or repeat an unchanged native campaign. Existing
native receipts remain immutable. The new service is a default-disconnected
engineering model and is not relabelled as native execution.

## Result

The two paths answer different questions.

- The isolated native backend demonstrates real MQSim command lifecycles,
  physical placement, shared FTL/TSU/PHY ownership, out-of-place one-page
  maintenance commit, and actual foreground/maintenance interference. It does
  not demonstrate target-HBF TB/s, payload equality, die-wide refresh, ECC, or
  calibrated product energy.
- `TopologyService` demonstrates deterministic aggregate byte flow through
  explicit per-channel media, finite two-bank turnover, direct/relay/DASH
  routes, endpoint gates, and shared HBM-link capacity. Its completion and
  latency are window-quantized engineering estimates. It has no MQSim command,
  FTL mapping, NAND payload, or native program/erase result.
- The 60-point base system/thermal matrix presently sends foreground read-rate
  demand only. Every window says
  `NO_MAINTENANCE_DEMAND_IN_BASE_RATE_WORKLOAD`; therefore zero maintenance
  queues in that matrix mean **not exercised**, not free or infinitely fast
  maintenance.

## Operation-by-operation contract

| Operation or property | Isolated native backend | Aggregate topology service | Safe comparison |
| --- | --- | --- | --- |
| Foreground read | A request becomes actual MQSim child transactions and emits command-issued, media begin/end, and data-out phases with native transaction IDs and PPA fields. The OCP4K pilot observed 7,100 `USERIO` read children. | A byte cohort consumes configured channel media work and route resources. `media_read` and link activity are model facts; effective delivery completes at an exact integer time under the aggregate pipeline approximation. | Compare conservation, route identity, queue pressure, and qualitative contention. Do not compare either service rate or latency as if both were native. |
| Foreground program | The standalone startup caller submitted 16,384 real 4 KiB writes, drained them, then completed 16,384 same-page native reads. The rolling-QD256 receipt records 81,920 native command events and all loaded pages covered, including the last page. | `program` is an explicit external job whose media work is scaled by a scenario ratio. It emits `media_program`, but has no FTL allocation, mapping generation, data-program success, or readable destination. | The proxy can budget heat/resource pressure from a declared program coefficient. Only native evidence can claim MQSim program and mapping behavior. |
| Page maintenance / “refresh” | `maintain` performs a native source read, destination program, generation-CAS mapping commit, old-page retirement, and optional safe reclaim. Age reset is returned only after commit. This is one-page out-of-place metadata/version maintenance, not die-wide product refresh. | There is no combined refresh transaction or mapping commit. A caller may submit explicit `refresh_read`, `program`, `erase`, or migration jobs with a maintenance ID. Completion means aggregate job bytes finished; it cannot reset native mapping age by itself. | Keep operation phases and energy inputs explicit. Never treat a proxy maintenance completion as a native refresh commit. |
| Erase | The fixed C++ test covers a successful safe erase and an injected post-commit erase failure. The latter preserves the committed destination and reports reconciliation required. The OCP4K loop itself observed zero erase media commands because its 64 committed jobs did not perform a safe reclaim erase. | `erase` consumes its configured aggregate media work and can be blocked by endpoint state. Its payload `bytes` is a work-accounting fixture; no native block is selected or erased. | Native fixed evidence establishes lifecycle/error semantics. Proxy erase is only a resource/energy scenario once explicit coefficients are provided. |
| Failure after activity | Native media activity before a terminal failure remains recorded. Read, program, stale-CAS, and post-commit erase failures preserve their distinct mapping consequences. | The service currently models gating and backlog, not NAND command failure, stale mapping, or reconciliation state. | Failure energy must remain in the native ledger; no native failure probability may be synthesized in the proxy. |

## Completion identity and conservation

The native service keeps foreground completion separate from
`maintenance_completions`. `finish` requires zero pending maintenance and one
terminal completion for each accepted maintenance ID. The final backend fixed
tests report `PASS METADATA_VERSION_VALIDITY shared_TSU_PHY maintenance
lifecycle` and `PASS isolated maintain JSON horizon/native-ID protocol`.

The OCP4K pilot supplied end-to-end runtime evidence:

- 7,260 offered, submitted, and completed foreground requests;
- zero censored and zero drain-only completions;
- 64 committed maintenance jobs; zero failed, cleanup-failed, or unsupported;
- per-stack foreground equality: each HBF completed 1,775/1,775 and each HBM
  completed 40/40;
- 7,228 unique native child transactions: 7,100 `USERIO` plus 128
  `HBF_MAINTENANCE`; the latter are 64 native reads and 64 native programs.

The aggregate service uses a different identity layer. Explicit jobs retain a
caller `job_id`; `completion_ids` and `maintenance_completion_ids` are emitted
once after the final aggregate batch completes. Automatic foreground cohorts
are retired after completion. Per-stack cumulative offered bytes must equal
cumulative delivered effective bytes plus foreground backlog. These are
model-level identities, not native transaction IDs.

Fixed proxy tests cover partial completion followed by exactly one terminal
ID, duplicate-ID rejection, completed cohort retirement, and separate
maintenance IDs. The campaign analyzer independently rejects repeated
maintenance completion IDs and per-stack/end-to-end byte imbalance.

## Shared arbitration: what is and is not the same

The native backend uses one `MqsimOnlineEngine`. Foreground and maintenance
share the actual address mapper, block manager, TSU, and ONFI PHY. Maintenance
is placed in the existing GC/WL queue class; source pinning and mapping
generation protect ownership without replacing the native scheduler. The fixed
test also schedules a real foreground write during maintenance and observes
that the foreground generation wins exactly once while stale maintenance
discards its destination.

`TopologyService` shares arithmetic capacities rather than native objects:

- foreground and maintenance use the same configured channel media capacity;
- HBF relay and HBM-local cohorts share the paired HBM banks and GPU link;
- relay admission jointly checks the HBF and partner-HBM endpoints;
- DASH direct work may proceed when its unrelated relay endpoint is shut down;
- severe state blocks foreground but retains the explicit maintenance policy;
  shutdown blocks maintenance until a later legal window;
- the two banks are continuous-turnover buffers, not 20 ms-sized storage.

Those rules provide a controllable proxy for contention and heat-source
placement. They do not reproduce MQSim TSU ordering, plane command overlap,
FTL allocation, GC, or physical bus timing. Increasing the offered rate to
0.384--1.920 TB/s per stack is deliberate thermal/service pressure; it is not
evidence that the native backend or a product sustains that rate.

## Evidence identity

All paths below are under
`eq3_thermal/plans/isolated-maintenance-campaign-v1/points` unless stated
otherwise.

| Evidence | Result and identity |
| --- | --- |
| `backend-generation-cpp-test-v1/result.json` | PASS; SHA-256 `6eb84a5783265f166b0fad611e7b2d3366cae916d00c15a6cf37ed5c92d4576a` |
| `backend-generation-service-test-v2/result.json` | PASS; same minimal terminal receipt hash; detailed stdout records the JSON horizon/native-ID protocol PASS |
| `OCP4K-LOOP-PILOT01/DONE.json` | COMPLETED in 83.04947019899555 s; SHA-256 `cb977f1c9bb45ec17a660b81ffb3d09cc2d2b3c3530c6c6beb5afd63a868dbcd` |
| `OCP4K-LOOP-PILOT01/summary.json` | Counts above; SHA-256 `4288782e581994510386fcab6be0056348f463fada484e1b3fbc95e961dc13f4` |
| `NATIVE-OPERATION-SUMMARY03/result.json` | PASS; 7,164 native reads, 64 programs, zero erase/unknown commands; SHA-256 `b5a18c1ae8a315bef850358e28e8d076ed962bcadff1cad1ebac87c95e0c94cd` |
| `STARTUP-WRITE-ROLLING-W256-N16384-01/raw/summary.json` | PASS; 16,384 writes + 16,384 read verifies; SHA-256 `332b99a3f1cb7e9549038502e17970edaa32d756e9c8df6c11e953863cdfee30` |
| `experiments/eq3_system_thermal/topology_service.py` | Aggregate proxy source reviewed at SHA-256 `4e40958215f26cd1675ec9280c3674bebb333b943257edbe93f0d54f1fdb55ab` |
| `experiments/eq3_system_thermal/test_topology_service.py` | Proxy fixed contracts reviewed at SHA-256 `ae6344c36b4722f11ca668256b9d084f2105325a61d9863f4afc411436b2636d` |

The isolated generation/pin binary registered in the backend boundary document
has SHA-256
`c64610b0f281397975e649b32f44161b00240f12d6a49b9b4d4776b1f554c257`.
The fixed native receipts already cover every requested semantic axis, so this
review did not rerun an unchanged binary merely to obtain a new timestamp.

## Remaining capability boundary

Native and proxy evidence together still leave payload equality, target-HBF
RBER/ECC/retry probability, calibrated program/erase energy, die-wide refresh,
physical spare/overprovisioning, product endurance, live GPU, external-GDDR
service/temperature, and token/s unavailable. The new system/thermal matrix
may report conditional aggregate delivery, queueing, resource use, maintenance
facts when actually injected, and complete coupled temperatures. It must keep
native backend capability and aggregate TB/s thermal pressure as separate axes.
