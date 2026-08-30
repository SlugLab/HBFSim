# Public Capacity Runtime: Non-Live Proof Checkpoint

Date: 2026-08-10

Branch: `hybrid`

Implementation base: `5c6e7edfefe351c096a1242ee3ffe8f8db581d28`

## What this checkpoint proves

Explicitly registered file ranges share one bounded, context-owned HBM cache.
Cache hits reuse resident frames without generating media work. A clean miss
generates a modeled MQSim read; a dirty victim generates an ordered modeled
program before the incoming read. Public map, flush, unregister, and teardown
are exercised through a stateful fake CUDA driver, including rollback, retry,
dirty persistence, daemon failure, and fail-closed quarantine paths.

| Claim | Evidence at this checkpoint |
|---|---|
| Public multi-file map/flush/unregister lifecycle | Proven with stateful fake-driver integration tests |
| Shared bounded cache and routed backing bytes | Proven with CPU and fake-driver tests |
| MQSim receives cache-miss reads and dirty writeback programs | Proven with CPU/MQSim and combined fake-driver+MQSim tests |
| PTX transformation and helper assembly | Proven statically; PTX JSON gate exits zero |
| Concurrency behavior in the focused capacity path | 200/200 worker stress passes and focused TSAN passes |
| Real CUDA memory copies or kernel execution | **Not proven; pending** |
| Live GPU delay injection and timing validity | **Not proven; pending** |
| Larger-than-VRAM capacity | **Not proven; pending** |
| llama.cpp and vLLM correctness/performance | **Not proven; pending** |
| GPU/CXL-SSD thermal validation | **Not proven; pending** |

No GPU binary and no `nvidia-smi` command was run for this checkpoint.

## Verification matrix

The commands below were run from the repository root on the implementation
base above. The checked-in documentation was being prepared, so the base SHA,
rather than a clean-tree claim, identifies the implementation under test.

| Configuration | Command | Result |
|---|---|---:|
| CPU (`CUDA=OFF`, `MQSIM=OFF`) | `ctest --test-dir build-verify-worker-cpu --output-on-failure` | 31/31 passed |
| CUDA static + fake driver (`CUDA=ON`, `MQSIM=OFF`) | `ctest --test-dir build-verify-worker-cuda13 --output-on-failure` | 43/43 passed |
| MQSim (`CUDA=OFF`, `MQSIM=ON`) | `ctest --test-dir build-verify-worker-mqsim --output-on-failure` | 33/33 passed |
| CUDA static + fake driver + MQSim (`CUDA=ON`, `MQSIM=ON`) | `ctest --test-dir build-verify-worker-cuda13-mqsim --output-on-failure` | 47/47 passed |
| PTX JSON integration | `python3 tests/integration/run_ptxpass_json.py build-verify-worker-cuda13/src/ptxpass_hbf/ptxpass_hbf` | passed, exit 0 |
| Documentation commit/range whitespace gate | `git diff --check 5c6e7edfefe351c096a1242ee3ffe8f8db581d28..HEAD` | passed, exit 0 |

Focused concurrency commands and results:

```bash
for run in $(seq 1 200); do
  build-verify-worker-cpu/hbfsim_capacity_worker_tests >/dev/null
done
# Result: 200/200 passed.

ctest --test-dir build-verify-worker-tsan \
  -R 'capacity_worker|capacity_backing_router|capacity_dispatch' \
  --output-on-failure
# Result: 3/3 passed (capacity_backing_router, capacity_dispatch,
# capacity_worker); no ThreadSanitizer report.
```

## Preconfigured build-tree metadata

The matrix above used five existing, preconfigured Ninja trees. The following
values were read from their `CMakeCache.txt` and checked against generated
`build.ninja` rules; they are part of this evidence rather than inferred
defaults.

| Tree | Build type | C++ compiler | CUDA compiler/toolkit | Cache arch | Feature/sanitizer flags |
|---|---|---|---|---:|---|
| `build-verify-worker-cpu` | `Release` | `/usr/bin/c++` (GCC 15.2.0) | CUDA feature off; CMake discovered `/usr/local/cuda` (CUDA 12.9) | n/a | `CUDA=OFF`, `MQSIM=OFF`, `LLM=OFF` |
| `build-verify-worker-cuda13` | `Release` | `/usr/bin/g++-15` (GCC 15.2.0) | `/usr/local/cuda-13.0/bin/nvcc`, `CUDAToolkit_ROOT=/usr/local/cuda-13` | 120 | `CUDA=ON`, `MQSIM=OFF`, `LLM=OFF`; CUDA host compiler `/usr/bin/g++-15` |
| `build-verify-worker-mqsim` | `Release` | `/usr/bin/c++` (GCC 15.2.0) | CUDA feature off; CMake discovered `/usr/local/cuda` (CUDA 12.9) | n/a | `CUDA=OFF`, `MQSIM=ON`, `LLM=OFF` |
| `build-verify-worker-cuda13-mqsim` | `Release` | `/usr/bin/c++` (GCC 15.2.0) | `/usr/local/cuda-13.0/bin/nvcc`, `CUDAToolkit_ROOT=/usr/local/cuda-13.0` | 75 | `CUDA=ON`, `MQSIM=ON`, `LLM=OFF`; device-PTX host compiler `/usr/bin/g++-13` (GCC 13.4.0) |
| `build-verify-worker-tsan` | `RelWithDebInfo` | `/usr/bin/c++` (GCC 15.2.0) | CUDA feature off; CMake discovered `/usr/local/cuda` (CUDA 12.9) | n/a | `CUDA=OFF`, `MQSIM=OFF`, `LLM=OFF`; `-fsanitize=thread` in C++, executable-linker, and shared-linker flags |

Both CUDA trees used CUDA 13.0 nvcc
(`Build cuda_13.0.r13.0/compiler.36424714_0`) and CUDA 12.8 `ptxas`
(`Build cuda_12.8.r12.8/compiler.35583870_0`). Their generated rules compile
the static `unsupported_kernel` fixture for `120-real;120-virtual` and the
device-helper PTX for `compute_120`. They also contain an excluded-from-all,
unexecuted `capacity_runtime_live_test` rule for `75-real;75-virtual`; its
presence is not live-GPU evidence. Common cached tools were CMake 4.2.3,
`/opt/miniconda3/bin/ninja` 1.13.0,
`/opt/miniconda3/bin/python3` 3.13.9, `/usr/bin/clang-19`,
`/usr/bin/llvm-objdump-19`, and `/usr/local/bin/llvm-nm`.
The three CUDA-disabled caches nevertheless record the CMake toolkit probe at
`/usr/local/cuda`, which resolved to CUDA 12.9; CUDA language support remained
disabled and no CUDA binary was executed. Their ordinary C++ and linker flags
were empty. The two CUDA-enabled caches also had empty ordinary C++, CUDA, and
linker flag fields; Release optimization came from the build type.

Equivalent configure and build commands are:

```bash
common=(
  -G Ninja
  -DCMAKE_MAKE_PROGRAM=/opt/miniconda3/bin/ninja
  -DHBFSIM_ENABLE_LLM_TESTS=OFF
  -DHBFSIM_CLANG_EXECUTABLE=/usr/bin/clang-19
  -DHBFSIM_LLVM_OBJDUMP_EXECUTABLE=/usr/bin/llvm-objdump-19
  -DHBFSIM_NM_EXECUTABLE=/usr/local/bin/llvm-nm
  -DHBFSIM_PYTHON3_EXECUTABLE=/opt/miniconda3/bin/python3
)

cmake -S . -B build-verify-worker-cpu "${common[@]}" \
  -DCMAKE_BUILD_TYPE=Release -DCMAKE_CXX_COMPILER=/usr/bin/c++ \
  -DHBFSIM_ENABLE_CUDA=OFF -DHBFSIM_ENABLE_MQSIM=OFF

cmake -S . -B build-verify-worker-cuda13 "${common[@]}" \
  -DCMAKE_BUILD_TYPE=Release -DCMAKE_CXX_COMPILER=/usr/bin/g++-15 \
  -DHBFSIM_ENABLE_CUDA=ON -DHBFSIM_ENABLE_MQSIM=OFF \
  -DCMAKE_CUDA_COMPILER=/usr/local/cuda-13.0/bin/nvcc \
  -DCMAKE_CUDA_HOST_COMPILER=/usr/bin/g++-15 \
  -DCMAKE_CUDA_ARCHITECTURES=120 -DCUDAToolkit_ROOT=/usr/local/cuda-13 \
  -DHBFSIM_PTXAS_EXECUTABLE=/usr/local/cuda-12.8/bin/ptxas

cmake -S . -B build-verify-worker-mqsim "${common[@]}" \
  -DCMAKE_BUILD_TYPE=Release -DCMAKE_CXX_COMPILER=/usr/bin/c++ \
  -DHBFSIM_ENABLE_CUDA=OFF -DHBFSIM_ENABLE_MQSIM=ON

cmake -S . -B build-verify-worker-cuda13-mqsim "${common[@]}" \
  -DCMAKE_BUILD_TYPE=Release -DCMAKE_CXX_COMPILER=/usr/bin/c++ \
  -DHBFSIM_ENABLE_CUDA=ON -DHBFSIM_ENABLE_MQSIM=ON \
  -DCMAKE_CUDA_COMPILER=/usr/local/cuda-13.0/bin/nvcc \
  -DCMAKE_CUDA_ARCHITECTURES=75 -DCUDAToolkit_ROOT=/usr/local/cuda-13.0 \
  -DHBFSIM_NVCC_HOST_COMPILER=/usr/bin/g++-13 \
  -DHBFSIM_PTXAS_EXECUTABLE=/usr/local/cuda-12.8/bin/ptxas

cmake -S . -B build-verify-worker-tsan "${common[@]}" \
  -DCMAKE_BUILD_TYPE=RelWithDebInfo -DCMAKE_CXX_COMPILER=/usr/bin/c++ \
  -DHBFSIM_ENABLE_CUDA=OFF -DHBFSIM_ENABLE_MQSIM=OFF \
  -DCMAKE_CXX_FLAGS=-fsanitize=thread \
  -DCMAKE_EXE_LINKER_FLAGS=-fsanitize=thread \
  -DCMAKE_SHARED_LINKER_FLAGS=-fsanitize=thread

cmake --build build-verify-worker-cpu -j2
cmake --build build-verify-worker-cuda13 -j2
cmake --build build-verify-worker-mqsim -j2
cmake --build build-verify-worker-cuda13-mqsim -j2
cmake --build build-verify-worker-tsan -j2
```

## Representative MQSim media-only benchmark

This command exercises the online flash-media reference model only. It does
not inject delay into a GPU workload.

```bash
build-verify-worker-mqsim/hbf_mqsim_bench \
  --profile configs/profiles/nominal.json \
  --requests 4096 \
  --bytes 16384 \
  --operation read \
  --arrival-gap-ns 0
```

Captured result:

| Field | Value |
|---|---:|
| Engine | `mqsim-hbf-media-only` |
| Submitted / completed | 4096 / 4096 |
| Average modeled latency | 211,331 ns |
| p50 / p99 modeled latency | 205,820 / 401,980 ns |
| Modeled makespan | 401,980 ns |
| Modeled bandwidth | 166,945,778,396.93518 bytes/s |
| Host wall time | 93,701,687 ns |
| Simulator throughput | 43,713.193765657605 requests/s |
| Effective capacity / blocks per plane | 68,719,476,736 bytes / 16 |

The modeled fields are deterministic for this request stream and profile. Host
wall time and simulator throughput are observations of this run and can vary
with host load; neither is evidence of live GPU delay.

## Reproduction boundary

Reusing the preconfigured trees reproduces this checkpoint only if the source
and toolchain are unchanged. The commands above reconstruct all five trees and
make their non-live boundary explicit. CUDA-enabled here means CUDA compilation
and static PTX assembly plus fake-driver tests; it must not be read as
real-device execution.
