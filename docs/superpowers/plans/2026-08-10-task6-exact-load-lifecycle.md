# Task 6 Exact Load and Context Lifecycle Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace process-global CUDA module expectations with one exact thread-local load transaction and invalidate every host association after successful CUDA context teardown.

**Architecture:** The launch gate parses the canonical identity from final patched PTX into a one-load thread-local transaction. A minimal patch to a copied pinned-bpftime source tree brackets only its cache-miss `cuModuleLoadDataEx`; the gate consumes that transaction before the driver call and associates only a successful exact live-marker match. Successful module/context lifecycle calls update a conservative process-wide association registry, while failed calls leave live associations unchanged.

**Tech Stack:** C++20, CUDA 12.8/13 driver and runtime ABIs, POSIX `dlsym`/`LD_PRELOAD`, CMake/CTest, Bash, Python 3, git patch artifacts.

---

### Task 1: Replace pending identities with exact thread-local transactions

**Files:**
- Modify: `include/hbfsim/module_identity.hpp`
- Modify: `src/cuda_runtime/module_identity.cpp`
- Modify: `tests/cpu/module_identity_test.cpp`

- [ ] **Step 1: Write failing parser and transaction tests**

Replace expectation-oriented tests with cases that construct the exact canonical declaration emitted by the pass and assert:

```cpp
const auto ptx = canonical_ptx(first);
const auto token = transactions.begin(ptx);
CHECK(token != 0);
CHECK(transactions.take() == first);
CHECK(!transactions.take().has_value());

CHECK(transactions.begin(".visible .entry kernel() { ret; }") == 0);
CHECK(transactions.begin(ptx + ptx) == 0);

const auto nested = transactions.begin(ptx);
CHECK(nested != 0);
CHECK(transactions.begin(canonical_ptx(second)) == 0);
transactions.end(nested);
CHECK(!transactions.take().has_value());
```

Run two `std::async` workers behind a barrier. Worker A begins and takes identity A; worker B begins and takes identity B. Assert neither thread observes the other identity. Replace registry expectation tests with direct trusted association, `erase`, `clear`, duplicate-handle rejection, and handle reuse after clear.

- [ ] **Step 2: Run the CPU test and verify RED**

Run:

```bash
cmake --build build-task6-release --target module_identity_test -j2
ctest --test-dir build-task6-release -R '^module_identity$' --output-on-failure
```

Expected: compilation fails because `ModuleLoadTransactionStore`, `begin`, `take`, `end`, and `ModuleIdentityRegistry::clear` do not exist.

- [ ] **Step 3: Implement strict parsing and TLS consumption**

Define this public surface:

```cpp
using ModuleLoadToken = std::uint64_t;

class ModuleLoadTransactionStore {
  public:
    [[nodiscard]] ModuleLoadToken begin(std::string_view ptx) noexcept;
    [[nodiscard]] std::optional<ModuleIdentity> take() noexcept;
    void end(ModuleLoadToken token) noexcept;

  private:
    struct Entry {
        ModuleLoadToken token;
        ModuleIdentity identity;
    };
    static thread_local std::optional<Entry> current_;
    std::atomic<ModuleLoadToken> next_token_{1};
};
```

The parser must accept exactly one declaration matching this complete shape and exactly 32 comma-separated lowercase two-digit hex bytes:

```text
\.visible \.const \.align 8 \.b8 __hbfsim_module_identity\[32\] = \{0x[0-9a-f]{2}(, 0x[0-9a-f]{2}){31}\};
```

Reject absent, duplicate, truncated, extended, or noncanonical declarations. A nested `begin` clears the older current-thread transaction before returning zero, so rejection cannot leave stale trust. `take()` moves and clears the current thread's entry. `end(token)` clears only the still-live matching token. Generate nonzero tokens even across counter wrap. Remove `expect`, the pending set, and `discard_expectations`; make `associate` insert only into the handle map and add `clear()`.

- [ ] **Step 4: Run the transaction test and verify GREEN**

Run the Step 2 commands. Expected: `module_identity` passes and concurrent workers retain distinct identities.

### Task 2: Make pass output inert until the exact bpftime load

**Files:**
- Modify: `src/ptxpass_hbf/plugin.cpp`
- Modify: `tests/integration/test_ptxpass_plugin.py`
- Modify: `CMakeLists.txt`

- [ ] **Step 1: Make the plugin test reject publication**

Change the source assertions to require the embedded canonical marker but reject all occurrences of:

```python
for forbidden in ("hbfsim_expect_module_identity", "publish_module_expectation",
                  "dlsym(RTLD_DEFAULT"):
    require(forbidden not in source,
            f"PTX pass still publishes stale process-global trust: {forbidden}")
```

- [ ] **Step 2: Run the plugin test and verify RED**

Run:

```bash
cmake --build build-task6-release --target ptxpass_hbf_plugin -j2
ctest --test-dir build-task6-release -R '^ptxpass_plugin$' --output-on-failure
```

Expected: failure because the plugin still resolves and calls `hbfsim_expect_module_identity`.

- [ ] **Step 3: Remove global publication**

Delete `publish_module_expectation`, its call after successful output copy, and the now-unused `<dlfcn.h>` include. Remove `${CMAKE_DL_LIBS}` from `ptxpass_hbf_plugin`; retain `TrustedModuleRegistry`, marker injection, manifest output, and pass-chain validation.

- [ ] **Step 4: Run the plugin test and verify GREEN**

Run the Step 2 commands. Expected: `ptxpass_plugin` passes without any process-global trust callback.

### Task 3: Add the minimal pinned-bpftime load bridge as a patch artifact

**Files:**
- Create: `patches/bpftime/0001-exact-module-load-provenance.patch`
- Create through the patch: `attach/nv_attach_impl/nv_attach_module_load_provenance.hpp`
- Create through the patch: `attach/nv_attach_impl/nv_attach_module_load_provenance.cpp`
- Create through the patch: `attach/nv_attach_impl/test/test_module_load_provenance.cpp`
- Modify through the patch: `attach/nv_attach_impl/nv_attach_fatbin_record.cpp`
- Modify through the patch: `attach/nv_attach_impl/CMakeLists.txt`
- Modify through the patch: `attach/nv_attach_impl/test/CMakeLists.txt`
- Create: `tests/integration/test_bpftime_patch.py`
- Modify: `CMakeLists.txt`

- [ ] **Step 1: Write a failing patch-contract test**

The test must run:

```bash
git -C third_party/bpftime apply --check \
  ../../patches/bpftime/0001-exact-module-load-provenance.patch
```

It must also inspect the patch and require both callback names, the direct `cuModuleLoadDataEx` bracketing site, a CPU test covering absent callbacks, partial callbacks, begin returning zero, successful begin/end, and no bridge call in the `module_pool` hit branch. Register it as `bpftime_patch` in CTest.

- [ ] **Step 2: Run the patch-contract test and verify RED**

Run:

```bash
ctest --test-dir build-task6-release -R '^bpftime_patch$' --output-on-failure
```

Expected: failure because the patch artifact does not exist.

- [ ] **Step 3: Create the bridge patch without dirtying the submodule**

Generate the artifact from a temporary copy of pinned bpftime. The bridge exposes a focused scope object with injectable hooks:

```cpp
using begin_module_load_fn = uint64_t (*)(const char *, size_t);
using end_module_load_fn = void (*)(uint64_t);

struct module_load_provenance_hooks {
    begin_module_load_fn begin;
    end_module_load_fn end;
};

class module_load_provenance_scope {
  public:
    module_load_provenance_scope(std::string_view ptx,
                                 module_load_provenance_hooks hooks);
    ~module_load_provenance_scope();
    bool permits_load() const;
};
```

`find_module_load_provenance_hooks()` resolves both symbols from `RTLD_DEFAULT`. Both null permits standalone loading. Exactly one null or a zero token rejects loading. A nonzero token permits loading and the destructor calls end exactly once. In `fatbin_record::try_loading_ptxs`, construct the scope from the final `ptx` immediately before the existing cache-miss driver call and throw `std::runtime_error` without calling CUDA when `permits_load()` is false. Do not add a scope in the `module_pool` hit branch.

The upstreamable Catch2 test injects fake callbacks and proves: both absent permits; partial pair rejects; zero-token begin rejects without end; nonzero begin permits and ends once with the same token.

- [ ] **Step 4: Verify patch applicability and submodule cleanliness GREEN**

Run:

```bash
cmake --build build-task6-release -j2
ctest --test-dir build-task6-release -R '^bpftime_patch$' --output-on-failure
git submodule foreach --recursive 'test -z "$(git status --porcelain)"'
```

Expected: patch applies to pinned `ec26daecc8e787fb80fd95dd596a576404a5e36e`, the contract test passes, and every submodule remains clean.

### Task 4: Build only a stamped patched bpftime copy

**Files:**
- Create: `cmake/PreparePatchedBpftime.cmake`
- Create: `scripts/build_patched_bpftime.sh`
- Modify: `scripts/run_with_bpftime.sh`
- Modify: `tests/integration/test_run_with_bpftime.py`
- Create: `tests/integration/test_build_patched_bpftime.py`
- Modify: `CMakeLists.txt`

- [ ] **Step 1: Write failing preparation and stamp tests**

`test_build_patched_bpftime.py` invokes the build helper's `--check` mode and requires that it verifies the pinned commit and `git apply --check` without modifying the submodule. `test_run_with_bpftime.py` first omits the stamp and expects exit 66 with `bpftime build provenance is missing`; it then writes:

```text
bpftime_commit=ec26daecc8e787fb80fd95dd596a576404a5e36e
patch_sha256=94f9b90f73f56d5ca73002009e01944b33e5c07cb49635435841fbadb1f3cfaa
bridge_version=1
```

where the shown digest is replaced in the test by `hashlib.sha256(patch.read_bytes()).hexdigest()`, then verifies the existing wrapper behavior. Wrong commit, wrong digest, and wrong bridge version must each fail before the loader starts.

- [ ] **Step 2: Run both tests and verify RED**

Run:

```bash
ctest --test-dir build-task6-release \
  -R '^(build_patched_bpftime|run_with_bpftime)$' --output-on-failure
```

Expected: the new test is absent/fails and the wrapper accepts an unstamped fake build.

- [ ] **Step 3: Implement copied-source preparation, build, and stamp**

`cmake/PreparePatchedBpftime.cmake` receives `HBFSIM_SOURCE`, `BPFTIME_SOURCE`, `PATCH`, and `OUTPUT_SOURCE`; it verifies the pinned commit, deletes/recreates only the explicit copied-source path, copies the source excluding root `.git` and `build`, applies `git apply --check`, then applies the patch.

`scripts/build_patched_bpftime.sh` supports:

```text
scripts/build_patched_bpftime.sh --check
scripts/build_patched_bpftime.sh /absolute/build-bpftime-hbfsim -DCMAKE_BUILD_TYPE=Release
```

The build form prepares `${BUILD_DIR}/_deps/bpftime-hbfsim-src`, configures and builds bpftime there, verifies the agent and syscall-server libraries, and only then writes `${BUILD_DIR}/hbfsim-bpftime.provenance` atomically with the pinned commit, patch SHA-256, and bridge version 1.

Update `run_with_bpftime.sh` to calculate the recorded patch digest and require an exact three-line stamp before starting the loader. Keep `HBFSIM_BPFTIME_BUILD_DIR` override support and set its default to `$HBFSIM_ROOT/build-bpftime-hbfsim`, matching the build helper's documented default.

- [ ] **Step 4: Run preparation and wrapper tests GREEN**

Run the Step 2 command plus:

```bash
bash -n scripts/build_patched_bpftime.sh scripts/run_with_bpftime.sh
```

Expected: helper check mode, missing/bad stamp rejection, valid stamp behavior, and shell syntax all pass without building or running CUDA code.

### Task 5: Consume the exact transaction in module loading

**Files:**
- Modify: `src/cuda_runtime/launch_gate.cpp`
- Modify: `tests/integration/fake_cuda_lookup.cpp`
- Modify: `tests/integration/test_cuda_module_association.py`
- Modify: `tests/integration/test_launch_gate_symbols.py`

- [ ] **Step 1: Rewrite lifecycle integration tests for exact begin/end**

Load final PTX output from the real pass and call `hbfsim_begin_module_load_from_ptx` immediately before the fake load. Assert malformed PTX, duplicate marker, and nested begin return zero. Prove copied-marker load without begin rejects; matching begin/load succeeds; missing live marker and mismatched live marker load but do not associate; failed load consumes only its transaction; end cancels an unconsumed token; and the same fake handle reused after unload rejects.

Add a two-thread barrier in the fake driver so thread A's `image == "fail-a"` fails while thread B loads identity B successfully. Assert B launches and a later A-marker spoof does not. Retain the module-cache reuse assertion, but do not call begin for the reuse launch.

- [ ] **Step 2: Run exact-load tests and verify RED**

Run:

```bash
cmake --build build-task6-release --target \
  fake_cuda_lookup hbfsim_launch_gate ptxpass_hbf_plugin -j2
ctest --test-dir build-task6-release \
  -R '^(module_identity|ptxpass_plugin|cuda_module_association|launch_gate_symbols)$' \
  --output-on-failure
```

Expected: failures because old `hbfsim_expect_module_identity` and global discard semantics remain.

- [ ] **Step 3: Export begin/end and consume before the original load**

Replace the old callback with:

```cpp
extern "C" std::uint64_t
hbfsim_begin_module_load_from_ptx(const char* ptx, std::size_t size) noexcept;
extern "C" void hbfsim_end_module_load(std::uint64_t token) noexcept;
```

`cuModuleLoadDataEx` must call `transactions.take()` before resolving or invoking the original. On original success, read the live identity once and call `associate` only for an exact transaction match. Never clear another thread's transaction. Update symbol tests to reject `hbfsim_expect_module_identity`, `expected_`, and `discard_expectations`.

- [ ] **Step 4: Run exact-load tests GREEN**

Run the Step 2 commands. Expected: all four focused tests pass, including concurrent A-fails/B-succeeds.

### Task 6: Clear associations on every destructive context path

**Files:**
- Modify: `src/cuda_runtime/launch_gate.cpp`
- Modify: `tests/integration/fake_cuda_lookup.cpp`
- Modify: `tests/integration/test_cuda_lookup_interposition.py`
- Modify: `tests/integration/test_cuda_module_association.py`
- Modify: `tests/integration/test_launch_gate_symbols.py`

- [ ] **Step 1: Write failing lifecycle and lookup tests**

Extend the fake driver with success/failure controls and exact ABI exports for:

```text
cuCtxDestroy, cuCtxDestroy_v2
cuDevicePrimaryCtxReset, cuDevicePrimaryCtxReset_v2
cuDevicePrimaryCtxRelease, cuDevicePrimaryCtxRelease_v2
cuGreenCtxDestroy
cudaDeviceReset
cudaThreadExit (CUDA 12 only)
```

For every function, establish a trusted association, force failure and prove launch remains authorized, then force success and prove the same numeric module handle reused by an untrusted load is rejected. Extend all six driver-entry API lookup rows to require local wrapper addresses for every supported `cu*` lifecycle spelling.

- [ ] **Step 2: Run lifecycle tests and verify RED**

Run:

```bash
cmake --build build-task6-release --target fake_cuda_lookup hbfsim_launch_gate -j2
ctest --test-dir build-task6-release \
  -R '^(cuda_lookup_interposition|cuda_module_association|launch_gate_symbols)$' \
  --output-on-failure
```

Expected: lifecycle lookup rows return fake originals and successful context teardown leaves the association live.

- [ ] **Step 3: Add success-only conservative clearing wrappers**

Implement direct wrappers that call the exact `RTLD_NEXT` spelling and invoke `module_identities().clear()` only on `CUDA_SUCCESS` or `cudaSuccess`. Use explicit declarations/`#undef` handling so both legacy and `_v2` driver symbols are exported. Guard `cudaThreadExit` with `CUDART_VERSION < 13000` and `cuGreenCtxDestroy` with `CUDA_VERSION >= 12040`.

Add every driver spelling to `interposed_wrapper_address`; `cuGetProcAddress`, `cuGetProcAddress_v2`, and all four runtime driver-entry wrappers already share this table. Keep runtime reset functions as direct runtime interposition rather than invalid driver-entry query names.

- [ ] **Step 4: Run CUDA 12 and CUDA 13 focused lifecycle tests GREEN**

Run the Step 2 commands, then:

```bash
cmake --build build-task6-cuda13-static-release --target \
  fake_cuda_lookup hbfsim_launch_gate -j2
ctest --test-dir build-task6-cuda13-static-release \
  -R '^(cuda_lookup_interposition|cuda_module_association|launch_gate_symbols)$' \
  --output-on-failure
```

Expected: both matrices pass; CUDA 12 includes `cudaThreadExit`, CUDA 13 does not, and CUDA 12.4+ includes green-context cleanup.

### Task 7: Full static verification, review, and focused commit

**Files:**
- Review: all files changed by Tasks 1-6

- [ ] **Step 1: Run source and dependency hygiene**

Run:

```bash
bash -n scripts/build_patched_bpftime.sh scripts/run_with_bpftime.sh
python3 -m py_compile \
  tests/integration/test_bpftime_patch.py \
  tests/integration/test_build_patched_bpftime.py \
  tests/integration/test_cuda_lookup_interposition.py \
  tests/integration/test_cuda_module_association.py \
  tests/integration/test_launch_gate_symbols.py \
  tests/integration/test_ptxpass_plugin.py \
  tests/integration/test_run_with_bpftime.py
git diff --check
git submodule foreach --recursive 'test -z "$(git status --porcelain)"'
```

Expected: every command exits zero and the bpftime gitlink/submodule remains unchanged.

- [ ] **Step 2: Run full Release CPU/static matrix**

Run:

```bash
cmake --build build-task6-release -j2
ctest --test-dir build-task6-release --output-on-failure
```

Expected: every test passes; no GPU executable is run.

- [ ] **Step 3: Run full CUDA 13 static matrix**

Run:

```bash
cmake --build build-task6-cuda13-static-release -j2
ctest --test-dir build-task6-cuda13-static-release --output-on-failure
```

Expected: every CPU/static and PTX-assembly test passes; no GPU executable is run.

- [ ] **Step 4: Review exact scope and commit**

Require the diff to contain the HBFSim sources/tests/scripts/CMake files and `patches/bpftime/0001-exact-module-load-provenance.patch`, but no README change, submodule gitlink change, generated build artifact, or unrelated file. Stage explicit paths and commit:

```bash
git commit -m "fix: make CUDA module trust transaction exact"
```

Expected: the worktree and all recursive submodules are clean; do not push.
