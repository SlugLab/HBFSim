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

## Optional causal consumer

`causal_workload.build_architecture_trace` keeps
`dependency_mode=synthetic_metadata_dag` as the existing mode. The optional
`dependency_mode=tiny_cpu_template` additionally requires this artifact's
repository-relative `tiny_trace_path`, exact `tiny_trace_sha256`, and a positive
`projection_context_tokens`.

The consumer recomputes and validates the artifact checksum, causal dependency
order, actual float32 weight-access shapes/bytes, attention-to-MLP-to-head
pattern, and the selected target model's layer count, BF16 tensor shapes,
1 MiB scenario addresses, payload bytes and analytical MAC count. Generated
storage and compute tasks carry the observed template operation IDs. Their
`duration_ns` values still come only from explicit scenario inputs; the tiny
CPU runtime is never transferred to 7B/72B timing.

`CONSUMER_EVIDENCE.json` records the fixed-test consumer output for both
registered Qwen2.5 targets. It is software evidence, not a thermal or native
backend result.
