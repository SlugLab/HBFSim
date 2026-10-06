import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import ucie_placement as placement


LAYOUT = Path(sys.argv.pop(1)) if len(sys.argv) > 1 else Path(__file__).resolve().parents[1] / "canonical-147.json"


class PlacementTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.layout = json.loads(LAYOUT.read_text())
        assert len(cls.layout["placements"]) == 147

    def selected(self, n, reverse=False):
        rows = [r for r in self.layout["placements"] if
                r["aliases"][0] in placement._GLOBALS or
                (placement._LAYER.match(r["aliases"][0]) and
                 int(placement._LAYER.match(r["aliases"][0]).group(1)) < n)]
        # Registration order is the live address order. Change that order
        # between runs without changing any canonical physical address.
        if reverse:
            rows.reverse()
        out = []
        for i, row in enumerate(rows):
            s = SimpleNamespace(address=0x100000 + i * 0x100000000,
                                size=row["storage_bytes"],
                                aliases=list(row["aliases"]))
            out.append((s, s.size))
        return out

    def generate(self, rows):
        with tempfile.TemporaryDirectory() as tmp:
            result = placement.generate_manifest(LAYOUT, rows, 16384,
                                                 Path(tmp) / "manifest.json")
            return result

    def test_five_selections_and_live_order(self):
        canonical = {tuple(r["aliases"]): r["canonical_physical_address"]
                     for r in self.layout["placements"]}
        for n, count in [(1, 12), (2, 21), (4, 39), (8, 75), (16, 147)]:
            for reverse in (False, True):
                output = self.generate(self.selected(n, reverse))
                rows = output["manifest"]["placements"]
                self.assertEqual(len(rows), count)
                self.assertEqual([r["range_id"] for r in rows],
                                 list(range(1, count + 1)))
                self.assertEqual({b["canonical_physical_address"] for b in
                                  output["bindings"]},
                                 {canonical[tuple(b["aliases"])] for b in
                                  output["bindings"]})
                for i, row in enumerate(rows):
                    self.assertEqual(row["registered_address"],
                                     0x100000 + i * 0x100000000)
                    if i:
                        previous = rows[i - 1]
                        self.assertEqual(row["file_offset"],
                                         previous["file_offset"] +
                                         placement._extent(previous["length"], 16384))

    def test_module_capacity_and_padding(self):
        seen = []
        stacks = set()
        for row in self.layout["placements"]:
            address = row["canonical_physical_address"]
            extent = row["media_extent_bytes"]
            stack, module = row["stack_id"], row["module_id"]
            stacks.add(stack)
            low = stack * placement.STACK_CAPACITY + module * placement.MODULE_CAPACITY
            self.assertGreaterEqual(address, low)
            self.assertLessEqual(address + extent, low + placement.MODULE_CAPACITY)
            self.assertEqual(address % 16384, 0)
            seen.append((address, address + extent))
        self.assertEqual(stacks, {0, 1, 2, 3})
        seen.sort()
        self.assertTrue(all(a[1] <= b[0] for a, b in zip(seen, seen[1:])))

    def test_negative_identity_truncation_order_and_corruption(self):
        rows = self.selected(1)
        wrong = copy.deepcopy(rows)
        wrong[0][0].aliases = ["missing.weight"]
        with self.assertRaises(ValueError): self.generate(wrong)
        wrong = copy.deepcopy(rows)
        wrong[0][0].size += 1
        with self.assertRaises(ValueError): self.generate(wrong)
        wrong = [(s, size - 1 if i == 0 else size)
                 for i, (s, size) in enumerate(copy.deepcopy(rows))]
        with self.assertRaises(ValueError): self.generate(wrong)
        wrong = copy.deepcopy(rows)
        wrong[1][0].aliases = wrong[0][0].aliases
        with self.assertRaises(ValueError): self.generate(wrong)
        wrong = copy.deepcopy(rows)
        wrong[0][0].address = wrong[1][0].address + 1
        with self.assertRaises(ValueError): self.generate(wrong)
        with tempfile.TemporaryDirectory() as tmp:
            corrupt = copy.deepcopy(self.layout)
            corrupt["placements"][1]["canonical_physical_address"] = \
                corrupt["placements"][0]["canonical_physical_address"]
            path = Path(tmp) / "bad.json"
            path.write_text(json.dumps(corrupt))
            with self.assertRaises(ValueError):
                placement.generate_manifest(path, rows, 16384, Path(tmp) / "out.json")
        with self.assertRaises(ValueError):
            self.generate([(rows[0][0], rows[0][1])] * 2)


if __name__ == "__main__":
    unittest.main()
