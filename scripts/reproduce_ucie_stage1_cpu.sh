#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
build_dir="$(realpath -m "${1:-$repo_root/build-ucie-stage1}")"
mode="${2:-full}"
jobs="${HBFSIM_BUILD_JOBS:-4}"
case "$build_dir" in
  "$repo_root"/build*) ;;
  *) echo "Build directory must be a build* directory inside this checkout" >&2; exit 2 ;;
esac
case "$mode" in full|focused) ;; *) echo "Mode must be full or focused" >&2; exit 2 ;; esac
[[ "$jobs" =~ ^[1-9][0-9]*$ ]] || { echo "HBFSIM_BUILD_JOBS must be positive" >&2; exit 2; }

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
    -R '^(ucie_(link|mqsim)|mqsim_(online|horizon))$'
fi
cd "$repo_root"
"$build_dir/ucie_mqsim_bench" \
  configs/profiles/ucie/hbf-grade2-single-module.json 4
