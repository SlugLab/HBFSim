# Proposal: die density/count/interleaving sweep, and SLC–QLC mixing

Raised in discussion with 胡学长, 2026-08-16. Two ideas, at different levels of
readiness. Written in the same terms as `todo/experiments-to-run.md` and
`todo/facts-to-verify.md`: what to run, what it needs, what is already true of
the code today, and what would have to be decided before more code is written.

**Update, 2026-08-16, same day.** Both ideas are now implemented and produce
numbers: `--pattern random`/`--seed` in `hbf_mqsim_bench`, the
`nand_technology`/`plane_allocation_scheme`/per-level-latency profile fields,
the QLC patch to MQSim, and a first sweep across all three axes. Full
methodology, results, and — important — the honest limitations of that first
run (single workload shape, unmeasured QLC constants, an MLC/TLC confound, no
mechanism traced for the density result) are in
`docs/proofs/2026-08-16-density-interleave-nand-sweep.md`. The rest of this
file is kept as the original feasibility analysis; nothing below has been
removed or found wrong, but item 1's "what is missing" list and item 2's
effort levels are now partly done rather than open.

## 1. Die density / die count / address interleaving, capacity held constant

**The claim under test.** At the same total capacity (e.g. 2 TB), swapping fewer
large-capacity dies for more small-capacity dies changes die/bank parallelism,
channel load balance, and conflict probability, and should show up most clearly
in random-access P99 latency at high queue depth. The spec gives 16 channels,
per-die/bank structure, and 64 B–4 KiB address interleaving options
(`docs/HBF_OCP/ocp2026-hbf-architecture-specification-v0-7-0.txt`, Figure 5 /
Figure 42, section 13), so this is a real design axis in the spec, not one we
invented.

**What is already supported, with no code change.** `capacity_bytes` in
`configs/schema/hbf-profile.schema.json` is not an independent field in the
timing model — `hbfsim::blocks_per_plane` (`src/profile/profile.cpp`) derives
`blocks_per_plane` from `capacity_bytes / (page_bytes * channels *
dies_per_channel * planes_per_die * pages_per_block)`, and rejects the profile
if that is not exact. So holding `capacity_bytes`, `channels`, `page_bytes`,
`planes_per_die`, `pages_per_block` fixed and only changing `dies_per_channel`
already gives a "same total capacity, different die count/density" sweep for
free. Seven such profiles now exist at
`configs/profiles/density-sweep/hbf-density-dpc{02,04,08,16,32,64,128}.json`,
spanning 32 GiB/die (32 dies) to 512 MiB/die (2048 dies) at a fixed 1 TiB and 16
channels; see `configs/profiles/density-sweep/README.md`.

**What is missing before this becomes the P99 experiment the idea asks for.**

1. **Address interleaving granularity is not a profile parameter.**
   `src/mqsim_adapter/mqsim_online.cpp` sets a single fixed
   `Device_Parameter_Set::Address_Mapping = PAGE_LEVEL`; there is no field
   anywhere for the 64 B / 256 B / 4 KiB host-side AXI interleaving choice the
   spec describes (Figure 5). Comparing interleaving granularities, as the idea
   asks, needs: (a) a new profile field, (b) it threaded through to whichever
   layer computes which channel/die a byte range lands on, and (c) a decision
   on whether MQSim's own `PAGE_LEVEL` FTL mapping is a reasonable stand-in for
   the spec's host-side interleaving or a different mechanism entirely — these
   are two different things in the spec (FTL striping across dies vs. AXI port
   interleaving across channels) and the current code only has the former.
2. **No random-access, high-queue-depth CLI path exists yet in the MQSim
   benchmark.** `benchmarks/mqsim/hbf_mqsim_bench.cpp` only issues addresses in
   a fixed round-robin stride (`(index * bytes) % (address_slots * bytes)`)
   with a constant `--arrival-gap-ns`; there is no `--pattern random` and no
   explicit queue-depth control (`profile.queue_depth` is passed to MQSim, but
   nothing in the benchmark drives concurrent in-flight requests beyond the
   arrival gap). The CUDA microbench
   (`benchmarks/cuda/hbf_microbench.cu`) already has a `random` /
   `pointer_chase` pattern and P50/P99-capable modes, but it goes through the
   GPU/hybrid path, not the plain MQSim media path, so it is not a clean
   channel/die-parallelism-only measurement either.
3. **P99 is computed by the C++ benchmarks already** (`percentile()` in
   `benchmarks/mqsim/hbf_mqsim_bench.cpp` and
   `src/hybrid/calibrator.cpp`), so no new statistics code is needed once (1)
   and (2) exist.

**Effort, if this is worth doing before the deadline.** The density-only sweep
(no interleaving) can be run today. Adding a `--pattern random` and an explicit
in-flight-request-count option to `hbf_mqsim_bench.cpp` is small, on the order
of the existing `--arrival-gap-ns` option. Adding an address-interleaving-
granularity profile field and wiring it into the mapping decision is the larger
piece and touches the schema and `mqsim_online.cpp`; it should not be started
without agreement, per the same reasoning as the profile-schema changes in
`docs/重要实现问题以及需补做实验/17-contributions-and-experiments-we-could-add.md`.

**Where this could land relative to the existing plan.** This is a variant of
item 6 in `todo/experiments-to-run.md` ("Access pattern and HBF parameter
sweep") — same access patterns and profiles idea, but sweeping die
geometry instead of (or in addition to) the three named timing profiles.

**What we need from you.** (a) Whether the density-only sweep (already runnable)
is worth a table/figure on its own, without interleaving. (b) Whether to build
the random-access + queue-depth option in `hbf_mqsim_bench`, or reuse the CUDA
microbench's `random` pattern instead. (c) Whether address-interleaving
granularity is worth modeling as a real parameter before the deadline, given it
is the larger of the two pieces of missing code.

## 2. NAND type selection: SLC through QLC, and mixed combinations

**The idea.** Sweep NAND cell type (SLC/MLC/TLC/QLC) as a variable, including
combinations within one device, to see where QLC's density-per-cost advantage
stops being worth its latency/endurance cost for this workload class — i.e. the
idea ends at "the QLC price benefit is not significant enough for it to be worth
it here," which is itself a testable negative result if the model supports it.

**What is already true of the code.** `Flash_Parameter_Set::Flash_Technology`
is set unconditionally to `Flash_Technology_Type::SLC` in
`src/mqsim_adapter/mqsim_online.cpp`, regardless of profile. There is no NAND
cell-type field anywhere in `include/hbfsim/profile.hpp` or
`configs/schema/hbf-profile.schema.json`. A profile's `read_latency_ns` /
`program_latency_ns` already let you *approximate* a slower cell type as a
single flat scalar for the whole device (this is exactly how `conservative.json`
vs `aggressive.json` differ today), but:

- there is no way to express **more than one technology in the same device** —
  the whole profile is one read/program latency pair, so an SLC-cache +
  QLC-capacity combination (a real, common SSD design) cannot be expressed at
  all;
- a flat latency scalar cannot express **why** QLC is slower — the LSB/CSB/MSB
  per-page latency split that `Flash_Parameter_Set` already has fields for
  (`Page_Read_Latency_LSB/CSB/MSB`, currently all set to the same
  `profile.read_latency_ns`) is exactly where bits-per-cell enters MQSim's own
  model, and it is being thrown away by setting all three fields equal;
- there is no endurance/cost model at all, so "is the QLC price benefit
  significant" cannot be answered by the simulator itself — it would need an
  external cost-per-GB and P/E-cycle-budget assumption fed in from outside, the
  same way `docs/重要实现问题以及需补做实验/17-...md` item 4 talks about
  endurance for the write-path proposal.

**Effort, in increasing order.**

1. Smallest: use the existing flat scalar to make one more named profile per
   cell type (e.g. `qlc-nominal.json` with a higher `read_latency_ns` /
   `program_latency_ns`), sourced from a citable per-cell-type latency number.
   This needs a citation, not code — see the discipline in
   `todo/facts-to-verify.md` about not inventing constants.
2. Medium: expose `Page_Read/Program_Latency_{LSB,CSB,MSB}` as three profile
   fields instead of one, so a single profile can express one real cell type's
   asymmetric page latencies (SLC has only LSB; QLC has all of
   LSB/CSB/MSB/... and they differ). Schema and `profile.cpp` change, no
   simulator redesign.
3. Largest, and the part that actually lets you "combine" types: per-die or
   per-region technology, e.g. some channels/dies configured as SLC and others
   as QLC in the same profile (an SLC-cache-over-QLC-capacity layout). MQSim's
   `Flash_Parameter_Set` is currently populated once per whole device in
   `configure_mqsim()`; making it heterogeneous per die/channel is a real
   simulator change, not a config change, and is comparable in size to the
   write-path items in
   `docs/重要实现问题以及需补做实验/17-contributions-and-experiments-we-could-add.md`.
4. Not code at all: an endurance/cost side calculation (cost per GB, P/E cycles,
   write amplification from item 1 of that same 17-....md file) to actually
   answer "is the QLC price benefit worth it" — the simulator can report
   latency/throughput cost, but "worth it" needs a cost number from outside the
   tool, and per the project's own standard, that number needs a citable source
   before it goes in the paper, not an invented one.

**What we need from you.** Whether this is worth pursuing at all before the
deadline, given `todo/experiments-to-run.md` already lists 8 required items;
if yes, which of the four effort levels above is the target, since 1 needs a
citation and nothing else, while 3 is a simulator redesign.
