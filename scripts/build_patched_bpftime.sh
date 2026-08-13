#!/usr/bin/env bash
set -euo pipefail

HBFSIM_ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
BPFTIME_SOURCE="$HBFSIM_ROOT/third_party/bpftime"
BPFTIME_PATCH1="$HBFSIM_ROOT/patches/bpftime/0001-exact-module-load-provenance.patch"
BPFTIME_PATCH2="$HBFSIM_ROOT/patches/bpftime/0002-sm120-aot-bundle-load.patch"
BPFTIME_PATCH3="$HBFSIM_ROOT/patches/bpftime/0003-libbpf-modern-libc-const.patch"
BPFTIME_PATCH4="$HBFSIM_ROOT/patches/bpftime/0004-honor-llvm-aot-cli-option.patch"
BPFTIME_PATCH5="$HBFSIM_ROOT/patches/bpftime/0005-cuda13-context-create.patch"
BPFTIME_COMMIT=ec26daecc8e787fb80fd95dd596a576404a5e36e

actual_commit=$(git -C "$BPFTIME_SOURCE" rev-parse HEAD)
if [[ $actual_commit != "$BPFTIME_COMMIT" ]]; then
    echo "build_patched_bpftime: expected $BPFTIME_COMMIT, found $actual_commit" >&2
    exit 65
fi
if ! source_status=$(git -C "$BPFTIME_SOURCE" status --porcelain=v1 \
    --untracked-files=all --ignore-submodules=none); then
    echo "build_patched_bpftime: unable to inspect pinned bpftime source worktree" >&2
    exit 65
fi
if [[ -n $source_status ]]; then
    echo "build_patched_bpftime: pinned bpftime source worktree is dirty" >&2
    exit 65
fi
if [[ ${1:-} == --check ]]; then
    if [[ $# -ne 1 ]]; then
        echo "usage: $0 --check" >&2
        exit 64
    fi
    check_root=$(mktemp -d "${TMPDIR:-/tmp}/hbfsim-bpftime-check.XXXXXX")
    cleanup_check() {
        rm -rf -- "$check_root"
    }
    trap cleanup_check EXIT
    cmake \
        -DBPFTIME_SOURCE="$BPFTIME_SOURCE" \
        -DPATCHES="$BPFTIME_PATCH1;$BPFTIME_PATCH2;$BPFTIME_PATCH3;$BPFTIME_PATCH4;$BPFTIME_PATCH5" \
        -DOUTPUT_SOURCE="$check_root/bpftime-hbfsim-src" \
        -P "$HBFSIM_ROOT/cmake/PreparePatchedBpftime.cmake" >/dev/null
    cleanup_check
    trap - EXIT
    echo "bpftime patch series applies to pinned source"
    exit 0
fi

build_dir=${1:-$HBFSIM_ROOT/build-bpftime-hbfsim}
if [[ $# -gt 0 ]]; then
    shift
fi
if [[ $build_dir != /* || $build_dir == / ]]; then
    echo "build_patched_bpftime: build directory must be a specific absolute path" >&2
    exit 64
fi
case $build_dir in
    "$HBFSIM_ROOT"|"$BPFTIME_SOURCE")
        echo "build_patched_bpftime: refusing unsafe build directory: $build_dir" >&2
        exit 64
        ;;
esac

source_copy="$build_dir/_deps/bpftime-hbfsim-src"
stamp="$build_dir/hbfsim-bpftime.provenance"
rm -f -- "$stamp"
cmake \
    -DBPFTIME_SOURCE="$BPFTIME_SOURCE" \
    -DPATCHES="$BPFTIME_PATCH1;$BPFTIME_PATCH2;$BPFTIME_PATCH3;$BPFTIME_PATCH4;$BPFTIME_PATCH5" \
    -DOUTPUT_SOURCE="$source_copy" \
    -P "$HBFSIM_ROOT/cmake/PreparePatchedBpftime.cmake"

cuda_root=${HBFSIM_CUDA_ROOT:-/usr/local/cuda-13.0}
if [[ $cuda_root != /* || ! -x $cuda_root/bin/nvcc ]]; then
    echo "build_patched_bpftime: CUDA root must contain an executable nvcc: $cuda_root" >&2
    exit 64
fi
cuda_root=$(cd "$cuda_root" && pwd -P)
if ! cuda_version_output=$("$cuda_root/bin/nvcc" --version) ||
   [[ $cuda_version_output != *"release 13.0"* ]]; then
    echo "build_patched_bpftime: exact AOT bridge requires CUDA release 13.0" >&2
    exit 65
fi
bpf_clang=${HBFSIM_BPF_CLANG:-/usr/bin/clang-19}
bpf_llvm_strip=${HBFSIM_BPF_LLVM_STRIP:-/usr/bin/llvm-strip-19}
for tool_spec in "BPF clang:$bpf_clang" "BPF llvm-strip:$bpf_llvm_strip"; do
    tool_label=${tool_spec%%:*}
    tool_path=${tool_spec#*:}
    if [[ $tool_path != /* || ! -x $tool_path ]]; then
        echo "build_patched_bpftime: $tool_label must be an executable absolute path: $tool_path" >&2
        exit 64
    fi
done
bpf_clang=$(cd "$(dirname -- "$bpf_clang")" && pwd -P)/$(basename -- "$bpf_clang")
bpf_llvm_strip=$(cd "$(dirname -- "$bpf_llvm_strip")" && pwd -P)/$(basename -- "$bpf_llvm_strip")
bpf_clang_real=$(readlink -f -- "$bpf_clang")
bpf_tool_bin=$(dirname -- "$bpf_clang_real")
if [[ ! -x $bpf_tool_bin/clang ||
      $(readlink -f -- "$bpf_tool_bin/clang") != "$bpf_clang_real" ]]; then
    echo "build_patched_bpftime: pinned clang directory lacks a matching clang command" >&2
    exit 65
fi
if ! bpf_clang_version=$("$bpf_clang" --version) ||
   [[ $bpf_clang_version != *"clang version 19.1.7"* ]]; then
    echo "build_patched_bpftime: bpftool requires clang version 19.1.7" >&2
    exit 65
fi
if ! bpf_strip_version=$("$bpf_llvm_strip" --version) ||
   [[ $bpf_strip_version != *"LLVM version 19.1.7"* ]]; then
    echo "build_patched_bpftime: bpftool requires llvm-strip version 19.1.7" >&2
    exit 65
fi
bpf_smoke=$(mktemp "${TMPDIR:-/tmp}/hbfsim-bpf-smoke.XXXXXX.o")
cleanup_bpf_smoke() {
    rm -f -- "$bpf_smoke"
}
trap cleanup_bpf_smoke EXIT
if ! "$bpf_clang" --target=bpf -x c -c /dev/null -o "$bpf_smoke" ||
   [[ ! -s $bpf_smoke ]]; then
    echo "build_patched_bpftime: pinned clang lacks a working BPF target" >&2
    exit 65
fi
cleanup_bpf_smoke
trap - EXIT
llvm_dir=${HBFSIM_LLVM_DIR:-/usr/lib/llvm-19/lib/cmake/llvm}
if [[ $llvm_dir != /* || ! -d $llvm_dir ||
      ! -r $llvm_dir/LLVMConfig.cmake ||
      ! -r $llvm_dir/LLVMConfigVersion.cmake ]]; then
    echo "build_patched_bpftime: LLVM_DIR must be an absolute LLVM package directory: $llvm_dir" >&2
    exit 64
fi
llvm_dir=$(cd "$llvm_dir" && pwd -P)
if ! grep -Fxq 'set(LLVM_PACKAGE_VERSION 19.1.7)' \
        "$llvm_dir/LLVMConfig.cmake"; then
    echo "build_patched_bpftime: llvmbpf requires LLVM package version 19.1.7" >&2
    exit 65
fi
for cmake_arg in "$@"; do
    if [[ $cmake_arg == -DLLVM_DIR=* ]]; then
        echo "build_patched_bpftime: set HBFSIM_LLVM_DIR instead of overriding LLVM_DIR" >&2
        exit 64
    fi
done
cmake -S "$source_copy" -B "$build_dir" \
    -DBPFTIME_ENABLE_CUDA_ATTACH=ON \
    -DBPFTIME_ENABLE_UNIT_TESTING=ON \
    -DBPFTIME_CUDA_ROOT="$cuda_root" \
    -DLLVM_DIR="$llvm_dir" \
    "$@"
PATH="$bpf_tool_bin:$PATH" CLANG="$bpf_clang" LLVM_STRIP="$bpf_llvm_strip" \
    cmake --build "$build_dir" -j "${HBFSIM_BUILD_JOBS:-2}"

for artifact in \
    "$build_dir/runtime/agent/libbpftime-agent.so" \
    "$build_dir/runtime/syscall-server/libbpftime-syscall-server.so"; do
    if [[ ! -r $artifact ]]; then
        echo "build_patched_bpftime: expected artifact is missing: $artifact" >&2
        exit 66
    fi
done

patch1_digest=$(sha256sum "$BPFTIME_PATCH1" | awk '{print $1}')
patch2_digest=$(sha256sum "$BPFTIME_PATCH2" | awk '{print $1}')
patch3_digest=$(sha256sum "$BPFTIME_PATCH3" | awk '{print $1}')
patch4_digest=$(sha256sum "$BPFTIME_PATCH4" | awk '{print $1}')
patch5_digest=$(sha256sum "$BPFTIME_PATCH5" | awk '{print $1}')
temporary_stamp=$(mktemp "$build_dir/.hbfsim-bpftime.provenance.XXXXXX")
cleanup() {
    rm -f -- "$temporary_stamp"
}
trap cleanup EXIT
printf 'bpftime_commit=%s\npatch_0001_sha256=%s\npatch_0002_sha256=%s\npatch_0003_sha256=%s\npatch_0004_sha256=%s\npatch_0005_sha256=%s\naot_bridge_version=1\ncuda_root=%s\ncuda_release=13.0\n' \
    "$BPFTIME_COMMIT" "$patch1_digest" "$patch2_digest" "$patch3_digest" "$patch4_digest" "$patch5_digest" "$cuda_root" \
    > "$temporary_stamp"
mv -f -- "$temporary_stamp" "$stamp"
trap - EXIT
echo "build_patched_bpftime: wrote $stamp"
