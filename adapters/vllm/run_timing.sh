#!/usr/bin/env bash
set -euo pipefail

HBFSIM_ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
HBFSIM_VLLM_CACHE=${HBFSIM_VLLM_CACHE:-/dev/shm/hbfsim-vllm-live-cache}
HBFSIM_TRITON_PTX_STAGE=${HBFSIM_TRITON_PTX_STAGE:-/dev/shm/hbfsim-vllm-ptx-stage}
triton_cache="$HBFSIM_VLLM_CACHE/triton"
pass_library="${HBFSIM_BUILD_DIR:?HBFSIM_BUILD_DIR is required}/libptxpass_hbf.so"
prestaged_manifest="$HBFSIM_TRITON_PTX_STAGE/pass-manifests.jsonl"

if [[ ! -d $triton_cache ]]; then
    echo "run_timing.sh: Triton cache is missing; run the baseline warmup first: $triton_cache" >&2
    exit 64
fi

python3 "$HBFSIM_ROOT/adapters/vllm/prepare_triton_ptx.py" \
    --cache-root "$triton_cache" \
    --staging-dir "$HBFSIM_TRITON_PTX_STAGE" \
    --kernel fused_moe_kernel \
    --pass-library "$pass_library" \
    --pass-manifest "$prestaged_manifest" \
    --quiet

export BPFTIME_CUDA_LATE_PTX_DIR="$HBFSIM_TRITON_PTX_STAGE"
export BPFTIME_CUDA_LATE_PTX_PREPATCHED=1
export HBFSIM_BPFTIME_PROBE="${HBFSIM_BPFTIME_PROBE:-${HBFSIM_BUILD_DIR:?HBFSIM_BUILD_DIR is required}/vllm_fused_moe_probe.bpf.o}"
export HBFSIM_VLLM_EXTENSION="${HBFSIM_VLLM_EXTENSION:-$HBFSIM_BUILD_DIR/libhbfsim_vllm_extension.so}"
export HBFSIM_COVERAGE_PATH="${HBFSIM_COVERAGE_PATH:-$PWD/coverage.jsonl}"
export HBFSIM_PASS_MANIFEST_PATH="${HBFSIM_PASS_MANIFEST_PATH:-$PWD/pass-manifests.jsonl}"
export HBFSIM_PRESTAGED_PASS_MANIFEST_PATH="$prestaged_manifest"

exec "$HBFSIM_ROOT/scripts/run_with_bpftime.sh" -- \
    python3 "$HBFSIM_ROOT/adapters/vllm/run.py" --mode timing "$@"
