# Auxiliary runtime components retained from earlier builds

The current execution bundle carries three pre-existing files in addition to
the newly built HBFSim runtime, pass, gate, and agent. Their presence in a
bundle is not evidence that this integration rebuilt them. All paths below
refer to the giga project root, `/root/hbfsim-exp/rebuttal_20260921`.

| File in the retained bundle | SHA-256 | Source and actual provenance |
| --- | --- | --- |
| `first-fault-compact-runtime-v2/hbfsim_bpftime_attach_loader` | `823ec55914c5a31522cc9645e2ee821fa736dff16854a58829d3bd149d34af72` | Byte-identical to `first-fault-compact-build-v2/hbfsim_bpftime_attach_loader`; that build's cache names `first-fault-compact-source-v2`. |
| `first-fault-compact-runtime-v2/vllm_fused_moe_probe.bpf.o` | `11dbc6a0724912c54a9ef5facb0219942ff9c0fbc87d4a996db9e3a9f9c6c7f5` | Byte-identical to `atomic-sidecar-build-v3/vllm_fused_moe_probe.bpf.o`; that build's cache names `atomic-sidecar-source-v1`. It is absent from `first-fault-compact-build-v2`, so the package name alone does not identify its producer. |
| `native-norm-bpftime-v4/runtime/syscall-server/libbpftime-syscall-server.so` | `0b9b0d61f07b2fc10eb1644fde51a9ab47835991e4e0a3864fe956f78cf3781d` | Copied unchanged across the native-norm and bpftime runtime wrappers. Its wrapper provenance states bpftime commit `ec26daecc8e787fb80fd95dd596a576404a5e36e`, old patch SHA `c10ef6129615d4f1183043eee43ba0f159d14604f4e6d18470416016dfdb640f` (`patches/bpftime/0001-exact-module-load-provenance.patch`), bridge version 2. The exact producing build/command for this byte sequence was not found. |

The loader source `src/cuda_runtime/bpftime_attach_loader.cpp` has SHA-256
`d8d41bda08d0c870531bfd1e49daffb86d00ede1ae5bbcc6f280cdb7c76c6399`
in both the historical producer and integrated main. The historical Ninja
compile used `/usr/bin/g++-15`, `-fconstexpr-loop-limit=1048576 -O3 -DNDEBUG`,
then linked `/usr/lib/x86_64-linux-gnu/libbpf.so`. Main's CMake target remains
`hbfsim_bpftime_attach_loader` (conditional on pkg-config libbpf and Clang).
The clean runtime profile has the target configured, but this integration did
not build it. A future isolated CPU rebuild command is:

```sh
cmake --build <clean-runtime-futures-on-build> \
  --target hbfsim_bpftime_attach_loader --parallel 2
```

The BPF source `tests/gpu/vllm_fused_moe_probe.bpf.c` has SHA-256
`d100fd992b5e4d2cbe02891443eb94d666d325c604c339618328af0adf7d3cd8`
in the actual producer and integrated main. Its retained Ninja command used
the pinned LLVM 20 Clang with `-target bpf -O2 -g -c`. Main's configured
target `hbfsim_vllm_probe` emits `vllm_fused_moe_probe.bpf.o` from the same
source. The CPU-only reconstruction command is:

```sh
cmake --build <clean-runtime-futures-on-build> \
  --target hbfsim_vllm_probe --parallel 2
```

The syscall-server source files and `runtime/syscall-server/CMakeLists.txt`
are byte-identical between the clean ec26 bpftime source and the new fully
patched agent source. The server target compiles `syscall_context.cpp`,
`syscall_server_main.cpp`, and `syscall_server_utils.cpp`, links `runtime`,
the enabled uBPF/LLVM VM libraries, pthread, math, dl, and spdlog, and uses
`syscall-server.version`. The new agent build is configured with that target,
but only `bpftime-agent` was built under the pinned lock. The target exists
and the output DSO is absent. The source identities are:

| Server source | SHA-256 |
| --- | --- |
| `runtime/syscall-server/CMakeLists.txt` | `7067f15d1523b89f64ecdd1124f7f16a6829b8998d89d48af142da6d1a8bfd71` |
| `runtime/syscall-server/syscall_context.cpp` | `9bae92e4ed2d3832a15ba606e90273fe79ef1294088251a7b3430328dd6c6bcd` |
| `runtime/syscall-server/syscall_server_main.cpp` | `d0b8375252addd0de26b1299286a5dea3fc719a90a1b6c8892315a3ac9707f3f` |
| `runtime/syscall-server/syscall_server_utils.cpp` | `d467d5e816f30eec52fa78e60d545f099ff608ce3d7554d0956c7cae20d59de6` |
| `runtime/syscall-server/syscall-server.version` | `86d431b486500721ce235365b14ddfecb87225840b51b14bc813aa2161d9c06c` |

The configured build uses GCC/G++ 13, CUDA 13, LLVM 20, and the pinned agent
lock with `BUILD_BPFTIME_DAEMON=ON`, `BPFTIME_BUILD_WITH_LIBBPF=ON`, and
uBPF/LLVM JIT enabled. These describe a **candidate new build**, not proven
flags for the historical `0b9b0d…` binary.

The candidate CPU command, after explicitly extending the locked target
allowlist and recording a separate receipt, is:

```sh
cmake --build <clean-agent-build> --target bpftime-syscall-server --parallel 2
```

The new server's bytes and runtime compatibility with the retained `0b9b0d…`
file are **unverified**. Until a separately reviewed CPU build and runtime
interface check complete, the bundle must identify that retained server by
its old provenance and exact hash. None of these three auxiliary files was
rebuilt, replaced, or GPU-tested by this source review.
