#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 2 || $1 != "--" ]]; then
    echo "usage: $0 -- command [args ...]" >&2
    exit 64
fi
shift

HBFSIM_ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)

canonical_build_dir() {
    local label=$1
    local value=$2
    if [[ $value != /* || ! -d $value ]]; then
        echo "run_with_bpftime: $label must be an existing absolute directory: $value" >&2
        exit 64
    fi
    (cd "$value" && pwd -P)
}

HBFSIM_BUILD_DIR=$(canonical_build_dir HBFSIM_BUILD_DIR \
    "${HBFSIM_BUILD_DIR:-$HBFSIM_ROOT/build}")
HBFSIM_BPFTIME_BUILD_DIR=$(canonical_build_dir HBFSIM_BPFTIME_BUILD_DIR \
    "${HBFSIM_BPFTIME_BUILD_DIR:-$HBFSIM_ROOT/third_party/bpftime/build}")
HBFSIM_CUDA_ROOT=${HBFSIM_CUDA_ROOT:-/usr/local/cuda-12.8}
if [[ $HBFSIM_CUDA_ROOT != /* || ! -d $HBFSIM_CUDA_ROOT ]]; then
    echo "run_with_bpftime: HBFSIM_CUDA_ROOT must be an existing absolute directory: $HBFSIM_CUDA_ROOT" >&2
    exit 64
fi
HBFSIM_CUDA_ROOT=$(cd "$HBFSIM_CUDA_ROOT" && pwd -P)

BPFTIME_AGENT="$HBFSIM_BPFTIME_BUILD_DIR/runtime/agent/libbpftime-agent.so"
BPFTIME_SERVER="$HBFSIM_BPFTIME_BUILD_DIR/runtime/syscall-server/libbpftime-syscall-server.so"
HBFSIM_GATE="$HBFSIM_BUILD_DIR/libhbfsim_launch_gate.so"
HBFSIM_PASS="$HBFSIM_BUILD_DIR/libptxpass_hbf.so"
HBFSIM_BPFTIME_LOADER=${HBFSIM_BPFTIME_LOADER:-$HBFSIM_BUILD_DIR/hbfsim_bpftime_attach_loader}
HBFSIM_BPFTIME_PROBE=${HBFSIM_BPFTIME_PROBE:-$HBFSIM_BUILD_DIR/coverage_probe.bpf.o}

for library in "$BPFTIME_AGENT" "$BPFTIME_SERVER" "$HBFSIM_GATE" "$HBFSIM_PASS"; do
    if [[ ! -r $library ]]; then
        echo "run_with_bpftime: required library is missing: $library" >&2
        exit 66
    fi
done
if [[ $HBFSIM_BPFTIME_LOADER != /* || ! -x $HBFSIM_BPFTIME_LOADER ]]; then
    echo "run_with_bpftime: attach loader must be an executable absolute path: $HBFSIM_BPFTIME_LOADER" >&2
    exit 66
fi
if [[ $HBFSIM_BPFTIME_PROBE != /* || ! -r $HBFSIM_BPFTIME_PROBE ]]; then
    echo "run_with_bpftime: attach probe must be a readable absolute path: $HBFSIM_BPFTIME_PROBE" >&2
    exit 66
fi
if [[ ! ${HBFSIM_ATTACH_TIMEOUT_MS:-5000} =~ ^[1-9][0-9]*$ ]]; then
    echo "run_with_bpftime: HBFSIM_ATTACH_TIMEOUT_MS must be a positive integer" >&2
    exit 64
fi

export BPFTIME_PTXPASS_DIR="$HBFSIM_ROOT/configs/ptxpass"
export BPFTIME_PTXPASS_LIBRARIES="$HBFSIM_PASS"
export BPFTIME_CUDA_ROOT="$HBFSIM_CUDA_ROOT"
export HBFSIM_COVERAGE_PATH="${HBFSIM_COVERAGE_PATH:-$PWD/coverage.json}"
export HBFSIM_PASS_MANIFEST_PATH="${HBFSIM_PASS_MANIFEST_PATH:-$PWD/hbfsim-pass-manifests.jsonl}"

original_preload=${LD_PRELOAD:-}
ready_file=$(mktemp "${TMPDIR:-/tmp}/hbfsim-bpftime-ready.XXXXXX")
loader_pid=
cleanup() {
    if [[ -n ${loader_pid:-} ]] && kill -0 "$loader_pid" 2>/dev/null; then
        kill "$loader_pid" 2>/dev/null || true
        wait "$loader_pid" 2>/dev/null || true
    fi
    rm -f -- "$ready_file"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

rm -f -- "$HBFSIM_COVERAGE_PATH" "$HBFSIM_PASS_MANIFEST_PATH"
LD_PRELOAD="$BPFTIME_SERVER${original_preload:+:$original_preload}" \
    "$HBFSIM_BPFTIME_LOADER" "$HBFSIM_BPFTIME_PROBE" "$ready_file" &
loader_pid=$!

deadline=$(( $(date +%s%3N) + HBFSIM_ATTACH_TIMEOUT_MS ))
while ! grep -qx 'HBFSIM_BPFTIME_ATTACH_READY v1' "$ready_file" 2>/dev/null; do
    if ! kill -0 "$loader_pid" 2>/dev/null; then
        wait "$loader_pid" || true
        echo "run_with_bpftime: bpftime loader exited before CUDA attach readiness" >&2
        exit 67
    fi
    if (( $(date +%s%3N) >= deadline )); then
        echo "run_with_bpftime: timed out waiting for bpftime CUDA attach readiness" >&2
        exit 68
    fi
    sleep 0.01
done
if ! grep -qx 'shm=bpftime' "$ready_file" ||
   ! grep -qx 'attach_type=8' "$ready_file" ||
   ! grep -Eq '^entries=[1-9][0-9]*$' "$ready_file"; then
    echo "run_with_bpftime: loader readiness did not verify SHM and a CUDA attach entry" >&2
    exit 69
fi

export LD_PRELOAD="$BPFTIME_AGENT:$HBFSIM_GATE${original_preload:+:$original_preload}"
set +e
"$@"
status=$?
set -e
if (( status != 0 )); then
    exit "$status"
fi

if ! python3 -c 'import json,sys; lines=[x for x in open(sys.argv[1]) if x.strip()]; assert lines; manifests=[json.loads(x) for x in lines]; assert all(m.get("module_id") and m.get("kernel") for m in manifests); coverage=json.load(open(sys.argv[2])); assert isinstance(coverage.get("decisions"),list) and coverage["decisions"]' "$HBFSIM_PASS_MANIFEST_PATH" "$HBFSIM_COVERAGE_PATH" 2>/dev/null; then
    echo "run_with_bpftime: target produced no valid instrumentation activation artifacts" >&2
    exit 70
fi
