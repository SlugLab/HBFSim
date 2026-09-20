#!/usr/bin/env python3
"""Build the Q4 eight-HBF package profile from the frozen mixed profile.

This is a geometry/template conversion only.  It does not calibrate power,
start a solver, or give the resulting grid the P2 spatial qualification.
"""

from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path
from typing import Any, Mapping


PAIRING = (
    ("hbm0", "hbf0", "hbf4"),
    ("hbm1", "hbf1", "hbf5"),
    ("hbm2", "hbf2", "hbf6"),
    ("hbm3", "hbf3", "hbf7"),
)


def _by_id(rows: list[dict[str, Any]], what: str) -> dict[str, dict[str, Any]]:
    result = {str(row.get("id")): row for row in rows}
    if len(result) != len(rows) or "None" in result:
        raise ValueError(f"{what} identities must be present and unique")
    return result


def _replace_prefix(value: Any, source: str, target: str) -> Any:
    if isinstance(value, str) and (value == source or value.startswith(source + ".")):
        return target + value[len(source):]
    return value


def convert_profile(source: Mapping[str, Any]) -> dict[str, Any]:
    """Return an all-HBF deep copy, rejecting anything outside the frozen shape."""
    result = copy.deepcopy(dict(source))
    placements = list(result.get("placements", []))
    blocks = list(result.get("blocks", []))
    placement_by_id = _by_id(placements, "placement")

    expected = {"gpu", *(x for pair in PAIRING for x in pair[:2])}
    if set(placement_by_id) != expected:
        raise ValueError(
            "source must be the one-GPU, hbm0..3, hbf0..3 mixed candidate; "
            f"found {sorted(placement_by_id)}"
        )

    block_ids = {str(block.get("id")) for block in blocks}
    if len(block_ids) != len(blocks):
        raise ValueError("block identities must be present and unique")

    new_placements: list[dict[str, Any]] = [copy.deepcopy(placement_by_id["gpu"])]
    new_placements.extend(copy.deepcopy(placement_by_id[f"hbf{i}"]) for i in range(4))
    new_blocks = [
        copy.deepcopy(block)
        for block in blocks
        if not any(str(block["id"]).startswith(f"hbm{i}.") for i in range(4))
    ]

    for old_hbm, template_hbf, new_hbf in PAIRING:
        target = placement_by_id[old_hbm]
        template = placement_by_id[template_hbf]
        if target.get("footprint_um") != template.get("footprint_um"):
            raise ValueError(
                f"{old_hbm} and {template_hbf} footprint/orientation differ; rotation is unsupported"
            )
        if target.get("array_die_count") != 12 or template.get("array_die_count") != 16:
            raise ValueError(f"unexpected die counts for {old_hbm}/{template_hbf}")
        translated = copy.deepcopy(template)
        translated.update({
            "id": new_hbf,
            "xy_um": copy.deepcopy(target["xy_um"]),
            "array_die_count": 16,
        })
        new_placements.append(translated)

        template_blocks = [
            block for block in blocks if str(block["id"]).startswith(template_hbf + ".")
        ]
        if len(template_blocks) != 35:
            raise ValueError(f"{template_hbf} must contain the complete 35-block 16-die template")
        die_indices = sorted(
            int(block["die_index"])
            for block in template_blocks
            if str(block["id"]).startswith(template_hbf + ".die")
        )
        if die_indices != list(range(16)):
            raise ValueError(f"{template_hbf} does not contain exactly die0..die15")

        delta = [target["xy_um"][i] - template["xy_um"][i] for i in range(2)]
        for original in template_blocks:
            cloned = copy.deepcopy(original)
            cloned["id"] = _replace_prefix(cloned["id"], template_hbf, new_hbf)
            cloned["device"] = "HBF"
            if "parent_device_id" in cloned:
                cloned["parent_device_id"] = _replace_prefix(
                    cloned["parent_device_id"], template_hbf, new_hbf
                )
            if cloned.get("power_group") is not None:
                cloned["power_group"] = _replace_prefix(
                    cloned["power_group"], template_hbf, new_hbf
                )
            cloned["xyz_um"][0] += delta[0]
            cloned["xyz_um"][1] += delta[1]
            new_blocks.append(cloned)

    result["profile_id"] = "eq3-all-hbf-direct-8-external-gddr-conditional-v1"
    result["status"] = "ENGINEERING_MODEL; STATIC_CONVERSION_VALIDATION_REQUIRED"
    result["purpose"] = (
        "Q4 complete-package thermal geometry: eight HBF stacks and one external "
        "physical GDDR identity outside the package thermal domain"
    )
    result["approval_scope"] = (
        "USER_CONFIRMED_EQ3_ISOLATED_MAINTENANCE_CAMPAIGN_V1; geometry/template "
        "conversion only; no P2 spatial qualification inherited"
    )
    result["placements"] = new_placements
    result["blocks"] = new_blocks

    selection = result.setdefault("device_selection", {})
    selection.setdefault("hbm", {})["count"] = 0
    selection.setdefault("hbf", {})["count"] = 8
    selection["gddr"] = {
        "profile_id": "external_physical_gddr_identity_v1",
        "physical_type": "GDDR",
        "package_geometry_modeled": False,
        "package_temperature": "UNAVAILABLE",
        "service_energy_scope": "SYSTEM_ONLY_NOT_PACKAGE_HEAT",
    }

    hbf_ids = [f"hbf{i}" for i in range(8)]
    result["data_topology"] = {
        "kind": "all_hbf_direct",
        "stack_count": 8,
        "hbm_count": 0,
        "hbf_count": 8,
        "coordinates_do_not_change_on_graph_rewiring": True,
        "gpu_links": [["gpu", identity] for identity in hbf_ids]
        + [["gpu", "external_fast_memory"]],
        "external_fast_memory_profile": copy.deepcopy(selection["gddr"]),
        "hbf_gpu_facing_phy": result.get("data_topology", {}).get(
            "hbf_gpu_facing_phy", "UNKNOWN_BLOCKING for service/area claims"
        ),
        "link_and_nand_arbitration": "PROVIDED_BY_ISOLATED_EXPERIMENT_FABRIC_NOT_THERMAL_MODEL",
    }
    result.setdefault("non_claims", []).append(
        "Q4 geometry does not inherit P2 grid convergence or external GDDR package temperature"
    )
    result["conversion_provenance"] = {
        "kind": "USER_CONFIRMED_TEMPLATE_TRANSLATION",
        "pairs": [
            {"removed_slot": old, "template": template, "new_stack": new}
            for old, template, new in PAIRING
        ],
        "preserved": ["package", "materials", "boundaries", "background", "gpu"],
    }
    return result


def fixture_power(profile: Mapping[str, Any]) -> dict[str, Any]:
    """One 20 ms all-source mapping fixture; values are not research workload data."""
    powered = sorted(
        str(block["id"])
        for block in profile["blocks"]
        if block.get("powered", block.get("power_group") is not None)
    )
    values = {identity: 1.0 for identity in powered}
    return {
        "schema_version": "eq3-q4-engineering-power-fixture-v1",
        "status": "ENGINEERING_FIXTURE",
        "evidence_kind": "SCENARIO_ASSUMPTION",
        "claim_scope": "component mapping and energy conservation only",
        "mode": "per_component",
        "power_unit": "W",
        "initial_k": 300.0,
        "traces": {
            "mapping_20ms": {
                "duration_s": 0.02,
                "intervals": [{"start_s": 0.0, "end_s": 0.02, "power_w": values}],
            }
        },
    }


def _write_new(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, indent=2, sort_keys=True, allow_nan=False)
        stream.write("\n")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-profile", type=Path, required=True)
    parser.add_argument("--output-profile", type=Path, required=True)
    parser.add_argument("--output-fixture-power", type=Path, required=True)
    args = parser.parse_args()
    source = json.loads(args.input_profile.read_text(encoding="utf-8"))
    converted = convert_profile(source)
    _write_new(args.output_profile, converted)
    _write_new(args.output_fixture_power, fixture_power(converted))


if __name__ == "__main__":
    main()
