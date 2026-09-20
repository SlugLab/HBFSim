"""Exact-completion age bridge for maintenance on CausalTopologyService.

Temperatures are known only at thermal-window boundaries.  The previously
observed per-die temperature is therefore applied piecewise-constantly through
the current window.  Maintenance completions advance only their affected
extents before CAS commit; all other extents catch up at the window boundary.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Mapping


class CausalMaintenanceAgeAdapter:
    def __init__(self, ledger, driver, previous_temperature_k_by_stack_channel: Mapping):
        self.ledger = ledger
        self.driver = driver
        self._temperature = self._normalize_temperatures(
            previous_temperature_k_by_stack_channel)
        self._validate_coverage()

    @staticmethod
    def _normalize_temperatures(raw: Mapping) -> dict[tuple[str, str], float]:
        result = {}
        if not isinstance(raw, Mapping):
            raise ValueError("temperature map must be a mapping")
        for stack, channels in raw.items():
            if not isinstance(stack, str) or not isinstance(channels, Mapping):
                raise ValueError("temperature map must be stack -> channel -> kelvin")
            for channel, value in channels.items():
                temperature = float(value)
                if not 0 < temperature < float("inf"):
                    raise ValueError("temperature must be finite and positive")
                result[(stack, str(channel))] = temperature
        return result

    def _validate_coverage(self) -> None:
        required = {(row["stack"], row["channel"])
                    for row in self.driver.extents.values()}
        if not required.issubset(self._temperature):
            raise ValueError("temperature map does not cover every maintenance extent")

    def _advance_extents(self, extent_ids, end_ns: int) -> None:
        groups = defaultdict(list)
        for extent_id in sorted(set(extent_ids)):
            row = self.driver.extents[extent_id]
            start_ns = self.ledger.accounted_through_ns(extent_id)
            if end_ns < start_ns:
                raise ValueError("completion precedes extent age frontier")
            groups[(start_ns, self._temperature[(row["stack"], row["channel"])] )].append(
                extent_id)
        for (start_ns, temperature), identities in sorted(groups.items()):
            self.ledger.advance_temperature_many(
                identities, start_ns, end_ns, temperature)

    def start_window(self, start_ns: int) -> list[dict]:
        """Discover due work only after every extent is aligned to the boundary."""
        if any(self.ledger.accounted_through_ns(extent_id) != start_ns
               for extent_id in self.driver.extents):
            raise ValueError("all extents must be age-aligned at window start")
        return self.driver.poll(start_ns, discover_due=True)

    def consume_receipt(self, receipt: dict, *, failed_job_ids=()) -> dict:
        """Apply exact maintenance completions and return delta plus next phases."""
        progress = {row["job_id"]: row for row in receipt.get("job_progress", [])}
        affected_by_time = defaultdict(list)
        for job_id in receipt.get("completion_ids", []):
            if job_id not in self.driver.outstanding:
                continue
            row = progress.get(job_id)
            if row is None or not isinstance(row.get("completion_ns"), int):
                raise ValueError("maintenance completion lacks exact progress timestamp")
            affected_by_time[row["completion_ns"]].extend(
                row.get("metadata", {}).get("extent_ids", ()))
        for completion_ns, extent_ids in sorted(affected_by_time.items()):
            self._advance_extents(extent_ids, completion_ns)
        delta = self.driver.consume_receipt(receipt, failed_job_ids=failed_job_ids)
        delta["reliability_events"] = self.ledger.drain_events()
        delta["next_phase_jobs"] = self.driver.poll(
            receipt["end_ns"], discover_due=False)
        return delta

    def finish_window(self, end_ns: int,
                      observed_temperature_k_by_stack_channel: Mapping) -> dict:
        """Catch every extent up with prior temperature, then install new facts."""
        self._advance_extents(self.driver.extents, end_ns)
        events = self.ledger.drain_events()
        next_temperature = self._normalize_temperatures(
            observed_temperature_k_by_stack_channel)
        prior = self._temperature
        self._temperature = next_temperature
        try:
            self._validate_coverage()
        except Exception:
            self._temperature = prior
            raise
        return {
            "end_ns": end_ns,
            "reliability_events": events,
            "temperature_semantics": (
                "PREVIOUS_KNOWN_WINDOW_TEMPERATURE_PIECEWISE_CONSTANT_NO_RETROACTIVE_REAGE"
            ),
        }


__all__ = ["CausalMaintenanceAgeAdapter"]
