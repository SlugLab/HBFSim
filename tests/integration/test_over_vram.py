#!/usr/bin/env python3

import json
import pathlib
import subprocess
import sys
import tempfile


def test_over_vram_capacity() -> None:
    root = pathlib.Path(__file__).resolve().parents[2]
    runner = root / "scripts" / "run_microbench.py"
    build = pathlib.Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else root / "build"
    with tempfile.TemporaryDirectory(prefix="hbfsim-over-vram-") as directory:
        summary = pathlib.Path(directory) / "summary.json"
        subprocess.run(
            [sys.executable, str(runner), "--build-dir", str(build),
             "--profile", str(root / "configs/profiles/nominal.json"),
             "--over-vram", "--logical-bytes", "110G",
             "--cache-bytes", "2G", "--output", str(summary)],
            cwd=root, check=True, timeout=300)
        report = json.loads(summary.read_text())
        case = report["cases"][0]
        assert case["mode"] == "capacity"
        assert case["logical_bytes"] == 110 * 1024**3
        assert case["cache_bytes"] == 2 * 1024**3
        assert case["access_span_bytes"] >= case["logical_bytes"] - 8
        assert case["checksum"] == case["baseline_checksum"]
        assert case["coverage"]["unsafe_launches"] == 0
        assert case["requests"]["completed"] == case["requests"]["submitted"]
