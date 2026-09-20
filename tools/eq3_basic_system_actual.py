#!/usr/bin/env python3
"""Run the four fixed basic-system cases through the actual MQSim CPU service."""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
from pathlib import Path
import shutil
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts" / "eval"))

from mqsim_service import MqsimService  # noqa: E402
from eq3_basic_fabric import BasicFabric  # noqa: E402
from eq3_basic_hbm import BasicHbm  # noqa: E402
from eq3_basic_system import BasicSystem, engineering_fixture  # noqa: E402


def _sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def derive_mqsim_case(base_profile, template_map, mode):
    hbf_count = 8 if mode == "all_hbf_direct" else 4
    required_profile = ("capacity_bytes", "page_bytes", "planes_per_die", "pages_per_block")
    if any(type(base_profile.get(key)) is not int or base_profile[key] <= 0
           for key in required_profile):
        raise ValueError("base MQSim fixture profile lacks positive integer geometry")
    if base_profile.get("channels") != 8 or base_profile.get("dies_per_channel") != 1:
        raise ValueError("base MQSim fixture must declare 8 channels and one die per channel")
    if base_profile.get("plane_allocation_scheme", "CWDP") != "CWDP":
        raise ValueError("base MQSim fixture must use CWDP placement")
    if (template_map.get("schema_version") != 1 or
            template_map.get("physical_kind") != "HBF" or
            template_map.get("route") != "direct" or
            template_map.get("address_layout") != "GLOBAL_PAGE_STRIPE_V1" or
            template_map.get("plane_allocation_scheme") != "CWDP" or
            template_map.get("page_bytes") != base_profile["page_bytes"] or
            template_map.get("channels") != 8 or
            template_map.get("dies_per_channel") != 1):
        raise ValueError("template stack map does not match the base 8-HBF fixture")
    rows = template_map.get("stacks")
    if (not isinstance(rows, list) or len(rows) != 8 or
            [row.get("id") for row in rows] != [f"hbf{i}" for i in range(8)] or
            any(row.get("declared_dies") != 1 or row.get("channels") != [i]
                for i, row in enumerate(rows))):
        raise ValueError("template stack map must explicitly name hbf0 through hbf7")
    profile = copy.deepcopy(base_profile)
    profile["name"] = f"ENGINEERING_FIXTURE_{mode}_{hbf_count}HBF_1DIE"
    profile["channels"] = hbf_count
    profile["dies_per_channel"] = 1
    profile["plane_allocation_scheme"] = "CWDP"
    denominator = (profile["page_bytes"] * profile["channels"] *
                   profile["dies_per_channel"] * profile["planes_per_die"] *
                   profile["pages_per_block"])
    if profile["capacity_bytes"] % denominator or profile["capacity_bytes"] // denominator < 4:
        raise ValueError("derived MQSim fixture geometry has invalid blocks per plane")
    stack_map = copy.deepcopy(template_map)
    stack_map.update({"page_bytes": profile["page_bytes"], "channels": hbf_count,
                      "dies_per_channel": 1,
                      "evidence": f"DERIVED_ENGINEERING_FIXTURE_{mode}"})
    stack_map["stacks"] = [
        {"id": f"hbf{i}", "declared_dies": 1, "channels": [i]}
        for i in range(hbf_count)
    ]
    return profile, stack_map


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--profile", type=Path, required=True)
    parser.add_argument("--stack-map", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--artifact-root", type=Path, default=ROOT)
    parser.add_argument("--timeout", type=float, default=30)
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    base_profile_path = args.profile.resolve(strict=True)
    template_map_path = args.stack_map.resolve(strict=True)
    base_profile = json.loads(base_profile_path.read_text())
    template_map = json.loads(template_map_path.read_text())
    page_bytes = base_profile["page_bytes"]
    summaries = []
    for mode in ("all_hbf_direct", "mixed_direct", "relay", "dash"):
        fixture = engineering_fixture(mode, page_bytes)
        directory = output / mode
        directory.mkdir()
        derived_profile, derived_map = derive_mqsim_case(base_profile, template_map, mode)
        source_profile_copy = directory / "mqsim-profile.source.json"
        source_map_copy = directory / "mqsim-stack-map.source.json"
        shutil.copyfile(base_profile_path, source_profile_copy)
        shutil.copyfile(template_map_path, source_map_copy)
        profile_path = directory / "mqsim-profile.derived.json"
        map_path = directory / "mqsim-stack-map.derived.json"
        profile_path.write_text(json.dumps(derived_profile, indent=2, sort_keys=True) + "\n")
        map_path.write_text(json.dumps(derived_map, indent=2, sort_keys=True) + "\n")
        (directory / "mqsim-input-provenance.json").write_text(json.dumps({
            "base_profile": str(base_profile_path),
            "base_profile_sha256": _sha256(base_profile_path),
            "saved_base_profile": source_profile_copy.name,
            "template_stack_map": str(template_map_path),
            "template_stack_map_sha256": _sha256(template_map_path),
            "saved_template_stack_map": source_map_copy.name,
            "derivation": "channels=topology HBF count; one channel and one die per HBF; CWDP explicit; capacity unchanged",
            "mode": mode,
        }, indent=2, sort_keys=True) + "\n")
        try:
            with MqsimService(
                    args.binary, profile_path, directory / "service", timeout=args.timeout,
                    artifact_root=args.artifact_root, stack_map=map_path,
                    native_observations=True) as mqsim:
                hbm = None if fixture["hbm"] is None else BasicHbm(fixture["hbm"])
                result = BasicSystem(
                    mode, mqsim, hbm, BasicFabric(fixture["fabric"])).run(fixture["requests"])
            result["fixture"] = fixture
            (directory / "result.json").write_text(
                json.dumps(result, indent=2, sort_keys=True, allow_nan=False) + "\n")
            summaries.append({"mode": mode, "status": "PASS",
                              "requests": len(result["completions"]),
                              "time_ns": result["time_ns"]})
        except BaseException as error:
            failure = {"mode": mode, "status": "FAIL",
                       "error_type": type(error).__name__, "error": str(error),
                       "fixture": fixture}
            (directory / "result.json").write_text(
                json.dumps(failure, indent=2, sort_keys=True, allow_nan=False) + "\n")
            summaries.append(failure)
            (output / "summary.json").write_text(
                json.dumps({"status": "FAIL", "cases": summaries}, indent=2,
                           sort_keys=True, allow_nan=False) + "\n")
            raise
    summary = {"schema_version": "eq3-basic-system-actual-v1",
               "status": "PASS", "cases": summaries,
               "claim_limit": "small CPU engineering fixtures; not research geometry"}
    (output / "summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True, allow_nan=False) + "\n")
    print(json.dumps(summary, sort_keys=True))


if __name__ == "__main__":
    main()
