# Controlled campaign analysis

`analyze_controlled_campaign.py` consumes completed `run_controlled.py` point
directories. By default it requires the frozen 39-point design:

- 2 topologies × 3 model sizes × 2 demand patterns × 3 policies at 16 scans/s;
- 3 additional all-HBF, 235B, continuous points at 32 scans/s.

Run it after the campaign is complete:

```sh
python3 -B experiments/eq3_rate_thermal/analyze_controlled_campaign.py \
  --campaign CAMPAIGN_DIR --output NEW_DERIVED_DIR
```

`--allow-partial` is intended for diagnostic or fixed-fixture analysis and does
not label an incomplete campaign complete.

When `CAMPAIGN_DIR/RUN_INDEX.json` exists, point discovery uses only its 39
entries with `phase=main`. Every registered output must have a complete DONE
point contract, a unique path, and matching topology/model/pattern/strategy,
scan rate, duration, offered-byte total, and profile/workload/scenario hashes.
Pilot entries, stage DONE receipts and derived diagnostic links are excluded.
Without a RUN_INDEX, fake fixtures and isolated pilot bundles use recursive
discovery only for directories containing all eight required point files; a
standalone stage receipt therefore cannot become a point.

The analysis rereads aligned `rates.jsonl`, `control.jsonl`, `energy.jsonl` and
`thermal.jsonl` streams. It validates per-point byte conservation, 50 pJ/B
served-energy accounting, component/window energy sums, the final thermal
energy receipt, and the byte-weighted delay histograms recorded by the runner.
It reports total and per-stack offered/delivered/backlog bytes, total-duration
and active-window delivery rates, peak/final temperatures, state residence
times, P95/P99 fluid delay, and incremental energy.

Service stability uses two preregistered window sets: every complete 20 ms
window in the full active interval, and every complete 20 ms window in the
fixed second half of that active interval (10--20 s for the full campaign).
For total service and each stack it reports population CV, nearest-rank
P5/P50/P95, and the fraction of zero-service windows. These metrics expose both
stop/start delivery and steady but lower service; they add no PASS threshold
and no windows are selected from observed outcomes.

Policy comparisons are paired within topology, model, pattern and scan rate.
Every delta is `right - left`; no sign is named a benefit. The separate
cross-topology table records:

- 4-stack 16 scans/s versus 8-stack 16 scans/s as equal total offered demand;
- 4-stack 16 scans/s versus 8-stack 32 scans/s as equal offered bytes per stack.

Nonzero backlog is a saturation result, not an execution failure. Backend
latency, token/s and maintenance remain unavailable because this campaign uses
the modelled fluid FIFO rather than MQSim or fabric completion. Energy is only
the user-confirmed incremental 50 pJ/B scenario; idle and GPU self-power remain
unknown in this path.

Outputs include a machine-readable JSON report, point and comparison CSVs, one
maximum-temperature/rate/backlog trajectory plot per workload group, one
three-policy per-stack-temperature panel figure per workload group, and a
campaign summary figure. Rate trajectories show offered demand as a dashed
line beside delivered service, and all trajectory panels mark the fixed active
cutoff. The complete 39-point design therefore produces 13 grouped per-stack
figures rather than 39 separate large figures. All figures are derived from the
same checked in analysis and immutable point streams.
