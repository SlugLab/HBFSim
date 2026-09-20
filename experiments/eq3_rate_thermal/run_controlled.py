#!/usr/bin/env python3
"""Default-disconnected fluid read-rate/thermal feedback experiment runner.

This runner models byte arrivals, FIFO service and incremental read energy.  It
does not issue MQSim commands and does not represent backend completion times.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import platform
import resource
import shutil
import subprocess
import sys
import time
from typing import Any


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
MAINTENANCE = ROOT / "experiments" / "eq3_maintenance"
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(MAINTENANCE))

from read_rate_policy import EngineeringProfile, ReadRatePolicy, StackWindowFacts, WindowFacts
from thermal_client import ThermalService


WINDOW_NS = 20_000_000
BASELINE_STACK_BPS = 1_536_000_000_000
ARRAY_J_PER_BYTE = 40e-12
BASE_J_PER_BYTE = 10e-12
ALLOWED_TOPOLOGIES = {"mixed_direct", "all_hbf_direct"}
ALLOWED_STRATEGIES = {"guard_only", "thermal_hysteresis_guard",
                      "read_rate_feedback_thermal_guard_v1"}


def _save(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, indent=2, allow_nan=False) + "\n")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _plain(value: Any) -> Any:
    if is_dataclass(value):
        return asdict(value)
    if isinstance(value, dict):
        return {key: _plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    return value


def _field(value: Any, key: str) -> Any:
    return value[key] if isinstance(value, dict) else getattr(value, key)


def _validate_inputs(profile: dict, workload: dict, scenario: dict, normalized: dict,
                     strategy: str) -> tuple[list[str], dict[str, dict[str, str]], dict[str, dict[str, int]]]:
    if strategy not in ALLOWED_STRATEGIES:
        raise ValueError("unsupported policy strategy")
    if scenario.get("schema_version") != "eq3-rate-controlled-scenario-v1":
        raise ValueError("unsupported controlled scenario schema")
    if not isinstance(scenario.get("scenario_id"), str) or not scenario["scenario_id"]:
        raise ValueError("scenario_id must be nonempty")
    if set(scenario) != {"schema_version", "scenario_id", "topology", "window_ns",
                        "target_read_Bps_per_stack"}:
        raise ValueError("controlled scenario contains missing or unknown fields")
    if scenario.get("topology") not in ALLOWED_TOPOLOGIES:
        raise ValueError("only mixed_direct and all_hbf_direct are currently supported")
    if profile.get("schema_version") != 1:
        raise ValueError("profile schema_version must equal 1")
    if profile.get("provenance") != "SCENARIO_ASSUMPTION_USER_CONFIRMED":
        raise ValueError("profile provenance is not the frozen user-confirmed rate-energy scenario")
    if profile.get("reference_read_Bps") != 1_600_000_000_000:
        raise ValueError("profile reference_read_Bps must remain 1.6 TB/s")
    if profile.get("array_j_per_byte") != ARRAY_J_PER_BYTE or profile.get("base_j_per_byte") != BASE_J_PER_BYTE:
        raise ValueError("runner requires the user-confirmed 40/10 pJ/B incremental energy profile")
    channel_map = profile.get("channel_map")
    capacities = profile.get("channel_capacity_Bps")
    if not isinstance(channel_map, dict) or not channel_map or set(channel_map) != set(capacities or {}):
        raise ValueError("profile must provide matching channel maps and capacities")
    stacks = sorted(channel_map)
    components = {row.get("id") for row in normalized.get("components", [])}
    checked_capacities = {}
    for stack in stacks:
        if set(channel_map[stack]) != set(capacities[stack]):
            raise ValueError(f"channel map/capacity mismatch for {stack}")
        if set(channel_map[stack].values()) - components or f"{stack}.base" not in components:
            raise ValueError(f"profile mapping is not present in the thermal model for {stack}")
        checked_capacities[stack] = {}
        for channel, raw in capacities[stack].items():
            value = int(raw)
            if value <= 0 or value != raw:
                raise ValueError("channel capacities must be positive integer B/s")
            checked_capacities[stack][channel] = value
    windows = workload.get("windows")
    if workload.get("schema_version") != "eq3-rate-model-workload-v1":
        raise ValueError("unsupported model workload schema")
    workload_metadata = workload.get("metadata", {})
    if (workload_metadata.get("stack_ids") not in (None, stacks) or
            workload_metadata.get("step_ns", WINDOW_NS) != WINDOW_NS):
        raise ValueError("workload metadata stack or step identity differs from the profile")
    if not isinstance(windows, list) or not windows:
        raise ValueError("workload.windows must be a nonempty array")
    expected = 0
    for index, window in enumerate(windows):
        if window.get("start_ns") != expected or window.get("end_ns") != expected + WINDOW_NS:
            raise ValueError(f"workload window {index} is not contiguous 20 ms")
        offered = window.get("stack_channel_offered_bytes")
        if not isinstance(offered, dict) or set(offered) != set(stacks):
            raise ValueError(f"workload window {index} must exactly cover profile stacks")
        for stack, channels in offered.items():
            if set(channels) != set(channel_map[stack]):
                raise ValueError(f"workload window {index} must exactly cover profile channels")
            if any(isinstance(value, bool) or not isinstance(value, int) or value < 0
                   for value in channels.values()):
                raise ValueError("offered byte counts must be nonnegative integers")
        if window.get("total_offered_bytes") != sum(sum(row.values()) for row in offered.values()):
            raise ValueError(f"workload window {index} total_offered_bytes does not conserve")
        expected += WINDOW_NS
    if (workload_metadata.get("active_ns") is not None and
            workload_metadata.get("active_ns") + workload_metadata.get("recovery_ns", 0) != expected):
        raise ValueError("workload active/recovery duration does not match its windows")
    actual_total = sum(window["total_offered_bytes"] for window in windows)
    if workload_metadata.get("actual_total_offered_bytes") not in (None, actual_total):
        raise ValueError("workload metadata total offered bytes does not match its windows")
    target = scenario.get("target_read_Bps_per_stack")
    if isinstance(target, bool) or not isinstance(target, int) or target <= 0:
        raise ValueError("scenario.target_read_Bps_per_stack must be an explicit positive integer")
    if scenario.get("window_ns", WINDOW_NS) != WINDOW_NS:
        raise ValueError("scenario window must remain 20 ms")
    physical = {stack: sum(checked_capacities[stack].values()) for stack in stacks}
    if len(set(physical.values())) != 1 or next(iter(physical.values())) != BASELINE_STACK_BPS:
        raise ValueError("v1 requires the frozen OCP Grade 2 physical capacity of 1.536 TB/s per stack")
    mean_total = workload.get("metadata", {}).get("mean_active_offered_Bps")
    if isinstance(mean_total, bool) or not isinstance(mean_total, int) or mean_total <= 0:
        raise ValueError("workload metadata must state positive integer mean_active_offered_Bps")
    expected_target = max(1, min(mean_total // len(stacks), BASELINE_STACK_BPS * 4 // 5))
    if target != expected_target:
        raise ValueError(
            "scenario target must equal min(mean active offered rate per stack, 0.8 physical capacity)"
        )
    return stacks, channel_map, checked_capacities


def _served_energy(served: dict[str, dict[str, int]], channel_map: dict[str, dict[str, str]]) -> tuple[dict, dict]:
    component = {}
    stacks = {}
    for stack in sorted(channel_map):
        expected = set(channel_map[stack])
        actual = served.get(stack, {})
        if set(actual) != expected:
            raise ValueError(f"fluid served channel coverage mismatch for {stack}")
        total = 0
        array_j = 0.0
        for channel, target in channel_map[stack].items():
            byte_count = actual[channel]
            if isinstance(byte_count, bool) or not isinstance(byte_count, int) or byte_count < 0:
                raise ValueError("fluid served bytes must be nonnegative integers")
            total += byte_count
            value = byte_count * ARRAY_J_PER_BYTE
            component[target] = component.get(target, 0.0) + value
            array_j += value
        base_j = total * BASE_J_PER_BYTE
        component[f"{stack}.base"] = base_j
        stacks[stack] = {"served_bytes": total, "array_energy_j": array_j,
                         "base_energy_j": base_j, "total_energy_j": array_j + base_j}
    return component, stacks


def _fluid_stack_receipts(result: Any) -> dict:
    receipts = _field(result, "stacks")
    return {key: _plain(value) for key, value in receipts.items()}


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
    raise AssertionError("delay histogram percentile did not reach rank")


def execute_loop(*, profile: dict, workload: dict, scenario: dict, normalized: dict,
                 strategy: str, fluid: Any, thermal: Any,
                 sinks: dict[str, Any] | None = None) -> dict:
    """Execute a closed loop with injected fluid and thermal services for fixed tests."""
    stacks, channel_map, _ = _validate_inputs(profile, workload, scenario, normalized, strategy)
    baseline = BASELINE_STACK_BPS * WINDOW_NS // 1_000_000_000
    minimum = baseline // 10
    step = baseline // 20
    target_bps = scenario["target_read_Bps_per_stack"]
    policy_profile = EngineeringProfile(
        profile_id=scenario.get("scenario_id", "UNNAMED") + ":" + strategy,
        enabled=True, strategy=strategy, window_ns=WINDOW_NS,
        target_bytes_per_s=target_bps, target_latency_p95_ns=None,
        tolerance_fraction=0.05, step_bytes=step,
        minimum_budget_bytes=minimum, maximum_budget_bytes=baseline,
        severe_budget_bytes=0, light_fraction=0.5)
    policy = ReadRatePolicy(policy_profile)
    budgets = {stack: baseline for stack in stacks}
    cumulative = {"arrived_bytes": 0, "served_bytes": 0, "energy_j": 0.0}
    peaks = {}
    records = {"rates": [], "control": [], "energy": [], "thermal": []}
    delay_histograms = {stack: {} for stack in stacks}
    maximum_oldest_wait = {stack: 0 for stack in stacks}
    last_thermal = None

    def emit(name: str, row: dict) -> None:
        records[name].append(row)
        if sinks and name in sinks:
            sinks[name].write(json.dumps(row, allow_nan=False) + "\n")
            sinks[name].flush()

    for window in workload["windows"]:
        start_ns, end_ns = window["start_ns"], window["end_ns"]
        offered = window.get("stack_channel_offered_bytes", {})
        fluid_result = fluid.advance(start_ns, end_ns, offered, budgets)
        served = _plain(_field(fluid_result, "served_by_channel"))
        stack_receipts = _fluid_stack_receipts(fluid_result)
        component_energy, stack_energy = _served_energy(served, channel_map)
        offered_total = sum(sum(channels.values()) for channels in offered.values())
        served_total = sum(row["served_bytes"] for row in stack_energy.values())
        for stack, receipt in stack_receipts.items():
            maximum_oldest_wait[stack] = max(maximum_oldest_wait[stack], receipt["oldest_wait_ns"] or 0)
            for bucket in receipt.get("delivered_delay_histogram_bytes", []):
                delay = int(bucket["delay_ns"])
                delay_histograms[stack][delay] = delay_histograms[stack].get(delay, 0) + int(bucket["bytes"])
        cumulative["arrived_bytes"] += offered_total
        cumulative["served_bytes"] += served_total
        window_energy = sum(component_energy.values())
        cumulative["energy_j"] += window_energy
        thermal_result = thermal.advance(start_ns, end_ns, component_energy)
        last_thermal = thermal_result
        for stack, temperature in thermal_result["temperatures"].items():
            peaks[stack] = max(peaks.get(stack, temperature), temperature)

        policy_adapter = {}
        for stack, receipt in stack_receipts.items():
            utilization = receipt["delivered_bytes"] / baseline
            policy_adapter[stack] = {
                "modelled_fluid_capacity_utilization": utilization,
                "modelled_fluid_capacity_saturated": utilization >= 1.0,
                "fluid_byte_weighted_latency_p95_ns": receipt["latency_p95_ns"],
                "utilization_semantics": "MODELLED_FLUID_PHYSICAL_CHANNEL_CAPACITY_NOT_NATIVE_BACKEND_BUSY",
                "latency_semantics": "FLUID_WINDOW_QUANTIZED_BYTE_WEIGHTED_NOT_BACKEND_LATENCY",
            }
        rate_row = {
            "start_ns": start_ns, "end_ns": end_ns,
            "semantics": "MODELLED_FLUID_BYTE_FIFO_NOT_MQSIM_COMPLETION",
            "backend_latency_ns": None,
            "budget_by_stack": dict(budgets),
            "offered_by_channel": offered,
            "served_by_channel": served,
            "stacks": stack_receipts,
            "fluid_receipt": _plain(fluid_result),
            "policy_fact_adapter_by_stack": policy_adapter,
            "window_arrived_bytes": offered_total,
            "window_served_bytes": served_total,
            "cumulative": dict(cumulative),
        }
        emit("rates", rate_row)
        emit("energy", {"start_ns": start_ns, "end_ns": end_ns,
                         "component_energy_j": component_energy,
                         "stack_energy_j": stack_energy,
                         "window_total_j": window_energy,
                         "cumulative_total_j": cumulative["energy_j"]})
        emit("thermal", thermal_result)

        facts = []
        for stack in stacks:
            receipt = stack_receipts[stack]
            facts.append(StackWindowFacts(
                stack_id=stack,
                offered_bytes=int(receipt["offered_bytes"]),
                delivered_bytes=int(receipt["delivered_bytes"]),
                backlog_bytes=int(receipt["backlog_bytes"]),
                oldest_wait_ns=int(receipt["oldest_wait_ns"] or 0),
                latency_p95_ns=receipt["latency_p95_ns"],
                censored_requests=0,
                gate_limited=bool(receipt["backlog_bytes"] > 0 and
                                  receipt["delivered_bytes"] == budgets[stack]),
                backend_busy_fraction=policy_adapter[stack]["modelled_fluid_capacity_utilization"],
                resource_busy=policy_adapter[stack]["modelled_fluid_capacity_saturated"],
                maintenance_due_bytes=0,
                maintenance_earliest_deadline_ns=None,
                retry_count=None,
                uecc_count=None))
        guard_states = {stack: thermal_result["stack_states"][stack] for stack in stacks}
        rank = {"normal": 0, "light": 1, "severe": 2, "shutdown": 3}
        worst = max(guard_states.values(), key=rank.get)
        decision = policy.evaluate(WindowFacts(
            start_ns=start_ns, end_ns=end_ns, guard_state=worst,
            stacks=tuple(facts), current_budget_bytes=dict(budgets),
            hysteresis_budget_bytes={stack: thermal_result["hysteresis_budget_bytes"][stack]
                                     for stack in stacks},
            guard_states=guard_states))
        next_budgets = {row.stack_id: row.budget_bytes for row in decision.stack_decisions}
        serialized_decision = _plain(decision)
        serialized_decision["library_fact_semantics"] = serialized_decision["fact_semantics"]
        serialized_decision["fact_semantics"] = "MODELLED_FLUID_BYTE_DELIVERY_NOT_ACTUAL_FABRIC_DELIVERY"
        emit("control", {"observed_window_start_ns": start_ns,
                          "observed_window_end_ns": end_ns,
                          "applies_to_window_start_ns": end_ns,
                          "causality": "COMPLETED_WINDOW_FACTS_AFFECT_NEXT_WINDOW_ONLY",
                          "policy_fact_adapter_semantics": {
                              "backend_busy_fraction": "MODELLED_FLUID_CAPACITY_UTILIZATION_NOT_NATIVE_BACKEND_BUSY",
                              "resource_busy": "MODELLED_FLUID_PHYSICAL_CAPACITY_SATURATION_NOT_NATIVE_RESOURCE_OBSERVATION",
                              "latency_p95_ns": "FLUID_WINDOW_QUANTIZED_BYTE_WEIGHTED_NOT_BACKEND_LATENCY",
                          },
                          "guard_states": guard_states,
                          "current_budget_bytes": dict(budgets),
                          "decision": serialized_decision,
                          "next_budget_bytes": next_budgets})
        budgets = next_budgets

    final_backlog = sum(int(row["backlog_bytes"]) for row in _fluid_stack_receipts(fluid_result).values())
    if cumulative["arrived_bytes"] != cumulative["served_bytes"] + final_backlog:
        raise AssertionError("end-to-end byte conservation failed")
    return {
        "summary": {
            **cumulative,
            "final_backlog_bytes": final_backlog,
            "byte_conservation_error": cumulative["arrived_bytes"] - cumulative["served_bytes"] - final_backlog,
            "peak_k_by_stack": peaks,
            "final_k_by_stack": last_thermal["temperatures"] if last_thermal else {},
            "final_guard_states": last_thermal["stack_states"] if last_thermal else {},
            "final_budget_bytes": budgets,
            "fluid_wait_by_stack": {
                stack: {
                    "delay_semantics": "FLUID_WINDOW_QUANTIZED_BYTE_WEIGHTED_NOT_BACKEND_LATENCY",
                    "delivered_delay_p95_ns": _weighted_percentile(delay_histograms[stack], 95),
                    "delivered_delay_p99_ns": _weighted_percentile(delay_histograms[stack], 99),
                    "maximum_oldest_backlog_wait_ns": maximum_oldest_wait[stack],
                    "delivered_delay_histogram_bytes": [
                        {"delay_ns": delay, "bytes": byte_count}
                        for delay, byte_count in sorted(delay_histograms[stack].items())
                    ],
                } for stack in stacks
            },
        },
        "policy_profile": _plain(policy_profile),
        "semantics": {
            "service": "MODELLED_FLUID_BYTE_FIFO",
            "policy_fact_semantics": "MODELLED_FLUID_BYTE_DELIVERY_NOT_ACTUAL_FABRIC_DELIVERY",
            "thermal_energy": "SERVED_BYTES_TIMES_USER_CONFIRMED_40_10_PJ_PER_BYTE",
            "backend_latency": "UNKNOWN",
            "policy_capacity_facts": "MODELLED_FLUID_NOT_NATIVE_BACKEND_OBSERVATION",
            "mqsim_or_fabric_completion": False,
            "idle_power": "UNKNOWN_NOT_INCLUDED",
            "gpu_self_power": "ZERO_INCREMENT_NOT_PHYSICAL_IDLE",
            "age_retention_ecc": "UNKNOWN_NOT_INFERRED",
            "maintenance": "UNAVAILABLE_IN_THIS_FLUID_PATH",
        },
        "records": records,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", type=Path, required=True)
    parser.add_argument("--model-dir", type=Path, required=True)
    parser.add_argument("--workload", type=Path, required=True)
    parser.add_argument("--scenario", type=Path, required=True)
    parser.add_argument("--thermal-binary", type=Path, required=True)
    parser.add_argument("--artifact-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--strategy", choices=sorted(ALLOWED_STRATEGIES), required=True)
    parser.add_argument("--address-limit-gib", type=int, default=4)
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    started = time.monotonic()
    try:
        if args.address_limit_gib <= 0:
            raise ValueError("address limit must be positive")
        for key in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
            os.environ[key] = "1"
        os.environ["CUDA_VISIBLE_DEVICES"] = ""
        profile = json.loads(args.profile.read_text())
        workload = json.loads(args.workload.read_text())
        scenario = json.loads(args.scenario.read_text())
        normalized = json.loads((args.model_dir / "normalized.json").read_text())
        stacks, channel_map, capacities = _validate_inputs(profile, workload, scenario, normalized, args.strategy)
        for source, name in ((args.profile, "profile.json"), (args.workload, "workload.json"),
                             (args.scenario, "scenario.json")):
            shutil.copyfile(source, output / name)
        manifest = {
            "experiment_kind": "MODELLED_FLUID_READ_RATE_COUPLED_THERMAL_CONTROL",
            "started_utc": datetime.now(timezone.utc).isoformat(),
            "python": sys.version, "platform": platform.platform(),
            "strategy": args.strategy,
            "environment_id": "eq3-thermal-cpu-v1",
            "source_sha256": {
                "experiments/eq3_rate_thermal/run_controlled.py": _sha256(HERE / "run_controlled.py"),
                "experiments/eq3_rate_thermal/rate_inputs.py": _sha256(HERE / "rate_inputs.py"),
                "experiments/eq3_rate_thermal/fluid_service.py": _sha256(HERE / "fluid_service.py"),
                "experiments/eq3_maintenance/read_rate_policy.py": _sha256(MAINTENANCE / "read_rate_policy.py"),
                "experiments/eq3_maintenance/thermal_client.py": _sha256(MAINTENANCE / "thermal_client.py"),
            },
            "input_sha256": {name: _sha256(output / name) for name in
                             ("profile.json", "workload.json", "scenario.json")},
            "model_dir": str(args.model_dir.resolve()),
            "thermal_binary": str(args.thermal_binary.resolve()),
            "thermal_binary_sha256": _sha256(args.thermal_binary),
            "window_ns": WINDOW_NS, "cpu_threads": 1, "gpu_count": 0,
            "address_limit_gib": args.address_limit_gib,
            "scope": "CONDITIONAL_MODELLED_FLUID_NOT_ACTUAL_MQSIM_OR_FABRIC",
            "backend_latency": "UNKNOWN",
            "supported_topologies": sorted(ALLOWED_TOPOLOGIES),
            "relay_dash": "UNAVAILABLE_IN_THIS_RUNNER",
            "thermal_limits_k": {"hbf": [353.15, 363.15, 378.15],
                                 "gpu": [363.15, 373.15, 383.15]},
            "guard_timing": {"action_delay_ns": WINDOW_NS,
                             "recovery_dwell_ns": 100_000_000, "hysteresis_k": 2.0},
            "disk_free_bytes": shutil.disk_usage(output).free,
        }
        revision = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT,
                                  check=True, text=True, capture_output=True).stdout.strip()
        dirty = subprocess.run(["git", "status", "--porcelain"], cwd=ROOT,
                               check=True, text=True, capture_output=True).stdout
        manifest["source_revision"] = revision
        manifest["source_dirty"] = bool(dirty)
        manifest["source_dirty_paths"] = [line[3:] for line in dirty.splitlines() if len(line) >= 4]
        _save(output / "manifest.json", manifest)
        resource.setrlimit(resource.RLIMIT_AS, (args.address_limit_gib * 1024**3,) * 2)
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
        from fluid_service import FluidService
        fluid = FluidService(channel_map, capacities, window_ns=WINDOW_NS)
        baseline = BASELINE_STACK_BPS * WINDOW_NS // 1_000_000_000
        sinks = {name: (output / f"{name}.jsonl").open("w")
                 for name in ("rates", "control", "energy", "thermal")}
        try:
            with ThermalService(args.thermal_binary, args.model_dir, output / "thermal-process",
                                window_ns=WINDOW_NS, artifact_root=args.artifact_root,
                                baseline_budgets={stack: baseline for stack in stacks},
                                action_delay_ns=WINDOW_NS, recovery_dwell_ns=100_000_000,
                                light_fraction=0.5) as thermal:
                manifest["thermal_model_lock"] = thermal.lock
                manifest["thermal_header"] = thermal.header
                _save(output / "manifest.json", manifest)
                result = execute_loop(profile=profile, workload=workload, scenario=scenario,
                                      normalized=normalized, strategy=args.strategy,
                                      fluid=fluid, thermal=thermal, sinks=sinks)
        finally:
            for stream in sinks.values():
                stream.close()
        _save(output / "manifest.json", manifest)
        final_receipt = result["records"]["thermal"][-1]["energy_j"]["cumulative"]
        mapping_error = final_receipt["total_input_j"] - result["summary"]["energy_j"]
        if abs(mapping_error) > 1e-9 * max(1.0, result["summary"]["energy_j"]):
            raise AssertionError("served-byte energy differs from thermal receipt")
        done = {
            "execution_status": "COMPLETED",
            "capability_status": "MODELLED_FLUID_RATE_TO_INCREMENTAL_ENERGY_TO_COUPLED_THERMAL_CONTROL",
            "scientific_scope": "CONDITIONAL_SIMULATED",
            "strategy": args.strategy,
            "summary": result["summary"],
            "policy_profile": result["policy_profile"],
            "semantics": result["semantics"],
            "thermal_energy_receipt": final_receipt,
            "served_to_thermal_energy_error_j": mapping_error,
            "wall_s": time.monotonic() - started,
            "child_peak_rss_kib": resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss,
        }
        _save(output / "DONE.json", done)
        return 0
    except BaseException as exc:
        _save(output / "FAILED.json", {"execution_status": "FAILED", "error": repr(exc),
                                       "wall_s": time.monotonic() - started,
                                       "evidence": "partial JSONL and thermal transcript retained"})
        raise


if __name__ == "__main__":
    raise SystemExit(main())
