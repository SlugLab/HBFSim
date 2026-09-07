# ABI4 TIMING Ordinary-Future Unit Implementation Plan

## Current execution addendum — 2026-09-07

Implementation has advanced beyond several historical unchecked items in this
plan, but no broad gate is promoted. The actual globaltimer-compatible future
image is retained at `c6-mapping/future-delay-attempt-004/`; the separate
untransformed native control is retained at
`c6-mapping/native-control-attempt-001/`. Both remain mapping
`NOT_PROVEN`. A focused native-control host build now passes after the reviewed
one-line `std::unique_ptr<Journal>` call repair. GPU002 then stopped at the
foreign-process resource guard before an owned kernel launch. Fresh GPU003
completed 67 launches and passed independent output/counter/trace arithmetic,
but all D=20,000 ns futures were ready before work. K0/K4096 work medians were
both 7,040 ns, so the selected W is `NON_IDENTIFYING`.

A subsequent single-active-lane fixed-work slice passed seven focused CPU
checks, a serial build, two transforms and four optimized-image collections.
The actual K=0/K=4096 cubins retain exact work markers and structural SASS shows
the 4,096 dependent IMAD chain in the intended load-to-wait/consumer interval;
this remains mapping `NOT_PROVEN`. Two sequential GPU children completed68
launches and independent analysis checked2,112 outputs,46 future groups and92
traces. The fixed work produced separated W (K=4096 medians6,928-7,008 ns
versus K=0 medians0), but all20 D=20,000 ns samples expired before work began.
The result is `NON_IDENTIFYING_PREWORK_COVERS_DELAY`, so the D/W overlap gate
is still open.

Preserve the exact guard, source, future004, native001 and accepted host-build
bindings for any next bounded condition. Do not alter G5 thresholds or claim
overlap/native-completion from mapping, host-build or this non-identifying GPU
evidence. Shared public ABI,
default-OFF behavior and the existing timeout remain unchanged. C6.3, G5, full
C6.4 and formal admission remain open; formal DONE remains 0. The checklist
below is retained as design/history and must not be read as a current completion
receipt without the corresponding actual evidence.

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:executing-plans` to implement this plan task by task. Steps use checkboxes for tracking. Parent owns integration and commits; do not create additional workers or commit without its instruction.

**Goal:** Implement and validate one complete, explicitly enabled ordinary-load future unit for physically backed TIMING ranges using the existing FAST scalar model, while retaining shared control ABI4 and synchronous production defaults.

**Architecture:** Add a separately versioned 64-byte kernel-local future and separately versioned lane metadata around the existing range table, scalar service reservation and CUDA-domain lifecycle. Close host capabilities, exact transformation identity, loader initialization and device state before enabling the new emitter. Preserve the synchronous resolver and reject unsupported configurations instead of silently falling back.

**Tech Stack:** C++20, CUDA/PTX, existing CMake helper-embedding chain, Python evaluation tools, CPU fixtures, optimized cubin/SASS evidence and guarded GPU correctness experiments.

---

## Status, authority and sequencing

This is a plan, not implemented support or a gate receipt. It was prepared against integration commit `dd853762b6ef94c522918a1efcf147293c3175a2`, frozen B `fc829992ecdc3ca68881656722b67a31067c5d33` and frozen S `f4dc28b2671c01939d98e4a968e6fb37b2e364d9`. The 64-byte design/build-command revision was checked at `13a416accf775339556459a514c9a37fc0c33b76`. Re-record the integration SHA and dirty patch when implementation starts.

The authorized phase-two contract and [selective donor map](sm120-donor-map.md) permit this complete additive unit. No renewed user permission is required. **C5 infrastructure is committed at `61de9f4`; its synchronous/known-delay prefix preserves the correction at `13a416a`. C6.1 is committed at `1f0f45b`. C6.2 plugin/build/loader admission is committed at `49ee96b`, with both reviews, all64 CPU checks closed and18/18 default-OFF checks. Separate native-image four-case and conditional-consumer GPU acquisitions are captured but unvalidated (see [actual checkpoint](../50-run-status.md#actual-checkpoint-hf010-and-native-conditional-consumers-2026-09-06)); C6.3 optimized dependency mapping and complete C6.4 GPU gold remain open.** Preserve those reviewed sources and their evidence; do not overwrite the helper from an earlier snapshot or bypass a busy-GPU guard.

Read [execution-plan C](../49-eval-audit/execution-plan.md), [G5](../49-eval-audit/claim-gates.md), [async semantics](../skills/04-cuda-async-cpasync-tma.md), [device/control ABI](../skills/05-device-helper-and-control-abi.md) and [server safety](../skills/10-server-experiment-safety.md). The literal S ABI9 dependency rule still applies to an actual S helper port. This plan instead defines a new complete device-local ABI and leaves the shared host/device layout at version4.

Admission advances through three explicit states:

1. **C5 infrastructure only:** build option defaults OFF; a future request rejects with `timing_future_unit_incomplete`. No partially emitted kernel can be admitted.
2. **C5 plus C6 implementation complete:** exact module/helper/owner contracts and all CPU/compile negatives pass. Only explicitly bounded correctness-gold launches may start under the existing GPU resource guard. This is not formal performance admission.
3. **Ordinary-subset gold complete:** a receipt binds the exact source, build, helper, target, profile, PTX/cubin and positive/negative evidence. Only matching ordinary-load conditions may be considered for formal execution. A receipt cannot authorize a broader async family or survive input/build changes.

Capacity frame leases, reference/hybrid async service, TensorMap publication and TMA barrier/group semantics remain independently blocked. They are **not prerequisites for this kernel-local TIMING subset** and are not closed by completing it. Full G5/EQ2 remains open for those families.

## Supported contract

| Dimension | Initial contract |
|---|---|
| Opt-in | CMake `HBFSIM_ENABLE_TIMING_FUTURES=OFF` by default; transform request explicitly names `timing_load_future_v1`. No environment-only enablement. |
| Memory | Scalar ordinary `ld.global.{u,s,b,f}{8,16,32,64}` forms that actually assemble and have proved typed def/use and value preservation. Reject illegal type/width combinations. Native non-HBF loads remain native. |
| HBF backing/model | Registered CUDA device allocation; TIMING range; FAST scalar profile; no empirical curve; positive scalar profile values and finite timeout. Initial validated time scale is1. |
| Control flow | Single straight-line region ending in unconditional return/exit. Predicated ordinary loads, scalar operations and drains are supported with executed-path state. Branches, loops, calls and conditional exits reject. |
| Ordering | Every executed consumer waits once for its active producer. Stores/fences, overwrites and terminal exit drain pending loads on their executed paths. Non-HBF output stores remain ordinary native stores; HBF writes are outside this read-only future mode and reject before the write. |
| Data lifetime | Native TIMING data is read at issue and retained until consumption. Original TIMING address semantics remain unchanged. No capacity frame, cross-kernel future or host-owned pending request is introduced. |
| Grouping | Preserve B's active-mask, range ID and complete logical-page grouping. One media reservation per group; per-lane futures and bytes are recorded separately. Do not replace grouping with per-lane accounting. |
| Unsupported | CAPACITY, reference/hybrid/empirical timing, atomics, vector/decorated memory forms outside the proved table, general CFG, `cp.async`, TMA and TensorMap operations reject explicitly. |
| Unknown coverage | Never relabel unknown/unmodeled work as future-modeled. Existing synchronous TIMING fallback stays unchanged; an explicitly requested future module cannot use that fallback to obtain admission. |

## Dependency and file map

Paths are relative to `/root/hbfsim-exp/eval-base-integration`. New names below are proposed files; the others already exist. Do not edit unrelated runtime, storage, MQSim, capture or prefetch components.

| Files | Responsibility |
|---|---|
| New `include/hbfsim/timing_future_abi.hpp` | CUDA-compatible 64-byte future, separate lane-metadata layout, version constants, typed states, capabilities and host/device pure validation rules. It is not a shared-control extension. |
| `include/hbfsim/launch_gate_abi.hpp`; `src/cuda_runtime/context.cpp` | Additive versioned host capability publication, derived from the actual context/profile. Preserve v2/v3 callers. |
| `include/hbfsim/timing_binding.hpp`; `src/cuda_runtime/timing_binding.cpp` | Owner/module capability matching, CUDA domain and generation binding, initialization/retirement rollback. |
| `include/hbfsim/coverage.hpp`; `src/cuda_runtime/coverage.cpp`; `src/cuda_runtime/launch_gate.cpp` | Strict future admission, immutable capability metadata, module-global verification and transactional enable/disable. |
| `src/ptxpass_hbf/plugin.cpp`; `transform.{hpp,cpp}` | Explicit dispatch, transform-mode identity, exact helper authenticity, complete manifest and no partial-output fallback. |
| `src/ptxpass_hbf/ptx_ir.{hpp,cpp}`; `ptx_analysis.{hpp,cpp}` | Accurate scalar/setup def/use, statement spans, executed-path consumers/drains, finite resource bounds. |
| New `src/ptxpass_hbf/future_transform.{hpp,cpp}` | Load-only future emission from the admitted plan and source spans. |
| `src/cuda_runtime/device/hbf_device.{cuh,cu}` | Additive range/group validation, service issue, typed model poll, dependency wait, scoped write rejection, timeout/error accounting. Parent released them at `13a416a`; preserve that clock correction. |
| `CMakeLists.txt`; existing `cmake/EmbedDevicePtx.cmake` | Build option, source inclusion, explicit NVCC definition/include dependency and regenerated embedded helper. Change the embed script only if a demonstrated need exists. |
| New `tests/cpu/timing_future_abi_test.cpp`, `timing_future_state_test.cpp`, `ptx_future_transform_test.cpp` | Executable record/state/emission regressions. |
| Existing CPU tests: `timing_binding_test.cpp`, `coverage_gate_test.cpp`, `module_identity_test.cpp`, `context_lifecycle_test.cpp`, `device_helper_abi_test.cpp`, `ptx_ir_gold_test.cpp`, `ptx_future_gold_test.cpp`, `ptx_transform_test.cpp`, `ptx_async_copy_coverage_test.cpp` | Preserve baseline and add bounded contract/negative cases at existing boundaries. |
| Existing integration tests: `test_device_helper_ptx.py`, `test_cuda_module_association.py`, `test_timing_gate_binding.py`, `test_ptxpass_plugin.py`; new `test_timing_future_unit.py` | Actual plugin, fake-driver loader failure paths and compile-only future unit checks. All are under `tests/integration/`. |
| New `tests/gpu/timing_future_correctness.cu`; `benchmarks/cuda/CMakeLists.txt`; new `scripts/eval/run_async_overlap.py`, `test_run_async_overlap.py` | Bounded ordinary-future correctness and D/W acquisition, plan by default. |
| Existing `scripts/eval/audit_sass_mapping.py`, `test_audit_sass_mapping.py`; new `validate_timing_future_mapping.py`, `test_validate_timing_future_mapping.py` | Reuse immutable disassembly collection; add a distinct, scoped semantic mapping receipt. Mere disassembly collection remains `NOT_PROVEN`. |

`include/hbfsim/protocol.hpp`, `src/host_service/control_layout.hpp`, daemon rings, capacity runtime and TensorMap code need no layout or semantic change for this unit. Existing module identity/load-transaction utilities can continue transporting a 32-byte digest; extend their tests rather than changing their representation unnecessarily.

## C5.1 — Freeze the module/device ABI with CPU RED first

**Files:** new ABI header and ABI/state tests; existing helper ABI/protocol tests; CMake test registration.

- [ ] Add a CPU ABI test that fails before the new contract exists. Freeze the 64-byte token and separate 32-byte metadata below with `sizeof`, alignment and every field offset; check that the old control/request/slot/resolver sizes are still 384/64/128/16 bytes. The user-specified future size remains 64; the previous 80-byte proposal is superseded. Equal size does not make this token binary-compatible with S's `DeviceFuture`: it has new versioned field meanings and matching emitter/helper marshaling.

```cpp
// Proposed new module-local ABI, independent of shared control ABI4.
struct alignas(16) DeviceTimingFutureV1 {
    std::uint64_t control_alias;       // 0
    std::uint64_t control_generation;  // 8
    std::uint64_t issue_ns;            // 16, GPU clock domain
    std::uint64_t ready_ns;            // 24, modeled completion
    std::uint64_t deadline_ns;         // 32, finite issue-relative limit
    std::uint64_t original_address;   // 40, unchanged for TIMING
    std::uint64_t reservation_id;     // 48, module/group accounting identity
    std::uint32_t state;              // 56, explicit enum values
    std::uint32_t status;             // 60, existing RequestStatus values
};
static_assert(sizeof(DeviceTimingFutureV1) == 64);
static_assert(alignof(DeviceTimingFutureV1) == 16);
static_assert(offsetof(DeviceTimingFutureV1, deadline_ns) == 32);
static_assert(offsetof(DeviceTimingFutureV1, state) == 56);
static_assert(offsetof(DeviceTimingFutureV1, status) == 60);

// Separate issue-to-terminal lane metadata; never appended to the token.
struct alignas(8) TimingFutureLaneMetadataV1 {
    std::uint32_t abi_version;         // 0, must equal1
    std::uint32_t struct_bytes;        // 4, must equal32
    std::uint32_t instruction_id;      // 8, frozen producer identity
    std::uint32_t bytes;               // 12, lane access bytes
    std::uint32_t group_mask;          // 16, frozen issue-time page group
    std::uint32_t group_leader;        // 20, elected issue-time lane
    std::uint64_t reservation_id;      // 24, must match the token
};
static_assert(sizeof(TimingFutureLaneMetadataV1) == 32);
static_assert(alignof(TimingFutureLaneMetadataV1) == 8);
static_assert(offsetof(TimingFutureLaneMetadataV1, reservation_id) == 24);
```

- [ ] Freeze metadata lifetime and helper transport as part of the same unit. The emitter owns one token slot and one metadata slot per producer in the executing thread's kernel-local storage. Issue writes all metadata before returning the 64-byte token; only then may the emitter publish its `valid` state. Later poll/wait/drain calls receive pointers to those same local slots and validate metadata version/size and matching reservation ID before any counter update. Instruction/width are validated explicit issue parameters; group mask/leader are outputs computed by issue, not guessed by the caller. No metadata pointer escapes the kernel, aliases another producer's live slot, or points to a temporary helper stack frame.
- [ ] Treat the pair as one nonduplicable producer state. The native-result bits and valid state remain separate retained lane values. A successful consume/drain marks the token terminal and clears valid exactly once before slot reuse; a predicated-off issue/drain leaves any earlier live state intact. Reissue through an unsupported backedge rejects; a permitted executed overwrite drains before reusing either slot. Do not reset shared reservation IDs or per-module accounting while any kernel can hold a token. Native tokens use reservation ID0 and explicit no-media metadata; group accounting applies only to nonzero modeled reservation IDs.
- [ ] Keep issue returning exactly `DeviceTimingFutureV1` by value. Pass the metadata output as a pointer to caller-owned local storage; poll and wait update that caller-owned token rather than operating on independently consumable copies. The consuming wait receives the retained native bits and returns them through the real call result with terminal status. Explicitly marshal the 64-byte return, metadata-pointer parameter and consuming result in PTX; test those call layouts against the generated helper. Both token ABI1/size64 and metadata ABI1/size32 belong to the module/helper capability identity. The total live storage is at least 96 bytes plus native bits/valid state; report actual registers/local-memory/spills instead of calling the whole implementation a 64-byte footprint.
- [ ] Add metadata RED cases: wrong version/size, mismatched reservation ID, wrong width/instruction binding, nonmember lane or invalid leader, producer-slot reuse before terminal state, and double consume/drain. Preserve lane/group conservation under predicate divergence. Do not reuse the issue-time group mask for a collective at a later divergent wait.
- [ ] Define `TimingFutureStateV1` values `Unissued=0`, `Native=1`, `Issued=2`, `ModelReady=3`, `Consumed=4`, `TerminalError=5`; define model-poll results `Pending`, `ModelReady`, `TerminalError`. A model poll alone never claims native data readiness. The emitter retains separate native-result bits until the consuming wait.
- [ ] Define module capability/config records with explicit `abi_version`, `struct_bytes`, required shared ABI4, mode `timing_load_future_v1`, scalar/no-empirical requirement, time scale1 and finite declared resource limits. Use fixed-width fields and offset assertions. Configuration is disabled at load and published only by the validated loader path.
- [ ] Run the ABI test RED and preserve the diagnostic. Implement the header without adding helper calls to production. Run ABI/protocol tests GREEN; a CUDA-off build must be able to compile the record/state tests.

Expected RED: missing versioned contract or a deliberately wrong field offset. Expected GREEN: record consistency only, never GPU ABI interoperability evidence.

## C5.2 — Publish actual context capabilities and bind lifecycle

**Files:** launch-gate ABI, context, timing-binding header/implementation and their CPU tests.

- [ ] Add RED cases to the existing fake initializer tests: a future module cannot become ready under legacy v2/v3 activation, reference/hybrid/empirical profile, wrong CUDA context/device, stale generation, failed initialization or retiring/quarantined owner.
- [ ] Add a host API v4 by preserving the complete v3 prefix and appending `activate_with_capabilities`. Its extra record contains version/size and a capability bitset derived inside `context.cpp` from validated options/profile; it is not supplied by an environment flag or trusted merely because a module asks for it. Legacy `activate` publishes no future capability.

```text
activate_with_capabilities(owner, control_alias, cuda_context, device,
                           validated_capabilities, generation_out)

future_ready = exact_module_contract
            && initializer_completed
            && module.domain == owner.domain == current_cuda_domain
            && module.generation == owner.generation != 0
            && required_capabilities subset_of owner.capabilities
            && !retiring && !quarantined
```

- [ ] Keep owner capability publication under the existing activation/retirement transition locks. A failed write leaves the module unready; invalidate enablement before alias retirement. Do not mutate capabilities on a live owner after launch. Re-creation obtains a new generation.
- [ ] Extend before-owner/after-owner module cases, failed initialization rollback, wrong-owner retirement, unload/context-destroy/device-reset invalidation and generation exhaustion tests. Run these and existing context lifecycle tests GREEN.

No device admission is enabled by C5.2. Current CUDA synchronization before control unmapping remains the lifetime boundary; no extra capacity lease is introduced.

## C5.3 — Separate transformation identities and close loader/coverage admission

**Files:** plugin, transform request, coverage, launch gate and existing identity/loader/plugin tests.

- [ ] Write a RED plugin test using identical original PTX for synchronous and future requests. Require separate identities and require rejection of unrecognized mode, incomplete unit, changed helper version and attempted mode switching on previously transformed PTX.
- [ ] Keep the existing synchronous identity/default behavior. For the new mode derive the digest from a canonical, domain-separated record:

```json
{
  "identity_domain": "hbfsim.timing-load-future.v1",
  "original_ptx_sha256": "64 hex characters computed from original bytes",
  "transform_mode": "timing_load_future_v1",
  "device_future_abi": 1,
  "device_future_bytes": 64,
  "lane_metadata_abi": 1,
  "lane_metadata_bytes": 32,
  "shared_control_abi": 4,
  "helper_sha256": "64 hex characters computed from embedded helper bytes",
  "admission_contract_sha256": "64 hex characters computed from subset and limits"
}
```

- [ ] Record trusted emitted PTX digest plus its immutable mode/contract. Repeated same-mode multi-kernel instrumentation preserves the original module identity. Reject mixed-mode conversion of an already emitted module in v1. A conflicting same-module/kernel capability manifest rejects instead of replacing the prior capability through `insert_or_assign`.
- [ ] Add an explicit transformed-module requirement symbol; presence of a future helper in the shared embedded helper text is not proof a kernel uses futures. Bind required version/mode to the trusted PTX identity, loaded module bytes and manifest. Preserve the one-shot load transaction, exact module handle association and helper authenticity check.
- [ ] Extend loader initialization: validate requirement/config sizes and versions, copy the disabled configuration, publish generation/config, publish enablement last, and mark the registry ready only after all copies succeed. On failure clear enablement/alias; if clearing cannot be proved, leave the module unready/quarantined. Retirement clears enablement before existing alias invalidation and unmap.
- [ ] Enforce strict future admission before the ordinary TIMING unmodeled fallback. Reject missing/opaque manifests, layout mismatch, missing capability, unsupported instructions, capacity addresses, wrong mode and all loader failures for explicitly requested future modules. Keep ordinary synchronous fallback policy unchanged. Device range checks remain necessary for addresses computed after launch.
- [ ] Run fake-driver and plugin RED/GREEN with no GPU. Missing-symbol, short-symbol, failed-copy, stale-handle, cross-context and same-PTX/different-mode cases must all reach refusal before launch enqueue. C5-only binaries still return `timing_future_unit_incomplete` for executable future requests.

## C5.4 — Implement complete additive device state and preserve coalescing

**Files:** new ABI/state tests, device helper pair after parent release, helper ABI/integration tests, CMake include/definition dependencies.

- [ ] Add table-driven CPU RED cases against the same pure arithmetic/state functions used by the device path. Do not create a second independent simulation and call that device proof.

```text
issue=100, ready=300, deadline=1000, now=299 -> Pending
issue=100, ready=300, deadline=1000, now=300 -> ModelReady
issue=100, ready=900, deadline=400,  now=950 -> TerminalError(Timeout)
issue=100, ready=300, deadline=400,  now=950 -> ModelReady
wrong alias/generation or shutdown/fault             -> TerminalError
ready/deadline arithmetic overflow or zero timeout  -> rejection
consuming TerminalError                              -> rejection
second consume/drain                                -> no second reservation release
```

The fourth case means modeled completion occurred before its deadline, even if the program checked later. Native readiness must still be established by the native-result dependency. An ordinary CUDA load is not a cancellable operation; helper timeout evidence must not claim a hung native load was canceled.

- [ ] Implement issue validation in this order: enabled compatible module contract; alias/generation and complete ABI4 header geometry; bounded range count and address lookup; permissions/whole-access bounds; TIMING/FAST/no-empirical/time-scale1 policy; media descriptor; issue-time active page grouping. Reject before executing a native load on an invalid or capacity address. An unregistered valid native address receives a native token without modeled service.
- [ ] Preserve B's `__activemask`, `__match_any_sync` range ID and both halves of logical-page comparison. The elected group leader reserves exactly one page service and broadcasts the same ready/deadline/status/reservation identity to the group's lanes. Freeze each lane's group mask/leader/instruction/width in its C5.1 metadata slot, retaining the matching reservation ID through terminal accounting. Later waits operate per lane and must not reuse issue-time warp masks for collectives after divergence.
- [ ] Use the existing scalar model arithmetic, with reservation and waiting separated for the new path:

```text
arrival = fresh_gpu_clock_read()
transfer_end = CAS_reserve(max(previous_tail, arrival) + scaled_transfer_ns)
ready = max(arrival + scaled_read_latency_ns, transfer_end)
deadline = arrival + request_timeout_ns
```

Check overflow and a finite timeout in CAS contention and polling loops. Preserve the synchronous resolver's original grouping, waits, profile treatment and final `resolved_address`; never redirect it through per-lane futures. Reuse the parent's corrected clock-reading primitive, with repeated-load optimized-code tests, instead of adding an independently hoistable timer implementation.

- [ ] Implement typed model poll and consuming wait. Capture the issuing alias/generation in the token and revalidate at entry and during waits; inspect fault/shutdown. Liveness uses GPU-relative elapsed time while observing heartbeat changes, never `GPU_time - host_heartbeat_timestamp`. A bounded timeout/fault returns a distinct terminal status and traps through the existing fault path before value use.
- [ ] Add module-local counters/traces for lane issues, grouped media reservations, model-ready transitions, dependency consumes, ordering drains, terminal errors, pending count, native/bypass bytes and rejected accesses. Define terminal conservation as `issued = consumed + drained + terminal_error + pending` for mutually exclusive lane terminal categories. Keep completion/readiness counters separate because they are not terminal categories. Existing shared FAST counters retain their grouped meaning; no shared ABI growth. Reject reservation-ID exhaustion and trace overflow without reusing IDs or silently dropping records. Validate one grouped completion update despite lane-local waits/drains; predicate divergence must not multiply the media completion counter.
- [ ] Bound outstanding state by an explicit positive per-thread limit recorded in the transformation contract; reject plans above the implementation's checked limit. Derive warp/CTA bounds from actual launch geometry, reject overflow/excess, and do not interpret the current CPU plan's zero CTA bound as a proof. The limit is a resource contract, not a fitted scientific tolerance.
- [ ] Add grouped-lane CPU tests for one page, two pages, distinct ranges, inactive/predicated lanes and pages differing only in their upper32 bits. Compile the complete helper with optimization and run existing helper ABI/synchronous tests. Still no future admission before C6 emission closes.

## C6.1 — Admit real kernel setup and emit executed-path waits

**Files:** IR/analysis, new future transform, transform dispatch, CPU parser/analysis/emission tests.

- [ ] Add RED fixtures that are complete PTX entries with pointer parameters, `ld.param`, address conversion, thread ID arithmetic and terminal output. Precisely extend def/use for admitted setup forms; never treat unknown instructions as harmless or remove parameter setup to make the analysis pass. Include multiline statements, declaration-adjacent instructions, `.loc`, comments and malformed-comment rejection.
- [ ] Emit from validated statement byte spans. Do not replace a physical source line containing multiple logical statements or a declaration. Preserve unrelated functions and the synchronous classifier's supported/excluded/unsupported accounting.
- [ ] Implement each load producer with a separate `valid` token and retained native-result register. Initialize token state before predicates; execute issue and native load only when the producer executes. A false producer leaves the prior architectural destination value intact. At a consumer, guard the wait by both the consumer's current execution predicate and the stored producer token, never by the producer's current predicate register value.

```text
producer executes:
    token = validate_and_issue(address, width, instruction_id, &lane_metadata)
    reject terminal token before memory access
    native_bits = original_typed_load(token.original_address)
    valid = true

consumer executes and valid:
    result = consuming_wait(&token, &lane_metadata, native_bits, Dependency)
    reject result unless successful
    original_destination = result.native_bits
    valid = false
consumer executes:
    execute original consumer
```

`consuming_wait` consumes the loaded bits and returns them through the actual call result, creating a real dependency into the original consumer. Preserve integer width/sign extension and floating-point bit patterns. This is the emission contract; optimized SASS in C6.3 must prove it was retained. A nearby timer/helper call without this dependency is insufficient.

- [ ] Apply the same executed-path token logic to overwrite, store/fence and exit drains. A conditional drain clears only executing lanes; an unconditional terminal drain closes all live tokens. Native output stores receive range validation so an address unexpectedly falling in an HBF range rejects this read-only mode. Do not add asynchronous stores.
- [ ] Execute table-driven emitted-operation fixtures with a tiny deterministic CPU interpreter covering only the admitted fixture opcodes. The interpreter must assert actual consumed value, order and counter conservation; do not reduce the test to searching for a helper name or wait count. Reuse existing analysis counterexamples as inputs.

Required checks include:

```text
@p load r1; p changes; unconditional use -> wait iff that load executed
load r1; @false use; unconditional use  -> latter use waits
load r1; @p use; @q use; final use      -> each predicate combination correct
load r1; @false fence; final use       -> pending token preserved
load r1; @p overwrite r1; final use    -> original overwrite semantics preserved
two loads to same destination         -> first drained before executed overwrite
two independent loads then one use    -> both dependency sets respected
native + TIMING lanes/pages            -> correct bytes and separate accounting
```

- [ ] Keep diamond, loop/backedge reissue, conditional exit, call, unknown def/use, atomic/vector/decorated forms and every cp.async/TMA form as executable rejection tests. Run CPU RED/GREEN before enabling the complete future dispatcher.

## C6.2 — Close plugin/build integration with a default-off proof

**Files:** CMake, transform/plugin, new integration unit test and existing plugin/helper/coverage regressions.

- [x] Add a test that requests the new mode with the build option OFF and requires explicit rejection without emitted future calls. Add an ON-build test with complete closure and a strictly compatible fixture; it must traverse the actual plugin, produce a complete manifest and assemble with the actual embedded helper.
- [x] Wire all C5/C6 sources together under `HBFSIM_ENABLE_TIMING_FUTURES`, explicitly passing the define and ABI-header include/dependency to the NVCC custom command. Preserve the single numeric target policy and exact embedded bytes; missing helper PTX, incompatible target and forged preexisting symbols reject.
- [x] Remove `timing_future_unit_incomplete` only after every required emitter/helper/loader/manifest component is compiled into that build. Keep the public transform mode opt-in and keep formal evaluation receipts absent until gold. No C5-only helper import or CPU-only analysis result enables dispatch.
- [x] Run optimized CUDA compile-only tests for all admitted scalar widths and predicate fixtures. Assert device return offsets, no unresolved helper symbols, matching helper/version identity and unchanged ABI4 layout. Capture original/transformed PTX and cubin hashes plus exact compiler/linker commands.
- [x] Run default-off synchronous, range/coverage, parser/comment/.loc, module lifecycle and MQSim admission regressions once after source stabilization. Never reconfigure the parent's existing frozen builds.

Closure at `49ee96b`: complete admission requires explicit ON, CUDA13 and
numeric sm120. Original PTX, mode, helper bytes, ABI64/32/shared4 and fixed16
producer/1024 block-thread limits form one identity. Actual positive grid/block,
PTX req/max axes and cumulative trace capacity validate at every launch. The
module owns a4MiB trace array; the host reserves at most three records per static
producer per actual launched lane and does not refund unproved enqueue failures.
Enable is published last; retirement synchronizes the owning domain and clears
enable before alias/config. An unproved clear quarantines the owner.

Only ordinary geometry-bearing driver and mapped runtime launches are admitted.
Cooperative, opaque, graph and extras paths remain closed for futures. Same-mode
multi-kernel images preserve original identity but validate each actual image's
present immutable kernel-manifest subset and the exact selected kernel at launch.
Reviews closed selected-entry comment/prefix metadata, old-image reactivation,
and both cooperative runtime alias counterexamples.

Evidence: `results/gold/timing-future-unit/c6-unit/handoff/attempt-003/`
(23 source files,8 build files,39 artifact hashes), plus
`results/gold/timing-future-unit/c6-unit/closure-attempt-001/`. Seventeen typed actual-plugin
controls assemble with normal optimization;49 fake-driver loader scenarios pass.
The new CPU phase initially passes63/64 in25.93s. Its sole failure was a source
guard selecting a forward declaration; the reviewed test-only correction passes
the focused CTest in0.08s and still rejects an unsafe-identity mutation. All64
checks are therefore closed, with the initial failure retained. A fresh default
OFF build passes18/18 in18.30s and matches C5 helper bytes exactly. These are
CPU/compile facts; neither SASS semantic gold nor hardware execution is proved.

## C6.3 — Prove optimized native/issue/wait/consumer ordering

**Files:** existing disassembly collector; new scoped mapping validator and tests; new GPU gold source and benchmark CMake target.

Initial read-only research is retained at
`results/gold/timing-future-unit/c6-mapping/source-audit-attempt-001/`.
One archived optimized u32 control shows native `LDG` intoR28, the wait's ready
return intoR4, caller transfer intoR5 and the final store usingR5. Collection
remains `NOT_PROVEN`: the fixture has no surviving useful independent work and
all lanes target the same output. Its wait clocks precede the explicit native
value use; scoreboard/control-word and call/reconvergence behavior must be
resolved before a clock/native-completion ordering claim. This observation alone
does not establish a hardware defect. The receipt binds archived bytes after
the build and is not contemporaneous build provenance. A new gold source needs
checksum-retained independent arithmetic and unique per-lane outputs. No GPU
execution or semantic receipt resulted from this audit.

- [ ] Add RED mapping fixtures: a helper present without a wait dependency; a consumed value taken from the pre-wait register; load or consumer moved across the declared boundary; reused source mapping for a different cubin; incomplete mapping; and incorrect helper/version. All refuse a successful semantic receipt. Fixture disassembly is `TEST_ONLY`/`MOCK`.
- [ ] Keep `audit_sass_mapping.py` as the immutable collection step with `mapping_validation=NOT_PROVEN`. The new validator consumes its frozen PTX/cubin/disassembly plus an exact kernel/instruction/dependency map; hashes and binding must match. It checks each admitted producer's native load, model issue, independent-work region, executed wait/drain and consumer dependency. Save semantic review evidence where the optimized form cannot be established mechanically; uncertainty rejects approval.
- [ ] Compile the gold source with normal optimization. Use retained loaded-value input and returned-value output across the wait helper, with a no-inline/opaque boundary where needed; inspect the emitted artifact to establish that the compiler retained it. Do not rely on a source annotation or global `-O0`. Include optimized timer-reload proof inherited from the parent's known-delay correction.
- [ ] Record actual registers, spills, shared memory and launch geometry; report theoretical occupancy separately from observations. Verify whole-warp same-page and distinct-page cases as well as sparse active masks; one active lane per warp alone cannot establish preserved coalescing.

## C6.4 — Acquire bounded ordinary-future gold and retain claim limits

**Files:** new GPU correctness source and overlap runner/tests; existing ResourceGuard and immutable artifact utilities reused without changing their authorization rules.

Checkpoint, 2026-09-06: the parent-authorized one-block/32-lane ordinary-u32
correctness smoke ran all four fixed same/distinct-page and dense/sparse cases.
All128 outputs matched and the recorded72 issued/model-ready/consumed lanes,
38 issued/completed groups and144 trace records conserve with zero pending,
terminal-error or overflow. Observable retirement steps succeeded; context
destroy remains a called-but-unobservable void API. The controller state is
`CAPTURED_UNVALIDATED`, and live driver JIT remains unbound to the prior optimized
cubin. This is the narrow correctness smoke contemplated below. It does not
satisfy C6.3, the D/W matched-control experiment, G5, overlap/native-completion
timing, the complete C6.4 lifecycle or any TMA requirement.

- [x] Acquire the fixed one-warp same/distinct-page and dense/sparse ordinary-u32 output/conservation smoke, retaining raw counters, traces and cleanup state as `CAPTURED_UNVALIDATED`.

- [ ] Add CPU runner RED cases with deterministic subprocess fixtures: wrong D/W/operation/treatment, wrong kernel/build/profile/receipt, failed checksum, leaked pending count, contaminated guard, timeout/interruption, forged SASS receipt and test-only output under formal runs. Reuse durable stdout/stderr/raw/environment/provenance conventions; no mock fixture can become a measured row.
- [ ] Implement `run_async_overlap.py` with plan as default, matrix-sourced `ordinary_future_load` selection and one explicitly bounded condition per execution. Use `GPU_EXCLUSIVE`, pinned GPU UUID/device, start/periodic/end guard and immutable attempt paths. Preserve blocked/failed/interrupted evidence. Parent owns actual GPU launches in this phase; do not reset/settings-change or signal unrelated processes.
- [ ] Add a separate explicitly scoped known-D module configuration outside shared ABI4 for this future gold. Match native, synchronous old issue-stall, matched-zero and new future treatments; use the same data, kernel, input, coverage and launch geometry. The new future configuration creates a ready deadline at issue and waits at consumption; it must not call the synchronous known-delay wait at issue. D=0 truly disables synthetic delay while retaining validation/grouping/dependency handling. Keep a positive scalar profile, do not set profile read latency to zero, and do not double-charge scalar service plus synthetic D.
- [ ] After complete CPU/compile/identity closure, run a tiny parent-authorized correctness smoke, then only the selected ordinary D/W cells. Save actual issue enter/exit, native load/value boundary, independent-work interval, consuming-wait enter/exit, consumer, total-kernel CUDAEvent, checksum and all conservation counters. Keep host/GPU clocks and control overhead distinct.
- [ ] Validate `max(0,D-W)` against measured independent work and the matched controls using frozen G5 thresholds: mean absolute residual error at most `max(0.10us, 0.10D)` and P95 at most `max(0.20us, 0.20D)`; D=0 reports absolute noise. Check issue cost separately so old issue-stall's near-zero consume residual cannot masquerade as overlap. No tolerance changes after observing results.
- [ ] Run bounded negative GPU cases for predicate paths, invalid configuration, stale generation in isolated test state, timeout and fault/liveness handling. Preserve the exact terminal result; intentional negative timeout tests are successful rejection evidence, whereas any timeout in a valid scientific cell fails that cell.
- [ ] Write a scoped `ORDINARY_TIMING_FUTURE_V1` receipt only after all applicable semantic, SASS, checksum and conservation gates pass. The receipt must explicitly list excluded capacity/reference/hybrid/empirical/cp.async/TMA/CFG families. Do not promote it into a full G5/EQ2 completion claim or authorize unmatched matrix conditions.

## Execution commands and durable evidence

These are implementation-time commands, not commands executed while writing this plan. Register new CTests with the names shown below. Work from the active checkout and use new build directories. The CPU run excludes hardware tests; compile-only CUDA work is separate from any parent-authorized GPU execution.

Read-only command verification used the existing `build-eval-implementation/CMakeCache.txt`, `build-eval-known-delay-gcc13/CMakeCache.txt` and `build-eval-known-delay-gcc13/CMakeFiles/TargetDirectories.txt`. The frozen CPU build sets `CMAKE_DISABLE_FIND_PACKAGE_PkgConfig=ON`: this avoids the base libbpf attach-loader branch, which is not guarded by `HBFSIM_ENABLE_CUDA` and can otherwise introduce CUDA probe commands into a CUDA-off build. Preserve that workaround here instead of expanding this task to repair base dependency configuration.

The frozen CUDA cache records both `HBFSIM_CUDA_ARCHITECTURE=120` and `CMAKE_CUDA_ARCHITECTURES=120`. The custom helper/benchmark command uses `HBFSIM_CUDA_ARCHITECTURE`; the top-level CMake currently derives it from `CMAKE_CUDA_ARCHITECTURES`. Pass both consistently, and inspect the generated command for `--gpu-architecture=compute_120` before compiling. The existing target names `hbfsim_device_ptx`, `ptxpass_hbf_plugin` and `hbfsim_launch_gate` were verified in source and the frozen target directory listing. `timing_future_correctness`, `timing_future_unit_compile` and the other future-specific target/test names below are **new names to register in C6**, not targets claimed to exist now.

```bash
cd /root/hbfsim-exp/eval-base-integration
cmake -S . -B build-eval-timing-future-cpu \
  -DHBFSIM_ENABLE_CUDA=OFF -DHBFSIM_ENABLE_EVAL_TOOLS=ON \
  -DCMAKE_DISABLE_FIND_PACKAGE_PkgConfig=ON \
  -DHBFSIM_ENABLE_TIMING_FUTURES=ON -DBUILD_TESTING=ON \
  -DCMAKE_BUILD_TYPE=Release -DCMAKE_CXX_COMPILER=/usr/bin/g++-13
cmake --build build-eval-timing-future-cpu --parallel 2
ctest --test-dir build-eval-timing-future-cpu --output-on-failure \
  -R '^(timing_future_abi|timing_future_state|ptx_future_transform|timing_binding|coverage_gate|module_identity|context_lifecycle|device_helper_abi|protocol|eval_ptx_ir_gold|eval_ptx_future_gold|ptx_transform|ptx_async_copy_coverage)$'
```

Expected initial RED comes from the specific new assertion/API absence described in each task, not an unrelated tool/configuration failure. Save the first relevant failure before the implementation fix. Expected final output: every selected test passes, with no silently skipped new test.

```bash
cmake -S . -B build-eval-timing-future-cuda \
  -DHBFSIM_ENABLE_CUDA=ON -DHBFSIM_ENABLE_EVAL_TOOLS=ON \
  -DHBFSIM_ENABLE_TIMING_FUTURES=ON -DBUILD_TESTING=ON \
  -DCMAKE_BUILD_TYPE=Release -DCMAKE_CXX_COMPILER=/usr/bin/g++-13 \
  -DCMAKE_CUDA_HOST_COMPILER=/usr/bin/g++-13 \
  -DCMAKE_CUDA_COMPILER=/usr/local/cuda-13.0/bin/nvcc \
  -DCUDAToolkit_ROOT=/usr/local/cuda-13.0 \
  -DHBFSIM_CUDA_ARCHITECTURE=120 -DCMAKE_CUDA_ARCHITECTURES=120
cmake --build build-eval-timing-future-cuda --parallel 2 \
  --target hbfsim_device_ptx ptxpass_hbf_plugin hbfsim_launch_gate timing_future_correctness
ctest --test-dir build-eval-timing-future-cuda --output-on-failure \
  -R '^(device_helper_ptx|ptxpass_plugin|timing_future_unit_compile)$'
```

Verify the separate default-off configuration and CPU runner/receipt tests:

```bash
cmake -S . -B build-eval-timing-future-off \
  -DHBFSIM_ENABLE_CUDA=ON -DHBFSIM_ENABLE_EVAL_TOOLS=ON \
  -DHBFSIM_ENABLE_TIMING_FUTURES=OFF -DBUILD_TESTING=ON \
  -DCMAKE_BUILD_TYPE=Release -DCMAKE_CXX_COMPILER=/usr/bin/g++-13 \
  -DCMAKE_CUDA_HOST_COMPILER=/usr/bin/g++-13 \
  -DCMAKE_CUDA_COMPILER=/usr/local/cuda-13.0/bin/nvcc \
  -DCUDAToolkit_ROOT=/usr/local/cuda-13.0 \
  -DHBFSIM_CUDA_ARCHITECTURE=120 -DCMAKE_CUDA_ARCHITECTURES=120
cmake --build build-eval-timing-future-off --parallel 2
ctest --test-dir build-eval-timing-future-off --output-on-failure \
  -R '^(timing_future_default_off|ptxpass_plugin|ptx_transform|ptx_async_copy_coverage)$'
PYTHONPATH=scripts/eval python3 -m unittest -v \
  test_run_async_overlap test_validate_timing_future_mapping test_audit_sass_mapping
```

The `timing_future_default_off` test must reject future requests and retain synchronous output. These commands do not launch the GPU correctness executable. Do not reinterpret compile success as GPU execution.

Store evidence in new immutable directories under `results/gold/timing-future-unit/`: `c5-abi`, `c5-capability`, `c5-loader`, `c5-device`, `c6-emitter`, `c6-compile`, `c6-sass`, and `c6-ordinary-gpu`. Each implementation attempt gets its own directory with exact argv, UTC, source/dirty patch, inputs, tool and artifact hashes, stdout/stderr, exit code and CPU/compile/GPU scope. Preserve RED and GREEN separately. Re-runs use a new destination; no log overwrite. If a command fails, preserve that failure and diagnose it before changing direction.

## Completion checklist

C5.1–C5.4 checkpoint: spec and quality review pass. Five discovered admission/
accounting failures have executable RED/GREEN evidence. Focused compile/fake-driver
18/18 and default-off 7/7 checks pass. The final CPU phase is 59/60 plus a passing
recheck of the sole concurrent worker-import failure. Exact 25-file source,
commands, binaries and boundaries are frozen under
`results/gold/timing-future-unit/handoff/attempt-001/`. C5 supplies compiled
infrastructure only; it keeps `kUnitComplete=false`, no enabling write and no
future transform admission. The whole-unit checklist below remains open for C6.

- [ ] Future token exactly 64 bytes; separate versioned metadata has complete issue-to-terminal lifetime/accounting. Shared ABI4 sizes/offsets and public legacy behavior unchanged; no S ABI9 records imported.
- [ ] Default-off and C5-incomplete requests reject; complete C6 opt-in uses one exact versioned unit.
- [ ] Owner/profile, module identity, helper bytes, loader initialization and launch policy agree.
- [ ] Full range checks, unchanged TIMING addresses and complete active-mask/page coalescing preserved.
- [ ] Predication, native-value dependency, drains, error/timeout and pending conservation proved for the declared subset.
- [ ] CPU/compile evidence and actual GPU/SASS evidence labeled separately; G5 thresholds unchanged.
- [ ] Capacity, reference/hybrid/empirical async, general CFG and cp.async/TMA remain rejected and independently blocked.
- [ ] Parent receives exact files, tests, evidence and remaining claim boundaries before integration; parent owns commits.

## C6.1 reviewed implementation checkpoint

Commit `1f0f45b` closes private emission and its CPU/compile proof. The public
mode still refuses `timing_future_unit_incomplete`, `kUnitComplete` remains false,
and the build option defaults OFF. This is not the C5+C6 complete-admission state.

`parse_module_spanned` and `emit_timing_futures` preserve exact source spans and
real setup def/use. Each supported scalar producer has separate local64/32-byte
state, native bits and a valid predicate. Actual emitted forward guards protect
call-parameter marshalling, since predicated parameter transfers do not assemble.
The wait's returned bits feed the actual consumer; executed overwrites,
store/fence and terminal drains preserve predicates and conservation. Required
launch dimensions are validated statically; reported CTA storage still assumes
the declared maximum block size, which C6.2 must enforce at actual launch.
The optional native-store guard rejects full-span HBF overlap and overflow.

Independent review closed malformed header/comment insertion, contradictory
required geometry, conditional second-producer handling, and quoted executable
tokens being erased. Legal module string metadata and quoted comments remain.
The final CPU phase is64/64 PASS in25.54s; focused CPU/compile9/9 and29 direct plus
helper-linked PTX fixtures pass. OFF helper bytes match the frozen C5 OFF image.
No kernel was launched. Exact14-file sources, reviewed/build/public-gate hashes,
all meaningful REDs and final status are frozen in
`results/gold/timing-future-unit/c6-emitter/handoff/attempt-005/`.
C6.2 admission was still open at that C6.1 checkpoint and is now closed by the
separate C6.2 evidence above. C6.3 optimized SASS dependency gold and C6.4 guarded hardware
controls remain open; the independent TMA/capacity/empirical families stay closed.

## C6.4 bounded correctness acquisition checkpoint

The first guarded ordinary-u32 correctness diagnostic ran on
`e96be28ae4b0be5dacc4f5944c64a554572bef9c` and retained its execution record
under `results/gold/timing-future-unit/c6-correctness/controller-attempt-001/`
and raw acquisition under `c6-correctness/attempt-001/diagnostic/`. It passed
the four fixed output/conservation cases and observable cleanup boundaries, with
the void context destroy explicitly unobservable. Its state remains
`CAPTURED_UNVALIDATED`. The unchecked C6.3 and C6.4 items above remain required;
this checkpoint neither creates an `ORDINARY_TIMING_FUTURE_V1` receipt nor
closes overlap, G5, native-completion timing, TMA or full lifecycle validation.
