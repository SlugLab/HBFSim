#!/usr/bin/env bash
set -euo pipefail

HBFSIM_ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
HBFSIM_VLLM_BUILD=${HBFSIM_VLLM_BUILD:-/dev/shm/hbfsim-vllm-gpu13}
HBFSIM_VLLM_PLUGIN_SITE=${HBFSIM_VLLM_PLUGIN_SITE:-/dev/shm/hbfsim-vllm-plugin}
HBFSIM_BUILD_CUDA_ROOT=${HBFSIM_BUILD_CUDA_ROOT:-/usr/local/cuda-13}
HBFSIM_BUILD_NVCC=${HBFSIM_BUILD_NVCC:-/usr/local/cuda-13.0/bin/nvcc}
HBFSIM_BUILD_CC=${HBFSIM_BUILD_CC:-/usr/bin/gcc-15}
HBFSIM_BUILD_CXX=${HBFSIM_BUILD_CXX:-/usr/bin/g++-15}
HBFSIM_BUILD_ARCH=${HBFSIM_BUILD_ARCH:-120}
HBFSIM_BUILD_PTXAS=${HBFSIM_BUILD_PTXAS:-/usr/local/cuda-13.0/bin/ptxas}
HBFSIM_BUILD_CLANG=${HBFSIM_BUILD_CLANG:-/usr/bin/clang-20}
HBFSIM_BUILD_OBJDUMP=${HBFSIM_BUILD_OBJDUMP:-/usr/bin/llvm-objdump-20}
HBFSIM_BUILD_NM=${HBFSIM_BUILD_NM:-/usr/lib/llvm-20/bin/llvm-nm}
HBFSIM_BUILD_PYTHON=${HBFSIM_BUILD_PYTHON:-python3.13}

for value in "$HBFSIM_VLLM_BUILD" "$HBFSIM_VLLM_PLUGIN_SITE" \
             "$HBFSIM_BUILD_CUDA_ROOT" "$HBFSIM_BUILD_NVCC" \
             "$HBFSIM_BUILD_CC" "$HBFSIM_BUILD_CXX" \
             "$HBFSIM_BUILD_PTXAS" "$HBFSIM_BUILD_CLANG" \
             "$HBFSIM_BUILD_OBJDUMP" "$HBFSIM_BUILD_NM"; do
    if [[ $value != /* ]]; then
        echo "build.sh: all configured paths must be absolute: $value" >&2
        exit 64
    fi
done

mkdir -p "$HBFSIM_VLLM_BUILD" "$HBFSIM_VLLM_PLUGIN_SITE"
CC="$HBFSIM_BUILD_CC" CXX="$HBFSIM_BUILD_CXX" \
cmake -S "$HBFSIM_ROOT" -B "$HBFSIM_VLLM_BUILD" \
    -DHBFSIM_ENABLE_CUDA=ON \
    -DHBFSIM_ENABLE_MQSIM=ON \
    -DCUDAToolkit_ROOT="$HBFSIM_BUILD_CUDA_ROOT" \
    -DCMAKE_CUDA_COMPILER="$HBFSIM_BUILD_NVCC" \
    -DCMAKE_CUDA_HOST_COMPILER="$HBFSIM_BUILD_CXX" \
    -DCMAKE_CUDA_ARCHITECTURES="$HBFSIM_BUILD_ARCH" \
    -DHBFSIM_PTXAS_EXECUTABLE="$HBFSIM_BUILD_PTXAS" \
    -DHBFSIM_CLANG_EXECUTABLE="$HBFSIM_BUILD_CLANG" \
    -DHBFSIM_LLVM_OBJDUMP_EXECUTABLE="$HBFSIM_BUILD_OBJDUMP" \
    -DHBFSIM_NM_EXECUTABLE="$HBFSIM_BUILD_NM" \
    -DHBFSIM_PYTHON3_EXECUTABLE="$HBFSIM_BUILD_PYTHON" \
    -DCMAKE_BUILD_TYPE=RelWithDebInfo
cmake --build "$HBFSIM_VLLM_BUILD" \
    --target hbfsimd hbfsim_launch_gate hbfsim_vllm_extension \
             ptxpass_hbf_plugin hbfsim_bpftime_attach_loader \
             hbfsim_coverage_probe hbfsim_vllm_probe -j1

TMPDIR=${TMPDIR:-/dev/shm} "$HBFSIM_BUILD_PYTHON" -m pip install \
    --no-build-isolation --no-deps --upgrade \
    --target "$HBFSIM_VLLM_PLUGIN_SITE" "$HBFSIM_ROOT/adapters/vllm"

printf 'export PYTHONPATH=%q${PYTHONPATH:+:$PYTHONPATH}\n' \
    "$HBFSIM_VLLM_PLUGIN_SITE"
printf 'export HBFSIM_VLLM_EXTENSION=%q\n' \
    "$HBFSIM_VLLM_BUILD/libhbfsim_vllm_extension.so"
printf 'export HBFSIM_BUILD_DIR=%q\n' "$HBFSIM_VLLM_BUILD"
printf 'export HBFSIM_DAEMON_PATH=%q\n' "$HBFSIM_VLLM_BUILD/hbfsimd"
