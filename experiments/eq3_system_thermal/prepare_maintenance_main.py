#!/usr/bin/env python3
"""Prepare the reviewed 19-point maintenance candidate; never launch or freeze it."""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
from pathlib import Path


TOPOLOGIES = ("mixed_direct", "relay", "dash", "all_hbf_direct")
STRATEGIES = ("guard_only", "thermal_hysteresis_guard",
              "read_rate_feedback_thermal_guard_v1")
REFERENCE_RATE_BPS = 1_536_000_000_000
ACTIVE_NS = 20_000_000_000
RECOVERY_NS = 10_000_000_000
INVARIANT_MAINTENANCE_KEYS = (
    "refresh_trigger", "initial_equivalent_age_ns", "initial_wall_age_ns",
    "initial_temperature_k", "block_bytes", "pages_per_block",
    "aged_blocks_per_stack", "spares_per_channel", "max_blocks_per_cohort",
    "program_energy_j_per_byte", "erase_energy_j_per_operation", "energy_evidence",
)


def digest(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def load(path: Path) -> dict:
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return value


def save(path: Path, value: dict) -> None:
    Path(path).write_text(json.dumps(value, indent=2, sort_keys=True,
                                     allow_nan=False) + "\n", encoding="utf-8")


def _pilot_gate(pilot_index_path: Path, analysis_root: Path,
                review_path: Path) -> tuple[dict, dict, dict[str, dict]]:
    index = load(pilot_index_path)
    points = index.get("points", [])
    if len(points) != 4 or {row.get("topology") for row in points} != set(TOPOLOGIES):
        raise ValueError("pilot index must contain exactly one point for every topology")
    analyses = {}
    analysis_hashes = {}
    for row in points:
        point_id = row["point_id"]
        output = Path(row["output"])
        done_path = output / "DONE.json"
        if not done_path.is_file() or load(done_path).get("status") != "COMPLETED":
            raise ValueError(f"pilot {point_id} lacks a COMPLETED DONE receipt")
        directory = analysis_root / point_id
        analysis_path, analysis_done = directory / "analysis.json", directory / "DONE.json"
        if not analysis_path.is_file() or not analysis_done.is_file() \
                or load(analysis_done).get("status") != "COMPLETED":
            raise ValueError(f"pilot {point_id} lacks completed strict analysis")
        analysis = load(analysis_path)
        if analysis.get("analysis_status") != "VALIDATED_COMPLETE_RECEIPTS" \
                or analysis.get("runner") != "maintenance" \
                or analysis.get("identity", {}).get("point_id") != point_id:
            raise ValueError(f"pilot {point_id} strict analysis identity/status failed")
        checks = analysis.get("checks", {})
        if any(checks.get(key) != "PASS" for key in
               ("timeline", "byte_conservation", "energy_to_thermal")):
            raise ValueError(f"pilot {point_id} strict receipt checks failed")
        if checks.get("terminal_uniqueness") != "EXACT_JOB_IDS":
            raise ValueError(f"pilot {point_id} lacks exact completion identity")
        if analysis.get("maintenance", {}).get("terminal_operation_count", 0) <= 0:
            raise ValueError(f"pilot {point_id} did not exercise terminal maintenance work")
        analyses[point_id] = analysis
        analysis_hashes[point_id] = digest(analysis_path)
    review = load(review_path)
    if review.get("status") != "APPROVED_FOR_MAIN_INPUT_GENERATION":
        raise ValueError("pilot review has not approved main input generation")
    if review.get("pilot_index_sha256") != digest(pilot_index_path) \
            or review.get("analysis_sha256") != analysis_hashes:
        raise ValueError("pilot review is not bound to these index/analysis receipts")
    resources = review.get("measured_resource_budget")
    required = ("point_wall_s", "stage_wall_s", "point_output_gib",
                "stage_output_gib", "address_space_gib", "cpu_threads",
                "host_ram_reserve_gib", "host_disk_reserve_gib")
    if not isinstance(resources, dict) or any(
            isinstance(resources.get(key), bool) or not isinstance(resources.get(key), (int, float))
            or resources[key] <= 0 for key in required):
        raise ValueError("pilot review lacks a positive measured resource budget")
    return index, review, analyses


def _pilot_templates(index: dict) -> tuple[dict[str, dict], dict[str, dict]]:
    templates, rows = {}, {}
    for row in index["points"]:
        topology = row["topology"]
        config_path = Path(row["config"])
        if digest(config_path) != row.get("config_sha256"):
            raise ValueError(f"pilot input identity mismatch for {topology}")
        config = load(config_path)
        if config.get("topology") != topology or config.get("maintenance", {}).get("mode") != "shared":
            raise ValueError("pilot template topology/mode mismatch")
        if config.get("workload", {}).get("per_stack_Bps") != REFERENCE_RATE_BPS:
            raise ValueError("pilot template is not the 1.536 TB/s maintenance input")
        if config.get("maintenance", {}).get("refresh_trigger") != "equivalent_age_or_wall":
            raise ValueError("pilot template does not consume equivalent age")
        templates[topology], rows[topology] = config, row
    reference = templates[TOPOLOGIES[0]]
    for topology, config in templates.items():
        if config.get("energy") != reference.get("energy"):
            raise ValueError(f"pilot energy profile differs for {topology}")
        for key in INVARIANT_MAINTENANCE_KEYS:
            if config["maintenance"].get(key) != reference["maintenance"].get(key):
                raise ValueError(f"pilot maintenance invariant {key} differs for {topology}")
        if config.get("maintenance_scope") != reference.get("maintenance_scope"):
            raise ValueError(f"pilot maintenance scope differs for {topology}")
    return templates, rows


def _specs() -> list[dict]:
    result = [{"topology": topology, "strategy": strategy, "mode": "shared", "ea_ev": 1.04,
               "axis": "shared_strategy_matrix"}
              for topology in TOPOLOGIES for strategy in STRATEGIES]
    result += [{"topology": topology, "strategy": STRATEGIES[2],
                "mode": "ideal_independent", "ea_ev": 1.04,
                "axis": "matched_resource_contention"}
               for topology in ("mixed_direct", "relay", "dash")]
    result += [{"topology": "mixed_direct", "strategy": strategy,
                "mode": "shared", "ea_ev": ea, "axis": "arrhenius_ea_sensitivity"}
               for ea in (1.01, 1.08) for strategy in (STRATEGIES[0], STRATEGIES[2])]
    return result


def _point_id(spec: dict) -> str:
    return (f"maint-main-v1-{spec['topology']}-p{STRATEGIES.index(spec['strategy'])}-"
            f"{spec['mode']}-ea{round(spec['ea_ev'] * 100):03d}-01")


def prepare(pilot_index_path: Path, analysis_root: Path, review_path: Path,
            destination: Path) -> dict:
    pilot_index_path, analysis_root, review_path, destination = map(
        Path, (pilot_index_path, analysis_root, review_path, destination))
    if destination.name != "maintenance-main-v1":
        raise ValueError("destination must be a distinct maintenance-main-v1 stage")
    if destination.exists():
        raise FileExistsError(destination)
    index, review, analyses = _pilot_gate(pilot_index_path, analysis_root, review_path)
    templates, pilot_rows = _pilot_templates(index)
    destination.mkdir(parents=True)
    inputs = destination / "inputs"
    inputs.mkdir()
    points = []
    for spec in _specs():
        point_id = _point_id(spec)
        config = copy.deepcopy(templates[spec["topology"]])
        config["point_id"] = point_id
        config["strategy"] = spec["strategy"]
        config["workload"]["active_ns"] = ACTIVE_NS
        config["recovery_ns"] = RECOVERY_NS
        config["maintenance"]["mode"] = spec["mode"]
        config["maintenance"]["ea_ev"] = spec["ea_ev"]
        config["maintenance_scope"]["matrix_axis"] = spec["axis"]
        path = inputs / f"{point_id}.json"
        save(path, config)
        template = pilot_rows[spec["topology"]]
        points.append({
            "point_id": point_id, **spec, "config": str(path.resolve()),
            "config_sha256": digest(path), "model_dir": template["model_dir"],
            "thermal_binary": template["thermal_binary"],
            "artifact_root": template["artifact_root"],
            "output": str((destination / "points" / point_id).resolve()),
        })
    if len(points) != 19 or len({row["point_id"] for row in points}) != 19:
        raise AssertionError("maintenance matrix must contain 19 unique points")
    pilot_receipts = {point_id: {
        "analysis_sha256": review["analysis_sha256"][point_id],
        "analysis_status": analysis["analysis_status"],
        "maintenance_terminal_count": analysis["maintenance"]["terminal_operation_count"],
    } for point_id, analysis in analyses.items()}
    candidate = {
        "schema_version": "eq3-maintenance-main-candidate-v1",
        "status": "PILOT_REVIEW_PASSED_PREPARED_NOT_FROZEN_NOT_LAUNCHABLE",
        "point_count": 19, "points": points,
        "pilot_gate": {"pilot_index": str(pilot_index_path.resolve()),
                       "pilot_index_sha256": digest(pilot_index_path),
                       "review": str(review_path.resolve()), "review_sha256": digest(review_path),
                       "receipts": pilot_receipts},
        "runtime_locks_to_rebind_at_freeze": {
            "runner": index.get("runner"), "runtime_source_locks_sha256": index.get(
                "runtime_source_locks_sha256"), "model_locks_sha256": index.get(
                "model_locks_sha256"), "thermal_binary_sha256": index.get(
                "thermal_binary_sha256")},
        "resources_candidate_from_pilot_review": review["measured_resource_budget"],
        "gpu_count": 0,
        "execution_gate": "REQUIRES_SEPARATE_FREEZE_AND_ROOT_SCHEDULING;THIS_INDEX_IS_NOT_RUNNABLE",
    }
    save(destination / "CANDIDATE_INDEX.json", candidate)
    preflight = {
        "schema_version": "eq3-maintenance-main-preflight-candidate-v1",
        "status": "CANDIDATE_NOT_FROZEN_NOT_LAUNCHED",
        "research_question": ("How topology and existing policy affect foreground/refresh "
                              "contention, and how Ea changes the consumed equivalent-age trigger."),
        "evidence_class": "CONDITIONAL_SIMULATED_AGGREGATE_RATE_SERVICE",
        "authority": "USER_EXPLICIT_FOUR_TOPOLOGY_PLAN",
        "code_and_environment_candidate": {
            "environment_id": "eq3-thermal-cpu-v1",
            "runtime_locks_from_pilot": candidate["runtime_locks_to_rebind_at_freeze"],
            "freeze_requirement": "REHASH_CURRENT_SOURCES_MODELS_BINARY_AND_INPUTS_BEFORE_RUN"},
        "matrix": {"shared_strategy_points": 12, "matched_ideal_points": 3,
                   "ea_endpoint_points": 4, "total": 19,
                   "active_ns": ACTIVE_NS, "recovery_ns": RECOVERY_NS,
                   "per_stack_Bps": REFERENCE_RATE_BPS, "replicates": 1,
                   "determinism": "FIXED_INPUT_SINGLE_DETERMINISTIC_RUN_PER_POINT"},
        "fixed_inputs": {"maintenance_age_and_subset": {
            key: templates[TOPOLOGIES[0]]["maintenance"][key]
            for key in INVARIANT_MAINTENANCE_KEYS},
            "maintenance_scope": templates[TOPOLOGIES[0]]["maintenance_scope"],
            "energy": templates[TOPOLOGIES[0]]["energy"]},
        "controls": ["same topology/workload/age/energy within policy comparisons",
                     "shared versus ideal changes resource contention only",
                     "Ea endpoints consume equivalent-age policy; Ea1.04 reused from shared matrix"],
        "metrics": ["foreground bytes/backlog/delay", "maintenance commit/failure/spares/age/wear",
                    "component energy and owner temperatures", "controller states and due bytes"],
        "mechanism": {
            "refresh_chain": "read -> program -> version commit -> old block erase",
            "ea_consumer": "ReliabilityLedger equivalent_age_or_wall trigger",
            "success_age_rule": "reset exact extents only after successful commit"},
        "pilot_gate": candidate["pilot_gate"],
        "resources": review["measured_resource_budget"],
        "outputs": {"stage": str(destination.resolve()), "raw": "points/<point_id>/",
                    "analysis": "new derived directory; never overwrite raw"},
        "acceptance": ["all raw terminal receipts retained", "strict identity/timeline/byte/energy checks",
                       "unique completion identities", "paired comparisons retain fixed inputs"],
        "safety_stop": ["stop on identity or dependency mismatch",
                        "preserve and classify domain failures",
                        "stop and diagnose non-domain failure before remaining points",
                        "CPU-only and no parallel thermal jobs"],
        "failure_contract": "PRESERVE_FAILED_RUNS;NO_AUTOMATIC_SCIENTIFIC_PASS",
        "limits": ["not native MQSim NAND timing", "metadata validity only; no payload integrity",
                   "program/erase energy and speed are engineering proxies",
                   "P2 remains unqualified; external GDDR temperature unavailable"],
    }
    save(destination / "PREFLIGHT_CANDIDATE.json", preflight)
    (destination / "PREFLIGHT_CANDIDATE.md").write_text(
        "# Maintenance main candidate\n\n"
        "This directory contains 19 generated inputs after all four maintenance pilots and their "
        "strict receipt analyses were reviewed. It is **not frozen or runnable**. Root must bind "
        "current runtime/model/binary hashes into a separate run index before scheduling.\n\n"
        "The matrix contains 12 shared-resource topology/policy points, three policy-matched "
        "ideal-independent contention points for mixed/relay/DASH, and four mixed-topology "
        "Ea endpoint points. Ea=1.04 comparisons reuse the shared matrix. All points keep the "
        "same 1.536 TB/s offered rate, 20 s active plus 10 s recovery, near-due age, 4 GiB/stack "
        "aged subset, channel-owned spares, and energy/timing proxies.\n\n"
        "Interpretation remains conditional aggregate-service evidence. No scientific PASS is "
        "created by input generation.\n", encoding="utf-8")
    return candidate


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pilot-index", type=Path, required=True)
    parser.add_argument("--analysis-root", type=Path, required=True)
    parser.add_argument("--pilot-review", type=Path, required=True)
    parser.add_argument("--destination", type=Path, required=True)
    args = parser.parse_args()
    prepare(args.pilot_index, args.analysis_root, args.pilot_review, args.destination)


if __name__ == "__main__":
    main()
