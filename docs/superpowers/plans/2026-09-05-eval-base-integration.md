# eval_base Integration Implementation Plan

> Execute inline using the executing-plans workflow. The user supplied the design, integration gates, logical commit strategy and authorization to validate and push; no new design approval is required.

**Goal:** Publish a reproducible evaluation base descended from frozen hybrid b41142288c1d1ca13be4219c320dbfa621a0300f.

**Architecture:** Select reviewed documents and bounded offline tools. Keep the runtime, control ABI, profile defaults, MQSim admission and coverage policy from hybrid. Replay the final PR4 coverage correction plus comment-safe integration from capacity commit 80fd29c. Reject whole-branch merges of inherited prefetch and thermal architecture.

**Tech Stack:** C++20, CMake/Ninja/CTest, Python unittest, pinned bpftime and MQSim, CUDA static checks when buildable.

## Tasks and gates

- [x] Fetch and audit all nine remote branches, merge bases, counts, unique commits, patch equivalence and endpoint diffs for unrelated history. Evidence is outside Git; final matrix belongs in docs/eval/EVAL_BASE_INTEGRATION.md.
- [ ] Run hybrid CPU/MQSim baseline; record genuine failures before changes. Disable optional PkgConfig discovery for both CPU builds because upstream creates a CUDA llama module from libbpf discovery even with CUDA disabled. Do not change runtime to work around environment.
- [ ] Select docs 47/48 and their supporting text. Preserve mainline E1-E4 terminology; state branch-specific historical and planned claims explicitly. Add docs/eval/README.md as current-status entry point. Do not import prefetch runtime, generated sweeps, paper pointer or reference PDF.
- [ ] Replay PR4 final three-file delta and then 80fd29c comment-safe three-file delta. Demonstrate regression failure against hybrid and original PR4, then pass final coverage and transformation tests. Preserve strict/capacity rejection.
- [ ] Integrate only independent offline prefetch model/header, benchmark, sweep script, unit test and bounded documentation. Use a separate static library behind HBFSIM_ENABLE_EVAL_TOOLS=OFF. Validate disabled baseline, policies, seeds, accuracy, latency and concurrency; do not claim actual predictor or GPU performance.
- [ ] Integrate minimal capacity offline closure: model_inventory.py, placement_policy.py, trace_replay.py, trace_validation.py, trace_schema.json, policy/replay tests, benchmarks/replay/hbf_trace_timing.cpp and its two integration tests. Guard executable and tests with the same option. No vLLM capture/staging, public stats ABI or loader changes. Document supplied trace/manifest requirements and current unsupported live metrics.
- [ ] Write capacity methodology, aggregation rules, coverage, thermal gate assessment, source/commit manifest and portable runbooks. Keep E1-E4 canonical, legacy EQ1-EQ4 crosswalk explicit. Record missing hbf_eval and qwen3_moe_capacity paths rather than inventing them.
- [ ] Test combined CPU default and optional builds, Python tests, schema consistency, PTX fail-closed cases, matched media baseline parity; attempt CUDA build and static tests with no live-GPU claims.
- [ ] Review every changed file, check links and whitespace, keep artifacts outside source, commit logical changes, refresh remote heads, push eval_base only if all required CPU tests pass and worktree is clean. Never force or modify hybrid/other remote branches.
