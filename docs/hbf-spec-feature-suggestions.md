# HBF Specification Feature Suggestions


Status: proposal. Nothing here is implemented; nothing here is a measurement.

## 1. Purpose and sources

This document proposes eight simulator features derived from the first HBF
specification, plus one completed description of the thermal model that the
paper treats as its core design. It is written for the HBFSim author to accept,
reject, or reorder before the FAST '27 fall deadline (Tuesday, September 15,
2026, 23:59 AoE; 12 pages excluding references; double-blind).

The specification details below come from two files now in `docs/ref_article/`:

- `semiinsights2026-hbf-standard-release-cn.pdf` and its companion
  `semiinsights2026-hbf-standard-release-cn.txt` -- a Chinese-language
  walk-through of the specification, which it identifies as version v0.7.0.
- `hbf2026-five-questions-answered-cn.md` -- an opinion article of unknown
  authorship that lists five public criticisms of HBF and answers each with a
  specification mechanism. Used here as a list of questions reviewers will ask,
  not as a normative source.

Both are second-hand. As `docs/ref_article/README.md` records, the normative OCP
specification PDF is not in the repository and could not be fetched. Every
mechanism attributed to the specification below should be re-checked against the
normative text before it appears in the paper. Statements marked as proposals
are the author's design suggestions and carry no specification authority.

## 2. The thermal core, written out in full

The collaborator's stated view is that the essence of HBFSim's thermal work is
an MTBF thermal model. This section expands that hint into something
implementable. Everything in it is a proposal except where a specification
number is quoted.

MTBF (mean time between failures) is the average time a device runs before it
fails; numerically it is the reciprocal of the failure rate. The standard way to
make a failure rate depend on temperature is the Arrhenius equation: the rate of
a temperature-driven failure mechanism is proportional to `exp(-Ea / kT)`, where
`Ea` is the activation energy of that mechanism in electron-volts (how much
energy a single event of the mechanism needs, so a larger `Ea` means the
mechanism is more strongly suppressed at low temperature), `k` is Boltzmann's
constant, and `T` is absolute junction temperature -- the temperature of the
silicon die itself, not of the package surface or the surrounding air. Comparing
two temperatures gives an acceleration factor, the ratio of failure rates:

```text
AF = exp[ (Ea / k) * (1/T_use - 1/T_stress) ]
```

So a lifetime measured at one temperature can be converted into a lifetime at
another. That conversion is the whole model.

### 2.1 The anchor point the specification supplies

From the specification write-up: operating junction temperature is 0 degrees C
to 105 degrees C, and powered data retention is guaranteed for 24 hours at
85 degrees C. Retention here means how long a NAND cell holds its stored charge
well enough to be read back correctly; when retention runs out the host must
refresh the data, that is, read it back and write it again so the charge is
restored.

That single guaranteed pair -- 24 hours, 85 degrees C -- is the anchor the
Arrhenius conversion needs. Given a simulated junction-temperature trajectory
for a zone, the acceleration factor converts the anchor into a retention
deadline for that zone at its own temperature history.

Two supporting numbers from outside the specification, found by web search and
**not yet verified page-by-page against the primary documents**, so treat them
as provisional: activation energies reported for NAND charge-retention loss fall
around 1.05 to 1.2 eV, and the relevant qualification standards are JEDEC
JESD47 and JESD22-A117/A108. JEDEC's client-SSD requirement is one year of
retention at 30 degrees C with an uncorrectable bit error rate (the fraction of
read bits that come back wrong and cannot be repaired by error-correcting code)
at or below 1e-15. Those give a sanity range for `Ea` and a familiar reference
point for reviewers.

### 2.2 What the model produces

Proposal. The model has two outputs, and both are fed back into the timing
simulation rather than reported on the side:

1. A failure rate, and therefore an MTBF estimate, as a function of the
   workload's own thermal history -- not of a nameplate temperature.
2. The refresh traffic required to keep data alive. Every zone whose retention
   deadline approaches must be read and rewritten. Those rewrites are ordinary
   writes: they occupy bandwidth, they queue behind and ahead of application
   requests, and each one consumes a P/E cycle -- one program-and-erase of a
   NAND block, of which any block tolerates only a bounded number before it
   wears out.

The point of the second output is that temperature stops being a background
condition of the experiment and becomes an input that generates real traffic
with a measurable cost in bandwidth and tail latency. A hotter trajectory
shortens retention deadlines, which raises refresh frequency, which raises both
the write count charged against endurance and the queueing delay seen by the
application. That feedback path is what makes the thermal model part of the
system design instead of a reliability appendix.

### 2.3 The substrate that already exists

HBFSim does not start from zero here. The repository already holds a first-order
thermal response fitted from live measurement, with time constants of 13.1 s for
the GPU and 12.4 s for the Dell CD8P, calibrated in
`configs/thermal/gpu-cd8p-logp-live.json`. It also holds a measured throttling
observation on real hardware: BF16 GEMM throughput fell from 379.117 TFLOP/s to
348.427 TFLOP/s, a change of -8.10%, with the sampled SM clock dropping from
about 2,062 MHz to about 1,642 MHz, and the driver's cumulative
`SW Thermal Slowdown` counter going from 0 to 8,279,678 us.

So the temperature trajectory generator exists and is calibrated. What sections
2.1 and 2.2 add is the conversion from that trajectory to retention deadlines,
refresh traffic, and a failure rate.

### 2.4 What this does not prove

The Arrhenius conversion is a model, and its constants are not measured on HBF
silicon by anyone in this project. It cannot establish an absolute lifetime for
a real HBF stack. What it can support is a comparison: given the same anchor and
the same `Ea`, two workloads or two placement policies produce different thermal
histories and therefore different refresh loads and different relative MTBF. The
paper should state the sensitivity of its conclusions to `Ea` across the 1.05 to
1.2 eV range rather than pick one value silently.

## 3. Feature proposals

Each proposal below names the public criticism it answers. The five criticisms
are those listed in `docs/ref_article/hbf2026-five-questions-answered-cn.md`:

1. Thermal stress inside the package: does GPU heat destroy NAND retention, bit
   error rate, and lifetime?
2. Limited P/E endurance against memory-class write rates such as KV cache and
   multi-tenant serving: does HBF wear out in days, and does a failed HBF stack
   take the co-packaged GPU with it?
3. Read latency around 10 microseconds and 4KiB write granularity: can HBF serve
   sparse or irregular access at all?
4. Refactoring cost and packaging premium against the per-GB cost advantage.
5. Ecosystem risk: is HBF read-only capacity, or a general-purpose tier?

That article answers each with a specification mechanism, and the eight
proposals here are simulator features for exactly those mechanisms. The value of
building them is that the paper can then report numbers on the questions
reviewers will already have in mind, instead of arguing about them in prose.

### F1. The four thermal modes as a simulator state machine

**Spec basis.** The write-up describes four thermal modes. Normal is
unrestricted operation. Light throttling begins when temperature crosses the LTT
threshold and triggers DFS (dynamic frequency scaling -- lowering the operating
clock so the device produces less heat). Severe throttling begins at the STT
threshold, and the device then asserts the CATTRIP pin (a hardware signal line
that tells the host the part has reached a catastrophic over-temperature
condition), deasserts AXI Ready so no new command is accepted, stops issuing
credits, and lets commands already in flight either finish or return command
status error code 0x9. Shutdown is reported as a link error. RTT is the release
threshold used while cooling down: the temperature at which the device leaves a
throttling mode is lower than the one at which it entered, which is hysteresis
-- deliberately different up and down thresholds so the device does not switch
modes back and forth around a single point. All three thresholds live at MMIO
address 0x150.

**Why it strengthens the paper.** It answers criticism 1 directly, and it is the
mechanism that turns the thermal model of section 2 into observable performance
behaviour rather than a reliability number.

**Implementation sketch.** Drive the state machine from the fitted first-order
thermal response already in `configs/thermal/`. Carry LTT, STT and RTT in the
control ABI (currently v4), whose shared header is the established place for
device-visible state -- it already carries profile parameters, six
empirical-curve breakpoints, and a burst state word. Light throttling scales the
modeled service rate. Severe throttling maps onto machinery that already exists:
stop issuing ring credits, which puts the existing backpressure path into effect
-- when the consumer stops accepting work the producer waits with bounded
backoff and eventually times out, rather than losing requests -- and drain
in-flight requests. Add one request status alongside `PENDING`, `READY`,
`IO_ERROR`, `COPY_ERROR`, `CHECKSUM_ERROR`, `TIMEOUT`, `UNSUPPORTED` and
`DAEMON_LOST` to mirror status 0x9. `scripts/thermal/simulate_overheat.py`
already produces a profile that crosses the configured GPU and SSD warning
points at 30 s and 42 s without driving real hardware there, so a test input
exists.

**Estimated effort: small to medium.** The thermal fit, the backpressure path,
and the status enumeration all exist; the new code is the threshold comparison,
the hysteresis bookkeeping, and one status value.

### F2. The retention-and-refresh loop -- the MTBF model of section 2

**Spec basis.** Powered data retention is guaranteed for 24 hours at 85 degrees
C. There is no on-die garbage collection, so the host must periodically
re-verify and rewrite data; the five-questions article states a patrol cycle of
24 to 48 hours. The base die keeps per-block page-read counters that signal
refresh status. Read disturb -- the gradual corruption of neighbouring cells
caused by repeatedly reading a block -- raises a correctable-error warning,
CECC, Status 0x5; an uncorrectable error is UECC, Status 0x4. Recovery from a
failed refresh is host replay of the write sequence for the whole NAND block.
The host may divide each channel's capacity into equal-size zones and read each
zone's PEC (program/erase cycle count) from CSR registers, and the wear
registers MAXPEC and AVGPEC are exposed through MMIO.

**Why it strengthens the paper.** This is where criticism 1 gets a number
instead of an argument, and it feeds criticism 2: refresh writes are writes, so
they raise write amplification -- the ratio of bytes actually programmed into
the media to the bytes the application asked to write -- and consume the same
bounded P/E budget the application is competing for.

**Implementation sketch.** Give each zone a temperature history from the F1
state machine and a retention deadline computed from the 24h at 85 degrees C
anchor by the acceleration factor of section 2. When a deadline approaches,
inject the refresh as ordinary media work through the host service dispatcher,
which already batches ring descriptors into `MqsimOnlineEngine`; the internal
read-then-program pair is the same shape as the existing capacity media plan,
which already submits a victim program followed by a dependent read with
collision-free internal request IDs. Keep per-zone PEC counters, incremented by
both application writes and refresh writes, and expose maximum and average
through the control ABI so an experiment can read them the way a host would read
MAXPEC and AVGPEC. Report as run outputs: the MTBF estimate, the fraction of
media bandwidth consumed by refresh, and the effect on p99 request latency.

**Estimated effort: medium.** It adds a background traffic generator and
per-zone bookkeeping, but no new transport, no new shared-memory layout beyond
counters, and no change to how requests reach the media model.

### F3. Scratchpad SRAM as a third range mode

**Spec basis.** Optional and product-dependent: each UCIe channel may carry an
independent SRAM region. It is a scratchpad -- a small fast memory that software
places data into and evicts by hand, unlike a hardware cache that fills itself.
Granularity is 64B, latency is a few clock cycles, requests to it are
high-priority, it consumes no P/E cycles, and its contents are lost on a power
cycle. The specification names intermediate activations and inter-layer buffers
as the intended contents, and states that host software manages it entirely.

**Why it strengthens the paper.** It is the specification's own answer to
criticisms 2 and 3: the traffic that is too small-grained for 4KiB writes and
too write-heavy for NAND endurance is meant to land in SRAM, not in flash. A
simulator that cannot express that placement cannot evaluate the design as
specified.

**Implementation sketch.** The range table already keeps up to 64 sorted ranges
with a per-range mode (`timing=1`, `capacity=2`). Add a third mode that the
resolver dispatches to ordinary HBM with a small fixed modeled latency and 64B
access granularity, charges no P/E cycles, and drops its contents at context
teardown to match loss on power cycle. Experiments can then steer KV-cache and
activation traffic into it and measure what moves.

**Estimated effort: small.** Range modes, the sorted range table, and resolver
dispatch all exist; this is a new mode value and a short latency path.

### F4. Channel partitioning and host-driven wear leveling

**Spec basis.** One stack has 16 UCIe channels. Each channel has its own NAND
dies and its own clocking and cannot reach another channel's data. The host may
divide each channel's capacity into equal-size zones and read each zone's PEC
from CSR registers. Zone Remapping, command 0x08, changes which physical region
a logical zone refers to without moving any data. There is no on-die garbage
collection, so wear leveling -- spreading writes so no one region reaches its
P/E limit far ahead of the rest -- is the host's job. The five-questions article
gives a concrete split as an example: 12 channels holding weights and 4 channels
holding KV cache.

**Why it strengthens the paper.** It is the direct answer to criticism 2. With
per-zone PEC and remapping in the simulator, the paper can report how long a
given partition survives a given serving workload, and what the partition costs
in performance, instead of asserting that host wear leveling is sufficient.

**Implementation sketch.** Model the 16 channels as independent media targets in
the MQSim adapter, which already does media-only online submission, and give
each its own queue so cross-channel independence shows up in contention. Reuse
the per-zone PEC counters from F2. Add a host-issued remap operation through the
control ABI that exchanges the physical mapping behind two zones with no data
movement and no P/E charge. The headline experiment is the weights-versus-KV
split, for example 12 plus 4, reported as lifetime against throughput.

**Estimated effort: medium.** The per-channel modeling is the real work; the
remap operation itself is a mapping-table swap.

### F5. Bank-level page buffers

**Spec basis.** Each bank has 2 cache buffers, each holding at least two pages
(8KiB). A read that hits one of them returns without touching NAND at all. The
specification recommends fully reading the first 4KiB page before issuing a
third 4KiB address, because that third address would evict the buffer the
first read still needs.

**Why it strengthens the paper.** This is the mechanism that makes criticism 3
answerable. The 10-microsecond figure is the NAND array access; a workload whose
64B reads fall inside an already-open page pays a far smaller cost. Without
these buffers the simulator charges array latency for every access and will
report that sparse attention is impossible, which is a property of the model,
not of the device.

**Implementation sketch.** Put a per-bank two-entry, two-page buffer in front of
the media model at the page directory and HBM cache lookup layer, which already
performs deterministic clock eviction and already distinguishes a hit that needs
no media action from a miss that does. A hit returns at a small modeled latency
with no MQSim submission; a miss allocates and, if needed, evicts. Report the
hit rate as a run statistic so a reviewer can see whether a result depends on it.

**Estimated effort: small.** The lookup layer with its hit-or-media-action
decision is the natural insertion point and needs no new interfaces.

### F6. 64B reads with multiple AXI IDs and out-of-order completion

**Spec basis.** The protocol is AXI over UCIe with a latency-optimized 256-byte
Flit, format 6. Reads are addressed at 64-byte granularity, so reading one 4KiB
page means issuing 64 read commands carrying different AXI IDs, or a burst of
length 0-63. An AXI ID is a tag on a transaction: responses carrying the same
tag must come back in order, while responses carrying different tags may come
back in any order. The write side is the opposite shape: writes are 4KiB, the
base die accumulates a full 4KiB before programming the core die, local
addresses within a NAND block must be written in sequence, the device returns an
error if it does not receive all of a 4KiB write within a host-configured time
limit, and a channel allows 64 or 128 outstanding write requests depending on
the product.

**Why it strengthens the paper.** It answers criticism 3 at the level of the
protocol rather than the media. Whether irregular access is viable depends on
how many 64B reads a host can keep in flight per channel and how their
completions interleave, and that cannot be shown by a page-granularity model.

**Implementation sketch.** Device requests today are page-granularity with warp
coalescing. This proposal splits them into 64B sub-requests carrying distinct
IDs, allows completions to arrive out of order while keeping same-ID order,
enforces a per-channel outstanding limit, and interleaves across channels. It
touches the device helper that converts an address to a media page, the ring
descriptor format, and the MQSim address mapping.

**Estimated effort: medium to large.** Three layers change at once, including
the shared-memory descriptor format, which is the part with the most existing
invariants to preserve.

### F7. Degraded-capacity operation after a partial failure (REDCAP)

**Spec basis.** REDCAP, command 0x0A, reads a 1024-bit failure bitmap at MMIO
address 0x14C. The name is short for reduced capacity: the device reports which
regions have failed, the host permanently fences those regions off, and the part
keeps running with less usable capacity rather than being declared dead.

**Why it strengthens the paper.** It answers the second half of criticism 2 --
whether a failing HBF stack takes the co-packaged GPU down with it. If the
simulator can lose a zone or a channel mid-run and show the workload continuing
at reduced capacity, that question has an experimental answer.

**Implementation sketch.** The fault-injection matrix already covers daemon
death, ring capacity exhaustion, short read, checksum mismatch, timeout, and
unsupported access. Add zone-level and channel-level failure as further entries:
mark the regions in a failure bitmap, shrink the usable capacity the range table
will allocate from, and let the run continue. The check that matters is that the
launch gate stays fail-closed throughout -- that is, when it cannot prove a
launch's pointer coverage it refuses the launch instead of running it with
unmodeled memory. Its per-launch decisions are already logged and countable; the
existing real run recorded 23,210 decisions of which 10,584 were
`opaque_unmodeled_timing`, so the same accounting can show that fencing a region
did not silently weaken the gate.

**Estimated effort: small.** It is one more axis in an injection matrix that
already exists, plus a capacity accounting change.

### F8. Re-anchor the profiles to the specification's bandwidth grades

**Spec basis.** The write-up gives three bandwidth grades, from roughly 0.4 TB/s
to 3.0 TB/s per stack. One stack has 16 UCIe channels, and each UCIe x64 module
delivers 256 GB/s at 32 GT/s. On capacity the same source says both "512 GiB or
more per stack" and "up to 512GB per stack in 8-high and 16-high NAND
configurations"; both wordings are reproduced here unchanged, and the
discrepancy is one more reason to check the normative PDF.

**Why it strengthens the paper.** The current `aggressive` profile in
`configs/profiles/` caps at 1 TB/s and 1 TiB, which is below the top grade and
above the stated per-stack capacity. Results produced under it are results for a
device the specification does not describe. Criticism 4, on packaging premium
against per-GB cost, cannot be discussed at all without stating which grade the
numbers assume.

**Implementation sketch.** Add specification-derived profiles beside the
existing `conservative`, `nominal`, `aggressive` and measured `cd8p-vmem-p50`,
built bottom-up from 16 channels at 256 GB/s each rather than from a chosen
aggregate. Keep the existing synthetic profiles; do not overwrite them, since
past results reference them.

**Estimated effort: very small.** New JSON files and a note in the profile
documentation.

## 4. Recommended order, and what to defer

| Feature | Effort | Criticism answered | Recommendation |
|---|---|---|---|
| F1 four thermal modes | small to medium | 1 | First |
| F2 retention and refresh | medium | 1, 2 | First |
| F3 scratchpad SRAM mode | small | 2, 3 | Early, cheap |
| F5 bank page buffers | small | 3 | Early, cheap |
| F7 REDCAP degraded capacity | small | 2 | Early, cheap |
| F8 spec-derived profiles | very small | 4 | Do it now |
| F4 channel partitioning and wear | medium | 2 | After the above |
| F6 64B out-of-order reads | medium to large | 3 | Defer if time is short |

F1 and F2 together are the thermal model the paper is built on, so they should
land first even though F8 is cheaper. F3, F5 and F7 are each small and each
removes a specific objection, which makes them good value per day of work. F4 is
worth doing if the schedule holds. F6 is the one to drop first: it improves
protocol fidelity but the questions it answers can be approached, less
precisely, with F5 alone, and it is the only proposal that changes the
shared-memory descriptor format this close to the September 15 deadline.

One framing note. If these land, the paper can describe HBFSim as an emulator
that reproduces the interface mechanisms published in the HBF specification --
meaning it implements the specification's own thermal modes, refresh
requirement, wear accounting and buffering rather than a generic model of flash
behind a cache. That is a stronger and more checkable claim than calling it a
flash-tier emulator, but it is only true for the mechanisms actually
implemented, and the claim should name them.

The prerequisite for all of it is obtaining the normative OCP PDF. Everything
above rests on a second-hand rendering, and a paper that cites a WeChat article
for register addresses will be asked about it.

## 5. Two naming cleanups, unrelated to the features

Neither affects behaviour; both affect how a reviewer reads the repository.

**The `logp` name.** `configs/thermal/gpu-cd8p-logp-live.json` and
`scripts/thermal/fit_logp.py` use "LogP", which is an established name for an
unrelated parallel-computation model. It also does not come from the cited
paper: Ardestani et al., ISLPED '12, DOI `10.1145/2333660.2333670`, names its
methods TASS and TAPS and never uses "LogP". A neutral replacement such as
"first-order thermal fit" would describe what the file actually contains.

**The old ACM filename.** Two places still refer to that paper as
`2333660.2333670.pdf`: `docs/proofs/2026-08-11-hybrid-complete.md` line 79 and
`scripts/thermal/collect.py` line 74. The PDF now lives at
`docs/ref_article/ardestani2012-thermal-aware-sampling.pdf` with a full citation
in `docs/ref_article/README.md`, so both references can be updated.

F8 above is a third item of the same kind -- the profile names promise a device
grade the specification does not define -- but it changes what experiments
measure, so it is listed as a feature rather than a cleanup.

