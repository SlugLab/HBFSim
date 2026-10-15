# giga build environment and components

This document records the environment used by our OLMoE research builds on
**2026-10-15**. It explains which compiler built each component and where the
build commands came from. Use fresh source and build directories when following
the commands. The original experiment continues to use its existing files.

The [command appendix](giga-build-commands-20261015.json) contains the full recorded
argument lists, including compile, archive and link commands. Its absolute paths
describe the original machine; they are not required installation paths.

## Machine and software

| Component | Recorded version or configuration |
|---|---|
| Operating system | Ubuntu 26.04.1 LTS, x86-64 |
| Kernel | 7.0.0-mk1+ |
| NVIDIA driver | 610.57.04, open x86-64 kernel module |
| CPU | AMD Ryzen Threadripper 7960X, 24 cores |
| GPU | NVIDIA GeForce RTX 5090; research builds target `sm_120` |
| C++ compilers | GCC 13.4.0 and GCC 15.2.0, with different roles below |
| CMake | 4.2.3 |
| Ninja | 1.13.2 installed; availability does not prove every historical build used it |
| Python | 3.13.9 |
| PyTorch | 2.9.1+cu128 |
| vLLM | 0.15.1 |
| Triton | 3.5.1 |
| FlashInfer | 0.6.1 |
| Framework CUDA compiler | CUDA 12.8, nvcc 12.8.93 |
| Private HBF/agent CUDA compiler | CUDA 13.0, nvcc 13.0.88 |
| GNU linker / glibc | Binutils 2.46 / glibc 2.43 |
| OpenSSL | 3.5.5 |
| Agent LLVM dependency | LLVM 20.1.8; exact archive paths are in the link command |
| Frida devkit declared by the public reconstruction recipe | 16.1.2 Linux x86-64, with archive hashes checked by the recipe |
| bpftime base | `ec26daecc8e787fb80fd95dd596a576404a5e36e`, with the recorded research patches |
| MQSim base | `51f0f2d3fed92d88ef4a0fa61a38024b07bf9d16` |

Python package versions come from the saved environment receipts and the actual
model worker log. They are not inferred from whichever Python happens to be on
the shell's `PATH`. Similarly, the research bpftime base revision alone does not
describe its additional source patches.

## Keep the CUDA roles separate

The framework uses the CUDA 12.8 libraries supplied for PyTorch/vLLM. Our private
HBF and agent builds also use a CUDA 13 toolkit. The target correlation provider
uses **CUDA 12 CUPTI**, so its build and runtime search paths differ from the
private agent's paths.

The recorded roots are:

```text
Framework toolchain:
  /root/hbfsim-exp/rebuttal_20260921/cuda128-toolchain-prep-v1/root/cuda-12.8
Private HBF toolchain:
  /root/hbfsim-exp/rebuttal_20260921/cuda13-glibc-rsqrt-overlay-v1/root/cuda-13.0
```

Other historical agent commands reference the downloaded CUDA 13 toolkit under
`env-restore-v1/toolchain-download-v1/root/`. Keep the exact compiler, include,
library and rpath choices together when reading those commands. Do not combine
fragments from different builds or globally redirect CUDA 12 libraries to CUDA 13.

## What is compiled

| Component | Inputs and compiler role | Output/use |
|---|---|---|
| HBF runtime and host service | HBF C++ source, GCC 15; CUDA translation units use nvcc 13 with GCC 13 as host compiler | Runtime, PTX pass, device helper and host service |
| MQSim service | Pinned MQSim source and the corresponding project patch | Host-side media request execution |
| bpftime agent | Pinned bpftime plus the research patches, GCC 13, LLVM 20, Frida and libbpf | `libbpftime-agent.so` and the PTX compiler component |
| Target identity provider | `provider_router_exact.cpp` and `library_identity_core.cpp`, GCC 15, CUDA 12/CUPTI includes and libraries | `libprovider_router.so` |
| Model adapters | Python source and entry-point metadata | Select actual model weight ranges for the supported consumers |
| Recovered SASS inputs | Exact kernel image, recovery tool source, ABI mapping and recovered PTX | Optional recovered-kernel binding; this is separate from native PTX support |

The actual backend configuration enabled CUDA, MQSim and the previously built
UCIe component; timing futures, evaluation tools and LLM tests were disabled in
that build. This describes the binary's build options. It does not start or
authorize a UCIe experiment.

## Build from source in a separate directory

There are three build paths. Choose the one matching the component you need:

| Build path | Source changes | How the run checks the build |
|---|---|---|
| Ordinary public agent | Pinned bpftime, `0001-exact-module-load-provenance.patch`, then `0002-ptx-target-compatibility-and-diagnostics.patch` | `run_with_bpftime.sh` checks the builder's four-line provenance file |
| Supported-weights agent | Pinned bpftime, the frozen `0002-supported-weights-from-ec26.patch`, then `0003-supported-weights-ptx-compatibility.patch` | The reproduction script records and verifies the prepared source and build |
| Optional SASS research agent/provider | Explicit source inputs plus the generated switch-aware copies | A separate research build manifest records the actual sources, successful build commands and selected libraries |

The two files starting with `0002` belong to different build paths. Their names
do not make them interchangeable. Keep the old experiment evidence unchanged
and build the new source separately.

For the ordinary public build, start with the repository's
[CPU build instructions](../../scripts/HBFSIM_CPU_BUILD.md) and
[bpftime build instructions](../../scripts/BPFTIME_AGENT_REPRODUCTION.md).
Initialize only the two build dependencies:

```bash
git submodule update --init --recursive third_party/bpftime third_party/mqsim

# Choose the toolkit appropriate for the GPU and PTX being compiled.
export HBFSIM_CUDA_ROOT=/absolute/path/to/cuda
HBFSIM_CUDA_ARCHITECTURES=121 ./scripts/bootstrap.sh
./scripts/build_patched_bpftime.sh /absolute/path/to/new-bpftime-build
```

The bpftime builder uses a clean copy of the pinned source and applies the
project's ordered patches there. The original submodule remains unchanged.
The build writes a provenance file, and the run wrapper checks it before loading
the agent. Use a new build directory when the toolkit or patches change.

The `121` above illustrates an explicit GB10 target; use the target for your own
GPU and a toolkit that supports it. Bootstrap passes the chosen toolkit to CMake
without changing global library search paths. This example does not claim that
the complete model path has been tested on GB10.

For the research SASS path, also follow the
[source reconstruction instructions](../../reproduction/sass-final147-20260926/REBUILD.md).
That package identifies the runtime patches, agent translation units, provider,
recovery inputs and licensing. It also states the remaining reconstruction gaps.
The optional [SASS runtime tools](../../tools/sass_runtime/) create modified copies
of the relevant consumers; they do not rewrite the saved experiment evidence.

A current research run is not reproduced by copying a library with the same
filename. Rebuild its source and dependencies, check the actual library selected
by the process, and validate the model output and measured coverage.

## Build commands and their limits

The command appendix preserves the backend configure/build, final agent compile,
archive/link, and provider compile commands. The final agent command is a
**partial rebuild/relink** using an existing dependency build. For a fresh machine,
first build those dependencies using the reconstruction recipe; the final link
line alone does not rebuild them.

The saved backend configure command refers to the original `local-stack-workset`
source/build paths, while later deployment uses a separate `hybrid-phase-diag`
build location. Both are retained as their recorded roles; a directory name is
not proof of identical contents.

Some original recovery commands and parts of the historical syscall-server build
were not retained completely. They are identified as gaps in the reconstruction
instructions. This document does not turn a proposed reconstruction command into
a verified historical command, or claim GB10 support from an x86-64/sm_120 build.

## Validation of the maintenance changes

The 2026-10-15 checks ran in separate directories on giga. Both public and
supported-weights compatibility patches applied to their intended source
versions. C++ fixtures exercised the actual metadata policy, compiler wrapper and
worker function, including compiler failures and worker cleanup. The compiler API
is substituted in these host tests; they do not run CUDA kernels. Source
preparation, toolkit selection, build provenance and launcher checks also passed.

The switch-aware SASS agent and provider were compiled and linked separately
using the recorded dependency build. Export and host branch checks verified that
the switch controls the actual recovered dispatch and identity code. Eleven host
unit checks passed. The layer report reader also accepted an existing completed
OLMoE correctness receipt and listed layers 0-15 plus embedding, final norm and
head. This reader check did not rerun the model.

These checks do not establish GB10 end-to-end support or the GPU semantics of the
new SASS binaries. The original experiment was not changed. Exact source/output
hashes, receipt identities and limits are in the
[validation record](validation-20261015.json).

## What to save for a new build

Save the project and submodule revisions, applied patch hashes, configure and
build commands, compiler versions, selected CUDA roots, and output library
hashes. For a model run, also save the model revision, selected weights,
input/output tokens, binding/coverage report and complete first error log.
The [reporting guide](../troubleshooting.md) and GitHub forms provide a short
format for sharing this information.
