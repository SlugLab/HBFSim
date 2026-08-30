#!/usr/bin/env bash
set -euo pipefail

HBFSIM_ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
LLAMA_CPP_SOURCE=${LLAMA_CPP_SOURCE:-/dev/shm/hbfsim-llama-src}
LLAMA_CPP_BUILD=${LLAMA_CPP_BUILD:-/dev/shm/hbfsim-llama-build}
HBFSIM_BUILD_DIR=${HBFSIM_BUILD_DIR:-/dev/shm/hbfsim-release-gpu13}
LLAMA_CPP_COMMIT=7ba604f1cb61cd14898138e9abc0b4ff2601f180

if [[ ! -d $HBFSIM_BUILD_DIR || -e $HBFSIM_BUILD_DIR/libcuda.so.1 ]]; then
    echo "llama adapter requires a test-free HBFSim build without fake libcuda.so.1" >&2
    exit 66
fi
if [[ ! -d $LLAMA_CPP_SOURCE/.git ]]; then
    git clone https://github.com/ggml-org/llama.cpp.git "$LLAMA_CPP_SOURCE"
fi
git -C "$LLAMA_CPP_SOURCE" checkout --detach "$LLAMA_CPP_COMMIT"
if git -C "$LLAMA_CPP_SOURCE" apply --recount --check \
    "$HBFSIM_ROOT/adapters/llama_cpp/0001-hbfsim-timing-adapter.patch"; then
    git -C "$LLAMA_CPP_SOURCE" apply --recount \
        "$HBFSIM_ROOT/adapters/llama_cpp/0001-hbfsim-timing-adapter.patch"
elif ! git -C "$LLAMA_CPP_SOURCE" apply --recount --reverse --check \
    "$HBFSIM_ROOT/adapters/llama_cpp/0001-hbfsim-timing-adapter.patch"; then
    echo "llama.cpp source has changes that conflict with the pinned adapter" >&2
    exit 65
fi

env HBFSIM_ROOT="$HBFSIM_ROOT" HBFSIM_BUILD_DIR="$HBFSIM_BUILD_DIR" \
    CC=${CC:-gcc-13} CXX=${CXX:-g++-13} CUDAHOSTCXX=${CUDAHOSTCXX:-g++-13} \
    cmake -S "$LLAMA_CPP_SOURCE" -B "$LLAMA_CPP_BUILD" \
        -DGGML_CUDA=ON -DGGML_NATIVE=OFF -DLLAMA_CURL=OFF \
        -DCMAKE_BUILD_TYPE=Release \
        -DCMAKE_CUDA_COMPILER=${CMAKE_CUDA_COMPILER:-/usr/local/cuda-13.0/bin/nvcc} \
        -DCUDAToolkit_ROOT=${CUDAToolkit_ROOT:-/usr/local/cuda-13.0} \
        -DCMAKE_CUDA_HOST_COMPILER=${CMAKE_CUDA_HOST_COMPILER:-/usr/bin/g++-13} \
        -DCMAKE_CUDA_ARCHITECTURES=${CMAKE_CUDA_ARCHITECTURES:-120}
cmake --build "$LLAMA_CPP_BUILD" --target llama-cli -j"${JOBS:-8}"
