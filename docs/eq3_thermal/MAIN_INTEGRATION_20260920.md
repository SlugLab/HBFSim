# Main integration: software validation, 2026-09-20

USER_CONFIRMED: integrate both histories and normally push to main (formerly
eval_base); preserve remote latency implementation. No force push or research
experiment is authorized by this delivery.

Remote parent: a2a3387b447d26e0c6ce551803213c11a5d28f0f.
EQ3 parent: b3b2a430bf2404769df4a2fc77d9dea3ae38a61b.
Merge: 8475fa2f95778107d01186d67ddebc92696f25e5 (no conflicts).
Tested code: 5e7c09cdc88c43e92852428a021922c8a02abad4.
All existing src/include files outside eq3_thermal, top-level CMake and
.gitmodules are identical to the remote parent. In particular, remote latency,
PTX, future and cache implementations were not changed. Two trace tests now
expect upstream latency/transfer overlap rather than their stale sum; one
fake-wrapper test uses its own CUDA-root fixture, not a host installation.

## Fixed CPU verification

Fresh CPU builds of merged core/MQSim and isolated thermal targets succeeded.
Core CTest: 59/59 selected tests passed. The service-client group was checked
separately: 3/3 Python tests passed using the freshly built binary copied into
an ignored checkout-local build directory, as required by its path guard.
Thermal CTest: 4/4 passed. Python discovery: 137 tests reported, OK with 9 skips
(optional native codec/RC runner prerequisites); skips are not native validation.

The initial complete core run was 55/62, with seven failures retained. Five
groups subsequently passed after test expectation/environment/path corrections.
Two unchanged upstream tests remain unresolved and excluded from the selected
rerun, so this is NOT a full 62/62 pass:

- context_lifecycle: std::bad_alloc under the 12 GiB process address-space limit
  with the existing large-cache fixture; resource-limited, not repaired here.
- vmem_tuning: external performance CSV missing at an old host-specific path;
  BLOCKED_EXTERNAL_ARTIFACT. No replacement data fabricated.

One CPU, OMP/BLAS=1, process12GiB/task16GiB, each step600s watchdog,
verification4GiB/task-retained20GiB, GPU0/cloud0. Existing toolchain only.
Private raw logs, commands, source/runner hashes, failures and per-step resource
receipts are retained in workspace-relative
eq3_thermal/integration/verification-20260920; runner verify_main_20260920.py
and audit MERGE_PUSH_20260920.md are alongside it. Raw experiment data and private
authorization packages are not included in Git. The two untouched original
worktrees retain baseline5eb789d5f1a42f0c040ee6fb5a2cdb5ffa0951d5 and EQ3 parent.

## Scientific status unchanged

P2_BLOCKED_WITH_EVIDENCE; no MODEL_FREEZE, blind remains sealed. P3/P4 software
and ENGINEERING_FIXTURE evidence are not physical calibration or GPU/live-path
validation. See P2_P3_P4_CAMPAIGN_RESULT.md for the scientific limitations.
