#!/usr/bin/env python3
"""Sweep media read latency against the number of concurrently outstanding
requests, and report how much of the media latency survives as stall.

This produces the data behind the figure item 2 of
docs/重要实现问题以及需补做实验/15-experiments-we-must-add-before-submission.md
asks for: the residual stall fraction against media latency, one curve per
outstanding-request count. The reasoning the figure answers is recorded in
docs/45-预取与延迟掩盖的核实.md.

WHAT THE SECOND AXIS IS, AND WHAT IT IS NOT. The sweep varies the profile
field `queue_depth`, which bounds how many requests the device is servicing at
once. Until the admission bound was added to
src/mqsim_adapter/mqsim_online.cpp the field reached MQSim as
Device_Parameter_Set::IO_Queue_Depth and was read by nothing on the HBF host
interface, so this axis did not exist. `queue_depth` is NOT the device's
parallel read-out unit count: item 1 of the same document sweeps that separate
quantity, whose identity is

    steady-state read bandwidth = parallel read-out units x page size / tR

and a run of this script may not be reported as a measurement of it.

RESIDUAL STALL FRACTION is defined here and is not taken from any paper. For
one cell, with `requests` requests of `bytes` each:

    exposed  = requests * read_latency_ns          (every access serialised)
    floor    = requests * bytes / aggregate_bandwidth_bytes_per_s
                                                   (bandwidth alone, latency
                                                    fully overlapped)
    residual = (modeled_ns - floor) / (exposed - floor)

residual is 1.0 when nothing is overlapped and 0.0 when the run is limited by
bandwidth alone. Values are clamped to [0, 1] for reporting and the raw
modeled_ns is kept alongside so the clamp can be audited.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import subprocess
import tempfile


ROOT = pathlib.Path(__file__).resolve().parents[1]

# The six media latencies item 1 fixes, in nanoseconds: 1, 2, 4, 5, 10, 20 us.
READ_LATENCY_NS = (1_000, 2_000, 4_000, 5_000, 10_000, 20_000)

# 1 is what the device-side fast path models today (one global cursor,
# src/host_service/control_layout.hpp fast_channel_tail_ns). The rest are the
# outstanding-request counts item 1 names.
QUEUE_DEPTHS = (1, 256, 1024, 1536, 4883)


def build_profile(base: dict, read_latency_ns: int, queue_depth: int,
                  directory: pathlib.Path) -> pathlib.Path:
    profile = dict(base)
    profile["name"] = f"{base['name']}-tr{read_latency_ns}-qd{queue_depth}"
    profile["read_latency_ns"] = read_latency_ns
    profile["queue_depth"] = queue_depth
    # The measured-curve path refuses any page size other than 4096 and is not
    # what this sweep drives; drop the metadata so the parameterised model runs.
    profile.pop("empirical_vmem", None)
    path = directory / f"{profile['name']}.json"
    path.write_text(json.dumps(profile, indent=2) + "\n")
    return path


def run_case(binary: pathlib.Path, profile: pathlib.Path, capacity_bytes: int,
             requests: int, bytes_per_request: int, pattern: str,
             seed: int) -> dict:
    command = [
        str(binary), "--profile", str(profile),
        "--capacity-bytes", str(capacity_bytes),
        "--operation", "read", "--requests", str(requests),
        "--bytes", str(bytes_per_request), "--arrival-gap-ns", "0",
        "--pattern", pattern, "--seed", str(seed),
    ]
    output = subprocess.run(command, cwd=ROOT, check=True,
                            capture_output=True, text=True, timeout=1800)
    result = json.loads(output.stdout)
    result["command"] = command
    return result


def residual_stall_fraction(result: dict, requests: int,
                            bytes_per_request: int, read_latency_ns: int,
                            aggregate_bandwidth: int) -> dict:
    exposed = requests * read_latency_ns
    floor = requests * bytes_per_request * 1e9 / aggregate_bandwidth
    modeled = result["timing_ns"]["modeled"]
    span = exposed - floor
    if span <= 0:
        # Bandwidth alone already costs more than fully serialised latency, so
        # the cell says nothing about latency hiding. Report it, do not plot it.
        return {"residual_stall_fraction": None, "degenerate": True,
                "exposed_ns": exposed, "bandwidth_floor_ns": floor,
                "modeled_ns": modeled}
    raw = (modeled - floor) / span
    return {"residual_stall_fraction": min(1.0, max(0.0, raw)),
            "residual_stall_fraction_raw": raw, "degenerate": False,
            "exposed_ns": exposed, "bandwidth_floor_ns": floor,
            "modeled_ns": modeled}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--build-dir", type=pathlib.Path,
                        default=ROOT / "build")
    parser.add_argument("--base-profile", type=pathlib.Path,
                        default=ROOT / "configs/profiles/nominal.json")
    parser.add_argument("--requests", type=int, default=4096)
    parser.add_argument("--bytes", type=int, default=16384)
    parser.add_argument("--capacity-bytes", type=int, default=1099511627776)
    parser.add_argument("--pattern", default="sequential",
                        choices=("sequential", "random"))
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--output", type=pathlib.Path,
                        default=ROOT / "docs/proofs/artifacts"
                        "/prefetch-window-sweep.json")
    args = parser.parse_args()

    binary = args.build_dir / "hbf_mqsim_bench"
    if not binary.is_file():
        raise RuntimeError(f"hbf_mqsim_bench is not built: {binary}")

    base = json.loads(args.base_profile.read_text())
    aggregate_bandwidth = base["aggregate_bandwidth_bytes_per_s"]

    # A depth at or above the request count can never fill, so every such depth
    # produces the same curve. Say so rather than letting the duplicate curves
    # read as a result.
    saturated = [depth for depth in QUEUE_DEPTHS if depth >= args.requests]
    if saturated:
        print(f"note: --requests {args.requests} is at or below queue depths "
              f"{saturated}; those depths cannot fill and will produce "
              f"identical curves. Raise --requests above "
              f"{max(QUEUE_DEPTHS)} to separate every depth.")

    report: dict = {
        "schema_version": 1,
        "base_profile": str(args.base_profile.relative_to(ROOT)),
        "workload": {
            "requests": args.requests, "bytes_per_request": args.bytes,
            "operation": "read", "arrival_gap_ns": 0,
            "pattern": args.pattern, "seed": args.seed,
        },
        "read_latency_ns": list(READ_LATENCY_NS),
        "queue_depths": list(QUEUE_DEPTHS),
        "cells": [],
    }

    with tempfile.TemporaryDirectory() as raw_directory:
        directory = pathlib.Path(raw_directory)
        for queue_depth in QUEUE_DEPTHS:
            for read_latency_ns in READ_LATENCY_NS:
                profile = build_profile(base, read_latency_ns, queue_depth,
                                        directory)
                result = run_case(binary, profile, args.capacity_bytes,
                                  args.requests, args.bytes, args.pattern,
                                  args.seed)
                cell = {
                    "queue_depth": queue_depth,
                    "read_latency_ns": read_latency_ns,
                    "latency_ns": result["latency_ns"],
                    "modeled_bandwidth_bytes_per_s":
                        result["modeled_bandwidth_bytes_per_s"],
                }
                cell.update(residual_stall_fraction(
                    result, args.requests, args.bytes, read_latency_ns,
                    aggregate_bandwidth))
                report["cells"].append(cell)
                fraction = cell["residual_stall_fraction"]
                shown = "degenerate" if fraction is None else f"{fraction:.4f}"
                print(f"qd={queue_depth:<5} tR={read_latency_ns:>6} ns  "
                      f"residual_stall={shown}  "
                      f"modeled={cell['modeled_ns']} ns")

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    print(f"wrote {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
