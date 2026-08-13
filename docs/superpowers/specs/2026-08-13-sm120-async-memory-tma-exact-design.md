# SM120 Calibrated Async Memory and TMA Design

Date: 2026-08-13  
Target: NVIDIA RTX PRO 6000 Blackwell Server Edition (`sm_120`)  
Implementation boundary: HBFSim PTX pass, device runtime, host CUDA
interposition, offline SASS/Nsight Compute calibration

## 1. Purpose

HBFSim currently rewrites selected `ld.global` and `st.global` instructions by
calling a resolver before the original instruction. The resolver waits for
address translation and modeled completion before returning. This preserves a
bounded functional path, but serializes accesses that real hardware may issue
asynchronously and does not model TMA completion, TensorMap descriptors,
barrier phases, bulk async groups, or the Blackwell request channels discussed
in the motivating critique.

This design replaces that synchronous instruction-local model with a
calibrated shadow pipeline. The PTX pass exposes issue, dependency, ordering,
and completion points. The device runtime maintains asynchronous transactions.
Host interposition records TensorMap provenance. Offline microbenchmarks,
fixed-toolchain SASS, and Nsight Compute measurements fit an `sm_120` profile
that the runtime replays.

The result must preserve application data exactly while approximating the
observable timing and contention of the calibrated RTX PRO 6000 within the
accepted validation tolerances.

## 2. Scope and Success Criteria

### 2.1 Required support

The implementation covers every path raised by the motivating critique:

- ordinary global loads and stores with non-blocking issue and dependency-side
  waiting;
- TMA loads and stores, including unicast and cluster multicast;
- TMA completion through mbarrier phases and bulk async groups;
- host-created, host-updated, device-copied, and device-modified TensorMaps;
- TensorMap accesses whose expanded tile contains both ordinary HBM and
  registered HBF addresses;
- a calibrated four-channel GNIC2TEX proxy and two-channel GPCARB proxy;
- an inferred SMSP class based on runtime-visible PTX identifiers and measured
  contention;
- fixed-toolchain PTX-to-SASS compilation, SASS identity, register, spill, and
  occupancy validation;
- timing-only and file-backed capacity modes.

The PTX parser accepts all `sm_120` forms required by those paths, including
predicated, scalar, vector, cache-qualified, volatile, acquire/release, and
signed-offset forms. Atomic read-modify-write operations that target registered
HBF are represented as indivisible transactions rather than silently falling
back to an ordinary load/store model.

### 2.2 Exact-mode meaning

Functional results must be bit-exact against a native baseline for race-free
programs.

Timing fidelity is accepted only when an independent validation set, collected
under the profile's declared clock, cache, power, temperature, occupancy, and
concurrency conditions, meets all of these limits:

- kernel P50 error no greater than 5 percent;
- kernel P95 error no greater than 10 percent;
- TMA, LSU, scoreboard, barrier/membar, and throughput counter error no greater
  than 10 percent;
- each operation class passes separately: ordinary load, ordinary store, TMA
  load, TMA store, unicast, multicast, and mixed HBM/HBF.

Passing an aggregate average cannot hide a failing class. A report may use the
label `exact` only after both pre-launch admission and post-run validation pass.

### 2.3 Hard visibility boundary

A PTX pass cannot inspect instructions that exist only in a cubin or recover
private state that the target exposes through neither PTX nor performance
counters. Cubin-only modules and opaque private inline-SASS operations are
therefore rejected by exact mode before launch. They are not counted as
supported and are never assigned an inferred exact result. Supporting them
would require a separate SASS binary-rewriting project outside this design.

Physical channel labels are also not observable. Calibration may identify
contention-equivalent latent channels and give them stable model labels, but
must not claim those labels are NVIDIA's physical numbering.

## 3. Selected Approach

The selected approach is a calibration-driven shadow pipeline.

The rejected alternatives are:

1. keeping a synchronous resolver before every memory operation, because it
   destroys memory-level parallelism and load-use overlap; and
2. interpreting virtual time before every PTX instruction, because pervasive
   instrumentation changes register allocation, occupancy, and scheduling so
   substantially that matching the native program becomes less likely.

The selected data flow is:

```text
PTX module
   |
   v
PTX IR, CFG, def-use, liveness, and async-object analysis
   |                         |
   |                         +--> TMA/mbarrier/bulk-group instrumentation
   +--> LDG/STG future instrumentation
   |
   v
fixed CUDA 13 ptxas --> calibrated cubin and SASS identity
   |
   v
device shadow transactions
   |
   +--> runtime-visible HBM/HBF range classification
   +--> TensorMap tile expansion and split
   +--> per-SM GNIC2TEX[4] and GPCARB[2] proxy queues
   +--> MQSim/media and capacity page service
   |
   v
dependency, mbarrier, bulk-group, fence, or exit completion
```

## 4. PTX Intermediate Representation

The current line-oriented memory parser is replaced by a lightweight PTX IR.
It parses functions, basic blocks, predicates, branches, register definitions
and uses, memory qualifiers, async-object operands, and instruction source
locations. The analysis builds:

- a control-flow graph;
- dominators and post-dominators;
- register def-use chains;
- register and future liveness;
- mbarrier identity and phase flow;
- bulk-group creation and wait flow;
- TensorMap definition, copy, replacement, fence, and use flow.

Every modeled instruction receives a stable `instruction_id` included in the
coverage manifest and runtime reports. The pass computes the maximum number of
live futures and async objects for each thread, warp, CTA, and cluster. Exact
compilation fails if the count exceeds the corresponding calibrated budget or
if control flow makes the association ambiguous.

The pass must not introduce an unconditional synchronous-completion fallback.
An unsupported or ambiguous exact transformation is a compilation failure.

## 5. Ordinary Load and Store Futures

### 5.1 Future state

The logical transaction state is:

```cpp
struct ShadowFuture {
    uint64_t ticket;
    uint64_t original_address;
    uint64_t resolved_address;
    uint64_t ready_ns;
    uint32_t bytes;
    uint32_t instruction_id;
    uint32_t channel;
    uint32_t flags;
};
```

The concrete device representation may pack fields or place large state in a
bounded table, but its observable semantics must match this structure.

The runtime exposes three distinct operations:

- `issue`: classify the address, reserve a transaction, select modeled
  channels, and return without waiting for completion;
- `poll`: inspect completion without blocking;
- `wait`: block only at a dependency or ordering point and return the terminal
  status and final translated address.

Waiting for a free issue slot is permitted only as calibrated issue throttling.
It must be reported separately from completion waiting.

### 5.2 Load transformation

For ordinary HBM, the native load executes at the original point. For
timing-only HBF, the native HBM load also executes at the original point while
the future records when its value may become visible to a consumer. For a
capacity-cache hit, the load uses the resident frame. For a capacity miss, the
pass issues the page request but defers the actual load until the first
consumer path materializes the value.

The pass inserts an idempotent `ensure_ready` before every possible first use
of the destination. It handles predicated definitions, branch joins, loops,
multiple consumers, and register redefinitions. A path that cannot be proven
to have a valid definition and matching future fails exact compilation.

Acquire and volatile operations retain their architectural ordering points.
They may still use a shadow future, but the pass may not sink materialization
past an instruction that PTX ordering rules constrain.

### 5.3 Store transformation

A deferred store copies its source value into pass-owned temporary registers
at the original issue point so later register reuse cannot change the stored
bytes. Timing-only stores execute their native data write while a future tracks
modeled visibility. Capacity stores defer translation or backing-page work as
needed and materialize through the page cache.

Release, volatile, atomic, fence, and kernel-exit operations drain every
transaction required by their scope and semantics. An atomic operation is one
serialized read-modify-write transaction with one terminal completion; it is
never split into independently observable load and store operations.

## 6. TensorMap Provenance

CUDA 13 defines `CUtensorMap` as sixteen 64-bit opaque words, for a total of
128 bytes aligned to 128 bytes. HBFSim does not decode undocumented bits.
Instead, host interposition records the structured arguments and the returned
descriptor bytes for:

- `cuTensorMapEncodeTiled`;
- `cuTensorMapEncodeIm2col`;
- `cuTensorMapEncodeIm2colWide`;
- `cuTensorMapReplaceAddress`;
- direct symbols and dynamically queried `cuGetProcAddress` variants.

Each successful operation creates a generation-stamped record containing the
raw descriptor hash, base address, rank, dimensions, global strides, box
dimensions, element strides, element type, interleave, swizzle, L2 promotion,
OOB behavior, and mode-specific fields.

The PTX pass identifies TensorMap kernel parameters and memory-resident
descriptors used by TMA. A device helper matches the descriptor bytes against a
GPU-visible shadow registry. The pass also instruments every
`tensormap.replace.tile.*`, TensorMap copy-fence, and TensorMap acquire fence so
that shared- and global-memory descriptor modifications update a matching
shadow generation.

An unknown descriptor, stale generation, missing release/acquire relationship,
or incomplete device-side construction is a terminal exact-mode error.

## 7. TMA Transactions and Completion

### 7.1 Tile expansion

At `cp.async.bulk.tensor` issue, the runtime combines the structured TensorMap,
dynamic coordinates, and opcode mode to enumerate the global-memory segments
for all `sm_120`-accepted dimensions and modes required by the module. Expansion
implements tiled, gather/scatter, im2col, wide-im2col, OOB fill, interleave, and
swizzle semantics applicable to the direction.

Each segment is intersected with the immutable runtime range table. A single
tile may produce ordinary HBM segments, multiple HBF pages, and OOB fill
segments. The operation no longer rejects page crossings or mixed address
spaces. Its modeled ready time is the maximum of every required native,
channel, media, page-service, and target-CTA completion.

### 7.2 TMA load

In timing-only mode, the native TMA instruction executes. The shadow model may
delay observation of its mbarrier completion but can never make completion
visible before the native operation.

In capacity mode, an all-resident tile may use a rewritten resident descriptor.
A tile containing a nonresident or mixed-capacity segment issues asynchronous
page work without reading an unbacked address. At the corresponding
instrumented mbarrier poll or wait, once its sources are ready, the runtime
materializes HBM and HBF segments into the destination shared-memory layout and
performs the matching complete-transaction update. Computation between issue
and wait remains executable.

### 7.3 Multicast

Each multicast target receives an independent key:

```text
(cluster_id, target_ctarank, mbarrier_address, phase)
```

The HBF source/media read is shared, while each target retains its own
materialization, fanout delay, native condition, and barrier completion. A
target CTA materializes its local shared or distributed-shared-memory result on
its instrumented completion path.

### 7.4 TMA store and bulk groups

TMA store snapshots the source shared-memory tile into a bounded per-CTA
staging slot at issue. This preserves source-read semantics when the program
reuses shared memory before the destination transaction finishes.

`cp.async.bulk.commit_group` groups every preceding uncommitted store future
from the executing thread. `wait_group.read N` waits for source snapshots;
`wait_group N` waits for complete destination visibility. Release operations,
fences, and kernel exit drain the groups required by PTX semantics.

### 7.5 Native and shadow barrier conjunction

The runtime tracks mbarrier address, phase, expected bytes, arrivals,
invalidation, and reuse. An instrumented wait succeeds only when both the native
condition and the associated shadow condition are satisfied. A native
completion cannot bypass pending HBF time, and a shadow completion cannot
bypass pending native work. Ambiguous address reuse or phase association fails
exact mode.

## 8. SM120 Calibration and Channel Model

### 8.1 Calibration suite

The offline suite measures:

- single-warp global loads across load-use distances, independent instruction
  counts, outstanding depths, and L1/L2/DRAM residency;
- multi-warp pairwise contention for LSU throttle and long-scoreboard behavior;
- TMA load and store across tile sizes, dimensions, CTA ranks, cluster shapes,
  and unicast/multicast masks;
- simultaneous warp and CTA combinations chosen to reveal four-way and two-way
  contention classes;
- issue and wait timestamps from `%globaltimer`, plus `%smid`, `%warpid`, and
  `%cluster_ctarank`;
- Nsight Compute TMA-pipe, LSU, scoreboard, barrier/membar, L2, DRAM, and
  throughput metrics.

Calibration and validation cases are immutable, hashed, and disjoint.

### 8.2 SMSP and channel inference

Because PTX exposes no `%smspid`, the calibrator treats SMSP identity as a
latent class. It clusters the pairwise contention matrix and searches for a
runtime function of visible state:

```text
smsp_proxy = F(warpid, CTA shape, resident warps)
gnic       = G(smid, smsp_proxy, cluster_ctarank, operation)
gpc        = P(smid, smsp_proxy, cluster_ctarank, operation)
```

Only functions that satisfy the independent validation limits may enter an
exact profile. Indistinguishable latent labels remain an equivalence class.

### 8.3 Runtime queues

The single aggregate fast-channel tail is replaced by per-SM modeled resources:

```text
SM
  +-- GNIC2TEX[4]: load issue, transfer, multicast fanout
  +-- GPCARB[2]: store and return-path arbitration
```

Every resource records a tail time, queue depth, service class, arbitration
state, and counters. A transaction's final ready time is the maximum of base
latency, selected channel completion, MQSim/media completion, capacity-page
completion, and any native instruction condition.

## 9. Reproducible SASS Boundary

Exact mode does not rely on an unconstrained driver JIT. Its build path is:

1. transform the original PTX;
2. compile it with the profile's fixed CUDA 13 `ptxas`;
3. extract SASS and resource metadata with `nvdisasm` and `cuobjdump`;
4. record original PTX, transformed PTX, cubin, and SASS hashes;
5. load the calibrated cubin at runtime.

The profile binds GPU identity and compute capability, driver, CUDA, PTXAS and
Nsight Compute versions, clocks, power limit, temperature interval, cache
condition, concurrency condition, cluster shape, register count, spill bytes,
shared-memory use, occupancy, and all calibration/validation artifact hashes.

An uncalibrated spill, changed occupancy tier, SASS mismatch, out-of-profile
outstanding count, preemption-visible SM migration, or environmental mismatch
rejects exact admission. Emulation mode may continue only with a structured
degradation record.

## 10. Failure and Reporting Semantics

The implementation never silently restores synchronous per-instruction
completion. Errors are attributed to module, kernel, instruction ID, operation,
address when available, descriptor generation, barrier/group identity, and
profile identity.

Pass-time ambiguity rejects exact module generation. Launch-time provenance or
profile mismatch rejects the launch. A runtime timeout, daemon loss, capacity
copy failure, malformed completion, or invalid async-object transition publishes
one terminal state to every linked future or target CTA, writes a structured
fault record, and traps if execution cannot safely continue.

Reports distinguish:

- native TMA;
- shadow-delayed native TMA;
- capacity software-materialized TMA;
- ordinary load/store futures;
- multicast fanout;
- HBM and HBF split bytes;
- issue throttle, dependency wait, barrier wait, media time, and emulator
  overhead;
- exact admission and post-run validation results;
- every degradation or rejection reason.

## 11. Verification Strategy

### 11.1 Parser and assembly

Golden PTX tests cover supported load/store qualifiers, predicates, vectors,
offsets, CFG shapes, register redefinitions, TensorMap operations, TMA modes,
mbarrier phases, and bulk groups. Every transformed fixture is assembled by the
fixed CUDA 13 `ptxas` for the selected `sm_120` target variant.

### 11.2 CPU reference and property tests

CPU tests compare tile expansion, OOB behavior, interleave, swizzle, arbitrary
HBM/HBF/page splitting, multicast fanout, async state transitions, and channel
arbitration against independent reference implementations. Randomized inputs
must preserve bounds, conservation of bytes, single terminal completion, and
ordering invariants.

### 11.3 Real-GPU correctness

Native and instrumented kernels compare every output byte for HBM-only,
HBF-only, mixed tiles, capacity hits and misses, eviction, store-source reuse,
all supported data types and dimensions, cluster ranks, and multicast masks.
Race-free cases must be bit-exact.

### 11.4 Async proof

Timestamped kernels demonstrate that independent instructions execute after
issue and before the dependency or barrier wait. The former synchronous
resolver model is retained only as a negative control and must fail this test.

### 11.5 Calibration holdout

The independent validation set enforces the P50, P95, and counter tolerances in
Section 2 for every operation class. Failure leaves the feature in calibrated
emulation status; thresholds are not relaxed after seeing results.

### 11.6 Regression workloads

The final gate runs the complete CTest suite, vLLM adapter pytest suite, CUDA
microbenchmarks, deterministic llama.cpp inference, and deterministic vLLM
inference. Outputs and tokens must match their native baselines. No unsafe
launch, unconsumed future, live barrier/group, or stale TensorMap generation may
remain.

All live calibration accesses use ordinary allocated GPU buffers and registered
HBF test ranges. The suite does not access raw storage devices.

## 12. Delivery Decomposition

The design is implemented through four sequential, independently testable
plans:

1. calibration tooling, fixed AOT cubin/SASS identity, profile schema, and
   exact-mode admission;
2. PTX IR plus ordinary load/store issue, future, consumer, ordering, and drain
   behavior;
3. TensorMap provenance, TMA load/store, unicast/multicast, mbarrier,
   bulk-group, and mixed-space materialization;
4. four-plus-two channel inference and runtime queues, real-GPU calibration,
   independent tolerance validation, and workload regression.

Static checks do not complete a live stage. The overall work is complete only
when stage 4 passes the declared real-GPU correctness and independent timing
gates. If a gate is not met, the result is reported as partial with the exact
failing evidence.
