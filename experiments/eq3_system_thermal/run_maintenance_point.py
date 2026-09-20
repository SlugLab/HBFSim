#!/usr/bin/env python3
"""Separate aggregate maintenance/rate/thermal point runner.

This does not modify or replace the frozen base runner.  Maintenance is an
aggregate rate-service scenario, not native MQSim NAND execution.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import platform
import resource
import subprocess
import sys
import time

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
MAINTENANCE = ROOT / "experiments" / "eq3_maintenance"
sys.path.insert(0, str(MAINTENANCE))

from thermal_client import ThermalService
from read_rate_policy import EngineeringProfile, StackWindowFacts, WindowFacts
from endpoint_policy import EndpointAwarePolicy
from energy import EnergyMapper
from maintenance_driver import MaintenanceDriver
from rate_workload import RateWorkload
from reliability import ReliabilityLedger
from run_system_point import channel_map, digest, energy_activities, percentile, save
from topology_service import TopologyService


WINDOW_NS = 20_000_000
MODES = {"disabled", "shared", "ideal_independent"}


def _maintenance(config, mapping):
    raw = config["maintenance"]
    required = {
        "mode", "ea_ev", "refresh_trigger", "initial_equivalent_age_ns",
        "initial_wall_age_ns", "initial_temperature_k", "block_bytes",
        "pages_per_block", "aged_blocks_per_stack", "spares_per_channel",
        "max_blocks_per_cohort", "program_energy_j_per_byte",
        "erase_energy_j_per_operation", "energy_evidence",
    }
    if not isinstance(raw, dict) or set(raw) != required:
        raise ValueError("maintenance config has missing or unknown fields")
    if raw["mode"] not in MODES:
        raise ValueError("unknown maintenance mode")
    if raw["mode"] == "disabled":
        return None, None, {}
    blocks = raw["aged_blocks_per_stack"]
    spares = raw["spares_per_channel"]
    if any(isinstance(value, bool) or not isinstance(value, int) or value <= 0
           for value in (blocks, spares, raw["block_bytes"], raw["pages_per_block"],
                         raw["max_blocks_per_cohort"])):
        raise ValueError("maintenance geometry/count fields must be positive integers")
    ledger = ReliabilityLedger({"ea_ev": raw["ea_ev"],
                                "refresh_trigger": raw["refresh_trigger"],
                                "initial_equivalent_age_ns": raw["initial_equivalent_age_ns"],
                                "initial_wall_age_ns": raw["initial_wall_age_ns"]})
    pools, rows, by_channel = {}, [], {}
    for stack, channels in sorted(mapping.items()):
        ordered = sorted(channels, key=int)
        if blocks % len(ordered):
            raise ValueError("aged_blocks_per_stack must divide configured channels")
        per_channel = blocks // len(ordered)
        pools[stack] = {}
        for channel in ordered:
            pools[stack][channel] = [f"{stack}:ch{channel}:spare{i}" for i in range(spares)]
            identities = []
            for index in range(per_channel):
                extent = f"{stack}:ch{channel}:extent{index}"
                identities.append(extent)
                rows.append({"extent_id": extent, "stack": stack, "channel": channel,
                             "source_block_id": f"{stack}:ch{channel}:source{index}",
                             "version": 0})
            by_channel[(stack, channel)] = identities
    driver = MaintenanceDriver(ledger, {
        "block_bytes": raw["block_bytes"], "pages_per_block": raw["pages_per_block"],
        "max_blocks_per_cohort": raw["max_blocks_per_cohort"],
        "spare_block_ids_by_stack_channel": pools,
        "program_energy_j_per_byte": raw["program_energy_j_per_byte"],
        "erase_energy_j_per_operation": raw["erase_energy_j_per_operation"],
        "energy_evidence": raw["energy_evidence"],
    })
    driver.register_extents(rows)
    return ledger, driver, by_channel


def _erase_energy(receipts, mapping, block_bytes, coefficient):
    result = defaultdict_float()
    for receipt in receipts:
        for row in receipt["activities"]:
            if row["phase"] != "media_erase":
                continue
            component = mapping[row["stack"]][str(row["channel"])]
            result[component] += row["bytes"] / block_bytes * coefficient
    return dict(result)


def defaultdict_float():
    from collections import defaultdict
    return defaultdict(float)


def _compact_reliability(ledger, driver, now_ns):
    if ledger is None:
        return {"mode": "disabled", "status": "FRESH_BASELINE_NO_MAINTENANCE"}
    states = ledger._blocks  # bounded internal read; avoids a full deepcopy every window
    return {
        "mode": "enabled", "block_count": len(states),
        "max_equivalent_age_ns": max((row["equivalent_age_ns"] for row in states.values()),
                                     default=0),
        "due_block_count": sum(bool(ledger.due_reasons(block, now_ns))
                               for block in states),
        "active_extent_count": len(driver.active_extents),
        "outstanding_job_count": len(driver.outstanding),
        "free_spare_count": sum(len(pool) for pool in driver.free_spares.values()),
        "quarantined_block_count": len(driver.quarantined_blocks),
    }


def _maintenance_pressure(ledger, extent_ids, now_ns, block_bytes):
    if ledger is None:
        return 0, None
    due = 0
    deadlines = []
    for extent_id in extent_ids:
        state = ledger._blocks[extent_id]
        deadlines.append(state["next_wall_due_ns"])
        if ledger.due_reasons(extent_id, now_ns):
            due += block_bytes
            deadlines.append(now_ns)
    return due, min(deadlines) if deadlines else None


def _receipt_output_delta(receipt):
    """Project external progress to changed/current-terminal rows for raw output.

    The full receipt is consumed by energy, maintenance, and control first.  A
    stalled external job remains represented by ``blocked`` and by the
    driver's outstanding summary; repeating its unchanged cumulative progress
    in every 20 ms raw row adds no observation.
    """
    result = dict(receipt)
    end_ns = receipt["end_ns"]
    result["job_progress"] = [row for row in receipt.get("job_progress", [])
                              if row["admitted_this_window_bytes"] > 0
                              or row["served_this_window_bytes"] > 0
                              or row["completion_ns"] == end_ns]
    result["output_projection"] = (
        "CHANGED_OR_CURRENT_TERMINAL_EXTERNAL_PROGRESS;BLOCKED_ROWS_RETAINED"
    )
    return result


def execute(config, normalized, thermal, sink):
    service = TopologyService(config["service"])
    mapping = channel_map(normalized, config["service"])
    workload = RateWorkload(mapping, config["workload"])
    energy = EnergyMapper(normalized, mapping, config["energy"])
    ledger, driver, extents_by_channel = _maintenance(config, mapping)
    extents_by_stack = {stack: [] for stack in config["service"]["channels"]}
    if driver:
        for extent_id, extent in driver.extents.items():
            extents_by_stack[extent["stack"]].append(extent_id)
    maintenance_mode = config["maintenance"]["mode"]
    independent = (TopologyService(config["service"])
                   if maintenance_mode == "ideal_independent" else None)
    stacks = sorted(config["service"]["channels"])
    hbf = sorted(mapping)
    baseline = {s: sum(config["service"]["channels"][s].values()) * WINDOW_NS // 10**9
                for s in stacks}
    budgets, states = dict(baseline), {s: "normal" for s in stacks}
    profiles = {s: EngineeringProfile(
        profile_id=config["point_id"] + ":" + s, enabled=True,
        strategy=config["strategy"], window_ns=WINDOW_NS,
        target_bytes_per_s=min(config["workload"]["per_stack_Bps"],
                               sum(config["service"]["channels"][s].values()) * 4 // 5),
        step_bytes=baseline[s] // 20, minimum_budget_bytes=baseline[s] // 10,
        maximum_budget_bytes=baseline[s], severe_budget_bytes=0, light_fraction=.5)
        for s in stacks}
    policies = {s: EndpointAwarePolicy(profile) for s, profile in profiles.items()}
    previous_die_k = {component: float(config["maintenance"]["initial_temperature_k"])
                      for channels in mapping.values() for component in channels.values()}
    total_energy, peak = 0.0, {}
    end = config["workload"]["active_ns"] + config["recovery_ns"]
    if end % WINDOW_NS:
        raise ValueError("run duration must align with thermal window")
    final_receipt = None
    for start in range(0, end, WINDOW_NS):
        stop = start + WINDOW_NS
        offered = workload.advance(start, stop)
        jobs = driver.poll(start) if driver else []
        if maintenance_mode == "ideal_independent":
            receipt = service.advance(start, stop, offered, budgets, states)
            maintenance_receipt = independent.advance(
                start, stop, {}, baseline, states, jobs)
            receipts = [receipt, maintenance_receipt]
        else:
            receipt = service.advance(start, stop, offered, budgets, states, jobs)
            maintenance_receipt = None
            receipts = [receipt]
        activities = []
        for item in receipts:
            activities.extend(row for row in energy_activities(item)
                              if row["operation"] != "erase")
        mapped = energy.map(activities,
                            gpu_external_j=config.get("gpu_external_w", 0) * WINDOW_NS / 1e9)
        if driver:
            erase = _erase_energy(receipts, mapping, config["maintenance"]["block_bytes"],
                                  config["maintenance"]["erase_energy_j_per_operation"])
            for component, joules in erase.items():
                mapped["component_energy_j"][component] = (
                    mapped["component_energy_j"].get(component, 0.0) + joules)
                mapped["scope_energy_j"]["erase:array"] = (
                    mapped["scope_energy_j"].get("erase:array", 0.0) + joules)
                mapped["total_j"] += joules
        total_energy += mapped["total_j"]
        heat = thermal.advance(start, stop, mapped["component_energy_j"])
        maintenance_delta = None
        if driver:
            entities = heat["entity_temperatures_k"]
            for key, extent_ids in extents_by_channel.items():
                component = mapping[key[0]][key[1]]
                current = float(entities[component]["hotspot_k"])
                midpoint = (previous_die_k[component] + current) / 2
                ledger.advance_temperature_many(extent_ids, start, stop, midpoint)
                previous_die_k[component] = current
            maintenance_delta = driver.consume_receipt(
                maintenance_receipt if maintenance_receipt is not None else receipt)
            maintenance_delta["reliability_events"] = ledger.drain_events()
        for owner, temperature in heat["temperatures"].items():
            peak[owner] = max(peak.get(owner, temperature), temperature)
        observed = {s: heat["stack_states"][s] for s in stacks}
        next_budgets, decisions = {}, {}
        for stack in stacks:
            row = receipt["stacks"][stack]
            delivered, backlog = row["delivered_effective_bytes"], row["backlog_effective_bytes"]
            utilization = min(1.0, delivered / baseline[stack])
            due_bytes, due_deadline = _maintenance_pressure(
                ledger, extents_by_stack.get(stack, ()), stop,
                config["maintenance"]["block_bytes"])
            facts = StackWindowFacts(
                stack_id=stack, offered_bytes=row["offered_effective_bytes"],
                delivered_bytes=delivered, backlog_bytes=backlog,
                oldest_wait_ns=row["oldest_wait_ns"] or 0,
                latency_p95_ns=percentile(row["delivered_delay_histogram_bytes"]),
                censored_requests=0, gate_limited=backlog > 0 and delivered >= budgets[stack],
                backend_busy_fraction=utilization, resource_busy=utilization >= 1,
                maintenance_due_bytes=due_bytes,
                maintenance_earliest_deadline_ns=due_deadline)
            decision = policies[stack].evaluate(WindowFacts(
                start_ns=start, end_ns=stop, guard_state=observed[stack], stacks=(facts,),
                current_budget_bytes={stack: budgets[stack]}, guard_states={stack: observed[stack]},
                hysteresis_budget_bytes={stack: heat["hysteresis_budget_bytes"][stack]}))
            next_budgets[stack] = decision.stack_decisions[0].budget_bytes
            decisions[stack] = asdict(decision)
        if config.get("control_disabled", False):
            next_budgets, next_states = dict(baseline), {s: "normal" for s in stacks}
        else:
            next_states = observed
        sink.write(json.dumps({
            "start_ns": start, "end_ns": stop,
            "service": _receipt_output_delta(receipt),
            "independent_maintenance_service": (
                None if maintenance_receipt is None else
                _receipt_output_delta(maintenance_receipt)),
            "maintenance_delta": maintenance_delta,
            "reliability": _compact_reliability(ledger, driver, stop),
            "energy": mapped, "thermal": heat,
            "control": {"budgets": budgets, "next_budgets": next_budgets,
                        "observed_states": observed, "decisions": decisions,
                        "facts": "MODELLED_FLUID_NOT_NATIVE_BUSY_OR_BACKEND_LATENCY"},
        }, separators=(",", ":"), allow_nan=False) + "\n")
        budgets, states = next_budgets, next_states
        final_receipt = receipt
    delivered = sum(final_receipt["stacks"][s]["cumulative_delivered_effective_bytes"]
                    for s in hbf)
    backlog = sum(final_receipt["stacks"][s]["backlog_effective_bytes"] for s in hbf)
    if workload.total != delivered + backlog:
        raise AssertionError("foreground effective-byte conservation failed")
    thermal_energy = heat["energy_j"]["cumulative"]["total_input_j"]
    if abs(thermal_energy - total_energy) > 1e-9 * max(1, total_energy):
        raise AssertionError("activity-to-thermal energy mismatch")
    final = None if driver is None else {
        "driver": driver.snapshot(), "reliability": ledger.snapshot(),
        "mode": maintenance_mode,
        "independent_semantics": ("IDEAL_INDEPENDENT_AGGREGATE_RESOURCE_PROXY_NOT_SECOND_NATIVE_ENGINE"
                                  if independent else None),
    }
    return ({"offered_bytes": workload.total, "delivered_bytes": delivered,
             "backlog_bytes": backlog, "energy_j": total_energy,
             "peak_k_by_stack": peak, "service_facts": service.immutable_facts(),
             "maintenance_mode": maintenance_mode,
             "maintenance_scope": "AGGREGATE_RATE_SERVICE_NOT_NATIVE_NAND",
             "token_throughput": "UNAVAILABLE_RATE_WORKLOAD_HAS_NO_TOKEN_DEPENDENCY_DAG"}, final)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("config", "model-dir", "thermal-binary", "artifact-root", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    started = time.monotonic()
    try:
        for key in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS",
                    "NUMEXPR_NUM_THREADS"):
            os.environ[key] = "1"
        os.environ["CUDA_VISIBLE_DEVICES"] = ""
        config = json.loads(args.config.read_text())
        save(args.output / "config.json", config)
        manifest = {"started_utc": datetime.now(timezone.utc).isoformat(),
                    "environment_id": "eq3-thermal-cpu-v1", "python": sys.version,
                    "platform": platform.platform(), "input_sha256": digest(args.config),
                    "source_revision": subprocess.check_output(
                        ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
                    "source_sha256": {str(path.relative_to(ROOT)): digest(path) for path in
                                      (HERE / "run_maintenance_point.py",
                                       HERE / "maintenance_driver.py", HERE / "reliability.py",
                                       HERE / "topology_service.py", HERE / "energy.py")},
                    "thermal_binary_sha256": digest(args.thermal_binary),
                    "model_dir": str(args.model_dir.resolve()), "cpu_threads": 1,
                    "gpu_count": 0, "resource_limits": config["resource_limits"]}
        save(args.output / "manifest.json", manifest)
        limit = config["resource_limits"]["address_space_gib"] * 1024**3
        resource.setrlimit(resource.RLIMIT_AS, (limit, limit))
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
        normalized = json.loads((args.model_dir / "normalized.json").read_text())
        baseline = {s: sum(c.values()) * WINDOW_NS // 10**9
                    for s, c in config["service"]["channels"].items()}
        with ThermalService(args.thermal_binary, args.model_dir, args.output / "thermal-process",
                            artifact_root=args.artifact_root, baseline_budgets=baseline,
                            limits=config.get("thermal_limits_k")) as thermal:
            manifest.update(thermal_model_lock=thermal.lock, thermal_header=thermal.header)
            save(args.output / "manifest.json", manifest)
            with (args.output / "windows.jsonl").open("w") as sink:
                summary, final = execute(config, normalized, thermal, sink)
        if final is not None:
            save(args.output / "maintenance-final.json", final)
            summary["maintenance_final_sha256"] = digest(args.output / "maintenance-final.json")
        save(args.output / "DONE.json", {"status": "COMPLETED", "summary": summary,
             "wall_s": time.monotonic() - started,
             "child_peak_rss_kib": resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss})
    except BaseException as error:
        save(args.output / "FAILED.json", {"status": "FAILED", "error": repr(error),
             "wall_s": time.monotonic() - started, "raw_preserved": True})
        raise


if __name__ == "__main__":
    main()
