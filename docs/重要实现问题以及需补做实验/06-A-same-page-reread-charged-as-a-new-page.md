# Reading the same page a second time is charged more than reading the next page

**Severity: A.** The modeled order between two cases comes out the opposite way round
from both the OCP specification and the NAND interface standard. The size of the
difference is small, about 10%, but the sign is wrong, and a reviewer who checks the
sign does not need a measurement to see the sign.

**How sure we are: read from the primary source** for the code line, for the six
calibration breakpoints, and for the OCP specification text. **Second-hand** for the
ONFI 6.0 figure quoted at the end, which we have not yet read in the original
document.

**Does this conflict with the OCP specification? Yes.** Sections 5.3.1 and the
BUCCAP register description require a read that hits a cache buffer to be served
immediately; the code charges such a read the full first-page cost.

All code quoted here was read from the remote branch `origin/hybrid`.

## What we read in the code

`src/cuda_runtime/device/hbf_device.cuh` line 419 decides whether an access continues
the current run of pages:

```
previous_page + 1 == page
```

The condition holds only when the current page is the page immediately after the
previous one. When the same page is read a second time, `previous_page == page`, the
condition does not hold, the run length is reset to 1, and the access is charged at
the first breakpoint of the measured curve.

The measured curve for the `cd8p-vmem-p50` profile has six breakpoints: 1 page at
11,133 ns, 4 pages at 41,495 ns, 16 pages at 168,606 ns, 64 pages at 2,824,351 ns,
256 pages at 10,767,793 ns, 512 pages at 20,254,374 ns. Putting the first two
breakpoints into the code path above gives the two cases:

- **Same page read twice.** The second read is charged 11,133 ns, the first-breakpoint
  cost.
- **Next page read after the first.** The second read is charged the marginal cost
  inside a run, (41,495 − 11,133) / 3 = 10,120.67 ns, which we write as 10,121 ns.

So reading the same page again is charged about 10% more than reading the next page.

## Why it looks questionable to us

Both the OCP specification and the NAND interface standard put the two cases the other
way round.

**OCP specification v0.7.0, section 5.3.1, pages 56 to 57.** Item 5a:
`Cache hit reads are served immediately if there are no ordering violations`. Item 8:
`The Base die shall immediately serve the cache hit read from the cache buffer if
ordering is not violated`. Item 7:
`HBF supports two cache buffers for each bank to hold up to at least two pages of
data`. The BUCCAP register field `NCBB` on page 70 gives the buffer count and size:
`Each Cache Buffer Size is same as NAND Page Size(4KiB)` and
`0x01: 2 Cache Buffers per Bank (default)`.

**ONFI 6.0, section 6.20, `Change Read Column`** (second-hand, see below): once a page
has been sensed into the page register, a second access to the same page needs only a
change of column address and does not pay the sense time again; the quoted overhead is
0.25 to 0.35 µs.

Under both documents, the second read of the same page should be the cheaper of the
two cases, not the more expensive one.

## Which direction the effect goes

Two separate statements, and only the first has a size behind it.

1. **The relative order of the two cases is reversed.** Same-page re-read is charged
   11,133 ns against 10,121 ns for the next page, so the code says re-reading is about
   10% more expensive, while both documents say re-reading is much cheaper.
2. **The absolute cost of a same-page re-read comes out above what the hardware would
   take.** How far above depends on the sense time of the device being modeled, which
   the OCP specification does not give — the specification contains no read-latency
   figure of any kind. We give no factor.

## Where we may have read it wrong

The single scalar `empirical_burst_state` at `src/cuda_runtime/device/hbf_device.cuh`
line 71 packs three things — page number plus one, a read/write bit, and the run
length — and drives the variable the measured curve is indexed by. That variable is
sensitive to **how long the current run of consecutive pages is**, which is not the
same question as **whether this page was read before**. If the burst state was only
ever meant to model transfer length, then the same-page case was never in scope, and
the line is doing what it was designed to do. We would like to know whether that is
the reading.

There is also a limit on how much the effect can be worth, which argues for leaving
the line alone: section 5.3.1 item 7 guarantees only two 4 KiB buffers per bank, and
the specification itself advises the host to finish reading the first full 4 KiB page
before issuing a third 4 KiB address that would evict the first. The reuse distance
over which any of this matters is two pages.

## What we would like you to confirm

1. Was `previous_page + 1 == page` written to model run length only, with the
   same-page case deliberately out of scope?
2. If the line is to change, which of three routes do you prefer?
   - **(a) One-line change.** Make the condition
     `previous_page + 1 == page || previous_page == page`. The reversal disappears
     immediately, the same-page re-read is charged the marginal 10,121 ns, and no
     number we do not already have is needed. Two costs: a long run of re-reads of one
     page keeps pushing the run length up, so the charge slides into the more expensive
     part of the curve; and 10,121 ns is nowhere near the 0.25 to 0.35 µs the ONFI
     figure points at.
   - **(b) Model the cache buffer.** Two 4 KiB buffers per bank, hits served at a
     separate cost. Needs a number for the hit cost that we do not have from a source
     we have read.
   - **(c) Leave the line alone and state the bound in the paper.** Cheapest, but the
     statement then has to be that our model has the sign wrong on this case, which is
     harder to write than a statement that the size is approximate.
3. The ONFI 6.0 figure is second-hand for us. Do you have the original document, so
   the 0.25 to 0.35 µs can be quoted from the standard rather than from a summary?

Please tell us which of the points here you agree with, which ones you think we have
read wrong, and which ones you intend to change.
