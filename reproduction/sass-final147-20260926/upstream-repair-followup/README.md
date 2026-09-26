# Upstream repair and merge follow-up — 2026-09-26

This supersedes the initial upstream review's OPEN / DO_NOT_MERGE status without replacing its original findings or failures. All five PRs were repaired/tested in independent worktrees and merged into `vickiegpt/Concordia:tmatmul`. Final commit: `facddd728db7931c5cc37f165f6fd66e312dba34`. The remote tree equals the reviewed combined tree, `1a1ec20aa474d3740a1137e518b4a13f5ec43997`.

- [PR1: FSEL payload tests](https://github.com/vickiegpt/Concordia/pull/1)
- [PR2: high-result multiply/add pair semantics](https://github.com/vickiegpt/Concordia/pull/2)
- [PR3: CS2R pair scope and tests](https://github.com/vickiegpt/Concordia/pull/3)
- [PR4: GEU and BF16 modes](https://github.com/vickiegpt/Concordia/pull/4)
- [PR5: generic register pairs and portable tests](https://github.com/vickiegpt/Concordia/pull/5)

Combined validation: **107 passed, 0 failed, 0 ignored, 0 filtered**, comprising 93 source-module tests, 12 fuzz/library integration tests, and 2 pipeline tests. All three optional offline NVIDIA assembler tests ran explicitly; there was no GPU execution. Numerical tests cover nonzero high words, carry/wrap, aliases, predication, rounding, packing and unsupported forms; the existing non-QKV integer fixture remains tested. A portable actual-source harness is committed upstream at `tools/prepare_sass_cpu_tests.py`; it imports the real parser and complete related CPU integration files.

Scope limits remain explicit. Complete workspace compilation is blocked by a baseline absent Gemmini target; a separately configured conventional `ptx` build reached LLVM compilation but timed out after 600 seconds. Neither is a workspace PASS. Internal parsing accepts BF16 RN/RZ, while RELU variants return Todo and fail the checked translation pipeline; all four forms produce legal NVIDIA PTX. Built-in binary decoding, other backends and arbitrary architectures are not certified by these fixes.

[Why HBFSim passed](HBFSIM_GAP_ANALYSIS.md): actual saved head/router parameters statically bypass the incorrect HI tail path; the original model result did not test arbitrary operand/mode combinations. Extrapolation to final147 per-call branches is labeled inference because full aggregate bytes were not retained for every call. Existing output/service validation is unchanged.

The HBFSim frozen runtime, helper, source snapshots and raw results were not patched or rerun. These upstream fixes are a successor source change, not retroactive validation of the historical binary. Initial publication manifest remains the manifest for the earlier commit; this follow-up is separately versioned by Git.
