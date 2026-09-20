import copy
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / "tools"))

from eq3_basic_fabric import BasicFabric
from eq3_basic_system import engineering_fixture
from incremental_fabric import IncrementalBasicFabric


def drive(fabric):
    snapshots = []
    requests = [
        ("d0", "hbf0", "direct", 64, 0, 7),
        ("d1", "hbf0", "direct", 64, 0, 9),
        ("r0", "hbf1", "relay", 64, 0, 8),
    ]
    for request_id, stack, route, size, arrival, ready in requests:
        assert fabric.reserve_source(request_id, stack, route, size, arrival)
        fabric.mark_source_ready(request_id, ready)
    snapshots.append((fabric.events(), fabric.completions(), fabric.resource_state()))
    while fabric.next_event_ns() is not None:
        fabric.advance(fabric.next_event_ns())
        snapshots.append((fabric.events(), fabric.completions(), fabric.resource_state()))
    return snapshots


class IncrementalFabricTests(unittest.TestCase):
    def config(self):
        return engineering_fixture("dash", 64)["fabric"]

    def test_schedule_events_completions_and_resources_match_default(self):
        default = drive(BasicFabric(copy.deepcopy(self.config())))
        incremental = drive(IncrementalBasicFabric(copy.deepcopy(self.config())))
        self.assertEqual(default, incremental)

    def test_cursors_return_each_observation_once_without_aliasing(self):
        fabric = IncrementalBasicFabric(self.config())
        event_cursor = completion_cursor = 0
        seen_events, seen_completions = [], []
        for request_id, stack, route in (("d", "hbf0", "direct"),
                                         ("r", "hbf1", "relay")):
            self.assertTrue(fabric.reserve_source(request_id, stack, route, 64, 0))
            event_cursor, rows = fabric.events_since(event_cursor)
            seen_events.extend(rows)
            self.assertEqual(fabric.events_since(event_cursor)[1], ())
            fabric.mark_source_ready(request_id, 10)
        while fabric.next_event_ns() is not None:
            fabric.advance(fabric.next_event_ns())
            event_cursor, rows = fabric.events_since(event_cursor)
            seen_events.extend(rows)
            completion_cursor, rows = fabric.completions_since(completion_cursor)
            seen_completions.extend(rows)
        self.assertEqual(tuple(seen_events), fabric.events())
        self.assertEqual(tuple(seen_completions), fabric.completions())
        seen_events[0]["kind"] = "mutated-copy"
        self.assertNotEqual(seen_events[0], fabric.events()[0])
        with self.assertRaises(ValueError):
            fabric.events_since(event_cursor + 1)


if __name__ == "__main__":
    unittest.main()
