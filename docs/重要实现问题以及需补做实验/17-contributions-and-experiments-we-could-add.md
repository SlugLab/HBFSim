# Things we could add before 2026-09-15, in the order we would do them

**What this file is.** Eight pieces of work that came out of reading the OCP
specification against the code, ordered by what we would do first. Three of the eight are
repairs that have to happen whatever the paper says; four are groundwork; one is a
candidate contribution with a judgement attached, including the case against it. The
sixteen experiments already agreed live in `15-experiments-we-must-add-before-submission.md` and are not
repeated here; section 9 says where the new items sit relative to those sixteen.

**How the order was decided.** First, anything that makes a reported number wrong.
Second, anything without which a claim cannot be stated at all. Third, anything that adds
a claim. Effort estimates are ours and are in working days for one person who knows the
code; where a number is missing rather than the code, the estimate says so, because no
amount of coding closes a measurement gap.

**How each item is marked.** Read from the primary source means we read the code line,
the specification page, or the paper's own text. Second-hand means the item rests on
material we have not read in the original. Inference means the item is our reasoning on
top of checked facts.

---

## 1. Charge a write once per 4 KiB unit, not once per instruction

**Priority: highest. Not a contribution — a repair.**

**Why.** Today a store into a registered range is charged one full program time per
instruction, so filling one 4 KiB page costs 8 to 32 times what the specification says.
Every conclusion involving writes is affected. The evidence and the code lines are in
entry `03-A-write-charged-per-instruction-not-per-4kib.md`.

**Effort.** About 150 to 250 lines including tests. A 64-bit coverage mask per open 4 KiB
unit — 4 KiB divided by the 64-byte write granularity is exactly 64 pieces — charged once
when the mask fills. The open-unit table is 4,096 entries of 16 bytes, 64 KB, held in a
new shared region rather than in the header. The header itself has 40 bytes of tail
padding free, so `static_assert(sizeof(SharedControlHeader) == 384)` and every existing
`offsetof` assertion survive; `kControlAbiVersion` goes from 4 to 5. The accumulation
logic can be a `constexpr` function tested on the host, so no GPU is needed to test it.
**Read from the primary source** for all layout facts.

**What we need from you.** Whether you want us to prepare the change or write it
yourself, given that it touches the shared layout and the ABI version.

## 2. Get the numerator, then decide how to get a denominator

**Priority: second, and the half-day parts should start this week.**

**Why.** No count exists of the accesses the tool skipped, so no accuracy statement with
an error bound can be written, and no comparison across configurations is safe. The full
argument, with the four missing quantities and five candidate routes, is entry
`05-A-accounting-unit-is-the-kernel-launch.md`.

**Effort, by route.** Export the three existing counters from the vLLM adapter: tens of
lines, hours, gives the numerator and no denominator. Vendor performance counters through
Nsight Compute: half a day to find out whether the metric names exist, one to two days to
use them, gives a whole-workload denominator. NVBit read-only census: half a day of smoke
testing against the untested `<= 575.xx` driver limit, then three days to two weeks,
gives the same denominator plus a breakdown of the instruction forms. NVBit coexisting
with HBFSim for a strict address-filtered denominator: 1.5 to 3 weeks with hangs as the
failure mode — **we do not recommend opening this before the deadline**.

**What we need from you.** Permission to add the `hbfsim_get_stats` call to the vLLM
adapter; and the output of `ncu --query-metrics` on the experiment machine, which we
cannot run from here because this machine has no CUDA toolchain.

## 3. Model a hit in the cache buffer, or state the bound

**Priority: third. Small, and the current behaviour has the sign wrong.**

**Why.** Reading the same page a second time is currently charged 11,133 ns while reading
the next page is charged 10,121 ns, so re-reading is modeled as about 10% more expensive
than moving on, while the specification requires a cache-buffer hit to be served
immediately. Entry `06-A-same-page-reread-charged-as-a-new-page.md` has the code line and
the specification quotations.

**Effort.** One line, plus a test, for the change that removes the reversal:
`previous_page + 1 == page || previous_page == page` at
`src/cuda_runtime/device/hbf_device.cuh` line 419. One to two days for a real two-buffer
model, which additionally needs a hit cost we do not have from a source we have read.
Zero for stating the bound and leaving the line alone.

**What we need from you.** Which of the three you want. Our own view, offered as a view:
make the one-line change first and record in the limitations that the size is still not
right. A reversed sign is caught at a glance; a size that is approximate is a modeling
boundary that can be written down.

## 4. Read and write asymmetry — can we implement it, and is it a contribution

This is the item you asked about directly, so it is written at more length. The short
answers first.

**Can we implement it?** Partly, and less than it looks. One constraint is already
satisfied by accident, one is expressible with modest work, three cannot be expressed at
all today because the state they need does not exist anywhere in the fast path.

**Is it a contribution?** **Not as "we implemented the specification's write rules
faithfully" — we would not put the paper's weight on that.** As "the write rules make the
most natural design impossible on HBF, and here is what the forced alternative costs" —
yes, with conditions, and the conditions are experimental.

### 4.1 What the current code can and cannot express

**Read from the primary source** for every code location below.

| Constraint from the specification | Where the code stands |
|---|---|
| Write unit is 4 KiB and 4 KiB aligned | Satisfied structurally, for an unrelated reason: `media_descriptor` at `src/cuda_runtime/device/hbf_device.cuh` lines 274 to 289 rounds every access down to a whole page and sets `bytes` to `range.page_bytes`, and the measured-curve path at `src/cuda_runtime/device/hbf_device.cu` lines 322 to 327 requires 4,096 bytes and page alignment. Reads and writes are treated identically, and nothing distinguishes a store that fills a page from a store that touches four bytes of it. |
| 64-byte commands accumulate into one 4 KiB program | Not expressible, and currently charged wrongly. Item 1 above. |
| Sequential writing within a NAND block, no page skipping | Not expressible: there is no per-block state anywhere in the fast path. `pages_per_block` is parsed into the profile (`include/hbfsim/profile.hpp` line 38) and passed to the detailed simulator (`src/mqsim_adapter/mqsim_online.cpp` line 74), and never reaches the shared control region or the device. The only order-related state is one scalar, `empirical_burst_state` at `src/cuda_runtime/device/hbf_device.cuh` line 71, which packs a page number, a read/write bit and a run length — a global run detector, not a per-block write pointer. |
| A page-0 write triggers an automatic block erase | Not expressible. Erase exists only in the detailed path, as a derived constant; see entry `12-B-erase-latency-is-a-derived-constant.md`. |
| Accumulation timeout and the four write error codes | Not expressible: the completion record has no notion of a device command status. `HbfCompletion` at `src/cuda_runtime/device/hbf_device.cuh` lines 106 to 116 is 64 bytes with a trailing `reserved` field, so the codes `0x2`, `0x4`, `0x5`, `0x6` and `0x7` would fit without changing the size. |
| Granularity and alignment checking in the PTX pass | Not present, and the PTX pass is the wrong place for it. `parse_memory_op` at `src/ptxpass_hbf/ptx_memory_op.cpp` lines 79 to 126 recovers only the access width from the opcode and a base register plus a constant offset from the operands; the address value is not known at compile time. The check belongs in `__hbfsim_resolve` at run time, where the page number is already computed. |
| The capacity mode's write-back | Runs, but performs an operation the device forbids. Entry `04-A-capacity-mode-rewrites-a-page-in-place.md`. |

### 4.2 Has anyone modeled this already

We checked four papers before deciding, and the verification level differs sharply
between them.

- **TileLens** (`docs/ref_article/ju2026-tilelens-two-dimensional-memory-layout.txt`,
  **full text held locally and read line by line**). TileLens puts only read-only weights
  on HBF. Lines 1250 to 1253: `Model weights are read-only during inference, making them
  natural candidates for HBF. On the other hand, activation and KV cache accesses involve
  both reads and writes, and we store these only on HBM.` The paper also states
  `TileLens does not affect endurance`. **No HBF write is modeled.**
- **FlashAccel** (arXiv:2607.10186,
  `docs/ref_article/wang2026-flashaccel-hbf-llm-inference.txt`, **full text held locally
  and read**). The only one of the four that writes a KV cache to HBF, and it handles the
  write constraints at the design level: no flash translation layer, append-only writing,
  and block-isolated allocation. Lines 1073 to 1082: `To match the block-level erase
  semantics of Flash, the storage layer adopts an isolated block allocation policy …
  thereby avoiding write amplification and reducing Flash wear`. **But the evaluation does
  not model those constraints**: lines 1324 to 1331 describe a simulator built on
  LLMCompass, `We extend it with a NAND simulator that models page access latency at plane
  granularity`, and the write analysis in section 7.4 is analytical arithmetic — read
  bandwidth 4.6 TB/s against a write bandwidth peak of 245.8 GB/s, 988 MB written per GPU
  per second taking 3.9 ms, an overhead of 4%, and five-year cumulative writes of
  148,570 TB against an endurance budget of 1,125,000 TBW. The 4 KiB write unit,
  sequential writing within a block, automatic erase and the accumulation timeout appear
  in none of its timing model.
- **H3** (Ha, Kim and Kim, IEEE Computer Architecture Letters 2026, DOI
  `10.1109/LCA.2026.3660969`). Read-only data on HBF, dynamically updated data on HBM, so
  no HBF write is modeled. **Second-hand: we read a vendor research blog summary and a
  search abstract; the paper itself is behind a paywall and we have not read it.**
- **HAVEN** (arXiv:2603.01175). Vector retrieval and reranking, read-dominated; the
  abstract mentions no write granularity, block ordering, erase or write amplification.
  **Second-hand: abstract only.**

One further pointer, **second-hand through TileLens**: its related-work section, lines
1881 to 1889, says that the system-level HBF papers by Son et al., Kyung et al. and Park
et al. `evaluate HBF at the system level, using roofline-style analytical models to assess
serving throughput, rather than a microarchitectural performance model`. The closest of
those to the write question is Kyung et al., IEEE Computer Architecture Letters 2026,
`High-Bandwidth Flash for KV Caches: Endurance and Performance Implications`, which we
have not obtained; TileLens describes it as observing that a KV cache is written once and
read many times, which is what lets it sit on HBF.

**Our reading of that survey: none of the four models the 4 KiB write unit or the
sequential-write-within-a-block rule in a timing model. The gap is real.**

### 4.3 Does the constraint change any conclusion

Yes, but what it changes is whether a design is possible, not the size of a number — and
the two framings are worth very different amounts.

**Write amplification and endurance: we would not put the paper's weight on it.** On the
`cd8p-vmem-p50` profile a block is 256 × 4 KiB = 1 MiB, so changing one 4 KiB page inside
a written block by replaying the block costs 256 × 408,305 = 104,526,080 ns, about
104.5 ms, a write amplification of 256 times. The figure is alarming, but FlashAccel has
already designed the problem away with append-only, block-isolated allocation, and has
already published the endurance arithmetic — 4% overhead, 148,570 TB against
1,125,000 TBW. Recomputing a 4% term more precisely is not a result.

**"This constraint rules out the design a reader would reach for first": this we would put
the paper's weight on.** Our own capacity mode — HBM as a cache, dirty pages written back
to the address they came from — is illegal on HBF, and the legal alternative is a
whole-block replay. That is a yes-or-no conclusion rather than a difference of a few
percent, and it lands on exactly the design most readers assume. Going one step further,
FlashAccel's fix has a cost nobody has measured: with 1 MiB blocks, a request whose KV
cache is smaller than 1 MiB still occupies a whole block, so internal fragmentation and
lost parallelism across planes for small requests are measurable quantities. **What
block-isolated allocation costs is worth more than how large write amplification is,
because the first has not been done and the second has.**

### 4.4 The objection a reviewer will raise, and how far it goes

**This subsection is our own judgement.** Sequential writing within a block, no in-place
update, no device-side garbage collection, wear leveling delegated to the host, and erase
tied to writing — that set of rules is what NVMe Zoned Namespaces already specifies, and
simulators have modeled zoned namespaces for years. **This part is second-hand: we have
not re-read the source trees of those simulators this round.** A FAST reviewer will
recognise the shape immediately, and "we implemented rules of the zoned-namespace kind"
is not a contribution at FAST.

Three things genuinely differ, and only the third carries weight:

1. Erase is implicit — writing to page 0 triggers it, and there is no explicit reset
   command.
2. There is a 64-byte to 4 KiB accumulation window, with a host-configured timeout and
   four dedicated error codes.
3. **The constraint sits behind ordinary memory instructions, not behind a block device
   submission queue.** With zoned namespaces the sequential-write rule is carried by a
   file system or a user-space library. Here the same rule has to be carried by an
   `st.global` inside a GPU kernel, and a kernel has no way to say "I am now writing this
   block in order". That mismatch is the part worth writing about.

### 4.5 What we would and would not claim

- "We faithfully implemented the write rules of the specification" — **we would not stake
  the paper on this.** It transcribes rules of a shape reviewers already know, and its
  effect on the main workloads is a few percent.
- "The write rules make demand paging impossible on HBF, the design is forced into
  append-only writing or whole-block replay, and here is what each costs — plus what
  block-isolated allocation costs in internal fragmentation" — **we would stake the paper
  on this, on the condition that the experiments are actually run and the sizes hold up.**
  It is a design-space result produced by the tool, it is absent from all four papers
  above, and it strikes the design a reader reaches for first.
- "4 KiB accumulation modeling" — **not a contribution, a required repair.** Item 1.

### 4.6 Effort and what we need from you

Building enough of the write path to support the second framing is items 1, 5 and 7 below
taken together: roughly one week of work for the accumulation and ordering model, plus the
capacity mode redesign, which is a design change rather than a fix. The experiment in item
8 sits on top.

**What we need from you.** Whether you agree with the split in section 4.5; whether the
capacity mode is meant to model writes at all; and whether you can obtain the Kyung et al.
and H3 papers, since our statement that neither models HBF writes currently rests on
second-hand material and cannot go into the paper in that state.

## 5. Per-block write ordering, and device command status codes

**Priority: fifth. Small to medium, and item 4's second framing depends on it.**

**Why.** Without per-block state the simulator cannot tell a legal write sequence from an
illegal one, so it cannot report what the constraint costs.

**Effort.** Full per-block write pointers do not belong on the device: on the
`cd8p-vmem-p50` profile, 1,919,850,381,312 bytes divided by 4,096 is 468,713,472 pages,
and at 256 pages per block that is 1,830,912 blocks, so one 32-bit pointer per block is
7.3 MB. The split we suggest is a small open-unit table on the device — per channel, the
currently open block and the last unit written, which catches page skipping and
out-of-order writing within a block — with the complete per-block table held on the host
side in the capacity mode only. Status codes are smaller still: `HbfCompletion` has a
trailing `reserved` field, so `0x2`, `0x4`, `0x5`, `0x6` and `0x7` fit without changing
the record size.

**What we need from you.** Whether per-channel open-block state is enough for what you
have in mind, or whether you want the full table.

## 6. Automatic erase on a page-0 write

**Priority: sixth, and blocked on a measurement rather than on code.**

**Why.** Section 5.4.1 item 5 of the specification makes erase implicit in writing, so any
workload that opens a block pays for one.

**Effort.** The code is a few lines: when the unit index within a block is zero, settle an
erase and reset the write pointer. **The blocker is that the only erase figure in the
project is `program_latency_ns * 10` = 4,083,050 ns, which has no measurement behind it —
entry `12-B-erase-latency-is-a-derived-constant.md`.** Writing the code without a number
produces a model with an invented constant in its largest write-side term.

**What we need from you.** A source for the erase time, or agreement to expose it as a
swept parameter with the range stated in the paper.

## 7. Redesign the capacity mode's write path

**Priority: seventh by order, but it is the largest single piece of work here.**

**Why.** The capacity mode writes an evicted page back to its own address, which the
device forbids. Entry `04-A-capacity-mode-rewrites-a-page-in-place.md` gives the two ways
out — append-only writing with a page directory, or honest whole-block replay — and what
each costs.

**Effort.** Not a parameter change. Append-only writing means a page directory, free-block
accounting, and a decision about who reclaims blocks, since the device performs no garbage
collection. We would not start it without your agreement on the direction.

**What we need from you.** Which of the two routes, and whether this belongs before the
deadline at all.

## 8. Measure what block-isolated allocation costs

**Priority: eighth, and it is the experiment that would carry item 4's contribution.**

**Why.** FlashAccel avoids write amplification by giving each request's KV cache its own
blocks. With 1 MiB blocks, a request smaller than 1 MiB still occupies a whole block. The
internal fragmentation and the loss of parallelism across planes for small requests are
measurable with the tool we have, and no published work has measured them.

**What number has to come out.** For a distribution of request sizes, the share of
allocated block capacity that holds no data, and the resulting change in effective write
bandwidth against an allocation policy that packs several requests into one block.

**Effort.** Depends on items 1 and 5 being in place first. The experiment itself is a
sweep, comparable in size to item 20 of `15-experiments-we-must-add-before-submission.md`
(item 14 before that document was renumbered).

**What we need from you.** Whether the request-size distribution should come from the
vLLM run we already have or from a synthetic distribution stated in the paper.

---

## 9. How these eight sit against the sixteen experiments already agreed

`15-experiments-we-must-add-before-submission.md` holds twenty-three items ordered by which contribution claim each
supports, with three of them blocking: item 1 for the temperature claim, item 3 for the
coverage claim, item 7 for the timing-model claim. Nothing here replaces that ordering.
Two things change.

**First, three of the new items should run before further experiments, because they change
the numbers those experiments would produce.**

| Order | Item | Reason for the position |
|---|---|---|
| 1 | New item 1, charge a write once per 4 KiB unit | Any experiment involving writes produces a number 8 to 32 times too large until this is done |
| 2 | New item 2, route 0, export the counters | Hours of work, and existing item 3 has no numerator without it |
| 3 | New item 2, route 3, half a day of `ncu --query-metrics` | Decides whether a denominator exists at all; existing item 3 depends on the answer |
| 4 | New item 3, the one-line change for same-page reads | Existing item 14 sweeps access patterns including patterns with reuse, and the reuse case currently has the wrong sign |
| 5 | Existing item 7, held-out calibration validation | Unaffected by the repairs above and can run in parallel |
| 6 | Existing item 1, temperature into a service rate | The largest missing mechanism, and independent of everything above |
| 7 | Existing item 8, split the 164.70x figure | Also needs the profile question in entry `02-A-headline-164x-came-from-a-100x-time-scale.md` answered |
| 8 | New items 5 and 6, ordering state and automatic erase | Groundwork for the write-side contribution |
| 9 | Existing item 11, re-anchor the profiles | Now has corrected specification numbers to anchor to; see below |
| 10 | New items 7 and 8, capacity mode and block isolation | Only if the direction in section 4.5 is agreed and time remains |

**Second, three of the sixteen need small corrections from what we read in the
specification.**

- **Existing item 11** re-anchors the profiles to "approximately 0.4 to 3.0 TB/s per
  stack, with 512 GB per stack". The specification's Table 4 on page 16 gives
  0.384 / 1.536 / 3.072 TB/s, and capacity is 512 GiB or higher, with
  `GiB = 2^30 bytes and GB = 10^9 bytes` defined on page 14. Page 15 of the specification
  writes the top figure as TiB/s, which is a disagreement inside the specification itself
  and should be noted rather than resolved silently. Details in
  `18-spec-conformance-findings.md`, finding 6.
- **Existing item 6**, on the size of the overestimate from cache hits, overlaps new item
  3 and entry `13-C-reference-path-has-no-cache-read-either.md`. The specification's
  cache-buffer clauses give item 6 a concrete target it did not have: two 4 KiB buffers per
  bank, hits served immediately.
- **Existing item 3** should state its result in units of launches unless a denominator
  route succeeds. The reasoning is in entry `05-A-accounting-unit-is-the-kernel-launch.md`,
  question three.

Everything else in `15-experiments-we-must-add-before-submission.md` stands as written, including item 17
(item 13 before that document was renumbered),
which is blocked on hardware not in hand, and item 16, which has already been moved to
future work.

---

Please tell us which of the points here you agree with, which ones you think we have read
wrong, and which ones you intend to change.


