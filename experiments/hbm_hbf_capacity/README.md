# Qwen3-30B-A3B HBM-HBF capacity study

This directory contains the reproducible experiment harness for the first
capacity study. It does not change the default HBFSim profile or the existing
`adapters/vllm` timing adapter.

The experiment freezes the following protocol:

- model: the pre-existing Qwen3-30B-A3B BF16 safetensors snapshot;
- native runtime: the frozen Python 3.13 / vLLM 0.15.1 environment, FlashInfer
  attention, Triton unquantized MoE, eager execution, and seed 0;
- smoke workload: four fixed 32-token prompts, 32 output tokens, maximum model
  length 256, fixed output length, and concurrency one;
- near-memory/HBM-equivalent ratios: 1:0, 1:1, 1:2, 1:4, 1:8, and 1:16;
- cache policies: full-resident, CLOCK, LRU, and offline Belady/oracle;
- baseline prefetch policy: `readahead_pages = 0`.

Model load, compilation, and warmup are excluded from steady-request timing.
Native execution, real Qwen route traces, trace-driven model results, MQSim
reference results, and fast/hybrid model results are reported as distinct
evidence classes. Simulated HBF timing is never described as physical HBF
hardware measurement.

All generated files, caches, builds, logs, and results must remain beneath
`/root/hbfsim-exp`. GPU cells require an empty compute-process list at admission
and must emit a `GPU_BUSY.json` record instead of disturbing another process.
No command in this study pushes a branch or modifies the source checkout from
which this isolated clone was created.
