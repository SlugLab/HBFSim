#!/usr/bin/env python3

import json
import pathlib
import subprocess
import sys
import tempfile


ROOT = pathlib.Path(__file__).resolve().parents[2]


def main() -> int:
    replay = pathlib.Path(sys.argv[1])
    with tempfile.TemporaryDirectory(prefix="hbfsim-app0-trace-") as temporary:
        directory = pathlib.Path(temporary)
        trace = directory / "access.csv"
        output = directory / "result.json"
        trace.write_text(
            "sequence,byte_offset,media_logical_address,media_bytes,operation,gpu_begin_ns,gpu_end_ns\n"
            "0,0,0,16384,0,100,110\n"
            "1,8,0,16384,1,120,132\n"
            "2,16384,16384,16384,0,140,151\n"
            "3,16392,16384,16384,0,160,173\n"
        )
        completed = subprocess.run(
            [
                str(replay),
                "--profile",
                "configs/profiles/nominal.json",
                "--trace",
                str(trace),
                "--output",
                str(output),
            ],
            cwd=ROOT,
            text=True,
            capture_output=True,
        )
        assert completed.returncode == 0, (completed.stdout, completed.stderr)
        result = json.loads(output.read_text())
        assert result["status"] == "PASS"
        assert result["trace"]["access_records"] == 4
        assert result["trace"]["read_accesses"] == 3
        assert result["trace"]["program_accesses"] == 1
        assert result["replay"]["requests_completed"] == 4
        assert result["replay"]["modeled_end_ns"] > 0
        assert result["semantics"]["per_access_observation"] is True
        assert result["semantics"]["per_access_live_injection"] is False
        assert result["semantics"]["observation_injects_delay"] is False
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
