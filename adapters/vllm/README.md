# vLLM timing adapter

This adapter lets HBFSim observe a real vLLM model without replacing its CUDA
allocations. vLLM loads the model normally, then the adapter registers every
final CUDA parameter storage as an explicit, physically backed HBF timing
range. The launch gate can therefore distinguish ordinary HBM launches,
instrumented HBF accesses, and opaque kernels whose machine code could not be
rewritten.

The distinction matters: range registration and workload completion prove the
adapter path, while an HBF delay claim additionally requires at least one PTX
pass manifest and one coverage decision with `modeled: true`. Cubin-only
FlashInfer or Triton kernels may run in timing mode, but are reported as
`opaque_unmodeled_timing` and do not count as delay injection.

## Build

The validated stack is recorded in [compatibility.json](compatibility.json).
Build HBFSim and install the loader plugin into a temporary site directory:

```bash
adapters/vllm/build.sh
export PYTHONPATH=/dev/shm/hbfsim-vllm-plugin${PYTHONPATH:+:$PYTHONPATH}
export HBFSIM_VLLM_EXTENSION=/dev/shm/hbfsim-vllm-gpu13/libhbfsim_vllm_extension.so
export HBFSIM_BUILD_DIR=/dev/shm/hbfsim-vllm-gpu13
export HBFSIM_DAEMON_PATH=/dev/shm/hbfsim-vllm-gpu13/hbfsimd
export HBFSIM_BPFTIME_BUILD_DIR=/dev/shm/hbfsim-bpftime-vllm-gcc14
```

`build.sh` defaults to CUDA 13 and GCC/G++ 15 for the HBFSim native targets.
The vLLM process uses GCC/G++ 13 for FlashInfer JIT compatibility. Cache and
temporary files default to `/dev/shm`.

## Reproduce the comparison

Use the same seed, prompt token IDs, model, and single-process vLLM engine for
both runs:

```bash
python3 adapters/vllm/run.py \
  --mode baseline \
  --model /home/victoryang00/Qwen3-30B-A3B \
  --report-dir /dev/shm/hbfsim-vllm-baseline \
  --num-prompts 4 --input-len 32 --output-len 32 \
  --max-model-len 256 --seed 0

mkdir -p /dev/shm/hbfsim-vllm-timing
HBFSIM_COVERAGE_PATH=/dev/shm/hbfsim-vllm-timing/coverage.jsonl \
HBFSIM_PASS_MANIFEST_PATH=/dev/shm/hbfsim-vllm-timing/pass-manifests.jsonl \
scripts/run_with_bpftime.sh -- \
  python3 adapters/vllm/run.py \
    --mode timing \
    --model /home/victoryang00/Qwen3-30B-A3B \
    --profile configs/profiles/nominal.json \
    --report-dir /dev/shm/hbfsim-vllm-timing \
    --num-prompts 4 --input-len 32 --output-len 32 \
    --max-model-len 256 --seed 0
```

The wrapper exits `70` if the workload completes but produces no modeled
instrumented access. This is an evidence gate, not a workload failure. The
result, storage-registration manifest, and coverage log remain available in
the report directory for diagnosis.

## Current real-GPU result

On an NVIDIA RTX PRO 6000 Blackwell Server Edition, Qwen3-30B-A3B completed in
both modes with identical output token IDs. All 435 unique model storages
(61,064,245,248 bytes) were registered. The timing run observed 23,210 launch
decisions, including 10,584 launches touching registered timing ranges, but
all were opaque and unmodeled because this workload loaded cubins rather than
rewritable PTX. Two timing runs measured a 37.15% to 51.30% throughput
reduction. This is variable adapter, interposition, and coverage overhead; it
is not HBF media latency.

See the [live proof](../../docs/proofs/2026-08-11-vllm-timing-adapter.md) for
the benchmark table and exact claim boundary.
