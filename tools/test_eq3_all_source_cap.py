import json
import unittest
from pathlib import Path

from eq3_all_source_cap import build
from eq3_layered_ir import normalize


ROOT = Path(__file__).resolve().parents[1]


class AllSourceCapTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.profile = json.loads(
            (ROOT / "configs/eq3_thermal/research/candidate_profile.json").read_text())
        cls.power = json.loads(
            (ROOT / "configs/eq3_thermal/research/calibration_power.json").read_text())

    def synthetic_grid(self):
        inline = {"id": "cap", "duration_s": self.power["slot_s"],
                  "slots_W": [dict(zip(self.power["group_order"],
                                        self.power["caps_W"], strict=True))]}
        ir = normalize(self.profile, self.power, inline)
        cells = []
        component_cells = {}
        for component in ir["components"]:
            if not component["powered"]:
                continue
            component_cells[component["id"]] = [len(cells)]
            cells.append({"id": f"n{len(cells)}", "component": component["id"],
                          "volume_m3": component["volume_m3"]})
        return {"cells": cells, "component_cells": component_cells}

    def test_all_seventeen_public_caps_are_mapped_and_conserved(self):
        events, receipt = build(self.profile, self.power, self.synthetic_grid())
        self.assertEqual(receipt["status"], "CAP_INPUT_GENERATED_NOT_SOLVED")
        self.assertFalse(receipt["blind_trajectory_read"])
        self.assertEqual(len(receipt["caps_w"]), 17)
        self.assertAlmostEqual(receipt["total_cap_w"], 840.0)
        self.assertAlmostEqual(sum(receipt["node_cap_w"].values()), 840.0)
        self.assertEqual(len(events.splitlines()), receipt["event_count"] + 1)
        self.assertEqual(set(receipt["group_members"]), set(self.power["group_order"]))

    def test_cap_identity_or_grid_coverage_cannot_silently_fallback(self):
        bad = dict(self.power)
        bad["group_order"] = self.power["group_order"][:-1]
        with self.assertRaisesRegex(ValueError, "17 unique"):
            build(self.profile, bad, self.synthetic_grid())
        grid = self.synthetic_grid()
        grid["component_cells"].pop(next(iter(grid["component_cells"])))
        with self.assertRaisesRegex(ValueError, "lacks powered component"):
            build(self.profile, self.power, grid)


if __name__ == "__main__":
    unittest.main()
