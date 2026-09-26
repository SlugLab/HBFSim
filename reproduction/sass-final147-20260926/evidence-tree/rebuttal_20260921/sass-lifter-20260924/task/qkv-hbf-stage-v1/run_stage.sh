#!/usr/bin/env bash
set -u

base=/root/hbfsim-exp/rebuttal_20260921
task="$base/sass-lifter-20260924/task/qkv-hbf-stage-v1"
out="$task/run-v1"
source_ptx="$base/sass-lifter-20260924/task/qkv-output-repair-v1/cpu-build-v1/qkv.quarantine.ptx"
plugin="$base/native-supported-layer0-runtime-bundle-v3/libptxpass_hbf.so"
entry="$base/worktree/adapters/vllm/prepare_triton_ptx.py"
kernel='_ZN8internal5gemvx6kernelIii13__nv_bfloat16S2_S2_fLb0ELb1ELb1ELb0ELi6ELb0E18cublasGemvParamsExIi30cublasGemvTensorStridedBatchedIKS2_ES6_S4_IS2_EfEEENSt9enable_ifIXntT5_EvE4typeET11_'

test ! -e "$out" || exit 73
mkdir "$out" || exit 73
hostname > "$out/host.txt"
sha256sum "$source_ptx" "$plugin" "$entry" "$task/run_stage.sh" > "$out/source.sha256"
if ! grep -q '^6db074711d19c3e31cbcc98c6e170f0b4330259f220c2d518ce8aee42b9c9eab ' "$out/source.sha256"; then
  echo 'frozen PTX identity mismatch' > "$out/preflight.error"
  exit 65
fi
if ! grep -q '^0ab365ca22940995a568cce6d8962f034709b6090d7074c417068379efb09a58 ' "$out/source.sha256"; then
  echo 'successful old98 pass plugin identity mismatch' > "$out/preflight.error"
  exit 65
fi
printf '%s\n' "$kernel" > "$out/kernel.txt"
printf '%s\n' 'python3 prepare_triton_ptx.py --transform-one --pass-library <pinned plugin> --kernel <exact entry>' > "$out/command.txt"
export HBFSIM_PASS_MANIFEST_PATH="$out/pass-manifests.jsonl"
ulimit -v 2097152
set +e
taskset -c 47 timeout -s TERM 120s python3 "$entry" \
  --transform-one --pass-library "$plugin" --kernel "$kernel" \
  < "$source_ptx" > "$out/staged.ptx" 2> "$out/stage.stderr"
rc=$?
set -e
printf '%s\n' "$rc" > "$out/stage.rc"
sha256sum "$source_ptx" "$plugin" "$entry" "$task/run_stage.sh" > "$out/source.after.sha256"
if test -f "$out/staged.ptx"; then sha256sum "$out/staged.ptx" > "$out/staged.sha256"; fi
if test -f "$out/pass-manifests.jsonl"; then sha256sum "$out/pass-manifests.jsonl" > "$out/manifest.sha256"; fi
exit "$rc"
