# Phase-III full-chain temperature simulation

This document describes the implementation published on the
`全链路温度模拟` branch. The branch is an audited full-source snapshot based on
the prototype-vLLM-0.15 path; it is not a vLLM 0.26 port.

## Locked application contract

- Python 3.13.9
- vLLM 0.15.1
- torch 2.9.1+cu128 with torch CUDA 12.8
- Triton 3.5.1 and FlashInfer 0.6.1
- HBFSim native CUDA/NVCC 13.0/13.0.88 and G++15
- FlashInfer JIT and llama.cpp GCC/G++13
- NVIDIA driver 595.84, compute capability 12.0

The validated prototype EnvironmentFingerprint is
`e50f0179fc436a0f9fb486929ae24dc2f900e921e67e63199cc428f814ffef53`.

## Data flow

```text
application / CUDA access
  -> device-local per-access observation
  -> offline MQSim media replay
  -> command/media activity and energy bins
  -> package thermal runtime / validated ROM temperature steps
  -> Arrhenius retention-equivalent age
  -> refresh demand
  -> extra media reads, programs, energy, busy time, and latency envelope
```

The implementation deliberately keeps observation and intervention separate.
APP-0 per-access observation and offline MQSim replay are supported. The
original `host_launch_mqsim` path is not represented as per-access live delay
injection on this platform.

## Package thermal runtime

The optional subsystem is enabled with `HBFSIM_ENABLE_PACKAGE_THERMAL=ON`.
Profiles, topologies, power clocks, model policies, ROM evaluation, reporting,
and runtime integration live under:

- `include/hbfsim/package_thermal.hpp`
- `src/package_thermal/`
- `plugins/package_thermal/offline/`
- `experiments/package_thermal/phase2/`

The checked-in synthetic fixtures prove plumbing only. Scientific runs must
lock their package profile, ROM, command-energy arms, cooling arms, and
threshold envelope once; parameters must not be refit per workload.

## Retention and refresh projection

`hbfsim::reliability::RetentionRefreshModel` is implemented in:

- `include/hbfsim/retention_refresh.hpp`
- `src/retention_refresh.cpp`
- `experiments/retention_refresh/retention_refresh_cli.cpp`
- `tests/cpu/retention_refresh_test.cpp`

For each temperature interval it evaluates

```text
AF(T) = exp[(Ea / kB) * (1 / Tref - 1 / T)]
equivalent_age += elapsed_time * AF(T)
refresh_cycles = floor(equivalent_age / reference_retention_interval)
```

The locked reference is 24 hours at 85 degrees C. Activation-energy values are
sensitivity arms, not a measured confidence interval for a particular 3D-NAND
process. Each refresh cycle projects a full protected-data read/program pass,
then reports byte counts, page counts, command energy, serialized busy time,
per-channel critical path, and a same-die collision upper bound.

The model is an offline causal projection. It does not write refresh energy
back into thermal power bins that have already been emitted, and it does not
claim per-access live injection.

## HBM-Power long-timescale mapping

The external adapter and energy-conserving mapper live in
`experiments/external/hbm-power/`. The mapper integrates a finite modeled trace
once and applies an explicit workload schedule:

```text
E_out_stack = P_baseline_stack * resident_equivalent_time
            + P_active_stack * active_equivalent_time
```

It does not concatenate or unconditionally repeat the short trace. The result
is a macro energy/power envelope and does not preserve command-level temporal
correlation. The available RTX PRO 6000 uses GDDR7; external HBM2/HBM3E model
artifacts are not local-GPU HBM measurements.

## Build and verification

CPU/MQSim/package-thermal build:

```bash
cmake -S . -B build-package -G Ninja \
  -DHBFSIM_ENABLE_CUDA=OFF \
  -DHBFSIM_ENABLE_MQSIM=ON \
  -DHBFSIM_ENABLE_PACKAGE_THERMAL=ON \
  -DBUILD_TESTING=ON
cmake --build build-package -j
ctest --test-dir build-package --output-on-failure
```

Focused checks:

```bash
ctest --test-dir build-package -R '^retention_refresh$' --output-on-failure
ctest --test-dir build-package -R '^hbm_power_energy_mapping$' --output-on-failure
```

The publication source was validated in the locked prototype environment with
76 of 76 configured tests passing. Exact experimental locks and raw campaign
artifacts are intentionally not embedded in the Git repository; they remain in
the separate experiment archive.

## Claim boundaries

- vLLM scope is exactly 0.15.1.
- APP-0 observation/replay is supported; live per-access injection is not
  claimed.
- Command energy, cooling, and thresholds are a locked sensitivity envelope
  where direct physical calibration evidence is unavailable.
- Retention/refresh results are offline temperature-driven projections.
- HBM-Power mapping conserves macro energy but not command timing correlation.
- GDDR7 hardware must not be described as HBM.
