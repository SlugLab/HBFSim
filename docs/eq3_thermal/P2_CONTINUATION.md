# P2 continuation checkpoint — 2026-09-19

USER_CONFIRMED: execute the P2 continuation taskbook; preserve P1; formal
experiments and GPU compute require explicit version/hash-bound user approval.

DOC_DERIVED: continuation HEAD 5d5e9602b41b17bd398a29a54dc3cb88b705d22f,
development branch eq3-thermal/p1. The source worktree was clean before this
continuation. Immutable baseline 5eb789d5f1a42f0c040ee6fb5a2cdb5ffa0951d5
remains in its separate detached worktree. No reinitialization or reset.
Previously reported baseline tests: full 32/34, supported subset 32/32; two
legacy absolute-path prerequisites remain missing. P1: 13 core / 8 config
tests and four-topology mode checks; temperatures are uncalibrated fixtures.

USER_CONFIRMED: host interactive nvidia-smi sees RTX 5090. Earlier agent failure
does not establish absent host hardware. Current diagnosis status:
GPU_ACCESS_DISCREPANCY_UNRESOLVED, pending contextual receipts.

Shared engineering budget: at most 12 independent CPU numerical configurations,
one execution each, <=1 CPU-hour, <=4 aggregate build threads, <=8 GiB RAM,
<=20 GiB additional disk; OMP/BLAS=1. GPU compute budget ZERO.
Fixed unit tests, source acquisition and read-only diagnosis are permitted.
No scientific experiment is ACCEPTED. No new push is authorized by this task.
