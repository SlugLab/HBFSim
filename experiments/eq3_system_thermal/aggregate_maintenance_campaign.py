#!/usr/bin/env python3
"""Aggregate a frozen 19-point maintenance campaign from validated receipts."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from pathlib import Path


WINDOW_NS = 20_000_000


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def observed_max(values):
    observed = [value for value in values if value is not None]
    return max(observed) if observed else None


def timing(point):
    first_due = first_commit = last_commit = None
    max_due = max_outstanding = 0
    with (point / "windows.jsonl").open() as stream:
        for line in stream:
            row = json.loads(line)
            due = row["reliability"]["due_block_count"]
            max_due = max(max_due, due)
            if due and first_due is None:
                first_due = row["end_ns"]
            delta = row["maintenance_delta"]
            max_outstanding = max(max_outstanding, delta["summary"]["outstanding_job_count"])
            committed = [value for value in delta["terminal_results"]
                         if value["status"] == "COMMITTED"]
            if committed:
                first_commit = row["end_ns"] if first_commit is None else first_commit
                last_commit = row["end_ns"]
    return {"first_due_ns": first_due, "first_commit_ns": first_commit,
            "last_commit_ns": last_commit, "max_due_blocks": max_due,
            "max_outstanding_jobs": max_outstanding}


def analyze(index_path: Path, point_analysis: Path, output: Path):
    if output.exists():
        raise FileExistsError(output)
    index = json.loads(index_path.read_text())
    if len(index["points"]) != 19:
        raise ValueError("maintenance main index must contain 19 points")
    receipt = json.loads((index_path.parent / "DONE.json").read_text())
    if receipt["status"] != "COMPLETED" or len(receipt["completed"]) != 19 or receipt["domain_failures"]:
        raise ValueError("maintenance stage terminal coverage mismatch")
    execution = {row["point_id"]: row for row in receipt["completed"]}
    points = []
    for spec in index["points"]:
        point = Path(spec["output"])
        config = json.loads(Path(spec["config"]).read_text())
        if digest(spec["config"]) != spec["config_sha256"]:
            raise ValueError("config identity mismatch")
        derived = json.loads((point_analysis / spec["point_id"] / "analysis.json").read_text())
        if derived["analysis_status"] != "VALIDATED_COMPLETE_RECEIPTS" or any(
                value != "PASS" for key, value in derived["checks"].items()
                if key in ("byte_conservation", "energy_to_thermal", "timeline")):
            raise ValueError("strict point analysis did not pass")
        done = json.loads((point / "DONE.json").read_text())["summary"]
        panels = derived["panels"]
        maintenance = derived["maintenance"]
        active_rates = panels["physical_effective_hbf_traffic"]["effective_useful_Bps"][:1000]
        row = {"point_id": spec["point_id"], "topology": config["topology"],
               "strategy": config["strategy"], "mode": config["maintenance"]["mode"],
               "ea_ev": config["maintenance"]["ea_ev"],
               "active_mean_delivered_Bps": sum(active_rates)/len(active_rates),
               "delivered_bytes": done["delivered_bytes"], "final_backlog_bytes": done["backlog_bytes"],
               "total_energy_j": done["energy_j"],
               "peak_hbf_k": observed_max(panels["owner_temperature_k"]["hbf_max"]),
               "peak_hbm_k": observed_max(panels["owner_temperature_k"]["hbm_max"]),
               "peak_gpu_k": observed_max(panels["owner_temperature_k"]["gpu"]),
               "max_logical_backlog_bytes": max(panels["queue_backlog_bytes"]["logical_backlog"]),
               "terminal_operations": maintenance["terminal_operation_count"],
               "terminal_extents": maintenance["terminal_extent_count"],
               "committed_operations": maintenance["terminal_status_counts"].get("COMMITTED", 0),
               "successful_age_resets": maintenance["age"]["successful_age_resets"],
               "free_spares": maintenance["free_spares"],
               "quarantined_blocks": maintenance["quarantined_blocks"],
               "wall_s": execution[spec["point_id"]]["wall_s"],
               "output_bytes": execution[spec["point_id"]]["output_bytes"]}
        row.update(timing(point))
        points.append(row)
    by = {(p["topology"], p["strategy"], p["mode"], p["ea_ev"]): p for p in points}
    metrics = ("active_mean_delivered_Bps", "delivered_bytes", "final_backlog_bytes",
               "peak_hbf_k", "max_logical_backlog_bytes", "total_energy_j",
               "first_due_ns", "first_commit_ns", "last_commit_ns")
    def compare(left, right, identity):
        return {"identity": identity, "left_point_id": left["point_id"],
                "right_point_id": right["point_id"],
                "right_minus_left": {name: right[name]-left[name] for name in metrics}}
    policy = []
    for topology in ("mixed_direct", "relay", "dash", "all_hbf_direct"):
        guard = by[(topology, "guard_only", "shared", 1.04)]
        for strategy in ("thermal_hysteresis_guard", "read_rate_feedback_thermal_guard_v1"):
            policy.append(compare(guard, by[(topology, strategy, "shared", 1.04)],
                                  f"{topology}: {strategy} minus guard_only"))
    contention = []
    for topology in ("mixed_direct", "relay", "dash"):
        shared = by[(topology, "read_rate_feedback_thermal_guard_v1", "shared", 1.04)]
        ideal = by[(topology, "read_rate_feedback_thermal_guard_v1", "ideal_independent", 1.04)]
        contention.append(compare(shared, ideal, f"{topology}: ideal_independent minus shared"))
    ea = []
    for strategy in ("guard_only", "read_rate_feedback_thermal_guard_v1"):
        center = by[("mixed_direct", strategy, "shared", 1.04)]
        for value in (1.01, 1.08):
            ea.append(compare(center, by[("mixed_direct", strategy, "shared", value)],
                              f"mixed_direct {strategy}: Ea {value} minus 1.04 eV"))
    result = {"schema_version": "eq3-maintenance-main-aggregate-v1", "status": "PASS",
              "point_count": 19, "points": points, "policy_costs": policy,
              "shared_vs_ideal": contention, "ea_sensitivity": ea,
              "resource_receipt": {"stage_wall_s": receipt["wall_s"],
                                   "retained_output_bytes": sum(p["output_bytes"] for p in points)},
              "claim_scope": ("CONDITIONAL_AGGREGATE_RATE_SERVICE; DELTAS_HAVE_NO_PRESET_WINNER; "
                              "NO_ECC_OR_NATIVE_NAND_THROUGHPUT_BENEFIT_CLAIM")}
    output.mkdir(parents=True)
    (output / "MAINTENANCE_MAIN_ANALYSIS.json").write_text(
        json.dumps(result, indent=2, sort_keys=True, allow_nan=False)+"\n")
    with (output / "point-summary.csv").open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(points[0])); writer.writeheader(); writer.writerows(points)
    lines = ["# Maintenance main result", "",
             "All 19 points passed strict timeline, byte, energy, and unique-completion checks. Deltas below are observational costs, with no preferred policy direction encoded.", "",
             "| Comparison | Δ active TB/s | Δ delivered TB | Δ final backlog TB | Δ peak HBF K |",
             "|---|---:|---:|---:|---:|"]
    for item in policy:
        delta = item["right_minus_left"]
        lines.append(f"| {item['identity']} | {delta['active_mean_delivered_Bps']/1e12:.6f} | {delta['delivered_bytes']/1e12:.6f} | {delta['final_backlog_bytes']/1e12:.6f} | {delta['peak_hbf_k']:.6f} |")
    lines += ["", "## Shared versus ideal-independent maintenance", "",
              "| Comparison | Δ delivered TB | Δ backlog TB | Δ peak HBF K | Δ first commit ms |",
              "|---|---:|---:|---:|---:|"]
    for item in contention:
        delta = item["right_minus_left"]
        lines.append(f"| {item['identity']} | {delta['delivered_bytes']/1e12:.6f} | {delta['final_backlog_bytes']/1e12:.6f} | {delta['peak_hbf_k']:.9f} | {delta['first_commit_ns']/1e6:.3f} |")
    lines += ["", "## Arrhenius activation-energy sensitivity", "",
              "| Comparison | Δ delivered GB | Δ backlog GB | Δ peak HBF K | Δ first commit ms | Δ last commit ms |",
              "|---|---:|---:|---:|---:|---:|"]
    for item in ea:
        delta = item["right_minus_left"]
        lines.append(f"| {item['identity']} | {delta['delivered_bytes']/1e9:.6f} | {delta['final_backlog_bytes']/1e9:.6f} | {delta['peak_hbf_k']:.9f} | {delta['first_commit_ns']/1e6:.3f} | {delta['last_commit_ns']/1e6:.3f} |")
    lines += ["", "All 19 points committed every declared operation, reset only the successful extents, returned every spare, and quarantined no block. Exact point counts and deltas are stored in `MAINTENANCE_MAIN_ANALYSIS.json`. Token throughput, native NAND timing, ECC benefit, and lifetime remain unavailable."]
    (output / "RESULT.md").write_text("\n".join(lines)+"\n")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--index", type=Path, required=True)
    parser.add_argument("--point-analysis", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    value = analyze(args.index.resolve(strict=True), args.point_analysis.resolve(strict=True),
                    args.output.resolve())
    print(json.dumps({"status": value["status"], "point_count": value["point_count"]}))


if __name__ == "__main__":
    main()
