#!/usr/bin/env python3
"""Run the die-density, interleaving-scheme, and NAND-type sweeps through
hbf_mqsim_bench and collect P50/P99 latency into one report.

Raised in discussion with 胡学长, 2026-08-16. See
todo/die-density-and-nand-mix-proposal.md for what each sweep does and does
not establish.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import subprocess


ROOT = pathlib.Path(__file__).resolve().parents[1]
CAPACITY_BYTES = 1099511627776


def run_case(binary: pathlib.Path, profile: pathlib.Path, pattern: str,
             requests: int, bytes_per_request: int, seed: int) -> dict:
    command = [
        str(binary), "--profile", str(profile),
        "--capacity-bytes", str(CAPACITY_BYTES),
        "--operation", "read", "--requests", str(requests),
        "--bytes", str(bytes_per_request), "--arrival-gap-ns", "0",
        "--pattern", pattern, "--seed", str(seed),
    ]
    output = subprocess.run(command, cwd=ROOT, check=True,
                            capture_output=True, text=True, timeout=300)
    result = json.loads(output.stdout)
    result["command"] = command
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--build-dir", type=pathlib.Path,
                        default=ROOT / "build")
    parser.add_argument("--requests", type=int, default=4096)
    parser.add_argument("--bytes", type=int, default=16384)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--output", type=pathlib.Path,
                        default=ROOT / "docs/proofs/artifacts"
                        "/2026-08-16-density-interleave-nand-sweep.json")
    args = parser.parse_args()

    binary = args.build_dir / "hbf_mqsim_bench"
    if not binary.is_file():
        raise RuntimeError(f"hbf_mqsim_bench is not built: {binary}")

    density_dir = ROOT / "configs/profiles/density-sweep"
    interleave_dir = ROOT / "configs/profiles/interleave-sweep"
    nand_dir = ROOT / "configs/profiles/nand-sweep"

    report: dict[str, list[dict]] = {"density_sweep": [], "interleave_sweep": [],
                                     "nand_sweep": []}

    for profile in sorted(density_dir.glob("hbf-density-*.json")):
        for pattern in ("sequential", "random"):
            result = run_case(binary, profile, pattern, args.requests,
                              args.bytes, args.seed)
            report["density_sweep"].append(result)
            print(f"density {profile.stem} {pattern}: "
                 f"p50={result['latency_ns']['p50']}ns "
                 f"p99={result['latency_ns']['p99']}ns")

    for profile in sorted(interleave_dir.glob("hbf-interleave-*.json")):
        for pattern in ("sequential", "random"):
            result = run_case(binary, profile, pattern, args.requests,
                              args.bytes, args.seed)
            report["interleave_sweep"].append(result)
            print(f"interleave {profile.stem} {pattern}: "
                 f"p50={result['latency_ns']['p50']}ns "
                 f"p99={result['latency_ns']['p99']}ns")

    for profile in sorted(nand_dir.glob("hbf-nand-*.json")):
        for pattern in ("sequential", "random"):
            result = run_case(binary, profile, pattern, args.requests,
                              args.bytes, args.seed)
            report["nand_sweep"].append(result)
            print(f"nand {profile.stem} {pattern}: "
                 f"p50={result['latency_ns']['p50']}ns "
                 f"p99={result['latency_ns']['p99']}ns")

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    print(f"wrote {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
