# Version-aware CUDA runtime launch fix (frozen v3)

Status: **CPU PASS; gate-only tiny and full-model GPU PASS.** This fixes the
cross-runtime gate defect. It does not establish bpftime-agent coverage or R1
scientific completion.

## Defect and direct evidence

The restored workload uses Torch 2.9.1+cu128 and imports
`cudaLaunchKernel@libcudart.so.12`, `cudaLaunchKernelExC@libcudart.so.12`,
and `cudaGraphLaunch@libcudart.so.12`. The earlier gate exported one
unversioned runtime wrapper and resolved both `cudaGetFuncBySymbol` and the
launch target with `RTLD_NEXT` from the gate DSO. In the failing tiny probe,
both resolved to the restored CUDA 13 runtime. The two launches returned 400;
the synchronized 4096-element fill remained all zero.

Original failure:
`results/R1_access_coverage/d0-diagnosis-gate-runtime-domain-tiny-v2/result.json`
(SHA256 `c9a9b2727904086cbf003b6f00bb11421e3a6c2ceea2543f4e78d5bac4b5cf22`).
Payload SHA256 was
`4fe7b59af6de3b665b67788cc2f99892ab827efae3a467342b3bb4e3bc8e5bfe`.

## Frozen implementation

Source:
`/root/hbfsim-exp/rebuttal_20260921/resolver-source-v3/src/cuda_runtime/launch_gate.cpp`
(SHA256 `19c59850d7534a0866132be8aeabe48cceecb5dc584a4347b288326aaf20695e`).

Binary:
`/root/hbfsim-exp/rebuttal_20260921/resolver-build-domain-v3/libhbfsim_launch_gate.so`
(SHA256 `a34ccc14adddc31f3b29db15537210f98a18ef42fe709321fab66d8932f5abf6`).

The fix exports distinct `libcudart.so.12` and `libcudart.so.13` symbol
versions. Each entry carries an explicit runtime domain. It lazily pins only
the selected SONAME through its domain accessor and resolves
`cudaGetFuncBySymbol` and the target launch with
`dlvsym(handle, symbol, exact_version)`. There is no cross-domain fallback.
Unversioned dynamic entry points fail closed instead of guessing a runtime.

Versioned paths cover normal, ptsz, cooperative, kernel-object, ExC, and graph
launches. The driver Ex resolver fix remains in the same library and separately
pins exact `cuLaunchKernelEx` and `cuLaunchKernelEx_ptsz` entries.

Patch from diagnostic v2:
`resolver-build-domain-v3/domain-v3-vs-diag-v2.patch`
(SHA256 `bfe9b0fab658d65490576f70789c383b4353d50684282acabeded94908c1442c`).

## CPU verification

`resolver-build-domain-v3/CPU_RECEIPT.txt`
(SHA256 `f7890d9cd633d1623565d61a8c6d5420ffd2d88f07d74d6df68d310fb2224c2a`)
records:

- actual callers with `@libcudart.so.12` and `@libcudart.so.13`
  relocations passed;
- each domain used its own query six times and its own launch ten times, while
  the opposite runtime counters stayed zero;
- normal, ptsz, cooperative, `__cudaLaunchKernel`, ExC, and graph paths passed;
- a missing selected-domain symbol failed closed without using the other
  runtime;
- unversioned lookup returned the explicit initialization-error entry;
- the LOCAL-driver Ex/ptsz resolver, missing-ptsz fail-closed, loader reentry,
  pinned lifetime, other-DSO, and lookup-interposition regressions passed.

Export receipt:
`resolver-build-domain-v3/versioned-exports.txt`
(SHA256 `2d9b33f7a5f8994590544e31321f4496d9d416c1048548dd7f3d8c7d0c7c9a26`).

## GPU verification owned by execution

The fixed tiny probe passed: min=max=3.25, sum=13312, and launch/query both
resolved to Torch's CUDA 12 runtime with result 0.

Receipt:
`results/R1_access_coverage/d0-diagnosis-gate-runtime-domain-tiny-fixed-v1/result.json`
(SHA256 `6c4e8a1e120327307fe8c92055c1f2eba69fc6146065b85eabcb389adeecccd5`).

The gate-only full model also completed and produced the native-matching token
SHA256
`1aa842abd697c23f4c1336783816b1fe655820f4694fccde4f1773c59732af19`.

Receipt:
`results/R1_access_coverage/d0-diagnosis-gate-domain-v3-model-v1/result.json`
(SHA256 `489cf24c6d54600751f79d56741de0c79f94bf784d0be43f1b8cee9004b8cd53`).

A CUDA sanitizer run still exited 99 because it reports the terminal
`cuFuncGetParamInfo` enumeration call. The separate default-off parameter
diagnostic showed indices 0/1/2 succeeded with widths 4/4/8 and index 3
returned `CUDA_ERROR_INVALID_VALUE` as the expected end-of-parameter-list
sentinel on both fills. This classifies that report as
`EXPECTED_ENUM_END`; it is not a claim of an unfiltered zero-error sanitizer
run. The diagnostic-only binary is
`resolver-build-paramdiag-v4/libhbfsim_launch_gate.so`
(SHA256 `16c7fafe76e62b36111329de49109f342a4af9a0ff77a59d80737e223df8e89e`).

## Integration boundary

The gate-only 700 failure is removed. Full R1 still requires the bpftime agent,
instrumented-module coverage, request counters, zero-delay control, and injected
profile validation.

The bpftime runtime hook currently finds the unversioned
`cudaLaunchKernel` export by name. In v3 that is the fail-closed Base entry,
while real CUDA 12/13 callers bind different versioned alias addresses. Agent
integration must either hook the exact versioned addresses or demonstrate the
actual path through driver hooks. The R3 capacity benchmark is unaffected by
this runtime alias issue: its source obtains `r3_page_read_kernel` with
`cuModuleGetFunction` and launches it explicitly with driver
`cuLaunchKernel`.
