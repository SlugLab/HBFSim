#!/usr/bin/env python3
"""Sustained BF16 GEMM heater with per-window performance telemetry."""

import argparse
import json
import pathlib
import time

import torch


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--duration", type=float, required=True)
    parser.add_argument("--matrix-size", type=int, default=8192)
    parser.add_argument("--output", type=pathlib.Path, required=True)
    args = parser.parse_args()
    torch.manual_seed(0)
    size = args.matrix_size
    left = torch.randn((size, size), device="cuda", dtype=torch.bfloat16)
    right = torch.randn((size, size), device="cuda", dtype=torch.bfloat16)
    for _ in range(3):
        result = torch.mm(left, right)
    torch.cuda.synchronize()
    started = time.time()
    previous = started
    iterations = 0
    previous_iterations = 0
    with args.output.open("w") as trace:
        while time.time() - started < args.duration:
            for _ in range(8):
                result = torch.mm(left, right)
                iterations += 1
            torch.cuda.synchronize()
            now = time.time()
            window_iterations = iterations - previous_iterations
            tflops = (2.0 * size**3 * window_iterations) / (now - previous) / 1e12
            trace.write(json.dumps({
                "timestamp_s": now,
                "elapsed_s": now - started,
                "iterations": iterations,
                "window_tflops": tflops,
                "checksum": float(result[0, 0]),
            }) + "\n")
            trace.flush()
            previous = now
            previous_iterations = iterations
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
