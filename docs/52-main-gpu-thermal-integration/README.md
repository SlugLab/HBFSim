# Main GPU verification and thermal integration

The review starts at upstream `78125a5`, with thermal work at `d471697`. The
consume-wait hybrid sleep is inherited in both. Tests found no evidence requiring
a production timing change: known-target sleeps clamp against the earlier of
ready time and deadline; unknown host completions retain backoff; binding and
liveness are checked again after a nap. Deliberate latency/transfer overlap and
separately labelled analytic sum models remain intact.

Actual RTX5090 checks pass for pending-to-ready and exact values, deadline before
ready, shutdown during a wait, generation invalidation during a wait, native
bypass, and two 16-lane groups with 32 unique lane completions. Generation
invalidation deliberately cannot release a now-unvalidated owner's counter;
the test does not mislabel this ownership rule as a leak. The test loads the
production helper as PTX, matching its module boundary. This is not proof of
end-to-end application speedup, host-mapped polling traffic, or physical HBF timing.

The upstream build ran 94 CTest cases successfully. The external historical
calibration CSV is unavailable: its provenance reproduction is explicitly skipped,
while the committed profile contract and generated-fixture software checks run.
Set `HBFSIM_VMEM_SOURCE_CSV` to the original file to perform its checksum and
byte-for-byte reproduction check; an explicit bad path or wrong hash still fails.
No synthetic source replaces the original measurement.

Review fixes are limited to tests and reproducibility:

- Raw PTX coverage includes both `ld.param.u64` and the unrewritable global load;
  the plugin's filtered coverage is a different quantity. The regression now
  asserts both exact opcodes and keeps checks active in Release builds.
- Compiled PTX must retain a sleep cycle that reloads time and control; poll must
  remain nonblocking. Removing sleep fails a negative fixture.
- Optional CUDA/glibc header compatibility uses a task-local include copy. Only
  two declaration exception specifications change. Neither the installed toolkit
  nor drivers are edited. Original failure is documented in NVIDIA's tracker:
  https://forums.developer.nvidia.com/t/cuda-headers-in-crt-math-functions-h-still-broken-in-debian-13-repo/362708
- Use one compatible C++ host compiler for both CXX and NVCC host compilation.
  Mixing GCC15-built core objects and GCC13 CUDA host linking failed here.

The existing cache-frame eviction/retirement race described in `docs/51` remains
an explicit limitation requiring a protocol design. No eviction-safety or full
application qualification claim is made by these tests.

## Reproduce the bounded live test

Use a supported CUDA13/sm120 machine with CUDA driver access. Choose the installed
paths locally; these commands do not install or alter system dependencies.

```sh
export CUDA_ROOT=/path/to/cuda-13
export HOST_CXX=/path/to/g++-13
mkdir -p build/live-review
# Only needed on the affected CUDA13.1 + recent glibc combination:
python3 scripts/build/prepare_cuda_glibc_overlay.py \
  --include "$CUDA_ROOT/targets/x86_64-linux/include" \
  --output build/live-review/cuda-include
export NVCC_PREPEND_FLAGS="-I$PWD/build/live-review/cuda-include"
"$CUDA_ROOT/bin/nvcc" -std=c++20 -O2 -arch=compute_120 -ptx \
  -ccbin="$HOST_CXX" -DHBFSIM_LIVE_DEVICE_IMAGE=1 \
  -Iinclude -Isrc/cuda_runtime/device tests/gpu/timing_future_wait_live_test.cu \
  -o build/live-review/wait.ptx
"$HOST_CXX" -x c++ -std=c++20 -O2 -Iinclude -I"$CUDA_ROOT/include" \
  tests/gpu/timing_future_wait_live_test.cu -L"$CUDA_ROOT/lib64" -lcuda \
  -o build/live-review/wait
# Check GPU availability and leave capacity for existing services first.
timeout --signal=TERM --kill-after=5s 30s build/live-review/wait build/live-review/wait.ptx
```

Dynamic test storage is below 7MiB; CUDA context/JIT overhead is additional.
Tests use one GPU serially with a process watchdog. Upper timing-performance
thresholds are intentionally absent on a shared GPU. Lower timing bounds,
Pending observations, values, states and accounting are asserted.

## Integrated thermal validation

The clean merge retains upstream main and all local thermal commits. No production
CUDA runtime, PTX transformer, or future ABI source differs from the live-tested
main. The original MQSim submodule is unchanged; isolated maintenance has its own
copied source, headers, archives and executable. The existing read-only command
observer remains an optional adapter in the normal patched build.

After integration, 94 main CTest cases pass again. Thermal C++3, system-thermal
Python111, rate-thermal27, maintenance70, isolated-native backend1, persistent
coupled-thermal5, and native codec/observer12 checks pass. The tools suite runs210
checks with9 initial skips; the7 native-codec skips are covered by the explicit
rebuilt native test, while2 checks requiring historical generated model inputs
remain not exercised in this fresh worktree. No previous P2 failure is promoted
to PASS by these software checks.

Process-level A/B passes with12 requests and zero time tolerance: request/raw/
reported completions, actual native phase order, physical addresses and existing
service statistics are equal with maintenance disabled. Each executable links
exactly one MQSim engine. The audit now accepts both Ninja and Makefile evidence
for the isolated target, and rejects cross-linking either engine into the other.
Actual maintenance JSON protocol checks cover successful commit, failed work
without age reset, and expiry rejection.

This integration does not complete the outstanding ECC/refresh data-identity
mapping, all research ablations, physical calibration, GPU+GDDR inference, or
application-level throughput validation. Historical thermal raw and failures stay
in the external campaign directory; only source, small fixtures and compact
software-verification receipts are published here.
