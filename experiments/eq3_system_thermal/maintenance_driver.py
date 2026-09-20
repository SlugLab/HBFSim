"""Bounded-spare maintenance state machine over TopologyService extra jobs.

The driver owns metadata versions and physical-block allocation only.  It does
not emulate NAND timing: read/program/erase work completes solely from service
receipts.  Callers advance the ReliabilityLedger through the receipt end time
before consuming that receipt so commit-time age reset remains exact.
"""

from __future__ import annotations

from collections import defaultdict, deque
from copy import deepcopy
import math
from typing import Any

try:
    from .reliability import ReliabilityLedger
except ImportError:  # Direct fixed-test execution from this directory.
    from reliability import ReliabilityLedger


class MaintenanceDriver:
    def __init__(self, reliability: ReliabilityLedger, config: dict[str, Any]):
        self.reliability = reliability
        self.block_bytes = self._positive(config.get("block_bytes"), "block_bytes")
        self.pages_per_block = self._positive(config.get("pages_per_block"), "pages_per_block")
        self.max_cohort = self._positive(config.get("max_blocks_per_cohort"),
                                         "max_blocks_per_cohort")
        raw_spares = config.get("spare_block_ids_by_stack_channel")
        if not isinstance(raw_spares, dict) or not raw_spares:
            raise ValueError("explicit spare_block_ids_by_stack_channel is required")
        self.free_spares: dict[tuple[str, str], deque[str]] = {}
        all_spares = set()
        for stack, channels in raw_spares.items():
            if not isinstance(stack, str) or not stack or not isinstance(channels, dict) or not channels:
                raise ValueError("each stack needs explicit per-channel spare pools")
            for channel, values in channels.items():
                channel = str(channel)
                if not channel or not isinstance(values, list) or not values:
                    raise ValueError("each configured channel needs nonempty spare block IDs")
                if any(not isinstance(value, str) or not value for value in values):
                    raise ValueError("spare block IDs must be nonempty strings")
                if len(set(values)) != len(values) or all_spares.intersection(values):
                    raise ValueError("spare block IDs must be globally unique")
                all_spares.update(values)
                self.free_spares[(stack, channel)] = deque(values)
        self.program_j_per_byte = self._optional_nonnegative(
            config.get("program_energy_j_per_byte"), "program_energy_j_per_byte")
        self.erase_j_per_operation = self._optional_nonnegative(
            config.get("erase_energy_j_per_operation"), "erase_energy_j_per_operation")
        self.energy_evidence = config.get("energy_evidence")
        if not isinstance(self.energy_evidence, dict):
            raise ValueError("energy_evidence must explicitly classify program and erase")
        if set(self.energy_evidence) != {"program", "erase"}:
            raise ValueError("energy_evidence must exactly cover program and erase")
        self.extents: dict[str, dict[str, Any]] = {}
        self.physical_wear: dict[str, dict[str, int]] = defaultdict(
            lambda: {"block_program_work_started": 0, "block_program_work_completed": 0,
                     "nand_page_programs_started": 0, "nand_page_programs_completed": 0,
                     "erase_phase_started": 0, "erase_completed": 0})
        self.operations: dict[str, dict[str, Any]] = {}
        self.outstanding: dict[str, tuple[str, str]] = {}
        self.active_extents: set[str] = set()
        self.quarantined_blocks: set[str] = set()
        self.events: list[dict[str, Any]] = []
        self.energy_facts: list[dict[str, Any]] = []
        self.results: list[dict[str, Any]] = []
        self._terminal_status_counts: dict[str, int] = defaultdict(int)
        self._terminal_extent_count = 0
        self._sequence = 0

    @staticmethod
    def _positive(value: Any, label: str) -> int:
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise ValueError(f"{label} must be a positive integer")
        return value

    @staticmethod
    def _optional_nonnegative(value: Any, label: str) -> float | None:
        if value is None:
            return None
        if not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
            raise ValueError(f"{label} must be finite non-negative or null")
        return float(value)

    def register_extents(self, rows: list[dict[str, Any]]) -> None:
        """Register exact refresh extents and their current physical mappings."""
        spare_ids = {item for values in self.free_spares.values() for item in values}
        mapped_sources = {row["physical_block_id"] for row in self.extents.values()}
        for raw in rows:
            required = {"extent_id", "stack", "channel", "source_block_id", "version"}
            if not isinstance(raw, dict) or set(raw) != required:
                raise ValueError("extent row has missing or unknown fields")
            extent, stack, channel, source = (raw["extent_id"], raw["stack"],
                                               str(raw["channel"]), raw["source_block_id"])
            version = raw["version"]
            if any(not isinstance(value, str) or not value
                   for value in (extent, stack, channel, source)):
                raise ValueError("extent identities must be nonempty strings")
            if (stack, channel) not in self.free_spares:
                raise ValueError("extent stack/channel lacks a same-channel spare pool")
            if source in spare_ids or source in mapped_sources or extent in self.extents:
                raise ValueError("source overlaps spare pool or extent is duplicated")
            if isinstance(version, bool) or not isinstance(version, int) or version < 0:
                raise ValueError("version must be a non-negative integer")
            self.extents[extent] = {"extent_id": extent, "stack": stack,
                                    "channel": channel, "physical_block_id": source,
                                    "version": version, "maintenance_attempt_count": 0}
            mapped_sources.add(source)
            # Establish the age identity without changing its configured initial age.
            self.reliability.due_reasons(extent, self.reliability.epoch_ns)

    def record_foreground_write(self, extent_id: str, at_ns: int) -> int:
        """Model an explicit mapping-generation conflict; read-only runs never call this."""
        row = self.extents[extent_id]
        if not isinstance(at_ns, int) or at_ns < 0:
            raise ValueError("foreground write time must be non-negative integer ns")
        if row["version"] == (1 << 64) - 1:
            raise OverflowError("mapping generation overflow")
        row["version"] += 1
        self.events.append({"kind": "foreground_version_write", "extent_id": extent_id,
                            "at_ns": at_ns, "new_version": row["version"]})
        return row["version"]

    def _begin_due(self, now_ns: int) -> None:
        groups: dict[tuple[str, str], list[str]] = defaultdict(list)
        for extent_id, row in sorted(self.extents.items()):
            if extent_id in self.active_extents:
                continue
            if self.reliability.accounted_through_ns(extent_id) != now_ns:
                raise ValueError("reliability age must be integrated exactly through poll time")
            reasons = self.reliability.due_reasons(extent_id, now_ns)
            if reasons:
                groups[(row["stack"], row["channel"])].append(extent_id)
        for (stack, channel), extent_ids in sorted(groups.items()):
            pool = self.free_spares[(stack, channel)]
            extent_ids.sort(key=lambda item: (
                self.extents[item]["maintenance_attempt_count"], item))
            while extent_ids and pool:
                count = min(len(extent_ids), len(pool), self.max_cohort)
                selected = extent_ids[:count]
                del extent_ids[:count]
                items = []
                for extent_id in selected:
                    row = self.extents[extent_id]
                    items.append({"extent_id": extent_id,
                                  "source_block_id": row["physical_block_id"],
                                  "destination_block_id": pool.popleft(),
                                  "expected_version": row["version"]})
                    row["maintenance_attempt_count"] += 1
                    self.active_extents.add(extent_id)
                operation_id = f"refresh:{self._sequence}"
                self._sequence += 1
                self.operations[operation_id] = {
                    "operation_id": operation_id, "stack": stack, "channel": channel,
                    "items": items, "phase": "refresh_read", "submitted": False,
                    "erase_queue": deque(), "committed_extents": [],
                    "conflicted_extents": [], "failures": [], "start_ns": now_ns,
                }
                self.events.append({"kind": "maintenance_started", "operation_id": operation_id,
                                    "at_ns": now_ns, "extent_ids": list(selected),
                                    "due_reasons": {item: self.reliability.due_reasons(item, now_ns)
                                                    for item in selected}})

    def poll(self, now_ns: int, *, discover_due: bool = True) -> list[dict[str, Any]]:
        """Offer each new phase once; jobs are suitable for TopologyService.extra_jobs."""
        if not isinstance(now_ns, int) or now_ns < 0:
            raise ValueError("now_ns must be non-negative integer ns")
        if discover_due:
            self._begin_due(now_ns)
        jobs = []
        for operation_id, operation in sorted(self.operations.items()):
            if operation["phase"] == "terminal" or operation["submitted"]:
                continue
            phase = operation["phase"]
            if phase in {"refresh_read", "program"}:
                blocks = ([item["source_block_id"] for item in operation["items"]]
                          if phase == "refresh_read" else
                          [item["destination_block_id"] for item in operation["items"]])
                extents = [item["extent_id"] for item in operation["items"]]
                expected = [item["expected_version"] for item in operation["items"]]
            else:
                erase = operation["current_erase"]
                blocks, extents = erase["block_ids"], erase["extent_ids"]
                expected = []
            job_id = f"{operation_id}:{phase}:{len(self.outstanding)}"
            job = {
                "job_id": job_id, "maintenance_id": job_id,
                "stack": operation["stack"], "channel": operation["channel"],
                "operation": "erase" if phase.startswith("erase_") else phase,
                "bytes": self.block_bytes * len(blocks), "arrival_ns": now_ns,
                "metadata": {"parent_maintenance_id": operation_id, "phase": phase,
                             "extent_ids": list(extents),
                             "physical_block_ids": list(blocks),
                             "expected_versions": list(expected),
                             "block_count": len(blocks)},
            }
            operation["submitted"] = True
            self.outstanding[job_id] = (operation_id, phase)
            jobs.append(job)
        return jobs

    def _cost_fact(self, operation_id: str, phase: str, completion_ns: int,
                   block_count: int, failed: bool) -> None:
        if phase == "program":
            quantity, unit, coefficient = self.block_bytes * block_count, "bytes", self.program_j_per_byte
        elif phase.startswith("erase_"):
            quantity, unit, coefficient = block_count, "operations", self.erase_j_per_operation
        else:
            return
        self.energy_facts.append({
            "operation_id": operation_id, "phase": phase, "completion_ns": completion_ns,
            "quantity": quantity, "unit": unit,
            "energy_j": None if coefficient is None else quantity * coefficient,
            "coefficient": coefficient, "evidence": self.energy_evidence[
                "program" if phase == "program" else "erase"],
            "failed_after_service": failed,
            "accounting_scope": "DRIVER_OPERATION_COST_FACT_CONSUME_ONCE",
        })

    def consume_receipt(self, receipt: dict[str, Any], *, failed_job_ids=()) -> dict[str, Any]:
        """Advance phases from unique service completions.

        ``failed_job_ids`` is a fixed fault-injection/test input.  Service work
        and energy before that terminal failure remain counted.
        """
        failed = set(failed_job_ids)
        progress = {row["job_id"]: row for row in receipt.get("job_progress", [])}
        for job_id in receipt.get("completion_ids", []):
            if job_id not in self.outstanding:
                continue
            if job_id not in progress or progress[job_id]["remaining_bytes"] != 0:
                raise ValueError("maintenance completion lacks byte-complete progress")
            operation_id, phase = self.outstanding.pop(job_id)
            operation = self.operations[operation_id]
            completion_ns = progress[job_id]["completion_ns"]
            if not isinstance(completion_ns, int):
                raise ValueError("maintenance completion timestamp unavailable")
            block_count = progress[job_id]["metadata"]["block_count"]
            did_fail = job_id in failed
            self._cost_fact(operation_id, phase, completion_ns, block_count, did_fail)
            operation["submitted"] = False
            if phase == "refresh_read":
                if did_fail:
                    operation["failures"].append("refresh_read")
                    for item in operation["items"]:
                        self.free_spares[(operation["stack"], operation["channel"])].append(
                            item["destination_block_id"])
                    self._terminal(operation, completion_ns, "FAILED_READ")
                else:
                    operation["phase"] = "program"
            elif phase == "program":
                for item in operation["items"]:
                    wear = self.physical_wear[item["destination_block_id"]]
                    wear["block_program_work_started"] += 1
                    wear["nand_page_programs_started"] += self.pages_per_block
                    if not did_fail:
                        wear["block_program_work_completed"] += 1
                        wear["nand_page_programs_completed"] += self.pages_per_block
                if did_fail:
                    operation["failures"].append("program")
                    operation["erase_queue"].append({
                        "kind": "cleanup", "block_ids": [x["destination_block_id"]
                                                           for x in operation["items"]],
                        "extent_ids": [x["extent_id"] for x in operation["items"]]})
                else:
                    old_blocks, old_extents, cleanup_blocks, cleanup_extents = [], [], [], []
                    for item in operation["items"]:
                        extent = self.extents[item["extent_id"]]
                        if (extent["version"] != item["expected_version"]
                                or extent["version"] == (1 << 64) - 1):
                            operation["conflicted_extents"].append(item["extent_id"])
                            if extent["version"] == (1 << 64) - 1:
                                operation["failures"].append("version_overflow")
                            cleanup_blocks.append(item["destination_block_id"])
                            cleanup_extents.append(item["extent_id"])
                            continue
                        old_blocks.append(item["source_block_id"])
                        old_extents.append(item["extent_id"])
                        extent["physical_block_id"] = item["destination_block_id"]
                        extent["version"] += 1
                        operation["committed_extents"].append(item["extent_id"])
                        self.reliability.record_refresh_terminal(
                            item["extent_id"], completion_ns, True, operation_id)
                    if old_blocks:
                        operation["erase_queue"].append({"kind": "old", "block_ids": old_blocks,
                                                         "extent_ids": old_extents})
                    if cleanup_blocks:
                        operation["erase_queue"].append({"kind": "cleanup",
                                                         "block_ids": cleanup_blocks,
                                                         "extent_ids": cleanup_extents})
                self._next_erase_or_terminal(operation, completion_ns)
            else:
                erase = operation["current_erase"]
                for block in erase["block_ids"]:
                    wear = self.physical_wear[block]
                    wear["erase_phase_started"] += 1
                    if not did_fail:
                        wear["erase_completed"] += 1
                        self.free_spares[(operation["stack"], operation["channel"])].append(block)
                    else:
                        self.quarantined_blocks.add(block)
                if did_fail:
                    operation["failures"].append(phase)
                operation.pop("current_erase")
                self._next_erase_or_terminal(operation, completion_ns)
        unknown_failed = failed - set(receipt.get("completion_ids", []))
        if unknown_failed:
            raise ValueError("failure injection names a non-completed job")
        return self.drain_delta(receipt.get("end_ns"))

    def _next_erase_or_terminal(self, operation: dict[str, Any], at_ns: int) -> None:
        if operation["erase_queue"]:
            erase = operation["erase_queue"].popleft()
            operation["current_erase"] = erase
            operation["phase"] = "erase_old" if erase["kind"] == "old" else "erase_cleanup"
            operation["submitted"] = False
            return
        if operation["committed_extents"]:
            status = "COMMITTED" if not operation["failures"] and not operation["conflicted_extents"] \
                else "COMMITTED_WITH_POST_PROGRAM_EXCEPTION"
        else:
            status = "FAILED_OR_VERSION_CONFLICT"
        self._terminal(operation, at_ns, status)

    def _terminal(self, operation: dict[str, Any], at_ns: int, status: str) -> None:
        operation["phase"] = "terminal"
        operation["submitted"] = False
        for item in operation["items"]:
            self.active_extents.discard(item["extent_id"])
        result = {"operation_id": operation["operation_id"], "status": status,
                  "start_ns": operation["start_ns"], "end_ns": at_ns,
                  "extent_ids": [x["extent_id"] for x in operation["items"]],
                  "committed_extents": list(operation["committed_extents"]),
                  "conflicted_extents": list(operation["conflicted_extents"]),
                  "failures": list(operation["failures"]),
                  "age_reset_extent_ids": list(operation["committed_extents"])}
        self.results.append(result)
        self.events.append({"kind": "maintenance_terminal", **deepcopy(result)})
        self._terminal_status_counts[status] += 1
        self._terminal_extent_count += len(result["extent_ids"])
        del self.operations[operation["operation_id"]]

    def drain_delta(self, end_ns: int | None = None) -> dict[str, Any]:
        """Return compact new facts since the prior drain, without cumulative maps."""
        events, energy, results = self.events, self.energy_facts, self.results
        self.events, self.energy_facts, self.results = [], [], []
        return {
            "schema_version": "eq3-maintenance-driver-window-delta-v1",
            "end_ns": end_ns, "events": events, "energy_facts": energy,
            "terminal_results": results,
            "summary": {"active_operation_count": sum(
                            op["phase"] != "terminal" for op in self.operations.values()),
                        "outstanding_job_count": len(self.outstanding),
                        "free_spare_count": sum(len(pool) for pool in self.free_spares.values()),
                        "quarantined_block_count": len(self.quarantined_blocks),
                        "committed_extent_count": sum(len(row["committed_extents"])
                                                      for row in results),
                        "failed_terminal_count": sum(row["status"] != "COMMITTED"
                                                     for row in results)},
        }

    def snapshot(self) -> dict[str, Any]:
        return {
            "schema_version": "eq3-maintenance-driver-v1",
            "service_semantics": "EXTERNAL_TOPOLOGY_SERVICE_COMPLETION_DRIVEN",
            "version_semantics": "MONOTONIC_LOGICAL_EXTENT_GENERATION_CAS",
            "age_reset_semantics": "SUCCESSFUL_COMMIT_EXACT_EXTENTS_ONLY",
            "extents": deepcopy(self.extents),
            "free_spares_by_stack_channel": {
                stack: {channel: list(self.free_spares[(stack, channel)])
                        for owner, channel in sorted(self.free_spares) if owner == stack}
                for stack in sorted({owner for owner, _ in self.free_spares})},
            "quarantined_blocks": sorted(self.quarantined_blocks),
            "physical_wear": deepcopy(dict(self.physical_wear)),
            "outstanding_job_ids": sorted(self.outstanding),
            "retained_terminal_results": deepcopy(self.results),
            "retained_events": deepcopy(self.events),
            "retained_energy_facts": deepcopy(self.energy_facts),
            "terminal_summary": {
                "operation_count": sum(self._terminal_status_counts.values()),
                "extent_count": self._terminal_extent_count,
                "status_counts": dict(sorted(self._terminal_status_counts.items())),
            },
            "limitations": ["METADATA_VERSION_VALIDITY_NO_PAYLOAD_INTEGRITY",
                            "NO_RBER_OR_WEAR_DAMAGE_CURVE",
                            "RATE_SERVICE_NOT_NATIVE_NAND_MAINTENANCE"],
        }


__all__ = ["MaintenanceDriver"]
