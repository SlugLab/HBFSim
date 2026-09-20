# D4 sustained-load control cost report

## Result and evidence boundary

The nine D4 runs are a deterministic **ENGINEERING_FIXTURE** comparison of
three policies at 5, 10, and 25 requests/s per stack.  Every arm uses the same
`mixed_direct` package, the same four HBM4 plus four HBF stack identities, the
same initial state and maintenance rules, 20 s of arrivals, and 10 s of
recovery.  Each rate has one run per arm, so there is no confidence interval.
Temperatures are simulated by the fixture and are neither a calibrated product
prediction nor a P2/P5 acceptance result.

The result is negative for a simple “control improves the system” claim.  Both
control policies reduce the recorded peak temperature, but they also complete
less foreground work.  At 25 requests/s per stack they leave 1,500 of 4,000
requests unfinished and raise completed-request p95 latency from 0.04 s to
10.34 s.  The lower energy in controlled arms also accompanies less completed
work, so it is not an efficiency result.

`none` means no thermal policy. `hyst` is the retained hysteresis policy and
`esc-v2` is the new, default-off `hysteresis_escalation_priority_v2` policy.
All foreground failures were zero and all foreground inflight counts at the
30 s horizon were zero; “unfinished” therefore means queued at the horizon.

## Foreground, temperature, and latency

`backend wait` is `start-arrival` for completed foreground requests. `backend
service` is `end-start`. `end-to-end` is `end-arrival`. These are completed-only
statistics; unfinished wait is reported separately rather than censored into
the latency distribution. `external wait` is the fixture's declared value and
is 0 s in every arm; this D4 fixture has no separate bounded external admission
queue.

| rate/stack (rps) | policy | offered | complete | unfinished | completed bytes | peak K (node) | peak foreground queue | end-to-end p95 s | backend wait p95 s | backend service p95/max s | external wait s | unfinished wait max/sum s |
|---:|---|---:|---:|---:|---:|---|---:|---:|---:|---:|---:|---:|
| 5 | none | 800 | 800 | 0 | 3,276,800 | 300.673743 (`hbf1_die7`) | 4 | 0.020 | 0.000 | 0.020 / 0.020 | 0.000 | 0.000 / 0.000 |
| 5 | hyst | 800 | 672 | 128 | 2,752,512 | 300.635724 (`hbf1_die8`) | 128 | 0.040 | 0.020 | 0.020 / 0.020 | 0.000 | 16.400 / 1,702.400 |
| 5 | esc-v2 | 800 | 672 | 128 | 2,752,512 | 300.635724 (`hbf1_die8`) | 128 | 0.040 | 0.020 | 0.020 / 0.020 | 0.000 | 16.400 / 1,702.400 |
| 10 | none | 1,600 | 1,600 | 0 | 6,553,600 | 300.932365 (`hbf1_die6`) | 4 | 0.040 | 0.020 | 0.020 / 0.020 | 0.000 | 0.000 / 0.000 |
| 10 | hyst | 1,600 | 1,264 | 336 | 5,177,344 | 300.683902 (`hbf1_die7`) | 336 | 0.020 | 0.000 | 0.020 / 0.020 | 0.000 | 18.400 / 4,788.000 |
| 10 | esc-v2 | 1,600 | 1,264 | 336 | 5,177,344 | 300.683902 (`hbf1_die7`) | 336 | 0.020 | 0.000 | 0.020 / 0.020 | 0.000 | 18.400 / 4,788.000 |
| 25 | none | 4,000 | 4,000 | 0 | 16,384,000 | 301.592464 (`hbf2_die7`) | 4 | 0.040 | 0.020 | 0.020 / 0.020 | 0.000 | 0.000 / 0.000 |
| 25 | hyst | 4,000 | 2,500 | 1,500 | 10,240,000 | 300.741831 (`hbf1_die7`) | 2,000 | 10.340 | 10.320 | 0.020 / 0.020 | 0.000 | 23.440 / 24,183.360 |
| 25 | esc-v2 | 4,000 | 2,500 | 1,500 | 10,240,000 | 300.741831 (`hbf1_die7`) | 2,000 | 10.340 | 10.320 | 0.020 / 0.020 | 0.000 | 23.440 / 24,183.360 |

The apparently lower p95 latency for the controlled 10-rps arms does not mean
better service: p95 is calculated only over the 1,264 completed requests, while
336 requests remain queued.  The backlog is the required counterweight to that
completed-only statistic.

## Maintenance age, backlog, and energy

Maintenance counts below are operations, while `overdue cohorts` counts die
cohorts. `max overdue` is age beyond the per-kind fixture period. Package energy
is the sum of the recorded package component dynamic energies. External energy
is recorded separately and is zero for this mixed-direct case.

| rate/stack | policy | maintenance done | failed | queued | inflight | overdue cohorts | max age s | max overdue s | peak maintenance queue | package dynamic J | external J |
|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 5 | none | 3,148 | 0 | 4 | 0 | 4 | 2.020 | 0.020 | 64 | 114.978000 | 0.000000 |
| 5 | hyst | 3,148 | 0 | 0 | 4 | 4 | 2.040 | 0.040 | 64 | 113.703600 | 0.000000 |
| 5 | esc-v2 | 3,148 | 0 | 0 | 4 | 4 | 2.040 | 0.040 | 64 | 113.703600 | 0.000000 |
| 10 | none | 3,128 | 0 | 0 | 0 | 0 | 1.980 | 0.000 | 64 | 123.001200 | 0.000000 |
| 10 | hyst | 3,132 | 0 | 4 | 0 | 4 | 2.020 | 0.020 | 64 | 119.482000 | 0.000000 |
| 10 | esc-v2 | 3,132 | 0 | 4 | 0 | 4 | 2.020 | 0.020 | 64 | 119.482000 | 0.000000 |
| 25 | none | 3,084 | 0 | 4 | 0 | 4 | 2.020 | 0.020 | 64 | 147.240400 | 0.000000 |
| 25 | hyst | 3,112 | 0 | 0 | 0 | 0 | 1.980 | 0.000 | 64 | 132.126800 | 0.000000 |
| 25 | esc-v2 | 3,112 | 0 | 0 | 0 | 0 | 1.980 | 0.000 | 64 | 132.126800 | 0.000000 |

Maintenance backlog is not monotone with foreground cost. At 10 rps the
controlled arms have four queued maintenance operations and four overdue
cohorts while `none` has neither. At 25 rps the controlled arms clear the
maintenance backlog but leave 1,500 foreground requests queued. Neither case
supports describing the controlled arm as an overall benefit.

## Same offered distribution, different HBM/HBF service

Every rate assigns the same number of requests to each of the eight stacks.
The per-stack result below is uniform within each four-stack kind. The different
completion counts therefore come from fixture media/maintenance/control
semantics, rather than an unequal offered distribution. HBF has 16 dies and
program/read/erase maintenance, while HBM4 has 12 dies and 5 ms fixture refresh;
control is applied per stack from its simulated hotspot. These are engineering
choices, not calibrated HBM or NAND timing claims.

| rate/stack | policy | each HBM4 arrivals / complete / queued / inflight | each HBF arrivals / complete / queued / inflight |
|---:|---|---|---|
| 5 | none | 100 / 100 / 0 / 0 | 100 / 100 / 0 / 0 |
| 5 | hyst | 100 / 100 / 0 / 0 | 100 / 68 / 32 / 0 |
| 5 | esc-v2 | 100 / 100 / 0 / 0 | 100 / 68 / 32 / 0 |
| 10 | none | 200 / 200 / 0 / 0 | 200 / 200 / 0 / 0 |
| 10 | hyst | 200 / 200 / 0 / 0 | 200 / 116 / 84 / 0 |
| 10 | esc-v2 | 200 / 200 / 0 / 0 | 200 / 116 / 84 / 0 |
| 25 | none | 500 / 500 / 0 / 0 | 500 / 500 / 0 / 0 |
| 25 | hyst | 500 / 461 / 39 / 0 | 500 / 164 / 336 / 0 |
| 25 | esc-v2 | 500 / 461 / 39 / 0 | 500 / 164 / 336 / 0 |

## Why the old and new policies are identical here

For every rate, `hyst` and `esc-v2` have identical control event times,
completed request IDs, temperatures, service, maintenance, and energy. The
event audit records 16 identical transitions at 5 rps and 12 at both 10 and
25 rps. The v2 policy changes the case where a more severe sampled action would
otherwise wait behind recovery dwell; these trajectories do not expose that
case. In the only recovery followed by another escalation (5 rps), the HBF
stacks change Severe→Light at 12.48 s and Light→Severe at 13.42 s, a 0.94 s
gap versus the configured 0.10 s dwell. The old dwell was already satisfied,
so escalation priority cannot change the outcome. Equality here verifies an
unexercised policy distinction, not proof that the two algorithms are
equivalent.

## UNKNOWN and unavailable fields

- Token throughput is `UNAVAILABLE`; this CPU fixture does not model tokens.
- External GDDR temperature is `UNAVAILABLE_OUTSIDE_PACKAGE`; this topology has
  no package GDDR node. That does not imply a physical external device has zero
  temperature or board-level thermal effect.
- A separate measured external-admission wait distribution is `UNKNOWN`. The
  fixture reports only the configured scalar `external_wait_s = 0` and keeps
  backlog in its internal foreground queue.
- Real MQSim/NAND command-stage latency, calibrated HBM timing, measured device
  energy, Ea/ECC/endurance behavior, and uncertainty across repetitions are
  `UNKNOWN` for this matrix.

## Provenance and retained historical results

The aggregate values come from
`eq3_thermal/plans/decision-execution-v2/D4_MATRIX_RESULT.json`; per-stack counts
come from `D4_PER_STACK_SERVICE.json`; control equality comes from
`D4_POLICY_EVENT_DIAGNOSTIC.json`; completed-only latency, queue, maintenance,
and energy details come from the nine immutable point `review.json` files, with
service duration and peak node read directly from their corresponding
`stdout.log` raw summaries. This report only reads those completed JSON files;
it launches no pilot or solver.

The prior six-point engineering results remain unchanged and retained in
`docs/eq3_thermal/P2_P3_P4_CAMPAIGN_RESULT.md`. Their inputs differ from this
nine-point sustained-load matrix, so they are historical evidence rather than
a substitute baseline and are not overwritten or reinterpreted here.
