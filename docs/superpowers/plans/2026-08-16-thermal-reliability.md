# HBF Thermal Reliability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a fail-closed, temperature-driven HBF service, retention, refresh, endurance, and parameterized MTBF loop to both HBFSim timing paths, then document it in the FAST paper.

**Architecture:** A host controller advances a first-order HBF junction-temperature model from live or declared GPU telemetry and HBF traffic. It publishes a generation-stamped thermal snapshot through control ABI v10, schedules deterministic zone refresh work into MQSim, and publishes equivalent refresh debt for the device fast path. Pure C++ reference models own equations and reliability state so CPU tests, host integration, CUDA helpers, and the paper use one contract.

**Tech Stack:** C++20, CUDA 13.0 (`sm_120`), GCC 13, nlohmann/json, NVML loaded through `dlopen`, MQSim, CMake/CTest, Python 3, LaTeX/USENIX.

---

## File Map

New focused files:

- `include/hbfsim/thermal_reliability.hpp`: validated thermal/reliability value types, equations, state machine, zone state, hazard samples, and public snapshots.
- `src/thermal/thermal_reliability.cpp`: pure deterministic implementation with no CUDA, NVML, MQSim, or wall-clock dependency.
- `src/cuda_runtime/thermal_telemetry.hpp`: injectable telemetry publisher interface and lifecycle.
- `src/cuda_runtime/thermal_telemetry.cpp`: reusable dynamic NVML temperature/power sampling.
- `src/host_service/thermal_controller.hpp`: fake-clock-friendly controller interface.
- `src/host_service/thermal_controller.cpp`: periodic thermal state publication, zone aging, and refresh eligibility.
- `src/host_service/refresh_scheduler.hpp`: deterministic foreground/background media scheduling contract.
- `src/host_service/refresh_scheduler.cpp`: refresh quantum order, MQSim background IDs, completions, PEC commit, and fast debt.
- `include/hbfsim/thermal_report.hpp`: immutable run-summary type and writer declaration.
- `src/reporting/thermal_report.cpp`: durable canonical JSON summary writer.
- `tests/cpu/thermal_reliability_test.cpp`: equations, state machine, damage, PEC, and hazard tests.
- `tests/cpu/thermal_controller_test.cpp`: fake-clock controller and ABI publication tests.
- `tests/cpu/refresh_scheduler_test.cpp`: reference/fast refresh-plan equivalence and failure tests.
- `tests/cpu/thermal_device_reference_test.cpp`: host reference for device admission, scaling, and debt helpers.
- `tests/integration/thermal_daemon_test.cpp`: process/shared-memory lifecycle, Severe backpressure, recovery, and Shutdown.
- `tests/integration/thermal_capacity_test.cpp`: refresh data-integrity and PEC integration.
- `tests/integration/test_thermal_report.py`: schema, provenance, sensitivity sweep, and terminal-report checks.
- `configs/profiles/thermal-validation.json`: deterministic accelerated validation profile.
- `configs/schema/thermal-reliability-summary.schema.json`: durable output contract.

Existing files modified in place:

- `include/hbfsim/profile.hpp`, `src/profile/profile.cpp`, and `configs/schema/hbf-profile.schema.json`: optional strict profile parsing.
- `src/host_service/control_layout.hpp` and `src/cuda_runtime/device/hbf_device.cuh`: control ABI v10 mirrored layout and device helpers.
- `include/hbfsim/api.h`, `src/api.cpp`, and `src/cuda_runtime/context.cpp`: versioned stats API, telemetry lifecycle, and terminal status mapping.
- `src/cuda_runtime/device/hbf_device.cu`: thermal admission and service/debt application to synchronous, future, and TMA timing.
- `src/host_service/request_dispatcher.hpp`, `src/host_service/request_dispatcher.cpp`, and `src/host_service/main.cpp`: thermal controller and background media work.
- `src/mqsim_adapter/mqsim_online.cpp`: background request flag validation without changing MQSim media physics.
- `CMakeLists.txt`: sources and tests.
- `6a7bdca65b0b3f3b7f676b14/design.tex`: final Design section in the independent Overleaf checkout, not the old paper submodule snapshot in this worktree.

All new C++ tests follow the repository's existing standalone-test convention:
each file defines `check(bool, std::string_view)` and
`close_ld(long double actual, long double expected, long double relative_epsilon)`
helpers, returns nonzero after any failed check, and does not require Catch2 or
GoogleTest. Byte quantities use ordinary integer expressions such as
`4ULL << 10` and `1ULL << 20`.

### Task 0: Preserve the Approved Contract

**Files:**
- Create: `docs/superpowers/specs/2026-08-16-thermal-retention-refresh-mtbf-design.md`
- Create: `docs/superpowers/plans/2026-08-16-thermal-reliability.md`

- [ ] **Step 1: Re-run documentation checks**

```bash
git add --intent-to-add \
  docs/superpowers/specs/2026-08-16-thermal-retention-refresh-mtbf-design.md \
  docs/superpowers/plans/2026-08-16-thermal-reliability.md
git diff --check
python3 - <<'PY'
from pathlib import Path

needles = ("TB" + "D", "TO" + "DO", "implement " + "later")
paths = (
    Path("docs/superpowers/specs/2026-08-16-thermal-retention-refresh-mtbf-design.md"),
    Path("docs/superpowers/plans/2026-08-16-thermal-reliability.md"),
)
hits = [(path, needle) for path in paths for needle in needles
        if needle in path.read_text()]
if hits:
    print(hits)
    raise SystemExit(1)
PY
```

Expected: whitespace check passes and the placeholder search prints no lines.

- [ ] **Step 2: Commit the design contract and execution plan**

```bash
git add docs/superpowers/specs/2026-08-16-thermal-retention-refresh-mtbf-design.md \
  docs/superpowers/plans/2026-08-16-thermal-reliability.md
git commit -m "docs: specify thermal reliability implementation"
```

### Task 1: Strict Thermal Profile Contract

**Files:**
- Modify: `include/hbfsim/profile.hpp`
- Modify: `src/profile/profile.cpp`
- Modify: `configs/schema/hbf-profile.schema.json`
- Create: `configs/profiles/thermal-validation.json`
- Modify: `tests/cpu/profile_test.cpp`

- [ ] **Step 1: Add a failing valid-profile and invalid-boundary test**

Add a `thermal_document()` fixture to `tests/cpu/profile_test.cpp` and assert the parsed values and exact errors:

```cpp
const auto thermal_path = write_profile(thermal_document(), "thermal-valid");
const auto thermal = hbfsim::load_profile(thermal_path);
check(thermal.thermal_reliability.has_value(), "thermal profile loaded");
check(thermal.thermal_reliability->ltt_millic == 80'000, "LTT parsed");
check(close_ld(thermal.thermal_reliability->retention_ea_ev, 1.10L, 1e-12L),
      "retention activation energy parsed");

auto invalid_thermal = thermal;
invalid_thermal.thermal_reliability->rtt_millic = 81'000;
check_profile_error(
    [&] { hbfsim::validate_profile(invalid_thermal); },
    "thermal thresholds must satisfy RTT < LTT < STT < shutdown <= 105C");
```

- [ ] **Step 2: Run the profile test and verify RED**

Run:

```bash
cmake --build build-thermal-baseline-gcc13 --target hbfsim_profile_tests -j2
ctest --test-dir build-thermal-baseline-gcc13 -R '^profile$' --output-on-failure
```

Expected: compile failure because `Profile::thermal_reliability` is absent.

- [ ] **Step 3: Add the profile value type and strict parser**

Add this contract to `include/hbfsim/profile.hpp`:

```cpp
enum class ThermalTemperatureSource : std::uint32_t {
    LiveGpu = 1,
    Trace = 2,
    Constant = 3,
};

struct ThermalReliabilityProfile {
    ThermalTemperatureSource temperature_source;
    std::int64_t ambient_millic;
    std::int64_t initial_hbf_junction_millic;
    long double tau_seconds;
    long double gpu_coupling_ratio;
    long double thermal_resistance_c_per_w;
    long double idle_power_w;
    long double read_energy_j_per_byte;
    long double write_energy_j_per_byte;
    std::uint32_t telemetry_period_ms;
    std::uint32_t controller_period_ms;
    std::int64_t rtt_millic;
    std::int64_t ltt_millic;
    std::int64_t stt_millic;
    std::int64_t shutdown_millic;
    std::uint32_t light_service_ppm;
    std::uint64_t zone_bytes;
    long double reference_retention_hours;
    std::int64_t reference_retention_millic;
    long double retention_ea_ev;
    long double refresh_damage_threshold;
    std::uint64_t read_disturb_limit;
    std::uint64_t refresh_quantum_bytes;
    bool registered_ranges_contain_valid_data;
    long double reliability_time_acceleration;
    std::uint64_t max_pec;
    long double reference_mtbf_hours;
    std::int64_t mtbf_reference_millic;
    long double mtbf_ea_min_ev;
    long double mtbf_ea_max_ev;
    long double mtbf_ea_step_ev;
    std::string source_sha256;
};
```

Store it as `std::optional<ThermalReliabilityProfile>` in `Profile`. Parse every
required key when `thermal_reliability` exists, reject unknown keys through the
JSON schema, and validate finite numbers, periods, geometry, thresholds,
activation-energy sweep, and SHA-256.

- [ ] **Step 4: Add the JSON schema and named validation profile**

Add a closed `thermal_reliability` object to
`configs/schema/hbf-profile.schema.json`. Create
`configs/profiles/thermal-validation.json` with a constant 79C source, 78/80/90/100C
thresholds, 900,000 ppm Light service, 4KiB pages, 1MiB zones, 24h at 85C,
1.10eV retention energy, 0.95 damage threshold, 4KiB refresh quantum, 1000x
test-only reliability acceleration, MAXPEC 3000, 20M-hour reference MTBF, and
1.05--1.20eV in 0.05eV steps.

- [ ] **Step 5: Run GREEN and the schema check**

Run:

```bash
cmake --build build-thermal-baseline-gcc13 --target hbfsim_profile_tests -j2
ctest --test-dir build-thermal-baseline-gcc13 -R '^profile$' --output-on-failure
python3 -m json.tool configs/profiles/thermal-validation.json >/dev/null
```

Expected: profile test passes and JSON parsing exits 0.

- [ ] **Step 6: Commit the profile contract**

```bash
git add include/hbfsim/profile.hpp src/profile/profile.cpp \
  configs/schema/hbf-profile.schema.json configs/profiles/thermal-validation.json \
  tests/cpu/profile_test.cpp
git commit -m "feat: define strict thermal reliability profiles"
```

### Task 2: Pure Thermal and Reliability Model

**Files:**
- Create: `include/hbfsim/thermal_reliability.hpp`
- Create: `src/thermal/thermal_reliability.cpp`
- Create: `tests/cpu/thermal_reliability_test.cpp`
- Modify: `CMakeLists.txt`

- [ ] **Step 1: Write failing equation and transition tests**

The test profile fixes `tau_seconds=0.001`, `gpu_coupling_ratio=1`, zero media
thermal resistance, and zero traffic energy so a one-second advance reaches
the selected state deterministically. The test must check reference identities,
direction, variable-temperature damage, both hysteresis windows, Shutdown
terminality, PEC commit, and hazard:

```cpp
using hbfsim::ThermalMode;
const auto profile = make_test_thermal_profile();
hbfsim::ThermalReliabilityModel model(profile, 78'000);
const auto light = model.advance({.elapsed_ns = 1'000'000'000,
                                  .gpu_millic = 85'000,
                                  .read_bytes = 1ULL << 20,
                                  .write_bytes = 0});
check(light.mode == ThermalMode::Light, "LTT enters Light");
check(light.service_ppm == 900'000, "Light service is 90 percent");

check(close_ld(hbfsim::retention_hours(profile, 85'000),
               24.0L, 1e-12L),
      "reference retention identity");
check(hbfsim::retention_hours(profile, 70'000) > 24.0L,
      "cooler temperature extends retention");
check(hbfsim::retention_hours(profile, 95'000) < 24.0L,
      "hotter temperature shortens retention");

hbfsim::ZoneReliability zone{.valid = true};
hbfsim::integrate_zone_damage(zone, profile, 85'000,
                              std::chrono::hours(12));
check(close_ld(zone.retention_damage, 0.5L, 1e-12L),
      "twelve reference hours accumulate half damage");
```

- [ ] **Step 2: Run RED**

```bash
cmake -S . -B build-thermal-baseline-gcc13 \
  -DCMAKE_BUILD_TYPE=Debug -DHBFSIM_ENABLE_LLM_TESTS=OFF \
  -DCMAKE_CXX_COMPILER=/usr/bin/g++-13 \
  -DCMAKE_CUDA_COMPILER=/usr/local/cuda-13.0/bin/nvcc \
  -DCMAKE_CUDA_HOST_COMPILER=/usr/bin/g++-13
cmake --build build-thermal-baseline-gcc13 --target thermal_reliability_test -j2
```

Expected: target or header is missing.

- [ ] **Step 3: Implement checked equations and state**

Expose these operations in `thermal_reliability.hpp`:

```cpp
enum class ThermalMode : std::uint32_t { Normal, Light, Severe, Shutdown };

struct ThermalInput {
    std::uint64_t elapsed_ns;
    std::int64_t gpu_millic;
    std::uint64_t read_bytes;
    std::uint64_t write_bytes;
};

struct ThermalSnapshot {
    std::uint64_t generation;
    ThermalMode mode;
    std::int64_t junction_millic;
    std::uint32_t service_ppm;
};

long double retention_hours(const ThermalReliabilityProfile&,
                            std::int64_t temperature_millic);
void integrate_zone_damage(ZoneReliability&,
                           const ThermalReliabilityProfile&,
                           std::int64_t temperature_millic,
                           std::chrono::nanoseconds elapsed);
std::vector<MtbfSensitivityPoint> integrate_mtbf_sensitivity(
    const ThermalReliabilityProfile&, std::span<const TemperatureInterval>);
```

Use `std::expm1` near zero, Kelvin conversion with 273.15, checked 128-bit
counter addition, and finite `long double` validation. Implement heat-up as
Normal→Light at LTT and Light→Severe at STT; cooldown as Severe→Light at LTT
and Light→Normal at RTT; Shutdown never exits.

- [ ] **Step 4: Run GREEN for the pure model**

```bash
cmake --build build-thermal-baseline-gcc13 --target thermal_reliability_test -j2
ctest --test-dir build-thermal-baseline-gcc13 \
  -R '^thermal_reliability$' --output-on-failure
```

Expected: all thermal/reliability assertions pass.

- [ ] **Step 5: Commit the pure model**

```bash
git add include/hbfsim/thermal_reliability.hpp \
  src/thermal/thermal_reliability.cpp tests/cpu/thermal_reliability_test.cpp \
  CMakeLists.txt
git commit -m "feat: add deterministic thermal reliability model"
```

### Task 3: Control ABI v10 and Versioned Public Stats

**Files:**
- Modify: `src/host_service/control_layout.hpp`
- Modify: `src/cuda_runtime/device/hbf_device.cuh`
- Modify: `include/hbfsim/protocol.hpp`
- Modify: `include/hbfsim/api.h`
- Modify: `src/api.cpp`
- Modify: `src/cuda_runtime/context.cpp`
- Modify: `tests/cpu/protocol_layout_test.cpp`
- Modify: `tests/cpu/device_helper_abi_test.cpp`

- [ ] **Step 1: Write failing ABI assertions**

```cpp
static_assert(hbfsim::host_service::kControlAbiVersion == 10);
static_assert(offsetof(SharedControlHeader, thermal_generation) ==
              hbfsim::device::kThermalGenerationOffset);
assert(static_cast<std::uint32_t>(hbfsim::RequestStatus::ThermalShutdown) == 8u);
assert(hbfsim_abi_version() == 5u);
```

Include `hbfsim/api.h` in the public-ABI test. Add a public stats call test that
passes `struct_bytes`, reads Normal state, and rejects a short structure.

- [ ] **Step 2: Run RED**

```bash
cmake --build build-thermal-baseline-gcc13 \
  --target hbfsim_protocol_tests hbfsim_device_helper_abi_tests -j2
```

Expected: missing fields/status and old ABI versions.

- [ ] **Step 3: Add the generation-stamped shared fields**

Append 64-bit-aligned fields to both mirrored headers:

```cpp
alignas(8) std::uint64_t telemetry_generation;
std::uint64_t telemetry_host_ns;
std::int64_t telemetry_gpu_millic;
std::uint64_t telemetry_gpu_power_mw;
std::uint32_t telemetry_status;
std::uint32_t thermal_mode;
alignas(8) std::uint64_t thermal_generation;
std::int64_t thermal_junction_millic;
std::uint32_t thermal_service_ppm;
std::uint32_t thermal_admission_open;
alignas(8) std::uint64_t thermal_read_bytes;
std::uint64_t thermal_write_bytes;
std::uint64_t thermal_refresh_read_bytes;
std::uint64_t thermal_refresh_write_bytes;
alignas(8) std::uint64_t refresh_debt_bytes;
std::uint64_t refresh_debt_generation;
std::uint64_t thermal_transitions;
std::uint64_t thermal_inflight_completed;
std::uint64_t thermal_completed_refresh_blocks;
std::uint64_t thermal_max_pec;
std::uint64_t thermal_average_pec_millionths;
std::uint64_t thermal_refresh_claimed_bytes;
std::uint64_t thermal_refresh_background_drained_bytes;
std::uint64_t thermal_summary_generation;
std::uint32_t thermal_summary_status;
std::uint32_t reserved1;
```

Bump control ABI to 10 and public ABI to 5. Add
`RequestStatus::ThermalShutdown` and `HBFSIM_THERMAL_SHUTDOWN` without changing
the numeric values of existing states.

- [ ] **Step 4: Add the versioned stats structure and getter**

```c
typedef struct hbfsim_thermal_stats {
    uint32_t struct_bytes;
    uint32_t mode;
    int64_t junction_millic;
    uint32_t service_ppm;
    uint32_t telemetry_status;
    uint64_t transitions;
    uint64_t refresh_read_bytes;
    uint64_t refresh_write_bytes;
    uint64_t refresh_debt_bytes;
    uint64_t completed_refresh_blocks;
    uint64_t max_pec;
    uint64_t average_pec_millionths;
} hbfsim_thermal_stats;

int hbfsim_get_thermal_stats(hbfsim_context*, hbfsim_thermal_stats*);
```

The getter checks `struct_bytes`, holds `ContextOperation`, performs acquire
loads, and returns `HBFSIM_IO_ERROR` for an invalid mapping.

- [ ] **Step 5: Run GREEN**

```bash
cmake --build build-thermal-baseline-gcc13 \
  --target hbfsim_protocol_tests hbfsim_device_helper_abi_tests \
           context_lifecycle_test -j2
ctest --test-dir build-thermal-baseline-gcc13 \
  -R 'protocol|device_helper_abi|context_lifecycle' --output-on-failure
```

Expected: mirrored layout and lifecycle tests pass.

- [ ] **Step 6: Commit ABI v10**

```bash
git add src/host_service/control_layout.hpp \
  src/cuda_runtime/device/hbf_device.cuh include/hbfsim/protocol.hpp \
  include/hbfsim/api.h src/api.cpp src/cuda_runtime/context.cpp \
  tests/cpu/protocol_layout_test.cpp tests/cpu/device_helper_abi_test.cpp
git commit -m "feat: publish thermal state through control ABI v10"
```

### Task 4: Live/Trace Telemetry Publisher

**Files:**
- Create: `src/cuda_runtime/thermal_telemetry.hpp`
- Create: `src/cuda_runtime/thermal_telemetry.cpp`
- Modify: `src/cuda_runtime/exact_environment.cpp`
- Modify: `src/cuda_runtime/context.cpp`
- Create: `tests/cpu/thermal_telemetry_test.cpp`
- Modify: `CMakeLists.txt`

- [ ] **Step 1: Write failing injectable-source lifecycle tests**

Use a fake source returning two samples followed by failure:

```cpp
FakeThermalSource source({
    {.host_ns = 10, .gpu_millic = 45'000, .gpu_power_mw = 200'000},
    {.host_ns = 20, .gpu_millic = 46'000, .gpu_power_mw = 210'000},
});
ThermalTelemetryPublisher publisher(control, source, fake_wait);
check(publisher.publish_once() == TelemetryStatus::Ready,
      "first telemetry sample is ready");
check(control.header()->telemetry_generation == 1,
      "first sample publishes generation one");
check(publisher.publish_once() == TelemetryStatus::Ready,
      "second telemetry sample is ready");
check(control.header()->telemetry_generation == 2,
      "second sample publishes generation two");
check(publisher.publish_once() == TelemetryStatus::SourceFailed,
      "source exhaustion fails closed");
```

- [ ] **Step 2: Run RED**

```bash
cmake --build build-thermal-baseline-gcc13 --target thermal_telemetry_test -j2
```

Expected: telemetry types and target are missing.

- [ ] **Step 3: Implement reusable NVML sampling and sources**

Define:

```cpp
struct ThermalTelemetrySample {
    std::uint64_t host_ns;
    std::int64_t gpu_millic;
    std::uint64_t gpu_power_mw;
};

class ThermalTelemetrySource {
public:
    virtual ~ThermalTelemetrySource() = default;
    virtual std::optional<ThermalTelemetrySample> sample() noexcept = 0;
};
```

Refactor the existing dynamic NVML library boundary so exact admission and the
publisher share device lookup, temperature, and identity handling. Resolve
`nvmlDeviceGetPowerUsage`; a missing power symbol is a live-source failure, not
zero power. Implement constant and trace sources without NVML.

- [ ] **Step 4: Attach publisher lifecycle to context**

Start publication only after control initialization and CUDA device identity
validation. Stop and join before admission closes and before unmapping. Publish
terminal source failure with release ordering.

- [ ] **Step 5: Run GREEN**

```bash
cmake --build build-thermal-baseline-gcc13 \
  --target thermal_telemetry_test exact_environment_test context_lifecycle_test -j2
ctest --test-dir build-thermal-baseline-gcc13 \
  -R 'thermal_telemetry|exact_environment|context_lifecycle' \
  --output-on-failure
```

Expected: fake source, exact environment, and shutdown ordering pass.

- [ ] **Step 6: Commit telemetry**

```bash
git add src/cuda_runtime/thermal_telemetry.hpp \
  src/cuda_runtime/thermal_telemetry.cpp src/cuda_runtime/exact_environment.cpp \
  src/cuda_runtime/context.cpp tests/cpu/thermal_telemetry_test.cpp CMakeLists.txt
git commit -m "feat: publish live GPU thermal telemetry"
```

### Task 5: Host Thermal Controller and Durable Report

**Files:**
- Create: `src/host_service/thermal_controller.hpp`
- Create: `src/host_service/thermal_controller.cpp`
- Create: `include/hbfsim/thermal_report.hpp`
- Create: `src/reporting/thermal_report.cpp`
- Create: `tests/cpu/thermal_controller_test.cpp`
- Create: `tests/integration/test_thermal_report.py`
- Create: `configs/schema/thermal-reliability-summary.schema.json`
- Modify: `src/host_service/main.cpp`
- Modify: `CMakeLists.txt`

- [ ] **Step 1: Write failing fake-clock controller tests**

Use a test profile with `tau_seconds=0.001`, `gpu_coupling_ratio=1`, zero media
thermal resistance, and zero traffic energy. Drive 100 ms intervals across all
transitions, which places the modeled junction within numerical tolerance of
each source sample. Verify generation, service, stale telemetry, byte deltas,
and transition counters:

```cpp
controller.tick_at(100ms, sample(79'000), counters(0, 0));
check(snapshot().mode == ThermalMode::Normal, "starts Normal");
controller.tick_at(200ms, sample(85'000), counters(1ULL << 20, 0));
check(snapshot().mode == ThermalMode::Light, "LTT enters Light");
controller.tick_at(300ms, sample(95'000), counters(2ULL << 20, 0));
check(snapshot().mode == ThermalMode::Severe, "STT enters Severe");
check(snapshot().admission_open == 0, "Severe closes admission");
controller.tick_at(400ms, sample(79'000), counters(2ULL << 20, 0));
check(snapshot().mode == ThermalMode::Light, "LTT cooldown enters Light");
controller.tick_at(500ms, sample(77'000), counters(2ULL << 20, 0));
check(snapshot().mode == ThermalMode::Normal, "RTT cooldown enters Normal");
```

- [ ] **Step 2: Run RED**

```bash
cmake --build build-thermal-baseline-gcc13 --target thermal_controller_test -j2
```

Expected: controller target is absent.

- [ ] **Step 3: Implement controller tick and readiness**

`ThermalController::tick_at()` acquire-loads a stable telemetry generation,
computes counter deltas, calls the pure model, and release-publishes thermal
fields with generation last. It rejects stale samples, regressions, overflow,
and non-finite state. `hbfsimd` constructs it before the first heartbeat and
ticks it from the main loop.

- [ ] **Step 4: Implement canonical durable summary**

Write one JSON object atomically at clean or terminal shutdown:

```cpp
struct ThermalRunSummary {
    std::string profile_sha256;
    std::string temperature_source;
    long double reliability_time_acceleration;
    std::vector<ThermalTransition> transitions;
    ThermalAccounting accounting;
    std::vector<MtbfSensitivityPoint> mtbf;
    std::string terminal_status;
};

void write_thermal_summary(const std::filesystem::path&,
                           const ThermalRunSummary&);
```

Use create-temp, `fdatasync`, rename, and parent-directory `fsync`; do not
append partial JSON lines. Add the closed JSON schema.

- [ ] **Step 5: Run GREEN**

```bash
cmake --build build-thermal-baseline-gcc13 \
  --target thermal_controller_test hbfsimd -j2
ctest --test-dir build-thermal-baseline-gcc13 \
  -R 'thermal_controller|thermal_report' --output-on-failure
```

Expected: transition and report schema tests pass.

- [ ] **Step 6: Commit controller and report**

```bash
git add src/host_service/thermal_controller.hpp \
  src/host_service/thermal_controller.cpp include/hbfsim/thermal_report.hpp \
  src/reporting/thermal_report.cpp tests/cpu/thermal_controller_test.cpp \
  tests/integration/test_thermal_report.py \
  configs/schema/thermal-reliability-summary.schema.json \
  src/host_service/main.cpp CMakeLists.txt
git commit -m "feat: run and report the HBF thermal controller"
```

### Task 6: Device Admission and Thermal Service Scaling

**Files:**
- Modify: `src/cuda_runtime/device/hbf_device.cuh`
- Modify: `src/cuda_runtime/device/hbf_device.cu`
- Create: `tests/cpu/thermal_device_reference_test.cpp`
- Modify: `tests/cpu/device_future_reference_test.cpp`
- Modify: `tests/cpu/tma_async_test.cpp`
- Modify: `CMakeLists.txt`

- [ ] **Step 1: Write failing host/device-reference tests**

```cpp
check(scale_thermal_service_ns(10'000, 1'000'000) == 10'000,
      "Normal service does not scale");
check(scale_thermal_service_ns(10'000, 900'000) == 11'112,
      "Light service uses ceiling division");
check(thermal_admission(ThermalMode::Severe, false) ==
          ThermalAdmission::Wait,
      "Severe waits before reservation");
check(thermal_admission(ThermalMode::Shutdown, false) ==
          ThermalAdmission::Shutdown,
      "Shutdown terminates new requests");
```

Future and TMA tests must prove issue before Severe still drains exactly once,
while issue after Severe waits without incrementing request producer.

- [ ] **Step 2: Run RED**

```bash
cmake --build build-thermal-baseline-gcc13 \
  --target thermal_device_reference_test device_future_reference_test \
           tma_async_test -j2
```

Expected: thermal device helpers are absent.

- [ ] **Step 3: Add snapshot, admission, and scaling helpers**

Implement a seqlock-style snapshot: read generation, fields, generation again;
retry if odd or changed. Scale latency with ceiling division:

```cpp
scaled = ceil(base_ns * 1'000'000 / service_ppm);
```

Before synchronous resolver reservation, future issue, and TMA issue, call the
same thermal admission helper. Severe polls thermal generation plus existing
heartbeat/deadline without reserving a ring slot. Shutdown returns
`ThermalShutdown`. Requests admitted before Severe use their snapshotted
service and increment `thermal_inflight_completed` on terminal completion.

- [ ] **Step 4: Apply service to every timing path**

Scale empirical, scalar fast, SM120 channel, reference completion, future, and
TMA service exactly once. Add application read/write bytes once per modeled HBF
operation after range classification; mixed TensorMap operations count only HBF
segments.

- [ ] **Step 5: Run GREEN and assemble PTX**

```bash
cmake --build build-thermal-baseline-gcc13 \
  --target thermal_device_reference_test device_future_reference_test \
           tma_async_test hbfsim_device_ptx -j2
ctest --test-dir build-thermal-baseline-gcc13 \
  -R 'thermal_device_reference|device_future_reference|tma_async|device_helper_ptx' \
  --output-on-failure
```

Expected: helpers pass and CUDA 13 assembles the embedded `sm_120` PTX.

- [ ] **Step 6: Commit device thermal behavior**

```bash
git add src/cuda_runtime/device/hbf_device.cuh \
  src/cuda_runtime/device/hbf_device.cu \
  tests/cpu/thermal_device_reference_test.cpp \
  tests/cpu/device_future_reference_test.cpp tests/cpu/tma_async_test.cpp \
  CMakeLists.txt
git commit -m "feat: apply thermal admission and service on device"
```

### Task 7: Zone Aging and Deterministic Refresh Planner

**Files:**
- Create: `src/host_service/refresh_scheduler.hpp`
- Create: `src/host_service/refresh_scheduler.cpp`
- Create: `tests/cpu/refresh_scheduler_test.cpp`
- Modify: `src/host_service/thermal_controller.cpp`
- Modify: `CMakeLists.txt`

- [ ] **Step 1: Write failing eligibility and order tests**

Create four zones across two channels/dies, age them at different epochs, and
assert this exact order: eligibility epoch, channel round-robin, die
round-robin, zone, block, page, read before write. Assert no application read
and refresh action share a die/instant.

```cpp
const auto plan = scheduler.plan(now, application_reads);
check(plan[0] == action(RefreshRead, channel0, die0, block2, page0),
      "oldest eligible page reads first");
check(plan[1] == action(RefreshWrite, channel0, die0, block2, page0),
      "matching rewrite follows read");
check(plan[2].channel == 1, "channel round robin advances");
check(no_same_die_overlap(plan, application_reads),
      "refresh never overlaps an application read on the same die");
```

- [ ] **Step 2: Run RED**

```bash
cmake --build build-thermal-baseline-gcc13 --target refresh_scheduler_test -j2
```

Expected: refresh scheduler target is absent.

- [ ] **Step 3: Implement host-owned zone and block state**

Map registered ranges to channel/die/block/zone with checked profile geometry.
Track valid state, damage, read disturb, eligibility epoch, per-block refresh
progress, and PEC. Mark zones valid at range publication or first completed
program according to the profile.

- [ ] **Step 4: Implement deterministic quanta and commit rules**

Generate page-aligned read/write pairs. A full block commits only after every
quantum completes successfully and in order. On failure, retain damage,
read-disturb count, and PEC; clear only queued/in-flight markers so the terminal
report is accurate.

- [ ] **Step 5: Run GREEN**

```bash
cmake --build build-thermal-baseline-gcc13 --target refresh_scheduler_test -j2
ctest --test-dir build-thermal-baseline-gcc13 \
  -R '^refresh_scheduler$' --output-on-failure
```

Expected: ordering, overlap, failure rollback, and PEC tests pass.

- [ ] **Step 6: Commit planner**

```bash
git add src/host_service/refresh_scheduler.hpp \
  src/host_service/refresh_scheduler.cpp tests/cpu/refresh_scheduler_test.cpp \
  src/host_service/thermal_controller.cpp CMakeLists.txt
git commit -m "feat: plan deterministic HBF refresh work"
```

### Task 8: Reference MQSim Background Refresh

**Files:**
- Modify: `src/host_service/request_dispatcher.hpp`
- Modify: `src/host_service/request_dispatcher.cpp`
- Modify: `src/host_service/main.cpp`
- Modify: `src/mqsim_adapter/mqsim_online.cpp`
- Modify: `tests/integration/daemon_protocol_test.cpp`
- Modify: `tests/integration/mqsim_online_test.cpp`

- [ ] **Step 1: Write failing background-completion tests**

Queue one application read and one refresh read/write pair. Return the refresh
completion first and prove it updates only background state; the application
completion must still publish to its original ticket.

```cpp
dispatcher.enqueue_background(refresh_read);
dispatcher.enqueue_background(refresh_write);
check(dispatcher.poll_once(), "background completion is consumed");
check(refresh.completed_blocks == 1, "refresh block commits");
check(application_completion.request_id == original.request_id,
      "foreground ticket identity is preserved");
check(control.header()->thermal_refresh_write_bytes == block_bytes,
      "refresh write bytes are accounted");
```

- [ ] **Step 2: Run RED**

```bash
cmake --build build-thermal-baseline-gcc13 \
  --target daemon_protocol_test mqsim_online_test -j2
ctest --test-dir build-thermal-baseline-gcc13 \
  -R 'daemon_protocol|mqsim_online' --output-on-failure
```

Expected: background enqueue interface is absent.

- [ ] **Step 3: Add a disjoint background ID namespace**

Reserve IDs with bit 63 set for background work and cap foreground engine IDs
below that bit. Map background IDs to `RefreshAction`; never insert them into
`ticket_by_engine_id_`. The completion loop consumes background completions,
advances the refresh scheduler, and continues until it finds a foreground
completion or no engine event remains.

- [ ] **Step 4: Submit refresh to the same MQSim engine**

Use ordinary aligned `HbfRequest` media descriptors with an internal refresh
flag. Keep MQSim base latencies unchanged. Apply host thermal service scaling
outside MQSim and enforce no-same-die overlap before submission.

- [ ] **Step 5: Run GREEN**

```bash
cmake --build build-thermal-baseline-gcc13 \
  --target daemon_protocol_test mqsim_online_test hbfsimd -j2
ctest --test-dir build-thermal-baseline-gcc13 \
  -R 'daemon_protocol|mqsim_online' --output-on-failure
```

Expected: background and foreground completions remain paired and MQSim tests pass.

- [ ] **Step 6: Commit reference refresh**

```bash
git add src/host_service/request_dispatcher.hpp \
  src/host_service/request_dispatcher.cpp src/host_service/main.cpp \
  src/mqsim_adapter/mqsim_online.cpp tests/integration/daemon_protocol_test.cpp \
  tests/integration/mqsim_online_test.cpp
git commit -m "feat: inject refresh work into the MQSim reference path"
```

### Task 9: Fast Refresh Debt and Cross-Path Consistency

**Files:**
- Modify: `src/host_service/refresh_scheduler.cpp`
- Modify: `src/host_service/thermal_controller.cpp`
- Modify: `src/cuda_runtime/device/hbf_device.cuh`
- Modify: `src/cuda_runtime/device/hbf_device.cu`
- Modify: `tests/cpu/thermal_device_reference_test.cpp`
- Create: `tests/cpu/thermal_consistency_test.cpp`
- Modify: `CMakeLists.txt`

- [ ] **Step 1: Write failing debt and consistency tests**

```cpp
check(claim_refresh_debt(12ULL << 10, 4ULL << 10).claimed == (4ULL << 10),
      "debt claim is quantum bounded");
check(claim_refresh_debt(2ULL << 10, 4ULL << 10).claimed == (2ULL << 10),
      "short debt claim consumes the remainder");
check(decay_refresh_debt(8ULL << 10, std::chrono::milliseconds{1}, bandwidth)
              .remaining <= (8ULL << 10),
      "host decay never increases debt");

const auto reference = run_reference_trace(trace, telemetry, fake_clock);
const auto fast = run_fast_trace(trace, telemetry, fake_clock);
check(reference.refresh_order == fast.refresh_order,
      "reference and fast refresh order agree");
check(reference.refresh_bytes == fast.refresh_bytes,
      "reference and fast refresh bytes agree");
check(reference.pec == fast.pec, "reference and fast PEC agree");
check(close_ld(reference.final_damage, fast.final_damage, 1e-9L),
      "reference and fast final damage agree");
```

Run each harness once with refresh disabled and once with the same application
trace plus an eligible refresh. Check that refresh increases application
completion latency in both the MQSim reference path and the fast debt path.

- [ ] **Step 2: Run RED**

```bash
cmake --build build-thermal-baseline-gcc13 \
  --target thermal_device_reference_test thermal_consistency_test -j2
```

Expected: debt helpers and consistency harness are absent.

- [ ] **Step 3: Publish and decay relative debt**

The host uses a CAS loop to add planned refresh bytes and subtract bytes that
would finish during elapsed host time at current service capacity. It publishes
generation after debt. No host timestamp is written into a GPU queue tail.

- [ ] **Step 4: Claim bounded debt on device**

At fast admission, claim at most `refresh_quantum_bytes`, convert bytes to
thermally scaled service, and reserve it before application work on the same
modeled channel. Count claimed debt separately from background-decayed debt.

- [ ] **Step 5: Run GREEN**

```bash
cmake --build build-thermal-baseline-gcc13 \
  --target thermal_device_reference_test thermal_consistency_test \
           hbfsim_device_ptx -j2
ctest --test-dir build-thermal-baseline-gcc13 \
  -R 'thermal_device_reference|thermal_consistency|device_helper_ptx' \
  --output-on-failure
```

Expected: exact plan/accounting agreement and bounded timing tolerance pass.

- [ ] **Step 6: Commit fast debt**

```bash
git add src/host_service/refresh_scheduler.cpp \
  src/host_service/thermal_controller.cpp \
  src/cuda_runtime/device/hbf_device.cuh \
  src/cuda_runtime/device/hbf_device.cu \
  tests/cpu/thermal_device_reference_test.cpp \
  tests/cpu/thermal_consistency_test.cpp CMakeLists.txt
git commit -m "feat: model refresh contention on the fast path"
```

### Task 10: Capacity Integrity, Severe Lifecycle, and Reporting Integration

**Files:**
- Create: `tests/integration/thermal_daemon_test.cpp`
- Create: `tests/integration/thermal_capacity_test.cpp`
- Modify: `src/cuda_runtime/capacity_runtime.cpp`
- Modify: `src/host_service/capacity_page_service.cpp`
- Modify: `src/cuda_runtime/context.cpp`
- Modify: `tests/integration/test_thermal_report.py`
- Modify: `CMakeLists.txt`

- [ ] **Step 1: Write failing process and backing-byte tests**

The daemon test holds temperature above STT, submits without waiting, verifies
producer sequence does not change, cools below LTT then RTT, and proves the same
request completes. It then drives Shutdown and checks
`HBFSIM_THERMAL_SHUTDOWN` rather than daemon loss.

The capacity test hashes the sparse backing file and device-visible checksum,
forces one complete refresh, and asserts both hashes are unchanged while PEC
and refresh bytes increase. A second assertion performs one application program
and then one refresh rewrite, proving each successful full-block program adds
exactly one PEC while a read never changes PEC.

- [ ] **Step 2: Run RED**

```bash
cmake --build build-thermal-baseline-gcc13 \
  --target thermal_daemon_test thermal_capacity_test -j2
```

Expected: new integration targets are absent.

- [ ] **Step 3: Connect lifecycle and capacity accounting**

Publish range-valid state to the controller after range publication. Feed
complete capacity programs to block lifecycle accounting, but keep refresh as
media work that reads and rewrites identical bytes. Stop telemetry, drain
foreground work, finalize background state, write the report, then unmap.

- [ ] **Step 4: Verify RED becomes GREEN**

```bash
cmake --build build-thermal-baseline-gcc13 \
  --target thermal_daemon_test thermal_capacity_test hbfsimd hbfsim -j2
ctest --test-dir build-thermal-baseline-gcc13 \
  -R 'thermal_daemon|thermal_capacity|thermal_report' --output-on-failure
```

Expected: backpressure/recovery, Shutdown, byte integrity, and report tests pass.

- [ ] **Step 5: Run the complete offline regression**

```bash
ctest --test-dir build-thermal-baseline-gcc13 --output-on-failure \
  -E 'sm120_calibration_cases|live_sm120_environment|sm120_future_live|sm120_tma_live' \
  -j2
```

Expected: 88 baseline tests plus all new offline tests pass with zero failures.

- [ ] **Step 6: Commit integration**

```bash
git add tests/integration/thermal_daemon_test.cpp \
  tests/integration/thermal_capacity_test.cpp \
  src/cuda_runtime/capacity_runtime.cpp \
  src/host_service/capacity_page_service.cpp src/cuda_runtime/context.cpp \
  tests/integration/test_thermal_report.py CMakeLists.txt
git commit -m "feat: close the thermal refresh lifecycle"
```

### Task 11: Live Gate and Proof Artifact

**Files:**
- Create: `tests/gpu/thermal_timing_live.cu`
- Create: `tests/integration/test_thermal_timing_live.py`
- Create: `docs/proofs/2026-08-16-thermal-reliability.md`
- Modify: `CMakeLists.txt`

- [ ] **Step 1: Add a live Normal/Light timing and checksum test**

The test uses a simulated temperature source so it does not heat hardware. It
runs identical HBF reads under 1,000,000 and 900,000 ppm, checks Light median
latency is at least 1.10x Normal within the timer tolerance, and compares every
output byte.

- [ ] **Step 2: Build and run the live test when memory is available**

```bash
nvidia-smi --query-compute-apps=pid,process_name,used_gpu_memory \
  --format=csv,noheader,nounits
cmake --build build-thermal-baseline-gcc13 --target thermal_timing_live -j2
ctest --test-dir build-thermal-baseline-gcc13 \
  -R '^thermal_timing_live$' --output-on-failure
```

Expected when sufficient memory is free: PASS with identical checksum and
Light/Normal latency ratio at or above 1.10. If an external process still owns
the required memory, record its PID and memory use and leave this gate blocked.

- [ ] **Step 3: Run available SM120 and adapter regressions**

```bash
ctest --test-dir build-thermal-baseline-gcc13 \
  -R 'sm120_future_live|live_sm120_environment|thermal_timing_live' \
  --output-on-failure
python3 -m pytest -q adapters/vllm/tests
```

Expected: available live tests and vLLM tests pass; environment blockers are
recorded exactly.

- [ ] **Step 4: Write the proof record**

Record commit, compiler versions, commands, test counts, checksums, timing
ratio, refresh bytes, PEC, summary hash, and every blocked live gate in
`docs/proofs/2026-08-16-thermal-reliability.md`.

- [ ] **Step 5: Commit live test and evidence**

```bash
git add tests/gpu/thermal_timing_live.cu \
  tests/integration/test_thermal_timing_live.py \
  docs/proofs/2026-08-16-thermal-reliability.md CMakeLists.txt
git commit -m "test: prove the thermal reliability loop"
```

### Task 12: FAST Paper Design Section and Architecture Figure

**Files:**
- Modify: `/root/hbfsim/HBFSim/6a7bdca65b0b3f3b7f676b14/design.tex`
- Create: `/root/hbfsim/HBFSim/6a7bdca65b0b3f3b7f676b14/figures/hbfsim-design.pdf`
- Modify: `/root/hbfsim/HBFSim/6a7bdca65b0b3f3b7f676b14/main.tex` only if the figure requires an already-approved package

- [ ] **Step 1: Write D1 and compile to expose missing labels/citations**

D1 covers explicit 32,768-entry ranges, automatic PTX/TMA rewriting, digest
binding, coverage outcomes, fail-closed capacity admission, and deterministic
capacity paging. It must state that cubin-only modules are refused and that
instrumentation preserves data, not private physical pipeline identity.

Run:

```bash
cd /root/hbfsim/HBFSim/6a7bdca65b0b3f3b7f676b14
pdflatex -interaction=nonstopmode -halt-on-error main.tex
```

Expected before all subsections exist: LaTeX succeeds or reports only the
specific unresolved labels introduced by D1.

- [ ] **Step 2: Write D2 with equations and accounting**

Include the six cumulative breakpoints, piecewise interpolation equation,
online MQSim boundary, 1,024-request warmup plus deterministic 1% sampling,
20.8x fast/reference difference, and modeled/wall/service/overhead separation.
State that the measured CD8P-vmem curve is a software-path calibration, not HBF
media measurement.

- [ ] **Step 3: Write implemented D3**

Present the exact causal chain implemented by Tasks 1--11: GPU telemetry and
traffic → RC junction temperature → Normal/Light/Severe/Shutdown → retention
damage → refresh read/rewrite → bandwidth, PEC, and heat feedback. Include the
retention and hazard equations from the approved spec and label MTBF as a
parameter sweep.

- [ ] **Step 4: Generate the double-column architecture figure**

Draw device, shared ABI, and host regions with only these arrows: PTX/TMA issue,
telemetry/counters, thermal snapshot, reference request/completion, refresh
plan, MQSim, and fast refresh debt. Export a vector PDF and include it with a
caption that defines the feedback loop.

- [ ] **Step 5: Compile four passes and inspect warnings/pages**

```bash
pdflatex -interaction=nonstopmode -halt-on-error main.tex
bibtex main
pdflatex -interaction=nonstopmode -halt-on-error main.tex
pdflatex -interaction=nonstopmode -halt-on-error main.tex
rg -n 'LaTeX Warning|undefined|Overfull|Underfull' main.log
pdfinfo main.pdf | rg '^Pages:'
```

Expected: no undefined citations/references, no fatal overfull boxes, and the
paper remains within the 12-page body target after later incomplete sections
are accounted for explicitly.

- [ ] **Step 6: Commit the Overleaf paper**

```bash
git add design.tex figures/hbfsim-design.pdf main.tex
git commit -m "paper: write the HBFSim design section"
```

### Task 13: Final Verification, Review, and Push

**Files:**
- Modify only files required by review findings

- [ ] **Step 1: Run static and full offline gates**

```bash
cmake --build build-thermal-baseline-gcc13 -j2
ctest --test-dir build-thermal-baseline-gcc13 --output-on-failure \
  -E 'sm120_calibration_cases|live_sm120_environment|sm120_future_live|sm120_tma_live' \
  -j2
python3 -m compileall -q scripts tests adapters
python3 -m ruff check scripts tests adapters
bash -n scripts/*.sh adapters/*/*.sh
git diff --check
```

Expected: build and all offline tests pass; Python, Ruff, shell, and whitespace
checks exit 0.

- [ ] **Step 2: Use verification-before-completion and request code review**

Review the implementation against every numbered success criterion in the
design spec. Fix correctness findings with a new failing test before changing
production code, then rerun the affected and full gates.

- [ ] **Step 3: Verify clean commit history and remote targets**

```bash
git status -sb
git log --oneline --decorate feature/sm120-exact-stage1..HEAD
git remote -v
git -C /root/hbfsim/HBFSim/6a7bdca65b0b3f3b7f676b14 status -sb
git -C /root/hbfsim/HBFSim/6a7bdca65b0b3f3b7f676b14 remote -v
```

Expected: only intended commits, clean worktrees, HBFSim GitHub origin, and
Overleaf origin.

- [ ] **Step 4: Push both branches**

```bash
git push -u origin feature/thermal-reliability
git -C /root/hbfsim/HBFSim/6a7bdca65b0b3f3b7f676b14 push origin main
```

Expected: both pushes succeed and local/remote divergence is `0 0`.

- [ ] **Step 5: Report proof boundaries**

Report the HBFSim commit, Overleaf commit, test counts, paper PDF path/page
count, output/checksum evidence, thermal/refresh/PEC/MTBF artifact paths, and
any live gate still blocked by external GPU occupancy. Do not call a blocked
live gate passed.
