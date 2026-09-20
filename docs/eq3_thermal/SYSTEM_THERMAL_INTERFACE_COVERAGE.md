# Four-topology system thermal interfaces

This campaign is `CONDITIONAL_SIMULATED`. The original MQSim source, default
backend, production ABI and P2 evidence are unchanged. The TB/s service is an
isolated behavior proxy; it does not claim native MQSim produces those rates.
Native operation evidence and transfer limits remain separately identified in
`NATIVE_PROXY_COMPARISON.md`.

| Interface | Actual producer | Actual consumer | Enablement | Capability and limit |
|---|---|---|---|---|
| Offered bytes by stack/channel | `RateWorkload` | `TopologyService.advance` | Explicit system point config | Preserves offered overload and distribution; no token inference |
| Topology/resource contract | Existing `BasicFabric` validation plus explicit OCP media limits | `TopologyService` and `CausalTopologyService` | Experimental runner only | Direct/relay/DASH paths, shared HBM link, joint controlled endpoints; continuous buffer turnover approximation |
| Physical activity phases | Shared aggregate service ledger | `EnergyMapper` and causal/maintenance energy adapters | Explicit coefficient profile | Media and transfer facts are separated; read base energy counted once; forwarding alone does not access HBM array |
| Window byte admission | Existing `ReadRatePolicy`, `EndpointAwarePolicy` | Next service window | One of three explicit policies | Completed facts affect future work only; active causal transfers drain; this is not native backend latency |
| Full package temperatures | Existing layered RC service | Policy, reliability age and output analysis | Explicit immutable model/binary | GPU, every array die/base and shared paths; original 300–400 K domain; no qualified fast thermal ROM |
| Exact storage completion | `CausalTopologyService` | `CausalExecutor.complete` | Separate causal runner | Dependency unlocks only after all child stripes and explicit retries finish; rate-only 20 ms completion is never used as a layer timestamp |
| Captured weight/dependency template | Actual tiny NumPy Qwen2-style forward | `build_architecture_trace(tiny_cpu_template)` | Explicit artifact/checksum/context | Observed operation/access order drives target tasks; target model sizes regenerated from official metadata; tiny CPU wall time is not target GPU timing |
| Compute completion | Single shared scenario compute resource in `CausalExecutor` | Token completion and dynamic GPU energy | Explicit compute durations and active power | Validated tiny CPU access/dependency template or separately classified synthetic DAG; target shapes/bytes regenerated; no calibrated token/s, KV or activation traffic claim |
| Cache fill/hit/replacement | Completed source delivery, bounded LRU reservations | Explicit HBM fill/read service jobs | `cache_mode=external_hbm` | Finite cache, actual service costs and pinned in-flight hits; excluded from standalone topology without HBM |
| Placement copy | Access-triggered source-read/destination-program phases | Version commit, later request placement, old-block erase | `migration_mode=basic`, explicit finite destination capacity | Metadata/version semantics and modeled bytes; not native payload integrity or a full FTL |
| Temperature history | Per-die thermal samples | `ReliabilityLedger` | Explicit Ea/age profile | HeatWatch transfer proxy; wall and equivalent age remain distinct; no RBER or lifetime prediction |
| Refresh demand and commit | `MaintenanceDriver`, shared service completions | Per-extent age and physical block operation counters | Explicit maintenance mode and spare pools | Same service resources as foreground; only committed extents reset age; read/program/erase and failure retained |
| ECC/retry scenario | Explicit conditional retry count, rather than inferred error rate | Additional shared read service before logical success | Optional 0/1/4 retries per source read | SSD-inspired cost sensitivity; no claim that temperature monotonically predicts HBF retry probability |
| Native MQSim command/maintenance evidence | Existing isolated backend and immutable receipts | Native/proxy semantic comparison | Separate small native validation path | Preserves native stage/stack mapping; not the producer of TB/s fluid receipts |

Each runner writes input and source identity before its process starts, then
immutable activity/energy/thermal/control evidence and `DONE` or `FAILED`.
The base rate matrix intentionally has no token DAG and reports token
throughput `UNAVAILABLE`. New causal and maintenance interfaces require their
own completed run receipts before being described as experimentally exercised.
External physical GDDR has no package temperature node and remains
`UNAVAILABLE`; this is not a claim of zero board-level energy or heat.
