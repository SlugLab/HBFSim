import heapq
import unittest

from eq3_basic_fabric import BasicFabric
from eq3_basic_hbm import BasicHbm
from eq3_basic_system import BasicSystem, UnsupportedComposition, engineering_fixture
from eq3_basic_system_actual import derive_mqsim_case


def fabric_config(mode):
    return engineering_fixture(mode, 64)["fabric"]


def hbm_fixture(mode):
    value = engineering_fixture(mode, 64)["hbm"]
    return None if value is None else BasicHbm(value)


class FakeMqsim:
    """Existing-client-shaped deterministic backend used only by fixed tests."""

    def __init__(self, *, aggregate_delay=0, defer_until=None):
        self.now = 0
        self.aggregate_delay = aggregate_delay
        self.defer_until = defer_until
        self.requests = {}
        self.completions = {}
        self.observations = []
        self.native_observations = []
        self._pending = []
        self.submitted = []

    def submit(self, request):
        request = dict(request)
        request_id = request["request_id"]
        if request_id in self.requests:
            raise ValueError("duplicate fake request")
        self.requests[request_id] = request
        self.submitted.append(request)
        raw = request["issue_ns"] + 50
        heapq.heappush(self._pending, (raw + self.aggregate_delay, request_id, raw))
        for kind in (0, 1):
            self.observations.append({
                "kind": kind, "request_id": request_id,
                "arrival_ns": request["issue_ns"], "time_ns": request["issue_ns"],
                "reported_complete": 0, "bytes": request["bytes"],
                "device_outstanding": 1,
            })

    def try_submit(self, request):
        if self.defer_until is not None and self.now < self.defer_until:
            return {"submitted": False, "disposition": 1, "reason": "fixed gate",
                    "original_arrival_ns": request["issue_ns"],
                    "evaluated_ns": self.now, "target_ns": self.defer_until,
                    "backend_arrival_ns": None, "external_wait_ns": None}
        self.submit(request)
        return {"submitted": True, "disposition": 0, "reason": "gate disabled",
                "original_arrival_ns": request["issue_ns"],
                "evaluated_ns": self.now, "target_ns": None,
                "backend_arrival_ns": request["issue_ns"], "external_wait_ns": 0}

    def until(self, horizon):
        if self._pending and self._pending[0][0] <= horizon:
            reported, request_id, raw = heapq.heappop(self._pending)
            self.now = reported
            request = self.requests[request_id]
            self.observations.append({
                "kind": 2, "request_id": request_id,
                "arrival_ns": request["issue_ns"], "time_ns": raw,
                "reported_complete": reported, "bytes": request["bytes"],
                "device_outstanding": 0,
            })
            completion = {"request_id": request_id, "reported_complete": reported, "status": 0}
            self.completions[request_id] = completion
            return completion
        self.now = horizon
        return None

    def finish(self):
        if self._pending or set(self.requests) != set(self.completions):
            raise ValueError("unfinished fake MQSim")
        total = sum(row["bytes"] for row in self.requests.values())
        return {"status": "FINISHED", "issued": len(self.requests),
                "completed": len(self.completions), "issued_bytes": total,
                "completed_bytes": total, "pending": 0}


def requests_for(mode):
    return engineering_fixture(mode, 64)["requests"]


class BasicSystemTests(unittest.TestCase):
    def build(self, mode, *, mqsim=None):
        mqsim = FakeMqsim() if mqsim is None else mqsim
        return BasicSystem(mode, mqsim, hbm_fixture(mode), BasicFabric(fabric_config(mode))), mqsim

    def test_fixed_four_topology_chains_conserve_identity_and_resources(self):
        for mode in ("all_hbf_direct", "mixed_direct", "relay", "dash"):
            with self.subTest(mode=mode):
                system, mqsim = self.build(mode)
                requests = requests_for(mode)
                result = system.run(requests)
                self.assertEqual(result["topology_mode"], mode)
                self.assertEqual(len(result["completions"]), len(requests))
                self.assertEqual({row["request_id"] for row in result["completions"]},
                                 {row["request_id"] for row in requests})
                self.assertTrue(all(row["state"] == "COMPLETE" for row in result["completions"]))
                self.assertTrue(all(row["external_wait_ns"] >= 0 for row in result["completions"]))
                self.assertTrue(all(row["final_completion_ns"] >= row["backend_completion_ns"]
                                    for row in result["completions"]))
                state = result["fabric_resource_state"]
                self.assertFalse(state["unfinished"])
                self.assertTrue(all(not any(row["banks"]) for row in state["hbf"].values()))
                self.assertTrue(all(not any(row["banks"]) for row in state["hbm"].values()))
                self.assertTrue(all(row["route"] == "direct" for row in mqsim.submitted))
                expected = {stack: sum(row["stack"] == stack for row in requests)
                            for stack in result["stack_request_counts"]}
                self.assertEqual(result["stack_request_counts"], expected)
                self.assertEqual(bool(result["external_devices"]), mode == "all_hbf_direct")
                if mode == "all_hbf_direct":
                    self.assertEqual(result["external_devices"][0]["service"], "UNAVAILABLE")
                    self.assertEqual(result["external_devices"][0]["temperature"], "UNAVAILABLE")
                self.assertEqual(result["limits"]["energy"],
                                 "UNKNOWN_WHERE_UNPARAMETERIZED")

    def test_actual_case_derivation_matches_each_hbf_axis(self):
        base = {"name": "base", "capacity_bytes": 128 << 20, "page_bytes": 16384,
                "channels": 8, "dies_per_channel": 1, "planes_per_die": 1,
                "pages_per_block": 256}
        template = {"schema_version": 1, "physical_kind": "HBF", "route": "direct",
                    "address_layout": "GLOBAL_PAGE_STRIPE_V1",
                    "plane_allocation_scheme": "CWDP", "page_bytes": 16384,
                    "channels": 8, "dies_per_channel": 1,
                    "stacks": [{"id": f"hbf{i}", "declared_dies": 1, "channels": [i]}
                               for i in range(8)]}
        for mode in ("all_hbf_direct", "mixed_direct", "relay", "dash"):
            profile, mapping = derive_mqsim_case(base, template, mode)
            expected = 8 if mode == "all_hbf_direct" else 4
            self.assertEqual(profile["channels"], expected)
            self.assertEqual(len(mapping["stacks"]), expected)
            self.assertEqual([row["channels"] for row in mapping["stacks"]],
                             [[i] for i in range(expected)])
            fixture = engineering_fixture(mode, 64)
            self.assertTrue(all(sum(row["stack"] == stack for row in fixture["requests"]) >= 3
                                for stack in fixture["fabric"]["hbf"] | fixture["fabric"]["hbm"]))
            if mode == "mixed_direct":
                self.assertTrue(all(row["pair"] is None and row["relay_link"] is None
                                    for row in fixture["fabric"]["hbf"].values()))

    def test_cascaded_direct_and_non_dash_route_mix_are_rejected(self):
        system, _ = self.build("relay")
        bad = requests_for("relay")[0]
        bad["route"] = "direct"
        with self.assertRaisesRegex(ValueError, "no HBF direct"):
            system.run([bad])
        system, _ = self.build("mixed_direct")
        bad = requests_for("mixed_direct")[0]
        bad["route"] = "relay"
        with self.assertRaisesRegex(ValueError, "only direct"):
            system.run([bad])

    def test_mqsim_aggregate_bound_is_not_composed_twice(self):
        system, _ = self.build("all_hbf_direct", mqsim=FakeMqsim(aggregate_delay=1))
        with self.assertRaisesRegex(UnsupportedComposition, "UNSUPPORTED_COMPOSITION"):
            system.run([requests_for("all_hbf_direct")[0]])

    def test_full_source_banks_leave_third_request_in_external_wait(self):
        system, _ = self.build("all_hbf_direct")
        rows = []
        for index in range(3):
            rows.append({"request_id": f"queued-{index}", "stack": "hbf0",
                         "stack_local_page": index, "route": "direct", "bytes": 64,
                         "arrival_ns": 0, "operation": "read"})
        result = system.run(rows)
        by_id = {row["request_id"]: row for row in result["completions"]}
        self.assertEqual(by_id["queued-0"]["external_wait_ns"], 0)
        self.assertEqual(by_id["queued-1"]["external_wait_ns"], 0)
        self.assertGreater(by_id["queued-2"]["external_wait_ns"], 0)
        self.assertFalse(result["fabric_resource_state"]["unfinished"])

    def test_gate_defer_uses_horizon_and_submits_reserved_request_once(self):
        mqsim = FakeMqsim(defer_until=25)
        system, _ = self.build("all_hbf_direct", mqsim=mqsim)
        result = system.run([requests_for("all_hbf_direct")[0]])
        completion = result["completions"][0]
        self.assertEqual(completion["backend_submit_ns"], 25)
        self.assertEqual(completion["external_wait_ns"], 25)
        self.assertEqual(len(mqsim.submitted), 1)


if __name__ == "__main__":
    unittest.main()
