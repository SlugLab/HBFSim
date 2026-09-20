#!/usr/bin/env python3
"""Run one system point with the default-off shared-HBM endpoint guard adapter."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys

import endpoint_policy
import run_system_point


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _output_argument() -> Path | None:
    try:
        return Path(sys.argv[sys.argv.index("--output") + 1])
    except (ValueError, IndexError):
        return None


def _record_adapter(output: Path | None) -> None:
    if output is None or not (output / "manifest.json").is_file():
        return
    path = output / "manifest.json"
    value = json.loads(path.read_text())
    sources = (Path(__file__).resolve(), Path(endpoint_policy.__file__).resolve())
    value["entrypoint_adapter"] = {
        "schema_version": "eq3-shared-hbm-endpoint-policy-adapter-v1",
        "capability": ("HBF_LEGACY_READ_RATE_POLICY_PLUS_HBM_SHARED_ENDPOINT_"
                       "THERMAL_GUARD_WITH_NORMAL_BASELINE_RESTORE"),
        "source_sha256": {str(source): _digest(source) for source in sources},
        "default_connected": False,
    }
    run_system_point.save(path, value)


def main() -> None:
    # The isolated process is the opt-in boundary.  The base runner and policy
    # module remain unchanged for frozen historical points.
    run_system_point.ReadRatePolicy = endpoint_policy.EndpointAwarePolicy
    output = _output_argument()
    try:
        run_system_point.main()
    finally:
        _record_adapter(output)


if __name__ == "__main__":
    main()
