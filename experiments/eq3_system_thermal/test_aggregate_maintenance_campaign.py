import unittest

from aggregate_maintenance_campaign import observed_max


class MaintenanceAggregateTests(unittest.TestCase):
    def test_unavailable_owner_remains_unknown(self):
        self.assertIsNone(observed_max([None, None]))
        self.assertEqual(observed_max([None, 301.0, 302.0]), 302.0)


if __name__ == "__main__":
    unittest.main()
