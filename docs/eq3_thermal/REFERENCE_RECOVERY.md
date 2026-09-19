# Bounded historical reference recovery — P2 continuation

Status **PARTIAL_RECOVERY** for provenance, **HISTORICAL_NOT_ACQUIRED** for
historical numerical inputs/outputs. New work is explicitly **NEW_REFERENCE**.

Search bounded to the current workspace, known donor trees and tracked historical
receipt paths; no full-disk/credential search, old path aliases or remote backup
access. Active inspection was under the 30-minute ceiling. No further recovery
search is a prerequisite for the new CPU reference.

| Artifact | Current finding | Evidence / implication |
| --- | --- | --- |
| Source | Acquired stock 3D-ICE e0bb6850c5e446363e26936586d625270c87f224 | Frozen source and rebuilt private reference environment |
| Donor patches | Located in fd11c3d98cde74977be7ec504c64429369b6fd3a | Allocation and bottom-boundary patches are different; neither imported |
| Original geometry/floorplans | HISTORICAL_NOT_ACQUIRED | No donor .flp/.stk; current .flp files are newly acquired upstream fixtures |
| Original trace/golden | HISTORICAL_NOT_ACQUIRED | Expected golden-8hi/16hi-v9 manifests and held-mixed inputs absent |
| Original ROM/fit | HISTORICAL_NOT_ACQUIRED | Tracked receipt lists hashes and paths, not underlying matrices |
| Sensor/time mapping | PARTIAL_RECOVERY | Donor code available; original full input bindings absent; old timestamp shifting not inherited |
| New reference inputs | NEW_REFERENCE | Geometry/material generator and held-out traces independently named |

Tracked `docs/49-eval-audit/review-evidence/20260908/thermal-golden-check.json`
and `thermal-rom-eigencheck.json` contain useful historical hashes, including
224-state ROMs and 10 ms samples. Their referenced old `results/phase2-rom-*`
and `phase2-runtime-inputs-*` locations do not exist in the present workspace.
Those historical PASS/eigenvalue values are not new reproduction evidence.
Both baseline and donor .gitattributes have only patch whitespace handling;
no LFS artifact declaration was found there. GitHub release API access via the
web tool was unavailable; release assets/backups remain NOT_ACQUIRED, not
asserted nonexistent. No accessible backup location was supplied.

The rebuilt 3D-ICE binary SHA256 is
`240b598c6c8fe1f19db2d945c34596db4a14df7f589bc2f205d74104927244a7`,
not historical `48d736c7...`. Existing upstream 400-cell smoke establishes
readiness only. See workspace `docs/codex/EQ3_REFERENCE_BUILD.md` and
`environments/eq3-thermal-reference-v1` for reproducible source/build locks.
Exact historical replay remains unsupported; do not reuse old calibration errors.
