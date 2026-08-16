# Thermal Reliability and Refresh Proof Record

Date: 2026-08-16 UTC

Branch: `feature/thermal-reliability`

Implementation base tested for this checkpoint:
`8e51dece9a289ba3e3feda1cdb1271c976fd6e18`

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
| Complete build | passed with GCC 13/CUDA 13/MQSim enabled |
| Complete offline CTest set | 99/99 passed in 45.17 s; the four registered live/environment tests were excluded |
| Python/shell/source hygiene | `compileall`, Ruff, `bash -n`, and `git diff --check` passed |
| vLLM adapter regression | 46/46 passed; two SWIG deprecation warnings |
| `thermal_timing_live` | built; CTest skipped with return 77 because PID 269473 violates the exclusive-GPU contract; the same snapshot had only 256 MiB free |
| `live_sm120_environment` | blocked by `no_competing_process_contract` |
| `sm120_future_live` | rebuilt against ABI v10, then blocked in native CUDA initialization with `out of memory` |

## Toolchain and configuration

The active tree is a Debug build with CUDA and MQSim enabled.  It uses GCC
13.4.0 (`/usr/bin/g++-13`), CUDA 13.0 nvcc
(`Build cuda_13.0.r13.0/compiler.36424714_0`), CMake 4.2.3, and Python 3.13.9.
The generated test inventory contains 103 tests, including the offline
`thermal_timing_environment` parser gate as test 13 and
`thermal_timing_live` as test 96.

## Commands and captured evidence

```bash
cmake -S . -B build-thermal-baseline-gcc13
cmake --build build-thermal-baseline-gcc13 -j2
cmake --build build-thermal-baseline-gcc13 \
  --target thermal_timing_live -j2

ctest --test-dir build-thermal-baseline-gcc13 \
  -E 'sm120_calibration_cases|live_sm120_environment|sm120_future_live|sm120_tma_live|thermal_timing_live' \
  --output-on-failure
# 99/99 passed in 45.17 s

python3 -m compileall -q scripts tests adapters
python3 -m ruff check scripts tests adapters
bash -n scripts/*.sh adapters/*/*.sh
git diff --check
# all passed

python3 -m pytest -q adapters/vllm/tests
# 46 passed, 2 warnings

nvidia-smi --query-gpu=index,name,compute_cap,memory.total,memory.used,memory.free,temperature.gpu \
  --format=csv,noheader,nounits
# 0, NVIDIA RTX PRO 6000 Blackwell Server Edition, 12.0,
# 97887, 96997, 256, 45

nvidia-smi --query-compute-apps=gpu_uuid,pid,process_name,used_gpu_memory \
  --format=csv,noheader,nounits
# GPU-f07ea2df-1b6f-9a02-b534-5090abf3c174,
# 269473, /home/eabban/.conda/envs/bitnet/bin/python, 96860

ctest --test-dir build-thermal-baseline-gcc13 \
  -R '^thermal_timing_live$' --output-on-failure
# skipped (return 77: competing compute process)
```

The capacity integrity gate refreshes one complete 1 MiB block as 256 ordered
4 KiB read/rewrite pairs, accounts 2 MiB of refresh traffic, increments the
block to PEC 1, and requires the SHA-256 of every backing byte after refresh to
equal the pre-refresh SHA-256.  A subsequent application program plus refresh
reaches PEC 2.  The test deletes its temporary backing file after the equality
check, so this checkpoint records the equality assertion rather than claiming
a persistent data artifact.

The final integration gates also prove three post-review boundaries.  Completed
application writes are charged to the same per-block PEC state as background
refresh; partial page coverage cannot increment PEC until the whole block has
been programmed.  Reference and fast refresh both add nonzero modeled
foreground completion latency under contention.  Clean, source-failure,
model-error, thermal-shutdown, and media-error exits all emit schema-valid,
distinct terminal status strings.

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
