import unittest

from experiments.eq3_maintenance.repro_maintenance_energy_source_label import diagnose


class MaintenanceEnergySourceReproTests(unittest.TestCase):
    def test_actual_producer_and_legacy_fields_are_classified(self):
        receipt = diagnose()
        self.assertEqual(receipt["status"], "FIXED")
        self.assertEqual(receipt["actual_field_classification"], "HBF_MAINTENANCE")
        self.assertEqual(receipt["legacy_alias_classification"], "HBF_MAINTENANCE")
        self.assertEqual(receipt["foreground_classification"], "FOREGROUND")
        self.assertEqual(receipt["unknown_background_classification"],
                         "BACKEND_BACKGROUND")
        self.assertEqual(receipt["physics_effect"], "NONE; source is an evidence label, not a power coefficient")


if __name__ == "__main__":
    unittest.main()
