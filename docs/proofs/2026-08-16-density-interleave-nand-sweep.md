# Die density/count, address-interleaving order, and NAND cell-type sweeps

Date: 2026-08-16 UTC

## Outcome

Three new sensitivity axes are now wired end to end through
`hbf_mqsim_bench` and MQSim's HBF media-only path, all at a fixed 1 TiB
capacity and 16 channels (the OCP HBF spec's channel count):

1. **Die density/count** (`configs/profiles/density-sweep/`): holding
   capacity, channels, page size, planes/die, and pages/block fixed and
   sweeping `dies_per_channel` from 2 to 128 (32 to 2048 total dies, 32 GiB
   down to 512 MiB per die).
2. **Address-interleaving order** (`configs/profiles/interleave-sweep/`):
   MQSim's `Plane_Allocation_Scheme` (CWDP/WCDP/DWCP/PDWC), now exposed as the
   profile field `plane_allocation_scheme`, used here as the available proxy
   for the spec's host-side channel interleaving granularity — see
   "What this is and is not" below.
3. **NAND cell type** (`configs/profiles/nand-sweep/`): SLC/MLC/TLC/QLC, via
   the new `nand_technology` and per-level `page_read_latency_{lsb,csb,msb,tsb}_ns`
   / `page_program_latency_{lsb,csb,msb,tsb}_ns` profile fields. QLC required
   patching MQSim itself (`patches/mqsim/0002-qlc-support.patch`); stock MQSim
   only ships SLC/MLC/TLC.

This closes the "can the infrastructure express these three axes and produce
a number" question. It does not close "is the magnitude of any of these
effects a number worth quoting in the paper" — see the limitations at the end.

## What changed in code

- `include/hbfsim/profile.hpp`, `src/profile/profile.cpp`,
  `configs/schema/hbf-profile.schema.json`: added `nand_technology`,
  `plane_allocation_scheme`, and the eight per-level latency fields, all
  optional and defaulting to the previous flat-scalar, SLC, CWDP behaviour so
  every existing profile is unaffected.
- `src/mqsim_adapter/mqsim_online.cpp`: wires the new fields into
  `Device_Parameter_Set::Plane_Allocation_Scheme` and
  `Flash_Parameter_Set::Flash_Technology` / the eight `Page_{Read,Program}_Latency_*`
  fields, replacing the previous hardcoded `SLC` and the implicit default
  `CWDP`.
- `patches/mqsim/0002-qlc-support.patch` (applied automatically by
  `cmake/MQSimPatchedBuild.cmake` alongside the existing HBF-API patch): adds
  `Flash_Technology_Type::QLC`, a fourth `TSB` read/program latency level, the
  `SSD_Device.cpp` construction case for it, and a `pageID % 4` physical-page
  round-robin in `Flash_Chip.h`'s `Get_command_execution_latency` — the same
  kind of simplification MQSim's own MLC case (`pageID % 2`) already uses, not
  a published QLC physical-page layout.
- `benchmarks/mqsim/hbf_mqsim_bench.cpp`: added `--pattern sequential|random`
  and `--seed`. Addresses for `random` are now sampled uniformly over the
  full capacity with a seeded splitmix64 generator (an earlier version of
  this change only shuffled delivery order of the same narrow sequential
  address run, which produced no measurable difference between sequential
  and random and no measurable density effect — that bug is fixed in the
  numbers below).
- `scripts/run_density_interleave_nand_sweep.py`: runs all three sweeps at
  `--pattern sequential` and `--pattern random`, both at queue saturation
  (`--arrival-gap-ns 0`, i.e. all requests submitted at time 0 up to
  `profile.queue_depth` in flight).

## Results

4,096 random 16 KiB reads per case, `--arrival-gap-ns 0`, `queue_depth = 64`
in every profile. Full JSON:
`docs/proofs/artifacts/2026-08-16-density-interleave-nand-sweep.json`.

### Die density/count (random pattern, seed 42; three seeds checked, see below)

| dies_per_channel | total dies | capacity/die | P50 (ns) | P99 (ns) |
|---|---|---|---|---|
| 2   | 32   | 32 GiB  | 731,980   | 1,691,920 |
| 4   | 64   | 16 GiB  | 752,940   | 1,800,780 |
| 8   | 128  | 8 GiB   | 771,300   | 1,922,640 |
| 16  | 256  | 4 GiB   | 811,270   | 2,111,440 |
| 32  | 512  | 2 GiB   | 906,970   | 2,405,040 |
| 64  | 1024 | 1 GiB   | 1,089,760 | 2,820,500 |
| 128 | 2048 | 512 MiB | 1,401,070 | 3,369,680 |

Monotonic in both P50 and P99: at fixed total capacity and fixed channel
count, **more (smaller) dies per channel make random-access latency worse in
this model**, not better. Checked at three seeds (42, 7, 123) for the two
extremes and the midpoint; the ordering and roughly the same magnitudes hold
at all three (dpc02 P99 1,663,810–1,694,520 ns; dpc128 P99 3,348,720–3,372,930
ns across the three seeds).

This is the opposite of the naive "more dies means more parallelism" reading
of the idea as originally raised. We have not traced the exact MQSim internal
mechanism producing this (candidates: `Flash_Block_Manager`/GC bookkeeping
cost per die, `TSU_OutOfOrder` per-die scheduling overhead, or the
`Address_Mapping_Unit_Page_Level` mapping arithmetic itself scaling with die
count) — that is unfinished work, not a result, and the paper must not assert
a mechanism it has not traced in the code.

### Address-interleaving order (`plane_allocation_scheme`, random pattern, seed 42)

| scheme | P50 (ns) | P99 (ns) |
|---|---|---|
| CWDP | 811,270 | 2,111,440 |
| WCDP | 811,270 | 2,111,440 |
| DWCP | 810,620 | 2,154,660 |
| PDWC | 805,420 | 2,166,520 |

Effect size is small (< 3% on P99) and not monotonic in any obvious order,
against a roughly 2x P99 swing across the density sweep above. Our reading:
for a uniformly random address stream, which axis (channel/way/die/plane)
strides fastest barely matters, because the requests already land on all
channels roughly evenly regardless of striping order. This proxy is much more
likely to show an effect under sequential or strided patterns, where a
coarser channel stride keeps consecutive addresses on fewer channels longer —
untested here.

### NAND cell type (random pattern, seed 42)

| technology | read LSB/CSB/MSB/TSB (ns) | P50 (ns) | P99 (ns) |
|---|---|---|---|
| SLC | 25000/25000/25000/25000  | 946,300   | 2,367,640 |
| TLC | 25000/50000/75000/75000  | 948,250   | 3,100,740 |
| MLC | 25000/25000/75000/75000  | 1,696,300 | 4,474,140 |
| QLC | 25000/50000/75000/100000 | 1,994,070 | 5,457,210 |

QLC is the slowest, as expected, but **MLC lands above TLC**, which looks
backwards for "more bits per cell is slower." This is not a bug in the
requested feature; it is MQSim's own physical-page-to-latency-level
assignment: MLC uses `pageID % 2` (half of all physical pages get the slow
MSB latency) while TLC uses a specific non-uniform formula from Yaakobi et
al. 2012 that assigns the slow MSB latency to a smaller fraction of pages.
Different fraction-of-pages-at-slow-latency, not a same-fraction comparison,
so **MLC vs TLC vs QLC absolute numbers here should not be read as "QLC costs
this much more than TLC"** without also accounting for that page-assignment
difference; SLC vs QLC (1 level vs 4 levels, most different in kind) is the
cleaner comparison of the four, and there QLC is 2.1x the P50 and 2.3x the
P99 of SLC.

## What this is and is not

- **The four LSB/CSB/MSB/TSB latency numbers per technology are not
  measured or cited.** They start from MQSim's own shipped MLC/TLC constants
  (75,000 ns read / 750,000 ns program for the slowest level) and extrapolate
  a QLC fourth level and SLC/faster levels by even steps. Per the project's
  own standing rule against invented constants
  (`todo/facts-to-verify.md`), **these belong in the paper only as "assumed,
  not measured" numbers, with the assumption stated**, not as a QLC latency
  claim.
- **`plane_allocation_scheme` is MQSim's own channel/way/die/plane striping
  order, not the spec's literal 64 B/256 B/4 KiB host-side AXI interleaving
  granularity.** It is the closest available proxy given MQSim's addressing
  unit is the page; see `todo/die-density-and-nand-mix-proposal.md` item 1 for
  why real byte-granularity interleaving is a larger, unstarted piece of work.
- **This is one workload shape**: 4,096 reads, 16 KiB each, saturating a
  64-deep queue, three seeds. It is not the five-pattern-times-three-profile
  sweep in `todo/experiments-to-run.md` item 6, and it has none of that item's
  statistical repetition/confidence-interval treatment yet (P5 in
  `docs/35-FAST27论文大纲与逻辑线.md`).
- **No cost/endurance side is computed.** The die-density result says nothing
  about whether more, smaller dies are cheaper or more reliable to
  manufacture; it is a pure timing-model result.
- **QLC support is a code-level extension we wrote**, not a stock MQSim
  feature request from upstream, and the `pageID % 4` physical-page
  assignment is our own simplification in the same spirit as MQSim's existing
  MLC simplification — flagged in the patch itself.

## What would close this before it goes in the paper as a claim

1. Trace which MQSim component produces the die-density monotonic trend, so
   the paper can name a mechanism instead of reporting a correlation.
2. Re-run the interleaving-order sweep under sequential/strided patterns,
   where an effect is more plausible.
3. Either find a citable SLC/MLC/TLC/QLC page-latency source, or state
   plainly in the paper that the four numbers are an assumed step function,
   not measured, following the same discipline already used for the
   Ea/activation-energy sweep in `todo/experiments-to-run.md` item 4.
4. Decide whether the MLC/TLC page-assignment-fraction confound is worth
   controlling for (e.g. reporting per-level hit counts) or whether the paper
   only uses the SLC vs QLC comparison, where the confound does not apply.
