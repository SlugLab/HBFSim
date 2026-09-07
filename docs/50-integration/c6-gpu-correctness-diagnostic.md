# C6 one-warp GPU correctness diagnostic

## Current future/native continuation — 2026-09-07

The current path adds an actual globaltimer-compatible future mapping at
`results/gold/timing-future-unit/c6-mapping/future-delay-attempt-004/` and a
separately assembled, untransformed native control at
`c6-mapping/native-control-attempt-001/`. Their states remain
`COLLECTED_NOT_PROVEN` and `COLLECTED_NATIVE_CONTROL_NOT_PROVEN`; collecting
PTX/SASS and a native binding does not establish load-completion timing or
overlap.

The native-control host integration passed its four focused CPU controls. Its
first host build retained a compile failure caused by calling `stage` through a
`std::unique_ptr<Journal>` with `.`. The reviewed one-line `->` repair then
passed the focused single-target host build without rebuilding the future image.
GPU002 reached the existing resource guard and stopped on foreign GPU ownership;
it launched no owned kernel and did not touch that process. Fresh GPU003 then
completed 67 launches in 9.237038743 s. Its independent analysis matched all
2,112 outputs, 1,440 issued/ready/consumed requests, 45 groups and 2,880 traces;
observable cleanup succeeded, while the void context-destroy completion remains
unobservable. Every futureD measured lane was ready before work began. The
K0/K4096 prework medians were 309,376/316,856 ns versus D=20,000 ns, and both
work medians were 7,040 ns (ordinary native: 7,072 ns). This W choice is
`NON_IDENTIFYING`, not evidence of overlap or native completion timing.

These results do not close C6.3, G5, D/W, native completion, overlap, full
lifecycle, capacity/reference/hybrid/empirical support or TMA. The acquisitions
below remain historical `CAPTURED_UNVALIDATED` evidence.

This opt-in diagnostic is **UNVALIDATED**. It does not close C6.3, overlap, G5,
or a native-load completion-time claim. Later acquisitions now load explicitly
bound native images; their semantic mapping status remains `NOT_PROVEN`.
The initial driver-JIT acquisition below is distinct historical evidence.

## Later exact-native-image acquisitions

The four-case native-image run at76bae126 completed in7.207384349s, with all128
outputs and72 issued/ready/consumed independently checked. It loaded the retained
82664-byte cubin4b17ddb0... through the same validated buffer. Evidence and review:
`results/gold/timing-future-unit/c6-correctness/native-image-attempt-001/` and
`native-image-data-review-attempt-001/`.

The separate conditional-consumer target was committed at5b1aaf9 after seven CPU
controls and a serial build. The new79760-byte cubin0804821c... preserves ordinary
u32 load and independent work before a predicated first consumer and an
unconditional second consumer. In the mixed case, lanes0/8/16/24 use the first
consumer; the remaining lanes wait at the second. The actual three-case run
completed in10.239901762s, reporting96 correct outputs,96issued/ready/consumed,
three groups and192traces with no pending/error/overflow. The runtime recorded
the exact new native image and unchanged source/build/profile postchecks.
Evidence: `conditional-consumer-attempt-001/` under the same C6 correctness root.
Independent review recomputed all 96 outputs, sentinels and 192 traces, with
consistent counters and native-image binding; review and analysis are retained
in `conditional-consumer-data-review-attempt-001/` under that evidence root.

These remain CAPTURED_UNVALIDATED observations. A prescribed first-consumer bit
is not a dynamic wait-call-site observation. Trace timestamps do not measure
physical native load completion, and current positive scalar reservation is not
a trueD0 known-delay future control. D/W, G5, overlap, full lifecycle and TMA remain
open. Existing observable cleanup succeeded; contextdestroy is still a void API.

## First actual acquisition

The guarded diagnostic ran once on HEAD
`e96be28ae4b0be5dacc4f5944c64a554572bef9c`, starting
2026-09-06T17:29:55.997109Z. The controller exited0 after8.308445783 s with
state `CAPTURED_UNVALIDATED`. Its record is
`results/gold/timing-future-unit/c6-correctness/controller-attempt-001/execution.json`
(SHA256 `cc2fb688d8d0c85278a17a7b68980a10fa715313949cc7ca84dd35f37d727efc`).
The original diagnostic is
`results/gold/timing-future-unit/c6-correctness/attempt-001/diagnostic/raw.json`
(SHA256 `346dbac1219b6191052668518e97c3eb886f62b27a77cc47824470f1040775fa`).

All four cases reported `PASS`; all128 lane outputs matched their expected
values. Final counters were72 issued,72 model-ready,72 consumed,38 groups issued,
38 groups completed and144 trace records, with pending, terminal-error and
trace-overflow all zero. Module unload, range unregister, both allocation frees
and plugin close reported success. `hbfsim_context_destroy` is a void API, so
the record says only `called=true` and `completion=UNOBSERVABLE_VOID_API`.

Independent recomputation also matched128/128 outputs and all four checksums
without using the raw expected fields, and checked all144 trace records and
reservations1–38. The report and one-time recomputation are retained in
`results/gold/timing-future-unit/c6-correctness/data-review-attempt-001/`;
review SHA256 `01f92b33eb103f84e541e0362a2f9ce8a28075d49f0f89b48bf89967e05cc0bd`.

This acquisition establishes a narrow single-warp output/conservation result.
It does not bind the live driver-JIT image to the earlier optimized cubin and
does not close C6.3, G5, overlap, native-completion timing, the full C6.4
lifecycle or TMA.

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
the following command records the already completed first acquisition. Do not rerun its used attempt directory; any justified new run needs a fresh output path:

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
