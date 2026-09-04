"""Deterministic expert-cache policies used by real-trace replay.

All policies operate on equal-sized, complete expert objects (w13 + w2).
The caller is responsible for converting a byte budget into an integer number
of expert slots.  No policy performs I/O or invents an access sequence.
"""

from __future__ import annotations

import collections
import heapq
import math
from dataclasses import dataclass
from typing import Iterable

ExpertKey = tuple[int, int]


@dataclass(frozen=True)
class AccessResult:
    hit: bool
    evicted: ExpertKey | None = None


class ClockCache:
    """Second-chance CLOCK with deterministic insertion and scan order."""

    def __init__(self, capacity: int) -> None:
        if capacity < 0:
            raise ValueError("capacity must be non-negative")
        self.capacity = capacity
        self._slots: list[ExpertKey | None] = [None] * capacity
        self._referenced: list[bool] = [False] * capacity
        self._index: dict[ExpertKey, int] = {}
        self._hand = 0

    def access(self, key: ExpertKey, _: int) -> AccessResult:
        found = self._index.get(key)
        if found is not None:
            self._referenced[found] = True
            return AccessResult(hit=True)
        if self.capacity == 0:
            return AccessResult(hit=False)

        for index, value in enumerate(self._slots):
            if value is None:
                self._slots[index] = key
                self._referenced[index] = True
                self._index[key] = index
                return AccessResult(hit=False)

        while self._referenced[self._hand]:
            self._referenced[self._hand] = False
            self._hand = (self._hand + 1) % self.capacity
        victim = self._slots[self._hand]
        assert victim is not None
        del self._index[victim]
        self._slots[self._hand] = key
        self._referenced[self._hand] = True
        self._index[key] = self._hand
        self._hand = (self._hand + 1) % self.capacity
        return AccessResult(hit=False, evicted=victim)

    def resident(self) -> set[ExpertKey]:
        return set(self._index)


class LruCache:
    """Exact least-recently-used cache."""

    def __init__(self, capacity: int) -> None:
        if capacity < 0:
            raise ValueError("capacity must be non-negative")
        self.capacity = capacity
        self._resident: collections.OrderedDict[ExpertKey, None] = (
            collections.OrderedDict()
        )

    def access(self, key: ExpertKey, _: int) -> AccessResult:
        if key in self._resident:
            self._resident.move_to_end(key)
            return AccessResult(hit=True)
        if self.capacity == 0:
            return AccessResult(hit=False)
        victim: ExpertKey | None = None
        if len(self._resident) == self.capacity:
            victim, _ = self._resident.popitem(last=False)
        self._resident[key] = None
        return AccessResult(hit=False, evicted=victim)

    def resident(self) -> set[ExpertKey]:
        return set(self._resident)


class BeladyCache:
    """Offline optimal cache using exact future positions from the trace."""

    def __init__(self, capacity: int, accesses: Iterable[ExpertKey]) -> None:
        if capacity < 0:
            raise ValueError("capacity must be non-negative")
        self.capacity = capacity
        self._future: dict[ExpertKey, collections.deque[int]] = {}
        for position, key in enumerate(accesses):
            self._future.setdefault(key, collections.deque()).append(position)
        self._resident: set[ExpertKey] = set()
        self._versions: dict[ExpertKey, int] = {}
        self._next: dict[ExpertKey, float] = {}
        self._farthest: list[tuple[float, int, ExpertKey]] = []

    def _advance(self, key: ExpertKey, position: int) -> float:
        queue = self._future[key]
        if not queue or queue[0] != position:
            raise ValueError("Belady access order differs from frozen trace")
        queue.popleft()
        return float(queue[0]) if queue else math.inf

    def _record_next(self, key: ExpertKey, next_position: float) -> None:
        version = self._versions.get(key, 0) + 1
        self._versions[key] = version
        self._next[key] = next_position
        heapq.heappush(self._farthest, (-next_position, version, key))

    def _victim(self) -> ExpertKey:
        while self._farthest:
            negative_next, version, key = heapq.heappop(self._farthest)
            if (
                key in self._resident
                and self._versions.get(key) == version
                and -negative_next == self._next.get(key)
            ):
                return key
        raise RuntimeError("Belady resident set has no valid victim")

    def access(self, key: ExpertKey, position: int) -> AccessResult:
        next_position = self._advance(key, position)
        if key in self._resident:
            self._record_next(key, next_position)
            return AccessResult(hit=True)
        if self.capacity == 0:
            return AccessResult(hit=False)
        victim: ExpertKey | None = None
        if len(self._resident) == self.capacity:
            victim = self._victim()
            self._resident.remove(victim)
            self._next.pop(victim, None)
        self._resident.add(key)
        self._record_next(key, next_position)
        return AccessResult(hit=False, evicted=victim)

    def resident(self) -> set[ExpertKey]:
        return set(self._resident)


def make_policy(
    name: str, capacity: int, accesses: list[ExpertKey]
) -> ClockCache | LruCache | BeladyCache:
    normalized = name.lower()
    if normalized == "clock":
        return ClockCache(capacity)
    if normalized == "lru":
        return LruCache(capacity)
    if normalized == "belady":
        return BeladyCache(capacity, accesses)
    raise ValueError(f"unsupported cache policy: {name}")
