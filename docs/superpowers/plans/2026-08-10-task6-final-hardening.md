# Task 6 Final Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the remaining CUDA lookup, directory durability, and trusted module-provenance review blockers without GPU execution or pinned-submodule changes.

**Architecture:** CUDA driver-entry lookup tests use only real `cu*` query names and exercise each ABI and default-stream mode against rejecting fake originals. A shared host registry receives one-shot exact identities from the trusted PTX pass, associates them atomically with successful `cuModuleLoadDataEx` results, and removes associations only after successful unload. Durable append retains its `flock` and file descriptor until a newly created file's parent directory has been synchronized.

**Tech Stack:** C++20, CUDA 12.8/13 driver and runtime ABIs, `LD_PRELOAD` fake-driver integration tests, POSIX `flock`/`fdatasync`/`fsync`, CMake/CTest, Python 3.

---

### Task 1: Correct CUDA driver-entry lookup contracts

**Files:**
- Modify: `tests/integration/fake_cuda_lookup.cpp`
- Modify: `tests/integration/test_cuda_lookup_interposition.py`
- Modify: `tests/integration/test_launch_gate_symbols.py`
- Modify: `src/cuda_runtime/launch_gate.cpp`

- [ ] **Step 1: Write table-driven failing lookup tests**

Make the fake lookup originals reject names not beginning with `cu`. Table-drive `cuLaunch`, `cuLaunchGrid`, `cuLaunchGridAsync`, `cuLaunchKernel`, `cuLaunchKernelEx`, `cuLaunchCooperativeKernel`, `cuLaunchCooperativeKernelMultiDevice`, and `cuGraphLaunch`, including explicit `_ptsz` names and default/PTDS selection where a PTDS wrapper exists. Invoke legacy `cuGetProcAddress`, `cuGetProcAddress_v2`, `cudaGetDriverEntryPoint`, `_ptsz`, `ByVersion`, and `ByVersion_ptsz` with their exact installed-header ABIs. Assert invalid `cudaLaunchKernel` queries fail in the fake original and are never substituted.

- [ ] **Step 2: Run the lookup test and verify RED**

Run:

```bash
cmake --build build-task6-release --target fake_cuda_lookup hbfsim_launch_gate -j2
ctest --test-dir build-task6-release -R '^(cuda_lookup_interposition|launch_gate_symbols)$' --output-on-failure
```

Expected: `cuda_lookup_interposition` fails because the current test and substitution table use invalid runtime API names for driver-entry queries.

- [ ] **Step 3: Restrict lookup substitution to real driver names**

Keep runtime launch exports for ordinary symbol interposition, but make the driver-entry substitution table accept only the real `cu*` query names. Preserve explicit local wrapper addresses, successful-original-only substitution, default/PTDS semantics, and the legacy/v2 ABI split. Include lifecycle wrappers added in Task 3 so dynamically queried load/unload calls cannot bypass association cleanup.

- [ ] **Step 4: Run the focused lookup tests and verify GREEN**

Run the Step 2 commands. Expected: both tests pass under CUDA 12.8; repeat the same targets in `build-task6-cuda13-static-release` and expect both to pass under CUDA 13.

### Task 2: Hold append serialization through directory durability

**Files:**
- Modify: `tests/cpu/durable_append_test.cpp`
- Modify: `src/reporting/durable_append.cpp`

- [ ] **Step 1: Write a controlled concurrent-first-create test**

Extend the existing linker-wrapped `fsync` test hook with a condition-variable gate. Block the creator at the real parent-directory `fsync`, start a second appender for the same new file, and assert the second future remains blocked until the directory gate is released. Retain the existing EINTR and short-write assertions.

- [ ] **Step 2: Run the durability test and verify RED**

Run:

```bash
cmake --build build-task6-release --target durable_append_test -j2
ctest --test-dir build-task6-release -R '^durable_append$' --output-on-failure
```

Expected: failure because the creator currently closes the file descriptor and releases `flock` before synchronizing the parent directory.

- [ ] **Step 3: Move close after parent synchronization**

Keep the data file descriptor and exclusive `flock` live after `fdatasync`. If the call created the file, open and synchronize the parent directory while still holding the file lock. Close the file only after the parent sync and directory close have completed; preserve cleanup on every exception path.

- [ ] **Step 4: Run the durability test and verify GREEN**

Run the Step 2 commands. Expected: the creator reaches the controlled directory gate, the second appender blocks, and both complete with two intact JSONL records after release.

### Task 3: Bind launches to trusted module-load provenance

**Files:**
- Create: `include/hbfsim/module_identity.hpp`
- Create: `src/cuda_runtime/module_identity.cpp`
- Create: `tests/cpu/module_identity_test.cpp`
- Modify: `CMakeLists.txt`
- Modify: `src/ptxpass_hbf/plugin.cpp`
- Modify: `src/cuda_runtime/launch_gate.cpp`
- Modify: `tests/integration/fake_cuda_lookup.cpp`
- Create: `tests/integration/test_cuda_module_association.py`
- Modify: `tests/integration/test_launch_gate_symbols.py`

- [ ] **Step 1: Write failing registry state tests**

Define tests for exact one-shot expectation consumption, duplicate expectation coalescing, two concurrent distinct identities associating only with their matching handles, missing-expectation rejection, successful unload erasure, failed-unload retention, and reused-handle lookup after erasure. Run the new target and verify it fails because `ModuleIdentityRegistry` does not exist.

- [ ] **Step 2: Implement the minimal thread-safe registry**

Store pending identities as an exact set and live associations as a handle-to-identity map under one mutex. `associate(handle, identity)` atomically consumes only the matching pending identity. Provide lookup, successful-unload erasure, and fail-closed pending-discard support for a failed module-load attempt. Format returned IDs through the existing `ptx:sha256:` helper.

- [ ] **Step 3: Write a failing fake-driver lifecycle test**

Extend the fake `libcuda.so.1` with `cuModuleLoadDataEx`, `cuModuleUnload`, `cuFuncGetModule`, `cuFuncGetName`, `cuModuleGetGlobal_v2`, `cuMemcpyDtoH_v2`, and `cuLaunchKernel`. In a preload child, register an HBF range and manifest, then prove: a copied-identity cubin without an expectation is rejected; a matching trusted expectation associates and launches; a failed load creates no association; successful unload followed by reuse of the same fake handle rejects a spoof; failed unload retains the valid association; and bpftime-style reuse without unloading remains valid.

- [ ] **Step 4: Verify fake-driver lifecycle RED**

Run:

```bash
cmake --build build-task6-release --target fake_cuda_lookup hbfsim_launch_gate -j2
ctest --test-dir build-task6-release -R '^cuda_module_association$' --output-on-failure
```

Expected: failure because no expectation callback or module-load/unload association exists and launch identity still comes directly from the embedded constant.

- [ ] **Step 5: Wire trusted pass expectations and module lifecycle hooks**

After a successful pass response, resolve the gate's internal expectation callback with `dlsym(RTLD_DEFAULT, ...)` and publish the exact 32-byte identity when the gate is present. Export the callback from the gate, interpose the pinned bpftime `cuModuleLoadDataEx` path, read the live constant only after a successful original load, and atomically associate on an exact pending match. Clear pending trust on failed load. Interpose `cuModuleUnload` and erase only after the original succeeds. Change launch identity resolution to consult only the host association.

- [ ] **Step 6: Run registry, fake-driver, lookup, and pass tests GREEN**

Run:

```bash
cmake --build build-task6-release --target module_identity_test fake_cuda_lookup hbfsim_launch_gate ptxpass_hbf_plugin -j2
ctest --test-dir build-task6-release -R '^(module_identity|cuda_module_association|cuda_lookup_interposition|launch_gate_symbols|ptxpass_plugin|coverage_gate)$' --output-on-failure
```

Expected: all focused tests pass without a CUDA device.

### Task 4: Final static verification and handoff

**Files:**
- Modify if needed: `docs/superpowers/specs/2026-08-09-hbfsim-hybrid-design.md`

- [ ] **Step 1: Verify scripts and source hygiene**

Run:

```bash
bash -n scripts/run_with_bpftime.sh
python3 -m py_compile tests/integration/test_cuda_lookup_interposition.py tests/integration/test_cuda_module_association.py
git diff --check
git submodule foreach --recursive 'test -z "$(git status --porcelain)"'
```

Expected: all commands succeed and submodules remain clean.

- [ ] **Step 2: Run full Release CPU/static matrix**

Run:

```bash
cmake --build build-task6-release -j2
ctest --test-dir build-task6-release --output-on-failure
```

Expected: every test passes; no GPU executable is run.

- [ ] **Step 3: Run full CUDA 13 static Release matrix**

Run:

```bash
cmake --build build-task6-cuda13-static-release -j2
ctest --test-dir build-task6-cuda13-static-release --output-on-failure
```

Expected: every CPU/static and PTX assembly test passes; no GPU executable is run.

- [ ] **Step 4: Commit focused implementation**

Stage only Task 6 source, tests, CMake, and approved spec/plan files. Commit with:

```bash
git commit -m "fix: bind CUDA launches to trusted module loads"
```
