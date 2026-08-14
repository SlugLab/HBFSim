# SM120 exact mode operator contract

Stage 1 makes an `exact` label fail closed. It binds original and transformed
PTX, a fixed AOT cubin, SASS, compiler resource records, a validated profile,
and a fresh read-only GPU/NVML snapshot before an HBF-relevant launch. Stages
2--4 add asynchronous ordinary-memory futures, TensorMap/TMA shadow state, and
independently validated contention-equivalent channel timing. The exact label
remains restricted to the recorded GPU, toolchain, modules, environment, exact
calibrated workload vectors, nominal clock pair, case manifests, and post-run
checks.

## Supported boundary

The live reference target is one **NVIDIA RTX PRO 6000 Blackwell Server
Edition** with CUDA compute capability 12.0. The calibrated workflow below uses
`sm_120`, CUDA 13.0, and the exact `ptxas`, `nvdisasm`, `cuobjdump`, and Nsight
Compute versions recorded in the profile. The artifact parser also recognizes
the architecture-qualified `sm_120a` and `sm_120f` encodings, but that does not
make an unvalidated product, target variant, driver, or tool version exact.

Exact mode also requires the V4 launch-gate ABI, a patched and stamped bpftime
build, an AOT module load, evidence that the current process is the only CUDA
compute process on the selected GPU, and a run contract newer than every
relevant range/module mutation. NVIDIA compute mode may remain `Default`;
HBFSim neither changes it nor treats the `Exclusive Process` setting itself as
proof that no competitor is present.

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
a schema-v3 ordinary-future manifest or schema-v4 TensorMap/TMA manifest,
compiles the final transformed PTX once, and saves the cubin/SASS/resource
evidence in a content-addressed bundle:

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

Most importantly, the result includes `scope: "stage4_prelaunch_only"`,
`timing_fidelity_proven: false`, and
`admitted_fidelity: "calibrated_emulation"`. A successful dry-run proves the
Stage 1 identity/environment gates and the independently validated Stage 4
profile, but cannot claim `exact` before an instrumented run passes its
correctness, counter, coverage, and async-object drain checks.

## Launch through the stamped bpftime copy

The application must explicitly create HBFSim with `hbfsim_context_create_v2`,
set `fidelity = HBFSIM_FIDELITY_EXACT_SM120`, and pass the same full profile via
`exact_profile_path`. It must also publish the numeric run contract after the
last relevant mutation; a missing or stale contract rejects the first relevant
launch. The contract includes operation class, issued-operation count, bytes,
resident warps, queue depth, dimension count, iterations, load/use distance,
tile elements, and multicast target count. Admission reconstructs the frozen
11-feature predictor vector from those runtime values. Exact requires an exact
match with a vector recorded for that operation class; interpolation,
extrapolation, and a post-run issued-operation mismatch are rejected.

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
| `exact` | Every Stage 1 identity/AOT/environment gate, Stage 2 future gate, Stage 3 in-bounds TensorMap/TMA gate, Stage 4 replication-validation gate, exact calibrated-vector/nominal-clock gate, and post-run correctness/queue-accounting/drain gate passed. |

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
| `sm_clock_mismatch`, `memory_clock_mismatch`, `power_limit_mismatch` | Reproduce the profile's nominal SM/memory clock pair and fixed power limit, or recollect. The recorded min/max clock envelope is provenance evidence only, not an interpolation domain. HBFSim will not mutate clocks, persistence mode, or power. |
| `temperature_out_of_range` | Wait for the device to enter the validated thermal interval and recollect. |
| `cache_condition_unproven` | Establish the declared cache condition and publish a nonzero epoch newer than all relevant mutations. |
| `concurrency_condition_unproven` | Remove competing compute processes and publish the declared concurrency state. `Default` compute mode is accepted only when the read-only NVML process list contains the current PID and no other PID. |
| `cluster_shape_mismatch` | Use the validated cluster X/Y/Z shape or validate a new profile. |
| `workload_out_of_domain` | Publish a complete workload contract matching one frozen class/vector exactly, or collect and validate that new operating point. |
| `pass_manifest_exact_evidence_missing` | Supply the schema-v3 future or schema-v4 TMA manifest from the offline pass; every relevant kernel must be fully instrumented and AOT-required. |
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
fence draining, timing-backed HBF, and capacity cold/warm accesses. An
additional live litmus leaves an `atom.global.acq_rel` result unused and then
accesses an independent address; it must still record a nonzero ordering wait.
Passing
requires byte-identical outputs, strict `issue < independent_end < wait_end`
timestamps, delayed waits relative to the synchronous control, zero unsafe
launches/faults/leaks, and `issued == drained`. It also forces the real system
NVIDIA driver ahead of the build-tree fake CUDA test library.

## Stage 3 TensorMap/TMA proof and Stage 4 collection

The Stage 3 live TensorMap/TMA proof can be reproduced on a CC 12.0 GPU with:

```bash
cmake --build build-sm120-exact --target sm120_tma_bench \
  hbfsim_launch_gate hbfsimd -j2
python3 tests/integration/test_sm120_tma_live.py \
  --build-dir build-sm120-exact --output /tmp/sm120-tma-live.json
```

It compares native HBM, instrumented timing, and instrumented capacity for 27
scenarios: tiled 1D--5D load/store, zero and typed NaN OOB fill, TF32 rounding,
mbarrier phase/source reuse, host/device descriptor replacement, descriptor
copy/acquire, im2col load/store/wide, and two-CTA multicast. Every scenario must
be byte-identical to native, conserve issue/fanout and HBM/HBF/OOB bytes, expose
issue-to-wait overlap, and finish with zero faults, leaks, and stale descriptor
generations. This live proof is not a substitute for the Stage 4 independent
calibration/replication gate. OOB is therefore supported and byte-checked in
emulation/live correctness mode. The current exact post-run contract is
deliberately narrower: it rejects nonzero `tma_oob_bytes`, so the `exact` label
applies only to in-bounds TMA until OOB has its own calibrated workload class
and runtime oracle.

The TMA manifest never guesses a register-valued multicast mask. It records the
original operand plus one of `immediate`, `constant_register`, or
`runtime_register`. Constant-register values are accepted only when an
unpredicated definition in the same PTX basic block dominates the TMA issue;
otherwise the manifest keeps the value dynamic. At execution time the helper
receives the actual mask, validates its 16-bit cluster scope, derives target
ranks and fanout from that value, and the live proof binds it to byte-exact
per-rank output and runtime fanout counters.

The runtime publishes a schema-v2 routing program into control ABI v9 and
maintains four load/fanout plus two store/return atomic queue timelines and
counters per SM. The names are contention-equivalent labels, never assertions
about undocumented physical channel numbering. Route inputs are the measured
SM/warp/cluster identifiers and the launch-visible CTA/warp proxy declared in
the profile. Queue saturation is fail-closed. The runtime post-run check is
structural queue accounting (submitted/completed work and fanout conservation),
not a claim that software can read undocumented physical-channel counters;
hardware-counter agreement is checked only in the retained Stage 4 datasets.

Collect training or holdout evidence with Nsight Compute `--clock-control
none`, without changing clocks, power, persistence, or compute mode:

```bash
python3 scripts/calibration/collect_sm120.py --suite training \
  --cases configs/calibration/sm120-training-cases.json \
  --benchmark build-sm120-exact/benchmarks/cuda/sm120_calibration \
  --ncu /usr/local/cuda-13.0/bin/ncu \
  --output-dir /tmp/hbfsim-sm120-training

python3 scripts/calibration/fit_sm120_channels.py \
  --training /tmp/hbfsim-sm120-training/manifest.json \
  --stage1-fragment /absolute/results/stage1-merged/profile-fragment.json \
  --output /absolute/results/sm120-candidate.json

python3 scripts/calibration/collect_sm120.py --suite holdout \
  --cases configs/calibration/sm120-holdout-cases.json \
  --benchmark build-sm120-exact/benchmarks/cuda/sm120_calibration \
  --ncu /usr/local/cuda-13.0/bin/ncu \
  --output-dir /tmp/hbfsim-sm120-holdout

python3 scripts/calibration/validate_sm120_exact.py \
  --candidate /absolute/results/sm120-candidate.json \
  --holdout /tmp/hbfsim-sm120-holdout/manifest.json \
  --output-profile /absolute/results/sm120-exact-profile.json \
  --report /absolute/results/sm120-validation.json
```

The collector requires CUDA 13.0 and Nsight Compute 2025.4.1.0, refuses
symlink inputs and pre-existing outputs, records every argv and the relevant
environment, and hashes raw stdout, stderr, and CSV members before atomically
publishing the manifest. It also refuses any competing process on the selected
GPU. The fitter freezes the observed SM and memory clock minima/maxima, one
nominal SM/memory clock pair, one unchanged power limit, and the observed
thermal interval. Exact admission requires the fresh read-only snapshot to
equal the nominal clock pair. The wider min/max observations document dynamic
behavior but do not authorize interpolation between uncalibrated clock
features.

The fixed suites contain 28 training cases (four per operation class) and
seven independent-seed replication cases (one per class); every case has two
warmups and nine retained Nsight Compute repetitions. The replication files
retain the historical `holdout` schema/name, and their IDs and seeds are
disjoint from training, but their feature vectors intentionally duplicate
calibrated operating points. They validate repeatability at those points, not
predictive generalization. Interpolation or extrapolation is not an exact-mode
claim and is rejected. The fitter reads training only and must pass its
repetition-stratified cross-validation before freezing the 4+2 routing model
and exact workload-domain vectors.

The independent validator then enforces latency P50 at most 5%, latency P95 at
most 10%, every retained counter at most 10%, and coverage of all seven
classes. Percentage counters use absolute percentage-point error divided by
the 100-point full scale. DRAM-byte and L2-sector traffic counters use the
largest of the native count, the logical issued opportunity, and the frozen
training-class envelope as denominator; other counters use the native value.
The scale contract and per-class scales are profile-bound and validated by both
the C++ and Python admission paths. This avoids meaningless relative-error
explosions for replay-dependent counters near zero without hiding absolute
traffic discrepancies.

Exit codes are 64 for usage, 66 for unsafe/invalid inputs, and 70 for a tool or
correctness failure. No collector or fitter may write
`validation.status=passed`; that remains the independent-seed replication
validator's sole responsibility. Here that retained `holdout` is the
independent-seed
replication set described above, not an unseen-feature set. Until that
validation and the post-run gates pass,
reporting remains `calibrated_emulation`.

## Real-workload adapters and the legacy async boundary

The TinyLlama and vLLM exact-workload checks use a one-shot, content-bound
sideband probe to exercise the Stage 2 future runtime inside a real semantic
workload test. They explicitly record `model_graph_fidelity: "native"`; they
do not relabel opaque framework kernels as exact.

For vLLM, `run_timing.sh` first runs Qwen in a native process with no bpftime
preload, records its deterministic token result, and performs the requested
thermal preheat. Only after that process exits does it start a minimal
no-Torch/no-vLLM exact process. The second process stages only the probe PTX
and pass-manifest row, attaches only `hbfsim_llama_probe_kernel`, registers a
12,288-byte range, binds the original `CUfunction` to its PTX hash, launches
the calibrated 128-thread/8-depth/384-iteration probe once, validates all 128
deterministic output checksums, and finalizes the exact session. Full
vLLM/Torch import after bpftime preload is
intentionally avoided because their many late fatbin registrations are outside
this probe-only contract.

Legacy `cp.async` is not an ordinary Stage 2 LD/ST/atomic future and is not a
Stage 3 TensorMap/TMA operation. A workload whose selected data path uses
legacy `cp.async` remains native unless a separate pass, manifest schema,
runtime semantic model, calibration class, and live correctness proof are
added for it. The Qwen fused-MoE graph currently has that boundary; its
sideband result proves semantic stability and exact-runtime activation, not
model-graph exactness.
