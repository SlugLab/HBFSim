# 2026-09-08 migration and EQ replan execution checklist

User-approved design: `HBFSim_Codex_迁移与EQ重规划_2026-09-08.md`, adopted explicitly in this task. This checklist implements that design; it does not authorize the long campaign or runtime/ABI integration.

Baseline: `eval/eq1-eq4-implementation@254d65a66279fbaffc5c185d04fbe41dc8dbba44`. Source is `/root/hbfsim-exp`; destination `/mnt/disk0/hbfsim-exp`; logical compatibility prefix is retained. Existing untracked figures/results, history and paused controllers must be preserved.

- [x] Locate live checkout and canonical documents; save original document bytes under `history/20260905/` and pre-existing Git state under workspace `reports/eq-replan-20260908/`.
- [x] Inventory all source files, sparse/hardlinks/external links, repos/environments and writers; verify PM1733a UUID and capacity budget.
- [x] Probe destination ACL/xattr/hardlink/sparse support; bounded copy pilot; first resumable copy without deletion or symlink dereference.
- [x] Freeze this task's writers, final incremental sync, checksum dry-run, independent provenance SHA256 and metadata checks.
- [x] Quarantine source, install compatibility link, verify environment/Git/model metadata/legacy entrypoints; remove only the verified migration source copy when writer and external-hardlink conditions permit. Record actual freed space.
- [x] Install task-local mount/low-water/cache guard and rollback runbook. Preserve PAUSED_BY_USER experiment state.
- [x] Audit current scalar future and historical donor TMA, thermal artifacts and official sources in independent bounded tasks.
- [x] Correct incremental-stall oracle; execute bounded CPU mechanism/contract checks and preserve negative evidence.
- [x] Update canonical EQ plan, execution, figures, matrix, claim gates, schema and indices; distinguish actual entrypoints from missing implementations.
- [x] Verify new matrix, local links and document diff against attachment requirements; deliver Chinese conclusion and remaining gates.

No driver, mount configuration, global environment, GPU clocks/power limits, model downloads, branch merges, remote push or full matrix launch are required. Any actual device change or new implementation gap remains a separate task; independent audits continue.

Final receipts: [Chinese delivery](review-evidence/20260908/交付结论.md), [migration manifest](review-evidence/20260908/migration-manifest.json), [document validation](review-evidence/20260908/document-validation.json). Migration source cleanup actually freed141639696384bytes; independent final requirements review found no additional substantive gap. Full experimental campaign remains unexecuted as required.
