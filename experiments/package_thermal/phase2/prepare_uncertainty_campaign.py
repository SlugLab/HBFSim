#!/usr/bin/env python3
"""Prepare the Phase-II steady 3D-ICE uncertainty/tornado campaign."""

from __future__ import annotations

import argparse
import copy
import pathlib
from typing import Any

import phase2_3dice_campaign as campaign


def arm_specs() -> list[dict[str, Any]]:
    return [
        {"arm": "baseline", "parameter": "baseline", "multiplier": 1.0},
        {"arm": "mold-k-0p5x", "parameter": "mold_k", "multiplier": 0.5},
        {"arm": "mold-k-2x", "parameter": "mold_k", "multiplier": 2.0},
        {"arm": "bond-k-0p5x", "parameter": "bond_k", "multiplier": 0.5},
        {"arm": "bond-k-2x", "parameter": "bond_k", "multiplier": 2.0},
        {"arm": "cooling-0p5x", "parameter": "cooling", "multiplier": 0.5},
        {"arm": "cooling-2x", "parameter": "cooling", "multiplier": 2.0},
        {"arm": "die-20um", "parameter": "die_thickness", "value_um": 20.0,
         "multiplier": 20.0 / 30.0},
        {"arm": "die-40um", "parameter": "die_thickness", "value_um": 40.0,
         "multiplier": 40.0 / 30.0},
        {"arm": "hbf-power-0p8x", "parameter": "hbf_power", "multiplier": 0.8},
        {"arm": "hbf-power-1p2x", "parameter": "hbf_power", "multiplier": 1.2},
        {"arm": "external-zero", "parameter": "gpu_hbm_power", "multiplier": 0.0,
         "gpu_w": 0.0, "hbm_w": 0.0},
        {"arm": "external-upper", "parameter": "gpu_hbm_power", "multiplier": 10.0,
         "gpu_w": 300.0, "hbm_w": 95.0},
    ]


def scaled_materials(parameter: str, multiplier: float
                     ) -> dict[str, tuple[Any, ...]]:
    result = copy.deepcopy(campaign.MATERIALS)
    name = "MOLD" if parameter == "mold_k" else (
        "BOND" if parameter == "bond_k" else None)
    if name is not None:
        conductivity, cv, evidence, source = result[name]
        result[name] = (tuple(value * multiplier for value in conductivity),
                        cv, evidence, source)
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--phase1-golden-root", type=pathlib.Path, required=True)
    parser.add_argument("--output", type=pathlib.Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    if output.exists():
        raise ValueError(f"refusing to overwrite {output}")
    output.mkdir(parents=True)
    original_materials = campaign.MATERIALS
    cases: list[str] = []
    try:
        for height in (8, 16):
            template = campaign.source_template(
                args.phase1_golden_root.resolve(), height)
            names = campaign.case_nodes(height)
            for spec in arm_specs():
                arm = spec["arm"]
                parameter = spec["parameter"]
                multiplier = float(spec["multiplier"])
                top_h = 1.0e-7 * multiplier if parameter == "cooling" else 1.0e-7
                die_um = float(spec.get("value_um", 30.0))
                hbf_total = (campaign.HBF_TOTAL_W * multiplier
                             if parameter == "hbf_power"
                             else campaign.HBF_TOTAL_W)
                gpu_w = float(spec.get("gpu_w", 30.0))
                hbm_w = float(spec.get("hbm_w", 5.0))
                case_id = f"uncertainty-{height}hi-{arm}"
                target = output / "cases" / case_id
                target.mkdir(parents=True)
                values = {name: [hbf_total / height]
                          for name in names if name.startswith("hbf.s")}
                values.update({"gpu": [gpu_w], "hbm": [hbm_w]})
                for source in template.glob("*.flp"):
                    campaign.rewrite_floorplan(
                        source, target / source.name, values, 1)
                materials = scaled_materials(parameter, multiplier)
                campaign.MATERIALS = materials
                stack = campaign.stack_text(
                    height, "g1_constant_top_interface", 2000,
                    top_h, None, None, names)
                if die_um != 30.0:
                    stack = stack.replace("source 30 MOLD ;",
                                          f"source {die_um:g} MOLD ;")
                (target / "package.stk").write_text(stack, encoding="utf-8")
                manifest = campaign.case_provenance(
                    height, "g1_constant_top_interface", 2000,
                    top_h, None)
                manifest["geometry"]["nand_die_um"] = die_um
                manifest["materials"] = {
                    name: {
                        "kx_w_per_um_k": value[0][0],
                        "ky_w_per_um_k": value[0][1],
                        "kz_w_per_um_k": value[0][2],
                        "cv_j_per_um3_k": value[1],
                        "class": value[2],
                        "source": value[3],
                    }
                    for name, value in materials.items()
                }
                manifest.update({
                    "case_id": case_id,
                    "study_roles": ["uncertainty_tornado"],
                    "analysis": "steady",
                    "input_names": names,
                    "output_names": names,
                    "uncertainty": spec,
                    "power": {
                        "arm": arm,
                        "hbf_total_w": hbf_total,
                        "per_nand_die_w": hbf_total / height,
                        "gpu_w": gpu_w,
                        "hbm_w": hbm_w,
                        "hbf_base_w": 0.0,
                    },
                })
                campaign.write_json(target / "case.json", manifest)
                cases.append(f"cases/{case_id}/case.json")
    finally:
        campaign.MATERIALS = original_materials
    campaign.write_json(output / "campaign-plan.json", {
        "schema_version": 2,
        "created_utc": campaign.utc_now(),
        "kind": "phase2_uncertainty_tornado",
        "case_count": len(cases),
        "baseline": {
            "geometry": "g1_constant_top_interface",
            "mesh_um": 2000,
            "top_h_w_per_um2_k": 1.0e-7,
            "hbf_total_w": campaign.HBF_TOTAL_W,
            "gpu_w": 30.0,
            "hbm_w": 5.0,
        },
        "arms": arm_specs(),
        "cases": cases,
    })
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
