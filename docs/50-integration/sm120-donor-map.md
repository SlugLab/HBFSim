# Selective SM120 integration map

Frozen objects: B = `fc829992ecdc3ca68881656722b67a31067c5d33`, S =
`f4dc28b2671c01939d98e4a968e6fb37b2e364d9`, X =
`37144843906b3bd71f3fbac1fecc6b5080d82b95`. Remote fetch confirms those
objects on 2026-09-05. B/S merge-base is
`0b34feb6459fb87eed8b40987e306d112118d363`. Exact merge-base, left/right log,
name-status and git-cherry outputs are saved in `results/gold/integration/`.
No direct merge or history-wide cherry-pick is authorized by this map.

All donor paths below refer to S. Decisions describe integration boundaries,
not completed runtime support. Runtime admission remains closed until its
complete dependency set and semantic gold pass.

| Unit | Donor files and functions | Base equivalent / conflicts / preserved fixes | Decision |
|---|---|---|---|
| A IR/parser/analysis | `src/ptxpass_hbf/ptx_ir.{hpp,cpp}`, `ptx_async_op.{hpp,cpp}`, `ptx_analysis.{hpp,cpp}`; `parse_module`, `parse_instruction`, `transfer_block` | B has `ptx_memory_op` and comment/multiline/.loc-safe scanning in `transform.cpp`. S loses those guarantees and confuses conditional with unconditional consumption. | REUSE in optional offline library, with parser repairs and constrained analysis. |
| B ordinary future transform | `future_transform.{hpp,cpp}`; `emit_issue`, `emit_wait`, `emit_reissue_drain` | Preserve B synchronous resolver and native predicate. S emits guarded waits but unconditionally clears analysis state. | REIMPLEMENT admission and guards; reuse emitter concepts only after tests. |
| C TMA | `async_object_analysis.{hpp,cpp}`, `tma_transform.{hpp,cpp}` | No base implementation. Linear static groups are not dynamic CFG semantics. Base TMA/ordinary cp.async rejection must remain active. | DEFER runtime import until single-group and polling structure are proven. |
| D TensorMap | `include/hbfsim/tensormap.hpp`, `src/cuda_runtime/{tensormap.cpp,device_tensormap.*,tensormap_interpose.cpp}` | No base equivalent. Encode/replace, domain/generation, publish/acquire and retirement must be integrated together. | REUSE validated concepts; no partial lifecycle. |
| E device ABI | `hbf_device.{cu,cuh}`; `DeviceFuture`, issue/poll/wait and TMA helpers | S redirects base resolver through per-lane future accounting, leaving old warp/page coalescing in unused `resolve_sync_legacy`. S also unconditionally emits newer architecture instructions. | REIMPLEMENT additive timing-only helpers; preserve B resolver, target policy and accounting. |
| F host/control ABI | `include/hbfsim/{protocol.hpp,api.h}`, `src/{api.cpp,host_service/control_layout.hpp,cuda_runtime/context.cpp}` | Control4→9, request/slot/header growth; public API1→4; existing stats getter grows from3 to6 u64 fields. | DISCARD wholesale ABI/API replacement; independently version any required new interface. |
| G context/loader/registry | `context.cpp`, `launch_gate.cpp`, `coverage.*`, `module_identity.*`, `plugin.cpp` | Retain B owner/generation, range table, exact module identity and registered-pointer policy. TensorMap descriptors need underlying base-address policy. | REIMPLEMENT minimal additions; donor exact-profile admission is separate. |
| H build/embedded PTX | `CMakeLists.txt`, `cmake/EmbedDevicePtx.cmake`, `main.cpp`, `plugin.cpp` | S removes base async-coverage, QD and eval tests. Preserve B helper authenticity and single numeric target. | SELECTIVE source additions; no wholesale build replacement. |
| I CPU tests | IR/analysis/async/future/TMA/TensorMap/device-reference tests | Retain B tests and new gold counterexamples. Reference state machines are not called by GPU helper code. | REUSE with new negative/semantic tests; label CPU proof accurately. |
| J GPU tests | `tests/gpu/sm120_{future,tma}_correctness.cu`, corresponding live validators | Existing tail movement checks are not a D/W residual oracle; synchronous overlap is forced to zero in parts of donor validation. | REUSE workloads after full admission; extend timestamps, checksum, failures and SASS proof. |

## Dependency closure and ABI

Offline IR/ordinary analysis needs `ptx_ir`, `ptx_async_op`, `ptx_analysis`
(header and implementation pairs). Offline future transformation additionally
needs `future_transform`; TMA adds `async_object_analysis` and `tma_transform`.
This closure can be compiled as an optional evaluation library without exposing
unimplemented helper symbols to production or changing host/device ABI.

Literal S runtime integration is substantially larger:

| Layout | B bytes | S bytes |
|---|---:|---:|
| DeviceFuture | absent | 64, alignment16 |
| HbfRequest | 64 | 128 |
| SharedRequestSlot | 128 | 192 |
| SharedControlHeader | 384 | 576 |
| TensorMap slot | absent | 448 |
| channel config/state | absent | 1024 / 256 |

S `hbf_device.cuh:268–279` defines the future (the older ledger points to its
assert region). Emitters marshal its exact offsets. A literal ABI9 port must
update all layouts, region sizing, validation, initialization, accessors,
loader consumers, embedded PTX and tests together. Importing three helpers
would be invalid.

An additive timing-only implementation can instead retain base ABI4 and use a
complete device-local future with explicit opt-in. Reference, hybrid and
capacity async must remain rejected until independently implemented. A
synchronous fallback must never be labeled async overlap. Predicated consumers
require may/must handling; initial general CFG support is explicitly rejected.

## Confirmed red tests and unresolved runtime invariants

`tests/cpu/ptx_future_gold_test.cpp`, compiled against unmodified S, fails for
predicated first use, multiple conditional uses and conditional ordering drain.
It also catches admission of the deliberately unsupported diamond/loop/early
exit subset. The unconditional control passes. `ptx_ir_gold_test.cpp` reproduces
lost decorated-load coverage, declaration-line swallowing and acceptance of an
unterminated block comment. Logs: `results/gold/async-counterexample/`.

Capacity futures retain resolved frame addresses until consumption, while
neither B nor S establishes a consumer-held cache-frame pin/lease. S TMA can
queue32 page futures before materialization. A single-page checksum cannot
prove safety under eviction; capacity async remains closed pending a complete
lease/release/rollback protocol or an independently proven alternative.

TMA static groups, predicated polling, phase reuse, group N>0 and barrier/group
timeout termination remain unproven. S barrier/group wait counters are declared
but have no device update site. `ShadowFutureMachine`, `BulkGroupTracker`,
`TmaBarrierTracker` and `expand_and_split` are CPU oracles, not runtime callers.

## Capacity/routing donor X

B and X have identical blobs for `model_inventory.py`, `placement_policy.py`,
`trace_schema.json`, placement tests, `hbf_trace_timing.cpp` and its two timing
tests. Do not duplicate these imports. B `trace_replay.py` has stronger full
profile provenance; B `trace_validation.py` checks duplicate/missing tensor,
dtype, expert, shard, offset and observed-byte identity. Preserve them.

The missing capture-only closure is `trace_collector.py`,
`routed_capture_compat.py` and selected `runner.py` wiring. Staging/runtime
imports are a separate dependency set. Preserve base MQSim QD admission
(`12ef138`), geometry/interleaving (`7552547`), async coverage (`975f0d5`), and
`.loc` handling (`d68fff8`). Thermal, NAND cache/write policy and unknown dynamic
coverage limitations are not repaired by adding futures.
