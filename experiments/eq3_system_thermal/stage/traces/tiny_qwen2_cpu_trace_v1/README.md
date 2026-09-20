# Tiny Qwen2-style CPU trace

This artifact executes a deterministic NumPy forward with random tiny weights:
hidden size 56, 28 query heads, 4 KV heads, head dimension 2, intermediate
size 128, 28 layers, four-token prefill, and two single-token decode steps.

It records actual CPU array access names, order, shapes and bytes, plus explicit
operation dependency IDs. It is a structural trace derived from a tiny
Qwen2-style decoder. It is not a pretrained model, native GPU/framework trace,
GPU timing result, or token-performance calibration.

The same output separately regenerates Qwen2.5-7B/72B logical region bytes,
1 MiB scenario addresses, and analytical dense MAC counts from the registered
official metadata. Those projections do not inherit the tiny CPU runtime.

Reproduce from `experiments/eq3_system_thermal` with BLAS thread counts set to
one:

```text
python3 tiny_cpu_trace.py \
  --config stage/traces/tiny_qwen2_cpu_trace_v1/config.json \
  --output stage/traces/tiny_qwen2_cpu_trace_v1/trace.json
```
