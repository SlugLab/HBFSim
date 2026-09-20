"""Read-only three-grid comparison on one common sensor/time window."""
import argparse
import csv
import json
import tempfile
from pathlib import Path

from eq3_campaign_compare import compare


def prefix(source, target, end_s):
    rows = 0
    times = set()
    with source.open() as incoming, target.open("x", newline="") as outgoing:
        reader = csv.DictReader(incoming)
        if not reader.fieldnames:
            raise ValueError(f"empty sensor CSV: {source}")
        writer = csv.DictWriter(outgoing, fieldnames=reader.fieldnames)
        writer.writeheader()
        for row in reader:
            time_s = float(row["time_s"])
            if time_s <= end_s + 1e-9:
                writer.writerow(row)
                rows += 1
                times.add(time_s)
    if not rows:
        raise ValueError(f"no observations at or before {end_s}s: {source}")
    if max(times) > end_s + 1e-9:
        raise ValueError("prefix retained an out-of-window timestamp")
    return {"rows": rows, "frames": len(times), "first_time_s": min(times),
            "last_time_s": max(times)}


def common_window(coarse, middle, fine, end_s=4.0, initial_k=300.0,
                  probes_k=(301.0, 330.0)):
    sources = (coarse, middle, fine)
    with tempfile.TemporaryDirectory() as root:
        reduced = []
        coverage = []
        for index, source in enumerate(sources):
            target = Path(root) / f"grid-{index}.csv"
            coverage.append(prefix(source, target, end_s))
            reduced.append(target)
        coarse_middle = compare(reduced[0], reduced[1], "reference",
                                initial_k, probes_k)
        middle_fine = compare(reduced[1], reduced[2], "reference",
                              initial_k, probes_k)
    return {
        "status": "DIAGNOSTIC_ONLY",
        "scope": (f"common [first observation,{end_s:g}s] window; does not replace "
                  "full memory/base excitation or establish convergence order"),
        "initial_k": initial_k,
        "probes_k": list(probes_k),
        "coverage": [dict(source=str(path), **item)
                     for path, item in zip(sources, coverage)],
        "coarse_to_middle": coarse_middle,
        "middle_to_fine": middle_fine,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--coarse", type=Path, required=True)
    parser.add_argument("--middle", type=Path, required=True)
    parser.add_argument("--fine", type=Path, required=True)
    parser.add_argument("--end-s", type=float, default=4.0)
    parser.add_argument("--initial-k", type=float, default=300.0)
    parser.add_argument("--probe-k", type=float, action="append", dest="probes_k")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.end_s <= 0:
        parser.error("--end-s must be positive")
    result = common_window(args.coarse, args.middle, args.fine, args.end_s,
                           args.initial_k, args.probes_k or (301.0, 330.0))
    with args.output.open("x") as output:
        json.dump(result, output, indent=2)
    print(json.dumps({
        "status": result["status"],
        "coarse_middle_max_abs_k": result["coarse_to_middle"]["max_abs_k"],
        "middle_fine_max_abs_k": result["middle_to_fine"]["max_abs_k"],
    }))


if __name__ == "__main__":
    main()
