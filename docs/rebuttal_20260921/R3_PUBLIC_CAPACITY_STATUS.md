# R3 public-map page protocol

Status: **READY_NOT_RUN / BLOCKED_BY_R1_R2_AND_CAPACITY_STATS_ABI**. No GPU
execution has occurred.

The independent benchmark retains the public `hbfsim_map_file` route, a
110 GiB sparse logical mapping, and a fixed 2 GiB HBF cache. All cases use the
same 65 distinct 64 KiB-aligned region bases stratified across the full logical
space, including first, middle and last. Values are a nonuniform deterministic
function of the absolute logical byte address, so page geometries see the same
content at the same address.

## Frozen matrix

The fixed-workload matrix has five page sizes (4, 8, 16, 32 and 64 KiB), two
densities and two passes. Dense reads and includes every u64 word in a checksum
for the complete 64 KiB region (4.0625 MiB valid bytes per pass). Sparse does
the same for each region's contiguous first 4 KiB (260 KiB per pass). Each
density/page case starts a fresh runtime for cold, then repeats the identical
region and word order warm: 20 stage records.

The relative-page matrix reads every u64 word in the first actual logical page
at P/16, P/4 and P coverage, cold only: 15 cells. Effective bytes are
65*P*fraction and must be reported and normalized; raw total time is not
comparable across different byte counts. The 64 KiB/full cell reuses the
identical fixed-dense cold stage. The other 14 cells are new. A smaller-page
full cell never reuses or substitutes the fixed 64 KiB workload.

There is no separate stride sweep. The 65 stratified region sequence is the
only inter-region spacing. Results record min/max region gaps and their ratios
to P. Each region records first/last GPU VA, logical page, expected/actual
whole-prefix checksum and explicit unsupported frame identity. Logging a
checksum per region controls artifact size while every accessed u64 word
participates in that checksum.

## Cache geometry

`CapacityRuntime` computes `frame_count=2GiB/P`.
`VmmFramePool::create` then forms one `frame_count*P=2GiB` pool, rounds that
single pool to CUDA's allocation granularity, and creates one allocation/map.
It does not round every logical frame separately. P is therefore the HBF
logical cache-page and software-addressing granularity; it is not a claim that
the GPU hardware page becomes P. The run receipt must record
`cuMemGetAllocationGranularity` and actual pool bytes.

## Required real counters

The current public `hbfsim_stats` is insufficient: request count is not fault
count. Formal execution requires an opt-in capacity v1 snapshot exposing real
resident and reclaimed hits, misses, successful backing-read pages/bytes,
successful frame-fill pages/bytes, evictions, dirty writeback pages/bytes,
failures, logical frame count, page bytes, CUDA VMM granularity and allocated
pool bytes. Each stage must save before/after snapshots and report deltas.

Until that ABI is implemented, hit/miss/fault/fill/host-read metrics are
explicitly unavailable and R3 stays blocked. Checksums alone are functional
coverage, not sufficient page-behavior evidence.

## Prepared files

- `r3_public_capacity_bench.cpp`: public map plus instrumented GPU loads,
  complete-word checksums, cold/warm stage structure.
- `run_r3_public_capacity.py`: defaults to preflight and requires both R1 and
  R2 PASS before execution; 25 cells, 35 stage records, 24 new processes.
- `build_public_capacity.py`: CPU build wrapper requiring explicit compiler
  and private CUDA root; it does not run CUDA.

CPU-only checks passed for Python syntax, the matrix contract and C++ syntax
against the private CUDA 13.0 headers. No scientific result is claimed.
