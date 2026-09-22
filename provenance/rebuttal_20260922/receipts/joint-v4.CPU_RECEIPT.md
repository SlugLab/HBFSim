# Joint request-accounting v4 CPU receipt

Status: **CPU_PASS_READY_FOR_SOLE_OWNER_GPU_VALIDATION**. No GPU command was run by this task.

## Frozen candidate

- Gate DSO: `/root/hbfsim-exp/rebuttal_20260921/resolver-build-joint-v4/libhbfsim_launch_gate.so`
  - SHA-256: `ad3aa52d584811259d9ad97cd461f2300ea26f3176d5deda547c390232e7d08f`
- Gate source: `/root/hbfsim-exp/rebuttal_20260921/resolver-source-joint-v4/src/cuda_runtime/launch_gate.cpp`
  - SHA-256: `40369dea9523e09e97a44b0f98fb399531b877c929c4e4ca2da13a583deb8334`
- Adapter: `/root/hbfsim-exp/rebuttal_20260921/resolver-source-joint-v4/adapters/vllm/run.py`
  - SHA-256: `67e610c08ec89be941aa87f9be0becfd455278930a6dfa17d9a27566446925da`

Frozen v3 was not modified.

## Joint ABI

```c
int hbfsim_request_accounting_begin_v1(
    uint64_t delay_ns, uint64_t request_epoch,
    uint64_t trace_capacity_per_module);
long long hbfsim_request_accounting_snapshot_v1(
    char *out_json, size_t capacity);
int hbfsim_request_accounting_abort_v1(void);
```

The snapshot follows the existing two-call size contract. Its object contains top-level
`status`, `epoch`, `access`, and `eval_delay`. The adapter preserves the existing
report shape by returning the nested `access` and `eval_delay` objects.

Joint begin, snapshot, and abort take the launch synchronizer's exclusive mutation guard
before the accounting mutex. This excludes runtime and driver launches across both
device configurations and across both snapshot reads. Both sessions use one epoch,
CUDA context/device, and ordered module identity set. A single-side legacy snapshot or
abort is rejected while a joint session owns the state.

Cleanup failure poisons later begins. Eval trace storage is quarantined instead of freed
when disable or final synchronization fails, avoiding a device dangling pointer.

## CPU checks

- Joint fake-driver fixture: PASS.
  - A launch concurrent with a blocked joint begin did not enter the fake driver until
    the joint transition released the exclusive launch guard.
  - Duplicate begin and single-side abort were rejected.
  - Two-call snapshot capacity behavior and COMPLETE epoch were checked.
  - A late module produced INCOMPLETE.
  - Missing eval symbols rolled back without leaving an active half-session.
  - Missing access symbols after eval activation rolled eval back, cleared its config,
    freed its trace, and allowed a later clean begin.
  - Injected eval-disable failure produced INCOMPLETE, did not free the still-referenced
    trace, and poisoned the next begin with -10.
- CUDA runtime-domain regression fixtures: caller12 PASS, caller13 PASS,
  missing12 fail-closed fixture PASS.
- Adapter `py_compile`: PASS.
- Export audit: all joint v1, access v2, and eval v1 symbols present.

Fixture source:
`/root/hbfsim-exp/rebuttal_20260921/resolver-build-joint-v4/joint_accounting_test.cpp`
SHA-256 `4da4885ef5963db9e0289aed4f1607639b2fe5a0d6dc7830982cfd1b153566c7`.
Fake driver SHA-256:
`589f478e74cfaf465868c76b9d5017aedeaf0388bb3799088df14b8499f6047a`.

This receipt validates CPU host behavior only. Actual instrumented module counters,
trace contents, and model execution remain for the execution owner's GPU run.

