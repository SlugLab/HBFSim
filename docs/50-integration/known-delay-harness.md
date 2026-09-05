# Explicit known-delay experiment

`HBFSIM_BUILD_KNOWN_DELAY_BENCHMARK` defaults OFF. The CUDA benchmark uses the
existing PTX pass, trusted module-load token and embedded helper binding. Each
warp contributes one active lane to a K-hop dependent read chain. The returned
value feeds the checksum and the next address. Globaltimer chain stamps, CUDA
Event time, CPU expected checksums, coverage counters and block/SM intervals
are separate observations. Dynamic shared-memory reservation is checked with
`%dynamic_smem_size`; theoretical occupancy is distinct from observed residency.

`EvalDelayConfig`, counters and trace records are module-local experiment data,
outside control ABI 4/header 384. Magic zero preserves the production resolver.
An explicitly enabled module permits registered TIMING reads at time_scale 1,
with D from 0 through 20 microseconds. Registered capacity/writes fail closed.
The hook follows the existing control, generation, range and media checks and
retains grouping/final address translation. D=0 retains this same helper path
but performs no synthetic wait. The ordinary runtime profile remains positive;
profile parsing, media service defaults and shared layouts are unchanged.

`run_gpu_delay.py` plans one exact CSV cell by default. Explicit execution runs
a bounded native/matched-zero/selected-treatment triplet with GPU_EXCLUSIVE
guarding and immutable diagnostics. Each raw result must match both the other
controls and its selected plan's D/K/warps/occupancy/treatment. The runner
preserves checksum/coverage and fixed G2 mean/P95 error limits; D=0 reports noise.
SM identities are opaque nonnegative integers; observed distinct SM count is
bounded by the device count, with block/chain identity checked. Numeric IDs
need not be contiguous ([NVIDIA PTX special-register contract](https://docs.nvidia.com/cuda/parallel-thread-execution/index.html#special-registers-nsmid)).
One triplet does not establish repeated hardware noise or full matrix G2 gold.
No inherited-lock scheduler adapter or long-form metric exporter is installed.

All 18 runner CPU/compile controls pass (3.294 s); no injected result is
accepted as a formal measurement. CPU and compile-only controls pass on CUDA 13 with a consistent g++-13 host/core
build. The original mixed GCC build failed during linking and remains negative
evidence. Successful build configuration and commands are recorded in
`results/gold/known-delay/`. Existing helper ABI and PTX controls also pass.
No GPU kernel, timing or observed occupancy measurement has run on this host.

One representative SM120 image was assembled and disassembled with cuobjdump
and nvdisasm. `results/gold/known-delay/sass-control/mapping-notes.md` records
the resolver call, returned address/status, native load, checksum use and next
dependent address in that exact synchronous image. This does not validate the
absent production future/TMA mappings. The reusable `audit_sass_mapping.py`
collects/cache-checks these artifacts while keeping mapping NOT_PROVEN until
an applicable independent semantic review establishes the requested scope.
