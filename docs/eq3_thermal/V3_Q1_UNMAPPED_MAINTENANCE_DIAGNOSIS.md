# V3 Q1 unmapped-maintenance diagnosis

Date: 2026-09-20
Scope: read-only review of completed Q1 points in
`eq3_thermal/plans/isolated-maintenance-campaign-v1/campaign-ocp4k-v3`.
No runtime, input, solver, or raw-result file was changed and no experiment was
started for this diagnosis.

## Finding and classification

The 26 failed maintenance jobs in every Q1 Safe and Near arm are
`DOMAIN_FAILURE` at the experiment-input/backend-initialization boundary, not a
confirmed backend implementation bug and not a deadline miss.  The maintenance
bundle declares 64 aged pages as eligible sources at 199,980,000 ns, but this
MQSim instance only creates the source mapping when a foreground read first
touches a page.  At the due instant, only 38 of the 64 target pages had completed
that first touch.  The other 26 correctly returned terminal
`REJECTED_UNMAPPED` without issuing a native transaction or changing a mapping.

This is also an `INITIAL_STATE_CAPABILITY_LIMITATION`: `initial_age_s` expresses
retention intent but does not initialize a resident MQSim mapping.  The current
experiment therefore does not yet represent the intended state “model weights
already resident at time zero” for every scheduled maintenance target.

## Actual completed-point evidence

All three arms within a scene use identical request and maintenance inputs and
have identical maintenance outcomes.

| Q1 scene | request-input SHA-256 prefix | mapped by due | committed | terminal failure |
|---|---:|---:|---:|---:|
| Safe, P0/P1/P2 | `ef1a5307fd60` | 38/64 | 38 | 26 `REJECTED_UNMAPPED` |
| Near, P0/P1/P2 | `272b5dbfcf28` | 38/64 | 38 | 26 `REJECTED_UNMAPPED` |
| Stress-thermal, P0/P1/P2 | `83ea34204e9d` | 64/64 | 64 | 0 |

All nine points use the same maintenance input
SHA-256 `c508f7404d2e74d843828f3fdc2cd55db63c46413bdd5cbb5233fcfc4612977d`.
It schedules local pages 0 through 15 on each of `hbf0..hbf3`, with due time
199,980,000 ns and deadline 219,980,000 ns.

For Safe and Near, the 38 mappings present at the due instant are:

- `hbf0`: local pages 0--9;
- `hbf1`: local pages 0--9;
- `hbf2`: local pages 0--8;
- `hbf3`: local pages 0--8.

The failed jobs are IDs 1000038--1000063: `hbf2:9`, `hbf3:9`, `hbf0:10`,
then the remaining stripe order through `hbf3:15`.  Every failed completion has
the same fail-closed facts: `end_ns == due_ns`, zero native transaction IDs,
`source_version == 0`, `mapping_committed == false`, and no age reset.  The
completion field `deadline_met == true` merely records that the immediate
rejection occurred before the deadline; it must not be counted as a successful
deadline-satisfying maintenance operation.

All 26 rejected sources become mapped later in the same Safe/Near execution,
after a real foreground read.  Their first backend completions span
200,010,310--340,010,310 ns.  Three (`hbf2:9`, `hbf3:9`, `hbf0:10`) map after
the due time but before the 219,980,000 ns deadline; the other 23 map after the
deadline.  The completed foreground records and the isolated backend contract,
which creates metadata for a populated page on first read, support this final
mapping conclusion.  No mapping was inferred from an address alone.

Representative immutable raw evidence is:

- Safe P0 `result.json`, SHA-256
  `bb59b9ef6b0bf7211436b5289d0a2e1f2c049e4cb7cc481ac2deac4a2ec5e1d2`;
- each point's `maintenance.csv`, `summary.json`, `result.json`, and `DONE.json`
  under its `OCP4K-W1-Q1-<scene>-P<arm>` directory.

## Code-path evidence

The isolated maintenance unit calls `Begin_hbf_maintenance` before allocating
or submitting a read/program pair.  A missing mapping returns
`REJECTED_UNMAPPED` immediately
(`experiments/eq3_maintenance/backend/patches/0004-eq3-maintenance.patch`,
the `HBF_Maintenance_Unit::Submit` hunk).  This is the intended fail-closed
behavior: maintenance cannot preserve or relocate an unknown source.

`ClosedLoopRunner._submit_due_maintenance` submits a due row once.  A backend
terminal rejection sets its state to `FAILED`; only `NOT_DUE`, `DEFERRED`, and
`THERMAL_WAIT` rows are considered on later iterations
(`experiments/eq3_maintenance/closed_loop.py`).  Consequently, retry after a
later first touch is not currently supported by the campaign coordinator.  The
backend could accept a newly identified maintenance request after the page is
mapped, but this campaign neither creates such a request nor preserves a retry
lifecycle for the failed ID.

## Minimal safe correction choices

1. **Smallest campaign-only correction:** construct each maintenance bundle
   only from pages proven mapped before its due time.  This preserves the
   backend and its fail-closed invariant.  It changes target count/comparability,
   so it requires a new input version and affected-point reruns; it cannot be
   applied retrospectively to v3.
2. **Closest match to the intended resident-weight initial state:** add an
   explicit pre-observation mapping initialization/import capability.  It must
   establish mappings without pretending that host writes or refresh commands
   occurred and without charging fabricated traffic, energy, or wear.  This
   changes backend initialization semantics and requires a reviewed interface
   proposal before implementation.
3. **Retry alternative:** retry a terminal unmapped job only after an observed
   foreground completion maps that exact page, retaining the original due,
   deadline, and age evidence.  The current coordinator has no such lifecycle;
   adding it changes maintenance scheduling semantics and requires approval.
   In this evidence, 23 of 26 sources only map after the original deadline, so
   retry would not turn them into on-time successes.

Silently issuing host writes to preload the pages is not a valid repair: it
would add program traffic, energy, wear, and timing that the read-only weight
workload did not request.  A rejected page must not receive an age reset.

## Interpretation boundary

The Q1 Safe/Near foreground and thermal results remain completed observations,
but their maintenance-success counts are conditioned on lazy first-touch
mapping.  They are not valid evidence that a resident 7B weight population can
refresh only 38 of these 64 pages.  Stress demonstrates that the same backend
path commits all 64 when all targets are mapped before due; it does not by
itself validate the missing resident-at-start initialization model.
