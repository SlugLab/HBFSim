#!/usr/bin/env python3
"""Reproducible analysis and plots for the 36-point controlled-rate campaign."""

from __future__ import annotations

import argparse
import csv
from itertools import combinations, zip_longest
import json
import math
from pathlib import Path
from typing import Any, Iterable


STRATEGIES = (
    "guard_only",
    "thermal_hysteresis_guard",
    "read_rate_feedback_thermal_guard_v1",
)
TOPOLOGIES = ("mixed_direct", "all_hbf_direct")
PATTERNS = ("continuous", "burst_equal_mean")
STATE_RANK = {"normal": 0, "light": 1, "severe": 2, "shutdown": 3}
EXPECTED_POINT_COUNT = 39
J_PER_BYTE = 50e-12


def _load(path: Path) -> dict:
    value = json.loads(path.read_text())
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return value


def _rows(path: Path) -> Iterable[dict]:
    with path.open() as stream:
        for line_number, line in enumerate(stream, 1):
            if not line.strip():
                continue
            value = json.loads(line)
            if not isinstance(value, dict):
                raise ValueError(f"{path}:{line_number} must contain a JSON object")
            yield value


def _weighted_percentile(histogram: dict[int, int], percentile: int) -> int | None:
    total = sum(histogram.values())
    if total == 0:
        return None
    rank = (percentile * total + 99) // 100
    cumulative = 0
    for delay, byte_count in sorted(histogram.items()):
        cumulative += byte_count
        if cumulative >= rank:
            return delay
    raise AssertionError("weighted percentile rank was not reached")


def _finite(value: Any, path: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{path} must be numeric")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"{path} must be finite")
    return result


def _interval(row: dict, source: str) -> tuple[int, int]:
    start = row.get("start_ns")
    end = row.get("end_ns")
    if isinstance(start, bool) or not isinstance(start, int) or isinstance(end, bool) or not isinstance(end, int):
        raise ValueError(f"{source} interval must use integer ns")
    if end <= start:
        raise ValueError(f"{source} interval must be positive")
    return start, end


def _histogram_from_done(done: dict, stack: str) -> dict[int, int]:
    wait = done["summary"]["fluid_wait_by_stack"][stack]
    histogram = {}
    for row in wait.get("delivered_delay_histogram_bytes", []):
        delay, byte_count = row.get("delay_ns"), row.get("bytes")
        if not isinstance(delay, int) or not isinstance(byte_count, int) or delay < 0 or byte_count < 0:
            raise ValueError("delay histogram must contain nonnegative integer ns/bytes")
        histogram[delay] = histogram.get(delay, 0) + byte_count
    if _weighted_percentile(histogram, 95) != wait.get("delivered_delay_p95_ns"):
        raise ValueError(f"{stack} p95 differs from its byte histogram")
    if _weighted_percentile(histogram, 99) != wait.get("delivered_delay_p99_ns"):
        raise ValueError(f"{stack} p99 differs from its byte histogram")
    return histogram


def analyze_point(point: Path) -> dict:
    done = _load(point / "DONE.json")
    manifest = _load(point / "manifest.json")
    workload = _load(point / "workload.json")
    scenario = _load(point / "scenario.json")
    if done.get("execution_status") != "COMPLETED":
        raise ValueError(f"{point} is not COMPLETED")
    strategy = done.get("strategy")
    if strategy not in STRATEGIES or manifest.get("strategy") != strategy:
        raise ValueError(f"{point} has an unsupported or inconsistent strategy")
    topology = scenario.get("topology")
    model_id = workload.get("metadata", {}).get("model_id")
    pattern = workload.get("metadata", {}).get("pattern")
    full_scans_per_s = workload.get("metadata", {}).get("full_scans_per_s")
    if (topology not in TOPOLOGIES or not isinstance(model_id, str) or pattern not in PATTERNS or
            isinstance(full_scans_per_s, bool) or not isinstance(full_scans_per_s, int) or
            full_scans_per_s <= 0):
        raise ValueError(f"{point} has invalid topology/model/pattern identity")
    stacks = workload.get("metadata", {}).get("stack_ids")
    if not isinstance(stacks, list) or not stacks or any(not isinstance(x, str) for x in stacks):
        raise ValueError(f"{point} workload must declare stack_ids")
    active_ns = workload["metadata"].get("active_ns")
    if not isinstance(active_ns, int) or active_ns <= 0:
        raise ValueError(f"{point} workload must declare active_ns")

    per_stack = {
        stack: {"offered_bytes": 0, "delivered_bytes": 0, "active_delivered_bytes": 0,
                "final_backlog_bytes": 0, "peak_k": -math.inf, "final_k": None,
                "state_time_ns": {state: 0 for state in STATE_RANK}}
        for stack in stacks
    }
    totals = {"offered_bytes": 0, "delivered_bytes": 0, "active_delivered_bytes": 0,
              "energy_j": 0.0}
    any_stack_state_time_ns = {state: 0 for state in STATE_RANK}
    trace = {"end_ns": [], "max_temperature_k": [], "delivered_Bps": [], "backlog_bytes": []}
    expected_start = 0
    final_thermal = None
    final_rate = None
    count = 0

    streams = (
        _rows(point / "rates.jsonl"),
        _rows(point / "control.jsonl"),
        _rows(point / "energy.jsonl"),
        _rows(point / "thermal.jsonl"),
    )
    for index, aligned in enumerate(zip_longest(*streams)):
        if any(row is None for row in aligned):
            raise ValueError(f"{point} JSONL streams have different row counts")
        rate, control, energy, thermal = aligned
        interval = _interval(rate, "rates")
        if interval[0] != expected_start:
            raise ValueError(f"{point} rates are not contiguous")
        for name, row in (("energy", energy), ("thermal", thermal)):
            if _interval(row, name) != interval:
                raise ValueError(f"{point} {name} interval differs from rates")
        if (control.get("observed_window_start_ns"), control.get("observed_window_end_ns")) != interval:
            raise ValueError(f"{point} control observation interval differs from rates")
        duration_ns = interval[1] - interval[0]
        expected_start = interval[1]
        count += 1
        receipts = rate.get("stacks")
        if not isinstance(receipts, dict) or set(receipts) != set(stacks):
            raise ValueError(f"{point} rate stack coverage differs from workload")
        window_offered = window_delivered = window_backlog = 0
        for stack in stacks:
            receipt = receipts[stack]
            offered = int(receipt["offered_bytes"])
            delivered = int(receipt["delivered_bytes"])
            backlog = int(receipt["backlog_bytes"])
            if min(offered, delivered, backlog) < 0:
                raise ValueError("rate receipts contain negative bytes")
            per_stack[stack]["offered_bytes"] += offered
            per_stack[stack]["delivered_bytes"] += delivered
            if interval[0] < active_ns:
                per_stack[stack]["active_delivered_bytes"] += delivered
            per_stack[stack]["final_backlog_bytes"] = backlog
            window_offered += offered
            window_delivered += delivered
            window_backlog += backlog
        if rate.get("window_arrived_bytes") != window_offered or rate.get("window_served_bytes") != window_delivered:
            raise ValueError(f"{point} window byte totals differ from stack receipts")
        totals["offered_bytes"] += window_offered
        totals["delivered_bytes"] += window_delivered
        if interval[0] < active_ns:
            totals["active_delivered_bytes"] += window_delivered

        component_energy = energy.get("component_energy_j")
        if not isinstance(component_energy, dict):
            raise ValueError("energy row lacks component_energy_j")
        window_energy = sum(_finite(value, "component energy") for value in component_energy.values())
        if not math.isclose(window_energy, _finite(energy.get("window_total_j"), "window_total_j"),
                            rel_tol=1e-12, abs_tol=1e-15):
            raise ValueError(f"{point} component energy does not sum to window total")
        if not math.isclose(window_energy, window_delivered * J_PER_BYTE,
                            rel_tol=1e-12, abs_tol=1e-15):
            raise ValueError(f"{point} served bytes do not map to 50 pJ/B once")
        totals["energy_j"] += window_energy

        temperatures = thermal.get("temperatures")
        states = thermal.get("stack_states")
        if not isinstance(temperatures, dict) or not isinstance(states, dict):
            raise ValueError("thermal row lacks temperatures/stack_states")
        if set(stacks) - set(temperatures) or set(stacks) - set(states):
            raise ValueError("thermal row lacks a workload stack")
        worst = "normal"
        for stack in stacks:
            temperature = _finite(temperatures[stack], f"temperature {stack}")
            state = states[stack]
            if state not in STATE_RANK:
                raise ValueError(f"unknown thermal state {state!r}")
            per_stack[stack]["peak_k"] = max(per_stack[stack]["peak_k"], temperature)
            per_stack[stack]["final_k"] = temperature
            per_stack[stack]["state_time_ns"][state] += duration_ns
            if STATE_RANK[state] > STATE_RANK[worst]:
                worst = state
        any_stack_state_time_ns[worst] += duration_ns
        trace["end_ns"].append(interval[1])
        trace["max_temperature_k"].append(max(float(temperatures[stack]) for stack in stacks))
        trace["delivered_Bps"].append(window_delivered * 1_000_000_000 / duration_ns)
        trace["backlog_bytes"].append(window_backlog)
        final_thermal, final_rate = thermal, rate

    workload_windows = workload.get("windows", [])
    if count != len(workload_windows) or expected_start == 0:
        raise ValueError(f"{point} JSONL count differs from workload")
    duration_ns = expected_start
    final_backlog = sum(row["final_backlog_bytes"] for row in per_stack.values())
    byte_error = totals["offered_bytes"] - totals["delivered_bytes"] - final_backlog
    if byte_error != 0:
        raise ValueError(f"{point} end-to-end byte conservation failed by {byte_error}")
    expected_offered = sum(int(window["total_offered_bytes"]) for window in workload_windows)
    if expected_offered != totals["offered_bytes"]:
        raise ValueError(f"{point} workload and rate arrivals differ")

    aggregate_histogram = {}
    for stack in stacks:
        histogram = _histogram_from_done(done, stack)
        if sum(histogram.values()) != per_stack[stack]["delivered_bytes"]:
            raise ValueError(f"{stack} delay histogram bytes differ from delivered bytes")
        for delay, byte_count in histogram.items():
            aggregate_histogram[delay] = aggregate_histogram.get(delay, 0) + byte_count
        stack_result = per_stack[stack]
        stack_result["mean_delivered_Bps_total_duration"] = (
            stack_result["delivered_bytes"] * 1_000_000_000 / duration_ns
        )
        stack_result["mean_delivered_Bps_active_windows"] = (
            stack_result["active_delivered_bytes"] * 1_000_000_000 / active_ns
        )
        stack_result["delivered_delay_p95_ns"] = _weighted_percentile(histogram, 95)
        stack_result["delivered_delay_p99_ns"] = _weighted_percentile(histogram, 99)

    thermal_receipt_j = _finite(done["thermal_energy_receipt"]["total_input_j"], "thermal receipt")
    done_energy_j = _finite(done["summary"]["energy_j"], "DONE energy")
    energy_stream_error_j = totals["energy_j"] - totals["delivered_bytes"] * J_PER_BYTE
    thermal_energy_error_j = thermal_receipt_j - totals["energy_j"]
    if not math.isclose(done_energy_j, totals["energy_j"], rel_tol=1e-12, abs_tol=1e-12):
        raise ValueError(f"{point} DONE and energy stream totals differ")
    if abs(thermal_energy_error_j) > 1e-9 * max(1.0, totals["energy_j"]):
        raise ValueError(f"{point} thermal receipt and energy stream differ")

    return {
        "point_id": point.name,
        "point_path": str(point.resolve()),
        "topology": topology,
        "model_id": model_id,
        "weight_bytes": workload["metadata"].get("weight_bytes"),
        "pattern": pattern,
        "full_scans_per_s": full_scans_per_s,
        "strategy": strategy,
        "window_count": count,
        "duration_ns": duration_ns,
        "active_ns": active_ns,
        "totals": {
            **totals,
            "final_backlog_bytes": final_backlog,
            "mean_delivered_Bps_total_duration": totals["delivered_bytes"] * 1_000_000_000 / duration_ns,
            "mean_delivered_Bps_active_windows": totals["active_delivered_bytes"] * 1_000_000_000 / active_ns,
            "delivery_fraction": (
                totals["delivered_bytes"] / totals["offered_bytes"] if totals["offered_bytes"] else None
            ),
            "byte_conservation_error": byte_error,
            "served_energy_error_j": energy_stream_error_j,
            "thermal_energy_error_j": thermal_energy_error_j,
            "byte_weighted_delay_p95_ns": _weighted_percentile(aggregate_histogram, 95),
            "byte_weighted_delay_p99_ns": _weighted_percentile(aggregate_histogram, 99),
            "peak_temperature_k": max(row["peak_k"] for row in per_stack.values()),
            "final_peak_temperature_k": max(row["final_k"] for row in per_stack.values()),
        },
        "per_stack": per_stack,
        "any_stack_state_time_ns": any_stack_state_time_ns,
        "final_guard_states": done["summary"].get("final_guard_states"),
        "semantics": done.get("semantics", {}),
        "limitations": {
            "backend_latency": "UNKNOWN",
            "token_per_s": "UNKNOWN",
            "maintenance": "UNAVAILABLE_IN_THIS_FLUID_PATH",
            "service": "MODELLED_FLUID_BYTE_FIFO_NOT_MQSIM_OR_FABRIC_COMPLETION",
            "energy": "INCREMENTAL_SERVED_BYTES_AT_USER_CONFIRMED_50_PJ_PER_BYTE_IDLE_UNKNOWN",
        },
        "input_identity": manifest.get("input_sha256", {}),
        "_trace": trace,
    }


def _delta(right: Any, left: Any) -> float | int | None:
    if right is None or left is None:
        return None
    return right - left


def pairwise_costs(points: list[dict]) -> list[dict]:
    grouped = {}
    for point in points:
        key = (point["topology"], point["model_id"], point["pattern"], point["full_scans_per_s"])
        strategy = point["strategy"]
        if strategy in grouped.setdefault(key, {}):
            raise ValueError(f"duplicate strategy {strategy!r} for {key}")
        grouped[key][strategy] = point
    rows = []
    for key, by_strategy in sorted(grouped.items()):
        for left_name, right_name in combinations(STRATEGIES, 2):
            if left_name not in by_strategy or right_name not in by_strategy:
                continue
            left, right = by_strategy[left_name], by_strategy[right_name]
            if left["input_identity"].get("workload.json") != right["input_identity"].get("workload.json"):
                raise ValueError(f"paired policies use different workloads for {key}")
            lt, rt = left["totals"], right["totals"]
            rows.append({
                "topology": key[0], "model_id": key[1], "pattern": key[2],
                "full_scans_per_s": key[3],
                "left_strategy": left_name, "right_strategy": right_name,
                "delta_semantics": "RIGHT_MINUS_LEFT_NO_BENEFIT_DIRECTION_ASSUMED",
                "delivered_bytes_delta": _delta(rt["delivered_bytes"], lt["delivered_bytes"]),
                "final_backlog_bytes_delta": _delta(rt["final_backlog_bytes"], lt["final_backlog_bytes"]),
                "mean_delivered_Bps_delta": _delta(rt["mean_delivered_Bps_total_duration"],
                                                     lt["mean_delivered_Bps_total_duration"]),
                "peak_temperature_k_delta": _delta(rt["peak_temperature_k"], lt["peak_temperature_k"]),
                "final_peak_temperature_k_delta": _delta(rt["final_peak_temperature_k"],
                                                            lt["final_peak_temperature_k"]),
                "byte_weighted_delay_p95_ns_delta": _delta(rt["byte_weighted_delay_p95_ns"],
                                                              lt["byte_weighted_delay_p95_ns"]),
                "byte_weighted_delay_p99_ns_delta": _delta(rt["byte_weighted_delay_p99_ns"],
                                                              lt["byte_weighted_delay_p99_ns"]),
                "energy_j_delta": _delta(rt["energy_j"], lt["energy_j"]),
                "light_or_worse_stack_ns_delta": _delta(
                    sum(rt_state for state, rt_state in right["any_stack_state_time_ns"].items()
                        if STATE_RANK[state] >= STATE_RANK["light"]),
                    sum(lt_state for state, lt_state in left["any_stack_state_time_ns"].items()
                        if STATE_RANK[state] >= STATE_RANK["light"]),
                ),
            })
    return rows


def cross_topology_pressure_costs(points: list[dict]) -> list[dict]:
    """Compare the 235B continuous points under two explicit demand identities."""
    index = {(p["topology"], p["model_id"], p["pattern"], p["full_scans_per_s"],
              p["strategy"]): p for p in points}
    model = "Qwen/Qwen3-235B-A22B"
    rows = []
    comparisons = (
        ("SAME_TOTAL_OFFERED_DEMAND", 16, 16),
        ("SAME_PER_STACK_OFFERED_PRESSURE", 16, 32),
    )
    for identity, mixed_scans, all_hbf_scans in comparisons:
        for strategy in STRATEGIES:
            left = index.get(("mixed_direct", model, "continuous", mixed_scans, strategy))
            right = index.get(("all_hbf_direct", model, "continuous", all_hbf_scans, strategy))
            if left is None or right is None:
                continue
            lt, rt = left["totals"], right["totals"]
            left_per_stack = lt["offered_bytes"] / len(left["per_stack"])
            right_per_stack = rt["offered_bytes"] / len(right["per_stack"])
            if identity == "SAME_TOTAL_OFFERED_DEMAND" and lt["offered_bytes"] != rt["offered_bytes"]:
                raise ValueError("same-total-demand comparison does not have equal offered bytes")
            if identity == "SAME_PER_STACK_OFFERED_PRESSURE" and left_per_stack != right_per_stack:
                raise ValueError("same-per-stack-pressure comparison does not have equal offered bytes/stack")
            rows.append({
                "comparison_identity": identity,
                "strategy": strategy,
                "left": "mixed_direct:4stack:16scans_per_s",
                "right": f"all_hbf_direct:8stack:{all_hbf_scans}scans_per_s",
                "delta_semantics": "RIGHT_MINUS_LEFT_NO_BENEFIT_DIRECTION_ASSUMED",
                "left_total_offered_bytes": lt["offered_bytes"],
                "right_total_offered_bytes": rt["offered_bytes"],
                "left_offered_bytes_per_stack": left_per_stack,
                "right_offered_bytes_per_stack": right_per_stack,
                "delivered_bytes_delta": _delta(rt["delivered_bytes"], lt["delivered_bytes"]),
                "final_backlog_bytes_delta": _delta(rt["final_backlog_bytes"], lt["final_backlog_bytes"]),
                "peak_temperature_k_delta": _delta(rt["peak_temperature_k"], lt["peak_temperature_k"]),
                "energy_j_delta": _delta(rt["energy_j"], lt["energy_j"]),
                "byte_weighted_delay_p99_ns_delta": _delta(rt["byte_weighted_delay_p99_ns"],
                                                              lt["byte_weighted_delay_p99_ns"]),
            })
    return rows


def analyze_campaign(campaign: Path, *, require_complete: bool = True) -> dict:
    point_dirs = sorted({path.parent for path in campaign.rglob("DONE.json")})
    points = [analyze_point(path) for path in point_dirs]
    if require_complete:
        if len(points) != EXPECTED_POINT_COUNT:
            raise ValueError(f"expected {EXPECTED_POINT_COUNT} completed points, found {len(points)}")
        design = {(p["topology"], p["model_id"], p["pattern"], p["full_scans_per_s"],
                   p["strategy"]) for p in points}
        models = sorted({p["model_id"] for p in points})
        expected = {(t, m, w, 16, s) for t in TOPOLOGIES for m in models
                    for w in PATTERNS for s in STRATEGIES}
        expected |= {("all_hbf_direct", "Qwen/Qwen3-235B-A22B", "continuous", 32, s)
                     for s in STRATEGIES}
        if len(models) != 3 or design != expected:
            raise ValueError("completed points do not form the frozen 2x3x2x3 design")
    return {
        "schema_version": "eq3-controlled-campaign-analysis-v1",
        "campaign": str(campaign.resolve()),
        "completed_point_count": len(points),
        "expected_point_count": EXPECTED_POINT_COUNT,
        "design_complete": len(points) == EXPECTED_POINT_COUNT,
        "points": points,
        "pairwise_policy_costs": pairwise_costs(points),
        "cross_topology_pressure_costs": cross_topology_pressure_costs(points),
        "global_limitations": {
            "backend_latency": "UNKNOWN",
            "token_per_s": "UNKNOWN",
            "maintenance": "UNAVAILABLE_IN_THIS_FLUID_PATH",
            "backend": "NO_MQSIM_OR_FABRIC_COMPLETION_IN_THIS_CAMPAIGN",
            "thermal": "CONDITIONAL_INCREMENTAL_READ_HEATING_IDLE_AND_GPU_SELF_POWER_UNKNOWN",
        },
    }


def _serializable(analysis: dict) -> dict:
    return {
        **analysis,
        "points": [{key: value for key, value in point.items() if not key.startswith("_")}
                   for point in analysis["points"]],
    }


def _slug(value: str) -> str:
    return "".join(character.lower() if character.isalnum() else "-" for character in value).strip("-")


def write_plots(analysis: dict, output: Path) -> list[str]:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    output.mkdir(parents=True, exist_ok=True)
    paths = []
    grouped = {}
    for point in analysis["points"]:
        grouped.setdefault((point["topology"], point["model_id"], point["pattern"],
                            point["full_scans_per_s"]), []).append(point)
    for key, points in sorted(grouped.items()):
        fig, axes = plt.subplots(3, 1, figsize=(10, 8), sharex=True, constrained_layout=True)
        for point in sorted(points, key=lambda row: STRATEGIES.index(row["strategy"])):
            trace = point["_trace"]
            x = [value / 1e9 for value in trace["end_ns"]]
            label = point["strategy"]
            axes[0].plot(x, trace["max_temperature_k"], label=label)
            axes[1].plot(x, [value / 1e12 for value in trace["delivered_Bps"]], label=label)
            axes[2].plot(x, [value / 1e12 for value in trace["backlog_bytes"]], label=label)
        axes[0].set_ylabel("Max HBF K")
        axes[1].set_ylabel("Delivered TB/s")
        axes[2].set_ylabel("Backlog TB")
        axes[2].set_xlabel("Time (s)")
        axes[0].legend(fontsize=8)
        for axis in axes:
            axis.grid(alpha=.2)
        fig.suptitle(f"{key[0]} | {key[1]} | {key[2]} | {key[3]} scans/s\n"
                     "modelled fluid service; backend/token latency unavailable")
        path = output / f"trajectory-{_slug(key[0])}-{_slug(key[1])}-{_slug(key[2])}-{key[3]}sps.png"
        fig.savefig(path, dpi=150)
        plt.close(fig)
        paths.append(str(path))

    points = sorted(analysis["points"], key=lambda p: (p["topology"], p["model_id"], p["pattern"],
                                                         p["full_scans_per_s"],
                                                         STRATEGIES.index(p["strategy"])))
    fig, axes = plt.subplots(3, 1, figsize=(14, 10), sharex=True, constrained_layout=True)
    labels = [f"{p['topology']}\n{p['model_id'].split('/')[-1]}\n{p['pattern']} "
              f"{p['full_scans_per_s']}sps\n{p['strategy']}" for p in points]
    x = list(range(len(points)))
    axes[0].bar(x, [p["totals"]["peak_temperature_k"] for p in points])
    axes[1].bar(x, [p["totals"]["delivery_fraction"] or 0.0 for p in points])
    axes[2].bar(x, [p["totals"]["final_backlog_bytes"] / 1e12 for p in points])
    axes[0].set_ylabel("Peak HBF K")
    axes[1].set_ylabel("Delivered/offered")
    axes[2].set_ylabel("Final backlog TB")
    axes[2].set_xticks(x, labels, rotation=90, fontsize=6)
    for axis in axes:
        axis.grid(axis="y", alpha=.2)
    fig.suptitle("Controlled-rate campaign summary (no policy benefit direction assumed)")
    path = output / "controlled-campaign-summary.png"
    fig.savefig(path, dpi=150)
    plt.close(fig)
    paths.append(str(path))
    return paths


def write_outputs(analysis: dict, output: Path, *, plots: bool = True) -> None:
    output.mkdir(parents=True, exist_ok=False)
    serializable = _serializable(analysis)
    (output / "CONTROLLED_CAMPAIGN_ANALYSIS.json").write_text(
        json.dumps(serializable, indent=2, sort_keys=True, allow_nan=False) + "\n"
    )
    point_fields = [
        "point_id", "topology", "model_id", "pattern", "strategy", "offered_bytes",
        "full_scans_per_s",
        "delivered_bytes", "final_backlog_bytes", "mean_delivered_Bps_total_duration",
        "delivery_fraction", "peak_temperature_k", "final_peak_temperature_k",
        "byte_weighted_delay_p95_ns", "byte_weighted_delay_p99_ns", "energy_j",
        "byte_conservation_error", "thermal_energy_error_j",
    ]
    with (output / "point-summary.csv").open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=point_fields)
        writer.writeheader()
        for point in analysis["points"]:
            writer.writerow({key: point.get(key, point["totals"].get(key)) for key in point_fields})
    pair_rows = analysis["pairwise_policy_costs"]
    if pair_rows:
        with (output / "pairwise-policy-costs.csv").open("w", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=list(pair_rows[0]))
            writer.writeheader()
            writer.writerows(pair_rows)
    cross_rows = analysis["cross_topology_pressure_costs"]
    if cross_rows:
        with (output / "cross-topology-pressure-costs.csv").open("w", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=list(cross_rows[0]))
            writer.writeheader()
            writer.writerows(cross_rows)
    plot_paths = write_plots(analysis, output) if plots else []
    lines = [
        "# Controlled rate campaign analysis", "",
        f"Completed points: {analysis['completed_point_count']} / {analysis['expected_point_count']}", "",
        "Metrics were recomputed from aligned rates/control/energy/thermal streams. Byte and energy",
        "conservation were checked per point. Pairwise deltas are always right minus left and do not",
        "encode a preferred policy or assume that lower temperature offsets lost delivery.", "",
        "Nonzero backlog is a measured saturation cost, not an execution failure. Cross-topology rows",
        "separately identify equal-total-demand and equal-per-stack-pressure comparisons.", "",
        "Backend latency, token/s and maintenance behavior remain unavailable. Delivery and byte-weighted",
        "delay are properties of the window-quantized fluid FIFO, not MQSim or fabric completion. Energy",
        "is incremental served-byte energy at the user-confirmed 50 pJ/B scenario; idle and GPU self-power",
        "are unknown in this path.", "",
        f"Generated plots: {len(plot_paths)}",
    ]
    (output / "CONTROLLED_CAMPAIGN_ANALYSIS.md").write_text("\n".join(lines) + "\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--campaign", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--allow-partial", action="store_true")
    args = parser.parse_args(argv)
    analysis = analyze_campaign(args.campaign.resolve(), require_complete=not args.allow_partial)
    write_outputs(analysis, args.output.resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
