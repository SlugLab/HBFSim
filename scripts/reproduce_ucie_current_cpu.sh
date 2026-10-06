#!/usr/bin/env bash
set -euo pipefail
repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
build_dir="$(realpath -m "${1:-$repo_root/build-ucie-current}")"
mode="${2:-focused}"
jobs="${HBFSIM_BUILD_JOBS:-4}"
case "$mode" in focused|build-only|remote-tests-only) ;; *) echo 'Mode must be focused, build-only or remote-tests-only' >&2; exit 2 ;; esac
[[ "$jobs" =~ ^[1-9][0-9]*$ ]] || { echo 'HBFSIM_BUILD_JOBS must be positive' >&2; exit 2; }
optimized_env=(env -u HBFSIM_SHORT_OBSERVER_MAILBOX -u HBFSIM_SHORT_OBSERVER_REPORT
  HBFSIM_UCIE_SHM_RPC=1 HBFSIM_UCIE_TYPED_RPC=1
  HBFSIM_UCIE_COALESCER_EMPTY_WAIT=1 HBFSIM_UCIE_RELEASABLE_CHILD_INDEX_V1=1
  HBFSIM_UCIE_ORDERED_ADMISSION_V1=1 HBFSIM_UCIE_CAPACITY_ZERO_INACTIVE_V1=1
  HBFSIM_UCIE_COMPACT_CONSUME_ORDERED_V1=1 HBFSIM_UCIE_COMPACT_CONSUME_WAVE_V1=1
  HBFSIM_UCIE_DEFERRED_INITIAL_HELLO_V1=1)
run_remote_tests() {
  # These existing fixtures assert two remote PIDs and inject an owned worker death.
  env -u HBFSIM_UCIE_LOCAL_STACK_ID "${optimized_env[@]}" \
    ctest --test-dir "$build_dir" --output-on-failure \
      -R '^ucie_(multistack_frontend|multistack_lifecycle)$'
}
if [[ "$mode" == remote-tests-only ]]; then
  for target in ucie_stack_worker ucie_multistack_frontend_test ucie_multistack_lifecycle_test; do
    [[ -x "$build_dir/$target" ]] || { echo "Missing built target: $target" >&2; exit 1; }
  done
  cd "$repo_root"
  run_remote_tests
  exit 0
fi
cmake -S "$repo_root" -B "$build_dir" -G Ninja \
  -DCMAKE_BUILD_TYPE=Release -DHBFSIM_ENABLE_CUDA=OFF \
  -DCMAKE_DISABLE_FIND_PACKAGE_PkgConfig=TRUE \
  -DCMAKE_DISABLE_FIND_PACKAGE_CUDAToolkit=TRUE \
  -DHBFSIM_ENABLE_MQSIM=ON -DHBFSIM_ENABLE_UCIE=ON -DBUILD_TESTING=ON
cmake --build "$build_dir" --parallel "$jobs" --target \
  hbfsimd ucie_stack_worker ucie_link_test ucie_bank_layout_test \
  ucie_device_coalescer_test ucie_stack_frontend_test ucie_worker_horizon_test \
  ucie_multistack_frontend_test ucie_multistack_lifecycle_test
if [[ "$mode" == build-only ]]; then exit 0; fi
cd "$repo_root"
env -u HBFSIM_UCIE_LOCAL_STACK_ID -u HBFSIM_SHORT_OBSERVER_MAILBOX -u HBFSIM_SHORT_OBSERVER_REPORT \
  HBFSIM_UCIE_SHM_RPC=0 HBFSIM_UCIE_TYPED_RPC=0 \
  HBFSIM_UCIE_COALESCER_EMPTY_WAIT=0 HBFSIM_UCIE_RELEASABLE_CHILD_INDEX_V1=0 \
  HBFSIM_UCIE_ORDERED_ADMISSION_V1=0 HBFSIM_UCIE_CAPACITY_ZERO_INACTIVE_V1=0 \
  HBFSIM_UCIE_COMPACT_CONSUME_ORDERED_V1=0 HBFSIM_UCIE_COMPACT_CONSUME_WAVE_V1=0 \
  HBFSIM_UCIE_DEFERRED_INITIAL_HELLO_V1=0 \
  ctest --test-dir "$build_dir" --output-on-failure \
  -R '^ucie_(link|bank_layout|device_coalescer|stack_frontend|worker_horizon|multistack_frontend|multistack_lifecycle)$'
env HBFSIM_UCIE_LOCAL_STACK_ID=0 "${optimized_env[@]}" \
  ctest --test-dir "$build_dir" --output-on-failure -R '^ucie_worker_horizon$'
run_remote_tests
