# TODO

This file is the public backlog for HBFSim. Every entry names one piece of work and the artifact
that would prove the work finished, so an outside contributor can pick an entry up without reading
the whole repository first. No entry carries a date and no entry carries a deadline; ordering inside
a group follows dependency, not calendar.

Every entry carries an evidence path: the proof document, the test, or the measured number whose
existence closes the entry. An entry is checked off only once the named artifact is in the
repository. Each completed entry ships a tagged release, and the checkbox is ticked in the same
commit that lands the evidence, so the release history and the checked boxes always agree.

An entry described below as a known defect is already recorded in the project's internal defect
register, with a severity grade and a source-code citation. That register is a working document
kept outside this repository; the defect identifier is quoted here so a maintainer can find the
entry. A known defect is a repair with a paper trail, not a new idea.

## Status at a glance

No tagged release exists yet. The first tagged release is the repository presentation work listed
under Done at the bottom of this file.

HBFSim applies High-Bandwidth Flash (**HBF**) timing, capacity and thermal effects to an inference
workload while the workload executes on a real GPU. Timing-only mode runs on a real GPU today:
bpftime interception, automatic rewriting of supported global loads and stores in PTX (the
intermediate code NVIDIA's compiler emits), host range registration and the host service are
implemented. An unmodified vLLM 0.15.1 serving Qwen3-30B returned the token identifiers of the
uninstrumented baseline, with 24 of 2,304 `fused_moe_kernel` launches carrying modeled delay. Only
16,384 bytes of a 61,064,245,248-byte tensor were registered for the vLLM run.

Capacity mode completed a 110 GiB logical address range against a 2 GiB high-bandwidth memory
(**HBM**) page cache on a 97,887 MiB GPU, with checksum `14245581564465502923` matching the baseline
and zero unsafe launches. The 110 GiB run proves a sparse logical span and correct page routing, not
that 110 GiB was physically read. The calibrated fast path served one deterministic Qwen3-30B case
in 2.014352 s against 44.469 s on the detailed online MQSim reference path, 20.8 times faster. The
20.8 ratio is emulator wall time, not a prediction about HBF hardware.

Temperature does not yet reach the timing path. The six calibration breakpoints at 1, 4, 16, 64, 256
and 512 contiguous 4 KiB pages match the measured device exactly, but all six breakpoints were used
to fit the curve and none was held back, so the match is a deterministic calibration check rather
than cross-validation. The formal EQ1 to EQ4 measurement campaign stands at 30 of 20,485 planned
runs, with EQ1 blocked and EQ2, EQ3 and EQ4 partial. A CPU-only build of 152 targets passes and 31
of 34 `ctest` tests pass.

## Correctness and fidelity

These entries come first because each entry changes a reported number by more than an order of
magnitude or in the wrong direction. Severity grades A and B are the project's own grades,
assigned in the internal defect register described at the top of this file.

- [ ] Serve a repeated read of the same page from the page buffer instead of restarting the burst and charging a whole first page: 11,133 ns against 10,121 ns, about 10.0 percent too much. Known defect, severity A, `00-A-repeated-read-of-the-same-page-is-charged-more.md`, in conflict with the Open Compute Project (**OCP**) HBF v0.7.0 specification, section 5.3.1 items 5a, 7 and 8, pages 56 to 57, and register field `NCBB`, page 70. 1 to 2 days. No GPU hardware for the change, GPU hardware to re-validate.
  - Evidence that closes it: a proof document under `docs/proofs/` in which a same-page repeat is charged a buffer transfer, plus a regression test that fails against the current charging rule.
- [ ] Charge a write once per 4 KiB program unit instead of once per store instruction. Filling one 4 KiB page currently costs 13.07 ms or 3.27 ms against the measured fsync P50 of 408,305 ns, 8 to 32 times the specification figure. Known defect, severity A, `03-A-write-charged-per-instruction-not-per-4kib.md`, in conflict with OCP section 5.4.1 item 3, page 58. `17-contributions-and-experiments-we-could-add.md` calls the change the highest priority and a repair rather than a contribution. No GPU hardware.
  - Evidence that closes it: a modeled write of one 4 KiB page landing within the measured 408,305 ns program cost, recorded in a proof document.
- [ ] Redesign the capacity mode write path so an evicted page is not rewritten in place. HBF forbids random writing inside a block, and the legal alternative is a whole-block replay costing 104.5 ms, a write amplification of 256 times. Known defect, severity A, `04-A-capacity-mode-rewrites-a-page-in-place.md`, in conflict with OCP sections 11.5.2.2 and 11.5.2.6, pages 121 to 122. `17-contributions-and-experiments-we-could-add.md` calls the redesign the largest single piece of work on the list. No GPU hardware for the model, GPU hardware to re-run the capacity proofs.
  - Evidence that closes it: a capacity-mode proof document reporting block-replay cost and measured write amplification in place of an in-place page rewrite.
- [ ] Export a skipped-access numerator so an access admitted without timing appears in a counter. The accounting unit is the kernel launch while charging happens per warp-merged request; in the first live run 10,584 launches touched registered memory, 0 were timed, and 0 ns of media time was reported. Known defect, severity A, `05-A-accounting-unit-is-the-kernel-launch.md`. No GPU hardware to add the counters, GPU hardware to measure.
  - Evidence that closes it: item 7 of `15-experiments-we-must-add-before-submission.md`, one workload run with timing forced on for every access and again with timing forced off for every access, with the difference reported.
- [ ] Record `time_scale` on every reported number and keep `time_scale: 100` away from any absolute performance claim. The 164.70x vLLM slowdown came from a profile with `time_scale` 100 and the proof document does not mention the factor: `configs/profiles/nominal.json` sets `read_latency_ns` 10,000 and `time_scale` 100, multiplied directly, so each charged access costs 1,000,000 ns. Known defect, severity A, `02-A-headline-164x-came-from-a-100x-time-scale.md`. 3 to 5 days of runs. GPU hardware.
  - Evidence that closes it: a calibration at `time_scale` 1, 10 and 100 from which physical numbers are derived rather than scaled wall time, with the `time_scale` of every run recorded.
- [ ] Settle whether an HBF write is a store instruction at all, and at which layer the injector belongs. The write path is modeled today by rewriting `st.global` in PTX. Known defect, severity A, `01-A-write-path-may-not-be-a-store-instruction.md`; the PTX-versus-SASS layer question is question 7 of `16-questions-only-you-can-answer.md`. Decision first, work second. No GPU hardware for the decision.
  - Evidence that closes it: a written decision under `docs/` naming the layer and naming the operations that layer can and cannot see, with the defect entry closed against the decision.
- [ ] Add a page-residency filter across warps. Without the filter, two warps reading one resident page are charged two full media reads, so the reported time comes out above hardware. Known defect, severity B, `09-B-no-page-residency-filter-across-warps.md`. The design consultation proposes two 4 KiB page-cache entries per bank and estimates 1 to 2 weeks, needing a device-visible shared structure and contention handling. GPU hardware.
  - Evidence that closes it: three warp-level microbenchmarks, 32 lanes on one 64-byte sector, 32 lanes on different sectors of one page, and 32 lanes on different pages, which the current code cannot tell apart in the first two cases.
- [ ] Set `page_bytes` to 4096 in `conservative.json`, `nominal.json` and `aggressive.json`. At 16 KiB the merge unit is more aggressive than the OCP NAND page granularity, so two addresses 4 KiB apart fall in one modeled page. Recorded in `18-spec-conformance-findings.md`; only `cd8p-vmem-p50.json` uses 4096 today. Hours to change, about one week to re-run everything downstream. GPU hardware for the re-runs.
  - Evidence that closes it: the three profiles changed and every downstream proof document re-run at 4 KiB.
- [ ] Inject delay where an access is consumed rather than where the access is issued. Injecting at the issue site turns an access that could have been overlapped into a stall in place. Known defect, severity B, `10-B-delay-injected-at-issue-not-at-use.md`, and item 9 of `15-experiments-we-must-add-before-submission.md`. GPU hardware.
  - Evidence that closes it: a paired measurement of one workload with injection at the issue site and with injection at the consume site.
- [ ] Measure block erase time instead of deriving the value. Erase is `program_latency_ns * 10`, that is 4,083,050 ns, with no measurement behind the factor of ten. Known defect, severity B, `12-B-erase-latency-is-a-derived-constant.md`. No GPU hardware.
  - Evidence that closes it: a measured or cited erase latency with the source registered in `docs/ref_article/README.md`.

## Validation

- [ ] Hold calibration points out of the fit and validate against the held-out points. All six breakpoints were used to fit the curve and none was held back. Item 11 of `15-experiments-we-must-add-before-submission.md`, one of the four experiments the repository itself calls blocking for submission. GPU hardware.
  - Evidence that closes it: a proof document reporting error at page counts that did not enter the fit.
- [ ] Compare the fast path against the detailed MQSim path on the same input. Nothing in the code puts the two outputs side by side, and `tests/cpu/calibrator_test.cpp` is 63 lines, asserting nothing about the two paths together. Known defect, severity C, `14-C-no-consistency-check-between-paths.md`. One concrete symptom: in the three named profiles the channel fields give 25.6, 51.2 and 102.4 GB/s while `aggregate_bandwidth_bytes_per_s` declares 128, 512 and 1000 GB/s, factors of 5, 10 and 9.8 apart, so the two paths may not be modeling the same device. About one week for the harness. No GPU hardware.
  - Evidence that closes it: a test that runs one request stream through both paths and asserts a stated agreement bound.
- [ ] Make `vmem_tuning` pass on a machine other than the calibration host. The test reads the absolute path `/home/victoryang00/nvme-mem2nvm/docs/superpowers/results/2026-07-30-vmem-sw-performance.csv`, which exists only on that host. No GPU hardware.
  - Evidence that closes it: `ctest` green on a clean checkout, with the CSV either committed or fetched by a script.
- [ ] Make `run_with_bpftime` skip cleanly instead of failing when `/usr/local/cuda-12.8` is absent. No GPU hardware.
  - Evidence that closes it: `ctest` on a machine without CUDA reporting a skip rather than a failure.
- [ ] Root-cause the `context_lifecycle` failure when a context is built with the empirical `cd8p-vmem-p50` profile. The failure is at source line 286 and reproduces on both `eval_base` and `docs/eval-mainline`. A separate `context_lifecycle` failure under concurrent builds was also never root-caused. No GPU hardware.
  - Evidence that closes it: a fix, a test that fails against the current code, and `ctest` at 34 of 34.
- [ ] Bring the known-delay fidelity numbers under the stated threshold. Per-chain K1 D0 mean absolute noise is 167,310.128 ns, and D500 mean and P95 error are 106,758.213 ns and 117,620 ns against an unchanged limit of 100 ns and 200 ns. A same-process ABBA arrangement narrowed the error to 5,083.489 ns and 13,193.532 ns, still above 100 ns. GPU hardware.
  - Evidence that closes it: a matrix-wide run meeting the 100 ns and 200 ns limits, not only the one bounded single-block ABBA run that reached 60 ns and 76 ns.
- [ ] Establish GPU baseline parity on the integration host, where `nvidia-smi` could not reach the driver and parity is therefore recorded as not verified. GPU hardware.
  - Evidence that closes it: a baseline-parity run recorded in `docs/eval/EVAL_BASE_INTEGRATION.md`.

## Modeling: stacks, links and topology

- [ ] Add `configs/systems/` as a system-level configuration layer, holding HBM stack count, capacity, bandwidth and latency; HBF stack count with a reference to a device profile; topology; per-link bandwidth and latency; and address mapping. `configs/eq3_thermal/topologies/` already holds four topology files with `layout` values `direct`, `daisy` and `dual`, but each file is a thermal-graph fixture marked `UNCALIBRATED_TEST_FIXTURE` and carries no link bandwidth, latency or queue field. 2 to 3 days. No GPU hardware.
  - Evidence that closes it: a schema, a parser, validation and tests, with the timing path reading the new layer.
- [ ] Keep `configs/profiles/*.json` frozen as the description of one HBF media device, and structure the configuration code as device profile, then stack organization, then interconnect topology, even where all three collapse into one file on disk. Folded into the `configs/systems/` entry above; the value is that MQSim, the tests and the historical results stay interpretable. No GPU hardware.
  - Evidence that closes it: `configs/schema/hbf-profile.schema.json` gaining no system-level or topology field.
- [ ] Express capacity, channel count and bandwidth per stack, and derive device totals from a stack count. `configs/schema/hbf-profile.schema.json` carries `channels`, `dies_per_channel`, `planes_per_die` and `aggregate_bandwidth_bytes_per_s` and no `stack_count` field at all, so `channels: 32` does not say whether one stack or the whole package is described. 1 to 2 days. No GPU hardware.
  - Evidence that closes it: a stack sweep at 1, 2, 4 and 8 stacks in which the totals are computed rather than re-entered by hand.
- [ ] Add `tier_id` and `stack_id` fields to the shared range record and the media descriptor instead of reinterpreting `stream_id`. Neither identifier exists in the runtime: the only matches are `tools/eq3_thermal_config.py:126`, `tools/eq3_thermal_smoke.py:60` and `tools/test_eq3_thermal_config.py:41`, all thermal fixtures, and the MQSim adapter sets `User_Request::Stream_id` to 0. Unverified: the field inventory of the shared range record and the media descriptor as reported in the design consultation was never checked against the source, so read both struct definitions before starting. 2 to 3 days. No GPU hardware.
  - Evidence that closes it: a request carrying a stack identifier end to end, asserted in a test.
- [ ] Replace the single scalar `fast_channel_tail_ns` with one service tail per stack, then one per stack per channel, so the 16, 32 and 64 channels the profiles declare produce parallel service. Every request currently contends for one tail. Known defect, severity B, `11-B-one-scalar-for-all-channels.md`. 1 to 2 weeks, including determinism tests and re-validation of every existing timing number. GPU hardware.
  - Evidence that closes it: a measured service-rate difference between a 16-channel profile and a 64-channel profile on the fast path.
- [ ] Instantiate one `MqsimOnlineEngine` per simulated stack over a shared single-stack profile, rather than adding a stack dimension inside MQSim. MQSim is a submodule carrying patches under `patches/mqsim/`, so the smaller change is the one outside MQSim. 3 to 5 days, plus a memory and throughput measurement of several concurrent engines. No GPU hardware.
  - Evidence that closes it: a benchmark running several engines at once, reporting per-engine and aggregate numbers.
- [ ] Add an `address_mapping` setting with contiguous and striped modes, and expert-aware mapping later. Contiguous partitioning alone serializes a streamed weight read onto one stack. 2 to 3 days for contiguous and striped; about one week for expert-aware mapping, which needs mixture-of-experts placement metadata. No GPU hardware.
  - Evidence that closes it: a stack-occupancy trace showing one streamed read spread across stacks under striped mapping.
- [ ] Model the GPU-to-HBM link, the GPU-to-HBF link and the HBM-to-HBF link as three independent resources. Each link carries a bandwidth, a latency, a queue and a service tail; a direct request occupies one link, a cascaded request occupies two links in sequence. Route HBM-tier traffic and cascaded HBF traffic through the same GPU-to-HBM queue, so a daisy-chain penalty emerges from queueing rather than from a constant hop latency added by hand. A cascaded relay is charged media time plus link and buffer time only, never an HBM DRAM cell-array access, which the thermal fixture already encodes as `"dram_array_access": False` in `tools/eq3_thermal_config.py`. About one week on top of the per-stack service tails. GPU hardware for validation.
  - Evidence that closes it: a matched pair of runs differing only in topology, at equal model, batch, placement, HBF parameters and HBM parameters.

## Modeling: endurance and temperature

- [ ] Track per-block erase counts and program bytes through MQSim and report four endurance metrics: write amplification factor (**WAF**), wear imbalance as maximum over mean program-erase count, the 99th-percentile program-erase count, and projected lifetime computed from maximum wear rather than mean wear. Nothing of the kind exists: a search for `write_amplification` and `WAF` across `src/`, `tools/`, `include/`, `configs/`, `benchmarks/` and `adapters/` returns nothing, and `configs/eq3_thermal/reliability.json` carries `"enabled":false,"status":"NOT_IMPLEMENTED"`. 2 to 3 weeks. No GPU hardware.
  - Evidence that closes it: a proof document reporting all four metrics for one workload, with per-block counters exported.
- [ ] Make the program-erase cycle limit a function of temperature, closing the loop from workload through write traffic, garbage collection, block wear, temperature and retired capacity. The activation energy in `configs/eq3_thermal/sources.json` is 1.04 eV with a 95 percent confidence interval of 1.01 to 1.08 eV and a coefficient of determination of 0.76, fitted over 20 to 70 degrees C. The same file records that the 85 degrees C specification point extrapolates beyond the fit temperature range, and the extrapolation has to be stated wherever the 85 degrees C point is used. 3 to 4 weeks after the wear accounting above. GPU hardware for thermal calibration.
  - Evidence that closes it: a retention deadline and a refresh write volume derived from a simulated junction-temperature trace, in a proof document that states the extrapolation.
- [ ] Connect temperature to the timing path. `src/eq3_thermal/` is a standalone CPU library with an independent `project()` declaration and no `add_subdirectory(src/eq3_thermal)` in the top-level `CMakeLists.txt`, so the baseline runtime links none of the thermal code, and `docs/eval/thermal_coverage.md` records the integration decision as deferred. Known defect, severity B, `08-B-thermal-not-connected-to-timing.md`, and item 5 of `15-experiments-we-must-add-before-submission.md`, one of the four blocking experiments. GPU hardware.
  - Evidence that closes it: a throughput difference between two thermal states produced by the running simulator rather than by an offline library.
- [ ] Implement thermal throttling as plane closure and write redirection, so delivered bandwidth becomes a function of time. A simulator that reports one fixed bandwidth figure mispredicts the serving queue. Depends on the per-stack and per-channel service tails and on per-stack power accounting. 2 to 3 weeks. GPU hardware.
  - Evidence that closes it: a bandwidth-against-time series from one run in which the hottest high-traffic planes close and writes move to cooler planes.
- [ ] Settle which of two conflicting thermal throughput measurements the project uses. One measurement reports 379.117 TFLOP/s at 35 to 70 degrees C against 348.427 TFLOP/s at 61 to 85 degrees C, a change of -8.10 percent; the second measurement reports -3.72 percent, and `configs/thermal/gpu-cd8p-logp-live.json` holds -3.72 percent in field `change_pct`, so the simulator runs with the smaller number. For the -3.72 percent measurement not one condition is written down. Neither number is the change in HBF service rate with temperature; both describe the GPU compute chip slowing itself down as temperature rises. No GPU hardware for the decision, GPU hardware to re-measure.
  - Evidence that closes it: one re-measurement with the conditions recorded, and the number not chosen marked superseded at every place the number appears.

## Evaluation campaign

- [ ] Move EQ1 out of the blocked state and EQ2, EQ3 and EQ4 out of partial. Formal completion stands at 0, sealed coverage is 30 of 20,485 planned runs and 30 of 13,105 minimum runs, and 22 minimum follow-up patches, P-Q1 through P-D2, are listed with none implemented. GPU hardware.
  - Evidence that closes it: per-question completion recorded in `docs/50-run-status.md` with the formal count above 0.
- [ ] Clear the storage blocker on the D0 condition set. D0 alone is 27 conditions and 270 repeats, straight uncompressed scaling of 50 to 60 GB exceeds available workspace headroom, and the storage target is recorded as blocked because no exclusive SSD input/output path is available. No GPU hardware for the storage provisioning itself.
  - Evidence that closes it: a completed D0 sweep, or a recorded decision to compress or subsample with the effect on the result stated.
- [ ] Re-run the two queue-depth-invalidated sweeps at a bounded queue depth. Every P50 and P99 value in the die-density table, the `plane_allocation_scheme` table and the NAND cell-type table ran with every request outstanding at once, and no corrected value for the three sweeps exists. The corrected MQSim media benchmark numbers are p50 659,840 ns and p99 1,309,370 ns; the pre-fix numbers must not be quoted anywhere. Whether the qualitative conclusions survive a bounded queue depth is not known. No GPU hardware, because MQSim runs on the CPU side.
  - Evidence that closes it: three re-run sweeps on the frozen head, with the pre-fix provenance kept and marked as superseded.
- [ ] Restore the figure renderer for the revised evaluation plan. The old renderer passing does not mean the new T1, T2, C1 and C2 figures are implemented, and the new figures are recorded as blocked on the renderer. No GPU hardware.
  - Evidence that closes it: the new figures produced from recorded run data, each figure paired with the run that produced the figure.
- [ ] Establish or withdraw the prefetch benefit claim. No validated prefetch benefit was established: at the original capacity, one-layer-ahead prefetch was inactive and matched on-demand item for item, and two further rows were classified as prefetch trigger not observed. The only cells in which prefetch fired changed capacity, so the capacity-sensitivity cells cannot establish a speedup claim. The prefetch model is built only under `HBFSIM_ENABLE_EVAL_TOOLS`, off by default, and is a sensitivity model rather than an implemented predictor. GPU hardware.
  - Evidence that closes it: a run in which prefetch fires at the original capacity with the generation-time difference reported, or a written withdrawal of the claim.

## Repository and tooling

- [ ] Tag the first release. No tag exists in the repository today, so the checkbox-and-release convention described at the top of this file has no starting point. No GPU hardware.
  - Evidence that closes it: a tag on `main` and a release page listing the repository presentation work under Done below.
- [ ] Update the clone and branch instructions in `README.md`. `README.md` states that the default branch is `eval_base` and that a clone should pass `--recurse-submodules`; the default branch is now `main`, and the `paper` submodule is marked `active = false` and `update = none` because the submodule points at a private Overleaf repository. No GPU hardware.
  - Evidence that closes it: a clone of `main` on a machine with no credentials for the private repository, followed by a successful build.
- [ ] Retarget the two open pull requests onto `main`. Pull request 4 carries `fix/ptx-async-copy-coverage` at `936b8e54`, whose classifier fix reached the default branch through a different commit, and pull request 5 carries the runtime readahead work at `1f19bdb`, recorded as deferred and unverified because `submit_speculative` has no caller and pages can become resident without a corresponding modeled speculative access. Neither head is an ancestor of the default branch. No GPU hardware.
  - Evidence that closes it: both pull requests either merged into `main` or closed with the reason recorded on the pull request.
- [ ] Decide and record a redistribution license for the reference material under `docs/ref_article/`. The directory holds published PDFs from several venues alongside vendor documents and Chinese-language captures, while the repository as a whole is Apache-2.0, which does not cover redistribution of third-party papers. No GPU hardware.
  - Evidence that closes it: a per-file entry in `docs/ref_article/README.md` recording redistribution status, and removal of any file that may not be redistributed.
- [ ] Correct the stale statements in four documents, at the statement itself rather than in an appended note. `16-questions-only-you-can-answer.md` says the specification text has still not been obtained, although `docs/HBF_OCP/ocp2026-hbf-architecture-specification-v0-7-0.txt` has been in the repository since commit `f340d60`, and `16-questions-only-you-can-answer.md` also says a search of `src/` and `include/` for thermal terms returns zero hits, although `src/eq3_thermal/` and `include/hbfsim/eq3_thermal/thermal.hpp` now exist. `15-experiments-we-must-add-before-submission.md` names a submission deadline that has passed, the internal defect register declares the code was read from `origin/hybrid`, and the three `docs/50-*` documents describe a different checkout. No GPU hardware.
  - Evidence that closes it: each of the four documents carrying the correction in the affected section.
- [ ] Keep `scripts/check_doc_links.py` in the pull-request gate and extend the checker to external links. The checker resolved 667 in-repository Markdown links in the round recorded below, and the continuous-integration workflow is CPU-only, so no GPU gate is covered there. No GPU hardware.
  - Evidence that closes it: a workflow run that rejects a pull request introducing a broken link.

## Done

Each entry below names the artifact inline, so the artifact is the evidence.

- [x] Add `LICENSE` (Apache-2.0), `CITATION.cff`, `CODE_OF_CONDUCT.md` and `SECURITY.md`.
- [x] Rename the default branch to `main`, from `eval_base`.
- [x] Stop `git clone --recurse-submodules` from failing for outside users. The `paper` submodule pointed at a private Overleaf repository, so a clone with submodules failed on authentication; `.gitmodules` now marks the submodule `active = false` and `update = none`, and `scripts/bootstrap.sh` initializes only the two build dependencies.
- [x] Add `.github/workflows/ci.yml`, a CPU-only build and test plus a repository hygiene check.
- [x] Add `.github/ISSUE_TEMPLATE/` with bug report, feature request and config entries, and `.github/pull_request_template.md`.
- [x] Add `scripts/check_doc_links.py`, which resolved all 667 in-repository Markdown links on the run recorded in this round.
- [x] Add `docs/assets/hbfsim-architecture.png` and `docs/assets/hbfsim-architecture.svg`, the architecture figure from the paper.

## How to claim an entry

Open an issue that quotes the first line of the entry, state which evidence artifact will be
produced, and link the artifact once the artifact exists. Claiming an entry before the evidence
exists is fine; checking the box before the evidence exists is not.

An entry marked as needing GPU hardware was validated on an NVIDIA RTX PRO 6000 Blackwell Server
Edition with driver 595.84. An entry that cannot be validated on comparable hardware should say so
in the issue, so that the entry can be split into a part that runs anywhere and a part that needs a
GPU.

An entry marked as a known defect is a repair: the defect document already states the mechanism, the
severity and the source-code location, so a claim on a known-defect entry should start from the
defect document rather than from a fresh investigation.
