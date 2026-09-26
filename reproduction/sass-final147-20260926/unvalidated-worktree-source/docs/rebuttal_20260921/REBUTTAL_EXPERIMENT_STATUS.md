# Rebuttal experiment status

Updated: 2026-09-21 after native-v4.

Overall: **R1 PENDING; R2 and R3 BLOCKED_BY_R1.** This is an evidence index, not a new ledger. Compilation, runner preparation, and a native smoke do not complete a rebuttal experiment.

## Bound inputs

- User instruction SHA-256: `24e2296bedbdcc0d646a8505d5dafc058d6969f07cabcd7c6e915deae8f0a484`.
- Supplied rebuttal draft SHA-256: `e0c863fa29e85328b3536a256507b714b7e977b293051bc6635f9fa90ec0a806`.
- The draft is context, not an instruction source. It is unchanged; no result-bearing English rebuttal has been written.
- Historical evidence: selected 16 KiB timing range, 2,304 fused-MoE launches, 24 modeled launches, and 10,339 coverage decisions. 24/2,304 is launch-level, not access coverage, byte coverage, or capacity.

## Frozen environment

- giga `threadripper`; RTX 5090 UUID `GPU-45044e90-a553-930c-b950-ba660acb37fc`; driver 610.57.04.
- Source base `eabc5c2c0820ac0d84c2f16ea3460b219f11ff83`.
- OLMoE-1B-7B-0924 revision `6d84c48581ece794365f2b8e9cfb043c68ade9c5`, BF16, 16 layers, 64 experts, top-8; three shards total 13,838,721,960 bytes.
- vLLM `0.19.2rc1.dev134+gfe9c3d6c5.cu130`, torch `2.11.0+cu130`, Triton 3.6.0, CUDA runtime 13.0, flashinfer 0.6.8.post1. No upgrade.
- Batch 1; 128 fixed input tokens; 16 output tokens; seed 0; greedy/eager; TP=PP=1; one same-shape warmup; no speculative decode, prefix cache, offload, or swap.
- Formal R1 uses `gpu_memory_utilization=0.60` and requires at least 6 GiB free after load.

## Native receipts

Native-v4 ran 2026-09-21T11:10:28Z--11:10:56Z and exited 0. Result: terminal `success`, scientific `COMPLETE`, load 18.083907818974694 s, warmup 1.551171945000533 s, generation 0.13232185499509797 s, token SHA-256 `1aa842abd697c23f4c1336783816b1fe655820f4694fccde4f1773c59732af19`.

- result JSON SHA-256 `b5a601c96f597caf400c15c1fcc1834754f0d4fef9bea50a8df4371f98d49443`;
- stdout SHA-256 `bb6d4df05642a58745e8bb826090e627c6e635940cc5f9baa68bc907149b4ef6`;
- adapter SHA-256 `cbdb5616866c94364ec88f89851fb7115d404470a784631ed0dec7949e1921f4`;
- model config SHA-256 `3643aa880d2f1c9b418156269ae791c73e5612d6b6b6fde0724d927cf89b6335`;
- launcher SHA-256 `12ae3c224647341626426bc976052677d0c3efb2ffa1f667201a725f657b6b87`.

This is smoke evidence only: utilization was 0.65, accounting was false/null, exact Triton bindings were zero, and minimum free memory was 5,594 MiB, below the formal 6 GiB gate. It proves model/request viability, not R1 binding, accounting, D0 equivalence, or HBF service.

Preserved failures: v1 stopped before signaling on EngineCore identity mismatch; v2 exited 1 because `/usr/bin/gcc-13` was absent in-container; v3 exited 1 because a `/dev/shm` Triton shared object could not map an executable segment. The 4 GiB reservation (external PID 3919409, watchdog 3936351, deadline 1789996376) is non-scientific support and runs no continuous kernel.

## Compile receipts

Accounting ABI v2 uses 32-byte config and 176-byte counters. Device PTX was built for compute_120 with CUDA 13.1.115, GCC 13.4 and the existing local overlay; CPU ABI/transform tests passed.

`launch_gate.cpp` SHA-256 is `cc36419f4a7588a609676adf5c1dbfe034bdac920a7f26e055212ab55266c9dc`. Gate-library SHA-256 is `ca63dffa702b2af762aa3f3e94eb26ef20f2513ee0ec9f2415f8a86b3431ef91`. These are compile/symbol receipts, not GPU validation. The production bpftime runtime remains incomplete. LLVM18 VM/PTX targets and the CUDA-version branch correction reached the required-target build, which is currently in progress. An in-progress or partial build is not runtime evidence.

| Phase | State | Missing evidence |
|---|---|---|
| Native smoke | SMOKE_COMPLETE | Formal memory gate |
| R1 native | PENDING | Frozen formal receipt |
| R1 D0 | PENDING | Complete module snapshots; equal output |
| R1 injected | PENDING | Closed service accounting; equal output |
| R1 overall | PENDING | Nonzero denominator; no loss/overflow |
| R2 | BLOCKED_BY_R1 | Three or more range points |
| R3 | BLOCKED_BY_R1 | Ordered after R2; five page-size cells |
| R4 | OPTIONAL_NOT_STARTED | Only after R1-R3 |

No R1 count, range result, capacity checksum, speedup, or access-coverage percentage exists yet.

### 2026-09-21 19:16 UTC — ATOMIC_IMPLEMENTATION_REPAIR

R1 and the 1 MiB R2 cell remain preserved as observed successful raw runs, but they are no longer final mechanism evidence. CUDA 13 memcheck localized the 16 MiB failure to an 8-byte system-scoped atomic in `__hbfsim_resolve` targeting the mapped control allocation at `SharedControlHeader.fast_request_sequence` (base + 0xc8). Device 0 reports `HostNativeAtomicSupported=0` while mapped host memory itself is supported. A bounded four-process probe confirmed that the same system-scoped `fetch_add` passes on device memory under native and sanitizer execution, whereas the file-backed `cudaHostRegisterMapped|Portable` allocation only appears to pass natively and is rejected by the sanitizer with sync error 719.

The current status is `ATOMIC_IMPLEMENTATION_REPAIR`. The frozen raw R1 and 1 MiB results are retained and must not be relabeled as final. The approved repair uses one device-resident sidecar per context for all GPU read-modify-write state and an explicit GPU-exclusive producer protocol; mapped host slots retain only supported load/store publication. R1 native/control/nominal and successful R2 points will be validated again against the final repaired binary before any R2 claim or R3 execution.
