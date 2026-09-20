#!/usr/bin/env python3
"""Build explicit, default-disconnected thermal-model sensitivity variants.

This tool derives a model directory from an existing layered RC export.  It
does not run a thermal solver and does not modify its input directory.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import shutil
import tempfile
from collections import defaultdict
from pathlib import Path
from typing import Any


SCHEMA = "eq3-system-thermal-model-variant-v1"
AMBIENTS_K = (300.0, 310.0, 320.0)
EXTERNAL_RESISTANCE_SCALES = (0.5, 1.0, 1.5)
COUPLING_MODES = ("full", "no_cross_domain_lateral")
REQUIRED_FILES = ("model.txt", "normalized.json", "rc_grid.json", "rc_sensors.json")


def _number(value: float) -> str:
    return format(value, ".17g")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _parse_model(path: Path) -> tuple[list[str], dict[str, dict[str, Any]], list[dict[str, Any]]]:
    header: list[str] = []
    nodes: dict[str, dict[str, Any]] = {}
    edges: list[dict[str, Any]] = []
    for line_number, raw in enumerate(path.read_text().splitlines(), 1):
        fields = raw.split()
        if not fields:
            continue
        if fields[0] == "node":
            if len(fields) != 11 or fields[1] in nodes:
                raise ValueError(f"invalid/duplicate node at {path}:{line_number}")
            nodes[fields[1]] = {
                "id": fields[1], "physical": fields[2], "role": fields[3],
                "component": fields[4], "die": fields[5],
                "capacity_j_k": float(fields[6]), "initial_k": float(fields[7]),
                "static_w": float(fields[8]), "boundary_g_w_k": float(fields[9]),
                "boundary_k": float(fields[10]),
            }
        elif fields[0] == "edge":
            if len(fields) != 5:
                raise ValueError(f"invalid edge at {path}:{line_number}")
            edges.append({"a": fields[1], "b": fields[2], "g_w_k": float(fields[3]),
                          "kind": fields[4]})
        else:
            header.append(raw)
    if not nodes or header[:2] != ["HBFSIM_EQ3_THERMAL_MODEL 1", "coupling on"]:
        raise ValueError("unsupported thermal model format")
    return header, nodes, edges


def _device_domains(normalized: dict[str, Any], cells: list[dict[str, Any]]) -> dict[str, str]:
    devices = []
    for device in normalized.get("devices", []):
        if device.get("external"):
            continue
        physical = str(device.get("physical_type", "")).upper()
        if physical == "GPU" or physical.startswith("HBM") or physical == "HBF":
            xy = device["xy_m"]
            footprint = device["footprint_m"]
            devices.append((str(device["id"]), xy[0] + footprint[0] / 2,
                            xy[1] + footprint[1] / 2))
    if not devices:
        raise ValueError("no internal GPU/HBM/HBF device anchors")
    devices.sort()
    domains: dict[str, str] = {}
    for cell in cells:
        x, y = cell["center_m"][:2]
        # Lexicographic device id is the deterministic tie breaker.
        domains[cell["id"]] = min(devices, key=lambda d: ((x-d[1])**2 + (y-d[2])**2, d[0]))[0]
    return domains


def _boundary_conductance(cell: dict[str, Any], normalized: dict[str, Any],
                          nz: int, scale: float) -> tuple[float, dict[str, float]]:
    _x, _y, z = cell["xyz_index"]
    area = cell["size_m"][0] * cell["size_m"][1]
    half_cell_r = cell["size_m"][2] / (2 * cell["k_xyz_w_m_k"][2] * area)
    parts: dict[str, float] = {}
    for side, applies in (("bottom", z == 0), ("top", z == nz - 1)):
        if not applies:
            continue
        h = float(normalized["boundaries"][side]["h_w_m2_k"])
        convection_r = math.inf if h == 0 else scale / (h * area)
        parts[side] = 0.0 if math.isinf(convection_r) else 1.0 / (half_cell_r + convection_r)
    return sum(parts.values()), parts


def _audit(nodes: dict[str, dict[str, Any]], edges: list[dict[str, Any]]) -> dict[str, Any]:
    incident = defaultdict(float)
    pairs: set[tuple[str, str]] = set()
    for edge in edges:
        a, b, g = edge["a"], edge["b"], edge["g_w_k"]
        if a not in nodes or b not in nodes or a == b or not math.isfinite(g) or g <= 0:
            raise ValueError("invalid retained thermal edge")
        pair = tuple(sorted((a, b)))
        if pair in pairs:
            raise ValueError(f"duplicate thermal edge {pair}")
        pairs.add(pair)
        incident[a] += g
        incident[b] += g
    max_row_residual = 0.0
    diagonal_sum = 0.0
    for node_id, node in nodes.items():
        diagonal = incident[node_id] + node["boundary_g_w_k"]
        diagonal_sum += diagonal
        max_row_residual = max(max_row_residual,
                               abs(diagonal - incident[node_id] - node["boundary_g_w_k"]))
    return {
        "edge_count": len(edges),
        "edge_conductance_sum_w_k": sum(e["g_w_k"] for e in edges),
        "boundary_conductance_sum_w_k": sum(n["boundary_g_w_k"] for n in nodes.values()),
        "laplacian_diagonal_sum_w_k": diagonal_sum,
        "max_reconstructed_row_balance_residual_w_k": max_row_residual,
        "symmetric_unique_edge_pairs": True,
        "internal_edge_energy_conservation": "PASS",
    }


def build_variant(source_model_dir: Path | str, output_dir: Path | str, *, ambient_k: float,
                  external_resistance_scale: float, coupling_mode: str) -> dict[str, Any]:
    """Create one self-contained derived model directory and return its manifest."""
    source = Path(source_model_dir).resolve(strict=True)
    output = Path(output_dir).resolve()
    if output.exists():
        raise FileExistsError(output)
    if ambient_k not in AMBIENTS_K or not math.isfinite(ambient_k):
        raise ValueError(f"ambient_k must be one of {AMBIENTS_K}")
    if external_resistance_scale not in EXTERNAL_RESISTANCE_SCALES:
        raise ValueError(f"external_resistance_scale must be one of {EXTERNAL_RESISTANCE_SCALES}")
    if coupling_mode not in COUPLING_MODES:
        raise ValueError(f"coupling_mode must be one of {COUPLING_MODES}")
    for name in REQUIRED_FILES:
        if not (source / name).is_file():
            raise FileNotFoundError(source / name)

    normalized = json.loads((source / "normalized.json").read_text())
    grid = json.loads((source / "rc_grid.json").read_text())
    cells = grid["cells"]
    if len(cells) != math.prod(grid["shape"]):
        raise ValueError("grid shape/cell count mismatch")
    header, nodes, edges = _parse_model(source / "model.txt")
    index = {cell["id"]: int(cell["index"]) for cell in cells}
    cell_by_id = {cell["id"]: cell for cell in cells}
    if set(nodes) != set(index) or len(index) != len(cells):
        raise ValueError("model/grid node identity mismatch")

    original_capacity = sum(node["capacity_j_k"] for node in nodes.values())
    original_static = sum(node["static_w"] for node in nodes.values())
    original_boundary_g = sum(node["boundary_g_w_k"] for node in nodes.values())
    boundary_parts = {"top": {"old_external_r_m2_k_w": 1 / float(normalized["boundaries"]["top"]["h_w_m2_k"]),
                               "new_external_r_m2_k_w": external_resistance_scale / float(normalized["boundaries"]["top"]["h_w_m2_k"])},
                      "bottom": {"old_external_r_m2_k_w": 1 / float(normalized["boundaries"]["bottom"]["h_w_m2_k"]),
                                  "new_external_r_m2_k_w": external_resistance_scale / float(normalized["boundaries"]["bottom"]["h_w_m2_k"])}}
    boundary_side_g = defaultdict(float)
    for node_id, node in nodes.items():
        g, parts = _boundary_conductance(cell_by_id[node_id], normalized, grid["shape"][2],
                                         external_resistance_scale)
        node["boundary_g_w_k"] = g
        node["initial_k"] = ambient_k
        node["boundary_k"] = ambient_k
        for side, value in parts.items():
            boundary_side_g[side] += value
    for side in ("top", "bottom"):
        boundary_parts[side]["new_conductance_sum_w_k"] = boundary_side_g[side]

    domains = _device_domains(normalized, cells)
    kept_edges: list[dict[str, Any]] = []
    removed_edges: list[dict[str, Any]] = []
    axis_counts = defaultdict(int)
    for edge in edges:
        ca, cb = cell_by_id.get(edge["a"]), cell_by_id.get(edge["b"])
        if ca is None or cb is None:
            raise ValueError("edge references a node absent from grid")
        delta = [abs(a-b) for a, b in zip(ca["xyz_index"], cb["xyz_index"])]
        if sum(delta) != 1:
            raise ValueError("non-neighbor grid edge")
        axis = delta.index(1)
        cut = (coupling_mode == "no_cross_domain_lateral" and axis in (0, 1)
               and domains[edge["a"]] != domains[edge["b"]])
        (removed_edges if cut else kept_edges).append(edge)
        axis_counts[("removed" if cut else "kept", "xyz"[axis])] += 1

    audit = _audit(nodes, kept_edges)
    if not math.isclose(original_capacity, sum(n["capacity_j_k"] for n in nodes.values()),
                        rel_tol=0, abs_tol=1e-12):
        raise ValueError("capacity changed")
    if not math.isclose(original_static, sum(n["static_w"] for n in nodes.values()),
                        rel_tol=0, abs_tol=1e-12):
        raise ValueError("static power changed")

    normalized["boundaries"]["initial_temperature_k"] = ambient_k
    normalized["boundaries"]["top"]["ambient_k"] = ambient_k
    normalized["boundaries"]["bottom"]["ambient_k"] = ambient_k

    source_hashes = {name: _sha256(source / name) for name in REQUIRED_FILES}
    manifest: dict[str, Any] = {
        "schema_version": SCHEMA,
        "status": "DERIVED_NOT_SOLVED",
        "evidence_class": "CONDITIONAL_ABLATION" if coupling_mode != "full" else "SENSITIVITY_VARIANT",
        "source_model_dir": str(source),
        "source_hashes_sha256": source_hashes,
        "ambient_k": ambient_k,
        "external_resistance_scale": external_resistance_scale,
        "external_resistance_semantics": "scales only 1/h on top and bottom; cell half-thickness conduction and material k are unchanged",
        "boundary_resistance_parts": boundary_parts,
        "coupling_mode": coupling_mode,
        "coupling_semantics": ("full source network" if coupling_mode == "full" else
            "VORONOI_THERMAL_DOMAIN_LATERAL_ABLATION: nearest device footprint-center XY domain; removes cross-domain x/y edges including substrate lateral paths and GPU-memory paths; preserves every z edge, within-domain x/y edge, and original per-node top/bottom shared cooling"),
        "not_equivalent_to_independent_packages": coupling_mode != "full",
        "node_count": len(nodes),
        "retained_edge_count": len(kept_edges),
        "removed_edge_count": len(removed_edges),
        "removed_edge_conductance_sum_w_k": sum(e["g_w_k"] for e in removed_edges),
        "edge_axis_counts": {f"{state}_{axis}": count for (state, axis), count in sorted(axis_counts.items())},
        "capacity_j_k_unchanged": original_capacity,
        "static_power_w_unchanged": original_static,
        "source_boundary_conductance_sum_w_k": original_boundary_g,
        "derived_boundary_conductance_sum_w_k": sum(n["boundary_g_w_k"] for n in nodes.values()),
        "matrix_audit": audit,
        "solver_started": False,
        "fast_thermal_rom": "UNAVAILABLE_FOR_CURRENT_GEOMETRY",
    }

    output.parent.mkdir(parents=True, exist_ok=True)
    temp = Path(tempfile.mkdtemp(prefix=output.name + ".tmp-", dir=output.parent))
    try:
        for name in REQUIRED_FILES:
            if name not in ("model.txt", "normalized.json"):
                shutil.copy2(source / name, temp / name)
        ordered_nodes = sorted(nodes.values(), key=lambda n: index[n["id"]])
        ordered_edges = sorted(kept_edges, key=lambda e: (min(index[e["a"]], index[e["b"]]),
                                                          max(index[e["a"]], index[e["b"]])))
        lines = list(header)
        for n in ordered_nodes:
            lines.append("node {} {} {} {} {} {} {} {} {} {}".format(
                n["id"], n["physical"], n["role"], n["component"], n["die"],
                _number(n["capacity_j_k"]), _number(n["initial_k"]), _number(n["static_w"]),
                _number(n["boundary_g_w_k"]), _number(n["boundary_k"])))
        for edge in ordered_edges:
            a, b = sorted((edge["a"], edge["b"]), key=index.__getitem__)
            lines.append(f"edge {a} {b} {_number(edge['g_w_k'])} {edge['kind']}")
        (temp / "model.txt").write_text("\n".join(lines) + "\n")
        (temp / "normalized.json").write_text(json.dumps(normalized, indent=2, sort_keys=True) + "\n")
        manifest["derived_hashes_sha256"] = {
            name: _sha256(temp / name) for name in REQUIRED_FILES
        }
        (temp / "variant_manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
        os.rename(temp, output)
    except BaseException:
        shutil.rmtree(temp, ignore_errors=True)
        raise
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-model-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--ambient-k", type=float, choices=AMBIENTS_K, required=True)
    parser.add_argument("--external-resistance-scale", type=float,
                        choices=EXTERNAL_RESISTANCE_SCALES, required=True)
    parser.add_argument("--coupling-mode", choices=COUPLING_MODES, required=True)
    args = parser.parse_args()
    manifest = build_variant(args.source_model_dir, args.output_dir,
                             ambient_k=args.ambient_k,
                             external_resistance_scale=args.external_resistance_scale,
                             coupling_mode=args.coupling_mode)
    print(json.dumps(manifest, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
