# Current state and evidence reuse — 2026-09-08

Canonical question definitions are in [doc49](../49-new-evaluation-plan.md). This file binds today's audit and supersedes old capability/status prose without altering old evidence.

## Workspace and baseline

- Host `gpu01` (the user-supplied 6787p context); source `/root/hbfsim-exp`, actual repo `eval-base-integration`, branch `eval/eq1-eq4-implementation`, HEAD `254d65a66279fbaffc5c185d04fbe41dc8dbba44`. Root container is not a valid Git repository. No applicable ancestor/root AGENTS.md was found; nested vendor CLAUDE.md was outside edited scope.
- Pre-existing tracked/index diff was empty; `figures/` and `results/` were untracked. Saved status/diffs/untracked/worktrees/submodules in workspace `reports/eq-replan-20260908/`. No reset/clean/checkout/merge/push; current branch retained.
- GPU RTX PRO 6000 Blackwell Server Edition, UUID `GPU-f07ea2df-1b6f-9a02-b534-5090abf3c174`, driver595.84, 97887MiB, sampled0MiB use. CUDA tools/environment smoke recorded separately. Accessibility is not G2/G5 scientific closure; GDDR execution is not HBM/HBF measurement.
- Current formal controllers were previously `PAUSED_BY_USER`; both recorded STOPPED/no owned survivors and `automatic_restart_authorized=false` in `reports/experiment-pause/paused-status.json`. Fresh project-scoped process inventory found no experiment writer. Migration preserves pause and does not restart jobs.
- PM1733a serial `S6USNE0TA08224`, `/dev/nvme2n1p1`, ext4 UUID `37be6892-a8aa-4fae-885d-5feed3b28fab`, host rw. Root source is Lexar `/dev/nvme1n1p2`, not CD8P. Source inventory:519221 regular files,92018 directories,2187 symlinks,one inactive FIFO; no scan error/nested device; 6 sparse files and67 external symlink paths. Inventory counts precede this task's generated documents/receipts.
- Source allocated regular-file unique bytes141174820864; apparent140039527429. Target preflight free690323095552B; budget364513120256B includes temporary8GiB/tools8GiB/bounded future64GiB/reserve128GiB. External model/cache/library targets stay external; symlinks are not dereferenced. This migration does not relocate `/home` model blobs or global `/opt` dependencies.

Migration completed with verified source-copy cleanup: observed root free-space increase141639696384bytes (131.912GiB). The logical prefix now resolves to `/mnt/disk0/hbfsim-exp`; paused controllers remain stopped. Full receipts and space change: [migration report](review-evidence/20260908/migration-report.md), [manifest](review-evidence/20260908/migration-manifest.json). The final manifest binds checksum,660 Git repositories,environment/model smoke and exact cleanup; first-copy receipts remain historical stage records.

## Current implementation versus historical claims

| Area | Verified current status | Safe reuse / missing evidence |
|---|---|---|
| Scalar future | Current C6 emitter has guarded possible consumers and fail-closed unsupported paths; freshly compiled19 CPU tests pass | CPU engineering correctness only; scalar fast TIMING, no capacity future/async MQSim/TMA; final SASS first consume and resource lifetime need independent GPU evidence |
| C6 prior timing | D=20us expires before observed W in retained attempts; fixed K0/K4096 did not establish useful W contrast | Preserve CAPTURED_UNVALIDATED and negative diagnosis; cannot close incremental-stall/overlap gate by retrying unchanged |
| Donor TMA/thermal | SM120 donor ancestor of thermal branch; standalone full-chain and49f9b2 snapshots lack common ancestry with current tree | Endpoint audit, no blind merge; source/build/control ABI must be bound together if separately integrated |
| MQSim service | Current queue-depth admission fix exists; per-profile resources and completion cursors present | Keep engineering/conservation tests; aggregate cap is not physically verified shared TSV arbitration; OCP HBF GC rules differ from SSD profile |
| Thermal | Current tree no package thermal runtime; prototype controller→activity→power→ROM→policy loop exists | Frozen ROM/3D-ICE numeric validation reusable; true-off runner/bin accounting/nominal parameters and refresh execution missing |
| Inventory and allocation | Existing inventory, effective fast budget, whole-expert packing and unbounded requested rho | Real KV formulas exist; independent staging/reservation and physical capacity/topology binding are missing |
| Routing/prefetch | real/shuffled metrics and analytic null; causal offline replay with three distinct demand/prefetch policies | TRACE_COMPOSED prefix, not live serving; analytic null has no replay request stream; dense matching/live prefetch remain gated |
| Renderer | Existing v1 mock/hash/unit/context/pair checks remain | NewT1/T2/C1/C2/expandedM panels and optional schema enforcement not implemented; no new formal figures claimed |

## Artifact-level reuse and exact code locations

[EQ1 audit](review-evidence/20260908/eq1-audit.md) contains C01–C11/S01–S04 source ledger, static/CPU evidence and old C6 negative results. [Thermal audit](review-evidence/20260908/thermal-audit.md) and [13-row reuse CSV](review-evidence/20260908/thermal-reuse-table.csv) cover every result named in the request. [EQ3/4 audit](review-evidence/20260908/eq34-audit.md) binds actual inventory/capture/prefetch APIs and test-only checks.

Historical thermal summary hashes:45 references match,20 golden checks match; two large bpftime libraries retained as DEFERRED within the directed hash budget. No historical GPU compatibility/3D-ICE/long heat workload was rerun. CL0 4s whole-window useful service91.7504MB/s differs from offered100MB/s; BW0 whole-window useful75.104256MB/s differs from old tail-window physical75.105616609MB/s. Old per-plane event0.01J gives64.5W only in the controlled toy case. ROM τ8Hi=.283248410s,16Hi=1.021309821s are numerical properties of specific hashed ROMs, not arbitrary geometry/device measurements.

Phase3 contains offline retention demand/energy and energy-conserving24h HBM envelope; neither resolves executed MQSim refresh or true transient command timing. The50.021us→10ms mismatch remains a transient blocker. Historical write-heavy negative/mixed results remain valid limitations.

Old B16 routing binds a frozen BF16 inventory in `results/batches/20260907-routing-continuation/composition/B16-group0/metrics/inventory.json`, not the current same-named F16/GGUF inventory under `results/manifests/`. Do not retarget old member/hash bindings. HF route-horizon has single-member and fixed context/budget assumptions; arbitraryB/r/kappa/beta are not existing CLI axes.

## External references and paper entry

The requested `HBFSim_EQ1-EQ4_汇报说明(1).md` was not located in the targeted workspace search; no contents inferred. `docs/49-new-evaluation-plan.md`, `docs/49-eval-audit/*`, `docs/eval/*` and README are the actual maintained entries. No private paper submodule manuscript was changed; future manuscript claims must follow current gates.

[Current official source ledger](review-evidence/20260908/source-research.md) covers R1–R19, access dates and frozen versions. OCP v0.7.0 actual130-page specification is accessible in browser, but local PDF download403 leaves byte hash unarchived. Accel-Sim2 release full Hopper support does not establish SM120; paper/release discrepancies retained. Latest documents are not assumed compatible with frozenCUDA13.0/PTX9.0.

## Current deliverables and exact limits

[Matrix](run-matrix.csv) contains300 planning rows:EQ1=114/EQ2=26/EQ3=132/EQ4=28. Only two EQ1 rows summarize this round's CPU/arithmetic checks; other CPU checks are in audit receipts. Lifecycle requires fresh bindings; three historical thermal rows are READY_NOT_RUN with exact argv and profile hashes. Main-claim rows remain gated. Estimated confirmation count1994 is a conditional planning lower bound after gates, not executed runs or an approved campaign. Optional/diagnostic/invalid cells are not automatically launched.

Old2848-row/20485-run matrix is preserved byte-for-byte under `history/20260905/`; old statuses/registries/condition hashes cannot be reused against the new matrix. The legacy generator now requires an explicit fresh output path; historical resource/count assertions remain against the old snapshot, and current resource/review-registration checks apply to the new plan. These are documentation-tool compatibility changes; runtime code and ABI remain unchanged.
