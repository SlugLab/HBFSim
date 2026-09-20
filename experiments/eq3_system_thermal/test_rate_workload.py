import unittest
from rate_workload import RateWorkload


class RateWorkloadTests(unittest.TestCase):
    channels = {f"hbf{s}": {str(c): f"hbf{s}.die{c}" for c in range(16)} for s in range(4)}

    def total(self, pattern="continuous", **extra):
        source = RateWorkload(self.channels, dict(active_ns=1_000_000_000,
                              per_stack_Bps=1_920_000_000_000, pattern=pattern, **extra))
        by_stack = {s: 0 for s in self.channels}
        for start in range(0, 1_200_000_000, 20_000_000):
            rows = source.advance(start, start + 20_000_000)
            if start >= 1_000_000_000:
                self.assertEqual(sum(map(sum, (r.values() for r in rows.values()))), 0)
            for stack, values in rows.items():
                by_stack[stack] += sum(values.values())
        return source.total, by_stack

    def test_burst_preserves_mean_and_over_capacity_demand(self):
        self.assertEqual(self.total()[0], 4 * 1_920_000_000_000)
        self.assertEqual(self.total()[0], self.total("burst_equal_mean")[0])

    def test_concentration_preserves_package_demand(self):
        total, rows = self.total(hot_stack="hbf0")
        self.assertEqual(total, self.total()[0])
        self.assertEqual(rows["hbf0"], total // 2)
        self.assertEqual(total, self.total(channel_distribution="first_quarter")[0])


if __name__ == "__main__":
    unittest.main()
