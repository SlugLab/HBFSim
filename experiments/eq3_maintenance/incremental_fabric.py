"""Additive incremental observation adapter for the experimental fabric path.

Scheduling, clocks, resource ownership, and the default BasicFabric API remain
owned by BasicFabric.  These methods only deepcopy an already-produced suffix.
"""
from __future__ import annotations

import copy

from eq3_basic_fabric import BasicFabric


def _offset(value, size, label):
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= size:
        raise ValueError(f"{label} must be an integer in [0,{size}]")
    return value


class IncrementalBasicFabric(BasicFabric):
    """BasicFabric with cursor-based, read-only observation access."""

    def events_since(self, offset):
        start = _offset(offset, len(self._events), "event offset")
        return len(self._events), tuple(copy.deepcopy(self._events[start:]))

    def completions_since(self, offset):
        start = _offset(offset, len(self._completions), "completion offset")
        return len(self._completions), tuple(copy.deepcopy(self._completions[start:]))

