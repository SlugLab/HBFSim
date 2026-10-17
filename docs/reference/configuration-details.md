# Configuration details and current implementation scope

These notes accompany the build options table in the [README](../../README.md#configuration).
They were moved out of the README so that the README keeps only the option table.

## How the build options are passed

`bootstrap.sh` directly forwards three of the options in the README table: CUDA, MQSim, and LLM tests; the other options are configured through `cmake -D...`. There are also test and diagnostic switches; see [CMakeLists.txt](../../CMakeLists.txt).

**SASS recovery** is a runtime choice: the ordinary entry point defaults to `HBFSIM_ENABLE_SASS_LIFTER=0`, and the research entry point `scripts/run_with_sass_research.sh` enables SASS recovery by default. [Usage notes](../../tools/sass_runtime/README.md). **EQ3** is likewise an optional module; for how to enable EQ3, see the [module documentation](../eq3_thermal/README.md).

## Profiles

**Currently available profiles.** `configs/profiles/{conservative,nominal,aggressive}.json` are historical synthetic configurations, each with 1 TiB and 16 KiB pages; `cd8p-vmem-p50` is a calibrated configuration from a measured software path. Configurations are read and checked by a typed loader (`src/profile/profile.cpp`); the field conventions are also described in a [JSON Schema](../../configs/schema/hbf-profile.schema.json). Capacity, page size, service parameters, and `time_scale` should be saved along with experiment results.

## Planned 4 KiB baseline

The follow-up study design uses a total capacity of 2 TiB across 4 HBF devices, with 512 GiB per device by default. The values below are study parameters; the values are not configurations implemented in the existing JSON files, and they are not measured values from an HBF device.

| Grade | Page | Read / write latency | Total internal channels | Total queue entries | Total bandwidth cap |
|---|---:|---:|---:|---:|---:|
| `conservative` | 4 KiB | 20 / 200 μs | 16 | 1024 | 128 GB/s |
| `nominal` | 4 KiB | 10 / 100 μs | 32 | 2048 | 512 GB/s |
| `aggressive` | 4 KiB | 5 / 50 μs | 64 | 4096 | 1000 GB/s |

Each HBF device independently enforces 1/4 of the total bandwidth cap and does not borrow unused allowance from other HBF devices. The program implementation, pre-run validation, and the budget for the formal experiment matrix are still to be done, so the historical JSON files with the same names cannot be used directly to launch the 4 KiB baseline. [Full design](../evaluation/hbf-4k-study-baseline.md)

## Current implementation scope

**Wait semantics.** None of the existing profiles meets all admission conditions of the experimental dependency-point waiting path, which include `time_scale=1` and calibration that is not from a measured curve, among others. Turning on the build switch alone is not enough to make the existing profiles use dependency-point waiting; there is also no paired measurement yet of issue-point waiting and dependency-point waiting on the same workload. [Implementation limits](../51-defect-review-and-fixes.md)

**Writes and paging.** Timing writes are still timed per store instruction rather than merged into 4 KiB program units; for the in-place dirty-page rewrite of Capacity, see the [correctness TODO](../../TODO.md#correctness-and-fidelity), and for early frame reuse, see the [defect review](../51-defect-review-and-fixes.md). Success with sparse read-only access cannot be extrapolated to correctness in arbitrary writable paging scenarios.

**Extension paths.** The UCIe backend currently supports reads only, with no GPU run records and no sweeps over 2 and 8 HBF devices yet; EQ3 has thermal and maintenance models but no MTBF lifetime prediction. Runtime prefetching in the paper prototype should not automatically be considered a capability of the `main` default build.
