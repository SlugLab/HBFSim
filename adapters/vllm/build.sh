#!/usr/bin/env bash
set -euo pipefail

HBFSIM_ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
HBFSIM_VLLM_BUILD=${HBFSIM_VLLM_BUILD:-/dev/shm/hbfsim-vllm-gpu13}
HBFSIM_VLLM_PLUGIN_SITE=${HBFSIM_VLLM_PLUGIN_SITE:-/dev/shm/hbfsim-vllm-plugin}
HBFSIM_BUILD_CUDA_ROOT=${HBFSIM_BUILD_CUDA_ROOT:-/usr/local/cuda-13}
HBFSIM_BUILD_NVCC=${HBFSIM_BUILD_NVCC:-/usr/local/cuda-13.0/bin/nvcc}
HBFSIM_BUILD_CC=${HBFSIM_BUILD_CC:-/usr/bin/gcc-15}
HBFSIM_BUILD_CXX=${HBFSIM_BUILD_CXX:-/usr/bin/g++-15}

for value in "$HBFSIM_VLLM_BUILD" "$HBFSIM_VLLM_PLUGIN_SITE" \
             "$HBFSIM_BUILD_CUDA_ROOT" "$HBFSIM_BUILD_NVCC" \
             "$HBFSIM_BUILD_CC" "$HBFSIM_BUILD_CXX"; do
    if [[ $value != /* ]]; then
        echo "build.sh: all configured paths must be absolute: $value" >&2
        exit 64
    fi
done

mkdir -p "$HBFSIM_VLLM_BUILD" "$HBFSIM_VLLM_PLUGIN_SITE"
CC="$HBFSIM_BUILD_CC" CXX="$HBFSIM_BUILD_CXX" \
cmake -S "$HBFSIM_ROOT" -B "$HBFSIM_VLLM_BUILD" \
    -DHBFSIM_ENABLE_CUDA=ON \
    -DHBFSIM_ENABLE_MQSIM=OFF \
    -DCUDAToolkit_ROOT="$HBFSIM_BUILD_CUDA_ROOT" \
    -DCMAKE_CUDA_COMPILER="$HBFSIM_BUILD_NVCC" \
    -DCMAKE_CUDA_HOST_COMPILER="$HBFSIM_BUILD_CXX" \
    -DCMAKE_BUILD_TYPE=RelWithDebInfo
cmake --build "$HBFSIM_VLLM_BUILD" \
    --target hbfsimd hbfsim_launch_gate hbfsim_vllm_extension \
             ptxpass_hbf_plugin hbfsim_bpftime_attach_loader \
             hbfsim_coverage_probe hbfsim_vllm_probe -j2

TMPDIR=${TMPDIR:-/dev/shm} python3 -m pip install --no-deps --upgrade \
    --target "$HBFSIM_VLLM_PLUGIN_SITE" "$HBFSIM_ROOT/adapters/vllm"

printf 'export PYTHONPATH=%q${PYTHONPATH:+:$PYTHONPATH}\n' \
    "$HBFSIM_VLLM_PLUGIN_SITE"
printf 'export HBFSIM_VLLM_EXTENSION=%q\n' \
    "$HBFSIM_VLLM_BUILD/libhbfsim_vllm_extension.so"
printf 'export HBFSIM_BUILD_DIR=%q\n' "$HBFSIM_VLLM_BUILD"
printf 'export HBFSIM_DAEMON_PATH=%q\n' "$HBFSIM_VLLM_BUILD/hbfsimd"
