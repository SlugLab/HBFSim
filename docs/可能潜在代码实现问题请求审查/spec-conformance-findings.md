# What the OCP specification actually says, and where our earlier wording was wrong

**What this file is.** We read the OCP HBF architecture specification v0.7.0 end to end
and checked every statement this project makes about the device against the text. This
file lists what we found, one item at a time, with the page number for each. Several
items correct wording we ourselves used earlier, so this file is as much a list of our
own errors as a list of facts.

**Which document, and how we read it.** `docs/HBF_OCP/ocp2026-hbf-architecture-specification-v0-7-0.pdf`,
version 0.7.0, dated 2026-08-03, 130 pages, with the text extracted alongside it as a
`.txt` file in the same directory. Page numbers below are the printed page numbers of the
PDF. Every quotation is copied from the extracted text.

**How each item is marked.** Read from the primary source means we read the sentence in
this specification. Second-hand means the item rests on a document we have not read.
Inference means the item is our reasoning on top of quoted text, and is not itself
quoted.

**One item in this file is deliberately not in the paper.** Finding 2 below — that HBF is
reached through AXI transactions rather than through ordinary memory instructions, and
that the layer above the descriptor is vendor-defined — stays in this file and in our
discussion with you. It is not in the paper and not in the outline. We are telling you
because it bears on how the simulator should be described between us, not because it
changes what the paper claims.

---

## 1. In this specification, "Host" means the xPU, not the server CPU

**Read from the primary source.**

Page 90: `providing a direct test interface between the host(xPU) and the HBF`.
Page 15: `The High Bandwidth Flash (HBF) is tightly coupled to the xPU Host Compute die`.
Searching the whole document for `CPU` gives one occurrence, in a historical statement in
the introduction.

**Why this matters to us.** We have written sentences of the form "the host side has no
submission queue, no doorbell register and no driver layer to replace". Read with this
specification's vocabulary, that sentence is false: the host software in this
specification is software on the xPU, and that software does issue commands — page 70
even gives it a `Maximum Outstanding Commands Supported (MOCS)` register.

**What we are changing.** Wherever we mean the x86 or ARM processor, we now write "the
server CPU side". Wherever we mean what the specification calls the host, we write "the
xPU, which the specification calls the host". We are not asking you to do anything here;
we are recording the correction so that our documents and yours use the words the same
way.

## 2. HBF is reached by AXI transactions carrying a command type, and the layer above the descriptor is not defined by the specification

**Read from the primary source.** *(This finding stays in this file. It is not in the
paper and not in the outline.)*

The address space is flat and computed by software. Page 22, Figure 3:
`Host Software HBF Global Address (G_Addr) (64B Granularity)`, with a closed-form
computation of the channel number and the local address, and `Total_UCIe_Ch = 16`.

Each access is an AXI transaction carrying a command type field. Page 41, Table 8:
`ARUSER [1:0]: Cmd_packet_type; 2b00: Flash IO CMD; 2b01: Scratchpad IO CMD; 2b10:
CSR/ADMIN`. Each transaction carries an AXI ID and may complete out of order, and each
returns a per-command status code, `RUSER [5:2]: CMD Status`, with the status table on
pages 59 to 60, Table 12.

Page 40, section 5.2.3: software on the xPU produces descriptors, and the
`AXI Master Link Layer Adapter` turns descriptors into AXI signals. **Everything above
the descriptor, inside the xPU, is vendor-defined and outside the scope of this
specification.**

**Why we are telling you.** This project rewrites `ld.global` and `st.global`
instructions, which models a machine in which the xPU exposes HBF as a range of addresses
reachable with ordinary memory instructions. The specification neither requires nor
forbids that arrangement; it simply does not define the layer where the arrangement would
live. If a vendor instead offers only a descriptor submission interface, the object to
rewrite changes from a memory instruction to the call site of that interface.

## 3. Read and write granularity are not symmetric

**Read from the primary source.**

**Read side.** Page 56, section 5.3.1: `All Read requests are 64-byte granularity`.
Page 15: `Burst Length of 64B to 4KiB in multiple of 64B for read access on 64B aligned
address and does not cross 4KiB NAND page boundary`. Section 5.3.1 items 1 to 4, page 56,
require 64 read commands with distinct AXI IDs to fetch one full 4 KiB page, with a burst
read of length 0 to 63 allowed where the product supports it. Items 5 to 9, page 57:
different AXI IDs may complete out of order while one AXI ID stays ordered;
`Cache hit reads are served immediately if there are no ordering violations`; sense
requests to the same bank stay strictly ordered;
`HBF supports two cache buffers for each bank to hold up to at least two pages of data`;
`The Base die shall immediately serve the cache hit read from the cache buffer if
ordering is not violated`. Page 70, BUCCAP register field `NCBB`:
`Each Cache Buffer Size is same as NAND Page Size(4KiB)`, and
`0x01: 2 Cache Buffers per Bank (default)`. Section 5.3.2, page 57: ordinary reads are
gathered and scheduled together, while a batch read, indicated by AXI user bit 0, is
scheduled without waiting for the timer.

**Write side.** Page 15: `Burst Length of 4KiB on write access and on a 4KiB aligned
address`. Page 58, section 5.4.1, nine requirements, of which we quote six:

```
All writes are non-posted writes. A write command received with any length receives
only one response sent to the host irrespective of length size
```
```
The write command is completed to the host only after the data is written to the
Core die.
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
The host shall write the 4KiB local address consecutively within the NAND block.
The NAND block size is defined in the product specification.
```

Item 6 of the same section sets an accumulation timeout:
`When HBF gets the first 64Byte write request for a local address for that channel, HBF
shall receive all the 4KiB writes within a time limit. The time limit is set by the host
in the HBF config register during initialization.` Page 122, section 11.5.2.5 repeats it
as `The host can configure the maximum wait time`. Item 7 makes the number of outstanding
4 KiB write units per host channel product-defined:
`For example, it is 64 or 128 or any other number`.

Violating the order returns write error `0x6`, and failing to complete a 4 KiB unit
within the timeout returns write error `0x5` (Table 13, pages 61 and 62; the timeout case
allows the whole 4 KiB unit to be resent).

**Scope of the sequential rule.** Page 122, section 11.5.2.6:
`HBF requires the host to write sequentially within a NAND block, while the host can
write multiple random NAND blocks simultaneously.` Page 121, section 11.5.2.2:
`HBF doesn't support random write within the NAND block`; rewriting a block requires
`start writing from NAND block page 0, wordline 0, and string 0`. Page 62, section 5.7
gives the closed-form computation of an address inside a block and requires a whole-block
replay to be issued as `L2, L2 + R5, L2 + 2*R5, …, L2 + (R3-1)*R5`. Page 117, section
11.4: `HBF does not support any GC (garbage collection) or the transfer of active data
from one physical location to another.`

**No partial page write, but there is a buffer.** The granularity reaching the core die
is 4 KiB (item 3 above). Page 58, section 5.4.2 says scratchpad writes
`does not have to be 4KiB page size` and `All scratchpad writes have 64B granularity`,
which is the contrast case. The accumulation buffer on the base die is visible to reads:
page 59, section 5.4.3 says a read that hits the accumulated part returns the accumulated
data, and a read that hits a part not yet received returns read error `0xA`. The
scratchpad itself (section 5.4.2 page 58, section 11.3 page 116) is an optional SRAM
buffer, per channel, 64-byte granularity, returned in a few cycles, lost on power
removal, managed by the host, and its presence is product-defined.

**Two more write-side facts.** Page 115, section 11.2.2: write performance is best when
all banks of all channels are written with the same page number within a NAND die. Page
125, section 13.1.1: when a model is loaded,
`the data is written at 4KiB * N * 16 sequentially across all channels`.

## 4. There is no per-access event anywhere outside the program under test

**Read from the primary source.** Searching the whole specification gives: `page fault`
0 occurrences, `doorbell` 0, `submission queue` 0, `completion queue` 0, `DMA` 0, and
`interrupt` 1 occurrence, on page 99, where it concerns the risk of running a test mode
concurrently with mission mode and has nothing to do with access.

This is the fact behind our claim that the effect can only be written into the compiled
artifact of the program under test, and it is also the reason no external counter can
tell us how many accesses were skipped. See entry
`10-accounting-unit-is-the-kernel-launch.md`.

## 5. The specification contains no read latency figure of any kind

**Read from the primary source.** We have written, more than once, that the
specification gives a page read latency. It does not, anywhere. That phrasing was ours
and has been removed from our documents. Any latency figure we use has to be attributed
to its real source: our own CD8P calibration, a NAND datasheet, or another paper's swept
assumption.

## 6. The headline numbers, and two places where the specification disagrees with itself

**Read from the primary source.**

- **Page size 4 KiB**, pages 15, 16 and 74.
- **Three bandwidth points: 0.384, 1.536 and 3.072 TB/s**, page 16, Table 4. Page 15
  states the same top figure as `up to 3.072 TiB/s of user bandwidth`. **The units differ
  between the two pages.** Page 14 defines `GiB = 2^30 bytes and GB = 10^9 bytes`, so the
  two spellings are not the same quantity. We write the three points as
  0.384 / 1.536 / 3.072 TB/s, following Table 4, and flag the discrepancy rather than
  choosing silently.
- **Capacity per stack: 512 GiB or higher**, with the binary definition on page 14 above.
  Item 11 of `docs/EXPERIMENTS-NEEDED.md` currently says "512 GB per stack"; that should
  become GiB.
- **Second internal disagreement.** Page 15 gives the write burst length as 4 KiB, while
  section 5.4.1 item 6 on page 58 describes the host issuing 64-byte write requests that
  the device accumulates. The specification does not reconcile the two.

## 7. Correction to what we said about the failed-capacity bitmap

**Read from the primary source, and this corrects an earlier statement of ours.**

We previously wrote that a 1024-bit bitmap of unusable capacity does not exist. That was
wrong in one direction and right in another, so both halves are stated here.

- **The MMIO register is small.** `0x014C–0x014F` is a 4-byte read-only register,
  `REDCAP, Reduced capacity information`, in the MMIO offset table on page 69, with the
  field description on page 76: only bit 0 carries meaning. **There is no bitmap at this
  address.**
- **The bitmap does exist, and is read another way.** Page 120, section 11.5.1.8:
  `could be retrieved from the CSR registers by using CSR Command(opcode=0x0A). The 1024
  CSR which is 1024-bitmap will contain the unusable logical capacity information per
  NAND block. The 1024 CSRs support 1MiB, 2MiB, or 4MiB per NAND block capacity. Each bit
  in the CSR represents 1 NAND block`. The command format is on page 50, in the CSR/ADMIN
  read description: `[15:8] Report Info Type; 0x00: Per-Block Bitmap` and
  `[7:0] Opcode; 0x0A`.

The correct statement is therefore: the bitmap is not in the MMIO `REDCAP` register; it
is read with CSR command `0x0A`.

## 8. Under severe throttling, new commands are back-pressured, not rejected

**Read from the primary source.** Page 107: status code `0x9` applies to commands already
in flight at the moment severe throttling begins. New commands are held back by
deasserting AXI Ready and withholding credits; they are not returned with an error. Any
thermal model we build has to express throttling as commands waiting longer, not as
commands failing.

## 9. Zone remapping changes the mapping only, and the specification gives no time for it

**Read from the primary source.** The remap command
`only updates the logical-to-physical mapping within HBF device. It does not migrate or
copy the user data between physical NAND b...` — our text extraction truncates the
sentence there, and we quote only what we have read. Page 117, section 11.4 adds the
constraint that a zone must be a whole multiple of the core die NAND block size, and that
a product may bound the spread of average erase counts between zones.

We have previously described this remapping as taking nanoseconds. **No such figure is in
the specification**, and we have removed the word from our documents.

## 10. Wear leveling has two selectable modes, so "the host manages wear" is only half true

**Read from the primary source.** The `BUCC` register, bits 07 to 06:
`Wear Leveling Selected (WLS): 01b: Base Die-driven automatic wear leveling is selected;
10b: Host-controlled wear ...` — the extraction truncates the second option, and we quote
only what we have read. Our earlier statement that wear management is the host's
responsibility holds in one of the two modes and not in the other, so the mode has to be
named wherever the statement is used.

## 11. Host-side periodic refresh is in the specification

**Read from the primary source.** Page 118, section 11.5:
`periodic data refresh is required at intervals specified in the reliability
specification`. The interval itself lives in a reliability specification we do not have,
so any refresh period we use is ours and must be labelled as such.

## 12. The two error tables

**Read from the primary source.** Read errors, Table 12, pages 59 to 61, define `0x1`
through `0xA`. Write errors, Table 13, pages 61 and 62, define nine codes: `0x1` invalid
address; `0x2` address overlap, meaning already written or already pending, which is the
random-write case; `0x3` illegal user field; `0x4` the outstanding 4 KiB unit limit
reached; `0x5` the 4 KiB accumulation timeout, where the whole 4 KiB unit may be resent;
`0x6` write order violation, with additional information `0x1` for a skipped page and
`0x2` for a preceding page whose program failed; `0x7` program failure requiring the whole
block to be replayed from page 0; `0x8` channel local capacity marked unusable; `0x9` a
die temporarily locked for recovery. `0xA` to `0xC` are reserved and `0xD` to `0xF` are
vendor-defined.

**One inference, marked as such.** The accumulation timeout of section 5.4.1 item 6 is set
by the host in a configuration register, and the only host-writable timer facility in the
register map is `0x0100–0x013F  TMR  Product-specific timers` (read/write, page 69),
described on page 75 as a value plus a resolution selected from nanoseconds, microseconds,
milliseconds, seconds, minutes or hours, with 64 timers in total. **The specification does
not say which TMR index carries this timeout.** In the simulator it can only be a
configurable parameter; we must not claim it is bound to a named register.

---

## 13. Statements of ours that have to change

| What we said before | What the specification says | Where |
|---|---|---|
| The specification gives a page read latency | No latency figure of any kind appears in the document | Finding 5 |
| The host side has no queue, no doorbell, no driver layer | True of the server CPU side; false of the host as this specification uses the word, which is the xPU | Finding 1 |
| A 1024-bit failed-capacity bitmap does not exist | The bitmap exists and is read with CSR command `0x0A`; what does not exist is a bitmap at the MMIO `REDCAP` address | Finding 7 |
| Zone remapping takes nanoseconds | No time is given for the remap command | Finding 9 |
| Wear management is the host's job | True in one of the two selectable modes only | Finding 10 |
| 512 GB per stack | 512 GiB or higher, with `GiB = 2^30 bytes` defined on page 14 | Finding 6 |
| Three bandwidth points 0.4 to 3.0 TB/s | 0.384 / 1.536 / 3.072 TB/s from Table 4, with the specification itself writing TiB/s on page 15 | Finding 6 |

## 14. Which findings turned into questions about the code

Four of the findings above are the reason four entries exist in this directory:

- Finding 3, read side, cache buffers → `07-same-page-reread-charged-as-a-new-page.md`
  and `08-reference-path-has-no-cache-read-either.md`.
- Finding 3, write side, 4 KiB accumulation →
  `11-write-charged-per-instruction-not-per-4kib-unit.md`.
- Finding 3, sequential writing within a block →
  `12-capacity-mode-rewrites-a-page-in-place.md`.
- Finding 4, no per-access event → `10-accounting-unit-is-the-kernel-launch.md`.

## 15. One broken pointer we found while checking

`docs/35-FAST27论文大纲与逻辑线.md`, appendix D, holds a table of eight candidate
features. The table has no write-side entry: item 6 covers the read side, the 64-byte
requests with multiple AXI IDs, and item 4 covers wear accounting. The table points to a
longer version at `docs/hbf-spec-feature-suggestions.md`, and that file does not exist in
the repository. Both of these are things only you can settle — whether the longer file
exists somewhere else, and whether the write side belongs in the table.

## 16. What we would like you to confirm

1. Do you agree with the seven corrections in section 13? Each of the seven appears in
   text we have written, so if any of the seven is wrong we would rather find out before
   the wording spreads.
2. Do you read section 5.4.1 the same way we do — that the program cost is paid once per
   4 KiB unit rather than once per write command?
3. Do you have the reliability specification referred to on page 118, which holds the
   refresh interval? We do not.
4. Do you have a product specification for a real part, which would settle the three
   values the architecture specification leaves product-defined: the NAND block size, the
   number of outstanding 4 KiB write units per channel, and the accumulation timeout?
5. Does the longer feature-suggestion file named in appendix D exist somewhere we cannot
   see?

Please tell us which of the points here you agree with, which ones you think we have read
wrong, and which ones you intend to change.


