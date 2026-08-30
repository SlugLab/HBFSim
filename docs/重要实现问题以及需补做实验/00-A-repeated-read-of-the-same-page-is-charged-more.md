# A repeated read of the same page is charged more than a read of the next page

**Severity: A.** The two cases come out in the opposite order from the order the OCP
specification requires, so the direction of the reported number is wrong rather than its
size being imprecise. A reviewer who checks the direction does not have to run anything.

**How sure we are: primary source** for the code quoted below, read at the line numbers
given, and for the three sentences of the OCP specification, each quoted with its item
number and its page number. **Second-hand** for the ONFI 6.0 figure quoted below, which we
have not read in the original standard. **Inference** for the 10.0% difference, which is
arithmetic on the two charged costs that follow from the six calibration breakpoints
already in the profile.

**Does this conflict with the OCP specification? Yes.** Section 5.3.1 items 5 a, 7 and 8,
pages 56 to 57, and the `BUCCAP` register field `NCBB` on page 70.

**Why this file is numbered `00`, and why there is no `06`.** The two-digit number in every
file name in this directory is the severity rank of the point, and this point has to be
listed first. The other thirteen points are already numbered `01` to `14`. There is no
`06` in that sequence: the file that held the number wrote up this same point against the
same source file and the same condition, and it has been merged into this file and
deleted. Renumbering the thirteen a second time is not worth what renumbering costs: the
previous renumbering broke seven cross-references elsewhere in the repository, and each of
the seven had to be found and fixed by hand. The number `00` puts this point first and
leaves the other thirteen file names untouched.

## What we read in the code

`src/cuda_runtime/device/hbf_device.cuh`, lines 407 to 425, in the function
`update_empirical_burst`:

```cpp
    std::uint32_t run_pages = 1;
    const auto previous_page = empirical_burst_page(previous);
    if (previous != 0 && empirical_burst_run_pages(previous) != 0 &&
        empirical_burst_operation(previous) == operation &&
        previous_page != UINT64_MAX && previous_page + 1 == page) {
        const auto previous_run = empirical_burst_run_pages(previous);
        run_pages = previous_run == kEmpiricalBurstRunMask
                        ? previous_run
                        : previous_run + 1;
    }
```

What the lines do. The quantity being maintained is the run length, meaning how many
consecutive pages the current sequence of accesses has covered. The run length is raised
by one only when the page number of the current access is exactly one greater than the
page number of the previous access, which is the test `previous_page + 1 == page`. When
the same page is read again, the page number of the current access equals the page number
of the previous access rather than being one greater, the test does not hold, and the run
length is reset to 1. The run length then goes into `empirical_service_ns`, called at
lines 470 to 479 of the same file, which reads the marginal cost belonging to that run
length off the measured curve.

## Why it looks questionable to us

**A run length of 1 selects the cost of a whole first page.** Substituting the six
calibration breakpoints the profile already carries gives the two cases side by side: a
repeated read of the same page is charged 11,133 ns, the cost at the first breakpoint,
while a sequential read of the next page, which is a run length of 2, is charged the
marginal cost inside a run, (41,495 − 11,133) / 3 = 10,120.67 ns, written here as
10,121 ns. A repeated read of the same page is therefore charged about 10.0% more than a
sequential read of the next page.

**The specification requires the opposite direction.** OCP HBF architecture specification
v0.7.0, section 5.3.1, pages 56 to 57. Item 5 a:

```
Cache hit reads are served immediately if there are no ordering violations.
```

Item 8 of the same section:

```
The Base die shall immediately serve the cache hit read from the cache buffer if
ordering is not violated.
```

Item 7 of the same section:

```
HBF supports two cache buffers for each bank to hold up to at least two pages of data.
```

The `BUCCAP` register description on page 70 states the same two buffers in the field
`NCBB`, which gives the number of cache buffers per bank: `0x01: 2 Cache Buffers per Bank
(default)`, with `Each Cache Buffer Size is same as NAND Page Size(4KiB)`.

Read together, these sentences say that a repeated read of the same page should hit a
cache buffer and be returned immediately, and in no case should a repeated read of the
same page cost more than a read of the next page.

**The NAND interface standard puts the two cases the same way round.** ONFI 6.0, section
6.20, `Change Read Column`: once a page has been sensed into the page register, a second
access to the same page needs only a change of column address and does not pay the sense
time again, at a quoted overhead of 0.25 to 0.35 µs. This figure is second-hand for us —
we have read it in a summary and not in the ONFI standard itself. If you have the original
document, the figure can be quoted from the standard rather than from a summary.

## Which direction the effect goes

**For a workload that reads the same page again, the reported time comes out too high.**
How much too high depends on what share of the accesses in the workload fall back on a
page that has already been brought in, and this project has not measured that share, so no
factor is given here. The direction is settled: too high, not too low.

**One limit on the scope of the point has to be stated with it.** The measured curve used
in this project was calibrated on a complete path — a host software paging path plus one
Dell CD8P NVMe solid-state drive — and not on the NAND medium by itself. A curve
calibrated that way carries no notion of a cache-buffer hit at all. The defect is
therefore in applying a curve that has no notion of a hit to a device that has one, rather
than in any single measurement that produced the curve.

## Where we may have read it wrong

1. **Resetting the run length to 1 may be a deliberate conservative choice.** The measured
   curve cannot observe a cache-buffer hit, because the calibration path has no such
   event, so charging a full first page rather than inventing a cheaper cost may be the
   choice that was made on purpose.
2. **A repeated read of the same page may be rare in a real workload.** If the share is
   small enough, the deviation described above may not be visible in a measured run at
   all.
3. **Repeated accesses to one page are already merged inside a warp**, at
   `src/cuda_runtime/device/hbf_device.cu` lines 506 to 519. Some part of the repeated
   reads is therefore filtered out before reaching the code quoted above, and the share
   that survives the merge is smaller than the share the workload issues.

4. **There is a bound on how much the effect can be worth, and the bound argues for
   leaving the line alone.** Section 5.3.1 item 7 guarantees only two 4 KiB buffers per
   bank, and the specification itself advises the host to finish reading the first full
   4 KiB page before issuing a third 4 KiB address that would evict the first. The reuse
   distance over which any of this matters is two pages. A workload whose repeated reads
   fall outside that distance gains nothing from a fix, so the size of the error depends
   on the reuse distance of the access stream and not only on how often a page repeats.
   (Carried over verbatim from the entry this file absorbed; it is the strongest argument
   against acting on this point, and it should not be lost in the merge.)

## What we would like you to confirm

1. **Is resetting the run length to 1 on a repeated read of the same page deliberate, or
   was the case simply missed?**
2. **If the code is to change, how much cheaper should a hit be?** The specification says
   only that the read is served immediately and states no timing value anywhere, so this
   number has no source we can quote. One way to proceed is the one Micron takes in its
   HotInfra '26 paper: mark the value as an assumption, name where the value came from,
   and run one sensitivity check around the value.
3. **After the change, does the 20.8x result have to be produced again?** The source of
   that result is `docs/proofs/2026-08-11-hybrid-complete.md`, lines 27 to 30: `On the
   deterministic Qwen3-30B-A3B vLLM smoke, reference generation took 44.469 s and fast
   generation took 2.014352 s, a 20.8x emulator speedup.` The same passage records that
   the two runs produced identical token IDs, and that `This is emulator wall time, not a
   hardware HBF projection.`

**One figure that carries two different ratios.** The 44.469 s figure is the numerator of
both ratios this project quotes, and the two ratios are two different statements. Against
the uninstrumented baseline of 0.269996 s, 44.469 s is 164.70x. Against the fast path at
2.014352 s, 44.469 s is 20.8x. One figure, two denominators: whenever 44.469 s is cited,
name which of the two ratios is meant.

**One thing this point is not.** The point above is a defect to be fixed, and it is not a
precondition for the paper's second challenge, C2. The two numbers C2 rests on — 44.469 s
on the detailed path against 2.014352 s on the fast path, and the deviation of the six
calibration breakpoints — both stand today.

Please tell us which of the points here you agree with, which ones you think we have read
wrong, and which ones you intend to change.





