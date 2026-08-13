#!/usr/bin/env python3

import argparse
import json
import os
import pathlib
import subprocess
import sys
import tempfile


ROOT = pathlib.Path(__file__).resolve().parents[2]


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def sm120_present() -> bool:
    result = subprocess.run(
        ["nvidia-smi", "--query-gpu=compute_cap", "--format=csv,noheader"],
        text=True, capture_output=True,
    )
    return result.returncode == 0 and any(
        line.strip() == "12.0" for line in result.stdout.splitlines()
    )


def run(binary: pathlib.Path, environment: dict[str, str], directory: pathlib.Path,
        mode: str) -> dict:
    output = directory / f"{mode}.json"
    command = [
        str(binary), "--mode", mode,
        "--profile", str(ROOT / "configs/profiles/nominal.json"),
        "--report-dir", str(directory / f"{mode}-report"),
        "--output", str(output),
    ]
    completed = subprocess.run(
        command, cwd=ROOT, env=environment, text=True, capture_output=True,
        timeout=90,
    )
    require(completed.returncode == 0,
            f"{mode} TMA run failed ({completed.returncode}): "
            f"{completed.stderr.strip()}")
    report = json.loads(output.read_text())
    report["command"] = command
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--build-dir", type=pathlib.Path, required=True)
    parser.add_argument("--output", type=pathlib.Path)
    args = parser.parse_args()
    if not sm120_present():
        print("SM120 TMA live proof skipped: no CC 12.0 GPU", file=sys.stderr)
        return 77

    build = args.build_dir.resolve()
    binary = build / "benchmarks/cuda/sm120_tma_bench"
    gate = build / "libhbfsim_launch_gate.so"
    daemon = build / "hbfsimd"
    require(binary.is_file() and gate.is_file() and daemon.is_file(),
            "Stage 3 live targets are not built")

    with tempfile.TemporaryDirectory(prefix="hbfsim-sm120-tma-") as raw:
        directory = pathlib.Path(raw)
        environment = os.environ.copy()
        environment["LD_LIBRARY_PATH"] = ":".join(filter(None, (
            "/usr/lib/x86_64-linux-gnu",
            "/usr/local/cuda-13.0/targets/x86_64-linux/lib",
            environment.get("LD_LIBRARY_PATH", ""),
        )))
        environment["LD_PRELOAD"] = ":".join(filter(None, (
            str(gate), environment.get("LD_PRELOAD", ""),
        )))
        environment["HBFSIM_DAEMON_PATH"] = str(daemon)
        manifest_path = directory / "manifest.jsonl"
        environment["HBFSIM_PASS_MANIFEST_PATH"] = str(manifest_path)
        environment["HBFSIM_COVERAGE_PATH"] = str(
            directory / "coverage.jsonl"
        )
        linked = subprocess.run(
            ["ldd", str(binary)], env=environment, text=True,
            capture_output=True, check=True,
        ).stdout
        require("libcuda.so.1 => /usr/lib/" in linked,
                "live TMA test resolved the fake CUDA driver")

        native = run(binary, environment, directory, "native")
        instrumented = run(binary, environment, directory, "instrumented")
        for report in (native, instrumented):
            require(report["bit_exact"],
                    f"{report['mode']} TMA output was not byte exact")
            require(report["independent_checksum"] ==
                    report["expected_independent_checksum"],
                    f"{report['mode']} independent work differs")
            stamps = report["timestamps"]
            require(stamps["issue"] < stamps["independent_end"] <=
                    stamps["wait_end"],
                    f"{report['mode']} issue/work/wait timestamps differ")

        tma = instrumented["tma"]
        require(tma == {
            "issued": 1,
            "hbm_bytes": 0,
            "hbf_bytes": 256,
            "oob_bytes": 0,
            "fanout_targets": 1,
            "stale_generations": 0,
            "faults": 0,
            "leaked": 0,
        }, f"instrumented TMA conservation differs: {tma}")
        native_tail = (native["timestamps"]["wait_end"] -
                       native["timestamps"]["independent_end"])
        instrumented_tail = (instrumented["timestamps"]["wait_end"] -
                             instrumented["timestamps"]["independent_end"])
        require(instrumented_tail > native_tail,
                "shadow TMA completion did not delay the conjunctive wait")

        manifests = [json.loads(line) for line in
                     manifest_path.read_text().splitlines() if line]
        require(len(manifests) == 1, "TMA pass emitted an unexpected manifest count")
        manifest = manifests[0]
        require(manifest["manifest_schema_version"] == 4 and
                manifest["tma_transform_version"] == "sm120-tma-v1",
                "TMA schema-v4 evidence is missing")
        require(manifest["tensormap_parameters"] == [0] and
                manifest["maximum_live_async_objects"] == 1 and
                manifest["tma_ambiguities"] == [],
                "TMA provenance/liveness evidence differs")
        table = manifest["tma_instruction_table"]
        require(len(table) == 1 and table[0]["dimensions"] == 2 and
                table[0]["direction"] == "global_to_shared" and
                table[0]["mode"] == "tile" and
                table[0]["completion"] == "mbarrier",
                f"TMA instruction evidence differs: {table}")

        summary = {
            "schema_version": 1,
            "status": "passed",
            "gpu_compute_capability": "12.0",
            "bit_exact": True,
            "tma_leaks": 0,
            "stale_generations": 0,
            "native_wait_tail_ns": native_tail,
            "instrumented_wait_tail_ns": instrumented_tail,
            "reports": [native, instrumented],
            "manifest": manifest,
        }
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(json.dumps(summary, indent=2) + "\n")
        print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
