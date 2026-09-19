# Contributing to HBFSim

HBFSim is a research simulator, and the only reason to use a simulator rather than guess
is that the numbers a simulator reports can be trusted. Most of the rules below are
therefore about evidence rather than about code formatting.

## What is most useful here

In descending order of value to the project:

1. **A measurement that closes an item in `TODO.md`.** Every open item in `TODO.md` names
   a claim that is currently unproven or a gap that is currently unmeasured. A run that
   closes one of those items, together with the checkpoint document recording the run, is
   the most useful contribution HBFSim can receive.
2. A defect in the modeling that changes a reported number. Reporting a delay, a
   bandwidth, or a hit rate the modeled device would not produce is more serious than a
   crash: a crash is visible and a wrong number is not.
3. A workload adapter. HBFSim currently drives a microbenchmark, llama.cpp, and vLLM. An
   adapter for another real workload extends the set of questions HBFSim can answer.
4. Documentation. Corrections to the README, to the documents under `docs/`, and to the
   checkpoint documents under `docs/proofs/` are reviewed the same way code is.

## Getting a build

HBFSim needs native Linux, CMake 3.25 or newer, Ninja, a compiler supporting C++20,
OpenSSL, and Python 3. The CUDA-facing components need CUDA 12.8 or newer. The media
simulator and the media benchmark build without CUDA.

```bash
git clone https://github.com/SlugLab/HBFSim.git
cd HBFSim

HBFSIM_ENABLE_CUDA=OFF HBFSIM_ENABLE_MQSIM=ON ./scripts/bootstrap.sh
cmake --build build -j"$(nproc)"
ctest --test-dir build --output-on-failure
```

On a machine with no CUDA toolkit installed, the configure step succeeds, all 152 build
targets build, and 31 of the 34 ctest tests pass. Three tests do not pass on such a
machine, for three separate reasons:

- `vmem_tuning` reads a hard-coded absolute path,
  `/home/victoryang00/nvme-mem2nvm/docs/superpowers/results/2026-07-30-vmem-sw-performance.csv`,
  which is one author's calibration CSV, so `vmem_tuning` passes only on the machine
  holding that file.
- `run_with_bpftime` requires the directory `/usr/local/cuda-12.8` to exist.
- `context_lifecycle` fails while building a context from the empirical profile
  `cd8p-vmem-p50`, at line 286 of the test source. The failure reproduces on both the
  `main` branch and the `docs/eval-mainline` branch, so `context_lifecycle` is an
  unresolved defect in HBFSim rather than a property of the machine.

## The rule that matters most in this repository

**A number may only enter the repository together with the checkpoint document that
produced the number.**

Experiment numbers live in `docs/proofs/`. A checkpoint document there records the exact
command, the machine, the software versions, the raw output, and the boundary of what a
run does and does not show. A pull request quoting a number with no checkpoint document
behind the number will be asked for one before review continues, including a pull request
quoting the number only in a commit message or only in the README.

When a new measurement supersedes an older measurement, the older checkpoint document
must be marked as superseded in the same pull request that adds the new measurement.
Leaving two live versions of one number in the repository is the failure this rule exists
to prevent.

There is precedent. A batch of MQSim media-benchmark numbers published in
`docs/proofs/2026-08-10-capacity-runtime-non-live.md` was invalidated after publication:
the `queue_depth` field of a profile had never taken effect, so every run in that batch
behaved as though concurrency were unbounded. Commit `12ef138` fixed the field, the
corrected run produced a p50 and a p99 several times larger than the published ones, and
the superseded numbers are now recorded as invalid and are not to be quoted anywhere.

## Four kinds of time are reported separately

HBFSim reports four quantities and never merges any two of the four:

- modeled device time, the delay the modeled High-Bandwidth Flash (**HBF**) device
  would impose;
- host service time, the time the host side of HBFSim spends on a request;
- wall-clock time, the elapsed time a person waiting for the run observes;
- emulator overhead, the cost HBFSim itself adds to the workload.

Any performance claim, in the README, in a checkpoint document, in a commit message, or
in a pull request description, must say which of the four quantities the claim is about.
The separation is not bookkeeping: a live emulator can be functionally correct while the
software overhead of the emulator exceeds the device delay the emulator models, and a
claim that does not name the quantity hides exactly that case.

## What not to claim

Passing the CPU tests, passing an MQSim regression, or assembling rewritten PTX with
`ptxas` is static proof, and static proof is not evidence that delay has been injected on
a live GPU.

Each qualifier below is part of the result and may not be dropped when the result is
quoted:

- The six calibration breakpoints match the measured device exactly, and the match is a
  deterministic calibration check rather than a cross-validation: all six points took
  part in the fit and none was held out.
- The fast path serves the same Qwen3-30B case 20.8x faster than the detailed reference
  path, and 20.8x is a ratio between two wall-clock times of HBFSim itself, not a
  prediction of HBF hardware performance.
- An unmodified vLLM 0.15.1 returns the token identifiers of the uninstrumented baseline,
  with only the first 16,384 bytes of one tensor registered, out of 61,064,245,248 bytes
  in that tensor.
- A 110 GiB logical range runs on a 97,887 MiB GPU behind a 2 GiB high-bandwidth memory
  (**HBM**) cache, and the run is a sparse logical-capacity proof: the run does not
  claim that 110 GiB of payload was physically read.
- The calibration source device, a Dell DC NVMe CD8P E3.S 1.92TB at PCIe 5.0 32 GT/s x4,
  is an ordinary PCIe NVMe endpoint and not a CXL endpoint.

Dropping a qualifier of that kind is the most damaging edit possible in this repository,
because the shortened sentence still reads as true.

## Commits and pull requests

One logical change per pull request: a modeling fix, a new adapter, and a documentation
pass are three pull requests. Write a commit subject in the imperative mood saying what
changed, for example `Fix queue_depth handling in the profile loader`, and put the
reasoning and the evidence in the commit body.

Fill in `.github/pull_request_template.md` completely. The checklist there asks for a
clean CPU-only build, a green `ctest` run or an explanation of every new failure, zero
broken documentation links, no number without a supporting document under `docs/proofs/`,
and an updated proof document whenever a published number is superseded. Run the
documentation link checker before pushing any change that touches Markdown:

```bash
python3 scripts/check_doc_links.py
```

## Code style

New code is C++20. Match the formatting already present in the file being edited rather
than reformatting that file in the same commit; a reformatting pass belongs in a pull
request of its own. Do not add a third-party dependency without discussing the dependency
in an issue first; HBFSim is meant to build with the tools listed above and nothing more.

Two submodules are pinned to exact revisions, and the build checks both:

- bpftime at `ec26daecc8e787fb80fd95dd596a576404a5e36e`
- MQSim at `51f0f2d3fed92d88ef4a0fa61a38024b07bf9d16`

Either pin may move only in a commit whose message says why the pin moves and what
changes in HBFSim behavior as a result.

## Where to ask

Open an issue at https://github.com/SlugLab/HBFSim/issues, using the bug report template
or the feature request template under `.github/ISSUE_TEMPLATE/`. Report a suspected
security problem through `SECURITY.md` rather than in a public issue.

Participation in this project is governed by `CODE_OF_CONDUCT.md`, the Contributor
Covenant.
