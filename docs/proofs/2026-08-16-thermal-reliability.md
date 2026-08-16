# Thermal Reliability and Refresh Proof Record

Date: 2026-08-16 UTC

Branch: `feature/thermal-reliability`

Implementation base tested for this checkpoint:
`6a42ba0811c1ed416bc508123a0f40d17a474c34`

## Proven boundary

The deterministic CPU and process-level gates prove the thermal state machine,
service scaling, telemetry/control ABI, retention integration, refresh ordering,
fast/reference plan consistency, PEC accounting, terminal reporting, and
capacity-byte preservation.  The live Normal/Light timing target also builds
for SM120 and is registered as a CTest live gate.

The live timing result is **blocked**, not passed.  At the measurement attempt,
GPU 0 had only 256 MiB free out of 97,887 MiB.  External PID 269473
(`/home/eabban/.conda/envs/bitnet/bin/python`) owned 96,860 MiB.  HBFSim did not
terminate or modify that process.

| Gate | Command/result |
|---|---|
| Focused thermal/reliability offline set | 9/9 passed |
| vLLM adapter regression | 46/46 passed; two SWIG deprecation warnings |
| `thermal_timing_live` | built; CTest skipped with return 77 because 256 MiB is below the declared 2,048 MiB headroom |
| `live_sm120_environment` | blocked by `no_competing_process_contract` |
| `sm120_future_live` | rebuilt against ABI v10, then blocked in native CUDA initialization with `out of memory` |

## Toolchain and configuration

The active tree is a Debug build with CUDA and MQSim enabled.  It uses GCC
13.4.0 (`/usr/bin/g++-13`), CUDA 13.0 nvcc
(`Build cuda_13.0.r13.0/compiler.36424714_0`), CMake 4.2.3, and Python 3.13.9.
The generated test inventory contains 102 tests, including the new live test as
test 95.

## Commands and captured evidence

```bash
cmake -S . -B build-thermal-baseline-gcc13
cmake --build build-thermal-baseline-gcc13 \
  --target thermal_timing_live -j2

ctest --test-dir build-thermal-baseline-gcc13 \
  -R '^(thermal_reliability|thermal_controller|thermal_report|thermal_device_reference|refresh_scheduler|background_dispatch|thermal_consistency|thermal_daemon|thermal_capacity)$' \
  --output-on-failure
# 9/9 passed

python3 -m pytest -q adapters/vllm/tests
# 46 passed, 2 warnings

nvidia-smi --query-gpu=index,name,compute_cap,memory.total,memory.used,memory.free,temperature.gpu \
  --format=csv,noheader,nounits
# 0, NVIDIA RTX PRO 6000 Blackwell Server Edition, 12.0,
# 97887, 96997, 256, 45

nvidia-smi --query-compute-apps=pid,process_name,used_gpu_memory \
  --format=csv,noheader,nounits
# 269473, /home/eabban/.conda/envs/bitnet/bin/python, 96860

ctest --test-dir build-thermal-baseline-gcc13 \
  -R '^thermal_timing_live$' --output-on-failure
# skipped (return 77)
```

The capacity integrity gate refreshes one complete 1 MiB block as 256 ordered
4 KiB read/rewrite pairs, accounts 2 MiB of refresh traffic, increments the
block to PEC 1, and requires the SHA-256 of every backing byte after refresh to
equal the pre-refresh SHA-256.  A subsequent application program plus refresh
reaches PEC 2.  The test deletes its temporary backing file after the equality
check, so this checkpoint records the equality assertion rather than claiming
a persistent data artifact.

The live target would run five interleaved Normal/Light trials from declared
60 C and 85 C constant sources, respectively.  It checks every output word,
future conservation, clean terminal reports, mode residency, and a median
Light/Normal load-wait ratio of at least 1.10.  Because the precondition failed,
no checksum or timing ratio was collected; those fields remain pending a free
GPU window.

## Claim boundary

Offline gates are evidence for deterministic model and integration semantics;
they are not evidence of observed live-GPU delay.  The CUDA target build is
evidence of compile/link compatibility only.  The paper must therefore present
the Normal/Light live ratio as a validation method until this gate runs, not as
a measured result.
