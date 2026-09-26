# R1 dynamic access coverage

Status: **PENDING**. Native-v4 is a successful model smoke; no formal native, zero-delay, or injected R1 arm has completed.

## Scope

R1 adds this chain to the historical launch statistic: supported dynamic access -> registered-range intersection -> modeled admission -> completed service -> unchanged output. The denominator is limited to supported observed sites and must be called **supported-site dynamic in-range access coverage**, not whole-program coverage.

Frozen workload: OLMoE revision `6d84c48581ece794365f2b8e9cfb043c68ade9c5`; batch 1; 128 input and 16 output tokens; seed 0; greedy/eager; TP=PP=1; same-shape warmup. The range is exactly 16 KiB from a finalized storage proved to feed the actual Triton fused-MoE kernel. Parameter names do not prove binding. Native, D0, and existing-profile injected arms keep prompt, storage, range, backend and build fixed. Formal utilization is 0.60 with at least 6 GiB free after load.

## Accounting contract

Version-2 snapshots record accesses and bytes for supported, exact in-range intersection, native out-of-range, modeled admitted, service completed, failed after issue, unsupported before issue, failed before issue, translation failed, service requests, unclassified and overflow.

Every actual instrumented module is synchronized, resolved, checked, reset and assigned the epoch before measurement. Snapshot synchronizes, reads each module, disables all, synchronizes again, and emits per-module plus aggregate JSON. Missing symbols/reads, late loads, unloads, disable failures or overflow yield `INCOMPLETE`, never substituted zeros. Trace overflow/drop stay separate. Opaque overlap is `UNKNOWN` unless binding evidence proves it.

Pass requires nonzero supported/in-range denominators, closed admitted/terminal categories, zero required failure/unsupported/overflow/drop counts, complete modules, and identical native/D0/injected tokens and checksums.

## Available evidence

Native-v4 exited 0 with token SHA-256 `1aa842abd697c23f4c1336783816b1fe655820f4694fccde4f1773c59732af19`, but accounting was disabled, exact bindings were zero, utilization was 0.65 and free memory fell to 5,594 MiB. It is not R1.

Compiled access-accounting-v2/eval-delay-v1 gate SHA-256: `ca63dffa702b2af762aa3f3e94eb26ef20f2513ee0ec9f2415f8a86b3431ef91`. Build evidence only.

| Metric | Result |
|---|---|
| Registered range | 16 KiB planned; binding PENDING |
| Supported accesses/bytes | PENDING |
| In-range accesses/bytes | PENDING |
| Modeled accesses/bytes | PENDING |
| Completed accesses/bytes | PENDING |
| Unsupported/failures | PENDING |
| Overflow/drop | PENDING |
| Opaque overlap | PENDING |
| Native = D0 = injected | PENDING |
| Decision | PENDING |

No percentage is valid before a complete runtime denominator exists.
