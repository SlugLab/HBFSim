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
adapters/vllm/run_timing.sh \
    --model /home/victoryang00/Qwen3-30B-A3B \
    --profile configs/profiles/nominal.json \
    --report-dir /dev/shm/hbfsim-vllm-timing \
    --num-prompts 1 --input-len 32 --output-len 8 \
    --max-model-len 64 --max-num-batched-tokens 64 \
    --hbf-parameter-regex \
      '^model\.layers\.0\.mlp\.experts\.w13_weight$' \
    --hbf-range-bytes 16384 --seed 0
```

The baseline is also the Triton compilation warmup. `run_timing.sh` recursively
stages every unique cached PTX variant by SHA256, exports the flat late-PTX
directory expected by bpftime, and installs Triton's load hook before the vLLM
engine is created. Four same-name `fused_moe_kernel` specializations therefore
remain distinct and are bound by `(original CUfunction, PTX SHA256, name)`.
The timing wrapper uses a dedicated one-program fused-MoE BPF object so the
late bootstrap sees its complete attach set before traversing PTX variants.

The wrapper exits `70` if the workload completes but produces no modeled
instrumented access. This is an evidence gate, not a workload failure. The
result, storage-registration manifest, and coverage log remain available in
the report directory for diagnosis.

## Exact-workload mode

Passing `--exact-profile` selects the Stage 2--4 exact-workload path. It is
deliberately different from timing mode:

```bash
adapters/vllm/run_timing.sh \
    --exact-profile /absolute/results/sm120-exact-profile.json \
    --exact-preheat \
    --model /home/victoryang00/Qwen3-30B-A3B \
    --profile configs/profiles/nominal.json \
    --report-dir /absolute/results/qwen-exact-workload \
    --num-prompts 1 --input-len 32 --output-len 8 \
    --max-model-len 64 --max-num-batched-tokens 64 --seed 0
```

The wrapper first runs the Qwen graph natively, without bpftime preload, and
records its tokens plus native preheat evidence. After that process exits, a
minimal no-Torch/no-vLLM process stages only the content-addressed sideband
probe, attaches only its BPF target, registers one 12,288-byte range, and runs
the calibrated 128-thread, depth-8, 384-iteration ordinary-load vector. The
process validates all 128 output checksums against a deterministic host oracle
before running the exact post-run gates. `result.json` records
`model_graph_fidelity: "native"`,
`model_storage_registered: false`, and
`exact_scope: "one_shot_sideband_probe"`.

This boundary is required because the selected fused-MoE weight path uses
legacy `cp.async`; it is neither a Stage 2 ordinary LD/ST/atomic future nor a
Stage 3 TensorMap/TMA operation. The exact-workload result proves deterministic
Qwen semantics plus exact-runtime activation. It does not claim that the Qwen
model graph itself was rewritten or exactly modeled.

## Current real-GPU result

On an NVIDIA RTX PRO 6000 Blackwell Server Edition, Qwen3-30B-A3B completed in
both modes with identical output token IDs. The bounded run selected one 16 KiB
prefix of a layer-0 MoE weight from 435 discovered storages. It produced 10,339
launch decisions, 2,304 fused-MoE launches, and 24 exact modeled launches.
Generation took 0.270 s in the matched baseline and 44.469 s with the nominal
reference path. This 164.70x end-to-end slowdown includes the current
per-warp GPU/host emulator overhead; it is not a prediction of HBF hardware.

See the [exact live-delay proof](../../docs/proofs/2026-08-11-vllm-exact-live-delay.md)
for the benchmark, CD8P comparison, durable artifacts, and claim boundary.
