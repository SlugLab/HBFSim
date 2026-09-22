# R2 registered-range sweep

Status: **BLOCKED_BY_R1**. No R2 cell has run.

R2 tests whether 16 KiB was a controlled semantic footprint rather than a capacity ceiling. It is coverage/scalability validation, not performance evaluation. Model revision, finalized storage, prompt, backend, build, profile, request and seed remain fixed; only `registered_range_bytes` changes.

Candidate ladder: 16 KiB, 1 MiB, 16 MiB, 64 MiB, 256 MiB, full selected storage. Points larger than proved storage are omitted. Acceptance needs 16 KiB, one substantially larger point, and full storage. A failed full-storage cell is preserved, not renamed or replaced.

Each cell records requested/effective bytes; storage binding; supported, in-range, admitted and completed accesses/bytes; failures, unsupported, translation failures and unclassified events; service requests; overflow/drop; total and modeled fused-MoE launches; descriptive wall time; output checksum; opaque overlap; and per-module completeness. Missing evidence means `INCOMPLETE`.

Planned views: registered bytes (log scale) versus dynamic in-range bytes; registered bytes versus modeled launches; and closure/correctness table. Launch ratio, access coverage and registered capacity remain separate.

| Range | Status | In-range bytes | Modeled | Completed | Correct |
|---|---|---:|---:|---:|---|
| 16 KiB | BLOCKED_BY_R1 | PENDING | PENDING | PENDING | PENDING |
| 1 MiB | NOT_RUN | PENDING | PENDING | PENDING | PENDING |
| 16 MiB | NOT_RUN | PENDING | PENDING | PENDING | PENDING |
| 64 MiB | NOT_RUN | PENDING | PENDING | PENDING | PENDING |
| 256 MiB | NOT_RUN | PENDING | PENDING | PENDING | PENDING |
| full storage | NOT_RUN | PENDING | PENDING | PENDING | PENDING |

No R2 plot or claim is admissible.


---

## 2026-09-21 CPU-only execution preparation

The actual D0 registration receipt reports one selected storage with `storage_bytes=536870912` and the earlier attempted registered range of 16384 bytes. The generated ladder clips each requested point to the real object extent and de-duplicates equal effective sizes: 16384, 1048576, 16777216, 67108864, 268435456 and 536870912 bytes. Every effective range is positive and no larger than the selected storage.

The non-executing receipt is `/root/hbfsim-exp/rebuttal_20260921/results/r2-preflight-v1/plan.json` (SHA-256 `a269e9e0736e9abff590218677f74d1bba6baf49cfefb5f7acba3945ee2104dc`). Its current status is `BLOCKED_BY_R1` because the final successful D0 `result.json` does not yet exist. It records `gpu_execution_requested=false`. When regenerated after R1 PASS, it must bind the final R1 result, input hash receipt, launch script, profile hash, prompt-token hash and output-token hash. The only per-cell override is `hbf_range_bytes`. It does not presume or certify an older `num_stages` value.

`experiments/rebuttal_20260921/range_sweep/validate_r2_range_sweep.py` requires exit 0, exact effective registered bytes, the same 512 MiB storage binding, COMPLETE request/accounting/eval snapshots, every v2 aggregate counter field, and output-token hash equality with final R1. It reports N as supported accesses, M as modeled-admitted accesses, K as service-completed accesses, plus failures, unsupported, unclassified and overflow counters. Trace drop remains `UNKNOWN_NOT_EXPOSED` when the runtime returns null; opaque overlap remains `UNKNOWN_NOT_MEASURED`. A CPU synthetic contract fixture passed, which validates parser behavior only and is not an R2 experiment result.
