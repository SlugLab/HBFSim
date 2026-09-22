# Rebuttal experiment backup snapshot — 2026-09-22

This branch is a source and documentation backup taken before the automatic
weight-selection/instrumentation feature was continued.  It is based on
`eabc5c2c0820ac0d84c2f16ea3460b219f11ff83`.

The tracked source changes and selected `docs/rebuttal_20260921` and
`experiments/rebuttal_20260921` files reflect the current rebuttal worktree.
Build trees, model weights, raw experiment results, credentials, caches, and
generated binaries are intentionally excluded.

`provenance/rebuttal_20260922/patches` preserves exact patches for the verified
atomic sidecar and compact first-fault source snapshots.  The latter contains
the accepted runtime-domain forwarding and joint accounting implementation.
`provenance/rebuttal_20260922/r3-scalar-abi-source-v2` preserves the independent
R3 scalar-ABI source files.  Receipts and artifact checksums are copied without
claiming that binaries are stored in Git.

R3 status at this snapshot is **in progress**.  Earlier READY/NOT-RUN reports
are historical preparation records and do not supersede the current matrix.
No complete R3 page-size matrix result is claimed by this backup.

The automatic-weight feature is intentionally absent from this baseline.  Its
current unvalidated three-file WIP is preserved on the separate
`backup/auto-weights-wip-20260922` branch.

