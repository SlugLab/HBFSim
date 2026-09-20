# EQ3-MINIMAL-REPAIR-v1: decisions required beyond local repair

All options below are proposals, not approvals. USER_CONFIRMED: local repairs and
CPU checks may continue; structural changes require prior explicit confirmation.
Unchanged P2 failures and old six engineering points remain historical evidence.

## Real MQSim NAND phases and die maintenance — NEEDS_USER_CONFIRMATION_REFACTOR

Problem/evidence: `MqsimOnlineEngine` exposes Arrival/Admission/raw Completion;
request admission is not NAND command start. It has no operation expressing
HBF die refresh and no physical die/plane/transfer stage facts at this boundary.
Ordinary host write cannot certify read/program maintenance or commit age.

Minimal interface option (recommended current boundary): consume existing
request facts, disclose unknown die/plane/physical bytes, use an opt-in external
submission gate with existing horizon advancement, and explicitly return
UNSUPPORTED_CAPABILITY for maintenance. No MQSim timing or lifecycle changes.
This delivers a request-level CPU path, not a full real-backend maintenance loop.

Minimal structural option requiring approval: first expose immutable stage facts
at existing MQSim command transitions, then add an explicit backend maintenance
operation with identity, enqueue/start/end/commit/fail, ownership and shared
resource arbitration. Proposed affected boundaries: `src/mqsim_adapter/mqsim_online.cpp`,
`include/hbfsim/mqsim_online.hpp`, matching MQSim flash-controller/transaction
interfaces after a narrower source design. Do not retrofit these by extending
CpuService. Exact internal symbols and lifecycle design must be submitted before
this option can be implemented; this is a scope choice, not a ready implementation approval.
Tests: off event/completion parity, command ordering, shared arbitration,
fail-before-commit, unique completion, no callback reentrancy, CPU-only.
Rollback: opt-out wrapper; any backend patch in an independent revertible commit.
Resources stay within existing 16/12GiB, CPU1,600s,4/20GiB; wider work needs a new
concrete preflight. Affected conclusion: actual MQSim active maintenance/energy,
not the already verified engine completion semantics.

## Full 1mm reference — PENDING_USER_APPROVAL_RESOURCE

DOC_DERIVED: exact 4s pilot 328.012s, ~4.91GiB, setup/factor dominates;
100s estimate 2000–2600s, not a measurement. Same-window 2→1mm still fails0.25K.
Recommended optional next scope: one complete unchanged train1mm/100s reference,
CPU1,3600s watchdog (new requested limit), process12/task16GiB, point4/task20GiB,
existing reduced/lossless output, no blind opening. Before execution bind exact
input/output forecast and remaining disk to a separate approved preflight.
No automatic downstream/finer-grid authorization and no guarantee of convergence.
Alternative: keep P2 blocked with existing evidence. A different solver/ROM is a
separate scientific/structural proposal and is not a workaround for600s.

## Development domain — retain failure unless a new scientific version is approved

DOC_DERIVED: independent2mm reference reaches400.911K, first crossing31.14s;
300–400K domain is unchanged. Recommended: preserve negative result, complete
reference qualification before deciding on physical/input redesign. Alternatives
are source-supported material extension or explicitly redesigned load/cooling,
each with new scope, parameters and comparability statement. Do not clamp or trim.
The present diagnostic patch cannot recover unknown historical trial values.

## Emergency escalation and numerical budgets — optional policy/science changes

No existing contract found that exempts more-severe actions from minimum dwell;
`control_sample` uses delay/dwell for every change and already cancels obsolete
pending actions. Recommended: retain this contract. An escalation-priority,
recovery-only dwell policy would require a named opt-in strategy and separately
approved comparisons; it must not replace old parameters or six-point results.

Retain reference0.25K,MAE1K,hotspot2K,normalized5%,energy0.001 as written.
With a1K normalization floor5% is0.05K, tighter than the0.25K reference budget;
this is a diagnostic mismatch, not permission to relabel old FAIL as PASS.
Any new split reference/RC error budget requires a separate scientific decision.
Physical HBF/base/PHY energy, Ea/ECC/wear and device limits remain in
PARAMETER_GAPS.md. No GPU/live or formal paper matrix is authorized.

## Verified boundary symbols for the next narrower backend design

The existing adapter producer is
`MqsimOnlineEngine::Impl::{submit_to_device,dispatch_to_device,observe}` in
`src/mqsim_adapter/mqsim_online.cpp`; the host shim is maintained in
`patches/mqsim/0001-online-hbf-api.patch` (`Host_Interface_HBF::Submit_hbf_request`).
Actual upstream stage boundaries include
`NVM_PHY_ONFI_NVDDR2::Send_command_to_chip` and `transfer_read_data_from_chip`
(`third_party/mqsim/src/ssd/NVM_PHY_ONFI_NVDDR2.{h,cpp}`), plus
`Flash_Chip::Connect_to_chip_ready_signal`/`broadcast_ready_signal`
(`third_party/mqsim/src/nvm_chip/flash_memory/Flash_Chip.{h,cpp}`).
These are verified candidate observation boundaries, not permission to equate
command dispatch with NAND start or change callback ownership. The next design
must trace request→transaction→command identity and all start/end/failure paths
before declaring command energy complete. Cross-module event/type contracts and
maintenance arbitration are the reason this branch awaits a concrete approved
design. Existing CpuService limitations cannot close it.

Many-HBF-to-one-HBM relay is also unsupported by `CpuService::Impl`'s existing
unique-pair check. Recommended: retain four unique pairs for this task. If this
new topology is wanted, define shared-base policy, geometry and power mapping
first, then separately approve the affected constructor/route/arbitration
changes and contention tests. No need to change pair topology to fix current
relay Shutdown leakage.
