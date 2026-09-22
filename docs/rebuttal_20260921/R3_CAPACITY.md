# R3 capacity preparation

Status: **READY_NOT_RUN**. R1 and R2 must pass before the single giga GPU
coordinator may use `--execute`.

Two evidence paths remain separate:

1. The original R3 entry is `scripts/run_microbench.py --over-vram`, which
   drives the public `hbfsim_map_file` path with a 110 GiB sparse logical range,
   a 2 GiB frame cache and 128 accesses. This reproduces the historical
   addressability/checksum scope. It is not a full 110 GiB read or a full
   working-set performance measurement.
2. `capacity_payload_fixture` directly exercises `CapacityRuntime` for page
   sizes 4/8/16/32/64 KiB. Each point uses the same 110 GiB logical span, 2 GiB
   cache and 65 common 64 KiB-aligned offsets including first, middle and the
   last common 64 KiB region. It additionally checks each geometry's actual
   final page; for 4/8/16/32 KiB this point is intentionally not common-aligned.
   Every access is sequential. The fixture records the logical
   offset/page, an encounter-order alias for each returned CUDA frame address
   (not an internal cache index), expected/actual payload hash and real
   CUDA frame readback. It does not instantiate MQSim and supports no MQSim
   timing or concurrent-eviction claim.

Frame counts are 524288, 262144, 131072, 65536 and 32768 respectively.

`build_fixture.py` configures an isolated build below this experiment directory,
builds the existing live capacity target, compiles this runner with the same
toolchain, and never executes a CUDA binary. `run_r3_capacity.py` defaults to a
read-only preflight and refuses execution unless passed both `--execute` and
`--r1-r2-gate PASS`.


---

## 2026-09-21 public-map supplement

Current state: **BLOCKED_BY_R1** and ordered after R2. No R3 GPU cell has run. The earlier preparation record above remains preserved and is supplementary mechanism work, not a capacity result.

The primary path now prepared retains public `hbfsim_map_file`: a 110 GiB sparse logical file, fixed 2 GiB cache, and an instrumented GPU load through the mapped virtual address. It tests page sizes 4, 8, 16, 32 and 64 KiB with fixed total logical/cache bytes. Each cell uses 64 uniformly distributed positions plus first/middle/last anchors, de-duplicated, page aligned, and issued serially with synchronization. It does not deliberately create concurrent eviction.

Each sample will record logical offset/page, actual GPU virtual address, and expected/actual checksum. The public API has no frame getter, so `frame=null` and `frame_status=UNSUPPORTED_PUBLIC_API`; an internal alias must not be reported as a physical frame.

Compile-only receipts:

- host binary `build/rebuttal-r3-public-20260921/r3_public_capacity_bench`, SHA-256 `0778055f94af3c0fd00a594cb6cbb147c71932ed5ea1dadcfa9a1634adba0606`;
- original microbenchmark PTX used by the prepared path, SHA-256 `edd496420c7516f6af0a526c1d3fc2e0d693d8ae5bd250ce53c65b1bfdbc2706`;
- the initial CUDA 13.1/glibc `rsqrt/rsqrtf` compile failure is preserved; the existing build-local header overlay compiled without changing the system toolkit.

The runner defaults to no execution and requires explicit R1 and R2 PASS gates. There are currently no sampled addresses, checksum passes, page-size results, addressability result, performance result, or concurrent-eviction claim.


---

## 2026-09-21 current R3 packet (CPU-built, GPU not run)

Status remains **READY_NOT_RUN / BLOCKED_BY_R1_THEN_R2**. The current packet is independently built in `build/rebuttal-r3-stats-v1`; it does not replace or relabel the frozen R1 runtime. The non-executing campaign receipt is `/root/hbfsim-exp/rebuttal_20260921/results/r3-preflight-v1/campaign-plan.json` (SHA-256 `48024696e00ddbb4c2429b68223274edac077871294179c18c3253c223317283`). It records `gpu_execution_requested=false`, and no R3 result root was created.

The fixed-work matrix uses the same 65 common 64 KiB-aligned regions for all five logical HBF page sizes. Dense reads and returns every u64 in each 64 KiB region; sparse reads and returns every u64 in the first contiguous 4 KiB. CPU validation compares every returned word against a non-uniform value derived from the absolute logical byte offset and also records a checksum. Fixed cells run cold then warm in the same runtime and order. The relative-page matrix reads the first actual page at P/16, P/4 and P coverage, cold only. This yields 20 fixed stage records plus 15 relative cells; the 64 KiB/full relative cell reuses the identical dense-cold stage, so 14 relative stages are new.

All workloads initialize the complete 65 x 64 KiB backing pattern. The benchmark records requested 2 GiB cache bytes, actual VMM pool reserved bytes, logical page bytes, CUDA VMM allocation granularity, and logical frame count, then requires the actual geometry to equal the configured geometry. Logical HBF page size is a software cache/addressing granularity; it is not represented as the GPU hardware page size. The pool is one allocation rounded once to CUDA granularity, rather than one allocation per logical frame.

`HBFSIM_CAPACITY_STATS_V1=1` enables service-scoped counters. The published names describe only the host service path: resolve classifications, successful software backing payload returns, successful H2D payload copies, terminal ready results, writeback payloads, and resolve-path evictions. A device-resident fast path can bypass this service, so global cache hit rate remains `NA`. Backing payload bytes are not physical SSD traffic and may be served by the OS page cache. H2D payload bytes are not measured PCIe transaction bytes. Hardware I/O traffic remains `UNKNOWN`, request count is not called fault count, and physical/internal frame identity remains `UNKNOWN_NO_PUBLIC_GETTER`.

The exact BPF probe binds `kprobe/r3_page_read_kernel`, matching the PTX entry. Current compile-only artifacts are:

- benchmark: `build/rebuttal-r3-stats-v1/r3-public/r3_public_capacity_bench`, SHA-256 `626bf4a834bd0e7470a9aa01001fb0b04c46712c7301638eb7460d396d7fa247`;
- PTX: `build/rebuttal-r3-stats-v1/r3-public/r3_public_capacity_kernel.ptx`, SHA-256 `d6a4d29322796593f6a17892b457d36656ac3d5de1ca268638cd86d1a9f98daa`;
- matched probe: `build/rebuttal-r3-stats-v1/r3-public/r3_public_capacity_probe.bpf.o`, SHA-256 `3559c717692a3404874057bb0ebf3f45b2b2d015e0fb8993cfbc73467814815c`;
- R3 core archive: `build/rebuttal-r3-stats-v1/libhbfsim_core.a`, SHA-256 `983069dfccc99e444a1ae6416cbb697840ad64ba033b43e7be0ded3debe07812`;
- CPU service test: `build/rebuttal-r3-stats-v1/hbfsim_capacity_page_service_tests`, SHA-256 `77dcfcbbd9c590576a0c2c9b464813575be9bb1f2d1d83683ca6cd1074f25207`, direct execution exit 0.

The runner writes each completed region to `region-progress.jsonl`, writes the command before launch, preserves partial stdout/stderr on timeout, and publishes PASS only after module unload, device free, unregister, context destruction and backing cleanup succeed. None of these compile or CPU checks is a GPU result.
