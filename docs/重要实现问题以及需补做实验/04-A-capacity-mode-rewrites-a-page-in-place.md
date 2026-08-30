# The capacity mode writes an evicted page back to its own address, which HBF forbids

**Severity: A.** This is not a number that comes out wrong. It is a mode whose write
path could not run on the device being modeled: the specification defines an error code
for exactly this operation, and the only legal way to carry it out costs 256 times as
much as the code assumes.

**How sure we are.** Read from the primary source: the code path below and the
specification text on pages 117, 120, 121 and 122. Our own inference: the mapping of
our write-back onto write error `0x2`, and the 104.5 ms and 256-times figures, which are
arithmetic on top of the profile values.

**Does this conflict with the OCP specification? Yes**, section 11.5.2.2 on page 121 and
section 11.5.2.6 on page 122.

All code quoted here was read from the remote branch `origin/hybrid`.

## What we read in the code

The capacity mode keeps pages in an HBM page cache and writes a dirty page back to the
media when the page is evicted. The path is:

- `src/host_service/capacity_page_service.cpp` lines 96, 104 and 181 mark a page dirty;
- lines 114, 194, 253 and 327 of the same file choose the page to evict;
- line 131 sets the request kind to `CapacityMediaProgram`;
- `src/host_service/request_dispatcher.cpp` lines 107 to 120 turn that into a request
  with `operation = Write` and address `program_page * bytes`;
- `src/cuda_runtime/hbm_cache.cpp` lines 81 to 100 mark dirty and lines 114 to 175
  perform the eviction.

The address written is the address the page came from.

## Why it looks questionable to us

OCP specification v0.7.0, section 11.5.2.2, page 121:
`HBF doesn't support random write within the NAND block`. To change the contents of a
block, the host must `start writing from NAND block page 0, wordline 0, and string 0`.

Section 11.5.2.6, page 122:
`HBF requires the host to write sequentially within a NAND block, while the host can
write multiple random NAND blocks simultaneously.` The sequential requirement applies
within one NAND block; several blocks may be open at once.

Section 11.4, page 117:
`HBF does not support any GC (garbage collection) or the transfer of active data from
one physical location to another.` The device will not tidy up behind the host. Zone
remapping does not help either: the specification states that the command
`only updates the logical-to-physical mapping within HBF device. It does not migrate or
copy the user data between physical NAND b...` — the sentence is cut off in our text
extraction, and we quote only as much as we have read.

Writing a page back to an address that has already been written is the case the
specification's write error table covers: table 13, pages 61 and 62, defines `0x2` as
address overlap, meaning already written or already pending, and `0x6` as a write order
violation, with additional information `0x1` for skipping a page. **Mapping our
write-back onto error `0x2` is our inference, not a quotation.**

What the legal alternative costs, on the `cd8p-vmem-p50` profile: capacity
1,919,850,381,312 bytes divided by 4,096 gives 468,713,472 pages, and at 256 pages per
block that is 1,830,912 blocks of 1 MiB each — which sits inside the range the
specification allows on page 120, `1MiB, 2MiB, or 4MiB per NAND block`. Changing one
4 KiB page inside a written block by replaying the whole block costs
256 × 408,305 = 104,526,080 ns, about **104.5 ms**, and a write amplification of **256
times**.

## Which direction the effect goes

Two effects, and neither is a percentage.

1. **The modeled cost of an eviction is far below the legal cost on hardware**: one
   program time of 408,305 ns against a whole-block replay of 104,526,080 ns, if the
   block is replayed.
2. **More seriously, the operation as modeled is not available on the device.** A
   reader who compares the capacity mode against the specification concludes that the
   mode assumes update-in-place, and update-in-place is the one thing this device does
   not offer. No amount of recalibration fixes that; the mode has to work differently.

## Where we may have read it wrong

1. The capacity mode may be scoped deliberately as a model of an HBM page cache sitting
   in front of a device whose write path is out of scope for this paper, in which case
   the answer is a stated bound rather than a code change.
2. The write-back address may be reassigned somewhere we did not read, so that the
   request does not in fact go back to the original location. We read the four files
   above and did not find a reassignment.
3. Our mapping onto error `0x2` may be the wrong error code even if the operation is
   indeed illegal; the specification's own example of the overlap case is what we
   matched against.

## The two ways out, with what each costs

- **Append-only writing with a page directory.** Evicted pages are written to the next
  free position in an open block, and a directory maps the logical page to the position
  actually used. Legal on the device and cheap per eviction; the cost is that the
  capacity mode becomes a different design, with a directory to maintain and free-block
  accounting to do — the device does no garbage collection, so reclaiming has to be
  modeled somewhere.
- **Honest whole-block replay.** Keep the current placement and pay 104.5 ms per
  modified block. Small change, faithful, and it very likely makes the capacity mode
  unusable for any workload that writes.

This is the one item in this directory we would call a design change rather than a fix.

## What we would like you to confirm

1. Is the capacity mode meant to model writes to HBF at all, or is the write-back path
   there only to make the cache complete?
2. If the write path is in scope, which of the two routes above do you want, and who
   writes it?
3. If the write path is out of scope for the submission, may we state that bound in the
   paper — that the capacity mode models the read side of an HBM page cache and that its
   eviction path assumes update-in-place, which HBF does not provide?

Please tell us which of the points here you agree with, which ones you think we have
read wrong, and which ones you intend to change.
