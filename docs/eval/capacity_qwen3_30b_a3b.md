# Qwen3-30B-A3B HBM/HBF capacity evaluation

Source: [capacity study at 3714484](https://github.com/SlugLab/HBFSim/tree/37144843906b3bd71f3fbac1fecc6b5080d82b95), especially `experiments/hbm_hbf_capacity/README.md`, `adapters/vllm_capacity`, and `docs/重要实现问题以及需补做实验/19-hardware-grounded-evaluation-plan.md`. Current organization follows [doc 47](../47-评估主线设计.md); old EQ labels are cross-referenced in [overview](overview.md).

## Capacity and placement

The frozen source protocol sweeps requested near-memory:HBF expert-weight ratios 1:0, 1:1, 1:2, 1:4, 1:8, 1:16. These are logical expert-cache partitions, not statements about installed physical HBM or flash. The available historical RTX PRO 6000 uses GDDR7; label it near-memory/HBM-equivalent, not measured HBM hardware.

`capacity_geometry` divides the fixed expert working set by the requested ratio, rounds allocation to profile pages and whole expert slots, and reports `actual_hbm_bytes`, `actual_hbf_bytes`, `complete_expert_slots` and achieved ratio. Ratios with no complete slot may have zero cache and null achieved ratio; retain them as explicit geometry. Also report WSS/cache ratio and effective cache after KV, activations, nonexpert weights, workspace and allocator reserve. These latter dynamic reservations are planned measurements; the offline expert replay does not implement a full VRAM budget model.

The replay's replacement/admission unit is one complete expert. Media timing splits each expert miss into profile-sized pages. It is not the runtime's page-granular CLOCK cache. A full-resident 1:0 arm begins preloaded; other arms begin with their policy's empty cache. CLOCK, LRU and offline Belady/oracle are supported; Belady has future knowledge and must be labeled a bound. FIFO replacement for live capacity is not added; the separate prefetch model uses FIFO staging. Expert weights are read-only in replay, so zero writes does not validate dirty eviction or live writeback. Existing CPU runtime tests cover those behaviors separately.

## Workload and timing

Use frozen model inventory and route trace bytes, with hashes and the model fingerprint checked by the loader. The source Qwen shape is 48 layers × 128 experts, 8 selected per token. Native capture protocol, warmup, seeds and repetition rules are in [methodology](workload_methodology.md). New native Qwen capture and weight staging remain DEFERRED/UNVERIFIED.

The analytic mode reports a serialized profile-based bound. External fast/hybrid/MQSim modes share `hbf_trace_timing` and split misses into pages. They are ordered blocking misses with no captured compute gaps, effectively QD=1 at replay submission. They do not reproduce serving overlap, the live GPU critical path, or general queue saturation. Use `hbf_mqsim_bench` and the queue-depth regression for synthetic concurrent media arrival experiments. The MQSim profile queue-depth fix remains active and unchanged.

## Metric availability

| Metric | Status on eval_base |
|---|---|
| requested/achieved capacity, slot/page granularity | derived from frozen inventory; implemented |
| expert accesses, hits/misses, byte hit ratio, eviction, reads and page movement | trace-level modeled/derived; implemented |
| HBF writes/programs | zero by read-only replay contract; live dirty paths have separate tests |
| media latency, channel/queue-depth profile | implemented typed profile; replay admission remains blocking |
| modeled service, exposed stall, emulator dispatcher wall time | implemented separate fields; wall time is host simulation overhead |
| throughput/tokens per second | live capture measurement is external; no inferred GPU tokens/s from modeled service |
| TTFT, TPOT/inter-token latency, P95/P99/SLO goodput | unsupported serving metrics in this branch |
| prefill/decode end-to-end latency | phase access counts supported; live phase timing NOT VERIFIED |
| pressure from KV/activation/workspace | planned full-runtime measurement |
| mean/median across independent captures, CI/error bars | planned aggregation; see aggregation rules |

## Reproduction and provenance

The minimal dependency closure is `model_inventory.py`, `placement_policy.py`, `trace_replay.py`, `trace_validation.py`, `trace_schema.json`, policy/replay tests and the standalone timing executable plus integration tests. All are selected from the source SHA. No public API/stats ABI, loader, launch interception or live model registration changes are needed for replay. Optional CMake targets are off by default.

[Mechanism runbook](runbooks/mechanism_validation.md) provides CPU fixture and supplied-input commands. Fixtures prove implementation accounting, not Qwen performance. `trace_validation.py` checks archived capture coverage/token consistency; schema validation must be performed separately on complete capture records, since replay's small unit fixtures intentionally supply only consumed fields. Preserve source manifest, per-cell config hashes, trace hashes, command, environment fingerprint, seed, warmup and input-run repetition. Do not overwrite prior output directories.

No old experiment result is republished as an eval_base measurement. Capture tools and weight staging are available only at the pinned source branch and must pass a separate integration gate before use on a child `eval/capacity` branch.
