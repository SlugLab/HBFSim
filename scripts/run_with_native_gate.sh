#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 2 || $1 != "--" ]]; then
    echo "usage: $0 -- command [args ...]" >&2
    exit 64
fi
shift

HBFSIM_ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
HBFSIM_BUILD_DIR=${HBFSIM_BUILD_DIR:-$HBFSIM_ROOT/build}
if [[ $HBFSIM_BUILD_DIR != /* || ! -d $HBFSIM_BUILD_DIR ]]; then
    echo "run_with_native_gate: HBFSIM_BUILD_DIR must be an existing absolute directory" >&2
    exit 64
fi
HBFSIM_BUILD_DIR=$(cd "$HBFSIM_BUILD_DIR" && pwd -P)
HBFSIM_GATE="$HBFSIM_BUILD_DIR/libhbfsim_launch_gate.so"
if [[ ! -r $HBFSIM_GATE ]]; then
    echo "run_with_native_gate: launch gate is missing: $HBFSIM_GATE" >&2
    exit 66
fi

export HBFSIM_COVERAGE_PATH="${HBFSIM_COVERAGE_PATH:-$PWD/coverage.json}"
export HBFSIM_PASS_MANIFEST_PATH="${HBFSIM_PASS_MANIFEST_PATH:-$PWD/hbfsim-pass-manifests.jsonl}"
mkdir -p -- "$(dirname -- "$HBFSIM_COVERAGE_PATH")"     "$(dirname -- "$HBFSIM_PASS_MANIFEST_PATH")"
rm -f -- "$HBFSIM_COVERAGE_PATH" "$HBFSIM_PASS_MANIFEST_PATH"
if [[ -n ${HBFSIM_PRESTAGED_PASS_MANIFEST_PATH:-} ]]; then
    if [[ $HBFSIM_PRESTAGED_PASS_MANIFEST_PATH != /* ||
          ! -r $HBFSIM_PRESTAGED_PASS_MANIFEST_PATH ]]; then
        echo "run_with_native_gate: prestaged pass manifest must be a readable absolute path" >&2
        exit 66
    fi
    cp -- "$HBFSIM_PRESTAGED_PASS_MANIFEST_PATH"         "$HBFSIM_PASS_MANIFEST_PATH"
fi

export HBFSIM_NATIVE_TRITON_BINDING=1
export HBFSIM_LAUNCH_GATE_LIBRARY="$HBFSIM_GATE"
set +e
"$@"
status=$?
set -e
if (( status != 0 )); then
    exit "$status"
fi

if ! python3 -c 'import json,sys; manifests=[json.loads(x) for x in open(sys.argv[1]) if x.strip()]; decisions=[json.loads(x) for x in open(sys.argv[2]) if x.strip()]; assert manifests and all(m.get("host_launch_only") is True and m.get("instrumented") is False and m.get("module_id") and m.get("kernel") for m in manifests); assert decisions and all("allowed" in d and d.get("reason") for d in decisions); assert any(d.get("modeled") is True for d in decisions)' "$HBFSIM_PASS_MANIFEST_PATH" "$HBFSIM_COVERAGE_PATH" 2>/dev/null; then
    echo "run_with_native_gate: target produced no valid host-native activation artifacts" >&2
    exit 70
fi
