# Supported model-weight instrumentation

This package records the successful OLMoE supported-weight experiment and the source/environment needed to reproduce it. Start with [results and coverage limits](RESULTS.md), then [the tested environment](ENVIRONMENT.md) and [rebuilt source validation](REBUILT_SOURCE_VALIDATION.md).

The experiment selects model parameter storages, deduplicates aliases, registers their complete ranges with HBF and replaces the corresponding supported CUDA/PTX entries. Successful registration alone is not acceptance: verification also requires addressed modeled launches, completed module accounting and output equality with the native baseline.

## Evidence and scope

| Scope | Result |
|---|---|
| Representative layer: eight storages, 1,011,372,032 bytes | Output equality and addressed active-module coverage passed |
| All supported layers: 98 storages, 13,091,213,312 bytes | Output equality, 98/98 addressed active-module coverage and accounting closure passed |
| Attention projection, router and output-head storages | 49 storages remain outside HBF coverage |
| KV cache | Source feasibility assessment only; no implementation or experiment claimed |

The supported categories are expert weights, normalization weights and input Embedding. Read the exact interpretation of aggregate counts in [RESULTS.md](RESULTS.md); this short request does not traverse every byte or prove independent latency for every storage.

## Reproduction components

- Build HBF runtime and native gate separately with `scripts/reproduce_hbfsim_cpu_build.py`; the two runtime-link domains and actual hashes are recorded in [REBUILT_SOURCE_VALIDATION.md](REBUILT_SOURCE_VALIDATION.md).
- Retain PTX while compiling the relevant open-source framework modules. Pin the Torch/vLLM source versions, selected overlay binaries and CUDA12.8 framework/JIT toolchain described in [ENVIRONMENT.md](ENVIRONMENT.md).
- Extract exact embedded PTX with `tools/native_ptx_reproduction/collector/collect_native_ptx.py`. Use the exact selected-entry staging recipe in `tools/native_ptx_reproduction/execution/` with the separate norm and aggregate metadata profiles, then join their provenance with `tools/native_ptx_reproduction/scoped_join/join_scoped_staging_provenance.py`.
- Keep the selected entry, original container, original PTX, transformed PTX, pass metadata and native binding manifests consistent. Generate hashes from the actual new files.
- Verify a native short request, then the representative supported weights before expanding to all supported layers. Preserve separate output directories and all failure records.

The integrated runtime's representative model validation passed as epoch 5801: all eight selected storages had addressed active-module coverage, accounting closed, and output matched the new native baseline. The successful historical 98-storage experiment and fresh CPU-build evidence are separate records; a successful build does not by itself validate a new runtime combination.

## Troubleshooting and future scope

[KNOWN_ISSUES.md](KNOWN_ISSUES.md) records the concrete interface, parameter-metadata and toolchain mistakes found during development. Reuse those findings before changing the PTX rewrite algorithm. [KV_CACHE_FEASIBILITY.md](KV_CACHE_FEASIBILITY.md) identifies the separate read/write and allocation-lifetime work needed for mutable KV storage.
Build recipes: [HBF runtime and both metadata profiles](../../scripts/HBFSIM_CPU_BUILD.md), [pinned bpftime agent](../../scripts/BPFTIME_AGENT_REPRODUCTION.md), and [one-shot model execution](../../tools/native_ptx_reproduction/execution/README.md). The whole-Torch `auto_prepare_ptx.py` path is not a substitute for the exact scoped Embedding/Fill recipe; unsupported unrelated entries remain explicitly outside its claim.
