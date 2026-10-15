# Reporting and diagnosing a problem

Use the [Bug report form](https://github.com/SlugLab/HBFSim/issues/new?template=bug_report.yml)
for a reproducible defect, or the [Q&A form](https://github.com/SlugLab/HBFSim/discussions/new?category=q-a)
for setup help. You can write "unknown" for details you cannot obtain. English and
Chinese reports are welcome.

Include the HBFSim commit, your build and run commands, machine/software versions,
and the complete first error. For a model run, add the model revision, selected
weights, input/output lengths and SASS lifter setting. Tell us whether the same
input works without HBFSim. Attach the coverage and binding reports when available.
Remove secrets and private data before uploading logs; model weights are not needed.

## PTX compilation failures

`Unable to compile: 3` from `nvPTXCompilerCompile` means that PTX compilation
failed. The compiler's error text explains why. A later `Aborted` or Python worker
exit usually describes the consequence, so include the earlier compiler log too.

Three versions matter:

- The PTX input has a `.version` and a `.target` directive.
- The compiler library linked into the bpftime PTX compiler must support both.
- The GPU and driver must support the resulting target and instructions.

For example, `sm_120` needs at least PTX 8.7; `sm_121` needs at least PTX 8.8.
Architecture-specific suffixes such as `a` and family suffixes such as `f` carry
requirements too. Replacing a target name or lowering the PTX version can make
the input invalid. See NVIDIA's [target directive documentation](https://docs.nvidia.com/cuda/archive/12.9.2/parallel-thread-execution/index.html#ptx-module-directives-target).

The `ptxas` found on `PATH` does not identify the compiler library linked into an
existing bpftime build. Record `HBFSIM_CUDA_ROOT`, `BPFTIME_CUDA_ROOT`, the compiler
version printed by the failing process, and relevant `CMakeCache.txt` entries.
Changing an environment variable does not relink an old binary. Build into a new
directory when changing toolkits, and keep the failed log for comparison.

Our framework CUDA 12.8 and private HBF CUDA 13 components have different roles.
Do not redirect every CUDA library to a newer toolkit as a workaround. The
[giga build environment](build/giga-environment.md) describes these roles and the
recorded build commands.

## Missing model coverage

Registering a weight address does not prove that its consumer was instrumented.
Check the actual binding result, then whether a launch used that binding and
accessed the selected weight range. A planned layer list is not a measured
coverage list. Include the binding/coverage output when reporting a missing layer.

SASS recovery is experimental because recovering PTX from a binary can change
instruction meaning or argument interpretation. Ordinary runs keep it disabled
unless explicitly enabled. Native PTX instrumentation remains available for
supported kernels. A run requiring unsupported consumers must report missing
coverage or reject the request under its strict policy; it must not claim full
model coverage after silently falling back to native execution.
