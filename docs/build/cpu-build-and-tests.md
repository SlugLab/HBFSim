# CPU-only build, test scope and troubleshooting

These notes accompany the Quick start in the [README](../../README.md). They were moved
out of the README so that the README keeps only the commands a new user runs.

## Test results on a machine without CUDA

Configure succeeds, all 152 build targets build, and 31 of the 34 tests pass on a machine
without CUDA. [TODO.md](../../TODO.md) records the three tests that do not pass and the reason for
each of those three.

CI runs the tests with the additional exclusion list `-E 'vmem_tuning|run_with_bpftime|context_lifecycle'`, so CI does not run all tests. `vmem_tuning` and `run_with_bpftime` involve a specific environment or external inputs, and `context_lifecycle` corresponds to a problem that has not been fixed yet; for details, see the [CI configuration](../../.github/workflows/ci.yml) and [TODO](../../TODO.md#validation). When the external calibration CSV is missing, `vmem_tuning` skips the byte-for-byte reproduction subtest that depends on the CSV.

## SASS recovery is off by default

SASS recovery is experimental and is **off by default** in the user entry point.
The [SASS runtime tools](../../tools/sass_runtime/) explain how to opt in and how to
report the layers whose bindings actually ran. Native PTX instrumentation is
independent of this option.

## Build error with libbpf and clang installed

If the libbpf development package and clang are installed, a build without CUDA may report `CMake can not determine linker language for target: hbfsim_llama_probe_module`. Call CMake directly as described in the [environment runbook](../eval/runbooks/environment_setup.md) and add `-DCMAKE_DISABLE_FIND_PACKAGE_PkgConfig=ON`; `bootstrap.sh` does not forward this option.

## Effective capacity of the media benchmark

When `--capacity-bytes` is not passed, the media benchmark limits capacity to 16 blocks per plane. The example run in the README Quick start had an effective capacity of 64 GiB and a cache of 64 MiB, not the full configured values in `nominal.json`. Save `effective_profile` when comparing experiments.
