# Qwen3-30B-A3B capacity adapter

This directory captures the real routed-expert order exposed by the frozen
vLLM 0.15.1 runtime and replays that order against deterministic expert-cache
policies.  It never patches system `site-packages`, downloads a model, or sends
an unbacked/capacity pointer to Triton or FlashInfer.

The current implementation is deliberately split into evidence layers:

- `runner.py` uses vLLM's supported `enable_return_routed_experts` path and
  writes real route IDs plus the frozen safetensors tensor metadata to JSONL.
- `routed_capture_compat.py` compensates for the frozen vLLM 0.15.1
  initialization order without editing `site-packages`: model-construction and
  dummy-run calls see a no-op deferred proxy, while real forwards after buffer
  initialization reach vLLM's original capturer.
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

Formal sweeps randomize the ratio/policy/timing-mode execution order with
`--order-seed`. `--ratio` and `--policy` may be repeated to restrict expensive
reference/MQSim validation to preselected points without changing the canonical
cell manifest or its `CellID`.

## E6 experts-only capacity staging

The E6 path is explicitly opt-in.  A per-cell, generated `.dist-info` overlay
exposes one `vllm.general_plugins` entry point, `hbfsim_capacity`; the overlay
and `VLLM_PLUGINS=hbfsim_capacity` are present only for an E6 cell.  The plugin
registers the custom `hbfsim_capacity` load format and lazily replaces the
`Qwen3MoeForCausalLM` architecture for that process.  With the overlay absent,
vLLM resolves its native Qwen model and default loader unchanged.

The capacity loader is local-only and fails closed unless the frozen model,
manifest SHA256, model fingerprint, geometry, shard sizes, and all 18,432
expert tensor header records agree.  It reads safetensors payloads only for
non-expert keys.  Expert payloads are mapped read-only through one worker-owned
HBFSim context during model load, which allocates the full-resident capacity
cache early and holds it until teardown.

The model allocates routers and all native non-expert parameters, but no
`SharedFusedMoE` expert parameters.  For each layer it computes native top-k
routing, stages the unique selected experts through the instrumented
`hbfsim_capacity_copy_bf16` kernel into one reusable ordinary BF16 `w13`/`w2`
workspace, remaps expert IDs to compact slots, and calls frozen vLLM
`fused_experts`.  HBFSim logical pointers are never passed to the fused-MoE
kernel.  The generation state machine rejects duplicate in-flight stages,
stale generations, and use before staging completion.

`run_capacity_pilot.py` enforces Python 3.13, vLLM 0.15.1, Torch CUDA 12.8,
the frozen manifest, local/offline model use, at least 4 GiB free disk, and a
continuous no-external-GPU-process gate before importing the CUDA workload.  A
watchdog records any external compute process appearing during the cell; such a
cell is marked contaminated.  It never terminates, pauses, or modifies another
process and never uses a dummy memory reservation.

Required E6 environment variables are:

- `HBFSIM_CAPACITY_MANIFEST` and `HBFSIM_CAPACITY_MANIFEST_SHA256`
- `HBFSIM_CAPACITY_LIBRARY`, `HBFSIM_CAPACITY_PROFILE`, and
  `HBFSIM_CAPACITY_REPORT_DIR`
- `HBFSIM_CAPACITY_NVCC`, `HBFSIM_CAPACITY_PASS_LIBRARY`, and
  `HBFSIM_CAPACITY_PREPARE_PTX`

The E6 contract is single-rank, eager, BF16, `max_num_seqs=1`, with EPLB,
expert parallelism, sequence-parallel MoE, shared experts, quantization,
speculation, and online downloads disabled.
