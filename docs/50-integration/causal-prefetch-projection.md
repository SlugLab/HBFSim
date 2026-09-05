# Causal prefetch projection contract

`scripts/eval/run_prefetch.py` compares `none`, `on_demand` and
`one_layer_ahead` with identical frozen inputs. Each policy owns a fresh native
MQSim process; its demand and prefetch share that engine and queue. Bounded
clock advancement allows requests at compute boundaries without overshooting.

This is a layer-synchronous TRACE_COMPOSED model: each batch waits for its
unique expert weights, then executes a supplied compute-only duration. No
duration default is invented. Live scheduling, GPU allocation and independent
per-sequence DAGs are outside this model. Inputs bind inventory, recomputed
capacity budget, real/shuffled routing plus manifest, and a compute index with
matching hashes, prompt length and source. KV capacity must cover the active
members and context. Whole-expert sorted page packing counts padding in both
cache and media bytes; production address semantics are unchanged.

`none` serializes each miss; `on_demand` issues batch misses before waiting.
`one_layer_ahead` predicts from each current member's latest already observed
route at the target layer. Unknown first observations produce no prediction.
Current compute weights stay pinned; ready unpinned objects use LRU replacement.
In-flight requests reserve capacity and demand promotes an existing prefetch
without duplicate I/O. Failed speculative reservation leaves cache state
unchanged. An oversized atomic working set is INFEASIBLE before execution.

Every request records issue, ready, first-demand, consume and eviction times,
prediction evidence and useful/late/useless classification. Final speculation
drains after the separately recorded decode horizon. Per-expert request-ordinal
matching against on-demand assigns extra bytes to unmatched requests and saved
bytes to unmatched baseline requests. Gross extra minus saved equals the signed
traffic delta; this is accounting, not individual causal attribution.

Native command/response transcripts reconcile IDs, arrivals, admission,
completion, bytes and QD. Input/build/tool/environment identities accompany raw
output. Synthetic routes or compute remain MOCK; contradictory attribution is
rejected. Other inputs remain PROJECTED without capture/hardware authentication.
The tool writes neither scheduler DONE nor scientific validation receipts.

Gold: `test_prefetch_replay.py` uses a TEST_ONLY constant-delay oracle;
`test_mqsim_service_client.py` exercises the native transport;
`test_run_prefetch.py` uses tiny GGUF/routing/compute fixtures through all three
native policies. Evidence is in `results/gold/prefetch-replay/`; these controls
are not Qwen serving measurements.
