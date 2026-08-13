# SM120 Stage 4 Channel Calibration and Validation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Infer validated four-way GNIC2TEX and two-way GPCARB contention-equivalent models, run them in per-SM queues, independently validate every operation class, and permit the final `exact` label only when real-GPU correctness, timing, counters, and workload gates pass.

**Architecture:** Deterministic CUDA microbenchmarks emit timestamps and immutable case metadata; a read-only Nsight Compute harness captures declared metrics under a fixed environment. Offline fitting clusters contention-equivalent latent SMSP/channel classes without claiming physical numbering. A versioned profile carries the selected routing functions and queue parameters; a disjoint holdout validator is the sole producer of `validation.status=passed`.

**Tech Stack:** C++20, CUDA 13, Nsight Compute CLI 2025.4.1.0, Python 3, JSON/CSV, SHA-256, CMake/CTest, llama.cpp and vLLM regression runners.

---

## Stage 4 invariants

- Calibration and holdout case IDs and manifests are immutable and disjoint.
- No threshold changes after holdout results are observed.
- Labels are contention-equivalent model classes, not NVIDIA physical IDs.
- Static PTX identifiers are routing inputs, never ground-truth SMSP IDs.
- The profile records complete commands, tool versions, environment, raw hashes,
  fitted parameters, residuals, and rejected candidates.
- Only the validator may change `pending` to `passed`.
- Exact admission is pre-launch; exact reporting also requires post-run validation.

### Task 1: Extend the exact profile with calibrated routing and metrics

**Files:**
- Modify: `include/hbfsim/exact_profile.hpp`
- Modify: `src/profile/exact_profile.cpp`
- Modify: `configs/schema/sm120-exact-profile.schema.json`
- Create: `tests/fixtures/exact/sm120-stage4-valid.json`
- Modify: `tests/cpu/exact_profile_test.cpp`

- [ ] Write RED mutations for profile schema v2 containing four GNIC queues,
  two GPC queues, routing bytecode/version, latency/service distributions,
  queue depth/arbitration, metric names, raw calibration/holdout hashes, fitted
  case IDs, residuals, and per-class thresholds. Reject wrong queue counts,
  unknown routing inputs, non-disjoint cases, missing raw hash, relaxed
  thresholds, and any physical-channel claim.
- [ ] Run `cmake --build build-sm120-exact --target exact_profile_test -j2`
  and verify schema-v2 routing assertions fail.
- [ ] Parse exact schema v1 for Stage 1 diagnostics but require schema v2 for
  Stage 4 exact. Define:

```cpp
struct CalibratedQueue { std::uint32_t count, depth; std::string arbitration;
  std::vector<std::uint64_t> service_ns_by_class; };
struct RoutingProgram { std::uint32_t version;
  std::vector<std::uint32_t> smsp_proxy_lut, gnic_lut, gpc_lut; };
struct CounterThreshold { std::string metric; double max_error_percent; };
```

- [ ] Run `ctest --test-dir build-sm120-exact -R '^exact_profile$' --output-on-failure`
  and `python3 -m json.tool configs/schema/sm120-exact-profile.schema.json >/dev/null`.
- [ ] Commit `feat: define calibrated SM120 channel profile`.

### Task 2: Implement deterministic per-SM 4+2 runtime queues

**Files:**
- Create: `include/hbfsim/sm120_channels.hpp`
- Create: `src/cuda_runtime/sm120_channels.cpp`
- Create: `tests/cpu/sm120_channels_test.cpp`
- Create: `tests/cpu/sm120_channels_property_test.cpp`
- Modify: `src/host_service/control_layout.hpp`
- Modify: `src/cuda_runtime/device/hbf_device.cu`
- Modify: `CMakeLists.txt`

- [ ] Write RED exact vectors and randomized properties for routing from
  `(smid,warpid,cta_shape,resident_warps,cluster_ctarank,operation)`, four load/
  fanout queues, two store/return queues, FIFO/round-robin arbitration, queue
  depth, saturation, stable equivalence-class renaming, and byte/service
  conservation.
- [ ] Run `cmake --build build-sm120-exact --target sm120_channels_test sm120_channels_property_test -j2`
  and verify the routing/queue API is missing.
- [ ] Implement checked LUT evaluation and atomic per-SM tails/counters in the
  shared/device control. A transaction ready time is the maximum of base,
  selected GNIC/GPC, media, capacity, and native conditions.
- [ ] Run `ctest --test-dir build-sm120-exact -R '^sm120_channels(_property)?$' --output-on-failure`
  and repeat `build-sm120-exact/sm120_channels_property_test` 100 times.
- [ ] Commit `feat: run calibrated SM120 four-plus-two queues`.

### Task 3: Build deterministic calibration and holdout kernels

**Files:**
- Create: `benchmarks/cuda/sm120_calibration.cu`
- Create: `configs/calibration/sm120-training-cases.json`
- Create: `configs/calibration/sm120-holdout-cases.json`
- Create: `tests/integration/test_sm120_calibration_cases.py`
- Modify: `benchmarks/cuda/CMakeLists.txt`
- Modify: `CMakeLists.txt`

- [ ] Write RED case-schema tests requiring unique/disjoint IDs and coverage of
  ordinary load/store, TMA load/store, unicast, multicast, mixed HBM/HBF,
  single/multi warp, depths, load-use distances, cache conditions, dimensions,
  masks, CTA ranks, and cluster shapes.
- [ ] Run `python3 tests/integration/test_sm120_calibration_cases.py`
  and verify it fails because case manifests and benchmark are absent.
- [ ] Implement one executable that selects a case ID, allocates ordinary CUDA
  buffers, emits `%globaltimer`, `%smid`, `%warpid`, `%cluster_ctarank`, hashes
  all outputs, and prints one deterministic JSON record. It never changes
  clocks/power/compute mode.
- [ ] Run `python3 tests/integration/test_sm120_calibration_cases.py build-sm120-exact/benchmarks/cuda/sm120_calibration`
  and require schema plus native smoke cases to pass.
- [ ] Commit `bench: add SM120 calibration and holdout cases`.

### Task 4: Capture immutable CUDA/Nsight evidence read-only

**Files:**
- Create: `scripts/calibration/collect_sm120.py`
- Create: `tests/integration/test_collect_sm120.py`
- Modify: `docs/sm120-exact-mode.md`

- [ ] Write RED fake-tool tests for exact CUDA/Nsight versions, command argv,
  environment snapshot, warmup/repetition policy, raw stdout/stderr/CSV hashes,
  partial-run cleanup, symlink refusal, no clock/power/compute-mode mutation,
  and exit codes 64/66/70.
- [ ] Run `python3 tests/integration/test_collect_sm120.py`
  and verify it fails because `collect_sm120.py` is absent.
- [ ] Implement `collect_sm120.py --suite training|holdout --cases CASES
  --benchmark BENCHMARK --ncu NCU --output-dir OUTPUT`. Capture only metrics declared in
  the profile candidate: TMA pipe, LSU, long scoreboard, barrier/membar, L2,
  DRAM, and throughput. Write each raw artifact durably, then a manifest whose
  SHA-256 covers every member.
- [ ] Run `python3 tests/integration/test_collect_sm120.py` and then one case
  with `python3 scripts/calibration/collect_sm120.py --suite training
  --cases configs/calibration/sm120-training-cases.json
  --benchmark build-sm120-exact/benchmarks/cuda/sm120_calibration
  --ncu /usr/local/cuda-13.0/bin/ncu --output-dir /tmp/hbfsim-sm120-collect-smoke`.
- [ ] Commit `feat: collect immutable SM120 calibration evidence`.

### Task 5: Infer latent SMSP/channel routing and queue parameters

**Files:**
- Create: `scripts/calibration/fit_sm120_channels.py`
- Create: `tests/integration/test_fit_sm120_channels.py`

- [ ] Write RED synthetic datasets with known four/two contention classes,
  label permutations, noise, underdetermined matrices, overfit candidates,
  missing metrics, and non-disjoint case IDs. Require deterministic selected
  model and rejected-candidate reasons.
- [ ] Run `python3 tests/integration/test_fit_sm120_channels.py`
  and verify it fails because the fitter is absent.
- [ ] Implement pairwise contention matrix construction, latent-class
  clustering, bounded LUT search over visible inputs, queue service/depth fit,
  cross-validation inside training only, and canonical relabeling. Emit a
  pending profile fragment plus residual/candidate report; never read holdout.
- [ ] Re-run `python3 tests/integration/test_fit_sm120_channels.py` and require
  every synthetic recovery/rejection case to pass.
- [ ] Commit `feat: fit SM120 contention-equivalent channels`.

### Task 6: Make holdout validation the only passed-profile producer

**Files:**
- Create: `scripts/calibration/validate_sm120_exact.py`
- Create: `tests/integration/test_validate_sm120_exact.py`
- Modify: `scripts/calibration/check_sm120_exact_admission.py`

- [ ] Write RED tests for byte-exact correctness, per-class P50/P95, each
  declared counter, missing class, aggregate-pass/class-fail, tampered raw
  evidence, changed thresholds, training overlap, environment mismatch, and a
  fully passing synthetic holdout. Assert no other script contains code that
  writes `validation.status = passed`.
- [ ] Run `python3 tests/integration/test_validate_sm120_exact.py`
  and verify it fails because the validator is absent.
- [ ] Implement immutable join of Stage 1 artifact/profile fragment, fitted
  training fragment, and holdout manifest. Enforce P50 <= 5%, P95 <= 10%, every
  counter <= 10%, and all seven classes separately. On success write a new
  schema-v2 full profile with `status: passed`; otherwise write a report only
  and exit 2.
- [ ] Re-run `python3 tests/integration/test_validate_sm120_exact.py` and
  require the passing fixture to return 0 and every independent failure to return 2.
- [ ] Commit `feat: validate SM120 exact holdout evidence`.

### Task 7: Bind channel evidence and post-run validation to exact labels

**Files:**
- Modify: `include/hbfsim/exact_admission.hpp`
- Modify: `src/cuda_runtime/exact_admission.cpp`
- Modify: `include/hbfsim/coverage.hpp`
- Modify: `src/reporting/coverage_writer.cpp`
- Modify: `scripts/calibration/check_sm120_exact_admission.py`
- Modify: `tests/cpu/exact_admission_test.cpp`
- Modify: `tests/cpu/coverage_gate_test.cpp`
- Modify: `tests/integration/test_sm120_exact_admission.py`

- [ ] Write RED cases for missing queue profile, routing version/hash mismatch,
  out-of-profile outstanding depth, migration-visible SM mismatch, counter
  residual failure, unconsumed future/barrier/group, and exact prelaunch but
  failed post-run. Require `admitted_fidelity=calibrated_emulation` until both
  phases pass.
- [ ] Run `cmake --build build-sm120-exact --target exact_admission_test coverage_gate_test -j2`
  and `python3 tests/integration/test_sm120_exact_admission.py build-sm120-exact/libhbfsim_launch_gate.so`;
  verify the new channel/post-run assertions fail.
- [ ] Require schema-v2 routing and passed independent validation in prelaunch.
  Add `post_run_validation_passed`; coverage writer may serialize `exact` only
  when prelaunch allowed, post-run passed, zero leaks, and every evidence hash
  is nonempty.
- [ ] Re-run those commands and require all cases to pass.
- [ ] Commit `feat: require channel and post-run exact validation`.

### Task 8: Run real SM120 calibration and independent holdout

**Files:**
- Output only: `results/sm120-exact/<run-id>/` (gitignored; the run ID is the
  UTC timestamp plus the first 12 hex characters of the training case-manifest hash)
- Create: `docs/proofs/2026-08-13-sm120-exact-stage4.md`

- [ ] Prove no competing GPU work and record the read-only live snapshot. If
  the declared concurrency/clock/cache condition is not satisfied, stop before
  collection and report the exact environmental blocker; do not mutate it.
- [ ] Collect training:

```bash
python3 scripts/calibration/collect_sm120.py --suite training \
  --cases configs/calibration/sm120-training-cases.json \
  --benchmark build-sm120-exact/benchmarks/cuda/sm120_calibration \
  --ncu /usr/local/cuda-13.0/bin/ncu \
  --output-dir "$RUN/training"
```

- [ ] Fit without exposing holdout:

```bash
python3 scripts/calibration/fit_sm120_channels.py \
  --training "$RUN/training/manifest.json" \
  --stage1-fragment "$RUN/stage1/profile-fragment.json" \
  --output "$RUN/fitted-pending.json"
```

- [ ] Collect holdout with the frozen thresholds/model, then validate:

```bash
python3 scripts/calibration/collect_sm120.py --suite holdout \
  --cases configs/calibration/sm120-holdout-cases.json \
  --benchmark build-sm120-exact/benchmarks/cuda/sm120_calibration \
  --ncu /usr/local/cuda-13.0/bin/ncu \
  --output-dir "$RUN/holdout"
python3 scripts/calibration/validate_sm120_exact.py \
  --candidate "$RUN/fitted-pending.json" \
  --holdout "$RUN/holdout/manifest.json" \
  --output-profile "$RUN/sm120-exact-passed.json" \
  --report "$RUN/validation-report.json"
```

- [ ] Record raw hashes, environment, commands, fitted model, every per-class
  P50/P95/counter result, and whether the validator returned 0 or 2. Do not
  write a passed proof document if any class fails.
- [ ] Commit the proof document only after validation returns 0:
  `git commit -m "docs: record SM120 exact calibration proof"`.

### Task 9: Run correctness and real-workload regressions

**Files:**
- Modify: `tests/integration/test_llama_cpp.py`
- Modify: `tests/integration/test_vllm_probe.py`
- Modify: `scripts/run_microbench.py`
- Modify: `docs/proofs/2026-08-13-sm120-exact-stage4.md`

- [ ] Add RED exact-profile modes to microbench, deterministic llama.cpp, and
  deterministic vLLM runners. Require native/instrumented bytes or token IDs to
  match, all seven operation classes represented across the suite, nonzero
  modeled operations, zero unsafe launches/leaks/stale descriptors, and a
  post-run validation record.
- [ ] Run the three commands without implementation and verify each rejects
  the new `--exact-profile` option rather than silently using emulation.
- [ ] Implement exact launch arguments through stamped bpftime and persist all
  outputs under the run artifact directory.
- [ ] Run:

```bash
python3 scripts/run_microbench.py --exact-profile "$RUN/sm120-exact-passed.json" \
  --output "$RUN/microbench.json"
python3 tests/integration/test_llama_cpp.py --exact-profile \
  "$RUN/sm120-exact-passed.json"
python3 -m pytest tests/integration/test_vllm_probe.py -q --exact-profile \
  "$RUN/sm120-exact-passed.json"
```

- [ ] Append exact commands, byte/token hashes, performance measurements, and
  proof boundaries to the proof document; commit `test: validate exact SM120 workloads`.

### Task 10: Run the final Stage 2–4 proof gate

**Files:**
- Modify: `README.md`
- Modify: `docs/sm120-exact-mode.md`

- [ ] Update status only for gates with fresh artifacts. State explicitly that
  channel numbers are contention-equivalent model labels.
- [ ] Run full verification:

```bash
cmake --build build-sm120-exact -j2
ctest --test-dir build-sm120-exact --output-on-failure
python3 -m pytest tests/integration/test_vllm_probe.py -q
python3 tests/integration/test_sm120_future_live.py --build-dir build-sm120-exact
python3 tests/integration/test_sm120_tma_live.py --build-dir build-sm120-exact
python3 scripts/calibration/check_sm120_exact_admission.py \
  --profile "$RUN/sm120-exact-passed.json" --bundle "$BUNDLE" \
  --pass-manifest "$PASS" --training-manifest "$TRAIN" \
  --holdout-manifest "$HOLDOUT" --kernel "$KERNEL" \
  --run-contract "$CONTRACT" \
  --launch-gate build-sm120-exact/libhbfsim_launch_gate.so --load-mode aot
git diff --check
git submodule foreach --recursive 'test -z "$(git status --porcelain)"'
```

- [ ] Verify the final artifact implication:

```text
admitted_fidelity == exact
=> AOT + Stage2 future + Stage3 TMA + Stage4 channel evidence present
=> independent validation passed for all seven classes
=> post-run correctness/counters passed
=> zero live future/barrier/group/TensorMap leaks
```

- [ ] Commit `docs: complete calibrated SM120 exact mode`.
