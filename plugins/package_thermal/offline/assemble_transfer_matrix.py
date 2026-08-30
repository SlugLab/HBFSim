#!/usr/bin/env python3
"""Assemble unit-step golden datasets into H_ij(t) transfer CSVs."""

from __future__ import annotations

import argparse
import csv
import pathlib
import re
import sys

from _common import OfflineError, load_dataset, sha256_file, write_json


def arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build thermal_transfer_matrix from unit-step datasets")
    parser.add_argument("--dataset", type=pathlib.Path, action="append", required=True)
    parser.add_argument("--run-manifest", type=pathlib.Path, required=True)
    parser.add_argument("--geometry", type=pathlib.Path, required=True)
    parser.add_argument("--output", type=pathlib.Path, required=True)
    return parser.parse_args()


def safe(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", name)


def main() -> int:
    args = arguments()
    datasets = [load_dataset(path) for path in args.dataset]
    reference = datasets[0]
    by_source = {}
    for path, dataset in zip(args.dataset, datasets):
        if dataset["trace_kind"] != "unit_step":
            raise OfflineError(f"transfer-matrix input is not unit_step: {path}")
        provenance = dataset["provenance"]
        source = provenance.get("active_source")
        step_watts = provenance.get("step_watts")
        if source not in dataset["input_names"] or not isinstance(step_watts, (int, float)) or step_watts <= 0:
            raise OfflineError("unit-step dataset provenance lacks active_source/step_watts")
        if source in by_source:
            raise OfflineError(f"duplicate unit-step source {source}")
        if (dataset["input_names"] != reference["input_names"] or
                dataset["output_names"] != reference["output_names"] or
                dataset["sample_period_ns"] != reference["sample_period_ns"]):
            raise OfflineError("unit-step datasets use incompatible node ordering")
        by_source[source] = (path, dataset, float(step_watts))
    if set(by_source) != set(reference["input_names"]):
        raise OfflineError("unit-step datasets do not cover every configured source")
    args.output.mkdir(parents=True, exist_ok=True)
    files = []
    for source in reference["input_names"]:
        path, dataset, step_watts = by_source[source]
        baseline = dataset["samples"][0]["temperature_c"]
        output = args.output / f"source-{safe(source)}.csv"
        with output.open("w", encoding="utf-8", newline="") as stream:
            writer = csv.writer(stream, lineterminator="\n")
            writer.writerow(["time_ns", *reference["output_names"]])
            for sample in dataset["samples"]:
                writer.writerow([sample["time_ns"], *[
                    (value - initial) / step_watts for value, initial in
                    zip(sample["temperature_c"], baseline)]])
        files.append({"source": source, "path": output.name,
                      "dataset_sha256": sha256_file(path),
                      "transfer_sha256": sha256_file(output)})
    write_json(args.output / "manifest.json",
               {"schema_version": 1, "three_d_ice_version": "4.0",
                "run_manifest_sha256": sha256_file(args.run_manifest),
                "geometry_sha256": sha256_file(args.geometry),
                "sample_period_ns": reference["sample_period_ns"],
                "source_ordering": reference["input_names"],
                "observation_ordering": reference["output_names"],
                "sources": files})
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except OfflineError as error:
        print(f"assemble_transfer_matrix.py: {error}", file=sys.stderr)
        raise SystemExit(2)
