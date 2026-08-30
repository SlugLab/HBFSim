#!/usr/bin/env python3
"""Prepare and summarize the disclosed HBF full-stack thermal sweep points."""

from __future__ import annotations

import argparse
import json
import pathlib
import statistics
import sys
from typing import Any

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import phase2_3dice_campaign as campaign  # noqa: E402


SOURCE = {
    "title": "A Full-Stack Characterization of High-Bandwidth Flash for KV-Centric LLM Serving",
    "arxiv": "2608.11668",
    "locator": "Sections 4.4 and 8.1; Figure 10",
    "evidence_class": "L",
}

POINTS = [
    (50.0, 13.0),
    (100.0, 27.0),
    (150.0, 40.0),
    (202.27, 53.72),
    (250.0, 66.0),
    (300.0, 80.0),
    (400.0, 106.0),
]


def write_json(path: pathlib.Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def prepare(golden_root: pathlib.Path, source_pdf: pathlib.Path,
            output: pathlib.Path) -> None:
    if output.exists():
        raise ValueError(f"refusing to overwrite {output}")
    output.mkdir(parents=True)
    template = campaign.source_template(golden_root, 16)
    names = campaign.case_nodes(16)
    cases = []
    for bandwidth_gbps, power_w in POINTS:
        label = str(bandwidth_gbps).replace(".", "p")
        case_id = f"literature-16hi-{label}gbps-{str(power_w).replace('.', 'p')}w"
        target = output / "cases" / case_id
        target.mkdir(parents=True)
        per_die_w = power_w / 16.0
        values = {name: [per_die_w] for name in names if name.startswith("hbf.s")}
        for source in template.glob("*.flp"):
            campaign.rewrite_floorplan(source, target / source.name, values, 1)
        (target / "package.stk").write_text(
            campaign.stack_text(
                16, "g1_constant_top_interface", 2000,
                1.0e-7, None, None, names,
            ),
            encoding="utf-8",
        )
        manifest = campaign.case_provenance(
            16, "g1_constant_top_interface", 2000, 1.0e-7, None
        )
        manifest.update({
            "case_id": case_id,
            "study_roles": ["literature_reproduction"],
            "analysis": "steady",
            "input_names": names,
            "output_names": names,
            "power": {
                "arm": "literature_aggregate_dynamic_power",
                "single_stack_bandwidth_gb_s": bandwidth_gbps,
                "hbf_total_w": power_w,
                "per_nand_die_w": per_die_w,
                "gpu_w": 0.0,
                "hbm_w": 0.0,
                "hbf_base_w": 0.0,
            },
            "literature_source": SOURCE,
            "comparison_contract": (
                "Apply only the paper-disclosed aggregate HBF power to HBFSim G1. "
                "This is a sanity comparison, not the paper's undisclosed package model."
            ),
        })
        write_json(target / "case.json", manifest)
        cases.append(f"cases/{case_id}/case.json")

    intensity = [power / bandwidth for bandwidth, power in POINTS]
    profile = {
        "schema_version": 1,
        "profile_kind": "literature_reproduction_attempt",
        "source": {**SOURCE, "pdf_sha256": campaign.sha256(source_pdf)},
        "disclosed": {
            "stack": "16-Hi HBF similar to HBM4",
            "nand": "128-layer 3D-NAND TLC dies",
            "solver": "3D-ICE steady-state",
            "access_granularity": "16-token KV block",
            "safe_junction_c": 80.0,
            "thermal_limit": {"bandwidth_gb_s": 202.27, "dynamic_power_w": 53.72},
            "power_bandwidth_points": [
                {"bandwidth_gb_s": bandwidth, "dynamic_power_w": power}
                for bandwidth, power in POINTS
            ],
            "aggregate_dynamic_energy_nj_per_byte_range": [
                min(intensity), max(intensity)
            ],
        },
        "not_disclosed_or_not_independently_identifiable": {
            "read_command_energy_j": None,
            "program_command_energy_j": None,
            "erase_command_energy_j": None,
            "base_die_idle_power_w": None,
            "read_write_mix_at_each_sweep_point": None,
            "bytes_per_16_token_block_for_each_model": None,
            "floorplan_dimensions": None,
            "layer_thicknesses": None,
            "material_properties": None,
            "boundary_and_cooling_coefficients": None,
            "plane_power_mapping": None,
        },
        "interpretation": (
            "The 0.26-0.27 nJ/byte range is aggregate dynamic power divided by "
            "single-stack bandwidth. It is not a measured read/program/erase command energy."
        ),
    }
    write_json(output / "literature-reproduction-profile.json", profile)
    write_json(output / "campaign-plan.json", {
        "schema_version": 2,
        "kind": "phase2_literature_reproduction_attempt",
        "case_count": len(cases),
        "cases": cases,
        "source_profile_sha256": campaign.sha256(
            output / "literature-reproduction-profile.json"
        ),
    })


def summarize(root: pathlib.Path) -> None:
    profile_path = root / "literature-reproduction-profile.json"
    profile = json.loads(profile_path.read_text(encoding="utf-8"))
    rows = []
    for relative in json.loads((root / "campaign-plan.json").read_text())["cases"]:
        case_path = root / relative
        case = json.loads(case_path.read_text())
        result = json.loads((case_path.parent / "run-result.json").read_text())
        rows.append({
            "bandwidth_gb_s": case["power"]["single_stack_bandwidth_gb_s"],
            "literature_dynamic_power_w": case["power"]["hbf_total_w"],
            "g1_hbf_hotspot_c": result["summary"]["final_hbf_hotspot_c"],
            "g1_thermal_resistance_k_per_w": result["summary"]["thermal_resistance_k_per_w"],
        })
    threshold = next(row for row in rows if row["bandwidth_gb_s"] == 202.27)
    g1_resistances = [row["g1_thermal_resistance_k_per_w"] for row in rows]
    summary = {
        "schema_version": 1,
        "verdict": "NOT_REPRODUCED_PUBLIC_PARAMETERS_INSUFFICIENT",
        "source_profile_sha256": campaign.sha256(profile_path),
        "comparison_rows": rows,
        "paper_threshold_point": {
            "bandwidth_gb_s": 202.27,
            "dynamic_power_w": 53.72,
            "paper_peak_c": 80.0,
            "hbf_sim_g1_peak_c": threshold["g1_hbf_hotspot_c"],
            "absolute_temperature_gap_c": abs(
                threshold["g1_hbf_hotspot_c"] - 80.0
            ),
        },
        "effective_resistance": {
            "paper_threshold_implied_k_per_w": (80.0 - 30.0) / 53.72,
            "hbf_sim_g1_median_k_per_w": statistics.median(g1_resistances),
        },
        "reason": (
            "The paper discloses stack category, NAND type, aggregate power/bandwidth "
            "points and the 80 C limit, but not enough geometry, material, boundary, "
            "cooling or per-operation energy inputs to reconstruct its 3D-ICE model. "
            "Applying the disclosed power to the independent G1 model therefore tests "
            "non-equivalence; no parameter is tuned to force agreement."
        ),
        "claim_boundary": {
            "aggregate_power_bandwidth_curve": "LITERATURE_RECORDED",
            "g1_same_power_sanity_sweep": "NUMERICALLY_REPRODUCED",
            "paper_temperature_curve": "NOT_INDEPENDENTLY_REPRODUCED",
            "command_energy": "NOT_IDENTIFIABLE",
            "calibration": "PROHIBITED",
        },
        "profile": profile,
    }
    write_json(root / "literature-reproduction-summary.json", summary)
    print(json.dumps(summary["paper_threshold_point"], indent=2))


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    prep = sub.add_parser("prepare")
    prep.add_argument("--phase1-golden-root", required=True, type=pathlib.Path)
    prep.add_argument("--source-pdf", required=True, type=pathlib.Path)
    prep.add_argument("--output", required=True, type=pathlib.Path)
    finish = sub.add_parser("summarize")
    finish.add_argument("--root", required=True, type=pathlib.Path)
    args = parser.parse_args()
    if args.command == "prepare":
        prepare(args.phase1_golden_root.resolve(), args.source_pdf.resolve(),
                args.output.resolve())
    else:
        summarize(args.root.resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
