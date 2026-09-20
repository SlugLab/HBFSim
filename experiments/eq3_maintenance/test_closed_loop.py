import heapq
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / "tools"))

from closed_loop import ClosedLoopCoordinator, UnsupportedComposition
from read_rate_policy import EngineeringProfile, ReadRatePolicy
from eq3_basic_fabric import BasicFabric
from eq3_basic_system import engineering_fixture


class FakeMqsim:
    def __init__(self, *, raw_offset=0, maintenance="UNSUPPORTED_CAPABILITY"):
        self.now = 0
        self.raw_offset = raw_offset
        self.maintenance_mode = maintenance
        self.observations, self.native_observations = [], []
        self.requests, self.completions, self.pending = {}, {}, []
        self.maintenance_events, self.maintenance_completions = [], {}

    def try_submit(self, request):
        request = dict(request)
        rid = request["request_id"]
        self.requests[rid] = request
        reported = request["issue_ns"] + 50
        heapq.heappush(self.pending, (reported, rid, "request"))
        self.observations.extend({"kind": kind, "request_id": rid,
                                  "time_ns": request["issue_ns"],
                                  "reported_complete": 0, "bytes": request["bytes"]}
                                 for kind in (0, 1))
        self.native_observations.append({"phase": "media_start", "request_id": rid,
                                         "start_ns": request["issue_ns"], "end_ns": reported,
                                         "stack": request["stack"], "bytes": request["bytes"]})
        return {"submitted": True, "disposition": 0, "reason": "fixture",
                "target_ns": None, "backend_arrival_ns": request["issue_ns"]}

    def until(self, horizon):
        if self.pending and self.pending[0][0] <= horizon:
            reported, rid, kind = heapq.heappop(self.pending)
            self.now = reported
            if kind == "maintenance":
                self.maintenance_events.append({"request_id": rid, "parent_id": rid,
                                                "state": "commit", "time_ns": reported,
                                                "transaction_id": None, "source": {},
                                                "destination": None})
                self.maintenance_completions[rid] = {
                    "request_id": rid, "parent_id": rid, "status": "COMMITTED",
                    "enqueue_ns": reported-25, "start_ns": reported-25,
                    "end_ns": reported, "mapping_committed": True,
                    "age_reset_ns": reported}
                completion = None
            else:
                request = self.requests[rid]
                self.observations.append({"kind": 2, "request_id": rid,
                                          "time_ns": reported + self.raw_offset,
                                          "reported_complete": reported,
                                          "bytes": request["bytes"]})
                completion = {"request_id": rid, "reported_complete": reported, "status": 0}
                self.completions[rid] = completion
            return completion
        self.now = horizon
        return None

    def maintain(self, request):
        if self.maintenance_mode == "UNSUPPORTED_CAPABILITY":
            return {"status": "UNSUPPORTED_CAPABILITY", "submitted": False}
        rid = request['request_id']
        heapq.heappush(self.pending, (self.now + 25, rid, "maintenance"))
        return {"status": "ACCEPTED", "maintenance_accepted": 1,
                "placement": {"stack": request["stack"]}, "target": {}}

    def finish(self):
        if self.pending:
            raise ValueError("unfinished fake backend")
        return {"status": "FINISHED", "issued": len(self.requests),
                "completed": len(self.completions), "pending": 0}


class FakeEnergy:
    def __init__(self):
        self.now = 0
        self.rows = []

    def native(self, event):
        self.rows.append({"scope": "native", "request_id": event["request_id"]})

    def hbm(self, fact):
        self.rows.append({"scope": "hbm", "request_id": fact["request_id"]})

    def fabric(self, event, request):
        if request["route"] == "relay" and "partner" not in request:
            raise AssertionError("relay partner is required")
        self.rows.append({"scope": "fabric", "request_id": event["request_id"]})

    def flush(self, boundary):
        if boundary < self.now:
            raise ValueError("energy time moved backward")
        result = {"component:test": (boundary-self.now) * 1e-12}
        self.now = boundary
        return result


class FakeThermal:
    def __init__(self, stacks, states=None):
        self.stacks = tuple(stacks)
        self.states = list(states or [])
        self.calls = []

    def advance(self, start_ns, end_ns, component_energy_j):
        state = self.states.pop(0) if self.states else "normal"
        row = {"start_ns": start_ns, "end_ns": end_ns,
               "stack_states": {stack: state for stack in self.stacks},
               "temperatures": {stack: 300.0 for stack in self.stacks}}
        self.calls.append(row)
        return row


def request(request_id, arrival=0):
    return {"request_id": request_id, "stack": "hbf0", "stack_local_page": 0,
            "route": "direct", "bytes": 64, "arrival_ns": arrival, "operation": "read"}


class ClosedLoopTests(unittest.TestCase):
    def build(self, *, end=100, raw_offset=0, policy=None, thermal_states=None,
              maintenance="UNSUPPORTED_CAPABILITY"):
        fixture = engineering_fixture("all_hbf_direct", 64)
        fabric = BasicFabric(fixture["fabric"])
        stacks = sorted(fixture["fabric"]["hbf"])
        budgets = {stack: 1024 for stack in stacks}
        policy = policy or ReadRatePolicy(EngineeringProfile(profile_id="off", window_ns=100))
        endpoint = {f"{stack}:gpu-link": 1024 for stack in stacks}
        mqsim = FakeMqsim(raw_offset=raw_offset, maintenance=maintenance)
        thermal = FakeThermal(stacks, thermal_states)
        system = ClosedLoopCoordinator(
            "all_hbf_direct", mqsim, None, fabric, thermal, FakeEnergy(), policy,
            thermal_window_ns=100, experiment_end_ns=end,
            initial_stack_budget_bytes=budgets, shared_endpoint_caps_bytes=endpoint)
        return system, mqsim, thermal

    def test_empty_foreground_still_advances_cooling_windows(self):
        system, _, thermal = self.build(end=300)
        result = system.run([])
        self.assertEqual([(r["start_ns"], r["end_ns"]) for r in thermal.calls],
                         [(0, 100), (100, 200), (200, 300)])
        self.assertEqual(result["summary"]["offered_count"], 0)

    def test_submitted_request_drains_but_does_not_count_in_observation_window(self):
        system, _, _ = self.build(end=100)
        result = system.run([request("late", 90)])
        self.assertEqual(result["summary"]["observation_completed_count"], 0)
        self.assertEqual(result["summary"]["drain_completed_count"], 1)
        self.assertGreater(result["drain_end_ns"], result["observation_end_ns"])
        self.assertEqual([row["phase"] for row in result["timeline"]["energy"]
                          if row["kind"] == "WINDOW_TOTAL"],
                         ["OBSERVATION", "DRAIN"])

    def test_same_timestamp_thermal_shutdown_precedes_new_admission(self):
        profile = EngineeringProfile(profile_id="on", enabled=True, strategy="guard_only",
                                     window_ns=100, target_bytes_per_s=10_000_000_000,
                                     step_bytes=64, minimum_budget_bytes=0,
                                     maximum_budget_bytes=1024, severe_budget_bytes=64)
        system, mqsim, _ = self.build(end=200, policy=ReadRatePolicy(profile),
                                      thermal_states=["shutdown", "shutdown"])
        result = system.run([request("before", 0), request("at-boundary", 100)])
        self.assertEqual(len(mqsim.requests), 1)
        by_id = {row["request_id"]: row for row in result["requests"]}
        self.assertEqual(by_id["before"]["state"], "COMPLETE")
        self.assertTrue(by_id["at-boundary"]["censored"])
        self.assertIsNone(by_id["at-boundary"]["backend_submit_ns"])

    def test_raw_and_reported_mismatch_is_unsupported_composition(self):
        system, _, _ = self.build(raw_offset=-1)
        with self.assertRaisesRegex(UnsupportedComposition, "raw media and reported"):
            system.run([request("mismatch")])

    def test_unsupported_maintenance_is_reported_and_age_is_not_scaled(self):
        system, _, _ = self.build()
        maintenance = [{"request_id": 1001, "stack": "hbf0", "stack_local_page": 0,
                        "due_ns": 10, "initial_age_s": 86_399.5, "bytes": 64}]
        result = system.run([], maintenance)
        self.assertEqual(result["maintenance"][0]["state"], "UNSUPPORTED_CAPABILITY")
        self.assertEqual(result["maintenance"][0]["initial_age_s"], 86_399.5)
        self.assertEqual(result["summary"]["maintenance_unsupported"], 1)

    def test_inflight_maintenance_is_drained(self):
        system, _, _ = self.build(end=100, maintenance="ACCEPTED")
        maintenance = [{"request_id": 1001, "stack": "hbf0", "stack_local_page": 0,
                        "due_ns": 90, "initial_age_s": 86_400, "bytes": 64}]
        result = system.run([], maintenance)
        self.assertEqual(result["maintenance"][0]["state"], "COMMITTED")
        self.assertEqual(result["summary"]["maintenance_committed"], 1)
        self.assertGreater(result["drain_end_ns"], 100)


if __name__ == "__main__":
    unittest.main()
