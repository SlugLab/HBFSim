#!/usr/bin/env python3
"""Run one isolated causal storage/control/thermal engineering point."""
from __future__ import annotations

import argparse
from collections import defaultdict
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import json
import math
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

from causal_service import CausalTopologyService
from causal_workload import CausalExecutor, build_architecture_trace
from causal_maintenance_age import CausalMaintenanceAgeAdapter
from endpoint_policy import EndpointAwarePolicy
from maintenance_driver import MaintenanceDriver
from read_rate_policy import EngineeringProfile, StackWindowFacts, WindowFacts
from reliability import ReliabilityLedger
from thermal_client import ThermalService

WINDOW_NS = 20_000_000


def save(path, value):
    Path(path).write_text(json.dumps(value, indent=2, allow_nan=False) + "\n")


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def percentile(samples, pct):
    total = sum(size for _, size in samples)
    if not total:
        return None
    threshold = (total * pct + 99) // 100
    cursor = 0
    for delay, size in sorted(samples):
        cursor += size
        if cursor >= threshold:
            return delay
    raise AssertionError("percentile accumulation failed")


class CausalEnergyAdapter:
    """Map disjoint causal activity phases to thermal component energy."""

    def __init__(self, normalized, service_config, profile, granularity=None):
        self.components = {row["id"] for row in normalized["components"]}
        self.profile = profile
        self.hbf_channels = {}
        self.hbm_dies = defaultdict(list)
        granularity = granularity or {"mode": "physical_channels"}
        if granularity.get("mode") not in {"physical_channels", "uniform_stack_group_16ch"}:
            raise ValueError("unknown causal channel granularity")
        self.granularity = dict(granularity)
        for stack in service_config["fabric"]["hbf"]:
            dies = sorted((row for row in normalized["components"]
                           if row.get("device_id") == stack and row.get("role") == "array_die"),
                          key=lambda row: row["die_index"])
            channels = sorted(service_config["channels"][stack],
                              key=(int if granularity["mode"] == "physical_channels" else str))
            if granularity["mode"] == "physical_channels":
                if len(dies) != len(channels):
                    raise ValueError("causal HBF channel/die mapping must be explicit one-to-one")
                self.hbf_channels[stack] = {channel: [die["id"]]
                                            for channel, die in zip(channels, dies)}
            else:
                if (granularity.get("physical_channels_per_stack") != 16
                        or len(dies) != 16 or "causal_channel_groups" not in service_config):
                    raise ValueError("uniform group requires explicit 16-die/channel evidence")
                self.hbf_channels[stack] = {channel: [die["id"] for die in dies]
                                            for channel in channels}
        for row in normalized["components"]:
            if (str(row.get("physical_type", "")).startswith("HBM")
                    and row.get("role") == "array_die"):
                self.hbm_dies[row["device_id"]].append(row["id"])

    def map(self, activities, *, gpu_compute_j=0.0, gpu_external_j=0.0):
        component = defaultdict(float)
        scope = defaultdict(float)

        def add(owner, joules, name):
            if owner not in self.components:
                raise ValueError(f"energy owner absent from thermal model: {owner}")
            if not math.isfinite(joules) or joules < 0:
                raise ValueError("energy must be finite and nonnegative")
            component[owner] += joules
            scope[name] += joules

        def hbm(stack, size, name, array_j_per_byte, base_j_per_byte):
            dies = self.hbm_dies.get(stack, ())
            if not dies:
                raise ValueError(f"HBM dies unavailable for {stack}")
            for die in dies:
                add(die, size * array_j_per_byte / len(dies), name + ":array_uniform")
            add(stack + ".base", size * base_j_per_byte, name + ":base")

        for row in activities:
            phase, operation = row["phase"], row["operation"]
            stack, size = row["stack"], row["bytes"]
            if phase == "media_read":
                if stack.startswith("hbm"):
                    hbm(stack, size, operation,
                        self.profile["hbm_array_j_per_byte"],
                        self.profile["hbm_base_j_per_byte"])
                else:
                    dies = self.hbf_channels[stack][str(row["channel"])]
                    array_scope = (operation + ":array" if len(dies) == 1
                                   else operation + ":array_uniform_group")
                    for die in dies:
                        add(die, size * self.profile["read_array_j_per_byte"] / len(dies),
                            array_scope)
                    add(stack + ".base", size * self.profile["read_base_j_per_byte"], operation + ":base")
            elif phase == "media_program":
                dies = self.hbf_channels[stack][str(row["channel"])]
                array_scope = (operation + ":array" if len(dies) == 1
                               else operation + ":array_uniform_group")
                for die in dies:
                    add(die, size * self.profile["program_array_j_per_byte"] / len(dies),
                        array_scope)
                add(stack + ".base", size * self.profile["program_base_j_per_byte"], operation + ":base")
            elif phase == "media_erase":
                blocks = size / self.profile["erase_block_bytes"]
                dies = self.hbf_channels[stack][str(row["channel"])]
                erase_scope = ("erase:array_prorated" if len(dies) == 1
                               else "erase:array_prorated_uniform_group")
                for die in dies:
                    add(die, blocks * self.profile["erase_j_per_block"] / len(dies),
                        erase_scope)
            elif phase == "media_fill":
                hbm(stack, size, "hbm_fill",
                    self.profile["hbm_fill_array_j_per_byte"],
                    self.profile["hbm_fill_base_j_per_byte"])
            elif phase in {"relay_receive", "partner_gpu_drain"}:
                partner = row.get("partner", stack)
                coefficient = (self.profile["relay_receive_j_per_byte"]
                               if phase == "relay_receive"
                               else self.profile["relay_send_j_per_byte"])
                add(partner + ".base", size * coefficient, "relay:" + phase)
            elif phase in {"reverse_relay", "partner_gpu_receive"}:
                partner = row.get("partner")
                owner = stack if phase == "reverse_relay" else partner
                coefficient = (self.profile["relay_send_j_per_byte"]
                               if phase == "reverse_relay"
                               else self.profile["relay_receive_j_per_byte"])
                add(owner + ".base", size * coefficient, "migration:" + phase)
        add("gpu", gpu_compute_j, "gpu:causal_compute")
        add("gpu", gpu_external_j, "gpu:independent_external")
        return {"component_energy_j": dict(component), "scope_energy_j": dict(scope),
                "total_j": sum(component.values()),
                "evidence": "CONDITIONAL_CAUSAL_ACTIVITY_ENGINEERING_PROXY_NOT_CALIBRATED"}


def _default_energy_profile(raw):
    defaults = {
        "read_array_j_per_byte": 40e-12, "read_base_j_per_byte": 10e-12,
        "program_array_j_per_byte": 0.05 * 100e-6 / 4096,
        "program_base_j_per_byte": 0.01 * 100e-6 / 4096,
        "hbm_array_j_per_byte": 40e-12, "hbm_base_j_per_byte": 2e-12,
        "hbm_fill_array_j_per_byte": 40e-12, "hbm_fill_base_j_per_byte": 2e-12,
        "relay_receive_j_per_byte": 2e-12, "relay_send_j_per_byte": 2e-12,
        "erase_j_per_block": 50e-6, "erase_block_bytes": 1_048_576,
    }
    result = {**defaults, **raw}
    if any(not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0
           for value in result.values()):
        raise ValueError("invalid causal energy profile")
    return result


def _changed_rows(rows, prior):
    changed = []
    fields = ("cumulative_served_bytes", "remaining_bytes", "unadmitted_bytes",
              "inflight_transferred_bytes", "state", "completion_ns")
    for row in rows:
        signature = tuple(row.get(field) for field in fields)
        if prior.get(row["job_id"]) != signature:
            changed.append(row)
            prior[row["job_id"]] = signature
    for row in changed:
        if row["state"] == "DONE":
            prior.pop(row["job_id"], None)
    return changed


def _identity_hash(values):
    return hashlib.sha256("\n".join(sorted(values)).encode()).hexdigest()


def _aggregate_activities(rows):
    grouped = {}
    keys = ("operation", "stack", "channel", "route", "partner", "phase", "resource")
    for row in rows:
        identity = tuple(row.get(key) for key in keys)
        item = grouped.setdefault(identity, {key: row.get(key) for key in keys})
        item["activity_count"] = item.get("activity_count", 0) + 1
        item["bytes"] = item.get("bytes", 0) + row["bytes"]
        item["first_start_ns"] = min(item.get("first_start_ns", row["start_ns"]), row["start_ns"])
        item["last_end_ns"] = max(item.get("last_end_ns", row["end_ns"]), row["end_ns"])
    return list(grouped.values())


def _aggregate_progress(rows):
    grouped = {}
    for row in rows:
        identity = (row["operation"], row["stack"], row["route"], row["state"])
        item = grouped.setdefault(identity, {
            "operation": row["operation"], "stack": row["stack"],
            "route": row["route"], "state": row["state"], "job_count": 0,
            "total_bytes": 0, "cumulative_served_bytes": 0, "remaining_bytes": 0,
            "unadmitted_bytes": 0, "inflight_transferred_bytes": 0,
        })
        item["job_count"] += 1
        for key in ("total_bytes", "cumulative_served_bytes", "remaining_bytes",
                    "unadmitted_bytes", "inflight_transferred_bytes"):
            item[key] += row[key]
    return list(grouped.values())


def _aggregate_completions(rows):
    grouped = {}
    for row in rows:
        identity = (row["operation"], row["stack"])
        item = grouped.setdefault(identity, {"operation": row["operation"],
                                              "stack": row["stack"], "count": 0,
                                              "bytes": 0, "first_completion_ns": None,
                                              "last_completion_ns": None, "ids": []})
        item["count"] += 1; item["bytes"] += row["bytes"]
        item["ids"].append(row["job_id"])
        item["first_completion_ns"] = (row["completion_ns"] if item["first_completion_ns"] is None
                                        else min(item["first_completion_ns"], row["completion_ns"]))
        item["last_completion_ns"] = (row["completion_ns"] if item["last_completion_ns"] is None
                                       else max(item["last_completion_ns"], row["completion_ns"]))
    result = []
    for item in grouped.values():
        item["job_ids_sha256"] = _identity_hash(item.pop("ids")); result.append(item)
    return result


def _aggregate_observations(events, submissions):
    event_groups = {}
    for row in events:
        kind = row["kind"]
        item = event_groups.setdefault(kind, {"kind": kind, "count": 0,
                                               "completed_bytes": 0, "retry_count": 0})
        item["count"] += 1
        item["completed_bytes"] += int(row.get("completed_bytes", row.get("bytes", 0)))
        item["retry_count"] += int(row.get("retry_count", 0))
    submission_groups = {}
    for row in submissions:
        identity = (row["operation"], row["stack"], row.get("route"))
        item = submission_groups.setdefault(identity, {
            "operation": row["operation"], "stack": row["stack"],
            "route": row.get("route"), "count": 0, "bytes": 0, "ids": []})
        item["count"] += 1; item["bytes"] += row["bytes"]; item["ids"].append(row["job_id"])
    compact_submissions = []
    for item in submission_groups.values():
        item["job_ids_sha256"] = _identity_hash(item.pop("ids")); compact_submissions.append(item)
    return list(event_groups.values()), compact_submissions


def _pending_trace_bytes_by_stack(trace, executor_config):
    """Exact source-placement bytes for arrived but not instantiated batches."""
    targets = executor_config.get("stripe_targets")
    if not targets:
        target = executor_config.get("default_placement")
        targets = [target] if target else None
    if not targets:
        raise ValueError("pending backlog requires explicit stripe targets")
    stripe = int(executor_config["stripe_unit_bytes"])
    result = defaultdict(int)
    for batch in trace["batches"]:
        for task in batch["tasks"]:
            if task["type"] != "storage":
                continue
            size = int(task["tensor"]["bytes"])
            full, tail = divmod(size, stripe)
            base, extra = divmod(full, len(targets))
            for index, target in enumerate(targets):
                child = (base + (index < extra)) * stripe
                if tail and index == full % len(targets):
                    child += tail
                result[target["stack"]] += child
    return dict(result)


def _build_maintenance(config, hbf_channels):
    raw = config.get("maintenance", {"mode": "disabled"})
    if raw.get("mode") == "disabled":
        return None, None, None
    if raw.get("mode") != "shared":
        raise ValueError("causal runner supports disabled or shared maintenance")
    blocks = int(raw["aged_blocks_per_stack"])
    spares = int(raw["spares_per_channel"])
    ledger = ReliabilityLedger({
        "ea_ev": raw["ea_ev"], "refresh_trigger": raw["refresh_trigger"],
        "initial_equivalent_age_ns": raw["initial_equivalent_age_ns"],
        "initial_wall_age_ns": raw["initial_wall_age_ns"],
    })
    pools, extents, temperatures = {}, [], {}
    for stack, channels in sorted(hbf_channels.items()):
        ordered = sorted(channels, key=int)
        if blocks <= 0 or blocks % len(ordered):
            raise ValueError("aged_blocks_per_stack must be positive and divide HBF channels")
        pools[stack], temperatures[stack] = {}, {}
        for channel in ordered:
            pools[stack][channel] = [f"{stack}:ch{channel}:spare{i}" for i in range(spares)]
            temperatures[stack][channel] = float(raw["initial_temperature_k"])
            for index in range(blocks // len(ordered)):
                extents.append({"extent_id": f"{stack}:ch{channel}:extent{index}",
                                "stack": stack, "channel": channel,
                                "source_block_id": f"{stack}:ch{channel}:source{index}",
                                "version": 0})
    driver = MaintenanceDriver(ledger, {
        "block_bytes": int(raw["block_bytes"]),
        "pages_per_block": int(raw["pages_per_block"]),
        "max_blocks_per_cohort": int(raw["max_blocks_per_cohort"]),
        "spare_block_ids_by_stack_channel": pools,
        "program_energy_j_per_byte": raw.get("program_energy_j_per_byte"),
        "erase_energy_j_per_operation": raw.get("erase_energy_j_per_operation"),
        "energy_evidence": raw["energy_evidence"],
    })
    driver.register_extents(extents)
    return ledger, driver, CausalMaintenanceAgeAdapter(ledger, driver, temperatures)


def _observed_channel_temperatures(heat, hbf_channels):
    entities = heat.get("entity_temperatures_k")
    if not isinstance(entities, dict):
        raise ValueError("maintenance requires per-entity thermal temperatures")
    result = {}
    for stack, channels in hbf_channels.items():
        result[stack] = {}
        for channel, components in channels.items():
            values = []
            for component in components:
                row = entities.get(component)
                if not isinstance(row, dict) or "hotspot_k" not in row:
                    raise ValueError(f"missing thermal hotspot for {component}")
                values.append(float(row["hotspot_k"]))
            result[stack][channel] = max(values)
    return result


def execute(config, normalized, thermal, sink, *, initial_trace=None, trace_factory=None):
    window_ns = int(config.get("window_ns", WINDOW_NS))
    if window_ns <= 0:
        raise ValueError("window_ns must be positive")
    end_ns = int(config["active_ns"]) + int(config["recovery_ns"])
    if end_ns % window_ns:
        raise ValueError("duration must align to control windows")
    trace_config = dict(config["trace"])
    total_batches = int(trace_config["total_batches"])
    max_active = int(trace_config.get("max_active_batches", 4))
    interval_ns = int(trace_config["batch_interval_ns"])
    if total_batches <= 0 or max_active <= 0 or interval_ns <= 0:
        raise ValueError("invalid streaming trace bounds")
    if trace_factory is None:
        trace_factory = lambda index: build_architecture_trace({
            **trace_config, "batch_intervals": 1, "first_interval": index})
    seed = initial_trace or trace_factory(0)
    executor = CausalExecutor(seed, config["executor"])
    next_batch = 1
    service = CausalTopologyService(config["service"])
    granularity = config.get("causal_channel_granularity", {"mode": "physical_channels"})
    energy = CausalEnergyAdapter(normalized, config["service"],
                                 _default_energy_profile(config.get("energy", {})),
                                 granularity)
    if granularity.get("mode") != "physical_channels" \
            and config.get("maintenance", {"mode": "disabled"}).get("mode") != "disabled":
        raise ValueError("grouped causal channels do not claim physical maintenance extents")
    ledger, maintenance_driver, maintenance_age = _build_maintenance(
        config, energy.hbf_channels)
    stacks = sorted(config["service"]["channels"])
    targets = config["target_bytes_per_s_by_stack"]
    if set(targets) != set(stacks):
        raise ValueError("target_bytes_per_s_by_stack must exactly cover service stacks")
    baseline = {stack: sum(config["service"]["channels"][stack].values()) * window_ns // 10**9
                for stack in stacks}
    budgets = dict(baseline)
    states = {stack: "normal" for stack in stacks}
    policies = {}
    for stack in stacks:
        profile = EngineeringProfile(
            profile_id=f"{config['point_id']}:{stack}", enabled=True,
            strategy=config["strategy"], window_ns=window_ns,
            target_bytes_per_s=int(targets[stack]),
            step_bytes=max(1, baseline[stack] // 20),
            minimum_budget_bytes=max(1, baseline[stack] // 10),
            maximum_budget_bytes=baseline[stack], severe_budget_bytes=0,
            light_fraction=float(config.get("light_fraction", .5)))
        policies[stack] = EndpointAwarePolicy(profile)
    known_jobs = {}
    progress_signatures = {}
    cumulative_tokens = 0
    total_energy = 0.0
    compute_intervals = []
    peak = {}
    offered_total = defaultdict(int)
    delivered_total = defaultdict(int)
    group_stack_bytes = defaultdict(lambda: defaultdict(int))
    group_logical_issue = {}
    pending_traces = []
    deferred_maintenance_jobs = []

    for start in range(0, end_ns, window_ns):
        stop = start + window_ns
        service.begin_window(start, stop, budgets, states)
        now = start
        maintenance_jobs = list(deferred_maintenance_jobs)
        deferred_maintenance_jobs = []
        if maintenance_age is not None:
            maintenance_jobs.extend(maintenance_age.start_window(start))
        activities, changed_by_id, completions, executor_events, submissions = [], {}, [], [], []
        maintenance_deltas = []
        token_window = 0
        offered_window, delivered_window = defaultdict(int), defaultdict(int)
        retry_window = defaultdict(int)
        delays = defaultdict(list)

        def consume_observations(observed):
            executor_events.extend(observed["events"])
            submissions.extend(observed["submissions"])
            for event in observed["events"]:
                if event["kind"] == "compute_start":
                    compute_intervals.append((event["start_ns"], event["scheduled_end_ns"]))
                elif event["kind"] == "storage_complete":
                    group_id = event["group_id"]
                    by_stack = group_stack_bytes.pop(group_id, {})
                    logical = group_logical_issue.pop(group_id, event["completion_ns"])
                    for stack, byte_count in by_stack.items():
                        delivered_window[stack] += byte_count
                        delivered_total[stack] += byte_count
                        delays[stack].append((event["completion_ns"] - logical, byte_count))
        while now < stop:
            retired = executor.retire_completed_batches(now)
            newly_retired = sum(row["token_count"] for row in retired)
            token_window += newly_retired
            cumulative_tokens += newly_retired
            while pending_traces and len(executor.trace["batches"]) < max_active:
                executor.append_trace(pending_traces.pop(0))
            while next_batch < total_batches and next_batch * interval_ns <= now:
                candidate_trace = trace_factory(next_batch)
                if len(executor.trace["batches"]) < max_active and not pending_traces:
                    executor.append_trace(candidate_trace)
                else:
                    pending_traces.append(candidate_trace)
                next_batch += 1
            jobs = executor.poll(now)
            jobs.extend(executor.offer_migrations(now))
            jobs.extend(maintenance_jobs)
            maintenance_jobs = []
            physical_jobs = []
            for raw in jobs:
                job = dict(raw)
                metadata = dict(job.get("metadata", {}))
                if job["arrival_ns"] < now:
                    metadata.setdefault("logical_issue_ns", job["arrival_ns"])
                    metadata["external_wait_before_submit_ns"] = now - job["arrival_ns"]
                    job["arrival_ns"] = now
                job["metadata"] = metadata
                physical_jobs.append(job)
                known_jobs[job["job_id"]] = job
                if job["operation"] == "read" and "maintenance_id" not in job:
                    offered_window[job["stack"]] += job["bytes"]
                    offered_total[job["stack"]] += job["bytes"]
                    group_id = metadata.get("parent_group_id", job["job_id"])
                    group_stack_bytes[group_id][job["stack"]] += job["bytes"]
                    logical = metadata.get("logical_issue_ns", job["arrival_ns"])
                    group_logical_issue[group_id] = min(
                        group_logical_issue.get(group_id, logical), logical)
                elif job["operation"] == "retry" and "maintenance_id" not in job:
                    retry_window[job["stack"]] += 1
            if physical_jobs:
                service.submit_jobs(physical_jobs)
            observed = executor.drain_observations()
            consume_observations(observed)
            candidates = [stop]
            for value in (service.next_event_ns(), executor.next_internal_event_ns()):
                if value is not None and value > now:
                    candidates.append(value)
            arrival = next_batch * interval_ns if next_batch < total_batches else None
            if arrival is not None and arrival > now:
                candidates.append(arrival)
            horizon = min(candidates)
            receipt = service.advance_to(horizon)
            activities.extend(receipt["activities"])
            for progress in _changed_rows(receipt["job_progress"], progress_signatures):
                changed_by_id[progress["job_id"]] = progress
            rows = {row["job_id"]: row for row in receipt["job_progress"]}
            if maintenance_age is not None:
                maintenance_delta = maintenance_age.consume_receipt(receipt)
                next_phases = maintenance_delta.pop("next_phase_jobs")
                maintenance_deltas.append(maintenance_delta)
                if receipt["end_ns"] < stop:
                    maintenance_jobs.extend(next_phases)
                else:
                    deferred_maintenance_jobs.extend(next_phases)
            for job_id in receipt["completion_ids"]:
                row = rows[job_id]
                job = known_jobs.pop(job_id)
                if job_id in executor.job_bytes:
                    executor.complete(job_id, row["completion_ns"], row["total_bytes"])
                completions.append({"job_id": job_id, "completion_ns": row["completion_ns"],
                                    "bytes": row["total_bytes"], "operation": job["operation"],
                                    "stack": job["stack"]})
            now = horizon
        observed = executor.drain_observations()
        consume_observations(observed)
        retired = executor.retire_completed_batches(stop)
        newly_retired = sum(row["token_count"] for row in retired)
        token_window += newly_retired
        cumulative_tokens += newly_retired
        compute_ns = sum(max(0, min(stop, end) - max(start, begin))
                         for begin, end in compute_intervals)
        mapped = energy.map(
            activities,
            gpu_compute_j=float(config.get("gpu_compute_w", 0)) * compute_ns / 1e9,
            gpu_external_j=float(config.get("gpu_external_w", 0)) * window_ns / 1e9,
        )
        total_energy += mapped["total_j"]
        heat = thermal.advance(start, stop, mapped["component_energy_j"])
        maintenance_finish = None
        if maintenance_age is not None:
            maintenance_finish = maintenance_age.finish_window(
                stop, _observed_channel_temperatures(heat, energy.hbf_channels))
        for owner, value in heat["temperatures"].items():
            peak[owner] = max(peak.get(owner, value), value)
        observed_states = {stack: heat["stack_states"][stack] for stack in stacks}
        next_budgets, decisions, stack_facts = {}, {}, {}
        for stack in stacks:
            backlog_jobs = [job for job in known_jobs.values()
                            if job["stack"] == stack and job["operation"] == "read"
                            and "maintenance_id" not in job]
            pending_bytes = sum(_pending_trace_bytes_by_stack(item, config["executor"]).get(stack, 0)
                                for item in pending_traces)
            backlog = sum(job["bytes"] for job in backlog_jobs) + pending_bytes
            oldest = max(
                [stop - job.get("metadata", {}).get("logical_issue_ns", job["arrival_ns"])
                 for job in backlog_jobs]
                + [stop - item["batches"][0]["arrival_ns"] for item in pending_traces
                   if _pending_trace_bytes_by_stack(item, config["executor"]).get(stack, 0)],
                default=0)
            facts = StackWindowFacts(
                stack_id=stack, offered_bytes=offered_window[stack],
                delivered_bytes=delivered_window[stack], backlog_bytes=backlog,
                oldest_wait_ns=oldest, latency_p95_ns=percentile(delays[stack], 95),
                censored_requests=(len(backlog_jobs) + sum(
                    task["type"] == "storage" for item in pending_traces
                    for batch in item["batches"] for task in batch["tasks"])),
                gate_limited=(bool(backlog_jobs)
                              and receipt["endpoint_quota_remaining_scaled"][stack] == 0),
                backend_busy_fraction=None, resource_busy=None,
                retry_count=retry_window[stack])
            decision = policies[stack].evaluate(WindowFacts(
                start_ns=start, end_ns=stop, guard_state=observed_states[stack],
                stacks=(facts,), current_budget_bytes={stack: budgets[stack]},
                guard_states={stack: observed_states[stack]},
                hysteresis_budget_bytes={stack: heat["hysteresis_budget_bytes"][stack]}))
            next_budgets[stack] = decision.stack_decisions[0].budget_bytes
            decisions[stack] = asdict(decision)
            stack_facts[stack] = asdict(facts)
        grouped_output = granularity.get("mode") == "uniform_stack_group_16ch"
        if grouped_output:
            output_activities = _aggregate_activities(activities)
            output_progress = _aggregate_progress(list(changed_by_id.values()))
            output_completions = _aggregate_completions(completions)
            output_events, output_submissions = _aggregate_observations(
                executor_events, submissions)
        else:
            output_activities = activities
            output_progress = list(changed_by_id.values())
            output_completions = completions
            output_events, output_submissions = executor_events, submissions
        row = {
            "start_ns": start, "end_ns": stop,
            "service": {"activities": output_activities,
                        "changed_job_progress": output_progress,
                        "completions": output_completions,
                        "endpoint_quota_remaining_scaled": receipt["endpoint_quota_remaining_scaled"],
                        "buffer_occupancy_bounds": receipt["buffer_occupancy_bounds"]},
            "executor": {"events": output_events, "submissions": output_submissions,
                         "completed_tokens": token_window,
                         "cumulative_completed_tokens": cumulative_tokens,
                         "active_batches": len(executor.trace["batches"]),
                         "uninstantiated_arrived_batches": len(pending_traces),
                         "token_rate_semantics": "EXACT_DAG_COMPLETIONS_NOT_CALIBRATED_TOKENS_PER_SECOND"},
            "output_granularity": ("UNIFORM_16_PHYSICAL_CHANNEL_GROUP_WINDOW_AGGREGATES"
                                   if grouped_output else "PHYSICAL_CHANNEL_JOB_DELTAS"),
            "energy": mapped, "thermal": heat,
            "maintenance": ({"mode": "disabled"} if maintenance_age is None else {
                "mode": "shared_exact_service", "receipt_deltas": maintenance_deltas,
                "window_age_finish": maintenance_finish,
                "driver": maintenance_driver.drain_delta(stop),
            }),
            "control": {"budgets": budgets, "next_budgets": next_budgets,
                        "observed_states": observed_states, "decisions": decisions,
                        "stack_facts": stack_facts,
                        "backend_latency": "UNKNOWN_FLUID_MODEL"},
        }
        sink.write(json.dumps(row, separators=(",", ":"), allow_nan=False) + "\n")
        budgets, states = next_budgets, observed_states
    final = executor.result(end_ns)
    return {
        "completed_tokens": cumulative_tokens,
        "incomplete_active_tokens": sum(not token["complete"] for token in final["tokens"]),
        "uninstantiated_batches": len(pending_traces) + total_batches - next_batch,
        "offered_useful_bytes_by_stack": dict(offered_total),
        "delivered_useful_bytes_by_stack": dict(delivered_total),
        "pending_external_jobs": len(final["pending_external_jobs"]),
        "maintenance": ({"mode": "disabled"} if maintenance_driver is None else {
            "mode": "shared_exact_service",
            "driver": maintenance_driver.snapshot(),
            "reliability": ledger.snapshot(),
            "deferred_phase_jobs": len(deferred_maintenance_jobs),
        }),
        "energy_j": total_energy, "peak_k_by_owner": peak,
        "service_facts": service.immutable_facts(),
        "scope": "CONDITIONAL_CAUSAL_FLUID_NOT_NATIVE_NAND_OR_CALIBRATED_TOKEN_RATE",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("config", "model-dir", "thermal-binary", "artifact-root", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    started = time.monotonic()
    try:
        for key in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
            os.environ[key] = "1"
        os.environ["CUDA_VISIBLE_DEVICES"] = ""
        config = json.loads(args.config.read_text())
        save(args.output / "config.json", config)
        sources = [HERE / name for name in ("run_causal_point.py", "causal_service.py",
                                             "causal_workload.py")]
        sources += [MAINTENANCE / "thermal_client.py", MAINTENANCE / "read_rate_policy.py"]
        manifest = {
            "started_utc": datetime.now(timezone.utc).isoformat(),
            "environment_id": "eq3-thermal-cpu-v1", "python": sys.version,
            "platform": platform.platform(), "input_sha256": digest(args.config),
            "source_sha256": {str(path.relative_to(ROOT)): digest(path) for path in sources},
            "thermal_binary_sha256": digest(args.thermal_binary),
            "source_revision": subprocess.check_output(
                ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
            "model_dir": str(args.model_dir.resolve()), "cpu_threads": 1, "gpu_count": 0,
            "resource_limits": config["resource_limits"],
        }
        save(args.output / "manifest.json", manifest)
        limit = config["resource_limits"]["address_space_gib"] * 1024**3
        resource.setrlimit(resource.RLIMIT_AS, (limit, limit)); resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
        normalized = json.loads((args.model_dir / "normalized.json").read_text())
        baseline = {stack: sum(channels.values()) * int(config.get("window_ns", WINDOW_NS)) // 10**9
                    for stack, channels in config["service"]["channels"].items()}
        with ThermalService(args.thermal_binary, args.model_dir, args.output / "thermal-process",
                            artifact_root=args.artifact_root, baseline_budgets=baseline,
                            limits=config.get("thermal_limits_k")) as thermal:
            manifest.update(thermal_model_lock=thermal.lock, thermal_header=thermal.header)
            save(args.output / "manifest.json", manifest)
            with (args.output / "windows.jsonl").open("w") as sink:
                summary = execute(config, normalized, thermal, sink)
        save(args.output / "DONE.json", {"status": "COMPLETED", "summary": summary,
                                          "wall_s": time.monotonic() - started,
                                          "child_peak_rss_kib": resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss})
    except BaseException as exc:
        save(args.output / "FAILED.json", {"status": "FAILED", "error": repr(exc),
                                            "wall_s": time.monotonic() - started,
                                            "raw_preserved": True})
        raise


if __name__ == "__main__":
    main()
