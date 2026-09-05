#!/usr/bin/env python3
"""Run the prefetch accuracy sweep and write the artifact the figure is drawn
from, plus a flat CSV for plotting.

The figure has prefetch accuracy on the x axis and modeled time on the y axis,
one curve per media latency. Two reference points sit on every curve: the
no-prefetch baseline, which is what HBFSim models today, and the next-page
policy, which carries no model of the workload and is plotted at the accuracy
it actually achieved.

Every number is produced by the model in src/prefetch/prefetch_model.cpp. None
of it is measured on a device or a GPU, and the artifact says so in its own
`disclaimer` field. The design behind the sweep is in
docs/46-预取实验设计.md.

Published expert-predictor accuracies are written into the artifact so the
figure can mark them, with their sources. They are other people's numbers, not
ours, and are not produced by this sweep.
"""

from __future__ import annotations

import argparse
import csv
import json
import pathlib
import subprocess


ROOT = pathlib.Path(__file__).resolve().parents[1]

# Access streams to sweep. `pages_per_expert` matters only for the moe stream:
# one Qwen3-30B-A3B expert is 3 * 2048 * 768 parameters in bf16, which is
# 9,437,184 bytes, or 2304 pages of 4 KiB.
STREAMS = (
    {"stream": "sequential", "pages_per_expert": 2},
    {"stream": "random", "pages_per_expert": 2},
    {"stream": "moe", "pages_per_expert": 2304},
)

# Other people's measured predictor accuracies, for marking on the x axis.
# Each entry names where the number comes from; none of them is ours.
PUBLISHED_PREDICTORS = (
    {"name": "ProMoE cross-layer gate on Qwen2-MoE", "accuracy": 0.669,
     "source": "arXiv:2410.22134"},
    {"name": "Fate, no training", "accuracy": 0.7879,
     "source": "arXiv:2502.12224"},
    {"name": "DAOP on Mixtral 8x7B", "accuracy": 0.8411,
     "source": "arXiv:2501.10375"},
    {"name": "ProMoE learned predictor, average", "accuracy": 0.847,
     "source": "arXiv:2410.22134"},
    {"name": "AdapMoE", "accuracy": 0.90, "source": "arXiv:2408.10284"},
    {"name": "HOBBIT next-layer top-1", "accuracy": 0.96,
     "source": "arXiv:2411.01433"},
    {"name": "Fate with over-fetch", "accuracy": 0.9715,
     "source": "arXiv:2502.12224; about 3.75x the weight traffic"},
)


def run_stream(binary: pathlib.Path, stream: str, accesses: int,
               compute_ns: int, lead: int, buffer_pages: int,
               max_in_flight: int, pages_per_expert: int, seed: int) -> dict:
    command = [
        str(binary), "--stream", stream, "--accesses", str(accesses),
        "--compute-ns", str(compute_ns), "--lead", str(lead),
        "--buffer-pages", str(buffer_pages),
        "--max-in-flight", str(max_in_flight),
        "--pages-per-expert", str(pages_per_expert), "--seed", str(seed),
    ]
    output = subprocess.run(command, cwd=ROOT, check=True,
                            capture_output=True, text=True, timeout=1800)
    result = json.loads(output.stdout)
    result["command"] = command
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--build-dir", type=pathlib.Path,
                        default=ROOT / "build")
    parser.add_argument("--accesses", type=int, default=20000)
    parser.add_argument("--compute-ns", type=int, default=4000,
                        help="accelerator time between two accesses; this is "
                             "what a prefetch hides behind")
    parser.add_argument("--lead", type=int, default=8)
    parser.add_argument("--buffer-pages", type=int, default=64)
    parser.add_argument("--max-in-flight", type=int, default=32)
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--output", type=pathlib.Path,
                        default=ROOT / "docs/proofs/artifacts"
                        "/prefetch-accuracy-sweep.json")
    parser.add_argument("--csv", type=pathlib.Path,
                        default=ROOT / "docs/proofs/artifacts"
                        "/prefetch-accuracy-sweep.csv")
    args = parser.parse_args()

    binary = args.build_dir / "hbf_prefetch_bench"
    if not binary.is_file():
        raise RuntimeError(f"hbf_prefetch_bench is not built: {binary}")

    report: dict = {
        "schema_version": 1,
        "disclaimer": "modeled, not measured on any device or GPU",
        "model": "src/prefetch/prefetch_model.cpp",
        "design": "docs/46-预取实验设计.md",
        "published_predictors": list(PUBLISHED_PREDICTORS),
        "runs": [],
    }

    rows: list[dict] = []
    for entry in STREAMS:
        result = run_stream(binary, entry["stream"], args.accesses,
                            args.compute_ns, args.lead, args.buffer_pages,
                            args.max_in_flight, entry["pages_per_expert"],
                            args.seed)
        report["runs"].append(result)
        for cell in result["cells"]:
            row = dict(cell)
            row["stream"] = entry["stream"]
            rows.append(row)
        naive = [c for c in result["cells"] if c["policy"] == "next_page"]
        if naive:
            worst = max(naive, key=lambda c: c["read_latency_ns"])
            print(f"{entry['stream']:>11}: next-page reaches accuracy "
                  f"{worst['achieved_accuracy']:.5f}, speedup "
                  f"{worst['speedup_over_no_prefetch']:.2f} at tR="
                  f"{worst['read_latency_ns']} ns, "
                  f"{worst['demand_misses']} demand misses left")

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    print(f"wrote {args.output}")

    if rows:
        args.csv.parent.mkdir(parents=True, exist_ok=True)
        fields = ["stream", "policy", "requested_accuracy",
                  "achieved_accuracy", "read_latency_ns", "total_ns",
                  "stall_ns", "demand_misses", "prefetch_issued",
                  "prefetch_hits", "prefetch_wasted",
                  "speedup_over_no_prefetch", "recovered_fraction"]
        with args.csv.open("w", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields)
            writer.writeheader()
            for row in rows:
                writer.writerow({field: row.get(field, "")
                                 for field in fields})
        print(f"wrote {args.csv}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
