# UCIe release CPU checkpoint — 2026-10-06

This checkpoint records the source integration and CPU interface validation
used for HBFSim v0.1.1. The interface contract and runtime selection are
documented in [docs/ucie](../ucie/README.md); the release changes are described
in [v0.1.1](../releases/v0.1.1.md).

## Source provenance

The source snapshot was collected from giga's current HBFSim worktree at
`70b1f93998ff142a8ab21f1a1cb75b7db84b8b90`, including its uncommitted UCIe
implementation. The accepted normal-backend source supplied 54 files. The
publication also includes the current MQSim patches, configuration schemas,
tests, daemon selection and vLLM placement/binding interface from that
snapshot. The snapshot archive was collected and checked byte for byte:
313,615,759 bytes, SHA-256
`3c6b925d892ed7465a39c8c5c0cd392b75bdd7c9c67c7847554ab8c8fd7cff81`.

The pinned submodules are:

| Dependency | Commit |
| --- | --- |
| bpftime | `ec26daecc8e787fb80fd95dd596a576404a5e36e` |
| MQSim | `51f0f2d3fed92d88ef4a0fa61a38024b07bf9d16` |

The publication retains the normal control ABI checks, all 14 UCIe archive
translation units and the existing core bank layout. Private observer and
oracle modes are not enabled. UCIe remains an optional build and the ordinary
backend remains the default.

The [final publication input manifest](artifacts/2026-10-06-ucie-release-cpu/SOURCE_MANIFEST_v6.json)
records 125 source/configuration/documentation inputs and their hashes.
Its SHA-256 is
`4ee1ee85438e3de7d4f601e67c48b35d73c842da38177acced6e152a75bd3770`.
The [preceding manifest](artifacts/2026-10-06-ucie-release-cpu/SOURCE_MANIFEST_v5.json)
is retained for the earlier batches.

## First run and fixture correction

The first public-entry run completed its 121 focused build actions and passed
all seven baseline tests. The optimized worker-horizon test also passed. The
next two fixtures required two independent remote workers, but the script
selected a local stack for those fixtures. They stopped at their original
ownership assertions, and the public script returned 8. The outer runner
returned 1 naturally and reaped its direct children.

The correction keeps the assertions and the production selection flags. It
selects remote workers for the two fixtures and retains the local-stack
configuration for the worker-horizon fixture. The successful build and test
prefix is reused; the corrected phase and the previously unstarted checks are
recorded separately. Combined validation is not represented as a second clean
execution of the entire public script.

The original collection subsequently hit a local `pathlib` import error.
Its already collected archive was extracted and checked offline, preserving
the first error and avoiding another remote run.

Original records are retained without rewriting their bytes:

- [Public-entry output](artifacts/2026-10-06-ucie-release-cpu/first-public-entry-verification.stdout)
- [Public-entry exit receipt](artifacts/2026-10-06-ucie-release-cpu/first-public-entry-verification.FINISH.json)
- [Outer exit receipt](artifacts/2026-10-06-ucie-release-cpu/first-FINISH.json)
- [First error](artifacts/2026-10-06-ucie-release-cpu/first-FIRST_ERROR.txt)

## Host wait-mode interface

Python's placement interface accepts `nominal` and `zero_injected`. The host
context now accepts `zero_injected` as an alias for its existing internal
`zero` value. It normalizes the name once, so both the control flag and daemon
argument select the same mode. Direct host callers retain `nominal` and
`zero`. The daemon CLI, media model, control ABI and device algorithm are
unchanged.

This is an independent host-interface correction to the publication source.
Of the 54 accepted backend files, 52 remain byte-identical. The context has
the alias/canonicalization correction; the daemon has the optional build
boundary correction described below. The accepted source snapshot and its
evidence retain their original bytes.

## Validation results

The second batch rebuilt the context and remaining targets incrementally,
then completed the following checks with natural return code 0:

| Check | Actual result |
| --- | --- |
| UCIe enabled, all targets | 112 additional Ninja actions completed |
| Real Context wait-mode seam | `nominal`, `zero`, `zero_injected` passed; invalid value rejected before daemon creation |
| Optimized remote-worker fixtures | 2/2 passed with two owned remote workers |
| Complete configured UCIe suite | 18/18 passed |
| Other CPU regressions, UCIe enabled | 31/31 passed, with the three documented exclusions below |
| Optimized sustained fixture | 16 requests of 16KiB, window 4; 64 native commands and 262,144 media bytes |

Each wait-mode seam completed one registered 16KiB read with four real native
media commands. Nominal mode retained positive modeled waiting; both zero
spellings returned zero injected waiting while simulated service remained
positive. Each report recorded no failures or outstanding physical work, and
each daemon exited naturally with status 0 before its Context reaped it.
The test checked actual process arguments (`nominal`, `zero`, `zero`), not
only the configured environment value.

The second batch then stopped at the placement Python test, which had run zero
test cases because its default layout path pointed inside the `tests`
directory. Its natural batch return code was 1. The independent portable
test-path correction points to the existing canonical layout and derives the
loader test's repository from its own source path; explicit overrides remain
supported. Neither production placement code nor validation assertions were
changed. Successful C++ checks were retained while the remaining Python and
default-backend checks continued in a separate batch.

- [Second batch exit receipt](artifacts/2026-10-06-ucie-release-cpu/recovery-v2/FINISH.json)
- [Full UCIe suite output](artifacts/2026-10-06-ucie-release-cpu/recovery-v2/all-ucie-baseline.stdout)
- [CPU regression output](artifacts/2026-10-06-ucie-release-cpu/recovery-v2/cpu-regression-on.stdout)
- [Context seam records](artifacts/2026-10-06-ucie-release-cpu/recovery-v2/CONTEXT_WAIT_SEAM_VALIDATION.json)
- [Sustained fixture output](artifacts/2026-10-06-ucie-release-cpu/recovery-v2/latest-optins-real16k.stdout)
- [Original Python path error](artifacts/2026-10-06-ucie-release-cpu/recovery-v2/placement-python.stderr)

The corrected placement test's normal default entry passed all three tests.
The optional loader-binding test then stopped during import because this CPU
Python environment did not contain `vllm`. Its loader assertions were not
executed and are marked **NOT_RUN_DEPENDENCY_UNAVAILABLE**, not passing. No
substitute vLLM package was installed or fabricated. This optional test is
separate from the public CPU reproduction script and the real C++ Context
seam above.

- [Portable placement result](artifacts/2026-10-06-ucie-release-cpu/recovery-v3/placement-python.stderr)
- [Optional loader dependency error](artifacts/2026-10-06-ucie-release-cpu/recovery-v3/loader-python.stderr)
- [Third batch exit receipt](artifacts/2026-10-06-ucie-release-cpu/recovery-v3/FINISH.json)

The default-backend configuration confirmed UCIe's default `OFF` value. Its
first all-target build stopped at the daemon: an unconditional private UCIe
observer include pulled in a JSON dependency outside the optional backend's
build domain. The batch exited naturally with return code 1, preserving the
compiler error and the completed independent build actions.

- [Default-backend compiler output](artifacts/2026-10-06-ucie-release-cpu/recovery-v4/build-all-default-legacy.stdout)
- [Fourth batch exit receipt](artifacts/2026-10-06-ucie-release-cpu/recovery-v4/FINISH.json)

The daemon correction places the private observer include, object and actual
callback inside the existing UCIe compile condition. The OFF branch uses an
empty callback at the same four polling points; the ON observer body and
reporting path are unchanged. No JSON dependency was added to the OFF target.

An intervening deployment attempt retained an earlier runner filename and
stopped at its existing-file protection before starting the remote runner,
compiler or tests. Its partially deployed inputs were checked exactly before
the intended runner was added in a separate recovery. Source and previous
results were not overwritten during that recovery.

- [Deployment error](artifacts/2026-10-06-ucie-release-cpu/deployment-v5/bootstrap.stderr)
- [Deployment reader receipt](artifacts/2026-10-06-ucie-release-cpu/deployment-v5/bootstrap.RECEIPT.json)

The remaining incremental OFF build completed with natural return code 0.
Its configured inventory contained 34 tests and no UCIe tests. All 31 tests
in the documented CPU regression scope passed; the same three exclusions
were retained. The cache confirmed `HBFSIM_ENABLE_UCIE=OFF` without supplying
that option to configuration.

The ON build then recompiled only the changed main object and relinked its
daemon. The existing worker, UCIe archive and test executable were reused.
A byte-identical copy of that actual daemon was placed in a separate
validation directory to keep the previous seam reports intact. All three
wait spellings and invalid-value rejection passed again against the new
daemon, with the same four native commands, 16,384 media bytes and positive
MQSim service in each successful mode.

The final batch completed 20 build, test and evidence commands naturally
with return code 0 and reaped its direct children. The final source hashes
matched all 125 inputs. The UCIe archive contained the fourteen expected
members; daemon and worker dependency inspection found no missing library
or stub path. This checkpoint combines the successful phases with their
exact source and executable identities; it does not turn the preceding
failed batches into passing runs.

- [Default CPU regression result](artifacts/2026-10-06-ucie-release-cpu/recovery-v5/cpu-regression-default-legacy.stdout)
- [Default test inventory](artifacts/2026-10-06-ucie-release-cpu/recovery-v5/inventory-tests-off.stdout)
- [Changed ON daemon build](artifacts/2026-10-06-ucie-release-cpu/recovery-v5/relink-on-daemon-boundary.stdout)
- [Matching daemon copy](artifacts/2026-10-06-ucie-release-cpu/recovery-v5/MATCHED_SEAM_DAEMON_COPY.json)
- [Final Context seam results](artifacts/2026-10-06-ucie-release-cpu/recovery-v5/CONTEXT_WAIT_SEAM_VALIDATION.json)
- [Final executable and archive identities](artifacts/2026-10-06-ucie-release-cpu/recovery-v5/ACTUAL_BINARIES_AND_ARCHIVE.json)
- [Final source hashes](artifacts/2026-10-06-ucie-release-cpu/recovery-v5/SOURCE_IMMUTABLE_AFTER.json)
- [Final natural exit receipt](artifacts/2026-10-06-ucie-release-cpu/recovery-v5/FINISH.json)

## Build and reproduction

The recorded giga toolchain was GCC 15.2.0, CMake 4.2.3, Ninja 1.13.2 and
Python 3.14.4. The UCIe build used `HBFSIM_ENABLE_CUDA=OFF`,
`HBFSIM_ENABLE_MQSIM=ON`, `HBFSIM_ENABLE_UCIE=ON` and `BUILD_TESTING=ON`.
PkgConfig and CUDAToolkit discovery were explicitly disabled for this CPU
configuration. The default-backend build uses the same CPU settings and omits
the UCIe option, verifying its default `OFF` value.

```bash
git submodule update --init --recursive third_party/bpftime third_party/mqsim
HBFSIM_BUILD_JOBS=4 bash scripts/reproduce_ucie_current_cpu.sh build-ucie-on

# Build the remaining targets and run the full UCIe suite.
cmake --build build-ucie-on --parallel 4
ctest --test-dir build-ucie-on --output-on-failure -R '^ucie_'

# Explicit Context -> normal daemon wait-mode fixture.
build-ucie-on/ucie_backend_adapter_test \
  build-ucie-on/ucie_stack_worker \
  configs/profiles/ucie/hbf-stage3-16k-small-top.json \
  build-ucie-on/hbfsimd --context-wait-seam
```

The explicit seam initializes generation 1 through an existing CPU test hook
and publishes a range through the existing test gate. It checks the actual
daemon arguments, control flag, registered ring read, real MQSim completion,
physical tail and natural daemon exit followed by Context-owned reaping.
These test hooks do not validate `cudaHostRegister`.

Its fixture keeps the 16KiB application page and request, a 4KiB native media
page, 10us read latency, 100us program latency and time scale 100. The top has
two stacks with two modules each, 1,024 child records and 65,536 bytes of
reassembly capacity; its link accepts 128 concurrent transactions. These are
fixed CPU fixture inputs, not the full-model experiment configuration.

The legacy CPU regression exclusions follow
[CONTRIBUTING.md](../../CONTRIBUTING.md): `vmem_tuning` requires an
author-specific calibration CSV, `run_with_bpftime` requires its CUDA 12.8
environment, and `context_lifecycle` has the documented existing
`cd8p-vmem-p50` defect. Excluded tests are not counted as passing.
