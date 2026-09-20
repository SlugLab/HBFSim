#!/usr/bin/env python3
"""Strict analysis of a frozen uncontrolled first-constraint subset."""

from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path


WINDOW_NS = 20_000_000
LABELS = ("light", "severe", "shutdown")


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def last_error_response(path):
    for line in reversed(Path(path).read_text().splitlines()):
        value = json.loads(line)
        response = value.get("response")
        if isinstance(response, dict) and response.get("type") == "ERROR":
            return response
    raise ValueError("thermal transcript has no ERROR response")


def first_crossings(rows, limits):
    first = {}
    for row in rows:
        end = row["end_ns"]
        for owner, temperature in row["thermal"]["temperatures"].items():
            kind = "gpu" if owner == "gpu" else owner[:3]
            for label, threshold in zip(LABELS, limits[kind]):
                if temperature >= threshold:
                    first.setdefault((label, owner), end)
    result = {}
    for label in LABELS:
        observed = [(time, owner) for (kind, owner), time in first.items() if kind == label]
        if observed:
            time = min(time for time, _ in observed)
            result[label] = {"status": "CROSSED", "end_ns": time,
                             "interval_ns": {"lower_exclusive": max(0, time-WINDOW_NS),
                                             "upper_inclusive": time},
                             "tied_owners": sorted(owner for value, owner in observed
                                                   if value == time)}
        else:
            result[label] = None
    return result


def analyze(index_path: Path, output: Path):
    if output.exists():
        raise FileExistsError(output)
    index = json.loads(index_path.read_text())
    if index["point_count"] != 18 or any(
            row["execution_mode"] != "uncontrolled_first_constraint"
            for row in index["points"]):
        raise ValueError("index is not the frozen 18-point uncontrolled subset")
    stage = index_path.parent
    receipt = json.loads((stage / "DONE.json").read_text())
    completed_ids = {row["point_id"] for row in receipt["completed"]}
    domain_ids = {row["point_id"] for row in receipt["domain_failures"]}
    if len(completed_ids) + len(domain_ids) != 18 or completed_ids & domain_ids:
        raise ValueError("stage receipt coverage mismatch")

    points = []
    for spec in index["points"]:
        point = Path(spec["output"])
        config = json.loads(Path(spec["config"]).read_text())
        if digest(spec["config"]) != spec["config_sha256"]:
            raise ValueError("config hash mismatch: " + spec["point_id"])
        manifest = json.loads((point / "manifest.json").read_text())
        if manifest["input_sha256"] != spec["config_sha256"]:
            raise ValueError("manifest input mismatch: " + spec["point_id"])
        if not config.get("control_disabled"):
            raise ValueError("controlled point in uncontrolled analysis")
        rows, previous_end = [], 0
        with (point / "windows.jsonl").open() as stream:
            for line in stream:
                row = json.loads(line)
                if row["start_ns"] != previous_end or row["end_ns"]-row["start_ns"] != WINDOW_NS:
                    raise ValueError("non-contiguous window: " + spec["point_id"])
                previous_end = row["end_ns"]
                if any(not value["cumulative_conserved"]
                       for value in row["service"]["stacks"].values()):
                    raise ValueError("byte conservation failure: " + spec["point_id"])
                expected = row["energy"]["total_j"]
                actual = row["thermal"]["energy_j"]["window"]["total_input_j"]
                if abs(expected-actual) > 1e-9*max(1, abs(expected)):
                    raise ValueError("energy conservation failure: " + spec["point_id"])
                rows.append(row)
        if not rows:
            raise ValueError("point has no completed thermal windows")
        status = "COMPLETED" if spec["point_id"] in completed_ids else "DOMAIN_FAILURE"
        failure = None
        if status == "COMPLETED":
            if not (point / "DONE.json").is_file() or (point / "FAILED.json").exists():
                raise ValueError("completed point terminal evidence mismatch")
        else:
            if not (point / "FAILED.json").is_file():
                raise ValueError("domain failure lacks FAILED evidence")
            last = last_error_response(point / "thermal-process" / "thermal-transcript.jsonl")
            if last.get("status") != "DOMAIN_FAILURE" or not last.get("failure_returned_to_caller"):
                raise ValueError("failure is not a preserved DOMAIN_FAILURE")
            if last["last_valid_time_ns"] != previous_end:
                raise ValueError("last-valid/raw coverage mismatch")
            failure = {key: last.get(key) for key in
                       ("status", "reason", "last_valid_time_ns", "trial_target_time_ns",
                        "last_valid_temperature_range_k", "trial_temperature_range_k",
                        "trial_declared_activity_energy_j", "failed_trial_step_energy")}
        crossings = first_crossings(rows, config["thermal_limits_k"])
        for label in LABELS:
            if crossings[label] is None:
                crossings[label] = {"status": ("NO_CROSSING_COMPLETED" if status == "COMPLETED"
                                                else "RIGHT_CENSORED_DOMAIN_FAILURE")}
        points.append({"point_id": spec["point_id"], "topology": spec["topology"],
                       "parameter_values": spec["sensitivity_values"], "status": status,
                       "completed_window_count": len(rows), "last_valid_time_ns": previous_end,
                       "crossings": crossings, "failure": failure,
                       "tested_parameter_set_weight": {"numerator": 1, "denominator": 18}})

    summaries = {}
    for label in LABELS:
        states = Counter(point["crossings"][label]["status"] for point in points)
        summaries[label] = {"counts": dict(sorted(states.items())),
                            "tested_parameter_set_denominator": 18,
                            "crossed_proportion": states["CROSSED"] / 18,
                            "tie_point_count": sum(len(point["crossings"][label].get("tied_owners", ())) > 1
                                                   for point in points)}
    result = {"schema_version": "eq3-uncontrolled-first-constraint-analysis-v1",
              "status": "PASS", "index": str(index_path.resolve()),
              "index_sha256": digest(index_path), "point_count": 18,
              "terminal_counts": dict(sorted(Counter(p["status"] for p in points).items())),
              "crossing_summaries": summaries, "points": points,
              "claim_scope": ("PROPORTIONS_OF_THE_18_TESTED_PARAMETER_SETS_ONLY; "
                              "NO_POPULATION_OR_POLICY_BENEFIT_INFERENCE"),
              "limitations": ["20MS_CROSSING_INTERVAL", "DOMAIN_FAILURE_RIGHT_CENSORING",
                              "CONDITIONAL_ENGINEERING_INPUTS", "P2_REFERENCE_NOT_QUALIFIED",
                              "NO_ECC_RETRY_OR_MAINTENANCE_BENEFIT_CLAIM"]}
    failed = [point for point in points if point["status"] == "DOMAIN_FAILURE"]
    result["domain_failure_summary"] = {
        "count": len(failed),
        "reasons": dict(sorted(Counter(point["failure"]["reason"] for point in failed).items())),
        "failed_trial_energy_statuses": dict(sorted(Counter(
            point["failure"]["failed_trial_step_energy"]["status"] for point in failed).items())),
        "configured_gpu_external_w_counts": dict(sorted(Counter(
            str(point["parameter_values"]["gpu_external_w"]) for point in failed).items())),
        "interpretation": ("Every crossing reported for a failed point occurred in a preserved completed "
                           "window before failure. The trajectory after the last-valid frame is unobserved."),
    }
    output.mkdir(parents=True)
    (output / "UNCONTROLLED_FIRST_CONSTRAINT_ANALYSIS.json").write_text(
        json.dumps(result, indent=2, sort_keys=True, allow_nan=False) + "\n")
    lines = ["# Uncontrolled first-constraint analysis", "",
             "Scope: the 18 tested parameter sets only; no policy, ECC/retry, maintenance, or population inference.", "",
             f"Terminal results: {result['terminal_counts']}.", ""]
    for label in LABELS:
        row = summaries[label]
        lines.append(f"- {label}: {row['counts']}; crossed proportion {row['crossed_proportion']:.3f} of 18; tie points {row['tie_point_count']}.")
    lines += ["", "All 16 failures explicitly report `temperature left required domain`; the failed trial step was not integrated and its energy terms remain UNKNOWN. Thirteen of those 16 configs prescribe 0 W external GPU heat, one prescribes 100 W, and two prescribe 200 W. The failure is therefore the registered 400 K domain response under uncontrolled conditional heat inputs, not a GPU-only failure.",
              "", "Each crossing is localized only to `(previous frame, reported frame]`, a 20 ms interval. Every reported failed-point crossing occurred in a preserved pre-failure window. A missing crossing in a DOMAIN_FAILURE point is right-censored, and no post-failure trajectory or later constraint ordering is inferred.", "",
              "| Point | Terminal | Light s / owners | Severe s / owners | Shutdown s / owners | Last valid s |",
              "|---|---|---|---|---|---:|"]
    def cell(crossing):
        if crossing["status"] != "CROSSED":
            return crossing["status"]
        return f"{crossing['end_ns']/1e9:.3f} / {','.join(crossing['tied_owners'])}"
    for point in points:
        lines.append("| {point_id} | {status} | {light} | {severe} | {shutdown} | {last:.3f} |".format(
            point_id=point["point_id"], status=point["status"],
            light=cell(point["crossings"]["light"]),
            severe=cell(point["crossings"]["severe"]),
            shutdown=cell(point["crossings"]["shutdown"]),
            last=point["last_valid_time_ns"]/1e9))
    (output / "RESULT.md").write_text("\n".join(lines) + "\n")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--index", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = analyze(args.index.resolve(strict=True), args.output.resolve())
    print(json.dumps({"status": result["status"], "point_count": result["point_count"]}))


if __name__ == "__main__":
    main()
