import unittest

from eq3_basic_hbm import BasicHbm, scenario_example


def config():
    value = scenario_example(("hbm0", "hbm1"))
    value["stacks"][0]["media_latency_ns"] = {"read": 10, "write": 20}
    value["stacks"][0]["media_bandwidth_Bps"] = {"read": 1_000_000_000,
                                                    "write": 2_000_000_000}
    value["stacks"][1]["media_latency_ns"] = {"read": 5, "write": 7}
    value["stacks"][1]["media_bandwidth_Bps"] = {"read": 1_000_000_000,
                                                    "write": 1_000_000_000}
    return value


class BasicHbmTests(unittest.TestCase):
    def test_explicit_latency_plus_ceiling_bandwidth_formula(self):
        hbm = BasicHbm(config())
        hbm.arrival({"request_id": "r", "stack_id": "hbm0", "op": "read",
                     "bytes": 5, "arrival_ns": 0})
        hbm.submit("r")
        self.assertEqual(hbm.next_event_ns(), 15)
        hbm.advance(15)
        facts = hbm.take_facts()
        start = next(item for item in facts if item["phase"] == "media_start")
        self.assertEqual(start["fixed_media_latency_ns"], 10)
        self.assertEqual(start["bandwidth_transfer_ns"], 5)
        completion = hbm.take_media_completions()[0]
        self.assertEqual(completion["time_ns"], 15)
        self.assertEqual(completion["phase"], "MEDIA_DONE")
        self.assertTrue(completion["requires_fabric"])
        self.assertFalse(completion["reported_completion"])

    def test_same_stack_fifo_and_different_stacks_progress_in_parallel(self):
        hbm = BasicHbm(config())
        for request in (
                {"request_id": "a", "stack_id": "hbm0", "op": "read", "bytes": 10,
                 "arrival_ns": 0},
                {"request_id": "b", "stack_id": "hbm0", "op": "write", "bytes": 10,
                 "arrival_ns": 0},
                {"request_id": "c", "stack_id": "hbm1", "op": "read", "bytes": 10,
                 "arrival_ns": 0}):
            hbm.arrival(request)
            hbm.submit(request["request_id"])
        self.assertEqual(hbm.next_event_ns(), 15)
        hbm.advance(20)
        ready = {item["request_id"]: item["time_ns"] for item in hbm.take_media_completions()}
        self.assertEqual(ready, {"c": 15, "a": 20})
        self.assertEqual(hbm.next_event_ns(), 45)
        hbm.advance(45)
        self.assertEqual(hbm.take_media_completions()[0]["request_id"], "b")

    def test_arrival_submit_media_and_fabric_boundaries_are_distinct(self):
        hbm = BasicHbm(config())
        hbm.arrival({"request_id": "r", "stack_id": "hbm0", "op": "write",
                     "bytes": 1, "arrival_ns": 7})
        self.assertIsNone(hbm.next_event_ns())
        self.assertEqual([item["phase"] for item in hbm.take_facts()], ["arrival"])
        hbm.submit("r", 9)
        facts = hbm.take_facts()
        self.assertEqual([item["phase"] for item in facts], ["submit", "media_start"])
        self.assertTrue(all(not item["reported_completion"] for item in facts))
        hbm.advance(30)
        completion = hbm.take_media_completions()[0]
        self.assertEqual(completion["time_ns"], 30)
        self.assertTrue(completion["requires_fabric"])

    def test_unknown_energy_die_plane_are_not_invented(self):
        hbm = BasicHbm(config())
        hbm.arrival({"request_id": "r", "stack_id": "hbm0", "op": "read",
                     "bytes": 1, "arrival_ns": 0})
        hbm.submit("r")
        hbm.advance(hbm.next_event_ns())
        completion = hbm.take_media_completions()[0]
        self.assertIsNone(completion["media_energy_j"])
        self.assertEqual(completion["media_energy_status"], "UNKNOWN_UNPARAMETERIZED")
        self.assertEqual(completion["die"], "UNKNOWN")
        self.assertEqual(completion["plane"], "UNKNOWN")

    def test_refresh_is_explicitly_unsupported_and_capabilities_are_limited(self):
        hbm = BasicHbm(config())
        self.assertEqual(hbm.refresh("hbm0")["status"], "UNSUPPORTED_CAPABILITY")
        capability = hbm.capability()
        self.assertFalse(capability["real_dram_backend"])
        self.assertEqual(capability["fabric_arbitration"], "EXTERNAL_REQUIRED")

    def test_invalid_lifecycle_and_address_placement_are_rejected(self):
        hbm = BasicHbm(config())
        with self.assertRaisesRegex(ValueError, "arrive before"):
            hbm.submit("missing")
        with self.assertRaisesRegex(ValueError, "die/plane"):
            hbm.arrival({"request_id": "bad", "stack_id": "hbm0", "op": "read",
                         "bytes": 1, "arrival_ns": 0, "die": 0})
        hbm.arrival({"request_id": "r", "stack_id": "hbm0", "op": "read",
                     "bytes": 1, "arrival_ns": 0})
        hbm.submit("r")
        with self.assertRaisesRegex(ValueError, "exactly once"):
            hbm.submit("r")
        with self.assertRaisesRegex(ValueError, "allowed range|backward"):
            hbm.advance(-1)

    def test_parameter_values_require_evidence_and_unknown_energy_stays_null(self):
        accepted = BasicHbm(config())
        self.assertEqual(accepted.capability()["evidence_class"],
                         "PARAMETRIC_HBM_SCENARIO")
        value = config()
        del value["stacks"][0]["media_latency_evidence"]
        with self.assertRaisesRegex(ValueError, "media_latency_evidence"):
            BasicHbm(value)
        value = config()
        value["stacks"][0]["energy_j_per_byte"]["read"] = 1e-12
        with self.assertRaisesRegex(ValueError, "non-UNKNOWN evidence"):
            BasicHbm(value)


if __name__ == "__main__":
    unittest.main()
