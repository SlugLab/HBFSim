# Manual source reconstruction and relocation

Status: source/recipe publication; **NOT_REBUILT**, cross-machine behavior **UNVERIFIED**. Historical compiler commands and successful run evidence are preserved, but this is not a claim that copying old archives recreates the system. Commands below are DOC_DERIVED reconstruction steps unless a linked receipt explicitly records actual argv.

## Inputs and paths

Set `PACKET` to the absolute path of this directory. Choose fresh directories `HBF_SRC`, `BPFTIME_SRC`, `HBF_BUILD`, `AGENT_BUILD`, `STAGE`, and `RUN`, plus tool paths `CXX13`, `CXX15`, `CUDA13`, `CUDA131`, `LLVM20`, and the CUDA12/PyTorch runtime/CUPTI overlay. `E` denotes this packet's `evidence-tree/rebuttal_20260921`; `T=$E/sass-lifter-20260924/task`. Do not reuse historical PID, inode, GPU UUID, deadline, or LIVE_PLAN. Device selection and resource limits are new launch configuration.

Frozen environment declarations are in `E/env-restore-v1`: Python 3.13.9, torch 2.9.1+cu128, Triton 3.5.1, vLLM 0.15.1, flashinfer 0.6.1. Agent/private bridge uses CUDA13; the target correlation provider uses the CUDA12 CUPTI domain. Recovery assembly uses CUDA13.1 sm_120. A different target requires a separately recorded build/environment and applicability review, not changing `sm_120` without qualification.

## HBF source and helper

```sh
git clone https://github.com/SlugLab/HBFSim.git "$HBF_SRC"
git -C "$HBF_SRC" checkout eabc5c2c0820ac0d84c2f16ea3460b219f11ff83
git -C "$HBF_SRC" submodule update --init --recursive
git -C "$HBF_SRC" apply "$PACKET/first-fault-runtime-from-eabc5c2.patch"
```

This source supplies the runtime, daemon, PTX pass, helper and their other translation units. Its pinned submodules include bpftime ec26daecc8e787fb80fd95dd596a576404a5e36e and MQSim 51f0f2d3fed92d88ef4a0fa61a38024b07bf9d16. Use the retained `first-fault-compact-build-v2/CMakeCache.txt` as the historical option declaration, not a relocatable cache: reconfigure from source with the same feature options and newly configured CUDA/compiler/library roots. The pass links newly built core, future emitter, PTX IR and MQSim libraries, OpenSSL crypto, CUDA runtime/driver and rt. Retain `first-fault-build-v3/cudart-versions.map` as the recorded link version script.

The exact nvcc invocation and pass compile/link blocks are in `PTX_STAGE_RECIPE.json` under the review task in evidence-tree. The helper is built from `src/cuda_runtime/device/hbf_device.cu`, compute_120, RDC, timing futures enabled; CMake's `cmake/EmbedDevicePtx.cmake` embeds it. The historical embedded payload is f9efe085… (297388 bytes). Rebuild it from this source; do not substitute a same-named B-weight source or an opaque runtime binary.

## Complete agent build, then final two translation units

Clone bpftime from the URL in the HBF base `.gitmodules`, checkout **ec26daecc8e787fb80fd95dd596a576404a5e36e**, initialize its pinned submodules, and apply `E/native-supported-partial-exact-agent-build-v1/REPRODUCTION_SOURCE_PATCH_FROM_EC26.patch`. This is the complete base for the other agent objects, not the HBF repository revision.

Overlay exactly:

| Source in packet | Destination in bpftime source |
|---|---|
| `T/router-li7-host-adapter-v1/source/nv_attach_impl_router_scoped.cpp` | `attach/nv_attach_impl/nv_attach_impl.cpp` |
| `T/li6-li7-combined-host-v1/source/nv_attach_impl_frida_setup.cpp` | `attach/nv_attach_impl/nv_attach_impl_frida_setup.cpp` |
| combined-host source `qkv_exact_abi_adapter.hpp`, `qkv_live_identity_v1.hpp` and other quoted local headers | same attach source directory |

Configure using the **actual argv** in `native-supported-partial-exact-agent-build-v1/configure-receipt.json`, replacing only path-valued tool/source/build/include options with configured roots. It specifies Release, g++13, CUDA attach, libbpf, LLVM20 JIT and UBPF JIT, and disables LTO/ASAN/MPK/ccache. The retained C compiler wrapper and compatibility include are part of this build input. Build the CMake agent and PTX compiler targets from source; use `build-receipt.json` for the historical target command. CMake rebuilds all objects and dependency archives. Do **not** copy `build-agent-v2/libbpftime_nv_attach_impl.a` as a reproduction shortcut.

Source→member chain: Li7 scoped implementation → `nv_attach_impl.cpp.o`; final combined setup → `nv_attach_impl_frida_setup.cpp.o`; both belong to `libbpftime_nv_attach_impl.a` → final `libbpftime-agent.so`. Exact historical final compile/link argv and CPU_BUILD_RECEIPT are under combined-host/build-v5. They document the original partial relink; fresh CMake build uses newly produced dependency archives. Record actual exports and dynamic resolution after building, not merely strings.

## Gate and provider

The gate source is `T/router-li7-host-adapter-v1/source/launch_gate.cpp`; final gate is unchanged from build-gate-v2. Its exact compile and link argv are included. Quoted include roots are preserved under `native-unbound-handshake-gate-source-v3/include` and `native-moe-align-scoped-unbound-source-v2/src/{cuda_runtime,ptxpass_hbf}`. Preserve those exact header roles when relocating. Its link receipt references `native-unbound-policy-build-v3/libhbfsim_core.a` and `libmqsim_hbf.a`; the retained SOURCE.patch/CPU_RECEIPT/focused build commands describe that historical variant. **Do not silently substitute the first-fault core archive**. A fresh rebuild of this historical gate-core variant has not been verified; check its base/source patch compatibility before running. This remains a specifically identified reconstruction verification gap, not an opaque binary requirement that has been declared solved.

The provider uses combined-host `provider_router_exact.cpp` + `library_identity_core.cpp` and their headers. `build-v5/provider_compile.argv.json` is the exact direct shared-library compile/link command: relocate CUDA12 include/CUPTI/library/rpath inputs. Archive is N/A. The old build_cpu.py is a historical partial-build tool, not this portable recipe.

## Recovered and instrumented stage

`PTX_STAGE_RECIPE.json/.md` records extraction, recovered ABI maps, Li6 transform argv, Li7 recovery argv and explicit provenance limits. Build the Rust lifter from the included qkv-output-repair Cargo source/lock; source-delivery/reproduce_v7_cpu.py and its profile describe the original tool/input contract. The license files accompany these derived sources. Obtain the exact licensed cuBLAS source image independently; extraction/slicing scripts are included. Li7 historical diagnostic rc1 is preserved and is not renamed success; assembly and later output/model evidence are separate.

For each exact Li6/Li7 symbol, the documented transform is:

```sh
HBFSIM_PASS_MANIFEST_PATH="$STAGE/pass-manifests.jsonl"   "$PYTHON" "$PREPARE_TRITON_PTX" --transform-one   --pass-library "$HBF_PASS_LIBRARY" --kernel "$EXACT_SYMBOL"   < "$RECOVERED_PTX" > "$STAGE/staged.ptx"
```

Li6 command is historical; Li7 is DOC_DERIVED reconstruction because original transform argv was not retained. The staged file name must be lowercase **original source SHA256.ptx**, while the contents have a different staged hash. Never swap these domains. Li6 source6db074…→stage7219c1…; Li7 sourcee70c7c…→stage7ac0e8…. Candidate maps are independent; original152-byte aggregate becomes19slots/148bytes. Preserve exact manifest/sidecar/native-binding joins.

The final prepared-v2 metadata describes nine staged files/ten manifest rows, including seven legacy modules. Those legacy generated PTX/vendor inputs are retained remotely and excluded here; the native binding/pass manifests identify them. Recreating that legacy input set requires original model-generated/native PTX collection under the accepted configuration; this packet does not pretend the two Li6/Li7 recipes alone generate all nine. New materialization must regenerate all path joins and identity receipts. Historical package generators remain documentary and may refer to old collected assets.

## Model validation and limits

Install the local combined-model adapter entry-point metadata in the configured Python path, default off. Use the canonical target ledger and exact scope manifests (3/45/49 added selectors; final147 includes old98). Reuse actual ABI/context/select/end ordering in INTERFACES.md. Final147 request timeout780 is a per-request helper wait limit, not a total wall-time estimate. Generate a fresh resource plan, selected-GPU mapping, ownership/lease/capture identities and bounded controller plan. Never execute retained historical START/LIVE_PLAN as current authorization.

Use the included unchanged validator and report generator on newly collected evidence. The accepted historical result covers one fixed scheduled request; native prefill, untriggered experts/lanes and exhaustive unsupported consumer coverage are not demonstrated. Fresh source builds, toolchain differences and new runs require new validation. No GPU build/run was performed for this publication.

## Model acquisition revision

The included [MODEL_ACQUISITION.json](evidence-tree/rebuttal_20260921/sass-lifter-20260924/task/github-sync-review-20260926-v1/MODEL_ACQUISITION.json) resolves the recorded model revision to `6d84c48581ece794365f2b8e9cfb043c68ade9c5` using the public Hugging Face API at publication. Acquire the model ID and files identified in that receipt at this full revision, under its license. The downloaded public config matched the actual run config. This observation is not retroactive whole-checkpoint byte proof; no weights were downloaded or rehashed for publication.
