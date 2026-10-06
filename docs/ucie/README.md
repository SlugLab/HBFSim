# UCIe backend

HBFSim can route application reads through a CPU UCIe front end and the real MQSim reference engine. The implementation supports independent stacks, module and bank routing, backing registration, bounded admission, ready-page reassembly, cancellation, and a physical service tail. The host daemon exposes this backend through the existing control interface; the legacy HBF route remains available.

## Build and reproduce

Use a Linux C++20 toolchain, CMake 3.25 or newer, Ninja, OpenSSL development headers, and the pinned submodules. CUDA is optional for this CPU build.

```sh
git submodule update --init third_party/bpftime third_party/mqsim
HBFSIM_BUILD_JOBS=4 scripts/reproduce_ucie_current_cpu.sh
```

The script configures `HBFSIM_ENABLE_CUDA=OFF`, `HBFSIM_ENABLE_MQSIM=ON`, `HBFSIM_ENABLE_UCIE=ON`, and `BUILD_TESTING=ON`; builds the daemon, worker, and focused tests; then runs baseline tests, the optimized local-stack horizon test, and optimized remote-process frontend/lifecycle tests. The latter retain their required two remote worker processes. Set `HBFSIM_BUILD_JOBS` for the available build resources. The optional second argument `build-only` prepares those targets without executing tests. Use `scripts/reproduce_ucie_current_cpu.sh EXISTING_BUILD_DIR remote-tests-only` to execute only the two optimized remote-process tests from existing built targets; this mode skips configuration, compilation, baseline tests and the local horizon test. It uses the same remote-test function as `focused`.

For direct use:

```sh
cmake -S . -B build-ucie -G Ninja \
  -DCMAKE_BUILD_TYPE=Release -DHBFSIM_ENABLE_CUDA=OFF \
  -DCMAKE_DISABLE_FIND_PACKAGE_PkgConfig=TRUE \
  -DCMAKE_DISABLE_FIND_PACKAGE_CUDAToolkit=TRUE \
  -DHBFSIM_ENABLE_MQSIM=ON -DHBFSIM_ENABLE_UCIE=ON -DBUILD_TESTING=ON
cmake --build build-ucie --parallel 4
ctest --test-dir build-ucie --output-on-failure -R '^ucie_'
```

The main targets are `hbfsim_ucie`, `ucie_stack_worker`, and `hbfsimd`. The UCIe archive compiles fourteen translation units, including the matching worker handler. Bank layout remains in the MQSim-enabled core target. The worker and daemon link against the same public/private protocol and accounting definitions. Oracle macros and private diagnostic observers are not enabled by these build commands. The daemon's normal control layout is checked at compilation.

## Profiles and request path

Profiles live in [`../../configs/profiles/ucie/`](../../configs/profiles/ucie/). `hbf-stage3-4x512gib-top.json` describes the multistack topology and references the link and media profiles; the reference link uses a 4 KiB media page. An application request may span several media pages and modules. Application request size and media page size are distinct settings: the accepted synthetic workload uses 16 KiB requests with 4 KiB media pages.

`MultistackFrontend` routes child requests, owns parent assembly, advances the physical frontier, and returns application completions. `StackFrontend` controls each stack's backing, capacity and media service. `StackWorkerClient` communicates with a matching `ucie_stack_worker`; a configured local stack calls the same handler in process. Ready data is consumed in the original parent order. Closing includes the real outstanding MQSim service, rather than treating host completion alone as the physical tail.

The backend is an optional build (`HBFSIM_ENABLE_UCIE` defaults to `OFF`). Enabling that build adds the daemon route; it does not select the route for every caller or change the default legacy behavior. Adapter configuration must explicitly select UCIe and provide a valid top profile and worker path.

## Runtime selection interfaces

The [vLLM loader](../../adapters/vllm/hbfsim_loader.py) requires all four selection variables when UCIe model registration is requested:

| Variable | Input contract |
| --- | --- |
| `HBFSIM_UCIE_TOP_PROFILE` | Topology profile for the matching UCIe backend. |
| `HBFSIM_UCIE_WORKER` | Matching executable worker path. |
| `HBFSIM_UCIE_WAIT_MODE` | Loader spelling: `nominal` or `zero_injected`. |
| `HBFSIM_UCIE_CANONICAL_LAYOUT` | Exact frozen 147-storage canonical layout used by [placement generation](../../adapters/vllm/ucie_placement.py). |

Registration uses `reference` or `hybrid` timing, complete selected storages and 16 KiB application pages. The frozen canonical layout is built for all 147 storages before selection; changing the selected subset does not create a new physical layout. The loader writes `ucie-host-placement.json` and `ucie-storage-bindings.json` under the run's report directory, and sets `HBFSIM_UCIE_PLACEMENT_MANIFEST` to that generated manifest.

The lower-level [context launcher](../../src/cuda_runtime/context.cpp) accepts the wait modes described below and selects [the daemon](../../src/host_service/main.cpp) with `--backend ucie`, `--ucie-top-profile`, `--ucie-worker`, `--ucie-placement-manifest`, `--ucie-wait-mode`, `--report-dir`, `--profile` and the inherited `--control-fd`. The context accepts `nominal`, `zero`, and the loader's `zero_injected` alias. It normalizes `zero_injected` to `zero` before setting the existing control flag and constructing the daemon arguments. Both zero spellings suppress injected waiting while the reference MQSim service continues. Other values are rejected before starting a daemon. The explicit CPU test seam checks this launch contract and a registered ring read; it skips CUDA registration and does not establish model coverage.

## Accepted software options

These options are explicit runtime opt-ins. An unset option retains the corresponding original path; it does not disable MQSim or change physical latency. The reproduction script sets them only for its optimized test invocation.

| Environment variable | Behavior |
| --- | --- |
| `HBFSIM_UCIE_COALESCER_EMPTY_WAIT=1` | Avoid an empty pending-queue scan, while retaining normal coalescer updates. |
| `HBFSIM_UCIE_RELEASABLE_CHILD_INDEX_V1=1` | Maintain eligible child indices for parent release. |
| `HBFSIM_UCIE_ORDERED_ADMISSION_V1=1` | Negotiate synchronous ordered admission, with a maximum of sixteen items. |
| `HBFSIM_UCIE_CAPACITY_ZERO_INACTIVE_V1=1` | Reuse a validated zero-capacity proof where it certifies an inactive module. |
| `HBFSIM_UCIE_COMPACT_CONSUME_ORDERED_V1=1` | Consume an eligible same-parent/stack/module group by direct IDs, maximum sixteen, with complete reply validation before host commit. |
| `HBFSIM_UCIE_COMPACT_CONSUME_WAVE_V1=1` | Overlap the existing first compact groups across eligible distinct remote workers in the fixed-point release path; drain before the next horizon. |
| `HBFSIM_UCIE_DEFERRED_INITIAL_HELLO_V1=1` | Initialize the local stack first, then send remote HELLOs in stack order and validate all replies before registration and return. |

The optimized transport uses `HBFSIM_UCIE_SHM_RPC=1`, `HBFSIM_UCIE_TYPED_RPC=1`, and `HBFSIM_UCIE_LOCAL_STACK_ID=0`. The private mailbox remains ABI version 1 with its existing acquire/release states, spin limit 512, futex wait, liveness and deadline checks. Both ends must use matching binaries. This release uses ordered/compact limits of sixteen and the matching ABI version 1 mailbox.

Compact and wave paths have conservative eligibility and negotiated fallbacks. A published operation with an unknown outcome is poisoned and is not replayed. The ordinary public Consume API remains synchronous. These host-side options do not change the worker core's per-item consume semantics, media timings, cache policy, credits, HYBRID ratio, device helper or PTX algorithms.

## Reports and scope

Normal daemon shutdown writes the adapter report and then emits frontend/client cold records during owned cleanup. Read complete stderr after natural exit. Ordered, zero-proof, Compact, wave and startup records describe their exported lifetime; a zero count is valid and does not prove an option was disabled. Independent coalescer/bitmap hot-hit totals are not exported. No universal getter certifies every optimization's activation.

Host wall time, host CPU time, simulated event time and injected device waiting are separate quantities. A zero-injection configuration suppresses injected waiting while MQSim still executes the reference service and queue transitions; it is not a zero-latency media profile. Registration and successful CPU tests alone do not establish model-weight coverage or model performance.

The MQSim dependency is pinned at `51f0f2d3fed92d88ef4a0fa61a38024b07bf9d16`. CMake applies the online HBF API, QLC support, command observer, and next-event peek patches to an isolated build copy; the upstream submodule remains unchanged.

## Historical stage documentation

[Stage 1](STAGE1_CPU_INTERFACE.md), [Stage 2](STAGE2_DEVICE_COALESCING.md), and [Stage 3](STAGE3_MULTISTACK.md) document the dated implementation/proof scopes recorded there. Their measurements remain historical evidence for those versions. Current source integration and a new clean release build are separate from those original results; the stage scripts remain available for those reproduction scopes.

The [release CPU proof](../proofs/2026-10-06-ucie-release-cpu.md) distinguishes the original public-script prefix from its corrected remote test stage and later validation. The additional test-only mode is `build-ucie-on/ucie_backend_adapter_test build-ucie-on/ucie_stack_worker configs/profiles/ucie/hbf-stage3-16k-small-top.json build-ucie-on/hbfsimd --context-wait-seam`. It requires the CPU targets to be built and writes one report for each wait spelling under `build-ucie-on/ucie-context-wait-seam/`. The original adapter test invocation remains unchanged.
