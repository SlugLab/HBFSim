#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd -- "${script_dir}/.." && pwd)"
build_dir="${HBFSIM_BUILD_DIR:-${repo_root}/build}"
cuda_enabled="${HBFSIM_ENABLE_CUDA:-ON}"
mqsim_enabled="${HBFSIM_ENABLE_MQSIM:-ON}"
llm_tests_enabled="${HBFSIM_ENABLE_LLM_TESTS:-OFF}"

# Only the two build dependencies are initialised.  The `paper` submodule
# points at a private Overleaf repository, is not part of the build, and
# would fail authentication for anyone outside the author group.
git -C "${repo_root}" submodule update --init --recursive \
    third_party/bpftime third_party/mqsim

if [[ "${cuda_enabled}" == "ON" ]]; then
    cuda_nvcc="/usr/local/cuda-12.8/bin/nvcc"
    if [[ ! -x "${cuda_nvcc}" ]]; then
        echo "CUDA is enabled but ${cuda_nvcc} is not executable" >&2
        exit 1
    fi
    "${cuda_nvcc}" --version
fi

cmake -S "${repo_root}" -B "${build_dir}" -G Ninja \
    -DHBFSIM_ENABLE_CUDA="${cuda_enabled}" \
    -DHBFSIM_ENABLE_MQSIM="${mqsim_enabled}" \
    -DHBFSIM_ENABLE_LLM_TESTS="${llm_tests_enabled}"
