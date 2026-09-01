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
  offline Belady at all requested ratios.  It can retain the explicitly tagged
  analytic upper bound or pass the exact miss stream to the repository-built
  `hbf_trace_timing` executable for HBFSim fast, hybrid-reference-sampled, or
  full MQSim reference timing.  Because routed-expert events do not expose
  device arrival timestamps, these timing modes use ordered, blocking demand
  misses with no compute gaps.  Each compact expert-miss event is expanded by
  the timing executable into the profile's actual 16 KiB page requests; device
  service, exposed stall, and simulator wall time are reported separately.

The two frozen toolchains remain intentionally separate.  HBFSim and the trace
timing executable are built with CUDA 13.0, GCC/G++ 14, the CUDA 13 `ptxas`, and
an explicit G++ 14 CUDA host compiler.  Native Qwen capture stays in the
server's original vLLM/PyTorch environment and uses its existing GCC/G++ 13
extension toolchain.  No package is upgraded merely to make version strings
look uniform.

Top-k weights and true per-layer timestamps are not exposed by the frozen vLLM
routed-expert API.  They are therefore emitted as `null`, with explicit
availability/semantics fields; IDs and their order remain real Qwen evidence.

All main experiments keep `readahead_pages=0` because PR #5 did not pass its
production-reachability and completion-ordering gate.
