# Rebuttal-ready numbers

Status: **NOT READY FOR A RESULT-BEARING REBUTTAL**.

## Historical Qwen3 evidence

Registered timing range: 16 KiB  
Fused-MoE launches: 2,304  
Modeled launches: 24  
Coverage decisions: 10,339  
What this metric means: launch-level selected-range statistic, not access coverage, byte coverage, or capacity.

## New access-level evidence

Model: OLMoE-1B-7B-0924 revision `6d84c48581ece794365f2b8e9cfb043c68ade9c5`  
Registered range: 16 KiB planned; binding PENDING  
Dynamic in-range accesses/bytes: PENDING  
Modeled accesses: PENDING  
Completed accesses: PENDING  
Unsupported: PENDING  
Opaque overlap: PENDING  
Correct output across native/D0/injected: PENDING

Native smoke only: exit 0; token checksum `1aa842abd697c23f4c1336783816b1fe655820f4694fccde4f1773c59732af19`. Accounting was disabled and free memory fell to 5,594 MiB, so this is not R1.

## Range expansion

16 KiB: PENDING  
Largest successful range: PENDING  
Full selected storage: PENDING  
R2: BLOCKED_BY_R1

## Capacity

Logical range: 110 GiB planned  
Frame cache: 2 GiB planned  
Page sizes: 4/8/16/32/64 KiB planned  
Samples: 64 uniform plus first/middle/last, de-duplicated per cell  
Correct checks: PENDING  
Result: BLOCKED_BY_R1; ordered after R2

## What we can now say

- Historical 24/2,304 is launch-level and cannot yield dynamic access coverage.
- Historical 16 KiB is a selected timing range, not a measured capacity limit.
- The smaller real MoE completes the short native smoke.
- Accounting and public-capacity paths have compile receipts only.

## What we still cannot claim

- No access-level percentage, modeled/completed count, or D0 equivalence.
- No range expansion or new 110 GiB checksum result.
- No five-page-size result, speedup, whole-program coverage, concurrent-eviction safety, or reproduction of original Qwen3 access distribution.

Do not insert numeric placeholders into an English rebuttal before R1-R3 pass.
