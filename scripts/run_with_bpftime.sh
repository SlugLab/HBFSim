#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 2 || $1 != "--" ]]; then
    echo "usage: $0 -- command [args ...]" >&2
    exit 64
fi
shift

HBFSIM_ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
BPFTIME_AGENT="$HBFSIM_ROOT/third_party/bpftime/build/runtime/agent/libbpftime-agent.so"
HBFSIM_GATE="$HBFSIM_ROOT/build/libhbfsim_launch_gate.so"
HBFSIM_PASS="$HBFSIM_ROOT/build/libptxpass_hbf.so"

for library in "$BPFTIME_AGENT" "$HBFSIM_GATE" "$HBFSIM_PASS"; do
    if [[ ! -r $library ]]; then
        echo "run_with_bpftime: required library is missing: $library" >&2
        exit 66
    fi
done

export BPFTIME_PTXPASS_DIR="$HBFSIM_ROOT/configs/ptxpass"
export BPFTIME_PTXPASS_LIBRARIES="$HBFSIM_PASS"
export BPFTIME_CUDA_ROOT=/usr/local/cuda-12.8
export HBFSIM_COVERAGE_PATH="${HBFSIM_COVERAGE_PATH:-$PWD/coverage.json}"
export HBFSIM_PASS_MANIFEST_PATH="${HBFSIM_PASS_MANIFEST_PATH:-$PWD/hbfsim-pass-manifests.jsonl}"
export LD_PRELOAD="$BPFTIME_AGENT:$HBFSIM_GATE${LD_PRELOAD:+:$LD_PRELOAD}"

rm -f -- "$HBFSIM_COVERAGE_PATH" "$HBFSIM_PASS_MANIFEST_PATH"
exec "$@"
