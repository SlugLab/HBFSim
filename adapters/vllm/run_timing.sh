#!/usr/bin/env bash
set -euo pipefail

HBFSIM_ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
HBFSIM_VLLM_CACHE=${HBFSIM_VLLM_CACHE:-/dev/shm/hbfsim-vllm-live-cache}
HBFSIM_TRITON_PTX_STAGE=${HBFSIM_TRITON_PTX_STAGE:-/dev/shm/hbfsim-vllm-ptx-stage}
triton_cache="$HBFSIM_VLLM_CACHE/triton"
pass_library="${HBFSIM_BUILD_DIR:?HBFSIM_BUILD_DIR is required}/libptxpass_hbf.so"
prestaged_manifest="$HBFSIM_TRITON_PTX_STAGE/pass-manifests.jsonl"
exact_profile=
arguments=("$@")
for ((index = 0; index < ${#arguments[@]}; ++index)); do
    if [[ ${arguments[index]} == --exact-profile ]]; then
        if [[ -n $exact_profile ]] ||
           (( index + 1 >= ${#arguments[@]} )); then
            echo "run_timing.sh: --exact-profile must occur once with a value" >&2
            exit 64
        fi
        exact_profile=${arguments[index + 1]}
    fi
done

if [[ ! -d $triton_cache ]]; then
    echo "run_timing.sh: Triton cache is missing; run the baseline warmup first: $triton_cache" >&2
    exit 64
fi

run_mode=timing
exact_wrapper_args=()
if [[ -n $exact_profile ]]; then
    mapfile -t runtime_artifacts < <(python3 - "$exact_profile" <<'PY'
import json
import pathlib
import sys

profile = pathlib.Path(sys.argv[1])
if profile.is_symlink() or not profile.is_file():
    raise SystemExit("exact profile must be a regular non-symlink file")
data = json.loads(profile.read_text())
conditions = data.get("conditions", {})
if data.get("schema_version") != 2 or data.get("validation", {}).get("status") != "passed":
    raise SystemExit("exact profile must be schema v2 and independently passed")
if conditions.get("cache_condition") != "warm_l2" or conditions.get("concurrency_condition") != "exclusive_process" or conditions.get("cluster_shape") != {"x": 1, "y": 1, "z": 1}:
    raise SystemExit("exact profile does not match the vLLM run contract")
artifacts = data.get("runtime_artifacts", {})
if set(artifacts) != {"bundle_root", "prepatched_ptx_dir", "pass_manifest"}:
    raise SystemExit("exact profile runtime_artifacts are incomplete")
for name in ("bundle_root", "prepatched_ptx_dir", "pass_manifest"):
    value = artifacts[name]
    path = pathlib.Path(value)
    if not path.is_absolute() or path.is_symlink() or "\n" in value:
        raise SystemExit(f"unsafe exact runtime artifact: {name}")
    if not (path.is_file() if name == "pass_manifest" else path.is_dir()):
        raise SystemExit(f"missing exact runtime artifact: {name}")
    print(path.resolve())
PY
    )
    if [[ ${#runtime_artifacts[@]} -ne 3 ]]; then
        echo "run_timing.sh: could not resolve exact runtime artifacts" >&2
        exit 66
    fi
    bundle_root=${runtime_artifacts[0]}
    prepatched_ptx_dir=${runtime_artifacts[1]}
    prestaged_manifest=${runtime_artifacts[2]}
    exact_profile=$(cd "$(dirname -- "$exact_profile")" && pwd -P)/$(basename -- "$exact_profile")
    exact_wrapper_args=(
        --exact-profile "$exact_profile"
        --exact-bundle-dir "$bundle_root"
        --prepatched-ptx-dir "$prepatched_ptx_dir"
    )
    run_mode=exact
else
    python3 "$HBFSIM_ROOT/adapters/vllm/prepare_triton_ptx.py" \
        --cache-root "$triton_cache" \
        --staging-dir "$HBFSIM_TRITON_PTX_STAGE" \
        --kernel fused_moe_kernel \
        --pass-library "$pass_library" \
        --pass-manifest "$prestaged_manifest" \
        --quiet
    export BPFTIME_CUDA_LATE_PTX_DIR="$HBFSIM_TRITON_PTX_STAGE"
    export BPFTIME_CUDA_LATE_PTX_PREPATCHED=1
fi
export HBFSIM_BPFTIME_PROBE="${HBFSIM_BPFTIME_PROBE:-${HBFSIM_BUILD_DIR:?HBFSIM_BUILD_DIR is required}/vllm_fused_moe_probe.bpf.o}"
export HBFSIM_VLLM_EXTENSION="${HBFSIM_VLLM_EXTENSION:-$HBFSIM_BUILD_DIR/libhbfsim_vllm_extension.so}"
export HBFSIM_COVERAGE_PATH="${HBFSIM_COVERAGE_PATH:-$PWD/coverage.jsonl}"
export HBFSIM_PASS_MANIFEST_PATH="${HBFSIM_PASS_MANIFEST_PATH:-$PWD/pass-manifests.jsonl}"
export HBFSIM_PRESTAGED_PASS_MANIFEST_PATH="$prestaged_manifest"

exec "$HBFSIM_ROOT/scripts/run_with_bpftime.sh" \
    "${exact_wrapper_args[@]}" -- \
    python3 "$HBFSIM_ROOT/adapters/vllm/run.py" --mode "$run_mode" "$@"
