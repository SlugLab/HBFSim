#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
build_dir="$(realpath -m "${1:-$repo_root/build-ucie-stage3}")"
mode="${2:-full}"
jobs="${HBFSIM_BUILD_JOBS:-4}"
case "$build_dir" in
  "$repo_root"/build*) ;;
  *) echo "Build directory must be a build* directory in this checkout" >&2; exit 2 ;;
esac
case "$mode" in
  full|focused|capacity-one|capacity-four) ;;
  *) echo "Mode must be full, focused, capacity-one, or capacity-four" >&2; exit 2 ;;
esac
[[ "$jobs" =~ ^[1-9][0-9]*$ ]] || {
  echo "HBFSIM_BUILD_JOBS must be positive" >&2; exit 2;
}

if [[ ! -e "$build_dir/CMakeCache.txt" ]]; then
  cmake -S "$repo_root" -B "$build_dir" -G Ninja \
    -DHBFSIM_ENABLE_CUDA=OFF -DHBFSIM_ENABLE_MQSIM=ON \
    -DHBFSIM_ENABLE_UCIE=ON -DHBFSIM_ENABLE_EVAL_TOOLS=ON \
    -DBUILD_TESTING=ON -DCMAKE_DISABLE_FIND_PACKAGE_PkgConfig=TRUE \
    -DCMAKE_DISABLE_FIND_PACKAGE_CUDAToolkit=TRUE
else
  for setting in HBFSIM_ENABLE_CUDA:BOOL=OFF HBFSIM_ENABLE_MQSIM:BOOL=ON \
      HBFSIM_ENABLE_UCIE:BOOL=ON HBFSIM_ENABLE_EVAL_TOOLS:BOOL=ON \
      BUILD_TESTING:BOOL=ON; do
    grep -Fqx "$setting" "$build_dir/CMakeCache.txt" || {
      echo "Existing build cache has wrong option: $setting" >&2; exit 2;
    }
  done
  grep -Eq '^CMAKE_DISABLE_FIND_PACKAGE_PkgConfig:[^=]+=TRUE$' \
      "$build_dir/CMakeCache.txt" || {
    echo "Existing build enables unrelated optional PkgConfig probe" >&2; exit 2;
  }
  grep -Fqx "CMAKE_HOME_DIRECTORY:INTERNAL=$repo_root" \
      "$build_dir/CMakeCache.txt" || {
    echo "Existing build belongs to a different source checkout" >&2; exit 2;
  }
fi

cmake --build "$build_dir" -j "$jobs"
cd "$repo_root"
"$build_dir/ucie_multistack_profile_resolve" \
  configs/profiles/ucie/hbf-stage3-4x512gib-top.json \
  > "$build_dir/ucie-stage3-resolved-default.json"
if [[ "$mode" == capacity-* ]]; then
  "$build_dir/ucie_stack_capacity_test" "$build_dir/ucie_stack_worker" \
      "${mode#capacity-}" | tee "$build_dir/ucie-stage3-${mode}.raw"
  exit 0
fi

if [[ "$mode" == full ]]; then
  ctest --test-dir "$build_dir" --output-on-failure -j "$jobs"
else
  ctest --test-dir "$build_dir" --output-on-failure \
      -R '^ucie_(stack_router|stack_frontend|worker_horizon|multistack_(profile|balanced|compare|frontend|reassembly|lifecycle))$'
fi

last_test="$build_dir/Testing/Temporary/LastTest.log"
grep '^compare topology=' "$last_test" > "$build_dir/ucie-stage3-compare.raw"
grep '^top_profile total=' "$last_test" > "$build_dir/ucie-stage3-profile.raw"
[[ "$(grep -c '^compare topology=' "$build_dir/ucie-stage3-compare.raw")" == 4 ]] || {
  echo "Missing four raw comparison cases" >&2; exit 1;
}
grep -Fq 'per_stack_service=128000000000' \
    "$build_dir/ucie-stage3-profile.raw" || {
  echo "Missing resolved per-stack service cap" >&2; exit 1;
}
cat "$build_dir/ucie-stage3-profile.raw" "$build_dir/ucie-stage3-compare.raw"
