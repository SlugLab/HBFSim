"""Check modeled accounting, including harmful prefetch under saturation."""

import json
import math
import subprocess
import sys


def run(binary, concurrency):
    return json.loads(subprocess.check_output([
        binary, "--accesses", "128", "--max-in-flight", str(concurrency),
        "--compute-ns", "1", "--seed", "7",
    ], text=True))


def main():
    for concurrency in (1, 4, 32):
        report = run(sys.argv[1], concurrency)
        assert report == run(sys.argv[1], concurrency), "seeded output drift"
        assert report["disclaimer"] == "modeled, not measured on any device or GPU"
        cells = report["cells"]
        baselines = {c["read_latency_ns"]: c for c in cells if c["policy"] == "none"}
        harmful = 0
        for cell in cells:
            baseline = baselines[cell["read_latency_ns"]]["total_ns"]
            span = baseline - report["compute_floor_ns"]
            expected = (baseline - cell["total_ns"]) / span
            assert math.isclose(cell["recovered_fraction"], expected, abs_tol=1e-6), cell
            assert cell["prefetch_hits"] + cell["demand_misses"] == 128
            assert cell["prefetch_hits"] + cell["prefetch_wasted"] == cell["prefetch_issued"]
            assert cell["total_ns"] == report["compute_floor_ns"] + cell["stall_ns"]
            if cell["total_ns"] > baseline:
                harmful += 1
                assert cell["recovered_fraction"] < 0
        if concurrency == 1:
            assert harmful > 0, "fixture must exercise harmful speculative traffic"


if __name__ == "__main__":
    main()
