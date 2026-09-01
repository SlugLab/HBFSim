# Qwen3-30B-A3B capacity adapter

This directory captures the real routed-expert order exposed by the frozen
vLLM 0.15.1 runtime and replays that order against deterministic expert-cache
policies.  It never patches system `site-packages`, downloads a model, or sends
an unbacked/capacity pointer to Triton or FlashInfer.

The current implementation is deliberately split into evidence layers:

- `runner.py` uses vLLM's supported `enable_return_routed_experts` path and
  writes real route IDs plus the frozen safetensors tensor metadata to JSONL.
- `trace_validation.py` checks layer counts, top-k cardinality, expert ranges,
  tensor offsets/shapes/bytes, access ordering, and optional token/route repeat
  equivalence.
- `trace_replay.py` performs exact cache/traffic replay for CLOCK, LRU, and
  offline Belady at all requested ratios.  Its preliminary timing number is
  clearly tagged as a serialized analytic profile bound, not as HBFSim
  fast/hybrid, MQSim, physical HBF, or measured GPU time.

Top-k weights and true per-layer timestamps are not exposed by the frozen vLLM
routed-expert API.  They are therefore emitted as `null`, with explicit
availability/semantics fields; IDs and their order remain real Qwen evidence.

All main experiments keep `readahead_pages=0` because PR #5 did not pass its
production-reachability and completion-ordering gate.
