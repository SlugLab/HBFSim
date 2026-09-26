# Build provenance and target-ABI audit

Date: 2026-09-21
Host inspected: giga
Scope: read-only audit of the current R1 candidate artifacts. No build or runtime environment was changed.

## Decision

**Every current candidate is a host-ABI artifact and is incompatible with the target vLLM container. None is a formal runtime.**

The current `rebuttal-r1-runtime-v1` directory is a copied mixed bundle, not one coherent CMake build. Its PTX pass, vLLM extension, attach loader and BPF object match the old `rebuttal-accounting-v2-20260921/full` build byte-for-byte. Its launch gate instead matches the independently linked `rebuttal-host-d0-20260921` gate. The bpftime agent and syscall server come from a third build tree, `rebuttal-bpftime-v6-llvm18`.

The immediate failure is not a GPU failure. Container preflight rejects the host-built libraries before R1 starts because they require GLIBC 2.38 and GLIBCXX 3.4.31/3.4.32. The launch gate and vLLM extension also require `libcudart.so.12`, which is absent from the CUDA 13 target container.

## Artifact inventory

| Artifact | SHA-256 | Direct CUDA NEEDED | Max GLIBC | Max GLIBCXX |
|---|---|---|---|---|
| `rebuttal-r1-runtime-v1/libptxpass_hbf.so` | `1e579773cb34cc5345e19333bef6cd14e796b8ca904120e11e0580907b9baed0` | none after --as-needed | 2.38 | 3.4.31 |
| `rebuttal-r1-runtime-v1/libhbfsim_launch_gate.so` | `ca63dffa702b2af762aa3f3e94eb26ef20f2513ee0ec9f2415f8a86b3431ef91` | `libcudart.so.12` | 2.38 | 3.4.32 |
| `rebuttal-r1-runtime-v1/libhbfsim_vllm_extension.so` | `7c85968f20cb08bedbd2dc715edd26995999f02a593ef358b0ce531ad450213f` | `libcudart.so.12`, `libcuda.so.1` | 2.38 | 3.4.31 |
| `rebuttal-r1-runtime-v1/hbfsim_bpftime_attach_loader` | `921005f2517904b76888a5b90c65ceaa7032c0a92ec9a9ea2787cef0fabb59fb` | none | 2.34 | 3.4.9 |
| `rebuttal-r1-runtime-v1/vllm_fused_moe_probe.bpf.o` | `7cd488b2cb918c207fe636bdd8c562ce4c79630b1843b1f6d8c67f0630575724` | eBPF relocatable | n/a | n/a |
| `bpftime-v6/runtime/agent/libbpftime-agent.so` | `05b54921e6feeed82da63a42ca72649c949e7c742796219469e9e2d33dcccaf0` | `libcudart.so.13`, `libcuda.so.1` | 2.38 | 3.4.32 |
| `bpftime-v6/runtime/syscall-server/libbpftime-syscall-server.so` | `3e1a939b88d1ab4e7c7a907beb6848271e7c80d7201969b8fde9ec2157e77ec3` | `libcudart.so.13`, `libcuda.so.1` | 2.38 | 3.4.32 |

The host `ldd` resolves all of these because giga has GLIBC 2.43, GCC 13 libstdc++, system CUDA 12 libraries, the CUDA 13.1 toolkit, and a host driver library. That host success does not establish target-container compatibility.

The preserved container preflight at
`results/r1-zero-v1/preflight.stdout.log` reports:

- PTX pass: GLIBC 2.38 and GLIBCXX 3.4.31 missing;
- launch gate: GLIBC 2.38, GLIBCXX 3.4.31/3.4.32 and `libcudart.so.12` missing;
- vLLM extension: GLIBC 2.38, GLIBCXX 3.4.31 and `libcudart.so.12` missing;
- bpftime agent/server: GLIBC 2.36/2.38 and GLIBCXX 3.4.31/3.4.32 missing.

The missing `libcuda.so.1` shown by a non-GPU preflight must be rechecked inside the exact NVIDIA-runtime launch environment; the driver library is normally injected there. It does not excuse the confirmed libc/libstdc++/cudart mismatches.

## Why CUDA 12 entered the main HBFSim targets

The full main-project cache records:

- `CMAKE_CUDA_COMPILER=/usr/local/cuda-13.1/bin/nvcc`;
- CUDA architecture 120;
- C++ compiler `/usr/bin/g++-13`;
- CUDA 13.1 include directories.

However, the same cache records:

- `CUDA_CUDART=/usr/lib/x86_64-linux-gnu/libcudart.so`;
- `CUDA_cudart_LIBRARY=/usr/lib/x86_64-linux-gnu/libcudart.so`;
- `CUDA_cuda_driver_LIBRARY=/usr/lib/x86_64-linux-gnu/libcuda.so`.

On giga, `/usr/lib/x86_64-linux-gnu/libcudart.so` resolves to
`libcudart.so.12.4.127`. The generated link commands for the PTX plugin,
launch gate and vLLM extension use that absolute system path. Therefore the
gate and extension have `DT_NEEDED libcudart.so.12` even though device PTX
was compiled with CUDA 13.1. The PTX plugin link command also names CUDA 12,
but GNU --as-needed drops it from DT_NEEDED because that shared object has no
live direct cudart reference.

This is a split-toolkit CMake cache: CUDA 13 compiler/headers plus CUDA 12
runtime libraries. Changing `CUDAToolkit_ROOT` after configuration is not a
safe repair because the absolute library paths are already cached.

The independent D0 gate has no CMake cache or link.txt. Its output is
byte-identical to the runtime-v1 gate and carries the same CUDA 12 and host ABI
requirements. Its provenance is therefore insufficient for formal deployment
even aside from incompatibility.

## Host ABI symbol origins

All main-project objects were compiled by GCC 13.4 on the host. The host
itself is GLIBC 2.43. Link-time symbol versioning binds ordinary calls to the
new host versions.

GLIBC 2.38 requirements in the main artifacts come from
`__isoc23_strtol[l]/strtoul[l]`. Object tracing identifies:

- `libhbfsim_core.a`: `coverage.cpp.o`, `profile.cpp.o`,
  `ptx_memory_op.cpp.o`;
- `libhbfsim_future_emitter.a`: `future_transform.cpp.o`;
- `libhbfsim_eval_ptx.a`: `ptx_ir.cpp.o`;
- `libmqsim_hbf.a`: parameter and trace parser objects.

The gate additionally requires
`std::ios_base_library_init()@GLIBCXX_3.4.32`; all main shared objects use
`basic_string::_M_replace_cold@GLIBCXX_3.4.31`.

The bpftime artifacts were also linked by host GCC 13.4. GLIBC 2.38 imports
come from its runtime JSON/config/map objects, CUDA attach objects, Frida
attach objects, its built libbpf, and server parsing. The agent/server also
import `fmod/fmodf@GLIBC_2.38` and
`std::ios_base_library_init()@GLIBCXX_3.4.32`. Their very large static LLVM
18 and Frida inputs do not make them portable; the final shared objects still
bind to host symbol versions.

## Compiler, link, and source provenance

Main build:

- generator: Unix Makefiles generated by CMake 4.2;
- C++: GCC 13.4.0, Release, C++20;
- device compiler: CUDA 13.1 nvcc, compute_120;
- main source base: `eabc5c2c0820ac0d84c2f16ea3460b219f11ff83`;
- `CMakeLists.txt` SHA-256:
  `f2953f253c8ea5b4aa762c56e81876ebc61d6ea24631bf032764b0c2c8be909e`;
- launch gate is a dirty task-local source, SHA-256
  `cc36419f4a7588a609676adf5c1dbfe034bdac920a7f26e055212ab55266c9dc`;
- vLLM extension source SHA-256:
  `cc43d61bcb355c93280278769373bb9babc528c880b2fe929631846d6129b740`;
- PTX pass main source SHA-256:
  `f24138a2e0279783d3c48717878705d3521d21a8736428a9c5d22acbae9046ef`;
- attach loader source SHA-256:
  `d8d41bda08d0c870531bfd1e49daffb86d00ede1ae5bbcc6f280cdb7c76c6399`.

Bpftime build:

- C++: GCC 13.4.0, Release C++20;
- LLVM: 18.1.8 from `/usr/lib/llvm-18/cmake`;
- CUDA links: explicit `/usr/local/cuda-13.1` library paths, producing
  `libcudart.so.13`;
- bpftime submodule revision:
  `ec26daecc8e787fb80fd95dd596a576404a5e36e`;
- fetched/copied source has no independent .git directory, so its content
  hashes and configure inputs are the authoritative provenance;
- copied bpftime CMake SHA-256:
  `7a93ae1f7deb4101efab3f3143f883e26fb2b05c213b8403b2806d3bf4171adf`;
- compatibility wrapper SHA-256:
  `54d0acf400420f55bfe74ead258df5113dcbcae720348964e1f727dc5ad1c7c7`;
- compatibility Boost header SHA-256:
  `30828feb175acdb50694dc1cbfadcef627a86630b7512d81ac36c98587461e66`.

The agent/server embed host-only RUNPATH entries under
`/usr/local/cuda-13.1`; the target container currently exposes CUDA 13 as
`/usr/local/cuda/lib64`. Host absolute RUNPATHs must not be carried into a
formal bundle.

## Required alignment strategy

1. Treat the exact immutable vLLM container image as the ABI target. Compile
   every runtime-loaded host artifact in that image or against a verified
   sysroot made from that image. Do not copy host-built static libraries into
   the aligned link.
2. Preserve all current build trees and failures. Configure entirely new
   directories, for example `build/rebuttal-r1-targetabi-v1` and
   `build/rebuttal-bpftime-targetabi-v1`. Do not delete or reuse a
   CMakeCache.
3. Use the compiler actually present and supported inside the target image.
   Do not require host `gcc-13`: native-v2 already proved
   `/usr/bin/gcc-13` is absent there. Record compiler identity, glibc,
   libstdc++, CMake, CUDA and LLVM before configuration.
4. Pin CUDA consistently. The new main configure must set, using the
   container's verified paths:

       -DCUDAToolkit_ROOT=/usr/local/cuda
       -DCMAKE_CUDA_COMPILER=/usr/local/cuda/bin/nvcc
       -DCUDA_cudart_LIBRARY=/usr/local/cuda/lib64/libcudart.so
       -DCUDA_CUDART=/usr/local/cuda/lib64/libcudart.so
       -DCUDA_cuda_driver_LIBRARY=/usr/local/cuda/lib64/stubs/libcuda.so
       -DCMAKE_CUDA_ARCHITECTURES=120

   Use the actual driver-stub path found by inventory if it differs. After
   configure, fail immediately unless CMakeCache and every link.txt contain
   only the selected CUDA 13 root for CUDA user-space libraries.
5. Set C/C++/CUDA host compiler paths from the target inventory explicitly.
   Keep Release/C++20 and the existing compute_120/device flags. Reuse the
   build-local CUDA/glibc header overlay only if the target compiler reproduces
   the known CUDA header conflict; record its hash and purpose.
6. Rebuild bpftime, LLVM-linked inputs, Frida, libbpf, the main static
   libraries, and final shared objects under the same target ABI. Reusing the
   host LLVM18 static archives would preserve host libc/libstdc++ imports.
   The decision owner must first select an LLVM development tree compatible
   with the target sysroot; no package upgrade is implied by this audit.
7. Avoid host absolute CUDA RUNPATHs. Prefer the target image's standard
   CUDA loader path or an explicit target `/usr/local/cuda/lib64` build
   RUNPATH. Do not embed `/usr/local/cuda-13.1` unless that exact path exists
   in the target image.
8. Assemble the runtime bundle only from one recorded target-ABI build set.
   Include SHA256SUMS plus CMakeCache, link.txt, compiler/version inventory,
   source hashes, and the exact container image digest.

## Mandatory non-GPU acceptance checks

Before any GPU request:

- `readelf -d`: gate, extension, agent and server use
  `libcudart.so.13`; no artifact uses `libcudart.so.12`;
- `readelf --version-info`: no required GLIBC or GLIBCXX version exceeds
  what the target image exports;
- target-container `ldd`: no missing dependency under the exact NVIDIA
  runtime environment;
- load-only checks inside the target container for PTX plugin, launch gate and
  vLLM extension;
- verify agent/server load and attach-loader linkage without issuing a GPU
  experiment;
- compare bundle hashes with the files actually mounted for R1.

Only after these checks pass may the artifacts be called a runtime candidate.
GPU validation and R1 scientific acceptance remain separate.


## 2026-09-21 route clarification: restored giga host ABI

The host-ABI incompatibility findings above apply to loading the audited
artifacts in the old vLLM container. They do not make the giga host ABI
intrinsically invalid. The restored host Python 3.13.9/vLLM 0.15.1 route can
legitimately use host GLIBC/GLIBCXX requirements when every Python and native
component is loaded on that same recorded host ABI.

The historical PyTorch cu128 wheel plus a CUDA 13 native instrumentation
toolchain is also a legitimate split. Their major CUDA runtimes must not be
conflated: PyTorch may retain its pinned cu128 dependencies while HBFSim native
targets are compiled and linked against the selected CUDA 13 toolkit.

The concrete native-build defect remains: the main CMake configuration
declared a CUDA 13.1 compiler/include route but resolved the default CUDA 12.4
`libcudart.so` through `/usr/lib/x86_64-linux-gnu`. Rebuild native targets
in a fresh directory with the selected CUDA 13 root and verify
CMakeCache/link.txt/readelf before assembling a coherent host runtime bundle.
Do not reuse the current mixed-tree bundle merely because host GLIBC is now
acceptable.
