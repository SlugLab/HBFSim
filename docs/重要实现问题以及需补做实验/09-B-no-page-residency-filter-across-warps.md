# A page read again by another warp, or later in time, is charged again

All code quoted here was read from the remote branch `origin/hybrid`.

**One claim we withdrew before writing this file.** We first thought that charging
every access at the granularity of a whole page was itself wrong. Checking the
code showed the claim does not hold, and we are recording the withdrawal here so
that the point that remains is not read as the point that fell. Two reasons the
claim does not hold. Page granularity is the right unit for NAND, because one NAND
read reads a whole page. And accesses to the same page from within one warp — a
warp being a group of threads on the GPU that execute in lockstep, normally 32 of
them — are already merged: `src/cuda_runtime/device/hbf_device.cu` lines 506 to
510 use `__match_any_sync` to group the lanes that fall in the same range and on
the same logical page, and lines 513 to 519 let only the leader of each group
issue one request while the remaining lanes wait on that one result. Thirty-two
accesses to the same page from one warp produce one modeled request.

## What we read in the code

Searching `src/` and `include/` on `origin/hybrid` for `cache_hit`, `hit_rate`
and `l2_hit` returns zero hits for each of the three. The merging described above
holds within one warp and within one call; nothing carries a record of which pages
have been touched from one warp to another, or from one moment to a later one.

## Why it looks questionable to us

Once the same page is read by a second warp, or read again by the first warp a
while later, the code charges another full media access. On hardware a large share
of such accesses never reach the media at all: they are served by the L2 cache, or
by the page cache that the capacity mode — the mode that places data in a real
buffer and hands back the address of that buffer — maintains.

## Which direction the effect goes

Upward: the modeled time comes out above what the hardware would take. How far
above is a function of how much reuse the workload has — a workload that streams
through data once is barely affected, a workload that reads the same weights on
every step is affected a great deal. We have not measured either case, and give no
factor here.

## Where we may have read it wrong

Line 30 of `docs/superpowers/specs/2026-08-09-hbfsim-hybrid-design.md` lists this
as a non-goal:

```
Modeling GPU SM pipelines, instruction scheduling, or cache hierarchy cycle by cycle.
```

We cannot tell whether that non-goal covers what we are asking about. The sentence
rules out following the cache hierarchy cycle by cycle. Keeping a record of which
pages are currently resident, and skipping the media cost when a page is, is a
coarser thing than that, and it may or may not be what the sentence was written to
exclude. If it was, then the code is doing what it was designed to do and the only
open question is the one below about the write-up.

## What we would like you to confirm

1. Was leaving this out deliberate, and does the non-goal on line 30 cover it?
2. If it stays out, how do you want the evaluation to state the direction? The
   direction is known even without a measurement, so a reviewer will ask, and we
   would rather agree the wording with you than write it and have you correct it
   later.

