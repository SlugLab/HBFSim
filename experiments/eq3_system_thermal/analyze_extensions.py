#!/usr/bin/env python3
"""Validate and summarize maintenance/causal point receipts without rerunning them."""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import hashlib
import json
import math
import os
from pathlib import Path
from typing import Any


STATE_RANK = {"normal": 0, "light": 1, "severe": 2, "shutdown": 3}
MEDIA_PHASES = {"media_read", "media_program", "media_erase", "media_fill"}


def _load(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return value


def _rows(path: Path):
    with path.open(encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, 1):
            if not line.strip():
                continue
            value = json.loads(line)
            if not isinstance(value, dict):
                raise ValueError(f"{path}:{line_number} must contain an object")
            yield value


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _canonical_sha256(value: Any) -> str:
    return hashlib.sha256(json.dumps(
        value, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode()).hexdigest()


def _number(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) \
            or not math.isfinite(float(value)):
        raise ValueError(f"{label} must be finite numeric")
    return float(value)


def _nonnegative_int(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{label} must be a nonnegative integer")
    return value


def _interval(row: dict, label: str) -> tuple[int, int]:
    start, end = row.get("start_ns"), row.get("end_ns")
    if type(start) is not int or type(end) is not int or start < 0 or end <= start:
        raise ValueError(f"{label} has invalid integer interval")
    return start, end


def _identity(point: Path, config: dict, manifest: dict) -> dict:
    input_hash = manifest.get("input_sha256")
    if not isinstance(input_hash, str) or len(input_hash) != 64:
        raise ValueError("manifest lacks input_sha256 identity")
    saved_hash = _sha256(point / "config.json")
    if saved_hash != input_hash:
        raise ValueError("saved config differs from manifest input identity")
    sources = manifest.get("source_sha256")
    if not isinstance(sources, dict) or not sources \
            or any(not isinstance(v, str) or len(v) != 64 for v in sources.values()):
        raise ValueError("manifest lacks complete source hashes")
    thermal_hash = manifest.get("thermal_binary_sha256")
    if not isinstance(thermal_hash, str) or len(thermal_hash) != 64:
        raise ValueError("manifest lacks thermal binary identity")
    trace_config = config.get("trace", {})
    trace_identity = None
    if trace_config.get("dependency_mode") == "tiny_cpu_template":
        checksum = trace_config.get("tiny_trace_sha256")
        path = trace_config.get("tiny_trace_path")
        if not isinstance(checksum, str) or len(checksum) != 64 or not isinstance(path, str):
            raise ValueError("tiny trace consumer lacks bound path/checksum")
        trace_identity = {"mode": "tiny_cpu_template", "path": path,
                          "trace_sha256": checksum}
    return {
        "point": str(point), "point_id": config.get("point_id"),
        "runner_input_sha256": input_hash, "saved_config_sha256": saved_hash,
        "saved_config_canonical_sha256": _canonical_sha256(config),
        "source_revision": manifest.get("source_revision"),
        "source_sha256": sources, "thermal_binary_sha256": thermal_hash,
        "thermal_model_lock": manifest.get("thermal_model_lock"),
        "trace_identity": trace_identity,
    }


def _activities(row: dict, runner: str) -> list[dict]:
    if runner == "causal":
        values = row["service"].get("activities", [])
    else:
        receipts = [row["service"]]
        if row.get("independent_maintenance_service") is not None:
            receipts.append(row["independent_maintenance_service"])
        values = [item for receipt in receipts for item in receipt.get("activities", [])]
    if not isinstance(values, list):
        raise ValueError("service activities must be a list")
    for item in values:
        _nonnegative_int(item.get("bytes"), "activity bytes")
    return values


def _temperature_groups(temperatures: dict) -> dict[str, float | None]:
    groups = {"gpu": [], "hbf": [], "hbm": []}
    for owner, value in temperatures.items():
        number = _number(value, f"temperature {owner}")
        lower = owner.lower()
        if lower == "gpu" or lower.startswith("gpu"):
            groups["gpu"].append(number)
        elif lower.startswith("hbf"):
            groups["hbf"].append(number)
        elif lower.startswith("hbm"):
            groups["hbm"].append(number)
    return {key: max(values) if values else None for key, values in groups.items()}


def _state_group(states: dict, prefix: str) -> int | None:
    selected = []
    for owner, state in states.items():
        if owner.lower().startswith(prefix):
            if state not in STATE_RANK:
                raise ValueError("unknown controller state")
            selected.append(STATE_RANK[state])
    return max(selected) if selected else None


def _driver_summary(final: dict | None, raw_terminal: list[dict]) -> dict:
    if final is None:
        return {"mode": "disabled", "capability": "NO_MAINTENANCE_FINAL_RECEIPT"}
    driver = final["driver"]
    reliability = final["reliability"]
    wear = driver.get("physical_wear", {})
    blocks = reliability.get("blocks", {})
    statuses = Counter(row["status"] for row in raw_terminal)
    terminal = driver.get("terminal_summary", {})
    if sum(statuses.values()) != terminal.get("operation_count", 0):
        raise ValueError("raw maintenance terminal count differs from final driver summary")
    if dict(sorted(statuses.items())) != terminal.get("status_counts", {}):
        raise ValueError("raw maintenance terminal statuses differ from final driver summary")
    if sum(len(row.get("extent_ids", ())) for row in raw_terminal) != \
            terminal.get("extent_count", 0):
        raise ValueError("raw maintenance terminal extents differ from final driver summary")
    free = driver.get("free_spares_by_stack_channel", {})
    return {
        "mode": final.get("mode", "shared_exact_service"),
        "terminal_status_counts": dict(sorted(statuses.items())),
        "terminal_operation_count": terminal.get("operation_count", 0),
        "terminal_extent_count": terminal.get("extent_count", 0),
        "free_spares_by_stack_channel": {
            stack: {channel: len(values) for channel, values in channels.items()}
            for stack, channels in free.items()},
        "free_spares": sum(len(values) for channels in free.values()
                           for values in channels.values()),
        "quarantined_blocks": len(driver.get("quarantined_blocks", [])),
        "age": {
            "block_count": len(blocks),
            "min_equivalent_age_ns": min((row["equivalent_age_ns"] for row in blocks.values()),
                                           default=None),
            "max_equivalent_age_ns": max((row["equivalent_age_ns"] for row in blocks.values()),
                                           default=None),
            "successful_age_resets": sum(row.get("last_refresh_commit_ns") is not None
                                           for row in blocks.values()),
        },
        "wear": {key: sum(row.get(key, 0) for row in wear.values()) for key in (
            "block_program_work_started", "block_program_work_completed",
            "nand_page_programs_started", "nand_page_programs_completed",
            "erase_phase_started", "erase_completed")},
        "limitations": driver.get("limitations", []),
    }


def analyze_point(point: Path) -> dict:
    point = Path(point)
    config_path, manifest_path = point / "config.json", point / "manifest.json"
    done_path, failed_path = point / "DONE.json", point / "FAILED.json"
    if done_path.exists() and failed_path.exists():
        raise ValueError("point has both DONE and FAILED terminal receipts")
    if failed_path.exists() and (not config_path.is_file() or not manifest_path.is_file()):
        return {"schema_version": "eq3-extension-analysis-v1",
                "analysis_status": "FAILED_RUN_PRESERVED_NOT_ANALYZED",
                "scientific_pass": "NOT_ASSESSED",
                "identity": {"point": str(point), "availability": "PARTIAL_STARTUP_IDENTITY",
                             "config_present": config_path.is_file(),
                             "manifest_present": manifest_path.is_file()},
                "failure_receipt": _load(failed_path), "panels": None}
    if not config_path.is_file() or not manifest_path.is_file():
        raise ValueError("point lacks config.json or manifest.json")
    config, manifest = _load(config_path), _load(manifest_path)
    identity = _identity(point, config, manifest)
    if failed_path.exists():
        return {"schema_version": "eq3-extension-analysis-v1",
                "analysis_status": "FAILED_RUN_PRESERVED_NOT_ANALYZED",
                "scientific_pass": "NOT_ASSESSED", "identity": identity,
                "failure_receipt": _load(failed_path), "panels": None}
    if not done_path.exists():
        return {"schema_version": "eq3-extension-analysis-v1",
                "analysis_status": "INCOMPLETE_NO_TERMINAL_RECEIPT",
                "scientific_pass": "NOT_ASSESSED", "identity": identity,
                "panels": None}
    done = _load(done_path)
    if done.get("status") != "COMPLETED":
        raise ValueError("DONE receipt is not COMPLETED")
    windows_path = point / "windows.jsonl"
    if not windows_path.is_file():
        raise ValueError("completed point lacks windows.jsonl")
    rows = _rows(windows_path)
    try:
        first = next(rows)
    except StopIteration as error:
        raise ValueError("completed point has no raw windows") from error
    runner = "causal" if "executor" in first else "maintenance"
    all_rows = [first, *rows]
    active_ns = config.get("active_ns", config.get("workload", {}).get("active_ns", 0))
    recovery_ns = config.get("recovery_ns", 0)
    total_end = _nonnegative_int(active_ns, "configured active_ns") + \
        _nonnegative_int(recovery_ns, "configured recovery_ns")
    if total_end == 0:
        raise ValueError("configured duration must be positive")
    expected_start = 0
    times, physical_hbf, effective_hbf = [], [], []
    gpu_k, hbf_k, hbm_k = [], [], []
    hbf_state, hbm_state = [], []
    refresh, retry, migration = [], [], []
    backlog_series, queue_series, token_rate = [], [], []
    total_energy = 0.0
    prior_thermal_energy = 0.0
    completion_ids = set()
    completion_group_hashes = set()
    completion_count = 0
    uniqueness = "EXACT_JOB_IDS"
    raw_terminal, causal_events = [], Counter()
    causal_retry_count = 0
    offered_by_stack, delivered_by_stack = defaultdict(int), defaultdict(int)
    last_backlog_by_stack = {}
    cumulative_tokens = 0
    for index, row in enumerate(all_rows):
        start, end = _interval(row, f"window {index}")
        if start != expected_start:
            raise ValueError("raw windows are not contiguous from zero")
        expected_start = end
        duration = end - start
        times.append((start + end) / 2e9)
        thermal = row.get("thermal")
        if not isinstance(thermal, dict) or _interval(thermal, "thermal") != (start, end):
            raise ValueError("thermal timeline differs from raw window")
        energy = row.get("energy")
        if not isinstance(energy, dict):
            raise ValueError("window lacks energy receipt")
        component = energy.get("component_energy_j")
        if not isinstance(component, dict):
            raise ValueError("energy receipt lacks component map")
        component_values = [_number(value, "component energy") for value in component.values()]
        if any(value < 0 for value in component_values):
            raise ValueError("component energy must be nonnegative")
        window_energy = sum(component_values)
        if not math.isclose(window_energy, _number(energy.get("total_j"), "energy total"),
                            rel_tol=1e-12, abs_tol=1e-12):
            raise ValueError("component energy does not sum to total_j")
        total_energy += window_energy
        cumulative = _number(thermal["energy_j"]["cumulative"]["total_input_j"],
                             "thermal cumulative energy")
        if cumulative + 1e-12 < prior_thermal_energy \
                or not math.isclose(cumulative, total_energy, rel_tol=1e-10, abs_tol=1e-9):
            raise ValueError("thermal cumulative energy differs from activity energy timeline")
        prior_thermal_energy = cumulative
        temperature = _temperature_groups(thermal.get("temperatures", {}))
        gpu_k.append(temperature["gpu"]); hbf_k.append(temperature["hbf"])
        hbm_k.append(temperature["hbm"])
        states = row.get("control", {}).get("observed_states")
        if not isinstance(states, dict):
            raise ValueError("control receipt lacks observed states")
        hbf_state.append(_state_group(states, "hbf")); hbm_state.append(_state_group(states, "hbm"))
        activities = _activities(row, runner)
        media_hbf_bytes = sum(item["bytes"] for item in activities
                              if str(item.get("stack", "")).startswith("hbf")
                              and item.get("phase") in MEDIA_PHASES)
        physical_hbf.append(media_hbf_bytes * 1e9 / duration)
        refresh_bytes = sum(item["bytes"] for item in activities
                            if item.get("operation") in {"refresh_read", "program", "erase"})
        retry_bytes = sum(item["bytes"] for item in activities
                          if item.get("operation") in {"retry", "retry_internal"}
                          and item.get("phase") == "media_read")
        migration_bytes = sum(item["bytes"] for item in activities
                              if item.get("operation") in {"migration", "migration_program"})
        refresh.append(refresh_bytes * 1e9 / duration)
        retry.append(retry_bytes * 1e9 / duration)
        migration.append(migration_bytes * 1e9 / duration)
        if runner == "maintenance":
            service = row["service"]
            effective = 0; backlog = 0; queue = 0
            for stack, receipt in service["stacks"].items():
                offered = _nonnegative_int(receipt["offered_effective_bytes"], "offered bytes")
                delivered = _nonnegative_int(receipt["delivered_effective_bytes"], "delivered bytes")
                remaining = _nonnegative_int(receipt["backlog_effective_bytes"], "backlog bytes")
                if receipt["cumulative_offered_effective_bytes"] != \
                        receipt["cumulative_delivered_effective_bytes"] + remaining:
                    raise ValueError("maintenance service byte conservation failed")
                offered_by_stack[stack] += offered; delivered_by_stack[stack] += delivered
                last_backlog_by_stack[stack] = remaining
                if stack.startswith("hbf"):
                    effective += delivered; backlog += remaining
            for receipt in (service, row.get("independent_maintenance_service")):
                if receipt is None:
                    continue
                for job_id in receipt.get("completion_ids", []):
                    if job_id in completion_ids:
                        raise ValueError("duplicate maintenance completion ID")
                    completion_ids.add(job_id); completion_count += 1
                queue += sum(item.get("remaining_bytes", 0)
                             for item in receipt.get("job_progress", []))
            delta = row.get("maintenance_delta") or {}
            terminals = delta.get("terminal_results", [])
            for terminal in terminals:
                if any(existing["operation_id"] == terminal["operation_id"]
                       for existing in raw_terminal):
                    raise ValueError("duplicate maintenance terminal operation")
                raw_terminal.append(terminal)
            effective_hbf.append(effective * 1e9 / duration)
            backlog_series.append(backlog); queue_series.append(queue); token_rate.append(None)
        else:
            facts = row["control"].get("stack_facts", {})
            effective = backlog = 0
            for stack, fact in facts.items():
                offered = _nonnegative_int(fact["offered_bytes"], "causal offered bytes")
                delivered = _nonnegative_int(fact["delivered_bytes"], "causal delivered bytes")
                remaining = _nonnegative_int(fact["backlog_bytes"], "causal backlog bytes")
                offered_by_stack[stack] += offered; delivered_by_stack[stack] += delivered
                last_backlog_by_stack[stack] = remaining
                if stack.startswith("hbf"):
                    effective += delivered; backlog += remaining
            progress = row["service"].get("changed_job_progress", [])
            queue = sum(_nonnegative_int(item.get("remaining_bytes", 0), "job remaining")
                        for item in progress)
            grouped = row.get("output_granularity", "").startswith("UNIFORM_")
            for item in row["service"].get("completions", []):
                if grouped:
                    group_hash = item.get("job_ids_sha256")
                    if not isinstance(group_hash, str) or len(group_hash) != 64:
                        raise ValueError("grouped causal completion lacks identity hash")
                    if group_hash in completion_group_hashes:
                        raise ValueError("duplicate grouped causal completion identity hash")
                    completion_group_hashes.add(group_hash)
                    completion_count += _nonnegative_int(item["count"], "completion count")
                    uniqueness = "AGGREGATED_JOB_ID_HASH_ONLY"
                else:
                    job_id = item["job_id"]
                    if job_id in completion_ids:
                        raise ValueError("duplicate causal completion ID")
                    completion_ids.add(job_id); completion_count += 1
            events = row["executor"].get("events", [])
            event_migration_bytes = 0
            for event in events:
                causal_events[event["kind"]] += int(event.get("count", 1))
                causal_retry_count += int(event.get("retry_count", 0))
                if event["kind"].startswith("migration"):
                    event_migration_bytes += int(event.get("completed_bytes",
                                                            event.get("bytes", 0)))
            if event_migration_bytes:
                migration_bytes = event_migration_bytes
            maintenance_row = row.get("maintenance", {})
            for delta in [*maintenance_row.get("receipt_deltas", []),
                          maintenance_row.get("driver", {})]:
                for terminal in delta.get("terminal_results", []):
                    if any(existing["operation_id"] == terminal["operation_id"]
                           for existing in raw_terminal):
                        raise ValueError("duplicate causal maintenance terminal operation")
                    raw_terminal.append(terminal)
            completed = _nonnegative_int(row["executor"]["completed_tokens"], "completed tokens")
            cumulative = _nonnegative_int(row["executor"]["cumulative_completed_tokens"],
                                          "cumulative tokens")
            if cumulative != cumulative_tokens + completed:
                raise ValueError("causal token cumulative timeline is inconsistent")
            cumulative_tokens = cumulative
            effective_hbf.append(effective * 1e9 / duration)
            backlog_series.append(backlog); queue_series.append(queue)
            token_rate.append(completed * 1e9 / duration)
            migration[-1] = migration_bytes * 1e9 / duration
    if expected_start != total_end:
        raise ValueError("raw timeline does not cover configured duration")
    summary = done.get("summary", {})
    if not math.isclose(total_energy, _number(summary.get("energy_j"), "summary energy"),
                        rel_tol=1e-10, abs_tol=1e-9):
        raise ValueError("DONE energy differs from raw timeline")
    hbf = [stack for stack in offered_by_stack if stack.startswith("hbf")]
    if runner == "maintenance":
        offered = sum(offered_by_stack[s] for s in hbf)
        delivered = sum(delivered_by_stack[s] for s in hbf)
        backlog = sum(last_backlog_by_stack[s] for s in hbf)
        if (offered, delivered, backlog) != (
                summary.get("offered_bytes"), summary.get("delivered_bytes"),
                summary.get("backlog_bytes")):
            raise ValueError("maintenance DONE bytes differ from raw HBF timeline")
        final_path = point / "maintenance-final.json"
        final = None
        if config.get("maintenance", {}).get("mode") != "disabled":
            if not final_path.is_file() or summary.get("maintenance_final_sha256") != _sha256(final_path):
                raise ValueError("maintenance final receipt identity mismatch")
            final = _load(final_path)
        maintenance = _driver_summary(final, raw_terminal)
        tokens = {"availability": "UNAVAILABLE_RATE_WORKLOAD_HAS_NO_TOKEN_DEPENDENCY_DAG"}
        consumers = {"availability": "NOT_APPLICABLE_RATE_WORKLOAD"}
    else:
        expected_offered = summary.get("offered_useful_bytes_by_stack", {})
        expected_delivered = summary.get("delivered_useful_bytes_by_stack", {})
        stacks = set(offered_by_stack) | set(expected_offered) | set(expected_delivered)
        if ({stack: offered_by_stack[stack] for stack in stacks} !=
                {stack: expected_offered.get(stack, 0) for stack in stacks} or
                {stack: delivered_by_stack[stack] for stack in stacks} !=
                {stack: expected_delivered.get(stack, 0) for stack in stacks}):
            raise ValueError("causal DONE useful bytes differ from raw control facts")
        if cumulative_tokens != summary.get("completed_tokens"):
            raise ValueError("causal DONE token count differs from raw timeline")
        trace_origin = summary.get("trace_origin")
        if not isinstance(trace_origin, str) or not trace_origin:
            raise ValueError("causal DONE lacks trace origin")
        provenance = summary.get("structure_provenance")
        if identity["trace_identity"] is not None:
            if not isinstance(provenance, dict) or provenance.get("trace_sha256") != \
                    identity["trace_identity"]["trace_sha256"]:
                raise ValueError("causal DONE trace provenance differs from bound input")
        if summary.get("pending_external_jobs") == 0 and summary.get("uninstantiated_batches") == 0 \
                and any(delivered_by_stack[s] != offered_by_stack[s] for s in offered_by_stack):
            raise ValueError("closed causal point does not conserve useful bytes")
        final_maintenance = summary.get("maintenance", {"mode": "disabled"})
        maintenance = (_driver_summary(final_maintenance, raw_terminal)
                       if final_maintenance.get("mode") != "disabled" else
                       {"mode": "disabled"})
        tokens = {"availability": "ACTUAL_DAG_TERMINAL_COMPLETIONS",
                  "completed_tokens": cumulative_tokens,
                  "average_tokens_per_s": cumulative_tokens * 1e9 / total_end}
        identity["trace_result"] = {
            "trace_origin": trace_origin, "structure_provenance": provenance}
        consumers = {
            "cache": {key: causal_events[key] for key in sorted(causal_events)
                      if key.startswith("cache_")},
            "prefetch": {key: causal_events[key] for key in sorted(causal_events)
                         if key.startswith("prefetch_")},
            "migration": {key: causal_events[key] for key in sorted(causal_events)
                          if key.startswith("migration_")},
            "retry": {
                "observed_retry_count": (
                    None if config.get("hbf_read_cost_proxy", {}).get("mode")
                    in {"conditional_nand_history_v1", "conditional_temperature_retry_v1"}
                    else causal_retry_count),
                "count_semantics": (
                    "UNKNOWN_INTEGER_COUNT_EXPECTED_WORK_PROXY"
                    if config.get("hbf_read_cost_proxy", {}).get("mode")
                    in {"conditional_nand_history_v1", "conditional_temperature_retry_v1"}
                    else "RECORDED_EXECUTOR_RETRY_EVENTS"),
            },
            "semantics": "ACTUAL_RECORDED_CONSUMER_EVENTS_ZERO_MEANS_NOT_OBSERVED_IN_POINT",
        }
    return {
        "schema_version": "eq3-extension-analysis-v1", "runner": runner,
        "analysis_status": "VALIDATED_COMPLETE_RECEIPTS",
        "scientific_pass": "NOT_ASSESSED",
        "identity": identity,
        "checks": {"timeline": "PASS", "byte_conservation": "PASS",
                   "energy_to_thermal": "PASS", "terminal_uniqueness": uniqueness,
                   "completion_count": completion_count},
        "maintenance": maintenance, "causal_tokens": tokens,
        "causal_consumers": consumers,
        "panels": {
            "time_s": times,
            "physical_effective_hbf_traffic": {
                "physical_media_Bps": physical_hbf, "effective_useful_Bps": effective_hbf},
            "owner_temperature_k": {"gpu": gpu_k, "hbm_max": hbm_k, "hbf_max": hbf_k},
            "controller_state_rank": {"labels": STATE_RANK, "hbm_worst": hbm_state,
                                      "hbf_worst": hbf_state},
            "maintenance_retry_migration_Bps": {"refresh": refresh, "retry": retry,
                                                  "migration": migration},
            "queue_backlog_bytes": {"logical_backlog": backlog_series,
                                    "changed_external_remaining": queue_series},
            "causal_token_rate": {"tokens_per_s": token_rate,
                                  "maintenance_semantics": (
                                      "UNAVAILABLE" if runner == "maintenance" else "NOT_APPLICABLE")},
        },
        "interpretation_limits": [
            "VALIDATED_COMPLETE_RECEIPTS_IS_NOT_A_SCIENTIFIC_MODEL_PASS",
            "PHYSICAL_TRAFFIC_IS_RECORDED_MODEL_MEDIA_ACTIVITY_NOT_NATIVE_NAND_WIRE_BYTES",
            "CHANGED_EXTERNAL_REMAINING_IS_A_DELTA_DIAGNOSTIC_NOT_ALWAYS_TOTAL_QUEUE",
        ],
    }


def plot_panels(analysis: dict, output: Path) -> None:
    if analysis.get("analysis_status") != "VALIDATED_COMPLETE_RECEIPTS":
        raise ValueError("plots require validated complete receipts")
    os.environ.setdefault("MPLCONFIGDIR", "/tmp/eq3-matplotlib-cache")
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    p, time = analysis["panels"], analysis["panels"]["time_s"]
    fig, axes = plt.subplots(6, 1, figsize=(11, 15), sharex=True)
    for name, values in p["physical_effective_hbf_traffic"].items(): axes[0].plot(time, values, label=name)
    for name, values in p["owner_temperature_k"].items():
        if any(value is not None for value in values): axes[1].plot(time, values, label=name)
    for name in ("hbf_worst", "hbm_worst"):
        values = p["controller_state_rank"][name]
        if any(value is not None for value in values): axes[2].step(time, values, where="mid", label=name)
    for name, values in p["maintenance_retry_migration_Bps"].items(): axes[3].plot(time, values, label=name)
    for name, values in p["queue_backlog_bytes"].items(): axes[4].plot(time, values, label=name)
    tokens = p["causal_token_rate"]["tokens_per_s"]
    if any(value is not None for value in tokens): axes[5].plot(time, tokens, label="causal tokens/s")
    else: axes[5].text(.5, .5, "UNAVAILABLE for rate workload", ha="center", transform=axes[5].transAxes)
    labels = ("HBF traffic (B/s)", "Owner temperature (K)", "Controller state rank",
              "Refresh/retry/migration (B/s)", "Queue/backlog (bytes)", "Causal token rate")
    for axis, label in zip(axes, labels):
        axis.set_ylabel(label); axis.grid(True, alpha=.25)
        if axis.lines: axis.legend(loc="best", fontsize=8)
    axes[-1].set_xlabel("Simulation time (s)")
    fig.tight_layout(); fig.savefig(output, dpi=160); plt.close(fig)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--point", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    try:
        result = analyze_point(args.point)
        (args.output / "analysis.json").write_text(
            json.dumps(result, indent=2, sort_keys=True, allow_nan=False) + "\n")
        if result["analysis_status"] == "VALIDATED_COMPLETE_RECEIPTS":
            plot_panels(result, args.output / "six-panel.png")
        terminal = "DONE.json"
    except BaseException as error:
        (args.output / "ANALYSIS_FAILED.json").write_text(json.dumps({
            "status": "ANALYSIS_FAILED", "error": repr(error),
            "source_point_preserved": True}, indent=2) + "\n")
        raise
    (args.output / terminal).write_text(json.dumps({
        "status": "COMPLETED", "analysis_status": result["analysis_status"],
        "scientific_pass": "NOT_ASSESSED"}, indent=2) + "\n")


if __name__ == "__main__":
    main()
