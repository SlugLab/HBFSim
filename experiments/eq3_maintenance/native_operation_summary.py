#!/usr/bin/env python3
"""Read-only summaries of actual native MQSim command observations.

``commands.csv`` is flattened: one native command-phase event appears once per
child transaction.  This module therefore reports command counts after
deduplicating by ``(command_id, phase)`` and child-transaction counts after
deduplicating by ``transaction_id``.  It never calls a PROGRAM observation a
P/E cycle: an actual ERASE observation is required and lifetime block-cycle,
spare-capacity, ECC, and payload-integrity facts are not exposed by this CSV.
"""

from __future__ import annotations

import argparse
import csv
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Iterable, Mapping


# Verified against the isolated patched MQSim source:
# NVM_PHY_ONFI_NVDDR2.h::HBF_Command_Observation_Phase and
# NVM_Transaction.h::{Transaction_Type,Transaction_Source_Type}.
PHASE = {
    0: "COMMAND_ISSUED",
    1: "MEDIA_BEGIN",
    2: "MEDIA_END",
    3: "DATA_OUT_BEGIN",
    4: "DATA_OUT_END",
}
OPERATION = {0: "READ", 1: "PROGRAM", 2: "ERASE", 3: "UNKNOWN"}
SOURCE = {
    0: "USERIO",
    1: "CACHE",
    2: "GC_WL",
    3: "MAPPING",
    4: "HBF_MAINTENANCE",
}

REQUIRED_FIELDS = {
    "command_id", "phase", "time_ns", "transaction_id", "type", "source",
    "channel", "chip", "die", "plane",
}


def _integer(row: Mapping[str, str], field: str) -> int:
    value = row.get(field)
    if value in (None, ""):
        raise ValueError(f"commands row lacks {field}")
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"commands row has non-integer {field}") from exc
    if parsed < 0:
        raise ValueError(f"commands row has negative {field}")
    return parsed


def _enum(row: Mapping[str, str], field: str, values: Mapping[int, str]) -> tuple[int, str]:
    code = _integer(row, field)
    if code not in values:
        raise ValueError(f"commands row has unsupported {field}={code}")
    return code, values[code]


def _empty_operation() -> dict:
    return {
        "media_command_starts": 0,
        "media_command_ends": 0,
        "paired_media_commands": 0,
        "unpaired_media_starts": 0,
        "unpaired_media_ends": 0,
        "child_transactions": 0,
        "media_start_first_ns": None,
        "media_start_last_ns": None,
        "media_end_first_ns": None,
        "media_end_last_ns": None,
        "paired_media_duration_ns": {
            "count": 0, "minimum": None, "maximum": None, "sum": 0,
        },
        "source_counts": {},
    }


def summarize_rows(rows: Iterable[Mapping[str, str]], *, source_name: str = "commands.csv") -> dict:
    """Summarize flattened command rows without inferring unobserved facts."""

    # COMMAND_ISSUED/MEDIA_BEGIN/MEDIA_END are command-level callbacks whose
    # rows are flattened over children. DATA_OUT callbacks are emitted once per
    # child transaction and may legitimately have distinct timestamps.
    phase_events: dict[tuple[int, int, int | None], dict] = {}
    transactions: dict[int, dict] = {}
    row_count = 0

    for row in rows:
        row_count += 1
        missing = REQUIRED_FIELDS.difference(row)
        if missing:
            raise ValueError(f"commands row lacks fields: {sorted(missing)}")
        command_id = _integer(row, "command_id")
        phase_code, phase_name = _enum(row, "phase", PHASE)
        time_ns = _integer(row, "time_ns")
        transaction_id = _integer(row, "transaction_id")
        operation_code, operation = _enum(row, "type", OPERATION)
        source_code, source = _enum(row, "source", SOURCE)
        placement = tuple(_integer(row, key) for key in ("channel", "chip", "die", "plane"))

        transaction = {
            "operation_code": operation_code,
            "operation": operation,
            "source_code": source_code,
            "source": source,
            "placement": placement,
        }
        previous = transactions.setdefault(transaction_id, transaction)
        if previous != transaction:
            raise ValueError(f"transaction {transaction_id} changed identity")

        key = (command_id, phase_code, transaction_id if phase_code in (3, 4) else None)
        event = phase_events.setdefault(key, {
            "command_id": command_id,
            "phase_code": phase_code,
            "phase": phase_name,
            "time_ns": time_ns,
            "transaction_ids": set(),
            "operations": set(),
            "sources": set(),
        })
        if event["time_ns"] != time_ns:
            raise ValueError(f"command {command_id} phase {phase_name} has multiple timestamps")
        event["transaction_ids"].add(transaction_id)
        event["operations"].add(operation)
        event["sources"].add(source)

    operations = {name: _empty_operation() for name in OPERATION.values()}
    transaction_sources: Counter[str] = Counter()
    placement_transactions: defaultdict[tuple[int, int, int, int], set[int]] = defaultdict(set)
    for transaction_id, transaction in transactions.items():
        operation = transaction["operation"]
        source = transaction["source"]
        operations[operation]["child_transactions"] += 1
        transaction_sources[source] += 1
        placement_transactions[transaction["placement"]].add(transaction_id)

    command_sources: Counter[str] = Counter()
    operation_source_starts: defaultdict[str, Counter[str]] = defaultdict(Counter)
    operation_source_ends: defaultdict[str, Counter[str]] = defaultdict(Counter)
    starts: dict[int, dict] = {}
    ends: dict[int, dict] = {}

    for (_, phase_code, _), event in phase_events.items():
        if phase_code not in (1, 2):
            continue
        if len(event["operations"]) != 1:
            raise ValueError(
                f"command {event['command_id']} {event['phase']} contains mixed operations")
        operation = next(iter(event["operations"]))
        source = next(iter(event["sources"])) if len(event["sources"]) == 1 else "MIXED"
        target = starts if phase_code == 1 else ends
        if event["command_id"] in target:
            raise ValueError(f"duplicate deduplicated {event['phase']} command")
        target[event["command_id"]] = event
        field = "media_command_starts" if phase_code == 1 else "media_command_ends"
        operations[operation][field] += 1
        times = [x["time_ns"] for x in target.values()
                 if next(iter(x["operations"])) == operation]
        prefix = "media_start" if phase_code == 1 else "media_end"
        operations[operation][f"{prefix}_first_ns"] = min(times)
        operations[operation][f"{prefix}_last_ns"] = max(times)
        if phase_code == 1:
            command_sources[source] += 1
            operation_source_starts[operation][source] += 1
        else:
            operation_source_ends[operation][source] += 1

    for operation, summary in operations.items():
        start_ids = {cid for cid, event in starts.items()
                     if next(iter(event["operations"])) == operation}
        end_ids = {cid for cid, event in ends.items()
                   if next(iter(event["operations"])) == operation}
        paired = start_ids & end_ids
        durations = []
        for command_id in paired:
            start, end = starts[command_id], ends[command_id]
            if start["operations"] != end["operations"]:
                raise ValueError(f"command {command_id} changed operation across media phases")
            if end["time_ns"] < start["time_ns"]:
                raise ValueError(f"command {command_id} media end precedes media begin")
            durations.append(end["time_ns"] - start["time_ns"])
        summary["paired_media_commands"] = len(paired)
        summary["unpaired_media_starts"] = len(start_ids - end_ids)
        summary["unpaired_media_ends"] = len(end_ids - start_ids)
        if durations:
            summary["paired_media_duration_ns"] = {
                "count": len(durations), "minimum": min(durations),
                "maximum": max(durations), "sum": sum(durations),
            }
        sources = set(operation_source_starts[operation]) | set(operation_source_ends[operation])
        summary["source_counts"] = {
            name: {
                "media_command_starts": operation_source_starts[operation][name],
                "media_command_ends": operation_source_ends[operation][name],
            }
            for name in sorted(sources)
        }

    tuple_rows = [{
        "channel": key[0], "chip": key[1], "die": key[2], "plane": key[3],
        "child_transactions": len(transaction_ids),
    } for key, transaction_ids in sorted(placement_transactions.items())]
    triple_transactions: defaultdict[tuple[int, int, int], set[int]] = defaultdict(set)
    for (channel, _chip, die, plane), transaction_ids in placement_transactions.items():
        triple_transactions[(channel, die, plane)].update(transaction_ids)
    triple_rows = [{
        "channel": key[0], "die": key[1], "plane": key[2],
        "child_transactions": len(transaction_ids),
    } for key, transaction_ids in sorted(triple_transactions.items())]

    return {
        "schema_version": "eq3-native-operation-summary-v1",
        "source": source_name,
        "evidence": "ACTUAL_MQSIM_COMMAND_OBSERVATIONS",
        "enum_contract": {
            "phase": {str(key): value for key, value in PHASE.items()},
            "operation": {str(key): value for key, value in OPERATION.items()},
            "source": {str(key): value for key, value in SOURCE.items()},
            "verified_source": {
                "phase": "experiments/eq3_maintenance/backend/patches/0004-eq3-maintenance.patch:NVM_PHY_ONFI_NVDDR2.h::HBF_Command_Observation_Phase",
                "operation_and_source": "experiments/eq3_maintenance/backend/patches/0004-eq3-maintenance.patch:NVM_Transaction.h::{Transaction_Type,Transaction_Source_Type}",
            },
        },
        "deduplication": {
            "input_rows": row_count,
            "native_phase_events": len(phase_events),
            "child_transactions": len(transactions),
            "media_commands_are_unique_by": ["command_id", "phase"],
            "child_transactions_are_unique_by": ["transaction_id"],
        },
        "operations": operations,
        "source_child_transaction_counts": dict(sorted(transaction_sources.items())),
        "source_media_command_start_counts": dict(sorted(command_sources.items())),
        "actual_channel_die_plane_tuple_count": len(triple_rows),
        "actual_channel_die_plane_tuples": triple_rows,
        "actual_channel_chip_die_plane_tuple_count": len(tuple_rows),
        "actual_channel_chip_die_plane_tuples": tuple_rows,
        "completion_semantics": {
            "media_end": "NAND_MEDIA_PHASE_ENDED_NOT_COMMITTED_SUCCESS",
            "maintenance_commit": "REQUIRES_SEPARATE_MAINTENANCE_COMPLETION_FACT",
            "failure_injection": "MEDIA_ACTIVITY_BEFORE_A_TERMINAL_FAILURE_REMAINS_OBSERVED",
        },
        "unavailable": {
            "program_erase_lifetime_cycles": "UNAVAILABLE_NOT_OBSERVED",
            "maximum_physical_spare_capacity_bytes": "UNAVAILABLE_NOT_OBSERVED",
            "ecc_corrected_bits": "UNAVAILABLE_NOT_OBSERVED",
            "ecc_uncorrectable_events": "UNAVAILABLE_NOT_OBSERVED",
            "payload_or_byte_integrity": "UNAVAILABLE_NOT_OBSERVED_METADATA_VALIDITY_ONLY",
        },
    }


def summarize_commands_csv(path: str | Path) -> dict:
    source = Path(path)
    with source.open(newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None:
            raise ValueError("commands CSV has no header")
        missing = REQUIRED_FIELDS.difference(reader.fieldnames)
        if missing:
            raise ValueError(f"commands CSV lacks fields: {sorted(missing)}")
        return summarize_rows(reader, source_name=str(source))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("commands_csv", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = summarize_commands_csv(args.commands_csv)
    text = json.dumps(result, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text)
    else:
        print(text, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
