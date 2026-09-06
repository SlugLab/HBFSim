# C6 one-warp GPU correctness diagnostic

This opt-in diagnostic is **UNVALIDATED**. It does not close C6.3, overlap, G5,
or a native-load completion-time claim. The live CUDA driver JIT image is not
bound to the earlier `ptxas -O3 sm_120` cubin/disassembly, whose semantic mapping
status remains `NOT_PROVEN`.

The executable runs one block of 32 lanes across four fixed cases:

| case | input stride | active mask | expected page groups |
| --- | ---: | ---: | ---: |
| same_dense | 4 | `0xffffffff` | 1 |
| distinct_dense | 4096 | `0xffffffff` | 32 |
| same_sparse | 4 | `0x01010101` | 1 |
| distinct_sparse | 4096 | `0x01010101` | 4 |

It uses the actual plugin `timing_load_future_v1` transform and the existing
module-load transaction, launch gate, FAST context, owned daemon, shared control,
range registration, and intercepted unload path. Only input is registered as a
timing/read range. The native output is a separate unregistered allocation.

Every case initializes all 32 outputs to a case-specific value guaranteed to
differ from the CPU expected value, confirms the device sentinel round trip, and
then launches. Before semantic validation, `raw.json.partial.json` saves every
available counter snapshot, original output word, and raw trace record. Trace
copying is capped by both the validated module capacity and 96 records, the
single-launch maximum. Missing fields keep explicit acquisition states.

Cleanup advances only after observed success: intercepted `cuModuleUnload`
confirms its internal context sync, future disable/clear, and unload before range
retirement; confirmed `hbfsim_unregister` precedes context/allocation release.
The void `hbfsim_context_destroy` result is recorded as called but unobservable.
A failed safety boundary stops later release and relies on the existing owned
process/session cleanup, without touching foreign jobs.

The controller retains signal handlers through artifact and status sealing.
Pending SIGINT/SIGTERM/SIGHUP always converts a possible capture to
`INTERRUPTED`. The child budget is 120 seconds for the first plugin transform,
driver JIT, daemon startup, and four cases. This budget is not a measured runtime;
the existing one-second future token liveness deadline is unchanged.

Build from the already reviewed future-capable configuration:

```text
cd /root/hbfsim-exp/eval-base-integration
cmake -S . -B build-eval-c6-unit-cuda \
  -DHBFSIM_BUILD_C6_GPU_DIAGNOSTIC=ON
cmake --build build-eval-c6-unit-cuda \
  --target hbfsimd c6_future_correctness -- -j1
```

The diagnostic input profile is derived from the frozen nominal time-scale-one
profile by changing **only** `page_bytes` from 16384 to 4096. Its source, derived
bytes, and hashes belong in the retained `inputs-attempt-001` evidence. Do not use
the empirical cd8p profile because the future capability gate rejects empirical
controls.

After the controller has frozen that input at
`results/gold/timing-future-unit/c6-correctness/inputs-attempt-001/nominal-4096-timescale1.json`,
run exactly one guarded child:

```text
/opt/miniconda3/bin/python3.13 -I -S -B \
  scripts/eval/run_c6_future_correctness_candidate.py \
  --execute \
  --out results/gold/timing-future-unit/c6-correctness/attempt-001 \
  --build-dir /root/hbfsim-exp/eval-base-integration/build-eval-c6-unit-cuda \
  --profile /root/hbfsim-exp/eval-base-integration/results/gold/timing-future-unit/c6-correctness/inputs-attempt-001/nominal-4096-timescale1.json \
  --ptx /root/hbfsim-exp/eval-base-integration/benchmarks/cuda/c6_u32_future_gold_candidate.ptx \
  --gpu-uuid GPU-f07ea2df-1b6f-9a02-b534-5090abf3c174
```

The runner inserts only its resolved `scripts/eval` directory for fixed project
imports under `-I -S`; it does not load site packages or `.pth` files. The output
directory must be new. A successful process is still
`CAPTURED_UNVALIDATED`; busy, interrupted, child failure, incomplete cleanup,
invalid artifacts, and semantic rejection remain non-success states.
