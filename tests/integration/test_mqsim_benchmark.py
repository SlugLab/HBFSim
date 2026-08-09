#!/usr/bin/env python3

import json
import pathlib
import subprocess
import sys


ROOT = pathlib.Path(__file__).resolve().parents[2]
BINARY = (
    pathlib.Path(sys.argv[1])
    if len(sys.argv) > 1
    else ROOT / "build" / "hbf_mqsim_bench"
)


def main() -> int:
    completed = subprocess.run(
        [
            str(BINARY),
            "--profile",
            "configs/profiles/nominal.json",
            "--requests",
            "4096",
            "--bytes",
            "16384",
            "--operation",
            "read",
            "--arrival-gap-ns",
            "0",
        ],
        cwd=ROOT,
        check=True,
        text=True,
        capture_output=True,
    )
    result = json.loads(completed.stdout)

    assert result["schema_version"] == 1
    assert result["engine"] == "mqsim-hbf-media-only"
    assert result["profile"] == "nominal"
    assert result["effective_profile"]["blocks_per_plane"] >= 16
    assert result["workload"] == {
        "operation": "read",
        "requests": 4096,
        "bytes_per_request": 16384,
        "arrival_gap_ns": 0,
    }
    assert result["requests"]["submitted"] == 4096
    assert result["requests"]["completed"] == 4096
    assert result["timing_ns"]["modeled"] > 0
    assert result["timing_ns"]["wall"] > 0
    assert result["latency_ns"]["average"] > 0
    assert result["latency_ns"]["p50"] > 0
    assert result["latency_ns"]["p99"] >= result["latency_ns"]["p50"]
    assert result["modeled_bandwidth_bytes_per_s"] > 0
    assert result["simulator_requests_per_s"] > 0

    for profile, operation in (
        ("conservative", "write"),
        ("aggressive", "mixed"),
    ):
        completed = subprocess.run(
            [
                str(BINARY),
                "--profile",
                f"configs/profiles/{profile}.json",
                "--requests",
                "64",
                "--bytes",
                "16384",
                "--operation",
                operation,
                "--arrival-gap-ns",
                "1000",
            ],
            cwd=ROOT,
            check=True,
            text=True,
            capture_output=True,
        )
        result = json.loads(completed.stdout)
        assert result["profile"] == profile
        assert result["effective_profile"]["blocks_per_plane"] >= 16
        assert result["workload"]["operation"] == operation
        assert result["requests"]["submitted"] == 64
        assert result["requests"]["completed"] == 64

    overflow = subprocess.run(
        [
            str(BINARY),
            "--profile",
            "configs/profiles/nominal.json",
            "--requests",
            "3",
            "--arrival-gap-ns",
            str(2**64 - 1),
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
    )
    assert overflow.returncode == 64
    assert "benchmark arrival timeline overflows" in overflow.stderr
    return 0


if __name__ == "__main__":
    sys.exit(main())
