#!/usr/bin/env bash
set -euo pipefail

HBFSIM_ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
HBFSIM_VLLM_CACHE=${HBFSIM_VLLM_CACHE:-/dev/shm/hbfsim-vllm-live-cache}
HBFSIM_TRITON_PTX_STAGE=${HBFSIM_TRITON_PTX_STAGE:-/dev/shm/hbfsim-vllm-ptx-stage}
triton_cache="$HBFSIM_VLLM_CACHE/triton"
pass_library="${HBFSIM_BUILD_DIR:?HBFSIM_BUILD_DIR is required}/libptxpass_hbf.so"
prestaged_manifest="$HBFSIM_TRITON_PTX_STAGE/pass-manifests.jsonl"
exact_profile=
report_dir=
profile=
exact_preheat=0
hbf_timing_model=hybrid
hbf_timing_model_seen=0
arguments=("$@")
for ((index = 0; index < ${#arguments[@]}; ++index)); do
    if [[ ${arguments[index]} == --exact-profile ]]; then
        if [[ -n $exact_profile ]] ||
           (( index + 1 >= ${#arguments[@]} )); then
            echo "run_timing.sh: --exact-profile must occur once with a value" >&2
            exit 64
        fi
        exact_profile=${arguments[index + 1]}
    elif [[ ${arguments[index]} == --report-dir ]]; then
        if [[ -n $report_dir ]] ||
           (( index + 1 >= ${#arguments[@]} )); then
            echo "run_timing.sh: --report-dir must occur once with a value" >&2
            exit 64
        fi
        report_dir=${arguments[index + 1]}
    elif [[ ${arguments[index]} == --profile ]]; then
        if [[ -n $profile ]] ||
           (( index + 1 >= ${#arguments[@]} )); then
            echo "run_timing.sh: --profile must occur once with a value" >&2
            exit 64
        fi
        profile=${arguments[index + 1]}
    elif [[ ${arguments[index]} == --exact-preheat ]]; then
        if (( exact_preheat != 0 )); then
            echo "run_timing.sh: --exact-preheat must occur at most once" >&2
            exit 64
        fi
        exact_preheat=1
    elif [[ ${arguments[index]} == --hbf-timing-model ]]; then
        if (( hbf_timing_model_seen != 0 )) ||
           (( index + 1 >= ${#arguments[@]} )); then
            echo "run_timing.sh: --hbf-timing-model must occur once with a value" >&2
            exit 64
        fi
        hbf_timing_model=${arguments[index + 1]}
        hbf_timing_model_seen=1
    fi
done

if [[ -z $report_dir ]]; then
    echo "run_timing.sh: --report-dir is required" >&2
    exit 64
fi
report_dir=$(python3 - "$report_dir" <<'PY'
import pathlib
import sys

print(pathlib.Path(sys.argv[1]).resolve())
PY
)

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
    probe_ptx=${HBFSIM_VLLM_EXACT_PROBE_PTX:-${HBFSIM_BUILD_DIR:?HBFSIM_BUILD_DIR is required}/hbfsim_llama_probe.ptx}
    mapfile -t sideband_artifacts < <(
        python3 "$HBFSIM_ROOT/adapters/vllm/prepare_exact_sideband.py" \
            --profile "$exact_profile" --probe-ptx "$probe_ptx" \
            --report-dir "$report_dir"
    )
    if [[ ${#sideband_artifacts[@]} -ne 2 ]]; then
        echo "run_timing.sh: could not stage the exact sideband probe" >&2
        exit 66
    fi
    prepatched_ptx_dir=${sideband_artifacts[0]}
    prestaged_manifest=${sideband_artifacts[1]}
    exact_wrapper_args=(
        --exact-profile "$exact_profile"
        --exact-bundle-dir "$bundle_root"
        --prepatched-ptx-dir "$prepatched_ptx_dir"
    )
    exact_bpftime_probe=${HBFSIM_VLLM_EXACT_BPFTIME_PROBE:-${HBFSIM_BUILD_DIR:?HBFSIM_BUILD_DIR is required}/llama_probe.bpf.o}
    run_mode=exact
else
    if [[ ! -d $triton_cache ]]; then
        echo "run_timing.sh: Triton cache is missing; run the baseline warmup first: $triton_cache" >&2
        exit 64
    fi
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
if [[ $run_mode == exact ]]; then
    export HBFSIM_BPFTIME_PROBE="$exact_bpftime_probe"
else
    export HBFSIM_BPFTIME_PROBE="${HBFSIM_BPFTIME_PROBE:-${HBFSIM_BUILD_DIR:?HBFSIM_BUILD_DIR is required}/vllm_fused_moe_probe.bpf.o}"
fi
export HBFSIM_VLLM_EXTENSION="${HBFSIM_VLLM_EXTENSION:-$HBFSIM_BUILD_DIR/libhbfsim_vllm_extension.so}"
export HBFSIM_COVERAGE_PATH="${HBFSIM_COVERAGE_PATH:-$report_dir/coverage.jsonl}"
export HBFSIM_PASS_MANIFEST_PATH="${HBFSIM_PASS_MANIFEST_PATH:-$report_dir/pass-manifests.jsonl}"
export HBFSIM_PRESTAGED_PASS_MANIFEST_PATH="$prestaged_manifest"

if [[ $run_mode == exact ]]; then
    if [[ -z $profile ]]; then
        echo "run_timing.sh: --profile is required for exact probe" >&2
        exit 64
    fi
    probe_arguments=(
        --profile "$profile"
        --exact-profile "$exact_profile"
        --report-dir "$report_dir"
        --hbf-timing-model "$hbf_timing_model"
    )
    if (( exact_preheat != 0 )); then
        probe_arguments+=(--exact-preheat)
    fi
    python3 "$HBFSIM_ROOT/adapters/vllm/run.py" --mode exact \
        --defer-exact-probe "$@"
    exec "$HBFSIM_ROOT/scripts/run_with_bpftime.sh" \
        "${exact_wrapper_args[@]}" -- \
        python3 "$HBFSIM_ROOT/adapters/vllm/run_exact_probe.py" "${probe_arguments[@]}"
fi

exec "$HBFSIM_ROOT/scripts/run_with_bpftime.sh" -- \
    python3 "$HBFSIM_ROOT/adapters/vllm/run.py" --mode timing "$@"
