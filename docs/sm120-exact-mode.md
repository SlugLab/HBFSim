# SM120 exact mode: Stage 1 operator contract

Stage 1 makes an `exact` label fail closed. It binds original and transformed
PTX, a fixed AOT cubin, SASS, compiler resource records, a validated profile,
and a fresh read-only GPU/NVML snapshot before an HBF-relevant launch. It does
not yet make the timing model microarchitecturally exact.

## Supported boundary

The live reference target is one **NVIDIA RTX PRO 6000 Blackwell Server
Edition** with CUDA compute capability 12.0. The calibrated workflow below uses
`sm_120`, CUDA 13.0, and the exact `ptxas`, `nvdisasm`, `cuobjdump`, and Nsight
Compute versions recorded in the profile. The artifact parser also recognizes
the architecture-qualified `sm_120a` and `sm_120f` encodings, but that does not
make an unvalidated product, target variant, driver, or tool version exact.

Exact mode also requires the V4 launch-gate ABI, a patched and stamped bpftime
build, an AOT module load, exclusive-process evidence when the profile requires
it, and a run contract newer than every relevant range/module mutation.

## Build the fixed tool path

Use CUDA 13 rather than whichever CUDA happens to be first on `PATH`:

```bash
cmake -S . -B build-sm120-exact \
  -DCMAKE_BUILD_TYPE=RelWithDebInfo \
  -DHBFSIM_ENABLE_CUDA=ON \
  -DCMAKE_CUDA_ARCHITECTURES=120 \
  -DHBFSIM_ENABLE_MQSIM=OFF \
  -DCMAKE_CUDA_COMPILER=/usr/local/cuda-13.0/bin/nvcc
cmake --build build-sm120-exact -j2

scripts/build_patched_bpftime.sh
```

`scripts/build_patched_bpftime.sh` records the pinned bpftime commit, patch
hashes, CUDA root, and CUDA release in `hbfsim-bpftime.provenance`. The launch
wrapper refuses an absent or mismatched stamp.

## Prepare PTX and the AOT bundle offline

Preparation invokes the PTX-pass plugin without launching a CUDA kernel, writes
the schema-v2 pass manifest, compiles the final transformed PTX once, and saves
the cubin/SASS/resource evidence in a content-addressed bundle:

```bash
ROOT=$PWD
OUT=/absolute/results/kernel-sm120
BUNDLES=/absolute/results/sm120-bundles

python3 scripts/calibration/prepare_sm120_exact.py \
  --original-ptx /absolute/input/kernel.ptx \
  --kernel kernel \
  --kernel-contract /absolute/input/kernel-contract.json \
  --target sm_120 \
  --output-dir "$OUT" \
  --bundle-root "$BUNDLES" \
  --ptxpass-plugin "$ROOT/build-sm120-exact/libptxpass_hbf.so" \
  --artifact-builder "$ROOT/scripts/calibration/build_sm120_artifact.py" \
  --ptxas /usr/local/cuda-13.0/bin/ptxas \
  --nvdisasm /usr/local/cuda-13.0/bin/nvdisasm \
  --cuobjdump /usr/local/cuda-13.0/bin/cuobjdump \
  --ncu /usr/local/cuda-13.0/bin/ncu \
  --expected-cuda-release 13.0
```

The output directory is created once and is never overwritten. It contains
`original.ptx`, `transformed.ptx`, `pass-manifest.jsonl`,
`profile-fragment.json`, and `prepatched-ptx/<original-sha256>.ptx`. The bundle
contains `original.ptx`, `transformed.ptx`, `module.cubin`, `module.sass`, and
`artifact.json` under `<bundle-root>/<original-sha256>/sm_120/`.

The generated fragment deliberately contains only:

- exact tool and module evidence that preparation observed;
- the content-addressed bundle path; and
- `validation.status: "pending"`.

Preparation does not query or change GPU clocks, does not run calibration, and
cannot claim that validation passed. There is no supported manual JSON-edit
path from `pending` to `passed`. Only the Stage 4 independent validation command
may join target/conditions/thresholds/datasets with this fragment and emit a
`passed` profile.

## Run the read-only admission check

Given a full Stage 4 profile and its independently retained training and
holdout manifests, create a run-contract JSON with the observed cache state,
concurrency state, cluster shape, and monotonic epochs. Then run:

```bash
python3 scripts/calibration/check_sm120_exact_admission.py \
  --profile /absolute/results/stage4-profile.json \
  --bundle "$BUNDLES/<original-sha256>/sm_120" \
  --pass-manifest "$OUT/pass-manifest.jsonl" \
  --training-manifest /absolute/results/training-manifest.json \
  --holdout-manifest /absolute/results/holdout-manifest.json \
  --kernel kernel \
  --run-contract /absolute/results/run-contract.json \
  --launch-gate "$ROOT/build-sm120-exact/libhbfsim_launch_gate.so" \
  --load-mode aot
```

This command creates a temporary CUDA context only to obtain the selected
SM120 device, calls the launch gate's read-only environment collector, and
opens then closes an AOT authorization transaction to validate the exact cubin
and artifact bytes. It does not load the application module, launch a kernel, change clocks, change
power limits, warm or flush caches, or modify any input. For deterministic CI
and offline replay, `--environment-json` may be supplied in addition to the
launch gate; the JSON result marks that source as `supplied_snapshot`, and it is
not live-hardware proof.

Exactly one JSON decision is written to stdout. Exit status is `0` for an
admissible profile/artifact under the observed inputs, `2` for a valid but
rejected exact request, `64` for malformed arguments or JSON contracts, `66`
for a missing/unsafe path, and `70` for a tool or runtime failure.

Most importantly, the result includes
`scope: "stage1_identity_environment_reproducibility_only"` and
`timing_fidelity_proven: false`. A successful Stage 1 dry-run proves identity,
resource, validation-record, and environmental reproducibility. It does not
prove LDG/STG/TMA/channel timing fidelity.

## Launch through the stamped bpftime copy

The application must explicitly create HBFSim with `hbfsim_context_create_v2`,
set `fidelity = HBFSIM_FIDELITY_EXACT_SM120`, and pass the same full profile via
`exact_profile_path`. It must also publish the V4 numeric run contract after the
last relevant mutation; a missing or stale contract rejects the first relevant
launch.

Run that exact-capable application through the wrapper:

```bash
export HBFSIM_BUILD_DIR="$ROOT/build-sm120-exact"
export HBFSIM_BPFTIME_BUILD_DIR="$ROOT/build-bpftime-hbfsim"
export HBFSIM_CUDA_ROOT=/usr/local/cuda-13.0
export HBFSIM_PRESTAGED_PASS_MANIFEST_PATH="$OUT/pass-manifest.jsonl"

scripts/run_with_bpftime.sh \
  --exact-profile /absolute/results/stage4-profile.json \
  --exact-bundle-dir "$BUNDLES" \
  --prepatched-ptx-dir "$OUT/prepatched-ptx" \
  -- /absolute/bin/exact-capable-application [arguments]
```

The wrapper selects prepatched PTX by original hash, the patched bpftime loader
loads only the matching cubin bytes, and HBFSim consumes a one-shot AOT load
transaction. PTX/JIT loading cannot be promoted to exact. Inspect the coverage
JSON after the run: only an allowed record with `aot_verified: true`,
`validation_passed: true`, nonempty profile/cubin/SASS identities, and an empty
`exact_rejection_reasons` array may say `admitted_fidelity: "exact"`.

## Fidelity labels

| Label | Meaning |
|---|---|
| `emulation` | The legacy/default synthetic HBF behavior. No exact profile is requested. |
| `calibrated_emulation` | Exact was requested but one or more admission gates failed. The launch is rejected; this label is never a silent fallback launch. |
| `exact` | Every Stage 1 identity, AOT, resource, validation-record, environment, and run-contract gate matched for this launch. It is still bounded by the Stage 1 timing disclaimer below. |

## Rejection reasons and remediation

All mismatches are hard failures and are reported in deterministic order.

| Reason | Remediation |
|---|---|
| `profile_not_validated`, `validation_class_missing` | Run Stage 4 independent training/holdout validation; do not edit the status manually. |
| `training_manifest_sha256_mismatch`, `holdout_manifest_sha256_mismatch` | Restore the exact retained dataset manifest or rerun Stage 4. |
| `gpu_name_mismatch`, `gpu_uuid_mismatch`, `pci_device_mismatch` | Use the calibrated physical GPU, or produce a separate validated profile. |
| `compute_capability_mismatch` | Use the CC 12.0 target; another architecture requires a separate exact implementation/profile. |
| `driver_version_mismatch` | Restore the profiled driver or repeat calibration and validation. |
| `live_environment_missing` | Establish an SM120 CUDA context and working CUDA/NVML collector; do not use a stale snapshot as live proof. |
| `module_artifact_missing`, `ptx_target_mismatch` | Select the content-addressed bundle belonging to the profile module and target. |
| `toolchain_mismatch` | Rebuild with the exact CUDA 13 tool versions recorded by the profile. |
| `aot_evidence_missing` | Use `--load-mode aot` and the stamped bpftime AOT loader; JIT is never exact. |
| `original_ptx_sha256_mismatch`, `transformed_ptx_sha256_mismatch`, `cubin_sha256_mismatch`, `sass_sha256_mismatch` | Restore the immutable prepared bundle or prepare, calibrate, and validate a new one. |
| `kernel_resource_missing`, `register_count_mismatch`, `spill_bytes_mismatch`, `shared_memory_mismatch`, `occupancy_tier_mismatch` | Restore the profiled cubin/toolchain/launch tier, or recalibrate the changed kernel. |
| `sm_clock_mismatch`, `memory_clock_mismatch`, `power_limit_mismatch` | Return the device to the already-approved operating condition outside HBFSim, then recollect; HBFSim will not mutate it. |
| `temperature_out_of_range` | Wait for the device to enter the validated thermal interval and recollect. |
| `cache_condition_unproven` | Establish the declared cache condition and publish a nonzero epoch newer than all relevant mutations. |
| `concurrency_condition_unproven` | Remove competing compute processes and publish the declared concurrency state. |
| `cluster_shape_mismatch` | Use the validated cluster X/Y/Z shape or validate a new profile. |
| `pass_manifest_exact_evidence_missing` | Supply the schema-v2 manifest from the offline pass; every relevant kernel must be fully instrumented and AOT-required. |
| `pass_artifact_identity_mismatch` | Use the manifest and AOT bundle produced from the same transformed PTX. |
| `exact_configuration_missing` | Create the context with the V2 exact API and an intact exact profile before registering ranges. |
| `exact_evaluator_exception` | Preserve the decision record and treat it as a runtime defect; never retry as exact automatically. |

Malformed contracts and unsafe paths are input errors rather than admission
reasons. Repair the reported shape/path; symlinks and existing preparation
output directories are intentionally refused.

## Stage 2 ordinary asynchronous futures

Stage 2 is implemented for ordinary global loads, stores, and supported atomic
RMW operations. The PTX pass builds a CFG/def-use IR, emits a nonblocking
shadow issue at the original memory instruction, waits at the first proven
consumer or ordering drain, snapshots store operands, and rejects ambiguous
control/data flow. Manifest schema v3 binds the IR hash, instruction table,
maximum-live future budgets, and ambiguity list to admission evidence.

The live proof is intentionally separate from exact Stage 4 admission:

```bash
cmake -S . -B build-sm120-exact \
  -DHBFSIM_ENABLE_CUDA=ON -DHBFSIM_ENABLE_MQSIM=ON \
  -DCMAKE_CUDA_ARCHITECTURES=120
cmake --build build-sm120-exact --target \
  sm120_future_bench sm120_future_correctness -j2
python3 tests/integration/test_sm120_future_live.py \
  --build-dir build-sm120-exact --output /tmp/sm120-future-live.json
```

The harness returns `77` only if no CC 12.0 GPU exists. Otherwise it compares
native, legacy synchronous negative-control, and asynchronous-future runs for
ordinary load/store/atomic, vector and branched paths, source reuse, system
fence draining, timing-backed HBF, and capacity cold/warm accesses. Passing
requires byte-identical outputs, strict `issue < independent_end < wait_end`
timestamps, delayed waits relative to the synchronous control, zero unsafe
launches/faults/leaks, and `issued == drained`. It also forces the real system
NVIDIA driver ahead of the build-tree fake CUDA test library.

## Remaining Stage 3/4 limit

Stages 1–2 do **not** yet implement or validate TMA load/store or
unicast/multicast completion semantics, TensorMap runtime address-space
splitting, GPCARB's two contention-equivalent queues, GNIC2TEX's four
contention-equivalent queues, or their SMSP/CGA-relative routing model. Those
require the TensorMap/TMA model (Stage 3) and independent CUDA/Nsight
calibration plus holdout validation (Stage 4). Until those gates pass, the
runtime remains fail-closed for complete Blackwell instruction timing fidelity.
