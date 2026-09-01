from __future__ import annotations

import unittest

from placement_policy import BeladyCache, ClockCache, LruCache


class PlacementPolicyTest(unittest.TestCase):
    def test_zero_capacity_never_resides(self) -> None:
        for policy in (ClockCache(0), LruCache(0), BeladyCache(0, [(0, 0)])):
            self.assertFalse(policy.access((0, 0), 0).hit)
            self.assertEqual(policy.resident(), set())

    def test_clock_second_chance(self) -> None:
        cache = ClockCache(2)
        self.assertFalse(cache.access((0, 0), 0).hit)
        self.assertFalse(cache.access((0, 1), 1).hit)
        self.assertTrue(cache.access((0, 0), 2).hit)
        result = cache.access((0, 2), 3)
        self.assertFalse(result.hit)
        self.assertEqual(len(cache.resident()), 2)

    def test_lru_exact_victim(self) -> None:
        cache = LruCache(2)
        cache.access((0, 0), 0)
        cache.access((0, 1), 1)
        cache.access((0, 0), 2)
        result = cache.access((0, 2), 3)
        self.assertEqual(result.evicted, (0, 1))

    def test_belady_uses_farthest_future(self) -> None:
        sequence = [(0, 0), (0, 1), (0, 2), (0, 0), (0, 1), (0, 2)]
        cache = BeladyCache(2, sequence)
        results = [cache.access(key, index) for index, key in enumerate(sequence)]
        self.assertEqual(results[2].evicted, (0, 1))
        self.assertTrue(results[3].hit)


if __name__ == "__main__":
    unittest.main()
