#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
build_dir="$(realpath -m "${1:-$repo_root/build-ucie-stage2}")"
mode="${2:-full}"
jobs="${HBFSIM_BUILD_JOBS:-4}"
case "$build_dir" in
  "$repo_root"/build*) ;;
  *) echo "Build directory must be a build* directory inside this checkout" >&2; exit 2 ;;
esac
case "$mode" in full|focused) ;; *) echo "Mode must be full or focused" >&2; exit 2 ;; esac
[[ "$jobs" =~ ^[1-9][0-9]*$ ]] || {
  echo "HBFSIM_BUILD_JOBS must be positive" >&2; exit 2;
}

if [[ ! -e "$build_dir/CMakeCache.txt" ]]; then
  cmake -S "$repo_root" -B "$build_dir" -G Ninja \
    -DHBFSIM_ENABLE_CUDA=OFF -DHBFSIM_ENABLE_MQSIM=ON \
    -DHBFSIM_ENABLE_UCIE=ON -DHBFSIM_ENABLE_EVAL_TOOLS=ON \
    -DBUILD_TESTING=ON -DCMAKE_DISABLE_FIND_PACKAGE_PkgConfig=TRUE
else
  for setting in HBFSIM_ENABLE_CUDA:BOOL=OFF HBFSIM_ENABLE_MQSIM:BOOL=ON \
      HBFSIM_ENABLE_UCIE:BOOL=ON HBFSIM_ENABLE_EVAL_TOOLS:BOOL=ON \
      BUILD_TESTING:BOOL=ON; do
    grep -Fqx "$setting" "$build_dir/CMakeCache.txt" || {
      echo "Existing build cache does not match required option: $setting" >&2; exit 2;
    }
  done
  grep -Eq '^CMAKE_DISABLE_FIND_PACKAGE_PkgConfig:[^=]+=TRUE$' \
    "$build_dir/CMakeCache.txt" || {
      echo "Existing build cache enables unrelated optional PkgConfig probes" >&2; exit 2;
    }
  grep -Fqx "CMAKE_HOME_DIRECTORY:INTERNAL=$repo_root" \
    "$build_dir/CMakeCache.txt" || {
      echo "Existing build cache belongs to a different source checkout" >&2; exit 2;
    }
fi

cmake --build "$build_dir" -j "$jobs"
if [[ "$mode" == full ]]; then
  ctest --test-dir "$build_dir" --output-on-failure -j "$jobs"
else
  ctest --test-dir "$build_dir" --output-on-failure \
    -R '^(ucie_(link|mqsim|bank_layout|device_coalescer|device_frontend|device_frontend_fake|device_profile)|mqsim_(online|horizon))$'
fi

cd "$repo_root"
"$build_dir/ucie_device_bench" \
  configs/profiles/ucie/hbf-stage2-fixture-on.json 8 \
  > "$build_dir/ucie-stage2-on8.jsonl"
"$build_dir/ucie_device_bench" \
  configs/profiles/ucie/hbf-stage2-fixture-off.json 8 \
  > "$build_dir/ucie-stage2-off8.jsonl"
python3 - "$build_dir/ucie-stage2-on8.jsonl" "$build_dir/ucie-stage2-off8.jsonl" <<'PY'
import json
import sys

for path, expected in zip(sys.argv[1:], (1, 8)):
    with open(path, encoding="utf-8") as stream:
        records = [json.loads(line) for line in stream]
    summary = records[-1]
    assert summary["summary"] is True
    assert summary["accepted"] == summary["succeeded"] == 8
    assert summary["native_commands"] == summary["media_submits"] == expected
    assert summary["application_bytes"] == summary["axi_payload_bytes"] == 512
    assert summary["media_submit_bytes"] == expected * 4096
    assert summary["ar_wire_bytes"] > 0 and summary["r_wire_bytes"] > 0
    assert sum("request_id" in item for item in records) == 8
    assert sum("native_group" in item for item in records) == expected
    print(json.dumps(summary, sort_keys=True))
PY
