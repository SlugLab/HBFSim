# Migration compatibility, rollback and space runbook

> 分享副本：审计事实、数值和原执行路径保持原记录；仅调整导航。大模型、完整 raw、部分本机清单与可执行二进制未随仓库发布，范围见 [审阅包说明](README.md)。

Owner: this task only. Logical prefix `/root/hbfsim-exp`; physical `/mnt/disk0/hbfsim-exp`; temporary quarantine `/root/hbfsim-exp.migration-source-20260908`. Never substitute a wildcard or a different directory. See migration-manifest.json for whether quarantine still exists.

## Launch and low-water guard

Use the task-local `migration_launch.py` with a stack fingerprint derived from source/build/environment/profile hashes. Example read-only check:

```
python3 /root/hbfsim-exp/reports/eq-replan-20260908/migration_launch.py --stack current-254d65a-cuda130-env20260908 --check
```

Real experiments require their separate reviewed registry/gate receipts and resource guard. A subsequent explicit command may be passed after `--`; this does not authorize resuming the existing paused controllers. The launcher verifies resolved compatibility prefix, expected PM1733a source/UUID/rw, and free space >=128GiB reserve +64GiB bounded output (use a validated per-task output estimate). The output estimate is an admission budget, not an enforced filesystem quota; the reviewed cell runner must enforce output/runtime limits. It creates fingerprint-isolated TMPDIR/HF_HOME/XDG_CACHE_HOME/TRITON_CACHE_DIR/CUDA_CACHE_PATH/TORCH_EXTENSIONS_DIR/NUMBA_CACHE_DIR/PIP_CACHE_DIR under the target `.task-runtime`. No global shell/profile/Conda/CUDA changes.

While its child runs, mount and reserve are rechecked every60s. On loss/low water it signals only its own new child session for checkpoint/shutdown; it escalates termination only if that owned session does not exit. The existing experiment runner must preserve partial results and record failure. No new processes start automatically. Pilot growth determines each run's output cap; retain2×growth error before extending. Keep CPU/GPU timing separate from copy/compile/storage stress. Old stack and prototype must use different fingerprints.

## Rollback before source-copy cleanup

1. Verify expected target UUID, source quarantine dev/inode and manifest stage. Stop only this task's writers normally and retain latest target changes. Do not reset/clean Git or overwrite the quarantine.
2. Verify `/root/hbfsim-exp` is exactly the symlink to `/mnt/disk0/hbfsim-exp`; remove that single link, then rename exact quarantine back to `/root/hbfsim-exp`. If restoring the link/rename fails, stop and preserve both trees.
3. Rerun before/after Python/CUDA/Git/model metadata smoke. Keep destination for diagnosis. New post-cutover files require a separately reviewed manifest-based reverse sync; never blanket --delete.

After verified source cleanup, the old full rollback tree no longer exists. The complete checksum-verified destination remains authoritative; restoring to root is a new reverse migration requiring sufficient space and the same checks, not a rename. Original disk backup directories outside this task are never deleted or implicitly trusted as complete replacements.

## Cleanup admission

Only after full quiescent checksum dry-run, path/metadata/sparse/internal-hardlink and77 independent original key hashes pass, plus compatibility smoke/no unknown writers, may exact source quarantine be removed. Retain its verified per-file manifest, Git snapshots/patches and receipts. Do not follow symlinks, cross mounts or remove external hardlink targets. If any unknown owner/write/hash discrepancy appears, preserve quarantine and mark cleanup BLOCKED. Record df before/after and actual bytes released; hardlinks outside the tree mean source du overestimates reclaimable space.
