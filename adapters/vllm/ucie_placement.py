"""Strict host-only placement for the frozen 147-storage UCIe sweep.

The canonical layout is independent of CUDA addresses and selection size.
The run manifest is deliberately tied to one ordered registration attempt.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Sequence

SCHEMA = "hbfsim.ucie.canonical_layout.v1"
MANIFEST_SCHEMA = "hbfsim.ucie.host_placement.v1"
PAGE_BYTES = 16384
STACK_COUNT = 4
STACK_CAPACITY = 512 * 1024**3
MODULES_PER_STACK = 16
MODULE_CAPACITY = STACK_CAPACITY // MODULES_PER_STACK
U64_MAX = (1 << 64) - 1
FROZEN_LAYOUT_SHA256 = "f17794781aa8b649576dfdee10d6cd87138a727dc73b1b00e07ed65c2879810d"
FROZEN_LEDGER_SHA256 = "666fb5bb13bb4206f30bd74986708ec0dc6a34d01ebd22d0e470c76dc10b4fda"
_LAYER = re.compile(r"^model\.layers\.(\d+)\.")
_GLOBALS = {
    "model.embed_tokens.weight": (1, 4),
    "model.norm.weight": (2, 4),
    "lm_head.weight": (3, 4),
}


def _integer(value: Any, name: str, *, positive: bool = False) -> int:
    if type(value) is not int or value < (1 if positive else 0) or value > U64_MAX:
        raise ValueError(f"invalid {name}")
    return value


def _extent(length: int, page_bytes: int) -> int:
    _integer(length, "length", positive=True)
    _integer(page_bytes, "page_bytes", positive=True)
    if length > U64_MAX - (page_bytes - 1):
        raise ValueError("media extent overflow")
    return ((length + page_bytes - 1) // page_bytes) * page_bytes


def _alias_key(aliases: Any) -> tuple[str, ...]:
    if not isinstance(aliases, (list, tuple)) or not aliases:
        raise ValueError("missing storage aliases")
    if any(not isinstance(alias, str) or not alias for alias in aliases):
        raise ValueError("invalid storage alias")
    if len(set(aliases)) != len(aliases):
        raise ValueError("duplicate alias within storage")
    return tuple(sorted(aliases))


def _module_for(aliases: tuple[str, ...]) -> tuple[int, int]:
    global_hits = [alias for alias in aliases if alias in _GLOBALS]
    layer_hits = {_LAYER.match(alias).group(1) for alias in aliases
                  if _LAYER.match(alias)}
    if len(global_hits) == 1 and len(aliases) == 1:
        return _GLOBALS[global_hits[0]]
    if global_hits or len(layer_hits) != 1 or any(
            not _LAYER.match(alias) for alias in aliases):
        raise ValueError("mixed or unsupported storage alias group")
    layer = int(next(iter(layer_hits)))
    if layer < 0 or layer > 15:
        raise ValueError("layer outside frozen 16-layer model")
    return layer % STACK_COUNT, (layer // STACK_COUNT) % MODULES_PER_STACK


def build_canonical_layout(ledger_path: str | Path) -> dict[str, Any]:
    source = Path(ledger_path).read_bytes()
    if hashlib.sha256(source).hexdigest() != FROZEN_LEDGER_SHA256:
        raise ValueError("unrecognized full-model ledger bytes")
    ledger = json.loads(source)
    if ledger.get("schema") != "hbfsim.full_coverage_target_ledger.v1" or \
            ledger.get("total_storage_count") != 147 or \
            ledger.get("total_storage_bytes") != 13838323712:
        raise ValueError("unexpected full-model ledger identity")
    entries = ledger.get("entries")
    if not isinstance(entries, list) or len(entries) != 147:
        raise ValueError("incomplete full-model ledger")
    observed: set[str] = set()
    rows = []
    for entry in entries:
        aliases = _alias_key(entry.get("aliases"))
        if any(a in observed for a in aliases):
            raise ValueError("alias belongs to multiple storages")
        observed.update(aliases)
        length = _integer(entry.get("storage_bytes"), "storage_bytes", positive=True)
        stack, module = _module_for(aliases)
        rows.append({"aliases": list(aliases), "storage_bytes": length,
                     "stack_id": stack, "module_id": module})
    if sum(row["storage_bytes"] for row in rows) != ledger["total_storage_bytes"]:
        raise ValueError("full-model byte total mismatch")
    next_offset: dict[tuple[int, int], int] = {}
    for row in sorted(rows, key=lambda r: tuple(r["aliases"])):
        slot = (row["stack_id"], row["module_id"])
        offset = next_offset.get(slot, 0)
        extent = _extent(row["storage_bytes"], PAGE_BYTES)
        if extent > MODULE_CAPACITY - offset:
            raise ValueError("storage exceeds 32GiB module capacity")
        row["media_extent_bytes"] = extent
        row["canonical_physical_address"] = (
            slot[0] * STACK_CAPACITY + slot[1] * MODULE_CAPACITY + offset)
        next_offset[slot] = offset + extent
    return {"schema": SCHEMA, "page_bytes": PAGE_BYTES,
            "stack_count": STACK_COUNT, "stack_capacity_bytes": STACK_CAPACITY,
            "modules_per_stack": MODULES_PER_STACK,
            "source_ledger_sha256": hashlib.sha256(source).hexdigest(),
            "total_storage_count": 147, "total_storage_bytes": 13838323712,
            "placements": sorted(rows, key=lambda r: tuple(r["aliases"]))}


def generate_manifest(canonical_layout_path: str | Path,
                      registered: Sequence[tuple[Any, int]], page_bytes: int,
                      output_path: str | Path) -> dict[str, Any]:
    """Write one strict manifest before the first NativeTimingSession creation.

    Caller must use this exact ordered `registered` list for subsequent API
    registration. Any pre-existing context/range registration is unsupported.
    """
    page_bytes = _integer(page_bytes, "page_bytes", positive=True)
    raw = Path(canonical_layout_path).read_bytes()
    if hashlib.sha256(raw).hexdigest() != FROZEN_LAYOUT_SHA256:
        raise ValueError("unrecognized frozen canonical layout bytes")
    layout = json.loads(raw)
    if layout.get("schema") != SCHEMA or layout.get("page_bytes") != page_bytes or \
            layout.get("stack_count") != STACK_COUNT or \
            layout.get("stack_capacity_bytes") != STACK_CAPACITY or \
            layout.get("modules_per_stack") != MODULES_PER_STACK or \
            layout.get("total_storage_count") != 147 or \
            layout.get("total_storage_bytes") != 13838323712 or \
            not isinstance(layout.get("placements"), list) or \
            len(layout["placements"]) != 147:
        raise ValueError("invalid canonical layout identity")
    by_alias: dict[tuple[str, ...], dict[str, Any]] = {}
    seen_alias: set[str] = set()
    physical = []
    for row in layout["placements"]:
        aliases = _alias_key(row.get("aliases"))
        if any(a in seen_alias for a in aliases):
            raise ValueError("duplicate canonical alias")
        seen_alias.update(aliases)
        length = _integer(row.get("storage_bytes"), "storage_bytes", positive=True)
        stack = _integer(row.get("stack_id"), "stack_id")
        module = _integer(row.get("module_id"), "module_id")
        address = _integer(row.get("canonical_physical_address"), "physical address")
        extent = _extent(length, page_bytes)
        if stack >= STACK_COUNT or module >= MODULES_PER_STACK or \
                (stack, module) != _module_for(aliases) or \
                row.get("media_extent_bytes") != extent or \
                address % page_bytes or \
                address < stack * STACK_CAPACITY + module * MODULE_CAPACITY or \
                address + extent > stack * STACK_CAPACITY + (module + 1) * MODULE_CAPACITY:
            raise ValueError("invalid canonical module placement")
        by_alias[aliases] = row
        physical.append((address, address + extent))
    physical.sort()
    if any(a[1] > b[0] for a, b in zip(physical, physical[1:])):
        raise ValueError("overlapping canonical objects")
    if sum(r["storage_bytes"] for r in by_alias.values()) != 13838323712:
        raise ValueError("canonical byte total mismatch")
    if not registered or len(registered) > 147:
        raise ValueError("empty or oversized registered selection")
    rows = []
    bindings = []
    offset = 0
    seen_storage: set[tuple[str, ...]] = set()
    seen_address: set[int] = set()
    previous_address = -1
    for index, pair in enumerate(registered, 1):
        storage, amount = pair
        aliases = _alias_key(storage.aliases)
        address = _integer(storage.address, "live storage address", positive=True)
        size = _integer(storage.size, "live storage bytes", positive=True)
        amount = _integer(amount, "registered bytes", positive=True)
        if aliases not in by_alias or aliases in seen_storage or \
                any(a not in seen_alias for a in aliases) or \
                size != by_alias[aliases]["storage_bytes"] or amount != size or \
                address <= previous_address or address in seen_address or \
                address > U64_MAX - size:
            raise ValueError("live selection differs from canonical storage/order")
        seen_storage.add(aliases)
        seen_address.add(address)
        previous_address = address
        item = by_alias[aliases]
        extent = _extent(amount, page_bytes)
        if offset > U64_MAX - extent:
            raise ValueError("file offset overflow")
        rows.append({"range_id": index, "file_offset": offset,
                     "length": amount, "page_bytes": page_bytes,
                     "registered_address": address,
                     "canonical_physical_address": item["canonical_physical_address"],
                     "endpoint_id": 7, "generation": 1})
        bindings.append({"aliases": list(aliases), "live_address": address,
                         "length": amount, "predicted_range_id": index,
                         "predicted_file_offset": offset,
                         "canonical_physical_address": item["canonical_physical_address"]})
        offset += extent
    manifest = {"schema": MANIFEST_SCHEMA, "placements": rows}
    output = Path(output_path)
    if output.exists():
        raise FileExistsError(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(manifest, sort_keys=True, indent=2) + "\n",
                      encoding="utf-8")
    return {"manifest": manifest, "bindings": bindings,
            "canonical_layout_sha256": hashlib.sha256(raw).hexdigest(),
            "manifest_sha256": hashlib.sha256(output.read_bytes()).hexdigest()}
