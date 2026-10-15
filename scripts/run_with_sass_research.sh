#!/usr/bin/env bash
set -euo pipefail
# Research opts in explicitly; a user's explicit 0 is never overridden.
export HBFSIM_ENABLE_SASS_LIFTER=${HBFSIM_ENABLE_SASS_LIFTER:-1}
export HBFSIM_QKV_COMBINED_V1=${HBFSIM_QKV_COMBINED_V1:-1}
export HBFSIM_QKV_RECOVERED_ABI_V1=${HBFSIM_QKV_RECOVERED_ABI_V1:-1}
export HBFSIM_SASS_CONFIGURATION_SOURCE=research-profile
exec "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/run_with_bpftime.sh" "$@"
