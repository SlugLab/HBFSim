# Async, cp.async and TMA: implemented boundary and donor map

## Purpose

Prevent synchronous issue-stall behavior, candidate helper names and static token bookkeeping from being mistaken for validated asynchronous memory semantics.

## Scope

B (`fc829992ecdc3ca68881656722b67a31067c5d33`) contains only the synchronous resolver path. S (`f4dc28b2671c01939d98e4a968e6fb37b2e364d9`) is an **unmerged donor** with reusable mechanisms and a confirmed static counterexample. This document maps S through the frozen [source ledger](../49-eval-audit/source-ledger.md); S paths below are Git-object paths, not files claimed to exist in B.

## Key concepts

| Concept | Meaning in this project | Concrete implementation or gap |
|---|---|---|
| Issue | Initiate model work and record an issue-relative deadline | B `resolve_leader` submits and waits in one call; S `issue_reference`/`issue_fast` return a future |
| Completion | Model deadline and any required native/copy work are ready | B `wait_for_completion`; S future/TMA helpers; a ready-like poll is not sufficient success status |
| Synchronization | The point at which the program requires outstanding work | S `emit_wait`, barrier polling and group waits; placement correctness remains a gate |
| First consume | First executed operation that needs the loaded value | S `transfer_block`/`transform_futures`; conditional uses require may-consume versus must-consume reasoning |
| Residual latency | Remaining wait when synchronization/consume is reached | Isolated oracle `max(0, D-W)`; native latency, issue overhead, host lag and copy readiness remain separate |
| mbarrier | Native transaction/phase synchronization for supported candidate TMA shapes | S `emit_barrier_poll` combines native readiness with modeled readiness |
| Bulk group | Dynamically committed work and the wait's retained recent groups | S transform keeps static token lists; `__hbfsim_tma_commit_group` is empty; general CFG/group semantics are unproved |
| TensorMap | Descriptor whose content, address and generation determine tile access | S replace/copy/acquire helpers and registry; B has no corresponding lifecycle implementation |

## Important files

B: [transform](../../src/ptxpass_hbf/transform.cpp), [device helper](../../src/cuda_runtime/device/hbf_device.cu), and [async rejection regression](../../tests/cpu/ptx_async_copy_coverage_test.cpp).

S only: `src/ptxpass_hbf/ptx_ir.cpp`, `ptx_analysis.cpp`, `future_transform.cpp`, `ptx_async_op.cpp`, `async_object_analysis.cpp`, `tma_transform.cpp`, and S's expanded device/control/context/loader sources. Exact frozen links and line ranges are [ledger S01–S19](../49-eval-audit/source-ledger.md).

## Important structs/classes/functions

S's 64-byte `DeviceFuture`, `__hbfsim_future_issue`, `__hbfsim_future_poll` and `__hbfsim_future_wait` carry ordinary deferred operations. `parse_tma`, `analyze_async_objects` and `transform_tma` provide candidate TMA analysis. `__hbfsim_tma_issue`, `__hbfsim_tma_barrier_poll`, `__hbfsim_tma_barrier_wait`, `__hbfsim_tma_wait_group` and `__hbfsim_tma_commit_group` are candidate device hooks. Their presence is source evidence, not an integrated or passing semantic feature.

## Call path / data path

B: native load site → `__hbfsim_resolve` → complete model wait → original load → independent work → consumer. The model delay has already been paid before independent work.

Intended selective-port path: analysis identifies producer and required consumer/drain → issue records future → independent work executes → wait/poll checks native, modeled and capacity-copy readiness → first consume. Timing data can be materialized at issue; capacity materialization must use a valid resolved address at the required boundary. Actual queue admission can block issue and must be measured separately.

The audit's TMA contract distinguishes `.read` source/descriptor-read completion from destination visibility, and records `tensormap.cp_fenceproxy` plus `fence.proxy.tensormap::generic.acquire` as the descriptor copy/publication operations. Do not invent standalone `tensormap.copy` or `tensormap.acquire` opcodes. Preserve artifact-specific PTX version, target and toolkit metadata when implementing these audited contracts.

## CPU-side vs GPU-side execution context

IR, CFG and descriptor-lifecycle analysis execute on CPU during PTX transformation. Future/tile state and waits execute on GPU; the host MQSim daemon supplies modeled completion. No dedicated SM is present in B. A candidate host completion provides a duration/issue-relative deadline; unrelated host and GPU absolute timestamps must not be subtracted.

## Invariants

Every executed consume must have a successful wait on all relevant paths. Issued/completed/drained counts and dynamic groups must be conserved. Correct TMA readiness requires the applicable native transaction, modeled deadline and data-copy readiness. Descriptor A→B updates need generation/publication/acquire validation. Unsupported shape or unknown descriptor must be rejected instead of erased from coverage. S's ABI 9 and 64-byte future cannot be paired with B's ABI 4 and 16-byte resolver return.

## Supported behavior

B detects global ordinary/bulk/tensor async copy families as unsupported memory operations and preserves synchronization-only instructions without falsely classifying them as memory. S is available for selective semantic integration after the dependency closure and regression gates. A restricted straight-line, single-group subset may be a future implementation target only after its wait structure is proved.

## Explicitly unsupported behavior

B has no modeled ordinary `cp.async`, deferred ordinary loads or TMA/TensorMap lifecycle. S cannot be directly merged or declared correct for arbitrary predication, loops, diamonds, early exits or multiple dynamic groups. The empty commit helper alone is not proof that all straight-line S programs are wrong: the static pass supplies bookkeeping, whose generalization is the unresolved issue.

## Common failure modes

The confirmed S counterexample is `ld.global` followed by a predicated first add and an unconditional second add. `transfer_block` clears pending state after the possible first use, while `emit_wait` guards the wait by that use's predicate. If the predicate is false, the later unconditional consume has no wait. This is a static transformed-program counterexample; the audit does not claim it observed stale bytes on a GPU.

Other hazards are ordinary `cp.async` falling through the older S regex, treating a terminal-error poll as success, static group counts standing in for loop iterations, no deadline for a barrier loop, source reuse before the required read completes, and claiming an old path's consume residual near zero means it hid the delay. Old issue-stall can have near-zero consume residual while its total exposed stall is large.

## Tests proving the behavior

B's [async coverage tests](../../tests/cpu/ptx_async_copy_coverage_test.cpp) prove rejection cases when executed. The earlier static reproduction is described in [async audit §3](../49-eval-audit/async-tma-audit.md) and its [probe script](../../scripts/eval/audit_async_probe.py); linked raw evidence must exist before citing a specific execution. S GPU test sources are catalogued by S17/S18; they were not run for this knowledge pack.

Required future gates include predicate false/true then unconditional use, diamond/loop/reissue/early exit/multiple consumers, D/W checksum and residual sweep, descriptor A→B with distinct bytes, missing acquire/wrong generation rejection, barrier phase and source reuse, dynamic group N=1/0, daemon loss and timeout, and optimized final PTX→SASS issue/wait/consumer mapping. CPU transform PASS alone cannot close these GPU gates.

## What not to change casually

The complete dependency unit: IR/analysis, transforms, 64-byte future, control ABI, context, loader, pointer registry, embedded PTX and reporting. First preserve B's coverage/comment/.loc and MQSim-admission regressions. Never inflate support by removing a rejection before the semantic path exists. Do not use global `-O0` as evidence that normal optimized execution is correct.

## Related docs

[Async audit](../49-eval-audit/async-tma-audit.md), [execution plan C](../49-eval-audit/execution-plan.md), [claim gate G5](../49-eval-audit/claim-gates.md), [ABI](05-device-helper-and-control-abi.md), [issue-stall finding](../重要实现问题以及需补做实验/10-B-delay-injected-at-issue-not-at-use.md), and [old async coverage finding](../重要实现问题以及需补做实验/07-B-cp-async-and-bulk-tensor-copy-unmatched.md).
