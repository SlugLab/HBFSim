#!/usr/bin/env python3
"""Build traceable Phase-II MQSim/package-thermal runtime inputs.

The certified 3D-ICE ROM uses the concise single-stack name ``hbm``.  The
Phase-II package schema deliberately requires explicit HBM stack names.  This
tool therefore produces a coefficient-identical runtime ROM whose sole change
is the semantic rename ``hbm`` -> ``hbm.s0`` and recomputes the portable v2
payload checksum.  It also derives a device profile and a matching package
profile for one controlled sensitivity experiment.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import struct
from typing import Any


def read_json(path: pathlib.Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON root must be an object: {path}")
    return value


def write_json(path: pathlib.Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n",
                    encoding="utf-8")


def payload_sha256_v2(payload: dict[str, Any]) -> str:
    encoded = bytearray(b"HBFSimRomPayloadV2\0")

    def unsigned(value: int) -> None:
        encoded.extend(struct.pack(">Q", value))

    def string(value: str) -> None:
        raw = value.encode("utf-8")
        unsigned(len(raw))
        encoded.extend(raw)

    def strings(values: list[str]) -> None:
        unsigned(len(values))
        for value in values:
            string(value)

    def doubles(values: list[float]) -> None:
        unsigned(len(values))
        for value in values:
            encoded.extend(struct.pack(">d", float(value)))

    string(payload["model_id"])
    string(payload["evidence_label"])
    unsigned(payload["sample_period_ns"])
    strings(payload["input_names"])
    strings(payload["output_names"])
    unsigned(payload["state_count"])
    for field in ("a", "b", "bias", "c", "d", "offset"):
        doubles(payload[field])
    for field in ("solver_identity", "geometry_sha256", "training_split",
                  "held_out_split"):
        string(payload[field])
    doubles([payload["held_out_max_error_c"],
             payload["held_out_p95_error_c"]])
    return hashlib.sha256(encoded).hexdigest()


def provenance(locator: str, note: str = "") -> dict[str, str]:
    result = {
        "class": "C",
        "source": "HBFSim Phase-II controlled sensitivity campaign",
        "locator": locator,
    }
    if note:
        result["note"] = note
    return result


def scalar(value: float, unit: str, locator: str,
           note: str = "") -> dict[str, Any]:
    return {
        "value": value,
        "unit": unit,
        "provenance": provenance(locator, note),
    }


def provider(watts: float, locator: str) -> dict[str, Any]:
    return {
        "kind": "synthetic",
        "provenance": provenance(locator, "constant controlled source"),
        "interpolation": "hold",
        "samples": [{"relative_time_ns": 0, "watts": watts}],
    }


def rename_runtime_rom(source: dict[str, Any]) -> dict[str, Any]:
    if source.get("schema_version") != 2:
        raise ValueError("Phase-II runtime ROM must use schema v2")
    payload = source.get("payload")
    if not isinstance(payload, dict):
        raise ValueError("ROM payload is missing")
    inputs = list(payload.get("input_names", []))
    outputs = list(payload.get("output_names", []))
    if inputs.count("hbm") != 1 or outputs.count("hbm") != 1:
        raise ValueError("certified ROM must contain one concise hbm node")
    renamed = json.loads(json.dumps(source))
    target = renamed["payload"]
    target["input_names"] = ["hbm.s0" if item == "hbm" else item
                             for item in inputs]
    target["output_names"] = ["hbm.s0" if item == "hbm" else item
                              for item in outputs]
    target["model_id"] = payload["model_id"] + "-runtime-hbm-s0"
    renamed["payload_sha256"] = payload_sha256_v2(target)
    return renamed


def build_device(base: dict[str, Any], height: int) -> dict[str, Any]:
    result = json.loads(json.dumps(base))
    result["name"] = f"phase2-g1-{height}hi-mqsim"
    old_height = int(base["dies_per_channel"])
    result["dies_per_channel"] = height
    result["capacity_bytes"] = int(base["capacity_bytes"]) * height // old_height
    return result


def build_package(args: argparse.Namespace, runtime_rom: dict[str, Any],
                  device: dict[str, Any]) -> dict[str, Any]:
    height = args.height
    names = runtime_rom["payload"]["input_names"]
    expected = ["gpu", "hbm.s0", "hbf.base"] + [
        f"hbf.s0.l{index}" for index in range(height)
    ]
    if names != expected or runtime_rom["payload"]["output_names"] != expected:
        raise ValueError("runtime ROM nodes do not match the one-stack profile")
    mappings = []
    for channel in range(int(device["channels"])):
        for die in range(height):
            mappings.append({
                "channel": channel,
                "chip": 0,
                "die": die,
                "package_stack": 0,
                "vertical_layer": die,
                "thermal_node": f"hbf.s0.l{die}",
            })
    threshold_note = (
        "TEST_ONLY_LOW_THRESHOLD; deliberately lowered to exercise control "
        "semantics and not a device qualification limit"
    )
    energy_note = (
        "sensitivity parameter; not measured or production-calibrated"
    )
    return {
        "schema_version": 1,
        "name": f"phase2-g1-{height}hi-{args.stage}",
        "stage": args.stage,
        "clock_mode": args.clock_mode,
        "ambient_c": scalar(30.0, "C", "G1 3D-ICE ambient boundary"),
        "bin_width_ns": scalar(
            runtime_rom["payload"]["sample_period_ns"], "ns",
            "certified ROM sample period"),
        "gpu_provider": provider(args.gpu_power_w, "controlled GPU source"),
        "package_architecture": "hbm_gpu_hbf",
        "near_memory": {
            "kind": "hbm3e",
            "placement": "interposer",
            "provenance": provenance(
                "Phase-II HBM architecture declaration",
                "single explicit HBM stack represented by hbm.s0"),
            "power_sources": [{
                "thermal_node": "hbm.s0",
                "provider": provider(args.hbm_power_w,
                                     "controlled explicit HBM source"),
            }],
        },
        "accelerator_power_semantics": {
            "value": "gpu_compute_plus_explicit_hbm",
            "provenance": provenance(
                "Phase-II power allocation",
                "GPU provider excludes explicit HBM source"),
        },
        "power_model_evidence_level": {
            "value": "sensitivity_only",
            "provenance": provenance("Phase-II NAND energy sweep", energy_note),
        },
        "timeline": {"enabled": True},
        "topology": {
            "physical": {
                "channels": int(device["channels"]),
                "chips_per_channel": 1,
                "dies_per_chip": height,
                "planes_per_die": int(device["planes_per_die"]),
            },
            "stack_height": height,
            "node_names": expected,
            "die_mappings": mappings,
            "provenance": provenance(
                "G1 one-stack channel aggregation",
                "all physical channels explicitly aggregate by die layer"),
        },
        "nand_energy": {
            "read": {
                "command_j": scalar(args.read_command_j, "J",
                                    "read command sensitivity", energy_note),
                "joules_per_byte": scalar(0.0, "J/byte",
                                           "read byte sensitivity", energy_note),
            },
            "program": {
                "command_j": scalar(args.program_command_j, "J",
                                    "program command sensitivity", energy_note),
                "joules_per_byte": scalar(0.0, "J/byte",
                                           "program byte sensitivity", energy_note),
            },
            "erase": {
                "command_j": scalar(args.erase_command_j, "J",
                                    "erase command sensitivity", energy_note),
                "joules_per_byte": scalar(0.0, "J/byte",
                                           "erase byte sensitivity", energy_note),
            },
        },
        "base_die": {
            "idle_w": scalar(args.base_idle_w, "W", "base-die sensitivity",
                             energy_note),
            "command_j": scalar(0.0, "J", "base command sensitivity",
                                energy_note),
            "joules_per_byte": scalar(0.0, "J/byte",
                                       "base byte sensitivity", energy_note),
            "thermal_node": "hbf.base",
        },
        "policy": {
            "light_on_c": scalar(args.light_on_c, "C", "test light on",
                                 threshold_note),
            "light_off_c": scalar(args.light_off_c, "C", "test light off",
                                  threshold_note),
            "severe_on_c": scalar(args.severe_on_c, "C", "test severe on",
                                  threshold_note),
            "severe_off_c": scalar(args.severe_off_c, "C", "test severe off",
                                   threshold_note),
            "shutdown_on_c": scalar(args.shutdown_on_c, "C", "test shutdown on",
                                    threshold_note),
            "shutdown_off_c": scalar(args.shutdown_off_c, "C",
                                     "test shutdown off", threshold_note),
            "light_scale": scalar(args.light_scale, "ratio",
                                  "controlled service scale"),
            "debounce_samples": args.debounce_samples,
            "minimum_dwell_samples": args.minimum_dwell_samples,
            "timing_provenance": provenance(
                "controlled policy timing", threshold_note),
        },
        "evidence_label": "synthetic_fixture",
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-device", type=pathlib.Path, required=True)
    parser.add_argument("--certified-rom", type=pathlib.Path, required=True)
    parser.add_argument("--output", type=pathlib.Path, required=True)
    parser.add_argument("--height", type=int, choices=(8, 16), required=True)
    parser.add_argument("--stage", choices=("read_only", "shadow", "active"),
                        required=True)
    parser.add_argument("--clock-mode",
                        choices=("model_time_replay", "live_monotonic"),
                        default="model_time_replay")
    parser.add_argument("--gpu-power-w", type=float, default=30.0)
    parser.add_argument("--hbm-power-w", type=float, default=5.0)
    parser.add_argument("--read-command-j", type=float, default=1.0e-3)
    parser.add_argument("--program-command-j", type=float, default=1.0e-3)
    parser.add_argument("--erase-command-j", type=float, default=1.0e-2)
    parser.add_argument("--base-idle-w", type=float, default=0.5)
    parser.add_argument("--light-on-c", type=float, default=45.0)
    parser.add_argument("--light-off-c", type=float, default=42.0)
    parser.add_argument("--severe-on-c", type=float, default=50.0)
    parser.add_argument("--severe-off-c", type=float, default=46.0)
    parser.add_argument("--shutdown-on-c", type=float, default=150.0)
    parser.add_argument("--shutdown-off-c", type=float, default=140.0)
    parser.add_argument("--light-scale", type=float, default=0.5)
    parser.add_argument("--debounce-samples", type=int, default=2)
    parser.add_argument("--minimum-dwell-samples", type=int, default=2)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    certified = read_json(args.certified_rom)
    runtime_rom = rename_runtime_rom(certified)
    device = build_device(read_json(args.base_device), args.height)
    package = build_package(args, runtime_rom, device)
    args.output.mkdir(parents=True, exist_ok=True)
    write_json(args.output / f"device-{args.height}hi.json", device)
    write_json(args.output / f"rom-{args.height}hi-runtime.json", runtime_rom)
    write_json(args.output / f"package-{args.height}hi-{args.stage}.json",
               package)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
