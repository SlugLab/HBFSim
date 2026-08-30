# `cp.async` and the bulk tensor copy instructions match neither pattern

All code quoted here was read from the remote branch `origin/hybrid`.

## What we read in the code

The `PTX` pass decides what to do with a memory instruction using two regular
expressions. `PTX` is the instruction-level intermediate language NVIDIA's
compilers emit for GPU code; ptxas then turns `PTX` into the machine code the
card runs.

The first regular expression decides which instructions are rewritten and
modeled. `src/ptxpass_hbf/ptx_memory_op.cpp` lines 79 to 80:

```
R"(^\s*((?:@!?%[A-Za-z0-9_$]+)?)\s*((?:ld|st)(?:\.volatile)?\.global(?:\.[^\s]+)*)\s+(.+);\s*$)"
```

The second decides which instructions are put on the unsupported list.
`src/ptxpass_hbf/transform.cpp` line 54:

```
R"(^\s*(?:@!?%[A-Za-z0-9_$]+\s+)?((?:atom|red)\.global\S*|ld\.(?!global)\S*|st\.(?!global)\S*|tex\S*|suld\S*|sust\S*|asm\s*\().*;\s*(?://.*)?$)"
```

An instruction that matches the second regular expression is counted and its
opcode recorded, at `src/ptxpass_hbf/transform.cpp` lines 228 to 230.

We ran six instructions through both regular expressions. The results below are
measured, not inferred.

| Instruction | Result |
|---|---|
| `ld.global.f32 %f1, [%rd4];` | supported |
| `ld.global.nc.v4.f32 {%f1,%f2,%f3,%f4}, [%rd8];` | supported |
| `atom.global.add.u32 %r1, [%rd2], 1;` | counted as unsupported |
| `ld.shared.f32 %f1, [%r2];` | counted as unsupported |
| `cp.async.ca.shared.global [%r1], [%rd2], 4;` | **matched by neither** |
| `cp.async.bulk.tensor.2d.shared::cluster.global.mbarrier::complete_tx::bytes [%r1], [tmap, {%r2,%r3}], [%r4];` | **matched by neither** |

Searching the two files above on `origin/hybrid` for `cp.async`, `tensormap` and
`bulk` returns zero hits for each of the three.

## Why it looks questionable to us

Line 27 of the design document
`docs/superpowers/specs/2026-08-09-hbfsim-hybrid-design.md` lists this goal:

```
Fail closed whenever an HBF address could reach an uninstrumented or unsupported memory operation.
```

`cp.async` copies from global memory into shared memory without going through a
register, and the bulk tensor copy instructions move a whole tile in one
instruction. Both read global memory. Under the two regular expressions above,
neither is rewritten and neither is added to the unsupported count. The result is
that the coverage record shows no trace that either instruction was ever present:
if an address registered as `HBF` is read by a `cp.async`, the current code
produces no entry of any kind — not a modeled delay, and not an unsupported-list
entry either.

## Which direction the effect goes

Downward for the modeled time, since an access that reaches `HBF` costs nothing.
The size depends on how many such instructions a workload issues against
registered ranges, which we have not measured. The second effect is on the
coverage record rather than on any number: a launch that uses these instructions
can be reported as covered.

## Where we may have read it wrong

Two readings under which the code is correct as written. First, these two
instruction families may never appear against a registered range in the workloads
this project runs, in which case there is nothing to catch. Second, there may be a
check above the `PTX` pass — at registration time, or when a kernel is admitted —
that already rejects or excludes such a kernel, so that the `PTX` pass never needs
to see the instructions. We did not find such a check, but we did not read every
path into the pass.

## What we would like you to confirm

1. Is the gap deliberate, or was it missed?
2. If deliberate: would you be willing to add the two instruction families to the
   unsupported-list regular expression anyway, so that their presence shows up in
   the coverage record even when nothing is modeled for them?

