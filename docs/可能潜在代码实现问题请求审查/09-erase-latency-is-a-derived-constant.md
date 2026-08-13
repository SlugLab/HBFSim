# The block erase time is derived from the program time by a factor of ten

**Severity: B.** The direction of the error is unknown and no result reported so far
depends on the constant. The severity is B rather than C because of what happens next:
if the write path is changed as asked in entry
`11-write-charged-per-instruction-not-per-4kib-unit.md`, and if the automatic erase
that the OCP specification attaches to a page-0 write is modeled, then this constant
becomes one of the largest single terms in the cost of writing.

**How sure we are: read from the primary source** for the line and for the profile
value. **Our own inference** for the statement that no measurement stands behind the
factor of ten — we searched and found none, which is not the same as knowing none
exists. You may have a source we do not have.

**Does this conflict with the OCP specification? No.** The OCP specification v0.7.0
contains no timing figure for erase, or for anything else. That is precisely why the
constant has to come from somewhere else, and why we are asking where.

All code quoted here was read from the remote branch `origin/hybrid`.

## What we read in the code

`src/mqsim_adapter/mqsim_online.cpp` line 67:

```
Block_Erase_Latency = profile.program_latency_ns * 10
```

For the `cd8p-vmem-p50` profile, `program_latency_ns` is 408,305, so the erase time
used is 408,305 × 10 = 4,083,050 ns, about 4.08 ms.

## Why it looks questionable to us

Every other media number in the profiles is traceable. The six breakpoints of the
measured curve — 1 page at 11,133 ns, 4 pages at 41,495 ns, 16 pages at 168,606 ns,
64 pages at 2,824,351 ns, 256 pages at 10,767,793 ns, 512 pages at 20,254,374 ns —
come from a calibration run on the Dell CD8P device and are recorded in
`docs/proofs/2026-08-11-cd8p-vmem-tuning.md`. The erase time is the one number in the
media model that comes from an arithmetic relation to another number rather than from
a measurement or a document, and we could not find the source of the factor of ten.

The reason this matters for the paper rather than only for the code: the OCP
specification, section 5.4.1 item 5 on page 58, makes erase implicit in the write
path —
`HBF will internally auto-erase the NAND block from the host point of view, when
receiving a page-0 (WL-0, STR-0, NAND block-X) write request of a NAND block.` There is
no separate erase command a host can choose not to issue. So any write workload that
opens a new block pays an erase, and the size of the erase decides how large the
write-side cost is.

## Which direction the effect goes

Unknown. We have not read a source that gives a block erase time for the class of 3D
NAND that HBF stacks, so we cannot say whether 4.08 ms is high or low. What we can say
is that the number carries no evidence today, and that a reviewer who opens
`mqsim_online.cpp` will see the multiplication.

## Where we may have read it wrong

Three readings under which the line is fine as written.

1. The factor of ten may come from a datasheet or a vendor figure you have and we do
   not, in which case the fix is a comment naming the source, not a change to the
   number.
2. The detailed MQSim path may not have contributed to any reported result yet, in
   which case the constant has never entered a number that anyone reads.
3. The constant may be a deliberate placeholder awaiting the same measurement pass
   that is still open elsewhere, in which case the only thing missing is a note saying
   so.

## What we would like you to confirm

1. Where does the factor of ten come from?
2. If no source exists, do you want the erase time exposed as a swept parameter with
   the range stated in the paper, in the same way activation energy is handled in item
   16 of `docs/EXPERIMENTS-NEEDED.md`, rather than fixed at one unsourced value?
3. Has the detailed MQSim path contributed to any number in `docs/proofs/`? If it has,
   we need to know which document, so the constant can be disclosed there.

Please tell us which of the points here you agree with, which ones you think we have
read wrong, and which ones you intend to change.
