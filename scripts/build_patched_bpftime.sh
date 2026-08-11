#!/usr/bin/env bash
set -euo pipefail

HBFSIM_ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
BPFTIME_SOURCE="$HBFSIM_ROOT/third_party/bpftime"
BPFTIME_PATCH="$HBFSIM_ROOT/patches/bpftime/0001-exact-module-load-provenance.patch"
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
if ! git -C "$BPFTIME_SOURCE" apply --check "$BPFTIME_PATCH"; then
    echo "build_patched_bpftime: patch does not apply to pinned source" >&2
    exit 65
fi
if [[ ${1:-} == --check ]]; then
    if [[ $# -ne 1 ]]; then
        echo "usage: $0 --check" >&2
        exit 64
    fi
    echo "bpftime patch applies to pinned source"
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
cmake \
    -DBPFTIME_SOURCE="$BPFTIME_SOURCE" \
    -DPATCH="$BPFTIME_PATCH" \
    -DOUTPUT_SOURCE="$source_copy" \
    -P "$HBFSIM_ROOT/cmake/PreparePatchedBpftime.cmake"

cuda_root=${HBFSIM_CUDA_ROOT:-/usr/local/cuda-12.8}
cmake -S "$source_copy" -B "$build_dir" \
    -DBPFTIME_ENABLE_CUDA_ATTACH=ON \
    -DBPFTIME_ENABLE_UNIT_TESTING=ON \
    -DBPFTIME_CUDA_ROOT="$cuda_root" \
    "$@"
cmake --build "$build_dir" -j "${HBFSIM_BUILD_JOBS:-2}"

for artifact in \
    "$build_dir/runtime/agent/libbpftime-agent.so" \
    "$build_dir/runtime/syscall-server/libbpftime-syscall-server.so"; do
    if [[ ! -r $artifact ]]; then
        echo "build_patched_bpftime: expected artifact is missing: $artifact" >&2
        exit 66
    fi
done

patch_digest=$(sha256sum "$BPFTIME_PATCH" | awk '{print $1}')
stamp="$build_dir/hbfsim-bpftime.provenance"
temporary_stamp=$(mktemp "$build_dir/.hbfsim-bpftime.provenance.XXXXXX")
cleanup() {
    rm -f -- "$temporary_stamp"
}
trap cleanup EXIT
printf 'bpftime_commit=%s\npatch_sha256=%s\nbridge_version=2\n' \
    "$BPFTIME_COMMIT" "$patch_digest" > "$temporary_stamp"
mv -f -- "$temporary_stamp" "$stamp"
trap - EXIT
echo "build_patched_bpftime: wrote $stamp"
