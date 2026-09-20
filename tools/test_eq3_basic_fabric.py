import unittest

from eq3_basic_fabric import BasicFabric


def stage(latency=10, bandwidth=1_000_000_000):
    return {"latency_ns": latency, "bandwidth_bytes_per_s": bandwidth}


def fixture():
    return {
        "evidence": "SCENARIO_ASSUMPTION",
        "hbf": {
            f"hbf{i}": {
                "pair": f"hbm{i}",
                "bank_count": 2,
                "bank_capacity_bytes": 1024,
                "fill": stage(),
                "direct_link": stage(20),
                "relay_link": stage(30),
            }
            for i in range(2)
        },
        "hbm": {
            f"hbm{i}": {
                "bank_count": 2,
                "bank_capacity_bytes": 1024,
                "gpu_link": stage(40),
            }
            for i in range(2)
        },
    }


class BasicFabricTests(unittest.TestCase):
    def test_direct_and_relay_exact_timing_and_bytes(self):
        direct = BasicFabric(fixture())
        direct.enqueue("d", "hbf0", "direct", 100, 0)
        self.assertEqual(direct.next_event_ns(), 0)
        direct.advance(230)
        self.assertEqual(direct.completions()[0]["completion_ns"], 230)
        self.assertEqual(direct.completions()[0]["bytes"], 100)

        relay = BasicFabric(fixture())
        relay.enqueue("r", "hbf0", "relay", 100, 0)
        relay.advance(380)
        self.assertEqual(relay.completions()[0]["completion_ns"], 380)
        starts = [e for e in relay.events() if e["kind"] == "start"]
        self.assertEqual([e["stage"] for e in starts], ["HBF_FILL", "HBF_RELAY", "HBM_GPU"])
        self.assertTrue(all(e["bytes"] == 100 for e in starts))

    def test_two_hbf_banks_allow_direct_relay_overlap(self):
        fabric = BasicFabric(fixture())
        fabric.enqueue("direct", "hbf0", "direct", 100, 0)
        fabric.enqueue("relay", "hbf0", "relay", 100, 0)
        fabric.advance(400)
        starts = {(e["request_id"], e["stage"]): e for e in fabric.events() if e["kind"] == "start"}
        direct = starts[("direct", "HBF_DIRECT")]
        relay = starts[("relay", "HBF_RELAY")]
        self.assertLess(relay["start_ns"], direct["end_ns"])
        self.assertNotEqual(direct["hbf_bank"], relay["hbf_bank"])

    def test_hbm_local_and_relay_share_gpu_link(self):
        fabric = BasicFabric(fixture())
        fabric.enqueue("local", "hbm0", "direct", 100, 240)
        fabric.enqueue("relay", "hbf0", "relay", 100, 0)
        fabric.advance(520)
        starts = [e for e in fabric.events() if e["kind"] == "start" and e["stage"] == "HBM_GPU"]
        self.assertEqual([(e["request_id"], e["start_ns"], e["end_ns"]) for e in starts],
                         [("local", 240, 380), ("relay", 380, 520)])

    def test_pairs_have_no_package_global_lock(self):
        fabric = BasicFabric(fixture())
        fabric.enqueue("r0", "hbf0", "relay", 100, 0)
        fabric.enqueue("r1", "hbf1", "relay", 100, 0)
        fabric.advance(380)
        self.assertEqual({row["request_id"]: row["completion_ns"] for row in fabric.completions()},
                         {"r0": 380, "r1": 380})

    def test_bank_backpressure_and_complete_release_admit_order(self):
        fabric = BasicFabric(fixture())
        for request_id in ("a", "b", "c"):
            fabric.enqueue(request_id, "hbf0", "direct", 100, 0)
        fabric.advance(231)
        starts = {(e["request_id"], e["stage"]): e for e in fabric.events() if e["kind"] == "start"}
        self.assertEqual(starts[("c", "HBF_FILL")]["start_ns"], 230)
        self.assertEqual(starts[("b", "HBF_DIRECT")]["start_ns"], 230)
        at_230 = [e for e in fabric.events() if (e.get("time_ns") == 230 or e.get("start_ns") == 230)]
        self.assertEqual(at_230[0]["kind"], "complete")

    def test_unique_ids_validation_and_snapshot_isolation(self):
        fabric = BasicFabric(fixture())
        fabric.enqueue("a", "hbf0", "direct", 100, 0)
        with self.assertRaises(ValueError):
            fabric.enqueue("a", "hbf0", "direct", 100, 0)
        with self.assertRaises(ValueError):
            fabric.enqueue("large", "hbf0", "direct", 2048, 0)
        with self.assertRaises(ValueError):
            fabric.enqueue("bad-route", "hbm0", "relay", 100, 0)
        fabric.advance(1)
        with self.assertRaises(ValueError):
            fabric.enqueue("past", "hbf0", "direct", 100, 0)
        facts = fabric.immutable_facts()
        facts["config"]["hbf"]["hbf0"]["bank_count"] = 99
        self.assertEqual(fabric.immutable_facts()["config"]["hbf"]["hbf0"]["bank_count"], 2)

    def test_unpaired_hbf_supports_direct_only(self):
        config = fixture()
        config["hbf"]["hbf0"]["pair"] = None
        config["hbf"]["hbf0"]["relay_link"] = None
        fabric = BasicFabric(config)
        fabric.enqueue("d", "hbf0", "direct", 100, 0)
        with self.assertRaises(ValueError):
            fabric.enqueue("r", "hbf0", "relay", 100, 0)

    def test_backend_delivered_reservation_is_bounded_and_skips_fill(self):
        fabric = BasicFabric(fixture())
        self.assertTrue(fabric.reserve_hbf("direct", "hbf0", "direct", 100, 0))
        self.assertTrue(fabric.reserve_hbf("relay", "hbf0", "relay", 100, 0))
        self.assertFalse(fabric.reserve_hbf("outside", "hbf0", "direct", 100, 0))
        self.assertNotIn("outside", [e.get("request_id") for e in fabric.events()])
        fabric.mark_hbf_ready("direct", 100)
        fabric.mark_hbf_ready("relay", 100)
        fabric.advance(221)
        starts = [e for e in fabric.events() if e["kind"] == "start"]
        self.assertFalse(any(e["stage"] == "HBF_FILL" for e in starts))
        self.assertEqual({e["stage"] for e in starts if e["start_ns"] == 100}, {"HBF_DIRECT", "HBF_RELAY"})
        self.assertTrue(fabric.reserve_hbf("outside", "hbf0", "direct", 100, 0))

    def test_backend_ready_validation_and_same_timestamp_release(self):
        fabric = BasicFabric(fixture())
        with self.assertRaises(ValueError):
            fabric.reserve_hbf("future", "hbf0", "direct", 100, 1)
        self.assertTrue(fabric.reserve_hbf("a", "hbf0", "direct", 100, 0))
        with self.assertRaises(ValueError):
            fabric.mark_hbf_ready("missing", 0)
        fabric.mark_hbf_ready("a", 100)
        with self.assertRaises(ValueError):
            fabric.mark_hbf_ready("a", 100)
        fabric.advance(220)
        events = fabric.events()
        ready = next(i for i, e in enumerate(events) if e["kind"] == "backend_ready")
        start = next(i for i, e in enumerate(events) if e["kind"] == "start" and e["stage"] == "HBF_DIRECT")
        self.assertLess(ready, start)

    def test_hbm_backend_uses_same_bounded_banks_as_relay(self):
        fabric = BasicFabric(fixture())
        self.assertTrue(fabric.reserve_source("local0", "hbm0", "direct", 100, 0))
        self.assertTrue(fabric.reserve_source("local1", "hbm0", "direct", 100, 0))
        self.assertFalse(fabric.reserve_source("outside", "hbm0", "direct", 100, 0))
        fabric.mark_source_ready("local0", 100)
        fabric.mark_source_ready("local1", 100)
        fabric.advance(240)
        self.assertEqual([row["request_id"] for row in fabric.completions()], ["local0"])
        self.assertTrue(fabric.reserve_source("outside", "hbm0", "direct", 100, 0))

        relay = BasicFabric(fixture())
        self.assertTrue(relay.reserve_source("local0", "hbm0", "direct", 100, 0))
        self.assertTrue(relay.reserve_source("local1", "hbm0", "direct", 100, 0))
        relay.mark_source_ready("local0", 0)
        relay.mark_source_ready("local1", 0)
        relay.enqueue("relay", "hbf0", "relay", 100, 0)
        relay.advance(1_000)
        relay_start = next(e for e in relay.events() if e["kind"] == "start" and e["stage"] == "HBF_RELAY")
        self.assertEqual(relay_start["start_ns"], 140)

    def test_invalid_config_rejected(self):
        config = fixture()
        config["hbf"]["hbf0"]["bank_count"] = 1
        with self.assertRaises(ValueError):
            BasicFabric(config)
        config = fixture()
        config["hbf"]["hbf1"]["pair"] = "hbm0"
        with self.assertRaises(ValueError):
            BasicFabric(config)
        config = fixture()
        config["evidence"] = "CALIBRATED"
        with self.assertRaises(ValueError):
            BasicFabric(config)


if __name__ == "__main__":
    unittest.main()
