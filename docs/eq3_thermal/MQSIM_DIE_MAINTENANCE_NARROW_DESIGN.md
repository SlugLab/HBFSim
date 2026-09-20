# MQSim die-maintenance narrow design

Status: `AWAITING_EXPLICIT_REFACTOR_APPROVAL`

This is a design record, not an implementation approval. The current
`MqsimOnlineEngine` capability remains `UNSUPPORTED_CAPABILITY` for die-level
maintenance. A host write is not maintenance, and no wrapper-side resource
ledger is proposed.

## Evidence and required invariants

The reusable MQSim machinery is inside the backend. `FTL` owns the
`Address_Mapping_Unit`, `Flash_Block_Manager`, `GC_and_WL_Unit`, `TSU`, and
`PHY`. `GC_and_WL_Unit_{Base,Page_Level}` already creates `GC_WL` read,
program, and erase transactions; `Address_Mapping_Unit_Base` defines physical
block and LPA barriers; `Flash_Block_Manager_Base` owns destination allocation
and the `GC_WL_started`/`GC_WL_finished` bookkeeping; `TSU_Base` submits these
transactions to the same arbitration path as foreground traffic. These are
`DOC_DERIVED` facts from the vendored MQSim source. Whether the resulting
operation is a scientifically valid HBF refresh model is still `INFERRED` and
would need a separately sourced operation definition.

The current page-level GC path is not already a failure-atomic maintenance
API. `Address_Mapping_Unit_Page_Level::allocate_page_in_plane_for_user_write`
invalidates the old page and calls `Update_mapping_info` when the destination
is allocated, before the program command completes. The serviced-write path
then removes the LPA barrier, and MQSim's normal PHY path does not expose a
program/erase failure result. Therefore exact commit/fail semantics cannot be
added only at the online wrapper. They require the mapping and GC/WL lifecycle
changes listed below and explicit approval.

Any accepted implementation must preserve these invariants:

1. Exactly one terminal completion is returned for every accepted maintenance
   request, after every child transaction reaches a terminal state.
2. The existing mapping remains valid until the replacement program succeeds.
   No source page or block is erased before all required relocation commits.
3. Destination pages come only from `Flash_Block_Manager`; foreground and
   maintenance commands share `TSU` and `NVM_PHY_ONFI_NVDDR2` arbitration.
4. P/E accounting advances only for program/erase commands that actually
   complete. HBF age changes only after the maintenance commit succeeds.
5. Failed and partially completed work keeps already incurred activity and
   energy facts, releases or reconciles backend resources through their owner,
   and never fabricates an uncomputed command phase.
6. Shutdown/checkpoint handling either drains accepted work or reports a
   precise terminal failure once; it never silently drops a cohort.

## Proposed internal interface

The smallest structural API is an internal maintenance endpoint beside the
patched HBF demand endpoint. It must not enter `Input_Stream_Manager_HBF` or
construct a `User_Request`.

```cpp
enum class HbfMaintenanceKind { RelocateAndRefresh };
enum class HbfMaintenanceStatus {
  Committed, RejectedUnsupported, RejectedInvalidTarget,
  FailedBeforeCommit, FailedAfterCommitNeedsReconcile
};

struct HbfMaintenanceRequest {
  uint64_t request_id;
  uint64_t cohort_id;
  HbfMaintenanceKind kind;
  flash_channel_ID_type channel;
  flash_chip_ID_type chip;
  flash_die_ID_type die;
  std::optional<flash_plane_ID_type> plane;
  uint64_t policy_version;
};

struct HbfMaintenanceCompletion {
  uint64_t request_id;
  uint64_t cohort_id;
  HbfMaintenanceStatus status;
  sim_time_type enqueue_time;
  sim_time_type start_time;
  sim_time_type end_time;
  std::vector<uint64_t> transaction_ids;
};
```

Proposed entry points are
`MqsimOnlineEngine::submit_die_maintenance(...)` and a new internal
`GC_and_WL_Unit_Base::Submit_hbf_maintenance(...)`. The engine would obtain
the existing `FTL` from `SSD_Device::Firmware`, validate the exact physical
target against configured MQSim bounds, and pass the request to the GC/WL
unit. It would retain only completion correlation, as it does for demand I/O;
the backend would own candidate selection, transactions, barriers, and
resources. The returned transaction and command observations would carry
`request_id`, `cohort_id`, and their existing unique transaction/command IDs.
Background work unrelated to an explicit request would keep a null maintenance
parent rather than borrowing a demand identity.

This endpoint changes the engine API and backend maintenance state machine.
That is why it is outside the current D5 implementation authorization.

## Lifecycle and failure model

On acceptance, the GC/WL unit validates the target die and chooses eligible
source blocks using its existing safety predicates. Each chosen block receives
the existing physical-block barrier and `GC_WL_started` bookkeeping. For each
valid source page, the backend reads the current mapping and content, sets the
existing LPA/MVPN barrier, obtains a destination through
`Allocate_new_page_for_gc`/`Flash_Block_Manager`, and submits the related
read/program transactions through `TSU_Base::{Prepare_for_transaction_submit,
Submit_transaction,Schedule}`. Copyback may be used only when the existing
backend declares it legal for the source and destination.

The proposed mapping path must switch to the programmed destination only on
successful program completion. Then the old page can be invalidated and its barrier removed. An
erase is submitted only after all valid pages in its source block have
committed. Successful erase completion returns the block to the pool and calls
`GC_WL_finished`; the maintenance request commits only after all selected
blocks finish. Empty targets must return an explicit successful no-op or
rejection according to the approved operation contract, not synthesize NAND
activity.

Before mapping commit, failure keeps the old mapping valid, removes barriers,
and reconciles any allocated but uncommitted destination through
`Flash_Block_Manager`. After mapping commit, failure must keep the new mapping
and enter a backend-owned reconciliation path; it must not blindly restore a
possibly invalidated source. A failed erase cannot be reported as a committed
refresh. The completion identifies the failed child and preserves prior phase
events. MQSim currently assumes successful flash commands in several paths, so
the exact injection and propagation of command failure is a required design
audit before implementation.

## Exact proposed change surface

All MQSim edits would be delivered as a new reproducible patch after the
observer patch, never as a dirty `third_party/mqsim` tree.

| File/symbol | Proposed change | Behavioral impact |
|---|---|---|
| `include/hbfsim/mqsim_online.hpp`, `src/mqsim_adapter/mqsim_online.cpp` | Add maintenance request/completion/capability types and `submit_die_maintenance`; correlate one terminal callback | Public HBFSim API and request lifecycle change |
| MQSim `src/exec/SSD_Device.{h,cpp}` or a narrowly scoped internal accessor | Give the online adapter typed access to the existing `FTL`; do not create a second FTL/resource owner | Module boundary change |
| MQSim `src/ssd/GC_and_WL_Unit_Base.{h,cpp}` and `GC_and_WL_Unit_Page_Level.{h,cpp}` | Add explicit target validation, cohort state, candidate selection, child accounting, commit/fail callback | Backend maintenance lifecycle and scheduling-visible traffic |
| MQSim `src/ssd/NVM_Transaction.h` | Carry optional maintenance request/cohort identity alongside existing source type | Internal transaction layout change |
| MQSim `src/ssd/Address_Mapping_Unit_{Base,Page_Level}.{h,cpp}` | Expose only the commit/reconcile operations missing from the existing GC path after audit | Mapping ownership and failure semantics change |
| MQSim `src/ssd/Flash_Block_Manager_Base.{h,cpp}` and `Flash_Block_Manager.{h,cpp}` | Expose backend-owned reservation reconciliation if existing GC cleanup is insufficient | Resource ownership change |
| MQSim `src/ssd/NVM_PHY_ONFI_NVDDR2.{h,cpp}` | Extend immutable observations with optional maintenance/cohort parents; do not add events | Observation schema only |
| `cmake/MQSimPatchedBuild.cmake`, `patches/mqsim/0004-...patch` | Register the reproducible patch | Build input change |

If audit shows the existing GC/WL path cannot keep the old mapping valid until
program completion, the implementation must stop and return with a revised,
larger proposal. It must not imitate completion in `CpuService`.

## Required validation before acceptance

The minimum fixed CPU suite must cover an empty target, one-page and multi-page
cohorts, exact die targeting, foreground contention through the same TSU/PHY,
copyback-enabled and disabled paths, failure before and after mapping commit,
program and erase failure, unique completion, no leaked barriers or blocks,
P/E increments only on actual completion, and age update only on commit. It
must also cover shutdown drain, checkpoint/restart identity and cohort state,
observer on/off timing parity, deterministic same-time command ordering, and
demand readback of every relocated LPA. Multi-channel tests cannot be promoted
to multi-stack tests until an explicit channel/chip/die-to-package-stack map is
configured and consumed.

Rollback is removal of the new patch and engine API, followed by rebuilding
from the unchanged vendored source plus patches 0001--0003. Existing demand
and read-only observation behavior must remain byte-for-byte compatible in
default-off mode.
