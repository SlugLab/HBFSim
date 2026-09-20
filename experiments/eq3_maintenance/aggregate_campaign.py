#!/usr/bin/env python3
"""Read-only aggregation for the isolated EQ3 maintenance campaign."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
from pathlib import Path
from typing import Any

from analyze_points import (_boolean, _cell, _integer, _json, _number, _quantile,
                            _rows, analyze_point)


SCHEMA = "eq3-isolated-campaign-aggregate-v1"
POLICY_INPUTS = {"policy-profile.json"}


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def pair_identity(manifest: dict[str, Any]) -> tuple[str | None, dict[str, Any]]:
    hashes = manifest.get("input_sha256")
    if not isinstance(hashes, dict):
        return None, {}
    selected = {key: value for key, value in hashes.items() if key not in POLICY_INPUTS}
    identity = {
        "source_head": manifest.get("source_head"),
        "mode": manifest.get("mode"), "workload": manifest.get("workload"),
        "active_ns": manifest.get("active_ns"), "end_ns": manifest.get("end_ns"),
        "weight_model": manifest.get("weight_model"),
        "thermal_model_dir": manifest.get("thermal_model_dir"),
        "executables": manifest.get("executable_sha256"), "inputs": selected,
    }
    return hashlib.sha256(_canonical(identity).encode()).hexdigest(), identity


def scenario_key(spec: dict[str, Any]) -> str:
    value = {key: spec.get(key) for key in ("workload", "mode", "scene")}
    value["config"] = spec.get("config")
    return hashlib.sha256(_canonical(value).encode()).hexdigest()[:16]


def _latencies(point: Path) -> list[int]:
    values = []
    for row in _rows(point / "requests.csv"):
        if str(row.get("phase", "")).upper() == "FINAL_COMPLETE":
            value = _integer(row.get("end_to_end_latency_ns"))
            if value is not None:
                values.append(value)
    return values


def _energy(point: Path) -> tuple[float | None, float | None, float | None]:
    rows = _rows(point / "energy-activity.csv")
    values = [(_number(row.get("energy_j")), row.get("component")) for row in rows]
    known = [(value, component) for value, component in values if value is not None]
    if not known:
        return None, None, None
    total = sum(value for value, _ in known)
    gpu = sum(value for value, component in known if component == "gpu")
    return total, gpu, (gpu / total if total else None)


def _native_coverage(point: Path) -> dict[str, list[int]] | None:
    rows = _rows(point / "native.csv")
    if not rows:
        return None
    coverage: dict[str, set[int]] = {}
    for row in rows:
        transactions = _cell(row.get("transactions"))
        if not isinstance(transactions, list):
            continue
        for tx in transactions:
            if isinstance(tx, dict) and str(tx.get("stack", "")).startswith("hbf"):
                die = _integer(tx.get("die"))
                if die is not None:
                    coverage.setdefault(str(tx["stack"]), set()).add(die)
    return {stack: sorted(dies) for stack, dies in sorted(coverage.items())}


def _thermal_transitions(point: Path, active_ns: int) -> dict[str, Any]:
    first = {state: None for state in ("light", "severe", "shutdown")}
    recovery = {state: None for state in ("severe", "light", "normal")}
    peak = {kind: None for kind in ("gpu", "hbf", "hbm")}
    representative: list[dict[str, Any]] = []
    for row in _rows(point / "thermal.csv"):
        end = _integer(row.get("end_ns"))
        states = _cell(row.get("stack_states")); temperatures = _cell(row.get("temperatures"))
        if end is None:
            continue
        if isinstance(states, dict):
            observed = set(states.values())
            for state in first:
                if first[state] is None and state in observed:
                    first[state] = end
            if end >= active_ns:
                for state in recovery:
                    if recovery[state] is None and state in observed:
                        recovery[state] = end
        grouped = {"gpu": [], "hbf": [], "hbm": []}
        if isinstance(temperatures, dict):
            for key, value in temperatures.items():
                number = _number(value)
                if number is None:
                    continue
                kind = "gpu" if key == "gpu" else "hbf" if str(key).startswith("hbf") else "hbm"
                grouped[kind].append(number)
            for kind, values in grouped.items():
                if values:
                    maximum = max(values)
                    peak[kind] = maximum if peak[kind] is None else max(peak[kind], maximum)
            representative.append({"end_ns": end,
                                   "gpu_k": max(grouped["gpu"], default=None),
                                   "hbf_k": max(grouped["hbf"], default=None),
                                   "hbm_k": max(grouped["hbm"], default=None)})
    return {"first": first, "recovery": recovery, "peak": peak, "series": representative}


def _resource_conservation(point: Path) -> bool | None:
    rows = _rows(point / "resources.csv")
    if not rows:
        return None
    fabric = _cell(rows[-1].get("fabric"))
    if not isinstance(fabric, dict) or not isinstance(fabric.get("unfinished"), list):
        return None
    return len(fabric["unfinished"]) == 0


def _control_stats(point: Path) -> dict[str, Any]:
    actions: dict[str, int] = {}; outcomes: dict[str, int] = {}
    first_stop = first_increase = None; zero_budget_windows = 0
    for row in _rows(point / "control.csv"):
        start = _integer(row.get("applies_to_window_start_ns"))
        decisions = _cell(row.get("stack_decisions"))
        if not isinstance(decisions, list):
            continue
        any_zero = False
        for decision in decisions:
            if not isinstance(decision, dict): continue
            action = str(decision.get("action", "UNKNOWN")); outcome = str(decision.get("outcome", "UNKNOWN"))
            actions[action] = actions.get(action, 0) + 1
            outcomes[outcome] = outcomes.get(outcome, 0) + 1
            any_zero = any_zero or _integer(decision.get("budget_bytes")) == 0
            if action == "STOP" and first_stop is None: first_stop = start
            if action == "INCREASE" and first_increase is None: first_increase = start
        zero_budget_windows += int(any_zero)
    return {"actions": actions, "outcomes": outcomes, "first_stop_ns": first_stop,
            "first_increase_ns": first_increase, "zero_budget_windows": zero_budget_windows}


def derive_completed(point: Path, spec: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    derived = analyze_point(point)
    manifest = _json(point / "manifest.json"); done = _json(point / "DONE.json")
    summary = _json(point / "summary.json")
    profile = _json(point / "profile.json")
    active_ns = _integer(manifest.get("active_ns"))
    if active_ns is None:
        raise ValueError(f"{point}: active_ns unavailable")
    active = [row for row in derived["time_series"] if row["start_ns"] < active_ns]
    recovery = [row for row in derived["time_series"] if row["start_ns"] >= active_ns]
    def total(rows, key):
        values = [row.get(key) for row in rows]
        return None if any(value is None for value in values) else sum(values)
    def boundary(rows, key):
        return rows[-1].get(key) if rows else None
    latencies = _latencies(point)
    energy_total, gpu_energy, gpu_fraction = _energy(point)
    transitions = _thermal_transitions(point, active_ns)
    control = _control_stats(point)
    identity_hash, identity = pair_identity(manifest)
    rates = [row["delivered_bytes_per_s"] for row in active
             if row["delivered_bytes_per_s"] is not None]
    mean = sum(rates) / len(rates) if rates else None
    stdev = (math.sqrt(sum((value-mean)**2 for value in rates) / len(rates))
             if rates and mean is not None else None)
    conservation = _resource_conservation(point)
    mismatch = derived["cross_checks"]["rates_vs_request_delivery_mismatch_windows"]
    censored = derived["explicit_censored_request_count"]
    functional = ("FUNCTIONAL_FAIL" if mismatch or conservation is False else
                  "FUNCTIONAL_PASS_WITH_CENSOR" if censored else "FUNCTIONAL_PASS")
    numerical = done.get("numerical_status")
    capability = done.get("capability_status")
    scientific = ("SCIENTIFIC_ACCEPTED" if numerical == "ACCEPTED" and capability == "ACCEPTED"
                  else "CONDITIONAL_NOT_MODEL_PASS")
    row = {
        "point_id": spec["id"], "scenario_key": scenario_key(spec),
        "workload": spec.get("workload"), "mode": spec.get("mode"),
        "scene": spec.get("scene"), "policy": spec.get("policy"),
        "weight_model": (spec.get("config") or {}).get("weight_model"),
        "source_head": manifest.get("source_head"), "page_bytes": profile.get("page_bytes"),
        "active_s": active_ns / 1e9,
        "observation_s": derived["scope"]["observation_end_ns"] / 1e9,
        "execution_state": done.get("execution_status", "UNKNOWN"),
        "functional_state": functional, "scientific_state": scientific,
        "capability_status": capability, "numerical_status": numerical,
        "pair_identity_sha256": identity_hash,
        "hbf_active_effective_delivered_bytes": total(active, "effective_delivered_hbf_bytes"),
        "hbm_active_effective_delivered_bytes": total(active, "effective_delivered_hbm_bytes"),
        "hbf_observation_effective_delivered_bytes": total(derived["time_series"], "effective_delivered_hbf_bytes"),
        "hbm_observation_effective_delivered_bytes": total(derived["time_series"], "effective_delivered_hbm_bytes"),
        "active_rate_p05_bytes_per_s": _quantile(rates, .05),
        "active_rate_mean_bytes_per_s": mean,
        "active_rate_cv": (stdev / mean if stdev is not None and mean else None),
        "latency_p95_ns": _quantile(latencies, .95), "latency_p99_ns": _quantile(latencies, .99),
        "latency_sample_count": len(latencies),
        "active_end_backlog_effective_bytes": boundary(active, "effective_backlog_bytes"),
        "observation_end_backlog_effective_bytes": boundary(derived["time_series"], "effective_backlog_bytes"),
        "censored_requests": censored,
        "first_light_ns": transitions["first"]["light"],
        "first_severe_ns": transitions["first"]["severe"],
        "first_shutdown_ns": transitions["first"]["shutdown"],
        "recovery_normal_ns": transitions["recovery"]["normal"],
        "recovery_severe_ns": transitions["recovery"]["severe"],
        "recovery_light_ns": transitions["recovery"]["light"],
        "control_first_stop_ns": control["first_stop_ns"],
        "control_first_increase_ns": control["first_increase_ns"],
        "control_zero_budget_windows": control["zero_budget_windows"],
        "control_action_counts": _canonical(control["actions"]),
        "control_outcome_counts": _canonical(control["outcomes"]),
        "peak_gpu_k": transitions["peak"]["gpu"], "peak_hbf_k": transitions["peak"]["hbf"],
        "peak_hbm_k": transitions["peak"]["hbm"],
        "maintenance_due": derived["maintenance"].get("due_count"),
        "maintenance_committed": derived["maintenance"].get("committed_count"),
        "maintenance_failed": derived["maintenance"].get("failed_count"),
        "maintenance_cleanup_failed": derived["maintenance"].get("cleanup_failed_after_commit_count"),
        "maintenance_declared_page_fraction": derived["maintenance"].get("declared_model_page_fraction"),
        "total_activity_energy_j": energy_total, "gpu_external_energy_j": gpu_energy,
        "gpu_external_energy_fraction": gpu_fraction,
        "energy_relative_residual_max": max((r["cumulative_energy_relative_residual"]
                                             for r in derived["time_series"]
                                             if r["cumulative_energy_relative_residual"] is not None), default=None),
        "resource_conservation": conservation,
        "hbf_native_die_coverage": _canonical(_native_coverage(point)),
        "q4_limit": ("EXTERNAL_GDDR_SERVICE_AND_PACKAGE_TEMPERATURE_UNAVAILABLE"
                     if spec.get("mode") == "all_hbf_direct" else None),
        "pair_status": "PENDING_GROUP_REVIEW",
    }
    representative = {"point_id": spec["id"], "mode": spec.get("mode"),
                      "active_ns": active_ns, "rates": derived["time_series"],
                      "thermal": transitions["series"], "identity": identity}
    return row, representative


def pending_row(spec: dict[str, Any], state: str, *, reason: Any = None,
                launcher: dict[str, Any] | None = None) -> dict[str, Any]:
    return {"point_id": spec["id"], "scenario_key": scenario_key(spec),
            "workload": spec.get("workload"), "mode": spec.get("mode"),
            "scene": spec.get("scene"), "policy": spec.get("policy"),
            "weight_model": (spec.get("config") or {}).get("weight_model"),
            "execution_state": state, "functional_state": "NOT_EVALUATED",
            "scientific_state": "NOT_EVALUATED", "pair_status": "INCOMPLETE",
            "not_started_reason": _canonical(reason) if reason is not None else None,
            "launcher_status": (launcher or {}).get("status"),
            "launcher_exit_code": (launcher or {}).get("exit_code")}


def aggregate(queue: dict[str, Any], points_root: Path,
              launcher_status: dict[str, Any] | None = None) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    rows, representatives = [], []
    launcher_by_id = {row.get("id"): row for row in (launcher_status or {}).get("points", [])}
    for spec in queue.get("points", []):
        point = points_root / spec["id"]
        if (point / "DONE.json").exists():
            row, representative = derive_completed(point, spec)
            launcher = launcher_by_id.get(spec["id"], {})
            row.update(launcher_status=launcher.get("status"),
                       launcher_exit_code=launcher.get("exit_code"), not_started_reason=None)
            rows.append(row); representatives.append(representative)
        elif (point / "NOT_STARTED.json").exists():
            receipt = _json(point / "NOT_STARTED.json")
            rows.append(pending_row(spec, "NOT_STARTED_SUPERSEDED_V1",
                                    reason=receipt.get("reason"),
                                    launcher=launcher_by_id.get(spec["id"])))
        elif (point / "FAILED.json").exists():
            rows.append(pending_row(spec, "FAILED", launcher=launcher_by_id.get(spec["id"])))
        else:
            rows.append(pending_row(spec, "PENDING_SUPERSEDED_V1",
                                    reason="V1_PROFILE_REVISED_BEFORE_START",
                                    launcher=launcher_by_id.get(spec["id"])))
    groups: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        groups.setdefault(row["scenario_key"], []).append(row)
    for group in groups.values():
        completed = [row for row in group if row["execution_state"] == "COMPLETED"]
        identities = {row.get("pair_identity_sha256") for row in completed}
        status = ("PAIR_READY" if len(completed) == 3 and len(identities) == 1 and None not in identities
                  else "PAIR_IDENTITY_MISMATCH" if len(completed) >= 2 and len(identities) > 1
                  else "INCOMPLETE")
        for row in group:
            row["pair_status"] = status
    return rows, representatives


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    keys = list(dict.fromkeys(key for row in rows for key in row))
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=keys); writer.writeheader()
        for row in rows:
            writer.writerow({key: "UNKNOWN" if value is None else value for key, value in row.items()})


def plot_overview(rows: list[dict[str, Any]], path: Path) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    completed = [row for row in rows if row["execution_state"] == "COMPLETED"]
    fig, axes = plt.subplots(3, 1, figsize=(12, 8), sharex=True, constrained_layout=True)
    if not completed:
        axes[1].text(.5, .5, "No completed points", ha="center", va="center")
    else:
        x = list(range(len(completed)))
        color = {"guard_only": "#777777", "thermal_hysteresis_guard": "#0072B2",
                 "read_rate_feedback_thermal_guard_v1": "#D55E00"}
        c = [color.get(row["policy"], "black") for row in completed]
        axes[0].scatter(x, [(row["hbf_active_effective_delivered_bytes"] or 0)/row["active_s"]/1e9
                            for row in completed], c=c, s=18)
        axes[0].set_ylabel("HBF active effective GB/s")
        axes[1].scatter(x, [row["peak_gpu_k"] for row in completed], c=c, s=18, label="GPU")
        axes[1].scatter(x, [row["peak_hbf_k"] for row in completed], c=c, marker="x", s=18,
                        label="HBF max")
        axes[1].set_ylabel("Peak K"); axes[1].legend(frameon=False, ncol=2)
        axes[2].scatter(x, [row["censored_requests"] for row in completed], c=c, s=18)
        axes[2].set_ylabel("Censored requests"); axes[2].set_xlabel("Completed point index (see main-table.csv)")
    for axis in axes: axis.grid(alpha=.25)
    fig.suptitle("EQ3 campaign aggregate — execution success is not scientific acceptance")
    fig.savefig(path, dpi=170); plt.close(fig)


def plot_representatives(representatives: list[dict[str, Any]], path: Path) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    selected = []
    for mode in ("mixed_direct", "relay", "dash", "all_hbf_direct"):
        candidate = next((row for row in representatives if row["mode"] == mode), None)
        if candidate: selected.append(candidate)
    fig, axes = plt.subplots(max(1, len(selected)), 1, figsize=(10, 2.6*max(1, len(selected))),
                             squeeze=False, constrained_layout=True)
    if not selected:
        axes[0][0].text(.5, .5, "No completed representative", ha="center", va="center")
    for axis, item in zip((row[0] for row in axes), selected):
        x = [(row["start_ns"]+row["end_ns"])/2e9 for row in item["rates"]]
        y = [(row["effective_delivered_hbf_bytes_per_s"] or 0)/1e9 for row in item["rates"]]
        axis.plot(x, y, color="#0072B2", label="HBF effective GB/s")
        temp = axis.twinx(); temp.plot([row["end_ns"]/1e9 for row in item["thermal"]],
                                      [row["gpu_k"] for row in item["thermal"]],
                                      color="#D55E00", alpha=.75, label="GPU K")
        axis.axvline(item["active_ns"]/1e9, color="black", linestyle=":", linewidth=.8)
        axis.set_title(f"{item['mode']}: {item['point_id']}"); axis.set_ylabel("GB/s"); temp.set_ylabel("K")
        axis.grid(alpha=.2)
    axes[-1][0].set_xlabel("Simulation time (s)")
    fig.savefig(path, dpi=170); plt.close(fig)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--queue", type=Path, required=True)
    parser.add_argument("--points-root", type=Path, required=True)
    parser.add_argument("--status", type=Path,
                        help="launcher status; NOT_STARTED receipts still take precedence")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.output.exists() and any(args.output.iterdir()):
        parser.error("output must be absent or empty")
    args.output.mkdir(parents=True, exist_ok=True)
    queue = _json(args.queue)
    launcher_status = _json(args.status) if args.status else None
    rows, representatives = aggregate(queue, args.points_root, launcher_status)
    lock_ref = queue.get("lock")
    lock_path = Path(lock_ref) if isinstance(lock_ref, str) else None
    if lock_path is not None and not lock_path.is_absolute():
        lock_path = args.queue.parent / lock_path
    lock_sha256 = (hashlib.sha256(lock_path.read_bytes()).hexdigest()
                   if lock_path is not None and lock_path.is_file() else None)
    for row in rows:
        row["campaign_lock"] = lock_ref
        row["campaign_lock_sha256"] = lock_sha256
    write_csv(args.output / "main-table.csv", rows)
    (args.output / "aggregate.json").write_text(json.dumps({
        "schema_version": SCHEMA, "point_count": len(rows),
        "campaign_lock": lock_ref, "campaign_lock_sha256": lock_sha256,
        "launcher_status_source": str(args.status.resolve()) if args.status else None,
        "completed_count": sum(row["execution_state"] == "COMPLETED" for row in rows),
        "rows": rows,
        "status_semantics": {"execution": "process/output completion",
                             "functional": "raw consistency/resource conservation; censor retained",
                             "scientific": "explicit acceptance only; exit zero is insufficient"},
    }, indent=2, sort_keys=True) + "\n")
    plot_overview(rows, args.output / "campaign-overview.png")
    plot_representatives(representatives, args.output / "representative-timeseries.png")
    (args.output / "DONE.json").write_text(json.dumps({
        "execution_status": "COMPLETED", "raw_modified": False,
        "completed_points": sum(row["execution_state"] == "COMPLETED" for row in rows),
    }, indent=2) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
