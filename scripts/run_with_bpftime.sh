#!/usr/bin/env bash
set -euo pipefail

export HBFSIM_ENABLE_SASS_LIFTER=${HBFSIM_ENABLE_SASS_LIFTER:-0}
case "$HBFSIM_ENABLE_SASS_LIFTER" in
    0) echo "HBFSim: recovered SASS binding disabled (default or explicit user setting). Recovery is experimental and may not preserve every kernel's semantics; native PTX remains available." >&2
       if [[ ${HBFSIM_INSTRUMENTATION_POLICY:-auto} == strict && ( ${HBFSIM_QKV_RECOVERED_ABI_V1:-0} == 1 || ${HBFSIM_QKV_COMBINED_V1:-0} == 1 ) ]]; then
           echo "HBFSim: strict recovered scope requested while disabled." >&2; exit 64
       fi
       # Legacy consumers also honor these gates; prevent an old research
       # configuration from bypassing the new default.
       export HBFSIM_QKV_RECOVERED_ABI_V1=0 HBFSIM_QKV_COMBINED_V1=0
       ;;
    1) echo "HBFSim: recovered SASS binding enabled (${HBFSIM_SASS_CONFIGURATION_SOURCE:-explicit user setting}); coverage is reported only after matched launches." >&2 ;;
    *) echo "HBFSim: HBFSIM_ENABLE_SASS_LIFTER must be 0 or 1" >&2; exit 64 ;;
esac

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
    "${HBFSIM_BPFTIME_BUILD_DIR:-$HBFSIM_ROOT/build-bpftime-hbfsim}")
HBFSIM_CUDA_ROOT=${HBFSIM_CUDA_ROOT:-/usr/local/cuda-12.8}
if [[ $HBFSIM_CUDA_ROOT != /* || ! -d $HBFSIM_CUDA_ROOT ]]; then
    echo "run_with_bpftime: HBFSIM_CUDA_ROOT must be an existing absolute directory: $HBFSIM_CUDA_ROOT" >&2
    exit 64
fi
HBFSIM_CUDA_ROOT=$(cd "$HBFSIM_CUDA_ROOT" && pwd -P)

BPFTIME_AGENT="$HBFSIM_BPFTIME_BUILD_DIR/runtime/agent/libbpftime-agent.so"
BPFTIME_SERVER="$HBFSIM_BPFTIME_BUILD_DIR/runtime/syscall-server/libbpftime-syscall-server.so"
if [[ $HBFSIM_ENABLE_SASS_LIFTER == 1 || ${HBFSIM_QKV_RECOVERED_ABI_V1:-0} == 1 || ${HBFSIM_QKV_COMBINED_V1:-0} == 1 ]]; then
    if ! nm -D --defined-only "$BPFTIME_AGENT" 2>/dev/null | grep ' hbfsim_sass_switch_abi_v1$' >/dev/null; then
        echo "HBFSim: recovered configuration needs a switch-aware agent build, including when disabled; an older agent cannot guarantee this setting. See tools/sass_runtime/prepare_overlay.py." >&2
        exit 66
    fi
fi
HBFSIM_GATE="$HBFSIM_BUILD_DIR/libhbfsim_launch_gate.so"
HBFSIM_PASS="$HBFSIM_BUILD_DIR/libptxpass_hbf.so"
HBFSIM_BPFTIME_LOADER=${HBFSIM_BPFTIME_LOADER:-$HBFSIM_BUILD_DIR/hbfsim_bpftime_attach_loader}
HBFSIM_BPFTIME_PROBE=${HBFSIM_BPFTIME_PROBE:-$HBFSIM_BUILD_DIR/coverage_probe.bpf.o}

BPFTIME_PROVENANCE="$HBFSIM_BPFTIME_BUILD_DIR/hbfsim-bpftime.provenance"
BPFTIME_PATCH="$HBFSIM_ROOT/patches/bpftime/0001-exact-module-load-provenance.patch"
BPFTIME_COMPATIBILITY_PATCH="$HBFSIM_ROOT/patches/bpftime/0002-ptx-target-compatibility-and-diagnostics.patch"
if [[ -n ${HBFSIM_SASS_BUILD_MANIFEST:-} ]]; then
    if [[ -n ${LD_PRELOAD:-} || -n ${HBFSIM_TARGET_EXTRA_LD_PRELOAD:-} ||
          ${HBFSIM_TARGET_CUDART12_PRELOAD:-} != /* ||
          ! -r ${HBFSIM_TARGET_CUDART12_PRELOAD:-} ||
          ${HBFSIM_TARGET_CUDART12_PRELOAD:-} == *:* ]]; then
        echo "HBFSim: research requires empty outer/extra preloads and one readable absolute target CUDA12 runtime." >&2; exit 64
    fi
    python3 "$HBFSIM_ROOT/tools/sass_runtime/verify_build.py" \
        --manifest "$HBFSIM_SASS_BUILD_MANIFEST" --sha256 "${HBFSIM_SASS_BUILD_MANIFEST_SHA256:?research manifest SHA256 required}" \
        --agent "$BPFTIME_AGENT" --server "$BPFTIME_SERVER" --gate "$HBFSIM_GATE" --pass "$HBFSIM_PASS" \
        --provider "${HBFSIM_SASS_PROVIDER:?research provider required}" \
        --cudart12 "$HBFSIM_TARGET_CUDART12_PRELOAD"
    if ! nm -D --defined-only "$HBFSIM_SASS_PROVIDER" | grep ' hbfsim_sass_provider_switch_abi_v1$' >/dev/null; then
        echo "HBFSim: switch-aware research provider required." >&2; exit 66
    fi
elif [[ $HBFSIM_ENABLE_SASS_LIFTER == 1 ]]; then
    echo "HBFSim: SASS research mode requires its separate build manifest; a public clean-build stamp is not research provenance." >&2
    exit 66
else
if [[ ! -r $BPFTIME_PROVENANCE ]]; then
    echo "run_with_bpftime: bpftime build provenance is missing: $BPFTIME_PROVENANCE" >&2
    exit 66
fi
mapfile -t provenance < "$BPFTIME_PROVENANCE"
expected_digest=$(sha256sum "$BPFTIME_PATCH" | awk '{print $1}')
compatibility_digest=$(sha256sum "$BPFTIME_COMPATIBILITY_PATCH" | awk '{print $1}')
if [[ ${#provenance[@]} -ne 4 ||
      ${provenance[0]:-} != bpftime_commit=ec26daecc8e787fb80fd95dd596a576404a5e36e ||
      ${provenance[1]:-} != patch_sha256="$expected_digest" ||
      ${provenance[2]:-} != compatibility_patch_sha256="$compatibility_digest" ||
      ${provenance[3]:-} != bridge_version=3 ]]; then
    echo "run_with_bpftime: bpftime build provenance mismatch: $BPFTIME_PROVENANCE" >&2
    exit 66
fi

fi

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
HBFSIM_ATTACH_TIMEOUT_MS=${HBFSIM_ATTACH_TIMEOUT_MS:-5000}
if [[ ! $HBFSIM_ATTACH_TIMEOUT_MS =~ ^[1-9][0-9]*$ ]]; then
    echo "run_with_bpftime: HBFSIM_ATTACH_TIMEOUT_MS must be a positive integer" >&2
    exit 64
fi

export BPFTIME_PTXPASS_DIR="$HBFSIM_ROOT/configs/ptxpass"
export BPFTIME_PTXPASS_LIBRARIES="$HBFSIM_PASS"
export BPFTIME_CUDA_ROOT="$HBFSIM_CUDA_ROOT"
export HBFSIM_COVERAGE_PATH="${HBFSIM_COVERAGE_PATH:-$PWD/coverage.json}"
export HBFSIM_PASS_MANIFEST_PATH="${HBFSIM_PASS_MANIFEST_PATH:-$PWD/hbfsim-pass-manifests.jsonl}"
mkdir -p -- "$(dirname -- "$HBFSIM_COVERAGE_PATH")" \
    "$(dirname -- "$HBFSIM_PASS_MANIFEST_PATH")"

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
if [[ -n ${HBFSIM_PRESTAGED_PASS_MANIFEST_PATH:-} ]]; then
    if [[ $HBFSIM_PRESTAGED_PASS_MANIFEST_PATH != /* ||
          ! -r $HBFSIM_PRESTAGED_PASS_MANIFEST_PATH ]]; then
        echo "run_with_bpftime: prestaged pass manifest must be a readable absolute path: $HBFSIM_PRESTAGED_PASS_MANIFEST_PATH" >&2
        exit 66
    fi
    cp -- "$HBFSIM_PRESTAGED_PASS_MANIFEST_PATH" \
        "$HBFSIM_PASS_MANIFEST_PATH"
fi
LD_PRELOAD="$BPFTIME_SERVER${original_preload:+:$original_preload}" \
    "$HBFSIM_BPFTIME_LOADER" "$HBFSIM_BPFTIME_PROBE" "$ready_file" &
loader_pid=$!

remaining_ms=$HBFSIM_ATTACH_TIMEOUT_MS
while ! grep -qx 'HBFSIM_BPFTIME_ATTACH_READY v1' "$ready_file" 2>/dev/null; do
    if ! kill -0 "$loader_pid" 2>/dev/null; then
        wait "$loader_pid" || true
        echo "run_with_bpftime: bpftime loader exited before CUDA attach readiness" >&2
        exit 67
    fi
    if (( remaining_ms <= 0 )); then
        echo "run_with_bpftime: timed out waiting for bpftime CUDA attach readiness" >&2
        exit 68
    fi
    sleep 0.01
    remaining_ms=$(( remaining_ms - 10 ))
done
if ! grep -qx 'shm=bpftime' "$ready_file" ||
   ! grep -qx 'attach_type=8' "$ready_file" ||
   ! grep -Eq '^entries=[1-9][0-9]*$' "$ready_file"; then
    echo "run_with_bpftime: loader readiness did not verify SHM and a CUDA attach entry" >&2
    exit 69
fi

export HBFSIM_TARGET_ORIGINAL_LD_PRELOAD="$original_preload"
research_preload=
if [[ -n ${HBFSIM_SASS_BUILD_MANIFEST:-} ]]; then
    research_preload=":$HBFSIM_SASS_PROVIDER:$HBFSIM_TARGET_CUDART12_PRELOAD"
    research_python=$(python3 -c 'import json,pathlib,sys; print(pathlib.Path(json.load(open(sys.argv[1]))["overlay_manifest"]["path"]).parent)' "$HBFSIM_SASS_BUILD_MANIFEST")
    export PYTHONPATH="$research_python${PYTHONPATH:+:$PYTHONPATH}"
fi
export LD_PRELOAD="$BPFTIME_AGENT:$HBFSIM_GATE$research_preload${original_preload:+:$original_preload}"
set +e
"$@"
status=$?
set -e
# The instrumented target is the only process that should load the agent and
# launch gate. Restore the caller's preload before validation and cleanup so
# wrapper-owned Python/rm/kill processes do not recursively bootstrap bpftime.
export LD_PRELOAD="$original_preload"
if (( status != 0 )); then
    exit "$status"
fi

activation_policy=${HBFSIM_INSTRUMENTATION_POLICY:-auto}
if [[ $activation_policy != strict ]]; then
    activation_policy=auto
fi
if ! python3 "$HBFSIM_ROOT/scripts/validate_bpftime_activation.py" \
        --policy "$activation_policy" \
        "$HBFSIM_PASS_MANIFEST_PATH" "$HBFSIM_COVERAGE_PATH" 2>/dev/null; then
    echo "run_with_bpftime: target produced no valid instrumentation activation artifacts" >&2
    exit 70
fi
