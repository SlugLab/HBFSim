from __future__ import annotations

import json
import pathlib
import subprocess
import sys
import tempfile


def main() -> int:
    binary = pathlib.Path(sys.argv[1]).resolve(strict=True)
    root = pathlib.Path(__file__).resolve().parents[2]
    profile = root / "configs" / "profiles" / "nominal.json"
    with tempfile.TemporaryDirectory() as temporary:
        events = pathlib.Path(temporary) / "events.jsonl"
        events.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "sequence": 1,
                    "logical_address": 0,
                    "bytes": 32_768,
                    "page_bytes": 16_384,
                    "operation": "read",
                }
            )
            + "\n",
            encoding="utf-8",
        )
        command = [
            str(binary),
            "--profile",
            str(profile),
            "--events",
            str(events),
            "--mode",
        ]
        completed = subprocess.run(
            [*command, "fast"],
            check=True,
            capture_output=True,
            text=True,
        )
        hybrid_completed = subprocess.run(
            [
                *command,
                "hybrid",
            ],
            check=True,
            capture_output=True,
            text=True,
        )
    result = json.loads(completed.stdout)
    assert result["status"] == "PASS"
    assert result["engine"] == "hbf-fast"
    assert result["requests"] == {
        "input_expert_misses": 1,
        "submitted": 2,
        "fast": 2,
        "reference": 0,
    }
    assert result["modeled_device_service_ns"] == 20_064
    assert result["demand_exposed_stall_ns"] == 20_000
    assert result["emulator_dispatcher_wall_time_ns"] > 0
    hybrid = json.loads(hybrid_completed.stdout)
    assert hybrid["status"] == "PASS"
    assert hybrid["engine"] == "hbf-hybrid"
    assert hybrid["requests"] == {
        "input_expert_misses": 1,
        "submitted": 2,
        "fast": 0,
        "reference": 2,
    }
    assert hybrid["modeled_device_service_ns"] > 0
    assert hybrid["demand_exposed_stall_ns"] > 0
    assert hybrid["emulator_dispatcher_wall_time_ns"] > 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
