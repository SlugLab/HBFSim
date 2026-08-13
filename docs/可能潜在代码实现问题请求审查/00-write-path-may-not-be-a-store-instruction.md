# The write path on real hardware may not be a store instruction at all

**Severity: A.** If a write to HBF on the real device is issued by a piece of software
rather than by a store instruction, then the write side of our emulation models an
operation that does not exist on the device, which is the "the operation being modeled
could not run on the real device" case in the severity definition. Every result that
involves writing is affected, and the affected part is the layer, not the size of a
number.

**How sure we are.** Primary source for every sentence of OCP HBF architecture
specification v0.7.0 quoted below; the page number is given for each one, and all of them
were read in
`docs/HBF_OCP/ocp2026-hbf-architecture-specification-v0-7-0.txt`. Inference for one step,
and we mark it as inference wherever it appears: that no GPU instruction set has a store
instruction with the semantics the specification requires of a write.

**Does this conflict with the OCP specification? Not a conflict with the specification —
a question about which layer our emulation models.** The specification says what a write
must look like when it arrives at the base die. Nothing in the specification says what
produces the write on the xPU side, so the specification neither supports nor rules out
the reading we currently build on.

This point does not rest on a code line, so no line numbers from `origin/hybrid` are
quoted here. What it rests on is the specification text, and the layer our PTX pass works
at.

One note on wording before the quotations. In every sentence quoted below, `host` is the
specification's own word, and in this specification `host` means the xPU, that is, the GPU
or TPU, not the server CPU: page 90 reads
`providing a direct test interface between the host(xPU) and the HBF`, and `CPU` appears
once in the whole 130-page document, in a historical sentence in the introduction.

## What we read

**The nine rules the specification gives for a write, section 5.4.1, page 58.** The ones
that matter here, quoted:

```
All writes are non-posted writes. A write command received with any length receives
only one response sent to the host irrespective of length size
```

```
The write command is completed to the host only after the data is written to the Core
die.
```

```
The write granularity to the Core die is 4KiB. The Base die controller first
accumulates the complete 4KiB write commands and data is sent to the Core die.
```

```
No skipping or jumping of 4KiB local address is allowed within a host channel's
allocated global address range.
```

```
HBF will internally auto-erase the NAND block from the host point of view, when
receiving a page-0 (WL-0, STR-0, NAND block-X) write request of a NAND block.
```

```
When HBF gets the first 64Byte write request for a local address for that channel, HBF
shall receive all the 4KiB writes within a time limit. The time limit is set by the
host in the HBF config register during initialization.
```

```
The host shall write the 4KiB local address consecutively within the NAND block.
```

**The ordering rules, sections 11.5.2.2 and 11.5.2.6, pages 121 and 122.** Page 122:
`HBF requires the host to write sequentially within a NAND block, while the host can write
multiple random NAND blocks simultaneously.` Page 121: `HBF doesn't support random write
within the NAND block`, and rewriting a block requires the host to
`start writing from NAND block page 0, wordline 0, and string 0`.

**The write error codes, Table 13, pages 61 to 62.** `0x2` for an address overlap, `0x5`
for a 4 KiB accumulation that ran past the time limit, `0x6` for a write-order violation,
and `0x7` for a program failure, which requires the whole block to be replayed from
page 0.

**The read side, for contrast, pages 15, 56 and 57.** Every read request is at 64-byte
granularity; a burst runs from 64 B to 4 KiB in multiples of 64 B, on a 64-byte aligned
address, and does not cross a 4 KiB NAND page boundary; reads carrying different AXI IDs
may complete out of order.

**The layer above the descriptor is not defined by the specification, section 5.2.3,
page 40.** The `AXI Master Link Layer Adapter` translates descriptor information generated
by host software into AXI signals, and the specification states that the link layer on
each side is customised by the xPU vendor and the HBF vendor respectively. The
specification therefore stops below whatever produces those descriptors.

**Our own side.** Design goal 1 of the design document reads
`Automatically rewrite supported PTX global loads and stores without source-level changes
to workload kernels.` For writes, that means we model a write to HBF by rewriting
`st.global` in PTX, so a write in our emulation is, by construction, a store instruction
executed by the kernel under test.

## Why it looks questionable to us

Put the seven rules from page 58 and the two ordering rules from pages 121 and 122
together, and a write to HBF has to do all of the following: arrive in 64-byte pieces that
the base die accumulates into a complete 4 KiB unit; arrive within a time limit that the
host set in a configuration register at initialisation, counted from the first 64-byte
piece; cover 4 KiB local addresses consecutively inside a NAND block, with no skipping;
trigger an internal erase of the entire NAND block when the piece being written is page 0,
wordline 0, string 0 of that block; and return one response per write command, carrying a
status code that distinguishes an address overlap from an accumulation timeout from an
order violation from a program failure.

**This is the step that is our inference rather than a reading of the specification:** we
know of no GPU instruction set whose store instruction has these semantics. A store does
not force 4 KiB alignment, does not require the program to keep a sequential order inside
a region, does not erase a surrounding region when a particular address is written, does
not run against a timer that starts at the first piece, and does not deliver a per-command
error code back to the instruction that issued it. On that inference, a write to HBF is
very unlikely to be an ordinary `st.global`, and is instead maintained by a piece of
software — a runtime layer — that accumulates, orders and sequences the pieces, and that
handles the error codes.

The read side does not have this problem. The 64-byte granularity and the burst rule of
64 B to 4 KiB without crossing a page boundary line up with what memory coalescing on a
GPU already produces, so reads could plausibly be exposed as ordinary load instructions.

One consequence for the specification itself is worth stating so that this point is not
read as more than it is. Section 5.2.3 on page 40 hands the layer above the descriptor to
the xPU vendor and the HBF vendor. So the specification does not say that a GPU will
expose HBF as an address range that ordinary store instructions can reach, and it does not
say that a GPU will not. Both readings survive the specification, which is exactly why we
are asking you rather than concluding.

## Which direction the effect goes

There is no size to give here, and we are not claiming one. What is at stake is whether
the operation we model exists on the device. If the write path on real hardware is
maintained by a runtime rather than issued as a store instruction, then rewriting
`st.global` models a path that the real device does not have, and the error is in the
layer rather than in the number: the charge is applied in a place where no write command
is produced on real hardware. That is a different question from entry 11 in this
directory, which is about how much a kernel-issued write is charged; this point asks
whether a kernel-issued write is the right thing to charge at all.

Two parts of the work are affected most:

- **The key-value cache scenario**, which is write-heavy, so the share of its result that
  depends on the write path is large.
- **The dirty write-back in capacity mode**, where an evicted page is written back to the
  media.

The read side is not affected by this point.

## Where we may have read it wrong

1. **The xPU may present HBF as an addressable range and keep all of these rules below the
   store instruction.** Accumulation into 4 KiB units, the ordering inside a NAND block,
   the auto-erase on a page-0 write and the error handling could all sit in xPU-side
   hardware or firmware that the instruction never sees. Section 5.2.3, page 40, leaves
   that layer to the vendors, so this reading is open. Under this reading, rewriting
   `st.global` is the right layer, and only the charging model needs work.
2. **We have read no xPU-side documentation for an HBF-attached part**, because we have
   none. The inference above rests on the instruction sets we know, not on a document
   describing a device that has HBF attached. If you have such a document, it settles this
   point immediately.
3. **The error codes may never reach the issuing layer.** If the base die controller and
   its firmware absorb `0x2`, `0x5`, `0x6` and `0x7` and retry internally, then the
   presence of per-command status codes says nothing about which layer issues a write, and
   one of our arguments falls.
4. **The two layers may be mixed.** A runtime could arrange the address range in advance —
   erasing blocks, fixing the order in which 4 KiB units are filled — while the data
   itself still moves through store instructions. If that is how it works, we would like to
   know which part is which, because the part that remains a store instruction is the part
   our PTX pass can keep.

## What we would like you to confirm

1. **In what you know of the real hardware, is a write to HBF issued by a store
   instruction, or is it produced by a piece of software — a runtime layer — that builds
   the descriptors?**
2. **If it is the second, how should our approach of rewriting `st.global` change?** We
   would like your view on which layer the write path should be modeled at, given that the
   read path may stay where it is.
3. **Should the paper state this as a stated limit on what our emulation covers, or should
   the approach change before 2026-09-15?** Both are open to us, and the choice affects
   the key-value cache scenario and the capacity-mode write-back rather than the read
   results.

Please tell us which of the points here you agree with, which ones you think we have read
wrong, and which ones you intend to change.



