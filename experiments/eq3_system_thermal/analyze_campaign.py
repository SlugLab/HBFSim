#!/usr/bin/env python3
"""Strict, read-only analysis for the 60-point four-topology campaign."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
from itertools import combinations
from pathlib import Path
from typing import Any, Iterable


EXPECTED_POINT_COUNT = 60
TOPOLOGIES = ("mixed_direct", "relay", "dash", "all_hbf_direct")
STRATEGIES = ("guard_only", "thermal_hysteresis_guard",
              "read_rate_feedback_thermal_guard_v1")
RATES_BPS = (384_000_000_000, 768_000_000_000, 1_152_000_000_000,
             1_536_000_000_000, 1_920_000_000_000)
STATE_RANK = {"normal": 0, "light": 1, "severe": 2, "shutdown": 3}
POINT_FILES = ("config.json", "manifest.json", "DONE.json", "windows.jsonl")


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
                raise ValueError(f"{path}:{line_number} must be a JSON object")
            yield value


def _finite(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label} must be numeric")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"{label} must be finite")
    return result


def _integer(value: Any, label: str, *, positive: bool = False) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{label} must be integer")
    if value < (1 if positive else 0):
        raise ValueError(f"{label} must be {'positive' if positive else 'nonnegative'}")
    return value


def _identity(config: dict, manifest: dict, point: Path) -> dict:
    required = ("point_id", "topology", "strategy", "workload", "recovery_ns")
    missing = [key for key in required if key not in config]
    if missing:
        raise ValueError(f"{point} config identity missing {missing}")
    topology, strategy = config["topology"], config["strategy"]
    workload = config["workload"]
    if not isinstance(workload, dict):
        raise ValueError("config.workload must be an object")
    rate = _integer(workload.get("per_stack_Bps"), "workload.per_stack_Bps", positive=True)
    active_ns = _integer(workload.get("active_ns"), "workload.active_ns", positive=True)
    recovery_ns = _integer(config["recovery_ns"], "recovery_ns", positive=True)
    duration_ns = active_ns + recovery_ns
    if topology not in TOPOLOGIES or strategy not in STRATEGIES or rate not in RATES_BPS:
        raise ValueError(f"{point} manifest is outside the frozen design")
    if duration_ns <= active_ns:
        raise ValueError("duration_ns must include a recovery interval")
    if not isinstance(config["point_id"], str) or not config["point_id"]:
        raise ValueError("point_id must be nonempty")
    config_sha = hashlib.sha256((point / "config.json").read_bytes()).hexdigest()
    if manifest.get("input_sha256") != config_sha:
        raise ValueError(f"{point} manifest input hash differs from config.json")
    return {"point_id": config["point_id"], "topology": topology,
            "offered_rate_Bps": rate, "strategy": strategy,
            "active_ns": active_ns, "duration_ns": duration_ns,
            "model_variant": Path(str(manifest.get("model_dir", "UNDECLARED"))).name,
            "input_sha256": config_sha}


def _token_fact(causal: Any) -> tuple[int | None, str]:
    if causal is None:
        return None, "UNAVAILABLE_CAUSAL_NULL"
    if not isinstance(causal, dict):
        raise ValueError("causal must be null or object")
    # Never derive tokens from bytes or layers. Only consume a named completion fact.
    for key in ("completed_tokens", "token_completions"):
        if key in causal:
            value = _integer(causal[key], f"causal.{key}")
            return value, f"OBSERVED_FIELD:{key}"
    return None, "UNAVAILABLE_NO_EXPLICIT_TOKEN_COMPLETION_FIELD"


def _sum_energy(mapping: Any, label: str) -> float:
    if not isinstance(mapping, dict):
        raise ValueError(f"{label} must be an object")
    result = sum(_finite(value, f"{label}.{key}") for key, value in mapping.items())
    if result < 0:
        raise ValueError(f"{label} cannot sum negative")
    return result


def analyze_point(point: Path) -> dict:
    config, manifest, done = (_load(point / "config.json"), _load(point / "manifest.json"),
                              _load(point / "DONE.json"))
    identity = _identity(config, manifest, point)
    if done.get("execution_status", done.get("status")) not in ("COMPLETED", "DONE"):
        raise ValueError(f"{point} is not complete")
    for key in ("point_id", "topology", "strategy"):
        if key in done and done[key] != identity[key]:
            raise ValueError(f"{point} DONE {key} differs from manifest")

    per_stack: dict[str, dict[str, Any]] = {}
    traces = {"end_ns": [], "offered_Bps": [], "delivered_Bps": [], "backlog_bytes": [],
              "max_temperature_k": [], "worst_state_rank": [], "maintenance_pending_bytes": [],
              "maintenance_completions": [], "token_completions": []}
    totals = {"offered_effective_bytes": 0, "delivered_effective_bytes": 0,
              "media_payload_bytes": 0, "energy_j": 0.0,
              "maintenance_completion_count": 0}
    expected_start = 0
    service_stacks: set[str] | None = None
    completed_maintenance: set[str] = set()
    token_semantics: set[str] = set()
    reliability_statuses: set[str] = set()
    final_backlog = 0
    count = 0

    for row in _rows(point / "windows.jsonl"):
        start = _integer(row.get("start_ns"), "start_ns")
        end = _integer(row.get("end_ns"), "end_ns", positive=True)
        if start != expected_start or end <= start:
            raise ValueError(f"{point} windows are not positive and contiguous")
        if end > identity["duration_ns"]:
            raise ValueError(f"{point} window exceeds declared duration")
        expected_start = end
        duration = end - start
        count += 1
        service, energy, thermal = row.get("service"), row.get("energy"), row.get("thermal")
        if not all(isinstance(value, dict) for value in (service, energy, thermal)):
            raise ValueError("window lacks service/energy/thermal objects")
        if service.get("start_ns", start) != start or service.get("end_ns", end) != end:
            raise ValueError("service interval differs from window")
        stacks = service.get("stacks")
        if not isinstance(stacks, dict) or not stacks:
            raise ValueError("service.stacks must be nonempty")
        if service_stacks is None:
            service_stacks = set(stacks)
            for stack in sorted(service_stacks):
                per_stack[stack] = {
                    "offered_effective_bytes": 0, "delivered_effective_bytes": 0,
                    "media_payload_bytes": 0, "final_backlog_effective_bytes": 0,
                    "peak_temperature_k": None, "final_temperature_k": None,
                    "peak_oldest_wait_ns": None,
                    "state_time_ns": {state: 0 for state in STATE_RANK},
                }
        elif set(stacks) != service_stacks:
            raise ValueError("service stack coverage changed across windows")

        window_offered = window_delivered = window_media = window_backlog = 0
        for stack in sorted(service_stacks):
            receipt = stacks[stack]
            if not isinstance(receipt, dict):
                raise ValueError("stack receipt must be object")
            offered = _integer(receipt.get("offered_effective_bytes"), "offered bytes")
            delivered = _integer(receipt.get("delivered_effective_bytes"), "delivered bytes")
            backlog = _integer(receipt.get("backlog_effective_bytes"), "backlog bytes")
            media = _integer(receipt.get("media_payload_bytes"), "media payload bytes")
            dest = per_stack[stack]
            dest["offered_effective_bytes"] += offered
            dest["delivered_effective_bytes"] += delivered
            dest["media_payload_bytes"] += media
            dest["final_backlog_effective_bytes"] = backlog
            wait = receipt.get("oldest_wait_ns")
            if wait is not None:
                wait = _integer(wait, "oldest_wait_ns")
                dest["peak_oldest_wait_ns"] = max(dest["peak_oldest_wait_ns"] or 0, wait)
            window_offered += offered; window_delivered += delivered
            window_backlog += backlog; window_media += media
        totals["offered_effective_bytes"] += window_offered
        totals["delivered_effective_bytes"] += window_delivered
        totals["media_payload_bytes"] += window_media
        final_backlog = window_backlog

        component_j = _sum_energy(energy.get("component_energy_j"), "component_energy_j")
        scope_j = _sum_energy(energy.get("scope_energy_j"), "scope_energy_j")
        total_j = _finite(energy.get("total_j"), "energy.total_j")
        if not math.isclose(component_j, total_j, rel_tol=1e-12, abs_tol=1e-12):
            raise ValueError("component energy does not conserve to total_j")
        if not math.isclose(scope_j, total_j, rel_tol=1e-12, abs_tol=1e-12):
            raise ValueError("scope energy does not conserve to total_j")
        thermal_energy = thermal.get("energy_j")
        if not isinstance(thermal_energy, dict) or not isinstance(thermal_energy.get("window"), dict):
            raise ValueError("thermal.energy_j.window must be an object")
        thermal_activity_j = _finite(thermal_energy["window"].get("activity_input_j"),
                                     "thermal window activity_input_j")
        thermal_total_j = _finite(thermal_energy["window"].get("total_input_j"),
                                  "thermal window total_input_j")
        if not math.isclose(thermal_activity_j, total_j, rel_tol=1e-12, abs_tol=1e-12):
            raise ValueError("thermal injected energy differs from mapped energy")
        if not math.isclose(thermal_total_j, total_j, rel_tol=1e-12, abs_tol=1e-12):
            raise ValueError("thermal total input includes energy outside mapped baseline")
        totals["energy_j"] += total_j

        temperatures, states = thermal.get("temperatures"), thermal.get("stack_states")
        if not isinstance(temperatures, dict) or not isinstance(states, dict):
            raise ValueError("thermal temperatures/states must be objects")
        if not service_stacks.issubset(temperatures) or not service_stacks.issubset(states):
            raise ValueError("thermal facts do not cover every service stack")
        worst_rank = 0
        for stack in sorted(service_stacks):
            temperature = _finite(temperatures[stack], f"temperature.{stack}")
            state = states[stack]
            if state not in STATE_RANK:
                raise ValueError(f"unsupported thermal state {state!r}")
            dest = per_stack[stack]
            dest["peak_temperature_k"] = max(dest["peak_temperature_k"] or -math.inf, temperature)
            dest["final_temperature_k"] = temperature
            dest["state_time_ns"][state] += duration
            worst_rank = max(worst_rank, STATE_RANK[state])

        progress = service.get("job_progress", [])
        if not isinstance(progress, list):
            raise ValueError("service.job_progress must be an array")
        pending_maintenance = 0
        for job in progress:
            if not isinstance(job, dict):
                raise ValueError("job_progress entry must be an object")
            if job.get("maintenance_id") is not None:
                pending_maintenance += _integer(job.get("remaining_bytes"), "maintenance remaining")
        completions = service.get("maintenance_completion_ids", [])
        if not isinstance(completions, list) or any(not isinstance(value, str) for value in completions):
            raise ValueError("maintenance completion IDs must be strings")
        if completed_maintenance.intersection(completions) or len(set(completions)) != len(completions):
            raise ValueError("maintenance completion identity is not unique")
        completed_maintenance.update(completions)
        totals["maintenance_completion_count"] += len(completions)

        token_count, token_kind = _token_fact(row.get("causal"))
        token_semantics.add(token_kind)
        reliability = row.get("reliability")
        if not isinstance(reliability, dict) or not isinstance(reliability.get("status"), str):
            raise ValueError("reliability must carry an explicit status")
        reliability_statuses.add(reliability["status"])
        traces["end_ns"].append(end)
        traces["offered_Bps"].append(window_offered * 1e9 / duration)
        traces["delivered_Bps"].append(window_delivered * 1e9 / duration)
        traces["backlog_bytes"].append(window_backlog)
        traces["max_temperature_k"].append(max(float(temperatures[s]) for s in service_stacks))
        traces["worst_state_rank"].append(worst_rank)
        traces["maintenance_pending_bytes"].append(pending_maintenance)
        traces["maintenance_completions"].append(len(completions))
        traces["token_completions"].append(token_count)

    if count == 0 or expected_start != identity["duration_ns"]:
        raise ValueError(f"{point} window coverage differs from declared duration")
    conservation_error = totals["offered_effective_bytes"] - totals["delivered_effective_bytes"] - final_backlog
    if conservation_error != 0:
        raise ValueError(f"{point} foreground byte conservation failed by {conservation_error}")
    for stack, result in per_stack.items():
        error = (result["offered_effective_bytes"] - result["delivered_effective_bytes"] -
                 result["final_backlog_effective_bytes"])
        if error != 0:
            raise ValueError(f"{point} {stack} byte conservation failed by {error}")
        result["byte_conservation_error"] = error
        result["mean_delivered_Bps"] = result["delivered_effective_bytes"] * 1e9 / identity["duration_ns"]
    totals.update({
        "final_backlog_effective_bytes": final_backlog,
        "byte_conservation_error": conservation_error,
        "mean_delivered_Bps": totals["delivered_effective_bytes"] * 1e9 / identity["duration_ns"],
        "delivery_fraction": (totals["delivered_effective_bytes"] / totals["offered_effective_bytes"]
                              if totals["offered_effective_bytes"] else None),
        "peak_temperature_k": max(result["peak_temperature_k"] for result in per_stack.values()),
        "final_peak_temperature_k": max(result["final_temperature_k"] for result in per_stack.values()),
    })
    token_available = all(value is not None for value in traces["token_completions"])
    no_maintenance = reliability_statuses == {"NO_MAINTENANCE_DEMAND_IN_BASE_RATE_WORKLOAD"}
    return {**identity, "point_path": str(point.resolve()), "window_count": count,
            "service_stacks": sorted(service_stacks), "totals": totals, "per_stack": per_stack,
            "token_metric": {"status": "AVAILABLE" if token_available else "UNAVAILABLE",
                             "semantics": sorted(token_semantics)},
            "maintenance_metric": {
                "status": ("NOT_EXERCISED_NO_DEMAND" if no_maintenance else
                           "AVAILABLE_MODELLED_JOB_PROGRESS"),
                "reliability_statuses": sorted(reliability_statuses),
            },
            "limitations": {"backend_latency": "UNKNOWN",
                            "service": "MODELLED_AGGREGATED_FLUID_NOT_MQSIM_COMPLETION",
                            "token_per_s": "AVAILABLE_ONLY_FROM_EXPLICIT_CAUSAL_TOKEN_COMPLETION_FACT",
                            "maintenance": "MODELLED_MAINTENANCE_JOB_PROGRESS_AND_UNIQUE_COMPLETIONS"},
            "_trace": traces}


def _delta(right: Any, left: Any) -> Any:
    return None if right is None or left is None else right - left


def policy_comparisons(points: list[dict]) -> list[dict]:
    groups: dict[tuple, dict[str, dict]] = {}
    for point in points:
        key = (point["topology"], point["offered_rate_Bps"], point["model_variant"])
        if point["strategy"] in groups.setdefault(key, {}):
            raise ValueError(f"duplicate strategy for {key}")
        groups[key][point["strategy"]] = point
    rows = []
    for key, by_strategy in sorted(groups.items()):
        for left_name, right_name in combinations(STRATEGIES, 2):
            if left_name not in by_strategy or right_name not in by_strategy:
                continue
            left, right = by_strategy[left_name], by_strategy[right_name]
            if left["totals"]["offered_effective_bytes"] != right["totals"]["offered_effective_bytes"]:
                raise ValueError(f"policy pair offered demand differs for {key}")
            lt, rt = left["totals"], right["totals"]
            rows.append({"topology": key[0], "offered_rate_Bps": key[1],
                         "model_variant": key[2], "left_strategy": left_name,
                         "right_strategy": right_name,
                         "delta_semantics": "RIGHT_MINUS_LEFT_NO_BENEFIT_DIRECTION_ASSUMED",
                         "delivered_effective_bytes_delta": _delta(rt["delivered_effective_bytes"], lt["delivered_effective_bytes"]),
                         "final_backlog_effective_bytes_delta": _delta(rt["final_backlog_effective_bytes"], lt["final_backlog_effective_bytes"]),
                         "peak_temperature_k_delta": _delta(rt["peak_temperature_k"], lt["peak_temperature_k"]),
                         "energy_j_delta": _delta(rt["energy_j"], lt["energy_j"]),
                         "maintenance_completion_count_delta": _delta(rt["maintenance_completion_count"], lt["maintenance_completion_count"])})
    return rows


def _is_point(path: Path) -> bool:
    return all((path / name).is_file() for name in POINT_FILES)


def _discover(campaign: Path) -> tuple[list[Path], str]:
    index_path = campaign / "RUN_INDEX.json"
    if index_path.is_file():
        index = _load(index_path)
        entries = index.get("points")
        if not isinstance(entries, list):
            raise ValueError("RUN_INDEX.points must be an array")
        main = [row for row in entries if isinstance(row, dict) and row.get("kind") == "base"]
        if len(main) != EXPECTED_POINT_COUNT:
            raise ValueError("RUN_INDEX must whitelist exactly 60 kind=base points")
        paths = []
        for row in main:
            raw = row.get("output")
            if not isinstance(raw, str) or not raw:
                raise ValueError("RUN_INDEX point lacks output")
            path = Path(raw)
            if not path.is_absolute():
                path = campaign / path
            path = path.resolve()
            if not _is_point(path):
                raise ValueError(f"indexed point incomplete: {path}")
            config_path = Path(row.get("config", ""))
            if not config_path.is_file():
                raise ValueError(f"RUN_INDEX config missing: {config_path}")
            if row.get("config_sha256") != hashlib.sha256(config_path.read_bytes()).hexdigest():
                raise ValueError(f"RUN_INDEX config hash differs: {config_path}")
            paths.append(path)
        if len(set(paths)) != len(paths):
            raise ValueError("RUN_INDEX contains duplicate output paths")
        return paths, "RUN_INDEX_KIND_BASE_WHITELIST"
    paths = sorted({path.parent.resolve() for path in campaign.rglob("DONE.json") if _is_point(path.parent)})
    if not paths:
        raise ValueError("no complete point directories found")
    return paths, "COMPLETE_POINT_DISCOVERY_WITHOUT_RUN_INDEX"


def analyze_campaign(campaign: Path, *, require_complete: bool = True) -> dict:
    paths, discovery = _discover(campaign)
    points = [analyze_point(path) for path in paths]
    identities = {(p["topology"], p["offered_rate_Bps"], p["strategy"]) for p in points}
    expected = {(topology, rate, strategy) for topology in TOPOLOGIES
                for rate in RATES_BPS for strategy in STRATEGIES}
    if len(identities) != len(points):
        raise ValueError("duplicate campaign design identity")
    if require_complete and identities != expected:
        raise ValueError("completed points do not form the frozen 4x5x3 design")
    return {"schema_version": "eq3-system-thermal-campaign-analysis-v1",
            "campaign": str(campaign.resolve()), "point_discovery": discovery,
            "completed_point_count": len(points), "expected_point_count": EXPECTED_POINT_COUNT,
            "design_complete": identities == expected, "points": points,
            "policy_comparisons": policy_comparisons(points),
            "global_limitations": {
                "backend_latency": "UNKNOWN",
                "service": "MODELLED_AGGREGATED_FLUID_NOT_NATIVE_MQSIM",
                "token_per_s": "UNAVAILABLE_WHEN_CAUSAL_IS_NULL; NEVER_INFERRED_FROM_BYTES",
                "thermal": "CONDITIONAL_MODEL_WITH_REGISTERED_VARIANTS_NOT_PRODUCT_MEASUREMENT"}}


def _serializable(analysis: dict) -> dict:
    return {**analysis, "points": [{k: v for k, v in p.items() if k != "_trace"}
                                    for p in analysis["points"]]}


def _slug(value: str) -> str:
    return "".join(c.lower() if c.isalnum() else "-" for c in value).strip("-")


def write_plots(analysis: dict, output: Path) -> list[str]:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    paths = []
    for topology in TOPOLOGIES:
        points = [p for p in analysis["points"] if p["topology"] == topology]
        if not points:
            continue
        fig, axes = plt.subplots(3, 2, figsize=(14, 11), sharex=True, constrained_layout=True)
        axes = axes.ravel()
        colors = {rate: plt.cm.viridis(i / max(1, len(RATES_BPS)-1))
                  for i, rate in enumerate(RATES_BPS)}
        styles = {STRATEGIES[0]: "-", STRATEGIES[1]: "--", STRATEGIES[2]: ":"}
        for point in sorted(points, key=lambda p: (p["offered_rate_Bps"], p["strategy"])):
            trace = point["_trace"]
            x = [value / 1e9 for value in trace["end_ns"]]
            label = f"{point['offered_rate_Bps']/1e12:.3f} TB/s | {point['strategy']}"
            kwargs = {"color": colors[point["offered_rate_Bps"]],
                      "linestyle": styles[point["strategy"]], "linewidth": 1, "label": label}
            axes[0].plot(x, [v / 1e12 for v in trace["offered_Bps"]], **kwargs)
            axes[0].plot(x, [v / 1e12 for v in trace["delivered_Bps"]], alpha=.7,
                         color=kwargs["color"], linestyle=kwargs["linestyle"], linewidth=.8)
            axes[1].plot(x, trace["max_temperature_k"], **kwargs)
            axes[2].step(x, trace["worst_state_rank"], where="post", **kwargs)
            axes[3].plot(x, [v / 1e6 for v in trace["maintenance_pending_bytes"]], **kwargs)
            axes[3].scatter(x, trace["maintenance_completions"], color=kwargs["color"], s=2)
            axes[4].plot(x, [v / 1e12 for v in trace["backlog_bytes"]], **kwargs)
        axes[0].set_ylabel("Offered / delivered TB/s")
        axes[1].set_ylabel("Max stack K")
        axes[2].set_ylabel("Worst state rank")
        axes[2].set_yticks(list(STATE_RANK.values()), list(STATE_RANK))
        axes[3].set_ylabel("Maint pending MB\n(points=window commits)")
        axes[4].set_ylabel("Foreground backlog TB")
        if any(p["token_metric"]["status"] == "AVAILABLE" for p in points):
            axes[5].text(.5, .5, "Explicit token facts available; see JSON/CSV\n"
                         "Token-rate aggregation intentionally requires frozen causal semantics",
                         ha="center", va="center", transform=axes[5].transAxes)
        else:
            axes[5].text(.5, .5, "TOKEN/S UNAVAILABLE\ncausal=null or no explicit token completion fact\n"
                         "Never inferred from bytes", ha="center", va="center",
                         transform=axes[5].transAxes)
        axes[5].set_axis_off()
        if all(p["maintenance_metric"]["status"] == "NOT_EXERCISED_NO_DEMAND" for p in points):
            axes[3].text(.5, .5, "NO MAINTENANCE DEMAND\nqueue/commit path not exercised",
                         ha="center", va="center", transform=axes[3].transAxes,
                         bbox={"facecolor": "white", "alpha": .8, "edgecolor": "gray"})
        for axis in axes[:5]:
            axis.axvline(points[0]["active_ns"] / 1e9, color="gray", linestyle="-.", linewidth=.7)
            axis.grid(alpha=.2)
            axis.set_xlabel("Time (s)")
        axes[0].legend(fontsize=5, ncol=2)
        fig.suptitle(f"{topology}: conditional modelled system/thermal campaign\n"
                     "line color=offered rate; style=policy; delivered is the lighter rate trace")
        path = output / f"six-panel-{_slug(topology)}.png"
        fig.savefig(path, dpi=150)
        plt.close(fig)
        paths.append(str(path))
    return paths


def write_outputs(analysis: dict, output: Path, *, plots: bool = True) -> None:
    output.mkdir(parents=True, exist_ok=False)
    (output / "SYSTEM_THERMAL_CAMPAIGN_ANALYSIS.json").write_text(
        json.dumps(_serializable(analysis), indent=2, sort_keys=True, allow_nan=False) + "\n")
    point_fields = ("point_id", "topology", "offered_rate_Bps", "strategy", "model_variant",
                    "offered_effective_bytes", "delivered_effective_bytes",
                    "final_backlog_effective_bytes", "delivery_fraction", "mean_delivered_Bps",
                    "peak_temperature_k", "final_peak_temperature_k", "energy_j",
                    "maintenance_completion_count", "byte_conservation_error", "token_status")
    with (output / "point-summary.csv").open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=point_fields); writer.writeheader()
        for p in analysis["points"]:
            writer.writerow({**{key: p.get(key, p["totals"].get(key)) for key in point_fields},
                             "token_status": p["token_metric"]["status"]})
    stack_fields = ("point_id", "topology", "offered_rate_Bps", "strategy", "stack",
                    "offered_effective_bytes", "delivered_effective_bytes",
                    "final_backlog_effective_bytes", "mean_delivered_Bps", "peak_temperature_k",
                    "final_temperature_k", "peak_oldest_wait_ns", "normal_ns", "light_ns",
                    "severe_ns", "shutdown_ns", "byte_conservation_error")
    with (output / "per-stack-summary.csv").open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=stack_fields); writer.writeheader()
        for p in analysis["points"]:
            for stack, row in sorted(p["per_stack"].items()):
                output_row = {"point_id": p["point_id"], "topology": p["topology"],
                              "offered_rate_Bps": p["offered_rate_Bps"],
                              "strategy": p["strategy"], "stack": stack}
                for field in stack_fields:
                    if field in row:
                        output_row[field] = row[field]
                output_row.update({f"{state}_ns": row["state_time_ns"][state]
                                   for state in STATE_RANK})
                writer.writerow(output_row)
    comparisons = analysis["policy_comparisons"]
    if comparisons:
        with (output / "policy-comparisons.csv").open("w", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=list(comparisons[0])); writer.writeheader(); writer.writerows(comparisons)
    plot_paths = write_plots(analysis, output) if plots else []
    lines = ["# Four-topology system/thermal campaign analysis", "",
             f"Completed main points: {analysis['completed_point_count']} / {EXPECTED_POINT_COUNT}.", "",
             "All byte, component/scope energy, thermal-input energy, time-axis, stack-coverage, and unique maintenance completion checks passed before output generation.", "",
             "Rates and queues are properties of the modelled aggregate topology service, not native MQSim completion. Token/s remains unavailable whenever the causal field is null or lacks an explicit token completion fact; it is never reconstructed from bytes. Policy deltas are right minus left and do not encode a preferred direction.", "",
             f"Generated topology six-panel figures: {len(plot_paths)}."]
    (output / "SYSTEM_THERMAL_CAMPAIGN_ANALYSIS.md").write_text("\n".join(lines) + "\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--campaign", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--allow-partial", action="store_true")
    parser.add_argument("--no-plots", action="store_true")
    args = parser.parse_args(argv)
    analysis = analyze_campaign(args.campaign.resolve(), require_complete=not args.allow_partial)
    write_outputs(analysis, args.output.resolve(), plots=not args.no_plots)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
