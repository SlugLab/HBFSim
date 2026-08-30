# vLLM HBF Timing-Only Adapter Design

**Date:** 2026-08-10

**Status:** Approved direction; implementation pending

## 1. Goal

Add a vLLM model-loader plugin that registers finalized CUDA model-weight
storages as timing-only HBF ranges while retaining their real HBM backing. Run
the existing Qwen3-MoE workload on the real Blackwell GPU, preserve token
correctness, inject delay into instrumentable PTX/Triton accesses, and report
opaque accesses honestly as unmodeled rather than treating them as capacity
proof.

This milestone does not expose file-backed, unbacked capacity pointers to
vLLM. Capacity mode remains fail-closed until every kernel that can consume a
logical HBF pointer has proven coverage.

## 2. Validated Baseline and Compatibility Boundary

The initial live target is:

- vLLM 0.15.1;
- PyTorch 2.9.1+cu128;
- Triton 3.5.1;
- FlashInfer 0.6.1;
- Python 3.13.9;
- NVIDIA driver 595.84;
- CUDA toolkit 12.9;
- compute capability 12.0;
- `/home/victoryang00/Qwen3-30B-A3B` using
  `Qwen3MoeForCausalLM`.

The real-GPU baseline uses FlashInfer, GCC/G++ 13 for FlashInfer JIT builds,
and caches under `/dev/shm`. The previously requested `4 x (32 input + 32
output)` random workload completed at 2.53 requests/s, 161.97 total tokens/s,
and 80.98 output tokens/s. These numbers establish that the model and vLLM
stack run; they are not HBF timing results.

`adapters/vllm/compatibility.json` records the exact runtime versions, GPU
identity, adapter source hashes, selected attention backend, compiler paths,
and model hash used by each proof. A mismatch is reported before a proof run;
the adapter must not silently claim compatibility with a different stack.

## 3. Chosen Architecture

### 3.1 Loader plugin

`HbfSimModelLoader` implements vLLM's current `BaseModelLoader` contract and is
registered as load format `hbfsim` through `register_model_loader`.

The loader composes a `DefaultModelLoader` rather than copying vLLM's
safetensors discovery and weight-loading logic. It creates a delegate
`LoadConfig` whose underlying format defaults to `safetensors`; HBF-specific
keys are removed before construction of the delegate. Download and weight-load
operations are passed through to the delegate.

The adapter overrides `load_model`, not only `load_weights`. It first lets the
default loader finish model initialization, weight loading, quantization, and
`process_weights_after_loading`. Only then does it register the finalized CUDA
storages. This avoids registering allocations that post-load processing later
replaces.

The plugin is exposed through the `vllm.general_plugins` entry-point group so
that registration occurs in process 0, the engine-core process, and worker
processes. Registration is idempotent. Installed vLLM files are never edited.

### 3.2 Native bridge

`adapters/vllm/hbfsim_extension.cpp` builds a small shared library linked to
`hbfsim_core`. It exposes a narrow C ABI for:

- creating a timing session from a profile and report directory;
- registering a CUDA storage base and byte length as read-only timing HBF;
- closing the session;
- returning stable HBFSim error codes.

The Python layer loads this library with `ctypes` and obtains allocation
metadata from PyTorch's public tensor/storage APIs. This avoids a dependency on
PyTorch's unstable C++ ABI and avoids rebuilding vLLM or PyTorch.

### 3.3 Storage discovery and deduplication

After model loading, the adapter walks `model.named_parameters(recurse=True)`.
For each CUDA parameter it uses the full untyped storage base and storage byte
length, not the tensor view's offset and extent. Exact `(device, base, bytes)`
tuples are registered once, so tied weights and parameter views do not produce
overlapping duplicate ranges.

The adapter rejects zero-length storage, a null pointer, a non-CUDA finalized
parameter, mixed CUDA device ordinals within one session, overlapping
non-identical storage intervals, or a registration error. It emits a manifest
containing parameter count, unique storage count, registered bytes, aliases,
device ordinal, and excluded non-parameter buffers.

Temporary tensors, KV cache, activations, CUDA graphs, and general model
buffers remain ordinary unregistered HBM allocations in this milestone.

### 3.4 Lifetime

One HBFSim timing context is created per vLLM GPU worker after its CUDA context
is current. The timing session is attached to the returned model, rather than
to the short-lived loader object. The worker shutdown path closes it before the
CUDA context is destroyed. A guarded finalizer is a fallback only; proof runs
must exercise explicit close and record successful retirement.

Timing ranges currently retire as a group when their HBFSim context is
destroyed. Per-storage removal is not added by this milestone.

## 4. Mode-Aware Launch Gate

The current launch gate treats every registered range as if it could be an
unbacked capacity pointer and therefore rejects an uninstrumented or opaque
consumer. That policy is required for capacity mode but unnecessarily prevents
a physically backed timing pointer from executing.

The launch-gate ABI is extended with a range backing policy:

- `TIMING_BACKED`: a valid, physically backed CUDA/HBM address;
- `CAPACITY_UNBACKED`: a logical address that is unsafe until rewritten.

The new ABI version carries the policy during range registration. The shared
library continues to expose the previous ABI version for compatibility. A new
runtime prefers the new ABI. Falling back to the old ABI retains the existing
strict fail-closed behavior; it never silently enables opaque timing access.

Coverage decisions become:

| Pointer and launch path | Decision | Reporting |
| --- | --- | --- |
| No HBF range | Allow | ordinary launch |
| Timing-backed pointer, instrumented supported PTX | Allow | modeled and delayed |
| Timing-backed pointer, opaque/cubin-only/unsupported operation | Allow | `opaque_unmodeled` |
| Capacity-unbacked pointer, instrumented supported PTX | Allow | modeled and resolved |
| Capacity-unbacked pointer, opaque/cubin-only/unsupported operation | Reject before launch | `UNSUPPORTED` with module/kernel |
| Uninspectable launch while any capacity range is active | Reject before launch | `uninspectable_launch_path` |
| Uninspectable launch with timing ranges only | Allow | `opaque_unmodeled` |

Mixed timing and capacity registrations always use the stricter decision when
the gate cannot prove which range an argument may reference. Existing capacity
and opaque-rejection tests remain fail-closed.

Allowing `TIMING_BACKED` through an opaque kernel is a safety decision, not a
claim that the access was modeled. Such launches increment separate counters
and contribute no fabricated HBF delay.

## 5. Runtime Flow

1. The runner sets the FlashInfer compiler to GCC/G++ 13 and places temporary,
   TorchInductor, Triton, vLLM, FlashInfer, and CUDA compute caches on a
   filesystem with sufficient capacity.
2. `run_with_bpftime.sh` starts vLLM with the PTX transformer and launch gate
   preloaded.
3. vLLM loads the general plugin in every process and selects load format
   `hbfsim`.
4. The default loader creates and finalizes the model on the worker's GPU.
5. The HBF loader creates a rank-specific timing context, discovers unique
   parameter storages, and registers them read-only.
6. Instrumentable PTX/Triton loads consult the HBF resolver and inject profile
   delay. Opaque consumers execute against the original HBM pointer and are
   recorded as unmodeled.
7. The runner performs deterministic generation, synchronizes the GPU, closes
   the engine/session, and writes result, registration, coverage, timing, and
   compatibility artifacts.

## 6. Configuration

HBF loader configuration is supplied through vLLM's model-loader extra config
and normalized into a manifest. Required fields are `profile_path` and
`report_dir`. Supported optional fields include:

- `underlying_load_format` (default `safetensors`);
- `ring_capacity`;
- `request_timeout_ns`;
- `require_modeled_accesses` (true for proof runs);
- `allow_opaque_timing` (true for timing-only runs, invalid for capacity);
- explicit compiler and cache paths recorded by the runner.

Named HBF profiles remain repository profiles such as `conservative`,
`nominal`, and `aggressive`. The adapter does not reinterpret their timing
values.

## 7. Failure Handling

Adapter setup is transactional. If any storage registration fails, the timing
context is retired and model execution does not begin. Error output includes
the HBFSim status, parameter/storage identity, device, address interval, and
report directory without printing tensor contents.

The runner fails the proof when:

- the plugin is absent in a worker;
- the native ABI or compatibility lock mismatches;
- no CUDA parameter storage is registered;
- explicit session retirement fails to complete;
- baseline and timing token IDs differ;
- the timing run reports zero modeled HBF accesses when
  `require_modeled_accesses` is enabled;
- a capacity-unbacked pointer is allowed through an opaque path;
- required artifacts are missing or internally inconsistent.

Opaque timing launches are not setup failures. They are a visible partial
coverage result and must never be counted as delayed accesses.

## 8. Test and Proof Strategy

### 8.1 Unit and integration tests

Tests are written before implementation and cover:

- plugin registration and idempotence;
- delegation to the current `DefaultModelLoader` API;
- post-finalization registration order;
- storage/view alias deduplication;
- mixed-device and overlap rejection;
- transactional cleanup after partial registration failure;
- explicit model/session lifetime;
- mode-aware gate decisions for instrumented, opaque, cubin-only,
  unsupported, aggregate, legacy, and mixed-mode paths;
- preservation of existing capacity fail-closed behavior;
- manifest and compatibility validation.

Tests that do not use a real GPU are labeled non-live. Fake-driver or mocked
loader success does not count as live vLLM or delay proof.

### 8.2 Real-GPU proof

The live proof uses the same fixed model, prompt/input generation, seed,
greedy decoding, request count, input length, output length, attention backend,
and cache warmup for baseline and HBF runs. It records at least:

- baseline;
- timing with the nominal profile;
- timing with the conservative and aggressive profiles;
- an intentionally opaque timing path that runs and is counted as unmodeled;
- an isolated capacity/opaque case that is still rejected before execution.

Acceptance requires bit-exact token IDs, nonzero registered storage bytes,
nonzero modeled HBF requests for the supported timing run, zero capacity unsafe
launches, explicit coverage counts, and complete artifacts. Throughput and
latency are reported with warm and cold-cache conditions separated. Profile
ordering is interpreted only for modeled portions of the workload; partial
opaque coverage is disclosed alongside every performance number.

## 9. Non-Goals

This milestone does not:

- provide vLLM capacity mode or larger-than-VRAM model execution;
- stage logical capacity pointers into HBM behind the gate;
- claim delay for FlashInfer, cuBLAS, cuBLASLt, or other opaque kernels;
- register KV cache, activations, or temporary workspace;
- modify installed vLLM source;
- treat build, import, fake-driver, or CPU tests as real-GPU proof.
