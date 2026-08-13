# SM120 Exact Calibration Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the fail-closed foundation that turns one transformed PTX module into a fixed `sm_120` AOT cubin/SASS artifact, binds it to a validated calibration profile and live RTX PRO 6000 environment, and admits a launch as `exact` only when every identity, resource, environment, and validation gate matches.

**Architecture:** An offline builder compiles pass output with pinned CUDA 13 tools and emits a content-addressed artifact bundle. The patched bpftime loader loads those exact cubin bytes and brackets `cuModuleLoadDataEx` with an HBFSim AOT provenance transaction. HBFSim verifies the bytes, the embedded module identity, the per-kernel resource record, the profile, and a read-only live GPU/NVML snapshot before an HBF-relevant launch. Public exact mode is explicit and fail-closed; legacy emulation behavior remains the default. This stage defines the profile and admission contract used by the later LDG/STG-future, TensorMap/TMA, and 4+2-channel plans, but does not claim their runtime semantics are complete.

**Tech Stack:** C++20, CUDA 13 `ptxas`/`nvdisasm`/`cuobjdump`, CUDA Driver API, dynamically loaded NVML, OpenSSL SHA-256, nlohmann/json, Python 3, pinned bpftime patch artifacts, CMake/CTest.

---

## Stage 1 invariants

These are hard requirements for every task below:

- `HBFSIM_FIDELITY_EMULATION` is the zero/default value and preserves current callers.
- `HBFSIM_FIDELITY_EXACT_SM120` requires an exact profile and the V4 launch-gate ABI.
- Exact mode loads a prebuilt cubin. A driver-JIT result from PTX is never exact.
- The cubin bytes passed to CUDA are the bytes whose SHA-256 appears in the bundle and profile.
- The live `__hbfsim_module_identity` must equal the module ID in the artifact record.
- Target, toolchain, GPU identity, clocks, power, temperature, cache/concurrency contract, registers, spills, shared memory, occupancy tier, and validation state all fail closed.
- A profile with `validation.status != "passed"`, or with any required operation class missing/failing, may run only in emulation and may never emit `fidelity: "exact"`.
- Physical channel inference, async futures, TMA semantics, and timing fitting remain later stages. Stage 1 supplies their versioned schema slots and admission limits only.

### Task 1: Define and strictly parse the versioned exact profile

**Files:**
- Create: `include/hbfsim/exact_profile.hpp`
- Create: `src/profile/exact_profile.cpp`
- Create: `configs/schema/sm120-exact-profile.schema.json`
- Create: `tests/fixtures/exact/sm120-valid.json`
- Create: `tests/cpu/exact_profile_test.cpp`
- Modify: `CMakeLists.txt`

- [ ] **Step 1: Add the failing profile tests and CMake target**

Create `exact_profile_test.cpp` with a table of mutations over the valid fixture. The positive case must expose typed fields; each negative case must throw `hbfsim::ExactProfileError` with a stable reason code.

```cpp
const auto profile = hbfsim::load_exact_profile(fixture("sm120-valid.json"));
CHECK(profile.schema_version == 1);
CHECK(profile.target.compute_capability_major == 12);
CHECK(profile.target.compute_capability_minor == 0);
CHECK(profile.modules.at(0).kernels.at(0).registers == 48);
CHECK(profile.validation.status == hbfsim::ValidationStatus::Passed);

expect_error("bad-target.json", "target_not_sm120");
expect_error("uppercase-digest.json", "invalid_sha256");
expect_error("duplicate-module.json", "duplicate_module_id");
expect_error("duplicate-kernel.json", "duplicate_kernel");
expect_error("relaxed-p50.json", "threshold_exceeds_exact_limit");
expect_error("missing-op-class.json", "validation_class_missing");
expect_error("overlap-datasets.json", "training_validation_overlap");
const auto failed = hbfsim::load_exact_profile("failed-op-class.json");
CHECK(failed.validation.status == hbfsim::ValidationStatus::Failed);
```

Register `src/profile/exact_profile.cpp` in `hbfsim_core`, add `exact_profile_test`, and set its working directory to the source root.

- [ ] **Step 2: Run the test and verify RED**

```bash
cmake -S . -B build-sm120-exact \
  -DCMAKE_BUILD_TYPE=RelWithDebInfo \
  -DHBFSIM_ENABLE_CUDA=ON \
  -DCMAKE_CUDA_ARCHITECTURES=120 \
  -DHBFSIM_ENABLE_MQSIM=OFF
cmake --build build-sm120-exact --target exact_profile_test -j2
```

Expected: compilation fails because `hbfsim/exact_profile.hpp` and its parser do not exist.

- [ ] **Step 3: Implement the typed contract and strict validation**

Use a separate type from the existing media `Profile`; do not overload nominal/aggressive/conservative media JSON. The public surface starts with:

```cpp
enum class ValidationStatus { Pending, Passed, Failed };

struct ExactKernelArtifact {
    std::string name;
    std::uint32_t registers;
    std::uint64_t spill_store_bytes;
    std::uint64_t spill_load_bytes;
    std::uint64_t static_shared_bytes;
    std::uint64_t max_dynamic_shared_bytes;
    std::uint32_t occupancy_blocks_per_sm;
};

struct ExactModuleArtifact {
    std::string module_id;              // ptx:sha256:<64 lowercase hex>
    std::string ptx_target;             // sm_120, sm_120a, or sm_120f
    std::string original_ptx_sha256;
    std::string transformed_ptx_sha256;
    std::string cubin_sha256;
    std::string sass_sha256;
    std::vector<ExactKernelArtifact> kernels;
};

struct ExactProfile {
    std::uint32_t schema_version;
    std::string profile_id;
    ExactTarget target;
    ExactToolchain toolchain;
    ExactConditions conditions;
    ExactThresholds thresholds;
    ExactFutureLimits limits;
    std::vector<ExactModuleArtifact> modules;
    ExactValidation validation;
};

ExactProfile load_exact_profile(const std::filesystem::path& path);
ExactProfile parse_exact_profile(std::string_view json);
```

The schema and C++ parser must both require:

- GPU product name, PCI vendor/device IDs, compute capability `12.0`, and driver version;
- exact CUDA, `ptxas`, `nvdisasm`, `cuobjdump`, and Nsight Compute versions;
- graphics/SM and memory clocks, power limit, temperature interval, cache state, concurrency state, and cluster shape;
- exact thresholds capped at P50 5%, P95 10%, counter 10%;
- bounded thread/warp/CTA/cluster future and async-object limits;
- unique module IDs, unique kernel names per module, lowercase SHA-256 strings, and nonzero resource/occupancy bounds;
- disjoint training and validation case IDs and digests;
- separate validation entries for `ordinary_load`, `ordinary_store`, `tma_load`, `tma_store`, `unicast`, `multicast`, and `mixed_hbm_hbf`.

`validation.status == "passed"` is legal only when every required class passes every threshold. `pending` and `failed` are parseable so Stage 4 can generate and diagnose profiles, but neither is admissible as exact.

- [ ] **Step 4: Run parser/schema tests and verify GREEN**

```bash
cmake --build build-sm120-exact --target exact_profile_test -j2
ctest --test-dir build-sm120-exact -R '^exact_profile$' --output-on-failure
python3 -m json.tool configs/schema/sm120-exact-profile.schema.json >/dev/null
```

Expected: all valid fields round-trip, every mutation reports its specified reason, and the schema is valid JSON.

- [ ] **Step 5: Commit the profile contract**

```bash
git add include/hbfsim/exact_profile.hpp src/profile/exact_profile.cpp \
  configs/schema/sm120-exact-profile.schema.json \
  tests/fixtures/exact/sm120-valid.json tests/cpu/exact_profile_test.cpp \
  CMakeLists.txt
git commit -m "feat: define sm120 exact profile contract"
```

### Task 2: Build content-addressed AOT cubin/SASS bundles

**Files:**
- Create: `scripts/calibration/build_sm120_artifact.py`
- Create: `tests/integration/test_build_sm120_artifact.py`
- Create: `tests/fixtures/exact/minimal-sm120.ptx`
- Create: `tests/fixtures/exact/kernel-contract.json`
- Modify: `CMakeLists.txt`

- [ ] **Step 1: Write deterministic fake-tool tests**

The test creates fake `ptxas`, `nvdisasm`, and `cuobjdump` executables, invokes the builder, and verifies exact argv, output layout, hashes, parsed resources, and cleanup. Cover these failures independently: target not in `{sm_120, sm_120a, sm_120f}`, tool version mismatch, embedded module ID mismatch, malformed resource output, compiler failure, missing SASS, pre-existing output directory, and post-build cubin tampering.

Expected bundle layout:

```text
<bundle-root>/<original-ptx-sha256>/<ptx-target>/
  original.ptx
  transformed.ptx
  module.cubin
  module.sass
  artifact.json
```

The test must assert that `artifact.json` contains:

```json
{
  "schema_version": 1,
  "module_id": "ptx:sha256:...",
  "ptx_target": "sm_120",
  "toolchain": {},
  "hashes": {
    "original_ptx_sha256": "...",
    "transformed_ptx_sha256": "...",
    "cubin_sha256": "...",
    "sass_sha256": "..."
  },
  "kernels": []
}
```

- [ ] **Step 2: Run the builder test and verify RED**

```bash
python3 tests/integration/test_build_sm120_artifact.py \
  scripts/calibration/build_sm120_artifact.py
```

Expected: failure because the builder does not exist.

- [ ] **Step 3: Implement the fail-closed builder**

Expose this CLI:

```text
build_sm120_artifact.py \
  --original-ptx FILE --transformed-ptx FILE --pass-manifest FILE \
  --kernel-contract FILE \
  --target sm_120 --bundle-root DIR \
  --ptxas /usr/local/cuda-13.0/bin/ptxas \
  --nvdisasm /usr/local/cuda-13.0/bin/nvdisasm \
  --cuobjdump /usr/local/cuda-13.0/bin/cuobjdump
```

Implementation rules:

1. Resolve every input/tool to an absolute path and record each tool's `--version` output.
2. Parse exactly one matching pass-manifest record and exactly one embedded `__hbfsim_module_identity` declaration.
3. Compile with an explicit target and no JIT fallback:

   ```python
   run([ptxas, f"--gpu-name={target}", "--verbose",
        "--output-file", cubin_tmp, transformed_ptx], check=True)
   ```

4. Generate SASS from the cubin with `nvdisasm --print-code --print-raw` and collect per-kernel resource usage with `cuobjdump --dump-resource-usage`.
   Require the calibration-produced kernel contract to supply block threads,
   maximum dynamic shared memory, and occupancy blocks per SM; cubin metadata
   alone cannot determine occupancy. Missing contract fields are errors.
5. Hash exact file bytes with SHA-256. Never hash paths, mtimes, or formatted JSON.
6. Write into a temporary sibling directory, fsync files and directory, then rename only when every check passes. Refuse to overwrite an existing bundle.
7. Re-open and re-hash the installed bundle before returning success.

Do not infer absent resource fields as zero. A missing register, spill, shared-memory, or kernel record is a build error.

- [ ] **Step 4: Run fake and real CUDA 13 assembly tests GREEN**

```bash
python3 tests/integration/test_build_sm120_artifact.py \
  scripts/calibration/build_sm120_artifact.py
ctest --test-dir build-sm120-exact \
  -R '^(device_helper_ptx|ptxpass_plugin)$' --output-on-failure
/usr/local/cuda-13.0/bin/ptxas --gpu-name=sm_120 \
  tests/fixtures/exact/minimal-sm120.ptx \
  --output-file /tmp/hbfsim-minimal-sm120.cubin
```

Expected: fake-tool contract tests pass and CUDA 13 assembles the fixture for `sm_120`. The `/tmp` cubin is test output only and is not committed.

- [ ] **Step 5: Register and commit the artifact builder**

Add a CTest entry named `build_sm120_artifact` for the fake-tool test, then:

```bash
git add scripts/calibration/build_sm120_artifact.py \
  tests/integration/test_build_sm120_artifact.py \
  tests/fixtures/exact/minimal-sm120.ptx CMakeLists.txt
git commit -m "feat: build fixed sm120 cubin artifacts"
```

### Task 3: Authorize the exact cubin bytes at module load

**Files:**
- Create: `include/hbfsim/exact_artifact.hpp`
- Create: `src/cuda_runtime/exact_artifact.cpp`
- Create: `tests/cpu/exact_artifact_test.cpp`
- Modify: `include/hbfsim/module_identity.hpp`
- Modify: `src/cuda_runtime/module_identity.cpp`
- Modify: `src/cuda_runtime/launch_gate.cpp`
- Modify: `tests/cpu/module_identity_test.cpp`
- Modify: `tests/integration/test_cuda_module_association.py`
- Modify: `CMakeLists.txt`

- [ ] **Step 1: Add failing artifact-transaction tests**

Test a one-shot thread-local AOT transaction whose authorization is computed from the exact cubin bytes and artifact JSON:

```cpp
hbfsim::AotLoadTransactionStore loads;
const auto token = loads.begin(cubin_bytes, artifact_json);
CHECK(token != 0);
const auto evidence = loads.take();
CHECK(evidence->identity == expected_identity);
CHECK(evidence->cubin_sha256 == sha256(cubin_bytes));
CHECK(evidence->sass_sha256 == artifact_sass_sha);
CHECK(!loads.take().has_value());
```

Reject a wrong cubin digest, malformed module ID, missing kernel resources, nested begin, duplicate token, zero-length image, oversized manifest, and a modified byte. Prove two threads cannot exchange evidence. Extend the module registry test so a handle is associated with the complete `LoadedModuleEvidence`, while `lookup_identity()` remains available to current launch code.

- [ ] **Step 2: Run tests and verify RED**

```bash
cmake --build build-sm120-exact --target exact_artifact_test module_identity_test -j2
```

Expected: compilation fails because the artifact transaction/evidence types do not exist.

- [ ] **Step 3: Implement byte-level authorization and the exported hook**

Define:

```cpp
struct LoadedModuleEvidence {
    ModuleIdentity identity;
    std::string module_id;
    std::string ptx_target;
    std::string cubin_sha256;
    std::string sass_sha256;
    ExactToolchain toolchain;
    std::vector<ExactKernelArtifact> kernels;
    bool aot_verified;
};

class AotLoadTransactionStore {
  public:
    ModuleLoadToken begin(std::span<const std::byte> cubin,
                          std::string_view artifact_json) noexcept;
    std::optional<LoadedModuleEvidence>
    take_for_image(const void* image) noexcept;
    void end(ModuleLoadToken token) noexcept;
};
```

Export this C hook from the launch-gate shared object:

```cpp
extern "C" std::uint64_t hbfsim_begin_module_load_from_aot(
    const void* cubin, std::size_t cubin_bytes,
    const char* artifact_json, std::size_t artifact_bytes) noexcept;
```

`cuModuleLoadDataEx` consumes at most one PTX or AOT transaction before calling CUDA. AOT consumption requires the same image pointer and re-hashes its recorded byte length immediately before the driver call, closing pointer-swap and between-hook mutation paths. After a successful driver load, it reads `__hbfsim_module_identity` from the live module. Association succeeds only when the live identity equals the transaction identity. A failed CUDA load, missing marker, mismatch, duplicate handle, or exception clears the transaction and stores no evidence. Preserve the current PTX transaction only for emulation; mark its evidence `aot_verified=false` so exact admission rejects it.

- [ ] **Step 4: Prove spoof/tamper rejection GREEN**

Extend `test_cuda_module_association.py` so the fake CUDA library receives an AOT transaction, then cover:

- exact bytes + exact manifest + exact live marker: associated;
- one changed cubin byte: begin returns zero;
- copied manifest with a different image: rejected;
- exact image with wrong live marker: loaded by CUDA but unassociated;
- stale transaction after a failed load: not reused;
- PTX/JIT association: still works in emulation but has no AOT evidence.

Run:

```bash
cmake --build build-sm120-exact --target exact_artifact_test \
  module_identity_test hbfsim_launch_gate_fake -j2
ctest --test-dir build-sm120-exact \
  -R '^(exact_artifact|module_identity|cuda_module_association)$' \
  --output-on-failure
```

- [ ] **Step 5: Commit byte-level module provenance**

```bash
git add include/hbfsim/exact_artifact.hpp src/cuda_runtime/exact_artifact.cpp \
  include/hbfsim/module_identity.hpp src/cuda_runtime/module_identity.cpp \
  src/cuda_runtime/launch_gate.cpp tests/cpu/exact_artifact_test.cpp \
  tests/cpu/module_identity_test.cpp \
  tests/integration/test_cuda_module_association.py CMakeLists.txt
git commit -m "feat: bind module loads to exact cubin bytes"
```

### Task 4: Make pinned bpftime load the AOT bundle instead of JIT output

**Files:**
- Create: `patches/bpftime/0002-sm120-aot-bundle-load.patch`
- Create: `patches/bpftime/0003-libbpf-modern-libc-const.patch`
- Create: `patches/bpftime/0004-honor-llvm-aot-cli-option.patch`
- Create: `patches/bpftime/0005-cuda13-context-create.patch`
- Modify: `cmake/PreparePatchedBpftime.cmake`
- Modify: `scripts/build_patched_bpftime.sh`
- Modify: `scripts/run_with_bpftime.sh`
- Create: `tests/integration/test_bpftime_aot_patch.py`
- Create: `tests/integration/test_bpftime_host_compiler_patch.py`
- Create: `tests/integration/test_bpftime_cuda13_patch.py`
- Modify: `tests/integration/test_build_patched_bpftime.py`
- Modify: `tests/integration/test_run_with_bpftime.py`
- Modify: `CMakeLists.txt`

- [ ] **Step 1: Write the failing two-patch and loader-contract tests**

The test must copy pinned bpftime, apply the ordered patch series, and run `git apply --check` before each patch. The AOT patch and its compiled Catch2 tests must prove:

- `HBFSIM_EXACT_BUNDLE_DIR` selects a bundle by original PTX SHA-256 and PTX target;
- exact mode reads `module.cubin` and `artifact.json` as bounded binary/text buffers;
- staged transformed PTX bytes match the artifact `transformed_ptx_sha256` before any load hook or CUDA call;
- the AOT hook receives the same cubin buffer later passed to `cuModuleLoadDataEx`;
- cache keys use cubin SHA-256, not original PTX or a prior JIT output;
- missing, duplicate, symlink-escaping, oversized, or malformed bundle files fail before CUDA;
- the existing JIT path is unchanged when the exact-bundle variable is absent.

Update stamp tests to require ordered patch digests, not one digest.

- [ ] **Step 2: Run and verify RED**

```bash
python3 tests/integration/test_bpftime_aot_patch.py \
  third_party/bpftime patches/bpftime/0001-exact-module-load-provenance.patch \
  patches/bpftime/0002-sm120-aot-bundle-load.patch
```

Expected: failure because patch 0002 and its AOT loader do not exist.

- [ ] **Step 3: Implement the minimal AOT patch and patch-series stamping**

Patch the pinned bpftime copy, not the submodule. Add an RAII scope analogous to the existing PTX provenance scope:

```cpp
using begin_aot_load_fn = uint64_t (*)(const void*, size_t,
                                       const char*, size_t);

aot_module_load_scope scope(cubin, artifact_json,
                            find_aot_module_load_hooks());
if (!scope.permits_load())
    throw std::runtime_error("HBFSim AOT provenance rejected");
load_result = cuModuleLoadDataEx(&module, cubin.data(),
                                 std::size(options), options, option_values);
```

Resolve paths beneath an already canonicalized bundle root and reject symlinks for `module.cubin` and `artifact.json`. Cap cubin and manifest sizes. Parse only the minimal artifact keys needed for path/hash selection; HBFSim remains the trust authority. Do not silently compile PTX if bundle lookup or AOT authorization fails.

Update copied-source preparation and stamp content to include:

```text
bpftime_commit=<pinned commit>
patch_0001_sha256=<digest>
patch_0002_sha256=<digest>
patch_0003_sha256=<digest>
patch_0004_sha256=<digest>
patch_0005_sha256=<digest>
aot_bridge_version=1
```

Require CUDA 13.0 for this pinned exact bridge build. Pin the BPF compiler, `llvm-strip`, and LLVM CMake package to the validated LLVM 19.1.7 toolchain so bpftool, generated BPF test assets, and llvmbpf cannot resolve an unrelated compiler through `PATH`. Record the canonical CUDA path and release in the stamp. Remove any prior stamp before preparing or rebuilding so a failed rebuild cannot leave stale provenance beside changed outputs.

Add `--exact-profile`, `--exact-bundle-dir`, and `--prepatched-ptx-dir` options to `run_with_bpftime.sh`. Exact mode requires all three. The wrapper must export canonical, non-symlink paths only after verifying the profile file, bundle root, lowercase-SHA256-named PTX inputs, and matching patch-series stamp.

- [ ] **Step 4: Build the patched copy and verify GREEN**

```bash
cmake --build build-sm120-exact -j2
ctest --test-dir build-sm120-exact \
  -R '^(bpftime_patch|bpftime_aot_patch|bpftime_host_compiler_patch|bpftime_cuda13_patch|build_patched_bpftime|run_with_bpftime)$' \
  --output-on-failure
git submodule foreach --recursive 'test -z "$(git status --porcelain)"'
```

Expected: all five patches apply in order, stamps bind all five digests, the real patched copy and its nv-attach tests pass, exact wrapper tests pass, and submodules remain clean.

- [ ] **Step 5: Commit the AOT bpftime bridge**

```bash
git add patches/bpftime/0002-sm120-aot-bundle-load.patch \
  patches/bpftime/0003-libbpf-modern-libc-const.patch \
  patches/bpftime/0004-honor-llvm-aot-cli-option.patch \
  patches/bpftime/0005-cuda13-context-create.patch \
  cmake/PreparePatchedBpftime.cmake scripts/build_patched_bpftime.sh \
  scripts/run_with_bpftime.sh tests/integration/test_bpftime_aot_patch.py \
  tests/integration/test_build_patched_bpftime.py \
  tests/integration/test_run_with_bpftime.py \
  tests/integration/test_bpftime_host_compiler_patch.py \
  tests/integration/test_bpftime_cuda13_patch.py CMakeLists.txt
git commit -m "feat: load calibrated cubins through bpftime"
```

### Task 5: Collect a read-only live SM120 environment snapshot

**Files:**
- Create: `include/hbfsim/exact_environment.hpp`
- Create: `src/cuda_runtime/exact_environment.cpp`
- Create: `tests/cpu/exact_environment_test.cpp`
- Create: `tests/integration/test_live_sm120_environment.py`
- Modify: `src/cuda_runtime/launch_gate.cpp`
- Modify: `tests/integration/test_launch_gate_symbols.py`
- Modify: `CMakeLists.txt`

- [ ] **Step 1: Write fake CUDA/NVML environment tests**

Define injectable driver and NVML function tables. Tests must cover an exact RTX PRO 6000 snapshot and each fail-closed failure: no current CUDA context, wrong CC, wrong PCI ID, NVML unavailable, UUID/PCI mismatch, unsupported clock query, zero power limit, temperature query failure, multiple compute processes under an exclusive-process contract, and changing clock/temperature values between probes.

```cpp
const auto live = hbfsim::collect_exact_environment(fake_driver, fake_nvml,
                                                     getpid());
CHECK(live.compute_capability_major == 12);
CHECK(live.compute_capability_minor == 0);
CHECK(live.sm_clock_mhz == 1830);
CHECK(live.memory_clock_mhz == 14001);
CHECK(live.current_process_is_exclusive);
```

- [ ] **Step 2: Run and verify RED**

```bash
cmake --build build-sm120-exact --target exact_environment_test -j2
```

Expected: compilation fails because the environment collector does not exist.

- [ ] **Step 3: Implement dynamic, read-only collection**

Use CUDA Driver API calls for device name, UUID, PCI bus ID, vendor/device ID, compute capability, and driver version. Resolve NVML at runtime with `dlopen("libnvidia-ml.so.1", RTLD_NOW | RTLD_LOCAL)` and query the same PCI device for SM clock, memory clock, enforced power limit, temperature, compute mode, and running compute-process PIDs.

```cpp
struct ExactLiveEnvironment {
    std::string gpu_name;
    std::string gpu_uuid;
    std::string pci_bus_id;
    std::uint32_t pci_vendor_id;
    std::uint32_t pci_device_id;
    std::uint32_t compute_capability_major;
    std::uint32_t compute_capability_minor;
    std::uint32_t cuda_driver_version;
    std::uint32_t sm_clock_mhz;
    std::uint32_t memory_clock_mhz;
    std::uint32_t power_limit_mw;
    std::uint32_t temperature_c;
    bool current_process_is_exclusive;
};
```

No function in this task may set clocks, power, compute mode, persistence mode, MIG state, or process state. Return structured probe errors; never substitute zero or a remembered host value.

Expose the result from the launch-gate library through the size-checked `hbfsim_collect_exact_environment_v1` C ABI. The ABI returns the structured error code, native status, operation, and a fixed-layout snapshot so live tests and later admission code do not scrape `nvidia-smi` text.

- [ ] **Step 4: Run unit and live read-only checks GREEN**

The Python live test loads the built launch-gate library, requests one snapshot, and skips only when no CC 12.0 GPU is present. On the target host it must assert the product contains `RTX PRO 6000`, CC is 12.0, PCI IDs and clocks are nonzero, and the snapshot has a timestamp.

```bash
cmake --build build-sm120-exact --target exact_environment_test \
  hbfsim_launch_gate -j2
ctest --test-dir build-sm120-exact -R '^exact_environment$' --output-on-failure
python3 tests/integration/test_live_sm120_environment.py \
  build-sm120-exact/libhbfsim_launch_gate.so
```

- [ ] **Step 5: Commit environment probing**

```bash
git add include/hbfsim/exact_environment.hpp \
  src/cuda_runtime/exact_environment.cpp \
  tests/cpu/exact_environment_test.cpp \
  tests/integration/test_live_sm120_environment.py \
  src/cuda_runtime/launch_gate.cpp \
  tests/integration/test_launch_gate_symbols.py CMakeLists.txt
git commit -m "feat: probe live sm120 exact environment"
```

### Task 6: Implement the pure exact-admission evaluator

**Files:**
- Create: `include/hbfsim/exact_admission.hpp`
- Create: `src/cuda_runtime/exact_admission.cpp`
- Create: `tests/cpu/exact_admission_test.cpp`
- Modify: `CMakeLists.txt`

- [ ] **Step 1: Write one-mismatch-at-a-time admission tests**

Start with a fully matching profile, loaded module evidence, live snapshot, kernel resource record, and explicit run contract. Assert it admits. Then mutate exactly one field per case and assert the complete ordered reason list.

```cpp
const auto admitted = evaluator.evaluate(profile, evidence, live, contract,
                                          "kernel");
CHECK(admitted.allowed);
CHECK(admitted.reasons.empty());

live.sm_clock_mhz++;
const auto rejected = evaluator.evaluate(profile, evidence, live, contract,
                                          "kernel");
CHECK(!rejected.allowed);
CHECK(rejected.reasons == std::vector{"sm_clock_mismatch"});
```

Required rejection cases:

```text
profile_not_validated
validation_class_missing
gpu_name_mismatch
gpu_uuid_mismatch
pci_device_mismatch
compute_capability_mismatch
driver_version_mismatch
ptx_target_mismatch
toolchain_mismatch
cubin_sha256_mismatch
sass_sha256_mismatch
kernel_resource_missing
register_count_mismatch
spill_bytes_mismatch
shared_memory_mismatch
occupancy_tier_mismatch
sm_clock_mismatch
memory_clock_mismatch
power_limit_mismatch
temperature_out_of_range
cache_condition_unproven
concurrency_condition_unproven
cluster_shape_mismatch
```

Also test that multiple mismatches are all returned in deterministic order; the evaluator must not stop after the first comparison.

- [ ] **Step 2: Run and verify RED**

```bash
cmake --build build-sm120-exact --target exact_admission_test -j2
```

Expected: compilation fails because the evaluator does not exist.

- [ ] **Step 3: Implement the side-effect-free evaluator**

```cpp
struct ExactRunContract {
    std::string cache_condition;
    std::string concurrency_condition;
    Dim3 cluster_shape;
    std::uint64_t cache_condition_epoch;
};

struct ExactAdmissionDecision {
    bool allowed;
    std::string profile_id;
    std::string module_id;
    std::string kernel;
    std::vector<std::string> reasons;
};
```

The evaluator compares values; it performs no I/O and mutates no registry. Use exact equality for IDs, versions, digests, resource usage, clocks, power limit, cache/concurrency labels, and cluster shape. Temperature alone is an inclusive interval. No tolerance in this pre-launch gate may be borrowed from the post-run timing thresholds.

`ExactRunContract.cache_condition_epoch` must be nonzero and newer than the most recent relevant module/range mutation. This prevents a caller from merely writing `"cold"` without running the later cache-conditioning harness. Until Stage 4 supplies that harness, exact launches using a cold-cache profile correctly reject with `cache_condition_unproven`.

- [ ] **Step 4: Run property and unit tests GREEN**

Add a deterministic randomized loop that mutates 1-5 fields and proves `allowed == reasons.empty()`, no duplicate reason is emitted, and reason order is stable.

```bash
cmake --build build-sm120-exact --target exact_admission_test -j2
ctest --test-dir build-sm120-exact -R '^exact_admission$' --output-on-failure
```

- [ ] **Step 5: Commit admission logic**

```bash
git add include/hbfsim/exact_admission.hpp \
  src/cuda_runtime/exact_admission.cpp tests/cpu/exact_admission_test.cpp \
  CMakeLists.txt
git commit -m "feat: enforce sm120 exact admission"
```

### Task 7: Wire explicit exact fidelity through the public and launch-gate ABIs

**Files:**
- Modify: `include/hbfsim/api.h`
- Modify: `src/api.cpp`
- Modify: `src/cuda_runtime/context.cpp`
- Modify: `include/hbfsim/launch_gate_abi.hpp`
- Modify: `src/cuda_runtime/launch_gate.cpp`
- Modify: `tests/cpu/build_smoke.cpp`
- Modify: `tests/cpu/context_lifecycle_test.cpp`
- Modify: `tests/integration/daemon_protocol_test.cpp`
- Modify: `tests/integration/public_cuda_lifecycle_test.cpp`
- Modify: `tests/gpu/unsupported_kernel.cu`
- Modify: `tests/integration/test_timing_gate_binding.py`
- Modify: `tests/integration/test_cuda_module_association.py`

- [ ] **Step 1: Add failing ABI/default/fail-closed tests**

Keep the existing `hbfsim_options` and `hbfsim_context_create` binary ABI intact. Add a size-tagged V2 entry point for exact mode:

```c
enum hbfsim_fidelity {
    HBFSIM_FIDELITY_EMULATION = 0,
    HBFSIM_FIDELITY_EXACT_SM120 = 1
};

typedef struct hbfsim_options_v2 {
    uint32_t struct_bytes;
    hbfsim_options base;
    uint32_t fidelity;
    const char* exact_profile_path;
} hbfsim_options_v2;

int hbfsim_context_create_v2(const hbfsim_options_v2* options,
                             hbfsim_context** out);
```

Tests must prove:

- an old binary-layout `hbfsim_options` still uses `hbfsim_context_create` without any out-of-bounds read;
- public ABI reports 2;
- V2 rejects a too-small or unknown `struct_bytes` value;
- an invalid fidelity is rejected;
- exact fidelity with null/empty/malformed profile is rejected;
- exact fidelity rejects a V2/V3 launch gate instead of downgrading;
- exact fidelity rejects PTX/JIT evidence, missing live environment, pending validation, and any evaluator mismatch;
- emulation keeps the existing V2/V3 fallback and existing launch behavior.

- [ ] **Step 2: Run and verify RED**

```bash
cmake --build build-sm120-exact --target hbfsim_cpu_tests \
  context_lifecycle_test daemon_protocol_test -j2
ctest --test-dir build-sm120-exact \
  -R '^(build_smoke|context_lifecycle|daemon_protocol|timing_gate_binding)$' \
  --output-on-failure
```

Expected: ABI/version and exact-option assertions fail.

- [ ] **Step 3: Add launch-gate V4 without weakening older modes**

Define V4 as the complete V3 field prefix plus exact callbacks:

```cpp
inline constexpr std::uint32_t kLaunchGateAbiVersion = 4;

struct LaunchGateApiV4 {
    // Copy every V3 field here, in the same order.
    std::uint32_t abi_version;
    std::uint32_t struct_bytes;
    /* V3 callbacks ... */
    int (*configure_exact)(std::uintptr_t owner, std::uint64_t generation,
                           const char* profile_json,
                           std::size_t profile_bytes) noexcept;
    int (*publish_run_contract)(std::uintptr_t owner,
                                std::uint64_t generation,
                                const ExactRunContractAbi*) noexcept;
};
```

Keep explicit V2 and V3 constants/structs for emulation compatibility. Do not cast a V4 pointer through a structurally different nested type; the actual struct must retain the complete V3 fields as a literal prefix, matching the existing ABI pattern.

Define `ExactRunContractAbi` as a size-tagged POD containing numeric cache/concurrency enums, cluster X/Y/Z, and a cache epoch; do not put `std::string` or C++ layout into the ABI.

At context creation:

1. load the media profile as today;
2. route the old entry point through an internal normalized emulation-only options object;
3. in the V2 entry point, validate `struct_bytes` before reading any added field;
4. when exact fidelity is requested, read and strictly parse the exact profile once;
5. require V4, activate the owner, and pass the already-read JSON bytes to `configure_exact`;
6. if configuration fails, retire the owner and fail context creation;
7. do not export profile paths or silently switch fidelity.

At every HBF-relevant launch, refresh the live environment, look up the loaded module evidence and run contract, evaluate admission, append the decision, and call CUDA only when admitted. Clear exact owner/profile/run-contract state on successful retirement, context destruction, module unload, and CUDA context reset.

- [ ] **Step 4: Run ABI and lifecycle tests GREEN**

```bash
cmake --build build-sm120-exact -j2
ctest --test-dir build-sm120-exact \
  -R '^(build_smoke|context_lifecycle|daemon_protocol|timing_gate_binding|cuda_module_association|public_cuda_lifecycle)$' \
  --output-on-failure
```

Expected: exact mode is accepted only through V4 with matching AOT evidence; emulation regressions remain green.

- [ ] **Step 5: Commit the explicit fidelity ABI**

```bash
git add include/hbfsim/api.h src/api.cpp src/cuda_runtime/context.cpp \
  include/hbfsim/launch_gate_abi.hpp src/cuda_runtime/launch_gate.cpp \
  tests/cpu/build_smoke.cpp tests/cpu/context_lifecycle_test.cpp \
  tests/integration/daemon_protocol_test.cpp \
  tests/integration/public_cuda_lifecycle_test.cpp \
  tests/gpu/unsupported_kernel.cu \
  tests/integration/test_timing_gate_binding.py \
  tests/integration/test_cuda_module_association.py
git commit -m "feat: expose fail-closed sm120 exact mode"
```

### Task 8: Report exact evidence and prevent false `exact` labels

**Files:**
- Modify: `include/hbfsim/coverage.hpp`
- Modify: `src/cuda_runtime/coverage.cpp`
- Modify: `src/reporting/coverage_writer.cpp`
- Modify: `src/ptxpass_hbf/plugin.cpp`
- Modify: `tests/cpu/coverage_gate_test.cpp`
- Modify: `tests/integration/coverage_manifest_flow_test.cpp`
- Modify: `tests/integration/test_ptxpass_plugin.py`

- [ ] **Step 1: Add failing manifest and report assertions**

Extend module manifests with pass-stage identity only:

```json
{
  "manifest_schema_version": 2,
  "module_id": "ptx:sha256:...",
  "original_ptx_sha256": "...",
  "transformed_ptx_sha256": "...",
  "aot_required_for_exact": true
}
```

Do not let the PTX pass invent cubin/SASS hashes or resource counts; those come only from the artifact builder. Extend `GateDecision` and JSONL output with:

```cpp
std::string requested_fidelity;
std::string admitted_fidelity;
std::string exact_profile_id;
std::string cubin_sha256;
std::string sass_sha256;
std::vector<std::string> exact_rejection_reasons;
bool aot_verified{false};
bool validation_passed{false};
```

Tests must assert that `admitted_fidelity == "exact"` implies all of: allowed, AOT verified, validation passed, nonempty profile/cubin/SASS IDs, and no rejection reasons. A pending profile must report `calibrated_emulation`, not `exact`.

- [ ] **Step 2: Run and verify RED**

```bash
cmake --build build-sm120-exact --target coverage_gate_test \
  coverage_manifest_flow_test ptxpass_hbf_plugin -j2
ctest --test-dir build-sm120-exact \
  -R '^(coverage_gate|coverage_manifest_flow|ptxpass_plugin)$' \
  --output-on-failure
```

Expected: new manifest/report field assertions fail.

- [ ] **Step 3: Implement provenance-separated reporting**

The pass records only facts it can prove from original/transformed PTX. The builder produces a separate artifact record. The runtime joins them by module ID and transformed PTX hash, then attaches live admission evidence.

Serialize rejection reasons as a JSON array in deterministic order. Preserve the existing `reason` field as the primary launch disposition for compatibility, but set it to `exact_admission_failed` when exact evaluation rejects and put details in `exact_rejection_reasons`.

Add this invariant immediately before JSON serialization:

```cpp
if (decision.admitted_fidelity == "exact" &&
    (!decision.allowed || !decision.aot_verified ||
     !decision.validation_passed ||
     !decision.exact_rejection_reasons.empty())) {
    throw std::logic_error("invalid exact coverage decision");
}
```

- [ ] **Step 4: Run report tests GREEN**

```bash
cmake --build build-sm120-exact --target coverage_gate_test \
  coverage_manifest_flow_test ptxpass_hbf_plugin -j2
ctest --test-dir build-sm120-exact \
  -R '^(coverage_gate|coverage_manifest_flow|ptxpass_plugin)$' \
  --output-on-failure
```

- [ ] **Step 5: Commit exact evidence reporting**

```bash
git add include/hbfsim/coverage.hpp src/cuda_runtime/coverage.cpp \
  src/reporting/coverage_writer.cpp src/ptxpass_hbf/plugin.cpp \
  tests/cpu/coverage_gate_test.cpp \
  tests/integration/coverage_manifest_flow_test.cpp \
  tests/integration/test_ptxpass_plugin.py
git commit -m "feat: report exact admission evidence"
```

### Task 9: Add the Stage 1 orchestration and proof gate

**Files:**
- Create: `scripts/calibration/prepare_sm120_exact.py`
- Create: `scripts/calibration/check_sm120_exact_admission.py`
- Create: `tests/integration/test_prepare_sm120_exact.py`
- Create: `tests/integration/test_sm120_exact_admission.py`
- Create: `docs/sm120-exact-mode.md`
- Modify: `README.md`
- Modify: `CMakeLists.txt`

- [ ] **Step 1: Write failing end-to-end orchestration tests**

With fake pass/tool/GPU providers, test this complete data flow:

```text
original PTX
  -> PTX pass + pass manifest
  -> fixed AOT builder + artifact bundle
  -> profile/artifact join
  -> exact context configuration
  -> AOT module-load evidence
  -> pre-launch exact admission JSON
```

Cases must include a fully admitted fixture and independent failures for changed original PTX, transformed PTX, cubin, SASS, register count, tool version, GPU identity, clock, temperature, cache epoch, validation dataset, and JIT loading. Verify every failure occurs before the fake CUDA launch counter increments.

- [ ] **Step 2: Run and verify RED**

```bash
python3 tests/integration/test_prepare_sm120_exact.py
python3 tests/integration/test_sm120_exact_admission.py \
  build-sm120-exact/libhbfsim_launch_gate.so
```

Expected: orchestration entry points do not exist.

- [ ] **Step 3: Implement the non-mutating preparation and admission commands**

`prepare_sm120_exact.py` invokes the existing PTX pass offline, writes the transformed PTX/pass manifest, invokes `build_sm120_artifact.py`, and emits a profile fragment with `validation.status: "pending"`. It must never change GPU clocks or claim validation passed.

`check_sm120_exact_admission.py` performs a dry run: load profile, re-hash every referenced artifact, collect the live environment, and print one JSON decision. Exit codes:

```text
0  admissible exact profile and artifact
2  valid inputs but exact admission rejected
64 malformed arguments/profile
66 missing or unsafe path
70 tool/runtime failure
```

The command must state explicitly that Stage 1 admission proves identity and environmental reproducibility, not LDG/STG/TMA/channel timing fidelity.

- [ ] **Step 4: Document the operator contract**

`docs/sm120-exact-mode.md` must include:

- the one supported GPU and `sm_120` target boundary;
- exact CUDA 13 and Nsight Compute version capture;
- offline PTX-pass and AOT-bundle commands;
- read-only admission dry run;
- exact launch command through the stamped bpftime copy;
- every rejection reason and remediation;
- the distinction between `emulation`, `calibrated_emulation`, and `exact`;
- an explicit statement that Stage 1 alone does not support async LDG/STG, TMA, TensorMap, or 4+2 channels yet.

Do not document manual JSON edits as a way to turn `pending` into `passed`; only the Stage 4 independent validation command may produce that transition.

- [ ] **Step 5: Run the Stage 1 proof gate**

```bash
cmake --build build-sm120-exact -j2
ctest --test-dir build-sm120-exact \
  -R '^(exact_|build_sm120_artifact|bpftime_aot_patch|cuda_module_association|coverage_|ptxpass_plugin|device_helper_ptx)' \
  --output-on-failure
python3 tests/integration/test_live_sm120_environment.py \
  build-sm120-exact/libhbfsim_launch_gate.so
git diff --check
git submodule foreach --recursive 'test -z "$(git status --porcelain)"'
```

Expected:

- all CPU/fake integration tests pass;
- CUDA 13 assembles and disassembles a real `sm_120` artifact;
- the live target is identified read-only as CC 12.0 with nonzero physical telemetry;
- a pending/unvalidated profile is rejected as exact;
- no dirty submodule or whitespace error remains.

This is the Stage 1 completion boundary. It does **not** satisfy the full design's correctness/timing gates; those require Stages 2-4.

- [ ] **Step 6: Commit Stage 1 orchestration and documentation**

```bash
git add scripts/calibration/prepare_sm120_exact.py \
  scripts/calibration/check_sm120_exact_admission.py \
  tests/integration/test_prepare_sm120_exact.py \
  tests/integration/test_sm120_exact_admission.py \
  docs/sm120-exact-mode.md README.md CMakeLists.txt
git commit -m "docs: add sm120 exact foundation workflow"
```

## Stage 1 handoff artifacts

Before beginning Stage 2, retain these exact proof artifacts under a new, non-source results directory and record their paths in the Stage 2 plan:

```text
original PTX SHA-256
transformed PTX SHA-256
cubin SHA-256
SASS SHA-256
per-kernel resource records
toolchain version records
live GPU/NVML snapshot
exact admission JSONL record
CTest log
```

The Stage 2 plan may consume the schema, AOT identity, resource budgets, and admission APIs. It must not weaken any Stage 1 rejection into a warning, and it must continue using test-first RED/GREEN steps for the PTX IR and ordinary load/store future implementation.
