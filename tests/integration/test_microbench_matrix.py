#!/usr/bin/env python3

import json
import pathlib
import subprocess
import sys
import tempfile


def test_microbench_matrix() -> None:
    root = pathlib.Path(__file__).resolve().parents[2]
    runner = root / "scripts" / "run_microbench.py"
    build = pathlib.Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else root / "build"
    with tempfile.TemporaryDirectory(prefix="hbfsim-microbench-") as directory:
        summary = pathlib.Path(directory) / "summary.json"
        subprocess.run(
            [sys.executable, str(runner), "--build-dir", str(build),
             "--profile", str(root / "configs/profiles/nominal.json"),
             "--quick", "--output", str(summary)],
            cwd=root, check=True, timeout=120)
        report = json.loads(summary.read_text())
        assert report["schema_version"] == 1
        assert report["cases"]
        for case in report["cases"]:
            assert case["checksum"] == case["baseline_checksum"]
            if case["mode"] != "baseline":
                assert case["coverage"]["unsafe_launches"] == 0
                assert case["requests"]["completed"] == case["requests"]["submitted"]
