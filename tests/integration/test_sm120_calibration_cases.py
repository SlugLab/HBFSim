#!/usr/bin/env python3
"""Validate immutable/disjoint SM120 calibration cases and native smoke."""

from __future__ import annotations

import hashlib
import json
import os
import pathlib
import shutil
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
CLASSES = {
    "ordinary_load", "ordinary_store", "tma_load", "tma_store",
    "unicast", "multicast", "mixed_hbm_hbf",
}
REQUIRED = {
    "id", "operation_class", "warps", "queue_depth", "load_use_distance",
    "cache_condition", "dimension_count", "dimensions", "multicast_mask",
    "cta_rank", "cluster_shape", "iterations", "bytes", "seed",
}


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def load(path: pathlib.Path, suite: str) -> tuple[dict, str]:
    raw = path.read_bytes()
    document = json.loads(raw)
    require(document["manifest_schema_version"] == 1, "wrong schema")
    require(document["suite"] == suite, "wrong suite")
    require(document["warmup"] >= 1 and document["repetitions"] >= 5,
            "insufficient repetition policy")
    contract = document.get("exact_profile_contract")
    require(isinstance(contract, dict), "exact profile contract missing")
    require(contract.get("cache_condition") in ("warm_l2", "cold"),
            "invalid deployment cache condition")
    require(contract.get("concurrency_condition") == "exclusive_process",
            "exact collection must require exclusive process")
    require(contract.get("clock_control") == "none",
            "exact collection must use the dynamic application clock policy")
    require(contract.get("cluster_shape") in ([1, 1, 1], [2, 1, 1]),
            "invalid deployment cluster shape")
    require(contract.get("thresholds") == {
        "p50_percent": 5, "p95_percent": 10, "counter_percent": 10,
    }, "thresholds are not frozen")
    require(set(contract.get("limits", {})) == {
        "max_thread_futures", "max_warp_futures", "max_cta_futures",
        "max_cluster_futures", "max_thread_async_objects",
        "max_warp_async_objects", "max_cta_async_objects",
        "max_cluster_async_objects",
    }, "exact limits missing")
    cases = document["cases"]
    ids: set[str] = set()
    classes: set[str] = set()
    for case in cases:
        require(set(case) == REQUIRED, f"wrong keys for {case.get('id')}")
        require(case["id"] not in ids, "duplicate case id")
        ids.add(case["id"])
        classes.add(case["operation_class"])
        require(case["operation_class"] in CLASSES, "unknown class")
        require(case["warps"] in (1, 2, 4, 8), "invalid warp count")
        require(case["queue_depth"] in (1, 2, 4, 8, 16), "invalid depth")
        require(case["load_use_distance"] in (0, 8, 32, 128),
                "invalid load/use distance")
        require(case["cache_condition"] in ("cold", "warm"),
                "invalid cache condition")
        require(0 <= case["dimension_count"] <= 5, "invalid dimensions")
        require(len(case["dimensions"]) == case["dimension_count"],
                "dimension count mismatch")
        require(all(isinstance(v, int) and v > 0 for v in case["dimensions"]),
                "invalid dimension extent")
        require(len(case["cluster_shape"]) == 3 and
                all(isinstance(v, int) and v > 0
                    for v in case["cluster_shape"]), "invalid cluster shape")
        require(case["iterations"] > 0 and case["bytes"] > 0,
                "empty case")
        if case["operation_class"] == "multicast":
            require(case["multicast_mask"] > 1, "multicast mask missing")
            require(case["cluster_shape"][0] > 1, "multicast cluster missing")
        else:
            require(case["multicast_mask"] in (0, 1), "unexpected mask")
    require(classes == CLASSES, f"missing operation class: {CLASSES-classes}")
    return document, hashlib.sha256(raw).hexdigest()


def main() -> int:
    training, training_hash = load(
        ROOT / "configs/calibration/sm120-training-cases.json", "training")
    holdout, holdout_hash = load(
        ROOT / "configs/calibration/sm120-holdout-cases.json", "holdout")
    require(training["exact_profile_contract"] ==
            holdout["exact_profile_contract"],
            "training/holdout exact contracts differ")
    training_ids = {case["id"] for case in training["cases"]}
    holdout_ids = {case["id"] for case in holdout["cases"]}
    require(training_ids.isdisjoint(holdout_ids), "training/holdout overlap")
    def operating_point(case):
        return tuple((key, json.dumps(value, sort_keys=True))
                     for key, value in sorted(case.items())
                     if key not in {"id", "seed"})
    training_points = {operating_point(case) for case in training["cases"]}
    require(all(operating_point(case) in training_points
                for case in holdout["cases"]),
            "holdout escaped the calibrated operating-point envelope")
    require({case["seed"] for case in training["cases"]}.isdisjoint(
            {case["seed"] for case in holdout["cases"]}),
            "training/holdout seeds overlap")
    combined = training["cases"] + holdout["cases"]
    require({case["warps"] for case in combined} >= {1, 2, 4, 8},
            "warp coverage missing")
    require({case["queue_depth"] for case in combined} >= {1, 2, 4, 8, 16},
            "depth coverage missing")
    require({case["load_use_distance"] for case in combined} >= {0, 8, 32, 128},
            "load/use coverage missing")
    require({case["cache_condition"] for case in combined} == {"cold", "warm"},
            "cache coverage missing")
    require({case["dimension_count"] for case in combined} >= {1, 2, 3, 4, 5},
            "TMA dimension coverage missing")
    require(any(case["cta_rank"] > 0 for case in combined), "CTA rank missing")
    require(any(case["multicast_mask"] > 1 for case in combined),
            "multicast mask missing")
    source = (ROOT / "benchmarks/cuda/sm120_calibration.cu").read_text()
    require("multicast_proxy" not in source and
            "multicast proxy" not in source.lower(),
            "multicast proxy is forbidden")
    for dimension in range(1, 6):
        require(
            f"cp.async.bulk.tensor.{dimension}d.shared::cta.global.tile."
            "mbarrier::complete_tx::bytes" in source,
            f"real TMA load {dimension}D opcode missing")
        require(
            f"cp.async.bulk.tensor.{dimension}d.global.shared::cta.tile."
            "bulk_group" in source,
            f"real TMA store {dimension}D opcode missing")
    require(
        "cp.async.bulk.tensor.5d.shared::cluster.global.tile."
        "mbarrier::complete_tx::bytes.multicast::cluster" in source,
        "real TMA multicast opcode missing")
    for field in ("executed_dimension_count", "executed_queue_depth",
                  "executed_multicast_mask", "executed_cluster_shape",
                  "issued_operations", "cache_condition_executed"):
        require(f'{{"{field}"' in source,
                f"native execution evidence missing: {field}")
    if len(sys.argv) == 2:
        benchmark = pathlib.Path(sys.argv[1]).resolve()
        cuobjdump_candidates = [
            shutil.which("cuobjdump"),
            str(pathlib.Path(os.environ.get("CUDA_HOME", "/usr/local/cuda")) /
                "bin/cuobjdump"),
            "/usr/local/cuda-13.0/bin/cuobjdump",
        ]
        cuobjdump = next((item for item in cuobjdump_candidates
                          if item is not None and
                          pathlib.Path(item).is_file()), None)
        require(cuobjdump is not None, "cuobjdump is unavailable")
        sass = subprocess.run(
            [cuobjdump, "--dump-sass", str(benchmark)], text=True,
            capture_output=True, check=False, timeout=60)
        require(sass.returncode == 0, sass.stderr)
        require("LDG.E.64" in sass.stdout and "STG.E.64" in sass.stdout,
                "ordinary memory SASS is missing")
        for dimension in range(1, 6):
            require(f"UTMALDG.{dimension}D" in sass.stdout,
                    f"TMA load {dimension}D SASS is missing")
            require(f"UTMASTG.{dimension}D" in sass.stdout,
                    f"TMA store {dimension}D SASS is missing")
        ptx = subprocess.run(
            [cuobjdump, "--dump-ptx", str(benchmark)], text=True,
            capture_output=True, check=False, timeout=60)
        require(ptx.returncode == 0 and
                "tensor.5d.shared::cluster.global.tile."
                "mbarrier::complete_tx::bytes.multicast::cluster" in ptx.stdout,
                "multicast PTX is missing from native binary")
        suites = (
            (training, training_hash,
             ROOT / "configs/calibration/sm120-training-cases.json"),
            (holdout, holdout_hash,
             ROOT / "configs/calibration/sm120-holdout-cases.json"),
        )
        for document, manifest_hash, path in suites:
            for case in document["cases"]:
                completed = subprocess.run(
                    [str(benchmark), "--cases", str(path),
                     "--case-id", case["id"]], text=True,
                    capture_output=True, check=False, timeout=60)
                require(completed.returncode == 0,
                        f"{case['id']}: {completed.stderr}")
                record = json.loads(completed.stdout)
                require(record["schema_version"] == 1,
                        "wrong record schema")
                require(record["case_id"] == case["id"],
                        "wrong executed case")
                require(record["case_manifest_sha256"] == manifest_hash,
                        "manifest hash mismatch")
                require(record["bit_exact"] and
                        record["output_sha256"] == record["expected_sha256"],
                        f"native output mismatch: {case['id']}")
                require(record["smid"] >= 0 and record["warpid"] >= 0,
                        "routing identifiers missing")
                require(record["timestamps"]["end"] >=
                        record["timestamps"]["issue"], "invalid timestamps")
                require(record["executed_dimension_count"] ==
                        case["dimension_count"], "executed dimension mismatch")
                require(record["executed_queue_depth"] == case["queue_depth"],
                        "executed queue depth mismatch")
                require(record["executed_multicast_mask"] ==
                        case["multicast_mask"], "executed multicast mismatch")
                require(record["executed_cluster_shape"] ==
                        case["cluster_shape"], "executed cluster mismatch")
                require(record["issued_operations"] > 0,
                        "issued operation count missing")
                require(record["cache_condition_executed"] ==
                        case["cache_condition"], "cache condition mismatch")
    print(json.dumps({"status": "passed", "training_sha256": training_hash,
                      "holdout_sha256": holdout_hash}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
