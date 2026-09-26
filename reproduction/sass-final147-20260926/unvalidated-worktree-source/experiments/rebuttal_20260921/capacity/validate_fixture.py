#!/usr/bin/env python3
"""Static validation for the prepared R3 capacity fixture and its outputs."""

from __future__ import annotations

import argparse
import json
import pathlib


LOGICAL_BYTES = 110 * 1024**3
CACHE_BYTES = 2 * 1024**3
PAGE_BYTES = [4096, 8192, 16384, 32768, 65536]


def validate_result(path: pathlib.Path) -> None:
    item = json.loads(path.read_text())
    page = item["page_bytes"]
    assert item["schema_version"] == 1
    assert item["experiment"] == "R3_CAPACITY_PAYLOAD_FIXTURE"
    assert item["evidence_class"] == \
        "CAPACITY_RUNTIME_PAYLOAD_FIXTURE_NOT_MQSIM_TIMING"
    assert item["logical_bytes"] == LOGICAL_BYTES
    assert item["cache_bytes"] == CACHE_BYTES
    assert page in PAGE_BYTES
    assert item["frame_count"] == CACHE_BYTES // page
    assert item["common_offset_alignment"] == 65536
    assert item["sequential_access_only"] is True
    assert item["concurrent_eviction_tested"] is False
    assert item["sample_count"] >= 64
    assert item["passed_count"] == item["sample_count"]
    samples = item["samples"]
    positions = {sample["position"] for sample in samples}
    assert {"first", "middle"} <= positions
    assert "last_common" in positions or "last_common_and_page" in positions
    assert "last_page" in positions or "last_common_and_page" in positions
    common = [sample for sample in samples
              if sample["position"] != "last_page"]
    assert len(common) >= 64
    assert all(sample["logical_offset"] % 65536 == 0 for sample in common)
    assert any(sample["logical_offset"] == LOGICAL_BYTES - page
               for sample in samples)
    assert all(sample["expected_hash"] == sample["actual_hash"] for sample in samples)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("results", nargs="*", type=pathlib.Path)
    args = parser.parse_args()
    expected_frames = {page: CACHE_BYTES // page for page in PAGE_BYTES}
    assert expected_frames == {
        4096: 524288,
        8192: 262144,
        16384: 131072,
        32768: 65536,
        65536: 32768,
    }
    for result in args.results:
        validate_result(result)
    print(json.dumps({"static_status": "PASS", "frame_counts": expected_frames},
                     sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
