# CUDA architecture compatibility

HBFSim's device resolver is a baseline CUDA implementation, not a Blackwell
instruction implementation. The reference proofs remain native Linux on
`sm_120`, but the source-level instruction requirements begin at compute
capability 7.0. This document defines what an alternate architecture build does
and does not establish.

## Required CUDA features

| HBFSim use | Source | Minimum or runtime requirement |
|---|---|---|
| Warp coalescing by range and logical page | `__match_any_sync`, `__shfl_sync`, `__activemask` in `hbf_device.cu` | `__match_any_sync` requires CC 7.0 |
| Bounded device polling | `__nanosleep` in `hbf_device.cu` | CC 7.0 |
| Request/completion ordering | `cuda::atomic_ref<..., thread_scope_system>`; generated PTX uses acquire/release system-scope atomics | PTX memory semantics require `sm_70`; system-wide atomics are not available on Tegra before CC 7.2 |
| GPU timestamping | `%globaltimer` in the resolver and llama.cpp probe | `sm_30`, but NVIDIA documents the register as target-specific; each platform needs timing calibration |
| Shared control mapping | `cudaHostRegisterMapped` and `cudaHostGetDevicePointer` | Host registration and mapped-host-memory support on the runtime platform |
| Capacity address space and HBM-role cache | `cuMemAddressReserve`, `cuMemCreate`, `cuMemMap`, and `cuMemSetAccess` | `CU_DEVICE_ATTRIBUTE_VIRTUAL_MEMORY_MANAGEMENT_SUPPORTED`; the Driver API calls fail closed when unavailable |

The resolver does **not** use Tensor Cores, thread-block clusters, distributed
shared memory, TMA, `mbarrier`, `cp.async`, `multimem`, or Blackwell `tcgen05`
instructions. Those architecture-specific features are therefore not a reason
to restrict the helper to `sm_120`.

There is one portability boundary that a compute capability number cannot
settle. NVIDIA states that atomic functions operating on mapped host memory are
not atomic from the point of view of the host or other GPUs. HBFSim's control
protocol performs CPU/GPU acquire/release operations and cross-domain RMWs on
such a mapping. A successful compile and `ptxas` run therefore proves device
instruction compatibility, not the full live protocol. Alternate platforms
must pass the live validation sequence below before their results are treated
as reference-quality evidence.

## Architecture families covered by CUDA 12.8

| Baseline target | NVIDIA architecture | HBFSim build status |
|---|---|---|
| `sm_70`, `sm_72` | Volta | Source-level candidate; CC 7.0 is the helper minimum |
| `sm_75` | Turing | Source-level candidate |
| `sm_80`, `sm_86`, `sm_87` | Ampere | Source-level candidate |
| `sm_89` | Ada | Compile/assembly and local CUDA/VMM tests; full live protocol remains experimental |
| `sm_90` / `sm_90a` | Hopper | Source-level candidate |
| `sm_100`, `sm_100a`, `sm_101`, `sm_101a` | Blackwell | Source-level candidate |
| `sm_120`, `sm_120a` | Blackwell | Reference build and proof target |

CUDA distinguishes baseline targets from architecture-specific `a` targets.
HBFSim always compiles its embedded helper for the numeric baseline because it
uses no architecture-specific instructions. The PTX pass may inject that helper
into a module with the same numeric target and an `a` suffix, but never into a
different baseline SM target. This preserves PTX module compatibility while
avoiding an architecture allowlist.

## Configure and validation policy

Select exactly one numeric architecture per build directory:

```bash
cmake -S . -B build-sm89 -G Ninja \
  -DHBFSIM_ENABLE_CUDA=ON \
  -DCMAKE_CUDA_ARCHITECTURES=89
```

The single-target rule is intentional. `cmake/EmbedDevicePtx.cmake` removes the
helper's module directives and inserts its functions into workload PTX, so the
helper and workload must share one baseline target. Separate build directories
are required to validate multiple architectures. CMake rejects non-numeric,
multi-target, and pre-Volta selections; NVCC remains the authority on whether
the installed Toolkit implements a particular numeric target.

An alternate GPU should be promoted from build-compatible to live-validated
only after all of the following pass on that GPU and operating environment:

1. Compile the helper and representative benchmark kernels for the selected
   `compute_XX`, then assemble rewritten PTX with `ptxas -arch=sm_XX`.
2. Run the CPU, fake-CUDA, PTX-pass, launch-gate, and coverage tests.
3. Query host registration and CUDA VMM support and run
   `capacity_runtime_live_test`.
4. Stress the mapped control queue bidirectionally, including CPU/GPU RMW,
   timeout, shutdown, and failure transitions.
5. Run timing-only and capacity microbenchmarks with fail-closed coverage, then
   recalibrate the latency profile for that GPU.
6. Validate the relevant llama.cpp or vLLM adapter separately; adapter proof is
   not implied by the core helper build.

## NVIDIA references

- [CUDA C++ Programming Guide: compute capabilities](https://docs.nvidia.com/cuda/cuda-programming-guide/05-appendices/compute-capabilities.html)
- [CUDA 12.8 Programming Guide: `__match_any_sync` and `__nanosleep`](https://docs.nvidia.com/cuda/archive/12.8.0/cuda-c-programming-guide/index.html)
- [PTX ISA 8.7: memory model, atomics, targets, and `%globaltimer`](https://docs.nvidia.com/cuda/archive/12.8.0/parallel-thread-execution/index.html)
- [CUDA Programming Guide: mapped host memory atomicity](https://docs.nvidia.com/cuda/cuda-programming-guide/02-basics/understanding-memory.html)
- [CUDA 12.8 Driver API device attributes](https://docs.nvidia.com/cuda/archive/12.8.0/cuda-driver-api/group__CUDA__TYPES.html)
- [CUDA 12.8 NVCC target architecture support](https://docs.nvidia.com/cuda/archive/12.8.0/cuda-compiler-driver-nvcc/index.html)
