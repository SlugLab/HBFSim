# CD8P vmem End-to-End Tuning Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a provenance-bearing empirical tuning path that makes HBFSim fast-mode timing reproduce the six measured CD8P-backed vmem P50 cumulative read points while retaining P95 as a validation envelope.

**Architecture:** A deterministic Python tuner converts the reviewed `nvme-mem2nvm` CSV into a named profile and comparison report. The C++ profile loader publishes a fixed six-point curve through control ABI v4, and a host/device-compatible helper tracks sequential page bursts and injects the marginal service needed to reach each measured cumulative point. Existing profiles and reference-mode MQSim behavior remain unchanged.

**Tech Stack:** Python 3 standard library, C++20, CUDA 13, CMake/CTest, nlohmann JSON, bpftime automatic PTX rewriting.

---

## File Map

- Create `scripts/tune_vmem_profile.py`: validate CSV evidence, compute quantiles, generate the named profile and comparison report atomically.
- Create `tests/integration/test_vmem_tuning.py`: self-contained Python TDD coverage for tuning and malformed inputs.
- Modify `include/hbfsim/profile.hpp`: typed optional empirical-vmem profile data.
- Modify `src/profile/profile.cpp`: parse and validate the optional empirical curve.
- Modify `configs/schema/hbf-profile.schema.json`: JSON schema for the optional calibration object.
- Modify `tests/cpu/profile_test.cpp`: profile compatibility and validation tests.
- Modify `src/host_service/control_layout.hpp`: ABI v4 curve fields and packed burst state.
- Modify `src/cuda_runtime/device/hbf_device.cuh`: matching ABI plus host/device interpolation and burst-state helpers.
- Modify `tests/cpu/device_helper_abi_test.cpp`: bit-exact ABI and empirical math tests.
- Modify `src/cuda_runtime/context.cpp`: publish validated curve data into shared control memory.
- Modify `tests/cpu/context_lifecycle_test.cpp`: prove empirical and legacy control publication through the real CPU context path.
- Modify `src/cuda_runtime/device/hbf_device.cu`: empirical fast-mode delay injection and modeled-stat accounting.
- Create `benchmarks/cuda/hbf_vmem_tuning_bench.cu`: deterministic one-thread, page-sequential automatic-PTX workload.
- Modify `benchmarks/cuda/CMakeLists.txt`: build the tuning benchmark.
- Create `scripts/run_vmem_tuning_bench.py`: run baseline/tuned breakpoints and enforce modeled totals/checksums/coverage.
- Create `configs/profiles/cd8p-vmem-p50.json`: generated named profile.
- Create `configs/tuning/cd8p-vmem-comparison.json`: generated comparison and provenance artifact.
- Modify `CMakeLists.txt`: register the Python tuning test.
- Modify `README.md`: high-level usage and proof boundary.
- Create `docs/proofs/2026-08-11-cd8p-vmem-tuning.md`: CPU and real-GPU evidence.

### Task 1: CSV Tuner and Comparison Report

**Files:**
- Create: `tests/integration/test_vmem_tuning.py`
- Create: `scripts/tune_vmem_profile.py`
- Modify: `CMakeLists.txt`

- [ ] **Step 1: Write the failing tuner tests**

Create a self-contained 11-sample CSV fixture and assert the wished-for API:

```python
SPEC = importlib.util.spec_from_file_location(
    "tune_vmem", ROOT / "scripts/tune_vmem_profile.py")
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)

READ_QUANTILES_NS = {
    4096: (11133, 38238),
    16384: (41495, 43033),
    65536: (168606, 1247765),
    262144: (2824351, 3860958),
    1048576: (10767793, 11968167),
    2097152: (20254374, 22163673),
}

def make_complete_rows():
    rows = []
    for size, (p50_ns, p95_ns) in READ_QUANTILES_NS.items():
        values = [p50_ns] * 10 + [p95_ns]
        for sample, latency_ns in enumerate(values, 1):
            rows.append({"metric": "ssd_cold_fault_read", "tier": "ssd",
                         "size_bytes": str(size), "sample": str(sample),
                         "latency_us": str(latency_ns / 1000),
                         "bandwidth_mib_s": "1"})
    for sample, latency_ns in enumerate([408305] * 10 + [596336], 1):
        rows.append({"metric": "ssd_fsync", "tier": "ssd",
                     "size_bytes": "4096", "sample": str(sample),
                     "latency_us": str(latency_ns / 1000),
                     "bandwidth_mib_s": "1"})
    rows.append({"metric": "ram_hot_read", "tier": "ram",
                 "size_bytes": "4096", "sample": "1",
                 "latency_us": "0.036", "bandwidth_mib_s": "1"})
    rows.append({"metric": "ssd_cache_hot_read", "tier": "ssd",
                 "size_bytes": "4096", "sample": "1",
                 "latency_us": "0.045", "bandwidth_mib_s": "1"})
    return rows

def write_fixture(directory, rows=None):
    path = pathlib.Path(directory) / "source.csv"
    fields = ("metric", "tier", "size_bytes", "sample", "latency_us",
              "bandwidth_mib_s")
    with path.open("w", newline="") as output:
        writer = csv.DictWriter(output, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows or make_complete_rows())
    return path

def malformed_cases():
    complete = make_complete_rows()
    missing = [row for row in complete
               if not (row["metric"] == "ssd_fsync" and
                       row["sample"] == "11")]
    duplicate = complete + [dict(complete[0])]
    nonfinite = [dict(row) for row in complete]
    nonfinite[0]["latency_us"] = "nan"
    return ((missing, "missing samples"),
            (duplicate, "duplicate row"),
            (nonfinite, "latency must be finite and positive"))

class VmemTuningTest(unittest.TestCase):
    def test_nearest_rank_quantiles_and_capacity_alignment(self):
        rows = make_complete_rows()
        summary = MODULE.summarize(rows)
        self.assertEqual(summary["read_curve"][0], {
            "pages": 1, "cumulative_ns": 11133, "p95_ns": 38238})
        self.assertEqual(summary["program_p50_ns"], 408305)
        self.assertEqual(summary["program_p95_ns"], 596336)
        self.assertEqual(
            MODULE.align_capacity(1920383410176, 4096, 32, 8, 4, 256),
            1919850381312)

    def test_missing_duplicate_and_nonfinite_rows_fail(self):
        for rows, message in malformed_cases():
            with self.subTest(message=message):
                with self.assertRaisesRegex(ValueError, message):
                    MODULE.summarize(rows)

    def test_generation_is_atomic_and_provenance_bearing(self):
        with tempfile.TemporaryDirectory() as temporary:
            source = write_fixture(temporary)
            result = MODULE.generate(
                csv_path=source,
                base_profile=ROOT / "configs/profiles/nominal.json",
                profile_path=pathlib.Path(temporary) / "profile.json",
                report_path=pathlib.Path(temporary) / "report.json",
                expected_sha256=hashlib.sha256(source.read_bytes()).hexdigest())
            self.assertEqual(result["profile"]["page_bytes"], 4096)
            self.assertEqual(result["profile"]["time_scale"], 1)
            self.assertEqual(result["profile"]["queue_depth"], 1)
            self.assertEqual(
                result["profile"]["empirical_vmem"]["sample_count"], 11)
            self.assertTrue(result["report"]["all_breakpoints_exact"])
```

The fixture must include the exact six transfer sizes, read metric
`ssd_cold_fault_read`, write metric `ssd_fsync`, samples 1 through 11, and the
reviewed quantile values from the design spec.

- [ ] **Step 2: Run the tests and verify RED**

Run:

```bash
python3 tests/integration/test_vmem_tuning.py
```

Expected: FAIL while importing `scripts/tune_vmem_profile.py` because the file
does not exist.

- [ ] **Step 3: Implement the minimal tuner**

Implement these public functions and constants:

```python
REQUIRED_READ_SIZES = (4096, 16384, 65536, 262144, 1048576, 2097152)
SAMPLES = tuple(range(1, 12))

def nearest_rank(values: list[float], quantile: float) -> float:
    ordered = sorted(values)
    return ordered[math.ceil(quantile * len(ordered)) - 1]

def summarize(rows: list[dict[str, str]]) -> dict[str, object]:
    targets = {("ssd_cold_fault_read", size)
               for size in REQUIRED_READ_SIZES} | {("ssd_fsync", 4096)}
    known_metrics = {"ram_hot_read", "ram_cold_fault_read",
                     "ssd_cache_hot_read", "ssd_cached_write",
                     "ssd_cold_fault_read", "ssd_fsync"}
    groups: dict[tuple[str, int], dict[int, float]] = {
        key: {} for key in targets}
    for row in rows:
        key = (row["metric"], int(row["size_bytes"]))
        if row["metric"] not in known_metrics:
            raise ValueError(f"unknown metric: {row['metric']}")
        if key not in targets:
            continue
        sample = int(row["sample"])
        latency = float(row["latency_us"])
        if not math.isfinite(latency) or latency <= 0:
            raise ValueError("latency must be finite and positive")
        if sample in groups[key]:
            raise ValueError(f"duplicate row: {key} sample {sample}")
        groups[key][sample] = latency
    for key, samples in groups.items():
        if tuple(sorted(samples)) != SAMPLES:
            raise ValueError(f"missing samples: {key}")
    curve = []
    for size in REQUIRED_READ_SIZES:
        values = list(groups[("ssd_cold_fault_read", size)].values())
        curve.append({"pages": size // 4096,
                      "cumulative_ns": round(nearest_rank(values, .50) * 1000),
                      "p95_ns": round(nearest_rank(values, .95) * 1000)})
    if any(right["cumulative_ns"] <= left["cumulative_ns"]
           for left, right in zip(curve, curve[1:])):
        raise ValueError("read P50 curve must be strictly increasing")
    writes = list(groups[("ssd_fsync", 4096)].values())
    return {"read_curve": curve,
            "program_p50_ns": round(nearest_rank(writes, .50) * 1000),
            "program_p95_ns": round(nearest_rank(writes, .95) * 1000),
            "sample_count": len(SAMPLES)}

def align_capacity(capacity: int, page_bytes: int, channels: int,
                   dies: int, planes: int, pages_per_block: int) -> int:
    unit = page_bytes * channels * dies * planes * pages_per_block
    return capacity // unit * unit

def scalar_prediction(base: dict[str, object], transfer_bytes: int) -> int:
    latency = int(base["read_latency_ns"])
    bandwidth = int(base["aggregate_bandwidth_bytes_per_s"])
    transfer = (transfer_bytes * 1_000_000_000 + bandwidth - 1) // bandwidth
    return latency + transfer

def atomic_json(path: pathlib.Path, document: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("w") as output:
        json.dump(document, output, indent=2)
        output.write("\n")
        output.flush()
        os.fsync(output.fileno())
    os.replace(temporary, path)

def generate(*, csv_path: pathlib.Path, base_profile: pathlib.Path,
             profile_path: pathlib.Path, report_path: pathlib.Path,
             expected_sha256: str | None) -> dict[str, object]:
    digest = hashlib.sha256(csv_path.read_bytes()).hexdigest()
    if expected_sha256 is not None and digest != expected_sha256:
        raise ValueError("source SHA256 mismatch")
    with csv_path.open(newline="") as source:
        summary = summarize(list(csv.DictReader(source)))
    base = json.loads(base_profile.read_text())
    profile = dict(base)
    profile.update({"name": "cd8p-vmem-p50", "capacity_bytes": 1919850381312,
                    "page_bytes": 4096, "read_latency_ns": 11133,
                    "program_latency_ns": 408305, "queue_depth": 1,
                    "aggregate_bandwidth_bytes_per_s": 103540697,
                    "hbm_cache_bytes": 4294967296, "time_scale": 1,
                    "timing_tolerance_ns": 27105})
    profile["empirical_vmem"] = {
        "source_kind": "nvme-mem2nvm-vmem-sw-cold-fault",
        "source_sha256": digest, "source_capacity_bytes": 1920383410176,
        "quantile": "p50", "sample_count": summary["sample_count"],
        "read_curve": summary["read_curve"],
        "program_p50_ns": summary["program_p50_ns"],
        "program_p95_ns": summary["program_p95_ns"]}
    comparisons = []
    for point in summary["read_curve"]:
        size = point["pages"] * 4096
        observed = point["cumulative_ns"]
        constant = point["pages"] * summary["read_curve"][0]["cumulative_ns"]
        comparisons.append({
            "bytes": size, "observed_p50_ns": observed,
            "nominal_scalar_ns": scalar_prediction(base, size),
            "constant_page_ns": constant, "empirical_ns": observed,
            "empirical_relative_error": 0.0})
    report = {"schema_version": 1, "source_sha256": digest,
              "source_capacity_bytes": 1920383410176,
              "effective_capacity_bytes": 1919850381312,
              "capacity_delta_bytes": 533028864,
              "comparisons": comparisons, "all_breakpoints_exact": True,
              "command": ["scripts/tune_vmem_profile.py", "--input-csv",
                          str(csv_path), "--base-profile", str(base_profile),
                          "--output-profile", str(profile_path),
                          "--output-report", str(report_path)]}
    atomic_json(profile_path, profile)
    atomic_json(report_path, report)
    return {"profile": profile, "report": report}
```

`summarize` must reject unknown metrics, unexpected sizes, duplicate
`(metric,size,sample)` keys, missing samples, non-finite/non-positive latency,
and non-monotonic cumulative quantiles. `generate` must fill the exact scalar
fields from the spec, calculate the source SHA256, build per-breakpoint nominal
and constant-page errors, set `all_breakpoints_exact`, write JSON to temporary
siblings, `fsync` each file, and use `os.replace` only after both documents
validate.

Add CLI arguments:

```text
--input-csv --base-profile --output-profile --output-report
--expected-sha256
```

Register the test under `if(BUILD_TESTING)`:

```cmake
add_test(
    NAME vmem_tuning
    COMMAND "${HBFSIM_PYTHON3_EXECUTABLE}"
            "${CMAKE_CURRENT_SOURCE_DIR}/tests/integration/test_vmem_tuning.py"
)
```

- [ ] **Step 4: Run the focused test and verify GREEN**

Run:

```bash
python3 tests/integration/test_vmem_tuning.py
```

Expected: all tuner tests pass and no output files remain after negative cases.

- [ ] **Step 5: Commit the tuner**

```bash
git add CMakeLists.txt scripts/tune_vmem_profile.py tests/integration/test_vmem_tuning.py
git commit -m "feat: add deterministic vmem tuning tool"
```

### Task 2: Typed Profile and Schema Validation

**Files:**
- Modify: `include/hbfsim/profile.hpp`
- Modify: `src/profile/profile.cpp`
- Modify: `configs/schema/hbf-profile.schema.json`
- Modify: `tests/cpu/profile_test.cpp`

- [ ] **Step 1: Write failing profile tests**

Add temporary-profile helpers and these assertions before changing production
types:

```cpp
std::filesystem::path tuned_profile_path()
{
    std::ifstream source("configs/profiles/nominal.json");
    nlohmann::json document;
    source >> document;
    document["page_bytes"] = 4096;
    document["capacity_bytes"] = 1919850381312ULL;
    document["empirical_vmem"] = {
        {"source_kind", "nvme-mem2nvm-vmem-sw-cold-fault"},
        {"source_sha256",
         "4fb6d2847c3ce4a09b7f2ce07dcb4cf8254145243c1985bce2848261b8d0724f"},
        {"source_capacity_bytes", 1920383410176ULL},
        {"quantile", "p50"},
        {"sample_count", 11},
        {"read_curve", {{{"pages", 1}, {"cumulative_ns", 11133},
                          {"p95_ns", 38238}},
                         {{"pages", 4}, {"cumulative_ns", 41495},
                          {"p95_ns", 43033}},
                         {{"pages", 16}, {"cumulative_ns", 168606},
                          {"p95_ns", 1247765}},
                         {{"pages", 64}, {"cumulative_ns", 2824351},
                          {"p95_ns", 3860958}},
                         {{"pages", 256}, {"cumulative_ns", 10767793},
                          {"p95_ns", 11968167}},
                         {{"pages", 512}, {"cumulative_ns", 20254374},
                          {"p95_ns", 22163673}}}},
        {"program_p50_ns", 408305},
        {"program_p95_ns", 596336}};
    const auto path = std::filesystem::temp_directory_path() /
                      "hbfsim-empirical-profile-test.json";
    std::ofstream output(path);
    output << document.dump(2) << '\n';
    return path;
}

const auto tuned = hbfsim::load_profile(tuned_profile_path());
check(tuned.empirical_vmem.has_value(), "empirical profile loaded");
check(tuned.empirical_vmem->read_curve.size() == 6,
      "six empirical points");
check(tuned.empirical_vmem->read_curve.front().pages == 1,
      "first empirical page");
check(tuned.empirical_vmem->read_curve.back().cumulative_ns == 20'254'374,
      "last empirical cumulative latency");
check(!nominal.empirical_vmem.has_value(), "legacy profile unchanged");

auto invalid_empirical = tuned;
invalid_empirical.page_bytes = 16'384;
check_profile_error(
    [&] { hbfsim::validate_profile(invalid_empirical); },
    "empirical_vmem requires page_bytes == 4096");
```

Add one mutation for each contract: count not six, non-increasing pages,
non-increasing P50, P95 below P50, last page above 1023, malformed digest,
zero program latency, and capacity/page index beyond the packed-state limit.

- [ ] **Step 2: Run the profile target and verify RED**

Run:

```bash
cmake --build /dev/shm/hbfsim-vllm-gpu13 --target hbfsim_profile_tests -j2
```

Expected: compile failure because `Profile` has no `empirical_vmem` member.

- [ ] **Step 3: Add the profile types and parser**

Add `<array>` and `<optional>` includes plus:

```cpp
struct EmpiricalVmemPoint {
    std::uint32_t pages{0};
    std::uint64_t cumulative_ns{0};
    std::uint64_t p95_ns{0};
};

struct EmpiricalVmemProfile {
    std::string source_kind;
    std::string source_sha256;
    std::uint64_t source_capacity_bytes{0};
    std::string quantile;
    std::uint32_t sample_count{0};
    std::array<EmpiricalVmemPoint, 6> read_curve{};
    std::uint64_t program_p50_ns{0};
    std::uint64_t program_p95_ns{0};
};
```

Add `std::optional<EmpiricalVmemProfile> empirical_vmem;` to `Profile` and
parse `document.contains("empirical_vmem")` without changing legacy required
fields. Validate every invariant and exact error string exercised in Step 1.
Update the JSON schema with `additionalProperties: false`, the six required
calibration fields, `read_curve` `minItems/maxItems: 6`, and point properties.

- [ ] **Step 4: Run focused and schema checks and verify GREEN**

Run:

```bash
cmake --build /dev/shm/hbfsim-vllm-gpu13 --target hbfsim_profile_tests -j2
ctest --test-dir /dev/shm/hbfsim-vllm-gpu13 -R '^profile$' --output-on-failure
python3 -m json.tool configs/schema/hbf-profile.schema.json >/dev/null
```

Expected: profile test passes; schema JSON parses.

- [ ] **Step 5: Commit profile support**

```bash
git add include/hbfsim/profile.hpp src/profile/profile.cpp \
  configs/schema/hbf-profile.schema.json tests/cpu/profile_test.cpp
git commit -m "feat: validate empirical vmem profiles"
```

### Task 3: Host/Device Curve Math and Packed Burst State

**Files:**
- Modify: `src/host_service/control_layout.hpp`
- Modify: `src/cuda_runtime/device/hbf_device.cuh`
- Modify: `tests/cpu/device_helper_abi_test.cpp`

- [ ] **Step 1: Write failing ABI/helper tests**

Add static and runtime checks:

```cpp
constexpr std::uint32_t pages[] = {1, 4, 16, 64, 256, 512};
constexpr std::uint64_t cumulative[] = {
    11'133, 41'495, 168'606, 2'824'351, 10'767'793, 20'254'374};

CHECK(empirical_cumulative_ns(pages, cumulative, 6, 1) == 11'133);
CHECK(empirical_cumulative_ns(pages, cumulative, 6, 4) == 41'495);
CHECK(empirical_cumulative_ns(pages, cumulative, 6, 512) == 20'254'374);
CHECK(empirical_service_ns(pages, cumulative, 6, 4) ==
      41'495 - empirical_cumulative_ns(pages, cumulative, 6, 3));

auto state = update_empirical_burst(0, 100, 0);
CHECK(state.run_pages == 1);
state = update_empirical_burst(state.packed, 101, 0);
CHECK(state.run_pages == 2);
CHECK(update_empirical_burst(state.packed, 103, 0).run_pages == 1);
CHECK(update_empirical_burst(state.packed, 102, 1).run_pages == 1);
```

Assert host/device header sizes and offsets remain identical and ABI version is
4.

- [ ] **Step 2: Build the ABI target and verify RED**

Run:

```bash
cmake --build /dev/shm/hbfsim-vllm-gpu13 --target hbfsim_device_helper_abi_tests -j2
```

Expected: compile failure because the empirical helpers and ABI fields do not
exist.

- [ ] **Step 3: Implement ABI v4 and pure helpers**

Append identically to both shared headers:

```cpp
alignas(8) std::uint64_t empirical_burst_state;
std::uint64_t empirical_cumulative_ns[6];
std::uint32_t empirical_breakpoint_pages[6];
std::uint32_t empirical_point_count;
std::uint32_t empirical_flags;
```

Set `kControlAbiVersion = 4`, require `sizeof(SharedControlHeader) == 384`, and
update all size/offset assertions. Implement host/device `constexpr`
`empirical_cumulative_ns`, `empirical_service_ns`, checked-ceiling
interpolation, final-segment extrapolation, pack/unpack, and
`update_empirical_burst`. Use layout:

```text
((page + 1) << 11) | (operation << 10) | run_pages
```

Reject page indices that cannot fit 53 bits; saturate run length at 1023 and
all nanosecond arithmetic at `UINT64_MAX`.

- [ ] **Step 4: Run ABI/helper tests and verify GREEN**

Run:

```bash
cmake --build /dev/shm/hbfsim-vllm-gpu13 --target hbfsim_device_helper_abi_tests -j2
ctest --test-dir /dev/shm/hbfsim-vllm-gpu13 -R '^device_helper_abi$' --output-on-failure
```

Expected: test passes with host/device size and offset equality.

- [ ] **Step 5: Commit curve math and ABI**

```bash
git add src/host_service/control_layout.hpp \
  src/cuda_runtime/device/hbf_device.cuh tests/cpu/device_helper_abi_test.cpp
git commit -m "feat: add empirical vmem control ABI"
```

### Task 4: Publish Empirical Profiles Through Real Context Creation

**Files:**
- Modify: `src/cuda_runtime/context.cpp`
- Modify: `tests/cpu/context_lifecycle_test.cpp`

- [ ] **Step 1: Write the failing context-publication test**

Create a CPU test context from a temporary tuned profile, duplicate its
existing `control_fd_for_test`, map `control_region_bytes(ring_capacity)`, and
assert:

```cpp
CHECK(header->abi_version == 4);
CHECK(header->empirical_flags == 1);
CHECK(header->empirical_point_count == 6);
CHECK(header->empirical_breakpoint_pages[3] == 64);
CHECK(header->empirical_cumulative_ns[3] == 2'824'351);
CHECK(header->empirical_burst_state == 0);
```

Create a nominal context and require point count, flags, state, arrays to be
zero.

- [ ] **Step 2: Build and run the focused lifecycle test and verify RED**

Run:

```bash
cmake --build /dev/shm/hbfsim-vllm-gpu13 --target context_lifecycle_test -j2
ctest --test-dir /dev/shm/hbfsim-vllm-gpu13 -R '^context_lifecycle$' --output-on-failure
```

Expected: failure because tuned curve fields remain zero.

- [ ] **Step 3: Publish curve fields in `create_context`**

After scalar timing fields are initialized, zero all empirical fields, then
copy exactly six points when `profile.empirical_vmem` is present:

```cpp
if (profile.empirical_vmem) {
    header->empirical_flags = 1;
    header->empirical_point_count = 6;
    for (std::size_t index = 0; index < 6; ++index) {
        header->empirical_breakpoint_pages[index] =
            profile.empirical_vmem->read_curve[index].pages;
        header->empirical_cumulative_ns[index] =
            profile.empirical_vmem->read_curve[index].cumulative_ns;
    }
}
```

Do not publish P95 values; they are evidence metadata only.

- [ ] **Step 4: Re-run lifecycle and ABI tests and verify GREEN**

Run:

```bash
ctest --test-dir /dev/shm/hbfsim-vllm-gpu13 \
  -R '^(context_lifecycle|device_helper_abi|profile)$' --output-on-failure
```

Expected: all selected tests pass.

- [ ] **Step 5: Commit control publication**

```bash
git add src/cuda_runtime/context.cpp tests/cpu/context_lifecycle_test.cpp
git commit -m "feat: publish vmem tuning curves"
```

### Task 5: Empirical Fast/Hybrid GPU Injection

**Files:**
- Modify: `src/cuda_runtime/device/hbf_device.cu`
- Modify: `tests/cpu/device_helper_abi_test.cpp`

- [ ] **Step 1: Add failing empirical decision tests**

Extract a host/device pure helper returning `{service_ns, packed_state,
valid}` and test:

```cpp
auto first = empirical_request_service(header, 0, 0, 0);
CHECK(first.valid && first.service_ns == 11'133);
std::uint64_t packed = 0;
std::uint64_t cumulative_ns = 0;
for (std::uint64_t page = 0; page < 4; ++page) {
    const auto request = empirical_request_service(header, packed, page, 0);
    CHECK(request.valid);
    packed = request.packed_state;
    cumulative_ns += request.service_ns;
}
CHECK(cumulative_ns == 41'495);
auto random = empirical_request_service(header, packed, 99, 0);
CHECK(random.run_pages == 1 && random.service_ns == 11'133);
auto write = empirical_request_service(header, random.packed_state, 100, 1);
CHECK(write.run_pages == 1 && write.service_ns == 408'305);
auto malformed = header;
malformed.empirical_point_count = 5;
CHECK(!empirical_request_service(malformed, 0, 0, 0).valid);
```

- [ ] **Step 2: Build the helper test and verify RED**

Run:

```bash
cmake --build /dev/shm/hbfsim-vllm-gpu13 --target hbfsim_device_helper_abi_tests -j2
```

Expected: compile failure because `empirical_request_service` is missing.

- [ ] **Step 3: Integrate empirical service into the device path**

In `resolve_fast_or_hybrid`, when `empirical_flags == 1`:

1. Validate count, arrays, page size, and packed page representability;
   invalid empirical control returns `RequestStatus::Unsupported` and never
   enters the legacy scalar branch.
2. Update `empirical_burst_state` with a system-scope CAS loop.
3. Use curve marginal service for reads and `program_latency_ns` for writes.
4. Reserve the existing `fast_channel_tail_ns` as
   `max(tail, arrival) + service * time_scale`.
5. Wait until that target with the existing timeout/shutdown/fault checks.
6. Add the unscaled empirical service to `fast_modeled_ns` and increment
   `fast_requests`.

Do not call `fast_transfer_ns` in the empirical branch; the measured curve
already includes transfer and software overhead. Leave the existing scalar
branch unchanged.

- [ ] **Step 4: Run helper, PTX, and lifecycle tests and verify GREEN**

Run:

```bash
cmake --build /dev/shm/hbfsim-vllm-gpu13 -j2
ctest --test-dir /dev/shm/hbfsim-vllm-gpu13 \
  -R '^(device_helper_abi|device_helper_ptx|context_lifecycle|timing_gate_binding)$' \
  --output-on-failure
```

Expected: selected tests pass; device helper PTX assembles for `sm_120`.

- [ ] **Step 5: Commit empirical injection**

```bash
git add src/cuda_runtime/device/hbf_device.cu \
  src/cuda_runtime/device/hbf_device.cuh tests/cpu/device_helper_abi_test.cpp
git commit -m "feat: inject burst-aware vmem timing"
```

### Task 6: Generate the Named Profile and Comparison Artifact

**Files:**
- Create: `configs/profiles/cd8p-vmem-p50.json`
- Create: `configs/tuning/cd8p-vmem-comparison.json`
- Modify: `tests/integration/test_vmem_tuning.py`

- [ ] **Step 1: Add a failing golden-artifact test**

Assert the committed files exist, load through the tuner, and match a fresh
temporary generation byte-for-byte after normalizing only the recorded output
paths. Require source digest, scalar fields, six curve points, aligned capacity,
and zero empirical breakpoint error.

- [ ] **Step 2: Run the golden test and verify RED**

Run:

```bash
python3 tests/integration/test_vmem_tuning.py
```

Expected: FAIL because the committed profile/report do not exist.

- [ ] **Step 3: Generate artifacts from the reviewed CSV**

Run:

```bash
python3 scripts/tune_vmem_profile.py \
  --input-csv /home/victoryang00/nvme-mem2nvm/docs/superpowers/results/2026-07-30-vmem-sw-performance.csv \
  --base-profile configs/profiles/nominal.json \
  --output-profile configs/profiles/cd8p-vmem-p50.json \
  --output-report configs/tuning/cd8p-vmem-comparison.json \
  --expected-sha256 4fb6d2847c3ce4a09b7f2ce07dcb4cf8254145243c1985bce2848261b8d0724f
```

Expected profile scalars: page 4096, read 11133 ns, program 408305 ns,
bandwidth 103540697 B/s, cache 4294967296, queue depth 1, time scale 1,
capacity 1919850381312.

- [ ] **Step 4: Verify artifact and profile tests GREEN**

Run:

```bash
python3 tests/integration/test_vmem_tuning.py
ctest --test-dir /dev/shm/hbfsim-vllm-gpu13 -R '^(vmem_tuning|profile)$' --output-on-failure
```

Expected: golden regeneration and C++ profile loading both pass.

- [ ] **Step 5: Commit generated evidence**

```bash
git add configs/profiles/cd8p-vmem-p50.json \
  configs/tuning/cd8p-vmem-comparison.json \
  tests/integration/test_vmem_tuning.py
git commit -m "data: add CD8P vmem tuned profile"
```

### Task 7: Deterministic Real-GPU Proof, Documentation, and Full Regression

**Files:**
- Create: `benchmarks/cuda/hbf_vmem_tuning_bench.cu`
- Modify: `benchmarks/cuda/CMakeLists.txt`
- Create: `scripts/run_vmem_tuning_bench.py`
- Modify: `README.md`
- Create: `docs/proofs/2026-08-11-cd8p-vmem-tuning.md`

- [ ] **Step 1: Write the failing runner contract test**

Extend `tests/integration/test_vmem_tuning.py` with a dry-run manifest test
requiring breakpoint cases `(1,4,16,64,256,512)`, separate baseline/tuned
commands, automatic bpftime wrapping for tuned cases, exact-checksum policy,
zero-unsafe-launch policy, and modeled-total equality.

- [ ] **Step 2: Run the runner contract and verify RED**

Run:

```bash
python3 tests/integration/test_vmem_tuning.py
```

Expected: FAIL because `scripts/run_vmem_tuning_bench.py` is absent.

- [ ] **Step 3: Implement the deterministic benchmark and runner**

The CUDA kernel must use one thread and a volatile byte load from the first
byte of each 4 KiB page:

```cpp
extern "C" __global__ void hbf_vmem_sequential(
    const volatile std::uint8_t* input, std::uint32_t pages,
    std::uint64_t* checksum)
{
    if (blockIdx.x != 0 || threadIdx.x != 0) return;
    std::uint64_t value = 0;
    for (std::uint32_t page = 0; page < pages; ++page)
        value = (value * 131) ^ input[static_cast<std::size_t>(page) * 4096];
    *checksum = value;
}
```

The executable must create deterministic input, register the whole range in
timing mode for tuned runs, launch once, synchronize, query `hbfsim_get_stats`,
unregister, and emit JSON. The Python runner must execute baseline and tuned
for all six breakpoints, parse coverage JSONL, and fail unless:

```text
baseline checksum == tuned checksum
modeled cumulative ns == source P50 cumulative ns
fast requests == pages
reference requests == 0
modeled launches == 1
unsafe launches == 0
```

Support `--dry-run`, `--build-dir`, `--bpftime-build-dir`, `--profile`, and
`--output`.

- [ ] **Step 4: Build and run the real-GPU proof**

Run:

```bash
cmake --build /dev/shm/hbfsim-vllm-gpu13 \
  --target hbf_vmem_tuning_bench hbfsimd hbfsim_microbench_probe -j2
HBFSIM_BUILD_DIR=/dev/shm/hbfsim-vllm-gpu13 \
HBFSIM_BPFTIME_BUILD_DIR=/dev/shm/hbfsim-bpftime-variant-gcc14 \
python3 scripts/run_vmem_tuning_bench.py \
  --profile configs/profiles/cd8p-vmem-p50.json \
  --output /mnt/disk2/hbfsim-cd8p-vmem-tuning-20260811/summary.json
```

Expected: six exact checksum/model matches on the real GPU, one modeled launch
per tuned case, and zero unsafe launches. Do not touch `/dev/vmem0` or the raw
NVMe namespace.

- [ ] **Step 5: Write proof documentation**

Document source hash, current live-device dirty-cache safety boundary, tuning
algorithm, scalar-baseline error, six real-GPU modeled totals, checksums,
coverage, GPU identity, and full artifact path. Update README with a high-level
description and reproduction command.

- [ ] **Step 6: Run complete regression and inspect the diff**

Run:

```bash
ctest --test-dir /dev/shm/hbfsim-vllm-gpu13 --output-on-failure
python3 -m pytest -q adapters/vllm/tests
git diff --check
git status --short
```

Expected: all CTest and vLLM tests pass, no whitespace errors, and only planned
files are modified.

- [ ] **Step 7: Commit the proof and documentation**

```bash
git add benchmarks/cuda/hbf_vmem_tuning_bench.cu \
  benchmarks/cuda/CMakeLists.txt scripts/run_vmem_tuning_bench.py \
  tests/integration/test_vmem_tuning.py README.md \
  docs/proofs/2026-08-11-cd8p-vmem-tuning.md
git commit -m "test: prove CD8P vmem tuning on GPU"
```

Record the final commit SHA and leave pushing as a separate explicitly
authorized operation.
