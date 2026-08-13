# The detailed MQSim path does not model a cache-buffer hit either

**Severity: C.** No number produced so far is wrong because of this point. What the
point costs is the ability to settle the question in entry
`06-A-same-page-reread-charged-as-a-new-page.md` by comparison: the detailed path,
which is the one we would normally appeal to when the fast path is in doubt, gives
the same answer as the fast path for the same reason.

**How sure we are: read from the primary source** for the adapter file in this
repository. **Read from the primary source, but not re-checkable in the local tree**
for the two upstream MQSim files — the `third_party/mqsim` submodule is not checked
out here, so please confirm the two line numbers against the revision your build
actually compiles.

**Does this conflict with the OCP specification? Indirectly, through the same clauses
as entry 06** — sections 5.3.1 items 5a, 7 and 8, pages 56 to 57, and the BUCCAP
register field `NCBB` on page 70.

All code quoted from this repository was read from the remote branch `origin/hybrid`.

## What we read in the code

**In this repository.** `src/mqsim_adapter/mqsim_online.cpp` lines 58 to 76 configure
the detailed flash device simulator as SLC and set the three page-read latency
parameters `Page_Read_Latency_LSB`, `Page_Read_Latency_CSB` and
`Page_Read_Latency_MSB` all to the same value, `profile.read_latency_ns`. No cache-read
behaviour of any kind is configured.

**Upstream.** `src/nvm_chip/flash_memory/Flash_Chip.h` line 101 returns the same
latency for every read command, and `src/nvm_chip/flash_memory/Flash_Chip.cpp` line 114
charges that latency unconditionally. There is no branch on whether the page requested
is the page already held in the page register.

## Why it looks questionable to us

The paper describes the detailed MQSim path as the reference the fast path can be
checked against. For the one behaviour where the fast path currently has the sign
wrong — reading the same page twice — the reference path answers the same way, so the
comparison cannot detect the problem. The two paths agree, and both disagree with
sections 5.3.1 items 5a, 7 and 8 of the OCP specification.

## Which direction the effect goes

Upward, for any workload that reads the same 4 KiB page more than once within a short
distance: every read is charged a full page read. The size is a function of how much
such reuse the workload has, and we have not measured it on either path.

There is a second effect with no direction attached: the fast path and the detailed
path cannot disagree on this behaviour, so agreement between the two paths is not
evidence that either path is right on this behaviour. That matters for entry
`14-C-no-consistency-check-between-paths.md`, which asks for the comparison to be built
— the comparison, once built, will pass here without telling anyone anything.

## Where we may have read it wrong

We read two upstream files and searched for a branch on the page register. MQSim may
model cache reads somewhere else — under advanced command handling, under plane state,
or under a name we did not search for — in which case the adapter would only need to
switch the behaviour on rather than have it added. We also cannot rule out that the
version of MQSim your build uses differs from the one we read, because the submodule
is empty in our checkout.

It is also possible that this was a deliberate scoping decision matching the one in
`docs/superpowers/specs/2026-08-09-hbfsim-hybrid-design.md` line 30, which lists
modeling the cache hierarchy cycle by cycle as a non-goal. Whether a page-register hit
falls inside that non-goal is the same open question raised in entry
`09-B-no-page-residency-filter-across-warps.md`.

## What we would like you to confirm

1. Are the two upstream line numbers, `Flash_Chip.h` line 101 and `Flash_Chip.cpp`
   line 114, the right ones for the MQSim revision your build compiles?
2. Does MQSim have a cache-read or page-register path we did not find?
3. If neither path is going to model a cache-buffer hit before the deadline, do you
   agree that the consistency check asked for in entry
   `14-C-no-consistency-check-between-paths.md` should carry a written note saying that
   this particular behaviour is common to both paths and is therefore not covered by
   the check?

Please tell us which of the points here you agree with, which ones you think we have
read wrong, and which ones you intend to change.
