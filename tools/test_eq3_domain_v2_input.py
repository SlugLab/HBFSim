import unittest

from eq3_domain_v2_input import derive


def power_fixture():
    order = [f"g{index}" for index in range(17)]
    return {
        "schema_version": "source-v1", "slot_s": .5,
        "group_order": order, "caps_W": [100.0] * 17,
        "traces": {
            "train": {"duration_s": 1.0,
                      "slots_W": [[0.0] * 17, [float(index) for index in range(17)]]},
            "development": {"duration_s": .5, "slots_W": [[100.0] * 17]},
            "new_blind": {"slots_W": "POISON_MUST_NOT_BE_TRAVERSED"},
        },
    }


def receipt_fixture(alpha=.5):
    return {
        "schema_version": "eq3-steady-envelope-v1",
        "status": "PREDICTED_ENVELOPE", "reference_qualified": False,
        "selected_alpha": alpha,
        "candidates": [
            {"alpha": 1.0, "within_limit": False, "initial_covered": True},
            {"alpha": .75, "within_limit": False, "initial_covered": True},
            {"alpha": .5, "within_limit": True, "initial_covered": True},
            {"alpha": .25, "within_limit": True, "initial_covered": True},
        ],
    }


class DomainV2InputTests(unittest.TestCase):
    def test_one_alpha_scales_all_groups_and_does_not_read_blind(self):
        derived, receipt = derive(power_fixture(), receipt_fixture(),
                                  ["train", "development"])
        self.assertEqual(set(derived["traces"]), {"train", "development"})
        self.assertFalse(derived["domain_v2"]["blind_trace_included"])
        self.assertFalse(receipt["blind_trajectory_read"])
        self.assertEqual(derived["caps_W"], [50.0] * 17)
        self.assertEqual(derived["traces"]["train"]["slots_W"][1],
                         [index * .5 for index in range(17)])
        self.assertEqual(derived["traces"]["development"]["slots_W"][0],
                         [50.0] * 17)
        for value in receipt["energy"].values():
            self.assertAlmostEqual(value["scaled_energy_j"],
                                   .5 * value["original_energy_j"])

    def test_blind_request_and_nonlargest_or_unknown_alpha_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "train/development"):
            derive(power_fixture(), receipt_fixture(), ["new_blind"])
        wrong = receipt_fixture(.25)
        with self.assertRaisesRegex(ValueError, "largest eligible"):
            derive(power_fixture(), wrong, ["train"])
        outside = receipt_fixture()
        outside["selected_alpha"] = .1
        with self.assertRaisesRegex(ValueError, "candidate set"):
            derive(power_fixture(), outside, ["train"])

    def test_missing_group_or_cap_violation_is_rejected(self):
        source = power_fixture()
        source["group_order"] = source["group_order"][:-1]
        with self.assertRaisesRegex(ValueError, "17 ordered"):
            derive(source, receipt_fixture(), ["train"])
        source = power_fixture()
        source["traces"]["train"]["slots_W"][0][0] = 101
        with self.assertRaisesRegex(ValueError, "exceeds"):
            derive(source, receipt_fixture(), ["train"])


if __name__ == "__main__":
    unittest.main()
