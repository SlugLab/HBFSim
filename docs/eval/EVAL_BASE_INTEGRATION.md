# HBFSim eval_base Integration Manifest

## Purpose

eval_base 是论文 Evaluation 的统一稳定基础，不取代 hybrid。

The user authorized audit, selective integration, validation and a normal push of eval_base only. All existing remote branches, default branch, PR state and private paper pointer are preserved. Working directory was an experiment archive rather than a Git checkout, so work used a new independent clone. Existing archive/source directories were not reset, stashed or deleted.

## Base

hybrid SHA: `b41142288c1d1ca13be4219c320dbfa621a0300f`  
Date: 2026-09-05 UTC  
Validated implementation SHA before this documentation-only manifest: `39bce4e`  
Final branch SHA: obtain with `git rev-parse HEAD`; the manifest cannot embed its own content-derived commit ID. The final delivery reports the exact pushed SHA.

Remote refs were frozen after `git fetch origin --prune`. GitHub connector also verified all nine branch heads and PR4/PR5 (both open, unmerged). Git ancestry and code, not PR labels, determined integration. No local or remote eval_base existed at the initial fetch.

## Branch matrix

Ahead/behind means source-only / hybrid-only commit counts, not endpoint file counts. For every related remote branch the audit recorded merge-base, `rev-list --left-right --count`, `log --left-right --cherry-pick`, `git cherry`, three-dot diff stat and rename-aware name-status. The unrelated full-chain branch was checked by roots and endpoint diffs; a missing merge-base is not silently treated as an empty diff.

| Branch | Source SHA | Ahead / behind hybrid | Decision | Type/risk | Reason |
|---|---|---:|---|---|---|
| `docs/eval-mainline` | `297db90e761784463bcbace4803667d5b9fe0028` | 15 / 2 | SELECTIVE | docs, low code risk | Canonical docs only: source inherits unverified PR5 runtime; not a pure-doc fast-forward. |
| `exp/hbm-hbf-capacity-qwen3-30b-a3b` | `37144843906b3bd71f3fbac1fecc6b5080d82b95` | 17 / 0 | SELECTIVE | experiment, medium | Semantic Qwen docs, offline replay closure and test/PTX corrections; live staging and public ABI changes deferred. |
| `feat/prefetch-model-and-accuracy-experiment` | `1f19bdbba06c89cbaf9b7e6874f3473dc8123b2b` | 10 / 2 | SELECTIVE | offline model medium; runtime high | Independent model only; runtime readahead DEFERRED because speculative submission has no producer, plus ticket/rollback risks. |
| `feature/sm120-exact-stage1` | `f4dc28b2671c01939d98e4a968e6fb37b2e364d9` | 40 / 27 | DEFER | architecture, high | 186-file async/PTX IR/TensorMap/TMA/calibration delta; not required for this baseline. |
| `feature/thermal-reliability` | `0069b2eec4d8b9d37cdbc9fbf84035d15572b0dd` | 61 / 27 | DEFER | architecture, high | Full SM120 ancestor, ABI 4 to 10, no minimal isolated combined parity gate. |
| `fix/mqsim-queue-depth-admission` | `12ef13809dc13424981c123f13dc31e6d0456a80` | 0 / 3 | ALREADY_INCLUDED | correctness, low | 12ef138 is an ancestor of hybrid; merge b411422 already contains the fix. |
| `fix/ptx-async-copy-coverage` | `936b8e548c0ad487975fea0de73938a1949bc156` | 4 / 2 | INCLUDE | correctness, medium | Replay final delta plus comment-safe 80fd29c integration and validated .loc correction; strict rejection preserved. |
| `hybrid` | `b41142288c1d1ca13be4219c320dbfa621a0300f` | 0 / 0 | BASE | baseline | Frozen primary design/code lineage; never rewritten or pushed to. |
| `全链路温度模拟` | `fd11c3d98cde74977be7ec504c64429369b6fd3a` | 8 / 91 (unrelated histories) | DEFER | independent snapshot, high | No common ancestor; package/ROM/3D-ICE/retention offline direction remains separate. |

## Integrated sources

| Source | Method | Resulting commit(s) |
|---|---|---|
| docs/eval-mainline 297db90 | Select doc47/doc48, supporting Markdown and open-question deltas; explicit current/historical/planned status; omit inherited code, paper pointer, result artifacts and reference PDF | 33db4a0 |
| PR4 936b8e5; capacity merge 80fd29c | Final three-file patch replay and comment-safe parser/test additions; no obsolete intermediate tensor exclusion | 975f0d5 |
| capacity test correction 9d210eb | Exact pattern/seed dictionary assertions for unchanged MQSim output | 69e3c51 |
| PR5 1f19bdb | Independent prefetch model/header/benchmark/script/test only; separate library behind default-OFF option; fix demonstrated unsigned recovered_fraction underflow | 47db5d2 |
| capacity 3714484 | Offline inventory, policy, trace replay/validation/schema, standalone page-timing tool, tests and explanatory README | 82656d4 |
| capacity source docs + canonical docs | Semantic Qwen capacity/metric/reproduction consolidation; no competing old EQ framework | 5560b7b |
| capacity fixture correction d183f2b | Populate eight mandatory per-page latency fields in fake-CUDA fixture; preserve strict production validator | aaa216a |
| integration review correction | Preserve coverage/rewriting after newline-terminated PTX .loc directives; CPU and CUDA tests | d68fff8 |
| integration review correction | Bind full profile configuration/hash into offline cell identity; validate exact unique tensor identities/dtype/source/offsets and observed byte totals | 39bce4e |
| audit/assessment/runbook metadata | This final documentation commit; identify using `git log -1 --format=%H -- docs/eval/EVAL_BASE_INTEGRATION.md` | manifest commit |

The commit history contains these logically separated implementation changes:

```text
33db4a0cbc5b0bf122a88aac5991b46a433572bf docs(eval): sync evaluation mainline
975f0d551e39856af78c0b7b6181620f75105bfc fix(eval): integrate PTX async-copy coverage correctness
69e3c5188e77414c98fc9014e953faa928b5809d test(eval): sync MQSim workload provenance fixture
47db5d2c93016663b6bec80b055a41bb52078e67 feat(eval): add optional offline prefetch evaluation model
82656d4c2690b3efad9d6414505439a37b82b4d1 feat(eval): add optional Qwen capacity trace replay
5560b7b83a12579909fe1624c89ea49c030935eb docs(eval): consolidate Qwen capacity and thermal scope
aaa216aa62ce9f2449c9550de2dc5bbf551643ae test(eval): refresh capacity runtime latency fixture
d68fff88d50539a72616c5f67c443bf5c80c7018 fix(eval): preserve memory coverage after PTX location directives
39bce4e6933b18476527c0764e149958e16e5f17 fix(eval): bind profile provenance and validate trace tensor identity
```

## Already contained

`fix/mqsim-queue-depth-admission` at 12ef13809dc13424981c123f13dc31e6d0456a80 is an ancestor of frozen hybrid and is not replayed. No patch-equivalent unique candidate was duplicated. PR4 is not an ancestor of hybrid; its tested final delta is explicitly replayed. The capacity branch contains PR4 via 80fd29c; that does not mean the whole capacity branch should be merged.

## Deferred branches and features

Thermal and SM120 code are DEFERRED; full-chain temperature simulation is DEFERRED. See [thermal assessment](thermal_integration_assessment.md), including module files/commits, independent-snapshot comparison and all A–I gates.

PR5 runtime readahead is DEFERRED/UNVERIFIED: `git grep submit_speculative` at its HEAD finds only the declaration and definition, no caller. `CapacityWorker` calls `run_one_readahead` without submitting a modeled media action. Pages can become resident without corresponding modeled speculative work. Reserved high-bit tickets and wrap behavior are explicitly unverified; failed frame copies can also follow clean eviction. No runtime speedup claim is accepted. The independent offline model is included and tested, not the guarded runtime feature originally hoped for.

Live vLLM capture/staging, capacity statistics public ABI v2, launch interposition changes and new GPU workloads from the capacity source are deferred. Their dependency closure includes public API/context/loader/CUDA coupling not needed by offline replay. No new weight files, model download, trace campaign or server-only runner is silently imported. The concrete optional closure and unsupported metrics are [documented here](capacity_qwen3_30b_a3b.md).

## Documentation provenance and conflict resolution

Current implementation wins over source plans; then canonical doc47 E1–E4, then compatible Qwen-specific detail. No `ours/theirs` or whole-doc-directory checkout was used. There were no unresolved Git textual conflicts; these semantic overlaps were resolved explicitly:

| File/concept | Sources | Decision and reason |
|---|---|---|
| doc47/Evaluation structure | docs/eval-mainline versus capacity doc19 EQ outline | E1–E4 canonical; EQ crosswalk in overview, old outline remains cited source |
| doc47 existing replay/capture claims | source experiment snapshot versus hybrid | offline replay added as minimal closure; capture/staging, serving metrics, byte coverage and Snapshot B remain planned/deferred |
| doc47 forced-admission experiment | research ablation plan versus fail-closed invariant | explicitly not implemented; no coverage bypass |
| doc46 prefetch tables/locality | PR5 synthetic results versus actual predictor/GPU claims | historical model outputs labeled synthetic; next-page accuracy distinguished from expert prediction; no checked-in result refresh |
| Qwen capacity metrics/schema | requested physical ratio/serving terms versus actual replay | expert-cache logical requested and achieved ratios, whole-expert policy/page-timing split, unavailable TTFT/TPOT clearly labeled |
| runtime/profile/defaults | PR5 and thermal changes versus hybrid | preserve hybrid runtime/profile/ABI/MQSim defaults unchanged |
| PTX transform | hybrid, PR4, capacity comment assembly | preserve stronger unsupported coverage; test .loc regression discovered by independent review |
| MQSim/fake CUDA fixtures | current validators/reports versus stale assertions | minimal source test corrections; no relaxation of validators |
| profile identity/trace validation | capacity offline source versus provenance contract | preserve full profile hash/config and reject incorrect tensor metadata |

Source auxiliary documents retain historical local evidence paths and dates as provenance, not portable commands or this branch's validation. The new runbooks use configurable paths outside the source tree. The new reference PDF and generated prefetch CSV/JSON were not imported; doc48 names their pinned source. Paper/Overleaf gitlink is unchanged and not required for testing. The README evaluation entry link was added and its existing CUDA-compatibility link corrected to the current file location.

## Enabled by default

Exactly hybrid's runtime modes, profile loader, named profiles, MQSim queue-depth enforcement, capacity cache, control ABI v4 and reports. PR4 coverage correctness is unconditional: supported operations remain instrumented; newly visible unsupported async globals trigger the existing policy.

## Disabled by default

`HBFSIM_ENABLE_EVAL_TOOLS=OFF`. When enabled, prefetch arithmetic builds as a separate static library linked only to standalone tools/tests; capacity replay builds a standalone executable using the existing core. There is no runtime readahead or thermal reliability switch to enable here because those features are not integrated. Existing thermal LogP/calibration scripts remain available.

## Experimental features and six invariants

1. Fail-closed policy is preserved. Strict/capacity affected launches reject unsupported operations. Hybrid's pre-existing non-strict timing opaque/unmodeled allowance remains explicitly unmodeled; it is not full coverage.
2. Default runtime baseline is preserved. Source diff under runtime, host service, profile, MQSim, core ABI, configs, patches and third-party pins is empty. PTX classification intentionally becomes stricter; no identical-admission claim is made for unsupported kernels.
3. Provenance is retained: exact source SHAs, model/trace hashes, full profile contents/hash and cell identity. The prefetch sweep records parameters/seed/commands/disclaimer; archive code/build/binary hashes alongside output as required by its runbook.
4. Modeled, empirical, proxy and measured quantities are distinct. Host simulation wall time is not projected GPU performance. No new physical HBF or Qwen results are claimed.
5. No default profile parameter changes. All 19 checked-in profiles validate against the unchanged schema.
6. One capacity replay implementation and one separate synthetic prefetch model; no competing live evaluation framework was imported. Aggregation policy is documented; a general independent-repeat/CI tool is PLANNED, not claimed implemented.

## Test matrix

Build commands follow [environment setup](runbooks/environment_setup.md). Evidence logs/results remain outside Git under the sibling `eval-base-evidence` directory. Final tests used unique writable TMPDIRs per suite. Each suite is a full CTest invocation, without test exclusions.

| Command / check | Result | Scope |
|---|---|---|
| original hybrid CPU configure/build (CUDA OFF, MQSim ON, Debug, PkgConfig disabled) | PASS | pinned baseline executable |
| original hybrid `ctest --output-on-failure` | FAIL 35/36 | only mqsim_benchmark stale pattern/seed expected dictionary; retained original log |
| original PR4 test linked to hybrid core | expected FAIL | cp.async silently absent before fix |
| comment-safe test linked to original PR4 core | expected FAIL | multiline comment parser gap |
| new .loc test before fix | expected FAIL | atomic/async following debug directive omitted |
| prefetch harmful-traffic report before fix | expected FAIL | recovered_fraction overflowed to ~1.44e14 instead of negative |
| capacity provenance negative tests before fix | expected FAIL | identical cell ID for different latencies; duplicate/wrong tensors accepted |
| CPU default full rebuild and CTest with isolated TMPDIR | PASS 37/37 | final source, offline tools OFF |
| CPU optional-tools full rebuild and CTest with isolated TMPDIR | PASS 42/42 | combined parser, capacity, policy, prefetch, queue and replay |
| CUDA 13 / G++14 sm_120 full build and CTest with isolated TMPDIR | PASS 61/61 | static PTX/helper and fake-driver tests; not live GPU |
| `PYTHONPATH=adapters/vllm_capacity python3 -m unittest discover -s adapters/vllm_capacity/tests -p 'test_*.py'` | PASS 10 tests | policy, tensor-validation and profile identity |
| `python3 -m pytest -q adapters/vllm/tests` | PASS 24 tests | original timing adapter; no live Qwen execution |
| prefetch sweep `--seed 7`, explicit external JSON/CSV | PASS 252 modeled cells | sequential/random/synthetic MoE, 6 latencies, accounting checked |
| JSON Schema validation of 19 profiles and trace schema metaschema | PASS | trace fixture tests do not substitute for complete real-capture schema validation |
| matched hybrid/eval media comparison | PASS 18 cells | 3 profiles x QD 1/4 x sequential read/random read/sequential mixed; every deterministic JSON field equal |
| full runtime/config/pin source diff | PASS empty | excludes intentional PTX classification changes and standalone tools |
| `git diff --check`, changed-file/relative-link review and artifact scan | PASS before publication | final hygiene gate |
| general independent-repeat statistical aggregator | SKIP / PLANNED | not present in source branches; accounting checked by replay tests |
| actual Qwen/Triton coverage, CUDA live capacity and GPU baseline parity | SKIP / NOT VERIFIED | NVIDIA driver unavailable; no hardware campaign performed |
| runtime prefetch saturation/dirty-victim/rollback/combined thermal tests | SKIP / DEFERRED | affected runtime features are not imported |

## Validation failures and environment limits

The first baseline CPU configure found libbpf/clang and created a CUDA llama probe even with CUDA OFF, failing linker-language inference. Baseline and candidate CPU builds both disable optional PkgConfig discovery; the underlying upstream build behavior is recorded rather than patched away.

The initial CUDA suite passed 60/61; the fake-driver capacity fixture omitted eight newly required NAND latency fields. Replaying test-only d183f2b made the fixture pass with production validation unchanged. This was not a missing-GPU failure.

A simultaneous rebuild/full-suite run of three builds failed `context_lifecycle` at line 286 (tuned-context startup). The same final binaries passed every test on rerun with isolated TMPDIRs. The exact cause of the concurrent startup failure was not established; shared temporary/process environment or startup scheduling is a test-environment limitation, not a proven source fix. Keep the failed logs; use isolated TMPDIRs or serialize suites. No timeout, assertion or runtime was weakened.

An initial unittest invocation on the pytest-style vLLM tests collected zero tests and was not accepted as validation; the correct pytest command passed 24. Compiler deprecation/narrowing warnings and Python NVML/SWIG warnings remain recorded; none are promoted to live-GPU proof.

## Environment

Linux 6.18.0-rc5 x86_64; CMake 4.2.3; Ninja; Python 3.13.9. CPU compiler GNU 15.2.0. CUDA toolchain 13.0.88 with GNU 14 host/C++ compiler, single architecture 120. OpenSSL 3.5.5. bpftime ec26daecc8e787fb80fd95dd596a576404a5e36e and MQSim 51f0f2d3fed92d88ef4a0fa61a38024b07bf9d16 verified before builds.

`nvidia-smi` could not communicate with the NVIDIA driver. GPU baseline parity: **NOT VERIFIED**. No driver changes or model/trace downloads were performed. The real model/capture environment is an external prerequisite; CPU toy fixtures do not replace it.

## Reproduction entry points

Use [build setup](runbooks/environment_setup.md), [mechanism validation](runbooks/mechanism_validation.md), [capacity definitions](capacity_qwen3_30b_a3b.md), and [aggregation rules](aggregation.md). Build directories, result JSON/CSV, raw logs, model weights and caches are not committed. Compare measured fields only within their evidence class and freeze profiles before sweeps.

## Known limitations

Runtime prefetch, thermal reliability, SM120 exact/TMA support, byte-level coverage, serving TTFT/TPOT/SLO, new live Qwen capture/staging, full dynamic VRAM budgets and independent-repeat CI aggregation are not implemented by this integration. Offline replay is read-only expert-cache accounting with ordered blocking page misses, not a live GPU execution predictor. Prefetch input overflow/huge-stream stress is outside current tested bounds. The PTX parser is not a complete grammar proof over all legal formatting. Historical documentation references and experimental results retain their source-specific validation boundaries.

## Future branch policy

```text
hybrid
  -> eval_base
       -> eval/capacity
       -> eval/prefetch
       -> eval/batch-size
       -> eval/cloud-edge
       -> eval/thermal
```

Future experiments should start from eval_base. Re-audit source commits, keep features optional/default-off, preserve strict admission/provenance, and run combined regression and matched-off parity. Thermal can use `eval/thermal-reliability` as a specific child. No automatic merge into hybrid, default-branch change, branch deletion or PR closure is authorized by this publication.

## Publication gate

Publish only a clean, reviewed eval_base with passing required CPU suites, complete provenance and no conflicts. Refresh remote refs and verify existing source heads remain unchanged. If origin/eval_base appears, only a safe fast-forward is allowed; never force or rewrite history. The final delivery records the observed push result and exact remote SHA. This file records the pre-publication integration evidence; it does not predict a future push result.
