#!/usr/bin/env python3
"""Derive compact closed-loop diagnostics from immutable point CSV outputs.

This module is deliberately a postprocessor: it neither imports the simulator
clients nor advances either backend.  JSON null is used for unavailable facts;
an observed zero remains zero.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
import statistics
import sys
from pathlib import Path
from typing import Any, Iterable


SCHEMA_VERSION = "eq3-closed-loop-analysis-v1"


def _json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path}: expected JSON object")
    return value


def _rows(path: Path) -> list[dict[str, str]]:
    if not path.exists() or path.stat().st_size <= 2:
        return []
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def _cell(value: Any) -> Any:
    if value is None or value == "" or value == "UNKNOWN":
        return None
    if not isinstance(value, str):
        return value
    try:
        return json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return value


def _number(value: Any) -> float | None:
    value = _cell(value)
    if isinstance(value, bool) or value is None:
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _integer(value: Any) -> int | None:
    number = _number(value)
    if number is None or not number.is_integer():
        return None
    return int(number)


def _boolean(value: Any) -> bool | None:
    value = _cell(value)
    if isinstance(value, bool):
        return value
    if value in (1, "1", "true", "True"):
        return True
    if value in (0, "0", "false", "False"):
        return False
    return None


def _sum_known(values: Iterable[float | int | None]) -> float | None:
    materialized = list(values)
    if not materialized or any(v is None for v in materialized):
        return None
    return float(sum(materialized))


def _quantile(values: Iterable[float | int], probability: float) -> float | None:
    ordered = sorted(float(v) for v in values if math.isfinite(float(v)))
    if not ordered:
        return None
    # Nearest-rank empirical quantile.  The definition is recorded in output.
    rank = max(1, math.ceil(probability * len(ordered)))
    return ordered[rank - 1]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _window_index(windows: list[dict[str, Any]], timestamp_ns: int) -> int | None:
    # Fixed windows use [start,end); a completion exactly at end belongs to the
    # next window.  The small point counts make this explicit scan sufficient.
    for index, window in enumerate(windows):
        if window["start_ns"] <= timestamp_ns < window["end_ns"]:
            return index
    return None


def _maintenance_summary(rows: list[dict[str, str]], manifest: dict[str, Any],
                         observation_end_ns: int, model_page_count: int | None) -> dict[str, Any]:
    declared = _integer(manifest.get("maintenance_count"))
    if not rows:
        observed_zero = declared == 0
        return {
            "declared_count": declared,
            "due_count": 0 if observed_zero else None,
            "committed_count": 0 if observed_zero else None,
            "failed_count": 0 if observed_zero else None,
            "backlog_at_observation_end": 0 if observed_zero else None,
            "initial_age_max_s": None,
            "declared_page_count": 0 if observed_zero else None,
            "declared_model_page_fraction": 0.0 if observed_zero and model_page_count else None,
            "declared_age_at_observation_max_s": None,
            "age_reset_count": 0 if observed_zero else None,
            "semantics": "OBSERVED_DISABLED" if observed_zero else "UNKNOWN_NO_ROWS",
        }

    latest: dict[str, dict[str, str]] = {}
    for index, row in enumerate(rows):
        request_id = row.get("request_id") or row.get("maintenance_id") or f"row-{index}"
        latest[str(request_id)] = row
    values = list(latest.values())
    due = [r for r in values if (_integer(r.get("due_ns")) is not None and
                                  _integer(r.get("due_ns")) <= observation_end_ns)]
    def completion(row: dict[str, str]) -> dict[str, Any]:
        nested = _cell(row.get("completion"))
        return nested if isinstance(nested, dict) else {}
    def field(row: dict[str, str], name: str) -> Any:
        direct = _cell(row.get(name))
        return direct if direct is not None else completion(row).get(name)
    committed = [r for r in values if _boolean(field(r, "mapping_committed")) is True]
    failed = [r for r in values if _boolean(field(r, "mapping_committed")) is False and
              "FAIL" in str(field(r, "status") or r.get("state") or "").upper()]
    # erase_completed=false is also the normal safe RECLAIM_DEFERRED state.
    # Only the explicit backend failure terminal (or coordinator state derived
    # from it) proves a cleanup failure.  Ignore legacy cleanup_failed booleans
    # that were derived solely from erase_completed.
    cleanup_failed = [r for r in committed
                      if str(field(r, "status") or r.get("backend_status") or
                             r.get("state") or "").upper()
                      in {"FAILED_AFTER_COMMIT_NEEDS_RECONCILE",
                          "COMMITTED_CLEANUP_FAILED"}]
    initial_ages = [_number(r.get("initial_age_s")) for r in values]
    reset_count = sum(1 for r in committed if _integer(field(r, "age_reset_ns")) is not None)
    committed_by_observation = [r for r in committed
                                if (_integer(field(r, "age_reset_ns")) is not None and
                                    _integer(field(r, "age_reset_ns")) <= observation_end_ns)]
    page_keys = {(r.get("stack"), _integer(r.get("stack_local_page"))) for r in values
                 if r.get("stack") and _integer(r.get("stack_local_page")) is not None}
    ages_at_observation = []
    for row in values:
        initial_age = _number(row.get("initial_age_s"))
        if initial_age is None:
            continue
        reset = _integer(field(row, "age_reset_ns"))
        mapping = _boolean(field(row, "mapping_committed")) is True
        if mapping and reset is not None and reset <= observation_end_ns:
            ages_at_observation.append((observation_end_ns-reset) / 1e9)
        else:
            ages_at_observation.append(initial_age + observation_end_ns / 1e9)
    return {
        "declared_count": declared,
        "due_count": len(due),
        "committed_count": len(committed),
        "failed_count": len(failed),
        "cleanup_failed_after_commit_count": len(cleanup_failed),
        "backlog_at_observation_end": max(0, len(due) - len(committed_by_observation)),
        "initial_age_max_s": max((v for v in initial_ages if v is not None), default=None),
        "declared_page_count": len(page_keys),
        "declared_model_page_fraction": (len(page_keys) / model_page_count
                                         if model_page_count else None),
        "declared_age_at_observation_max_s": max(ages_at_observation, default=None),
        "age_reset_count": reset_count,
        "semantics": ("RAW_LATEST_ROW_PER_REQUEST; age clears only on mapping_committed=true "
                      "with observed age_reset_ns; undeclared pages are UNKNOWN"),
    }


def analyze_point(point: Path) -> dict[str, Any]:
    point = point.resolve()
    manifest = _json(point / "manifest.json")
    done = _json(point / "DONE.json")
    rate_rows = _rows(point / "rates.csv")
    request_rows = _rows(point / "requests.csv")
    thermal_rows = _rows(point / "thermal.csv")
    control_rows = _rows(point / "control.csv")
    maintenance_rows = _rows(point / "maintenance.csv")
    model_page_count = None
    extent_path = point / "weight-model-extent.json"
    if extent_path.exists():
        model_page_count = _integer(_json(extent_path).get("global_page_count"))
    if not rate_rows:
        raise ValueError(f"{point}: rates.csv has no fixed windows")

    windows: list[dict[str, Any]] = []
    rate_facts: dict[tuple[int, str], dict[str, Any]] = {}
    for row in rate_rows:
        start = _integer(row.get("start_ns")); end = _integer(row.get("end_ns"))
        stacks = _cell(row.get("stacks"))
        if start is None or end is None or end <= start or not isinstance(stacks, list):
            raise ValueError(f"{point}/rates.csv: malformed fixed window")
        index = len(windows)
        windows.append({"start_ns": start, "end_ns": end})
        for fact in stacks:
            if not isinstance(fact, dict) or not fact.get("stack_id"):
                raise ValueError(f"{point}/rates.csv: malformed stack fact")
            rate_facts[(index, str(fact["stack_id"]))] = fact

    observation_end = windows[-1]["end_ns"]
    stacks = sorted({stack for _, stack in rate_facts})

    arrivals: dict[str, dict[str, str]] = {}
    completions: dict[str, dict[str, str]] = {}
    censored: dict[str, dict[str, str]] = {}
    for row in request_rows:
        request_id = row.get("request_id")
        if not request_id:
            continue
        phase = str(row.get("phase") or "").upper()
        if phase == "ARRIVAL":
            arrivals.setdefault(request_id, row)
        elif phase == "FINAL_COMPLETE":
            completions[request_id] = row
        elif phase == "CENSORED":
            censored[request_id] = row

    request_counts: dict[str, dict[str, int]] = {
        stack: {"arrived": 0, "final_delivered": 0, "censored": 0} for stack in stacks
    }
    request_bytes: dict[str, dict[str, int]] = {
        stack: {"arrived_effective": 0, "arrived_physical": 0,
                "final_delivered_effective": 0, "final_delivered_physical": 0,
                "censored_effective": 0, "censored_physical": 0} for stack in stacks
    }
    completion_samples: dict[tuple[int, str], list[float]] = {}
    derived_delivered: dict[tuple[int, str], int] = {}
    for request_id, row in arrivals.items():
        stack = str(row.get("stack") or "UNKNOWN")
        if stack not in request_counts:
            request_counts[stack] = {"arrived": 0, "final_delivered": 0, "censored": 0}
            request_bytes[stack] = {"arrived_effective": 0, "arrived_physical": 0,
                                    "final_delivered_effective": 0, "final_delivered_physical": 0,
                                    "censored_effective": 0, "censored_physical": 0}
        size = _integer(row.get("bytes")); valid = _integer(row.get("valid_weight_bytes"))
        valid = size if valid is None else valid
        request_counts[stack]["arrived"] += 1
        if size is not None:
            request_bytes[stack]["arrived_physical"] += size
        if valid is not None:
            request_bytes[stack]["arrived_effective"] += valid
    for request_id, row in completions.items():
        stack = str(row.get("stack") or arrivals.get(request_id, {}).get("stack") or "UNKNOWN")
        size = _integer(row.get("bytes")); valid = _integer(row.get("valid_weight_bytes"))
        valid = size if valid is None else valid
        request_counts.setdefault(stack, {"arrived": 0, "final_delivered": 0, "censored": 0})
        request_bytes.setdefault(stack, {"arrived_effective": 0, "arrived_physical": 0,
                                         "final_delivered_effective": 0, "final_delivered_physical": 0,
                                         "censored_effective": 0, "censored_physical": 0})
        request_counts[stack]["final_delivered"] += 1
        if size is not None:
            request_bytes[stack]["final_delivered_physical"] += size
        if valid is not None:
            request_bytes[stack]["final_delivered_effective"] += valid
        timestamp = _integer(row.get("final_completion_ns"))
        latency = _number(row.get("end_to_end_latency_ns"))
        if timestamp is not None:
            index = _window_index(windows, timestamp)
            if index is not None:
                if valid is not None:
                    derived_delivered[(index, stack)] = derived_delivered.get((index, stack), 0) + valid
                if latency is not None:
                    completion_samples.setdefault((index, stack), []).append(latency)
    for request_id, row in censored.items():
        stack = str(row.get("stack") or arrivals.get(request_id, {}).get("stack") or "UNKNOWN")
        size = _integer(row.get("bytes")); valid = _integer(row.get("valid_weight_bytes"))
        valid = size if valid is None else valid
        request_counts.setdefault(stack, {"arrived": 0, "final_delivered": 0, "censored": 0})
        request_bytes.setdefault(stack, {"arrived_effective": 0, "arrived_physical": 0,
                                         "final_delivered_effective": 0, "final_delivered_physical": 0,
                                         "censored_effective": 0, "censored_physical": 0})
        request_counts[stack]["censored"] += 1
        if size is not None:
            request_bytes[stack]["censored_physical"] += size
        if valid is not None:
            request_bytes[stack]["censored_effective"] += valid

    control_by_start: dict[int, dict[str, Any]] = {}
    for row in control_rows:
        start = _integer(row.get("applies_to_window_start_ns"))
        if start is not None:
            control_by_start[start] = row
    thermal_by_end: dict[int, dict[str, Any]] = {}
    for row in thermal_rows:
        end = _integer(row.get("end_ns"))
        if end is not None:
            thermal_by_end[end] = row

    time_series: list[dict[str, Any]] = []
    mismatch_windows = 0
    for index, window in enumerate(windows):
        facts = [rate_facts[(index, stack)] for stack in stacks if (index, stack) in rate_facts]
        duration_s = (window["end_ns"] - window["start_ns"]) / 1e9
        offered = _sum_known(_integer(f.get("offered_bytes")) for f in facts)
        delivered = _sum_known(_integer(f.get("delivered_bytes")) for f in facts)
        backlog = _sum_known(_integer(f.get("backlog_bytes")) for f in facts)
        physical_offered = _sum_known(_integer(f.get("physical_offered_bytes", f.get("offered_bytes")))
                                      for f in facts)
        physical_delivered = _sum_known(_integer(f.get("physical_delivered_bytes",
                                                       f.get("delivered_bytes"))) for f in facts)
        physical_backlog = _sum_known(_integer(f.get("physical_backlog_bytes", f.get("backlog_bytes")))
                                      for f in facts)
        delivered_hbf = _sum_known(_integer(f.get("delivered_bytes")) for f in facts
                                   if str(f.get("stack_id", "")).startswith("hbf"))
        delivered_hbm = _sum_known(_integer(f.get("delivered_bytes")) for f in facts
                                   if str(f.get("stack_id", "")).startswith("hbm"))
        incomplete = _sum_known(_integer(f.get("censored_requests")) for f in facts)
        raw_sample_count = sum(len(completion_samples.get((index, stack), [])) for stack in stacks)
        raw_samples = [v for stack in stacks for v in completion_samples.get((index, stack), [])]
        derived_bytes = sum(derived_delivered.get((index, stack), 0) for stack in stacks)
        if delivered is not None and int(delivered) != derived_bytes:
            mismatch_windows += 1

        control = control_by_start.get(window["start_ns"])
        decisions = _cell(control.get("stack_decisions")) if control else None
        if isinstance(decisions, list):
            budgets = [_integer(d.get("budget_bytes")) for d in decisions if isinstance(d, dict)]
            permit = _sum_known(budgets)
            actions = sorted({str(d.get("action")) for d in decisions if isinstance(d, dict)})
        else:
            permit = None; actions = []

        thermal = thermal_by_end.get(window["end_ns"])
        temperatures = _cell(thermal.get("temperatures")) if thermal else None
        groups: dict[str, float | None] = {"gpu": None, "hbf_max": None, "hbm_max": None}
        if isinstance(temperatures, dict):
            groups["gpu"] = _number(temperatures.get("gpu"))
            hbf = [_number(v) for k, v in temperatures.items() if str(k).startswith("hbf")]
            hbm = [_number(v) for k, v in temperatures.items() if str(k).startswith("hbm")]
            groups["hbf_max"] = max((v for v in hbf if v is not None), default=None)
            groups["hbm_max"] = max((v for v in hbm if v is not None), default=None)
        relative_residual = None
        cumulative_energy = None
        energy = _cell(thermal.get("energy_j")) if thermal else None
        if isinstance(energy, dict) and isinstance(energy.get("cumulative"), dict):
            cumulative = energy["cumulative"]
            residual = _number(cumulative.get("energy_residual_j"))
            total_input = _number(cumulative.get("total_input_j"))
            cumulative_energy = total_input
            if residual is not None and total_input not in (None, 0):
                relative_residual = abs(residual) / abs(total_input)

        time_series.append({
            **window,
            "offered_bytes": None if offered is None else int(offered),
            "delivered_bytes": None if delivered is None else int(delivered),
            "effective_offered_bytes": None if offered is None else int(offered),
            "effective_delivered_bytes": None if delivered is None else int(delivered),
            "physical_offered_bytes": None if physical_offered is None else int(physical_offered),
            "physical_delivered_bytes": None if physical_delivered is None else int(physical_delivered),
            "offered_bytes_per_s": None if offered is None else offered / duration_s,
            "delivered_bytes_per_s": None if delivered is None else delivered / duration_s,
            "effective_delivered_hbf_bytes": None if delivered_hbf is None else int(delivered_hbf),
            "effective_delivered_hbm_bytes": None if delivered_hbm is None else int(delivered_hbm),
            "effective_delivered_hbf_bytes_per_s": (None if delivered_hbf is None
                                                     else delivered_hbf / duration_s),
            "effective_delivered_hbm_bytes_per_s": (None if delivered_hbm is None
                                                     else delivered_hbm / duration_s),
            "backlog_bytes": None if backlog is None else int(backlog),
            "effective_backlog_bytes": None if backlog is None else int(backlog),
            "physical_backlog_bytes": None if physical_backlog is None else int(physical_backlog),
            "incomplete_or_censored_requests": None if incomplete is None else int(incomplete),
            "tail_latency_p95_ns": _quantile(raw_samples, 0.95),
            "tail_latency_sample_count": raw_sample_count,
            "control_permit_bytes": None if permit is None else int(permit),
            "control_actions": actions or None,
            "temperatures_k": groups,
            "cumulative_energy_input_j": cumulative_energy,
            "cumulative_energy_relative_residual": relative_residual,
        })

    active_ns = _integer(manifest.get("active_ns"))
    active_rates = [row["delivered_bytes_per_s"] for row in time_series
                    if row["delivered_bytes_per_s"] is not None and
                    (active_ns is None or row["start_ns"] < active_ns)]
    variability = {
        "scope": "FIXED_WINDOWS_INTERSECTING_ACTIVE_INTERVAL",
        "window_count": len(active_rates),
        "delivered_rate_p05_bytes_per_s": _quantile(active_rates, 0.05),
        "delivered_rate_mean_bytes_per_s": statistics.fmean(active_rates) if active_rates else None,
        "delivered_rate_stdev_bytes_per_s": statistics.pstdev(active_rates) if active_rates else None,
        "quantile_definition": "EMPIRICAL_NEAREST_RANK",
    }
    if variability["delivered_rate_mean_bytes_per_s"] not in (None, 0):
        variability["delivered_rate_cv"] = (
            variability["delivered_rate_stdev_bytes_per_s"] /
            variability["delivered_rate_mean_bytes_per_s"])
    else:
        variability["delivered_rate_cv"] = None

    required = ["manifest.json", "DONE.json", "requests.csv", "rates.csv", "thermal.csv",
                "control.csv", "energy.csv", "maintenance.csv"]
    provenance = {name: _sha256(point / name) for name in required if (point / name).exists()}
    return {
        "schema_version": SCHEMA_VERSION,
        "point_id": point.name,
        "source_point": str(point),
        "execution_status": done.get("execution_status"),
        "scope": {
            "workload": manifest.get("workload"),
            "policy": manifest.get("policy"),
            "active_ns": active_ns,
            "observation_end_ns": observation_end,
            "interpretation": "CONDITIONAL_ENGINEERING_PILOT",
            "host_write_workload": "NOHOSTWRITE",
        },
        "requests_by_stack": {
            stack: {"counts": request_counts.get(stack), "bytes": request_bytes.get(stack)}
            for stack in sorted(request_counts)
        },
        "fixed_window_incomplete_at_end": time_series[-1]["incomplete_or_censored_requests"],
        "explicit_censored_request_count": len(censored),
        "rate_variability": variability,
        "delivered_totals_by_backend_kind": {
            kind: {
                "effective_bytes": sum(value["final_delivered_effective"]
                                       for stack, value in request_bytes.items()
                                       if stack.startswith(kind.lower())),
                "physical_bytes": sum(value["final_delivered_physical"]
                                      for stack, value in request_bytes.items()
                                      if stack.startswith(kind.lower())),
            } for kind in ("HBF", "HBM")
        },
        "maintenance": _maintenance_summary(maintenance_rows, manifest, observation_end,
                                             model_page_count),
        "cross_checks": {
            "rates_vs_request_delivery_mismatch_windows": mismatch_windows,
            "window_count": len(windows),
            "temperature_row_count": len(thermal_rows),
            "control_row_count": len(control_rows),
        },
        "time_series": time_series,
        "provenance_sha256": provenance,
    }


def plot_point(result: dict[str, Any], path: Path) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    rows = result["time_series"]
    x = [(r["start_ns"] + r["end_ns"]) / 2e9 for r in rows]
    active_s = result["scope"]["active_ns"] / 1e9 if result["scope"]["active_ns"] is not None else None

    def series(key: str, scale: float = 1.0) -> list[float]:
        values = []
        for row in rows:
            value: Any = row
            for part in key.split("."):
                value = value.get(part) if isinstance(value, dict) else None
            values.append(math.nan if value is None else float(value) / scale)
        return values

    plt.rcParams.update({"font.size": 8.5, "axes.grid": True, "grid.alpha": 0.22,
                         "axes.spines.top": False, "axes.spines.right": False})
    fig, axes = plt.subplots(5, 1, figsize=(8.4, 9.4), sharex=True, constrained_layout=True)
    fig.suptitle(f"EQ3 closed-loop raw diagnostics — {result['point_id']}", fontsize=12)

    axes[0].plot(x, series("offered_bytes_per_s", 1e9), label="effective offered", color="#777777")
    axes[0].plot(x, series("effective_delivered_hbf_bytes_per_s", 1e9),
                 label="HBF effective delivered", color="#0072B2")
    axes[0].plot(x, series("effective_delivered_hbm_bytes_per_s", 1e9),
                 label="HBM effective delivered", color="#E69F00")
    axes[0].set_ylabel("GB/s")
    axes[0].legend(ncol=3, frameon=False, loc="upper right")

    axes[1].plot(x, series("backlog_bytes", 2**20), color="#D55E00", label="backlog")
    axes[1].set_ylabel("Backlog MiB")
    queue2 = axes[1].twinx()
    queue2.plot(x, series("incomplete_or_censored_requests"), color="#CC79A7", linestyle="--",
                label="incomplete/censored")
    queue2.set_ylabel("Requests")

    axes[2].plot(x, series("temperatures_k.gpu"), label="GPU", color="#D55E00")
    axes[2].plot(x, series("temperatures_k.hbf_max"), label="HBF max", color="#009E73")
    axes[2].plot(x, series("temperatures_k.hbm_max"), label="HBM max", color="#56B4E9")
    axes[2].set_ylabel("Temperature K")
    axes[2].legend(ncol=3, frameon=False, loc="upper left")

    axes[3].step(x, series("control_permit_bytes", 2**20), where="mid", color="#0072B2")
    axes[3].set_ylabel("Permit MiB/window")
    maintenance = result["maintenance"]
    display = lambda value: "UNKNOWN" if value is None else str(value)
    axes[3].text(0.995, 0.08,
                 f"maint due={display(maintenance['due_count'])}  "
                 f"commit={display(maintenance['committed_count'])}  "
                 f"declared age max={display(maintenance['initial_age_max_s'])}",
                 ha="right", va="bottom", transform=axes[3].transAxes, fontsize=7.5)

    residual = series("cumulative_energy_relative_residual")
    positive = [v if math.isnan(v) or v > 0 else math.nan for v in residual]
    axes[4].plot(x, positive, color="#009E73")
    axes[4].set_yscale("log")
    axes[4].set_ylabel("|energy residual|\n/ input")
    axes[4].set_xlabel("Simulation time (s)")

    if active_s is not None:
        for axis in axes:
            axis.axvline(active_s, color="black", linewidth=0.8, linestyle=":")
        axes[0].text(active_s, 1.01, "active end", transform=axes[0].get_xaxis_transform(),
                     ha="center", va="bottom", fontsize=7)
    fig.savefig(path, dpi=180)
    plt.close(fig)


def _markdown(results: list[dict[str, Any]]) -> str:
    lines = [
        "# EQ3 closed-loop point diagnostics",
        "",
        "This is a raw-derived engineering diagnostic. It does not advance MQSim or the thermal solver.",
        "Missing fields remain `UNKNOWN`; observed zero values remain zero.",
        "",
        "| Point | Workload / policy | active / observed | arrived / delivered / censored | HBF / HBM effective delivered | active rate p05 / mean | tail samples | peak GPU / HBF / HBM K | energy residual max |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for result in results:
        rows = result["time_series"]
        req = result["requests_by_stack"].values()
        arrived = sum(v["counts"]["arrived"] for v in req)
        delivered = sum(v["counts"]["final_delivered"] for v in req)
        censored = result["explicit_censored_request_count"]
        variability = result["rate_variability"]
        kinds = result["delivered_totals_by_backend_kind"]
        tail_samples = sum(r["tail_latency_sample_count"] for r in rows)
        def maximum(key: str) -> float | None:
            values = []
            for row in rows:
                value: Any = row
                for part in key.split("."):
                    value = value.get(part) if isinstance(value, dict) else None
                if value is not None:
                    values.append(float(value))
            return max(values) if values else None
        fmt = lambda v, scale=1.0: "UNKNOWN" if v is None else f"{v/scale:.6g}"
        lines.append(
            f"| {result['point_id']} | {result['scope']['workload']} / {result['scope']['policy']} | "
            f"{fmt(result['scope']['active_ns'],1e9)} / {fmt(result['scope']['observation_end_ns'],1e9)} s | "
            f"{arrived} / {delivered} / {censored} | "
            f"{kinds['HBF']['effective_bytes']} / {kinds['HBM']['effective_bytes']} B | "
            f"{fmt(variability['delivered_rate_p05_bytes_per_s'],1e9)} / "
            f"{fmt(variability['delivered_rate_mean_bytes_per_s'],1e9)} GB/s | {tail_samples} | "
            f"{fmt(maximum('temperatures_k.gpu'))} / {fmt(maximum('temperatures_k.hbf_max'))} / "
            f"{fmt(maximum('temperatures_k.hbm_max'))} | "
            f"{fmt(maximum('cumulative_energy_relative_residual'))} |")
    lines += [
        "",
        "## Interpretation boundary",
        "",
        "- `NOHOSTWRITE`: the current W1 input is read-only. It cannot support a host-write, write-amplification, or write-maintenance claim.",
        "- Pilot02 contains 0.4 s of arrivals and a 0.2 s recovery interval. It is a functional closed-loop check, not a main research result.",
        "- Rate p05 uses effective `valid_weight_bytes` (defaulting to physical bytes) and the empirical nearest-rank quantile across fixed windows intersecting the active interval. Tail latency reports its raw completion sample count.",
        "- HBF and HBM final-delivery totals remain separate. Their sum is a combined delivered-byte diagnostic and is not labeled HBF goodput.",
        "- Temperature traces use GPU plus the maximum reported HBF and HBM stack owner temperatures per window; this intentionally avoids a redundant plot for every sensor.",
        "- Maintenance age covers only explicitly declared pages. Age clears only with `mapping_committed=true` and an observed `age_reset_ns`; cleanup failure after commit remains distinct from a pre-commit failure.",
        "- Energy relative residual is `abs(cumulative residual) / abs(cumulative total input)` from each thermal window. No missing denominator is replaced with zero.",
        "",
    ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--point", action="append", required=True, type=Path,
                        help="completed point directory; repeat for comparison")
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)
    output = args.output.resolve()
    if output.exists() and any(output.iterdir()):
        parser.error(f"output must be absent or empty: {output}")
    output.mkdir(parents=True, exist_ok=True)
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "kind": "READ_ONLY_POSTPROCESS",
        "source_points": [str(p.resolve()) for p in args.point],
        "analyzer_sha256": _sha256(Path(__file__)),
        "pid": os.getpid(),
    }
    (output / "analysis-manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    try:
        results = [analyze_point(point) for point in args.point]
        for result in results:
            plot_point(result, output / f"{result['point_id']}-closed-loop.png")
        payload = {"schema_version": SCHEMA_VERSION, "points": results}
        (output / "derived-summary.json").write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
        (output / "analysis.md").write_text(_markdown(results), encoding="utf-8")
        (output / "DONE.json").write_text(json.dumps({
            "execution_status": "COMPLETED",
            "point_count": len(results),
            "raw_modified": False,
        }, indent=2) + "\n")
    except Exception as error:
        (output / "FAILED.json").write_text(json.dumps({
            "execution_status": "FAILED", "error": f"{type(error).__name__}: {error}"
        }, indent=2) + "\n")
        raise
    return 0


if __name__ == "__main__":
    sys.exit(main())
