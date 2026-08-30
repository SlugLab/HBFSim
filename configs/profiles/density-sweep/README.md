# Die density / die count sensitivity sweep

**Question.** At fixed total capacity, does trading fewer large-capacity dies for
more small-capacity dies change random-access parallelism and tail latency?
Raised in discussion with 胡学长, 2026-08-16.

**What these profiles vary.** Every file below is `configs/profiles/conservative.json`
(16 channels, matching the OCP HBF spec's 16-channel design, per
`docs/HBF_OCP/ocp2026-hbf-architecture-specification-v0-7-0.txt` line 1230) with only
`dies_per_channel` changed. `capacity_bytes` (1 TiB), `channels`, `planes_per_die`,
`pages_per_block`, `page_bytes`, and every latency/bandwidth field are held constant, so
`blocks_per_plane` is re-derived by `hbfsim::blocks_per_plane` to keep total capacity
exactly fixed — the die-capacity change is a pure consequence of the existing schema,
no new fields were needed.

| file | dies_per_channel | total dies (×16 channels) | capacity per die |
|---|---|---|---|
| hbf-density-dpc02.json | 2   | 32   | 32 GiB |
| hbf-density-dpc04.json | 4   | 64   | 16 GiB |
| hbf-density-dpc08.json | 8   | 128  | 8 GiB (= `conservative.json`) |
| hbf-density-dpc16.json | 16  | 256  | 4 GiB |
| hbf-density-dpc32.json | 32  | 512  | 2 GiB |
| hbf-density-dpc64.json | 64  | 1024 | 1 GiB |
| hbf-density-dpc128.json| 128 | 2048 | 512 MiB |

**What is not modeled by this sweep.** These profiles only change
channel/die/plane *count*, i.e. parallelism width. They do not change:

- **Host-side AXI address interleaving granularity** (64 B, 256 B, 4 KiB — spec
  section 13, Figure 5/42). MQSim is configured with a single fixed
  `Flash_Address_Mapping_Type::PAGE_LEVEL` policy in
  `src/mqsim_adapter/mqsim_online.cpp`, with no interleaving-granularity knob
  exposed from the profile. Comparing that would need a new parameter and mapping
  logic, not just a new config file — see the open item in
  `todo/experiments-to-run.md` item 6 and the proposal note below.
- **NAND cell type (SLC/QLC).** `Flash_Parameter_Set::Flash_Technology` is
  hardcoded to `Flash_Technology_Type::SLC` in the same file regardless of the
  profile; there is no per-profile or per-die NAND-type field in
  `include/hbfsim/profile.hpp` or `configs/schema/hbf-profile.schema.json`.

**How to run.** `benchmarks/mqsim/hbf_mqsim_bench` reads `--capacity-bytes`
directly; pass the profile's own `capacity_bytes` explicitly (the benchmark
otherwise caps capacity to a small default block count):

```sh
build/benchmarks/mqsim/hbf_mqsim_bench \
  --profile configs/profiles/density-sweep/hbf-density-dpc16.json \
  --capacity-bytes 1099511627776 \
  --operation read --requests 8192 --bytes 16384 --arrival-gap-ns 200
```

This benchmark currently issues addresses in a fixed round-robin stride, not a
random pattern, so it exercises die/channel parallelism but is not yet a
high-QD *random*-access P99 test; see the proposal note for what is missing to
close that gap.
