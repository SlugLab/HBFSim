import unittest

from energy import EnergyMapper, engineering_energy_profile


class EnergyTests(unittest.TestCase):
    def mapper(self):
        rows = [{"id": name} for name in ("hbf0.die0", "hbf0.base", "hbm0.base", "gpu")]
        rows.append({"id": "hbm0.die0", "physical_type": "HBM4", "role": "array_die", "device_id": "hbm0"})
        return EnergyMapper({"components": rows}, {"hbf0": {"0": "hbf0.die0"}}, engineering_energy_profile())

    def test_read_relay_disjoint_and_no_hbm_array(self):
        result = self.mapper().map([
            dict(operation="read", stack="hbf0", channel="0", bytes=4096),
            dict(operation="relay_receive", stack="hbm0", bytes=4096),
            dict(operation="relay_send", stack="hbm0", bytes=4096),
        ])
        self.assertAlmostEqual(result["total_j"], 4096 * 54e-12, places=18)
        self.assertNotIn("hbm0.die0", result["component_energy_j"])

    def test_retry_has_heat_without_effective_delivery_requirement(self):
        result = self.mapper().map([dict(operation="retry", stack="hbf0", channel="0", bytes=4096)])
        self.assertAlmostEqual(result["total_j"], 4096 * 50e-12, places=18)
        self.assertEqual(self.mapper().map([])["total_j"], 0)

    def test_actual_hbm4_geometry_label(self):
        result = self.mapper().map([dict(operation="hbm_read", stack="hbm0", bytes=4096)])
        self.assertAlmostEqual(result["total_j"], 4096 * 42e-12, places=18)

    def test_unknown_erase_and_duplicate_facts_fail(self):
        with self.assertRaisesRegex(ValueError, "UNKNOWN"):
            self.mapper().map([dict(operation="erase", stack="hbf0", channel="0", bytes=0, operations=1)])
        row = dict(operation="read", stack="hbf0", channel="0", bytes=4096, activity_id="one")
        with self.assertRaisesRegex(ValueError, "duplicate"):
            self.mapper().map([row, row])


if __name__ == "__main__":
    unittest.main()
