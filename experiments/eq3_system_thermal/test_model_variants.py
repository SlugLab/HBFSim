#!/usr/bin/env python3
import json
import math
import tempfile
import unittest
from pathlib import Path

from model_variants import build_variant


def make_source(root: Path, reverse: bool = False) -> Path:
    root.mkdir()
    axes = [[0.0, 1.0, 2.0], [0.0, 1.0, 2.0], [0.0, 0.5, 1.0]]
    cells = []
    for z in range(2):
        for y in range(2):
            for x in range(2):
                i = z * 4 + y * 2 + x
                cells.append({"id": f"n{z}_{y}_{x}", "index": i,
                              "xyz_index": [x, y, z], "component": "package",
                              "center_m": [x + .5, y + .5, z * .5 + .25],
                              "size_m": [1.0, 1.0, .5],
                              "k_xyz_w_m_k": [2.0, 2.0, 2.0]})
    grid = {"axes_m": axes, "shape": [2, 2, 2], "cells": cells,
            "component_cells": {"package": list(range(8))}}
    normalized = {
        "boundaries": {"initial_temperature_k": 300.0,
                       "top": {"ambient_k": 300.0, "h_w_m2_k": 4.0},
                       "bottom": {"ambient_k": 300.0, "h_w_m2_k": 2.0}},
        "devices": [
            {"id": "gpu", "physical_type": "GPU", "external": False,
             "xy_m": [0.0, 0.0], "footprint_m": [1.0, 2.0]},
            {"id": "hbf0", "physical_type": "HBF", "external": False,
             "xy_m": [1.0, 0.0], "footprint_m": [1.0, 2.0]},
        ],
    }
    # At scale=1: half-cell R is .125 K/W; convection R is .5 bottom/.25 top.
    bottom_g, top_g = 1 / .625, 1 / .375
    nodes = []
    for c in cells:
        g = bottom_g if c["xyz_index"][2] == 0 else top_g
        nodes.append(f"node {c['id']} other package package -1 3 300 0 {g:.17g} 300")
    edges = []
    for c in cells:
        x, y, z = c["xyz_index"]
        for dx, dy, dz in ((1, 0, 0), (0, 1, 0), (0, 0, 1)):
            q = (x + dx, y + dy, z + dz)
            if all(q[i] < (2, 2, 2)[i] for i in range(3)):
                edges.append(f"edge {c['id']} n{q[2]}_{q[1]}_{q[0]} 2 component")
    if reverse:
        nodes.reverse(); edges.reverse()
    (root / "model.txt").write_text("HBFSIM_EQ3_THERMAL_MODEL 1\ncoupling on\n" +
                                     "\n".join(nodes + edges) + "\n")
    (root / "normalized.json").write_text(json.dumps(normalized))
    (root / "rc_grid.json").write_text(json.dumps(grid))
    (root / "rc_sensors.json").write_text(json.dumps({"sensors": []}))
    return root


def parse_model(path: Path):
    nodes, edges = {}, []
    for line in path.read_text().splitlines():
        f = line.split()
        if f and f[0] == "node":
            nodes[f[1]] = {"capacity": float(f[6]), "initial": float(f[7]),
                           "static": float(f[8]), "g": float(f[9]),
                           "boundary": float(f[10])}
        elif f and f[0] == "edge":
            edges.append((f[1], f[2], float(f[3])))
    return nodes, edges


class ModelVariantTest(unittest.TestCase):
    def test_ambient_and_external_resistance_formula_preserve_c(self):
        with tempfile.TemporaryDirectory() as td:
            source = make_source(Path(td) / "source")
            output = Path(td) / "derived"
            manifest = build_variant(source, output, ambient_k=310.0,
                                     external_resistance_scale=1.5,
                                     coupling_mode="full")
            nodes, edges = parse_model(output / "model.txt")
            # half cell=.125; scaled top external=.375, bottom=.75 K/W.
            self.assertAlmostEqual(nodes["n1_0_0"]["g"], 2.0)
            self.assertAlmostEqual(nodes["n0_0_0"]["g"], 1 / .875)
            self.assertTrue(all(n["initial"] == 310 and n["boundary"] == 310
                                and n["capacity"] == 3 for n in nodes.values()))
            self.assertEqual(len(edges), 12)
            self.assertEqual(manifest["capacity_j_k_unchanged"], 24)
            normalized = json.loads((output / "normalized.json").read_text())
            self.assertEqual(normalized["boundaries"]["initial_temperature_k"], 310)
            self.assertEqual(normalized["boundaries"]["top"]["ambient_k"], 310)
            self.assertEqual(normalized["boundaries"]["bottom"]["ambient_k"], 310)

    def test_domain_cut_preserves_vertical_and_reconstructs_conservative_laplacian(self):
        with tempfile.TemporaryDirectory() as td:
            source = make_source(Path(td) / "source")
            output = Path(td) / "derived"
            manifest = build_variant(source, output, ambient_k=300.0,
                                     external_resistance_scale=1.0,
                                     coupling_mode="no_cross_domain_lateral")
            _nodes, edges = parse_model(output / "model.txt")
            self.assertEqual(manifest["removed_edge_count"], 4)
            self.assertEqual(manifest["edge_axis_counts"]["removed_x"], 4)
            self.assertEqual(manifest["edge_axis_counts"]["kept_z"], 4)
            self.assertEqual(len(edges), 8)
            self.assertEqual(manifest["matrix_audit"]["internal_edge_energy_conservation"], "PASS")
            self.assertLess(manifest["matrix_audit"]["max_reconstructed_row_balance_residual_w_k"], 1e-12)
            # Independently assemble the internal Laplacian and check every row sum.
            ids = sorted({p for e in edges for p in e[:2]})
            at = {node_id: i for i, node_id in enumerate(ids)}
            lap = [[0.0] * len(ids) for _ in ids]
            for a, b, g in edges:
                ia, ib = at[a], at[b]
                lap[ia][ia] += g; lap[ib][ib] += g
                lap[ia][ib] -= g; lap[ib][ia] -= g
            self.assertTrue(all(math.isclose(sum(row), 0, abs_tol=1e-12) for row in lap))

    def test_node_and_edge_reordering_has_identical_derived_model(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            source_a = make_source(root / "a")
            source_b = make_source(root / "b", reverse=True)
            build_variant(source_a, root / "out_a", ambient_k=320.0,
                          external_resistance_scale=.5, coupling_mode="no_cross_domain_lateral")
            build_variant(source_b, root / "out_b", ambient_k=320.0,
                          external_resistance_scale=.5, coupling_mode="no_cross_domain_lateral")
            self.assertEqual((root / "out_a/model.txt").read_bytes(),
                             (root / "out_b/model.txt").read_bytes())

    def test_rejects_nonregistered_variant_and_existing_output(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            source = make_source(root / "source")
            with self.assertRaises(ValueError):
                build_variant(source, root / "bad", ambient_k=305.0,
                              external_resistance_scale=1.0, coupling_mode="full")
            (root / "exists").mkdir()
            with self.assertRaises(FileExistsError):
                build_variant(source, root / "exists", ambient_k=300.0,
                              external_resistance_scale=1.0, coupling_mode="full")


if __name__ == "__main__":
    unittest.main()
