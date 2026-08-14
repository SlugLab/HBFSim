#!/usr/bin/env python3

import argparse
import json
import os
import pathlib
import subprocess
import sys
import tempfile


ROOT = pathlib.Path(__file__).resolve().parents[2]

SCENARIOS = [
    *(f"load_{rank}d" for rank in range(1, 6)),
    *(f"store_{rank}d" for rank in range(1, 6)),
    "oob_zero", "phase_reuse", "source_reuse", "host_replace",
    "device_replace", "descriptor_copy", "im2col_load", "im2col_store",
    "im2col_wide", "multicast",
    "oob_nan_f16", "oob_nan_f32", "oob_nan_f64", "oob_nan_bf16",
    "oob_nan_f32_ftz", "oob_nan_tf32", "oob_nan_tf32_ftz",
]


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


def run(binary: pathlib.Path, environment: dict[str, str],
        directory: pathlib.Path, scenario: str, mode: str, backend: str,
        profile: pathlib.Path, reference: pathlib.Path | None = None,
        iterations: int = 1) -> tuple[dict, pathlib.Path]:
    output = directory / f"{scenario}-{mode}-{backend}.json"
    command = [
        str(binary), "--mode", mode,
        "--backend", backend,
        "--profile", str(profile),
        "--report-dir", str(directory / f"{scenario}-{mode}-{backend}-report"),
        "--backing-dir", str(directory),
        "--scenario", scenario,
        "--iterations", str(iterations),
        "--output", str(output),
    ]
    if reference is not None:
        command += ["--reference", str(reference)]
    completed = subprocess.run(
        command, cwd=ROOT, env=environment, text=True, capture_output=True,
        timeout=120,
    )
    require(completed.returncode == 0,
            f"{scenario} {mode}/{backend} failed "
            f"({completed.returncode}): {completed.stderr.strip()}")
    report = json.loads(output.read_text())
    report["command"] = command
    return report, output


def validate_common(report: dict, scenario: str) -> None:
    require(report["schema_version"] == 2, f"{scenario} report schema differs")
    require(report["scenario"] == scenario, f"{scenario} identity differs")
    require(report["bit_exact"], f"{scenario} output was not byte exact")
    require(report["independent_checksum"] ==
            report["expected_independent_checksum"],
            f"{scenario} independent work differs")
    stamps = report["timestamps"]
    require(stamps["issue"] < stamps["independent_end"] <= stamps["wait_end"],
            f"{scenario} issue/work/wait timestamps differ")


def issue_count(scenario: str) -> int:
    return 2 if scenario == "phase_reuse" else 1


def fanout_count(scenario: str) -> int:
    return 2 if scenario == "multicast" else 1


def validate_tma_stats(report: dict, scenario: str, backend: str) -> None:
    stats = report["tma"]
    iterations = report["iterations"]
    issues = issue_count(scenario) * iterations
    require(stats["issued"] == issues,
            f"{scenario}/{backend} issue conservation differs: {stats}")
    require(stats["fanout_targets"] == issues * fanout_count(scenario),
            f"{scenario}/{backend} fanout conservation differs: {stats}")
    require(stats["stale_generations"] == 0 and stats["faults"] == 0 and
            stats["leaked"] == 0,
            f"{scenario}/{backend} runtime safety differs: {stats}")

    nan_bytes = {
        "oob_nan_f16": 2,
        "oob_nan_f32": 4,
        "oob_nan_f64": 8,
        "oob_nan_bf16": 2,
        "oob_nan_f32_ftz": 4,
        "oob_nan_tf32": 4,
        "oob_nan_tf32_ftz": 4,
    }
    if scenario in nan_bytes:
        element_bytes = nan_bytes[scenario]
        in_bounds_bytes = 16 * element_bytes * iterations
        expected = {
            "hbm_bytes": in_bounds_bytes // 2 if backend == "timing" else 0,
            "hbf_bytes": (in_bounds_bytes // 2 if backend == "timing"
                          else in_bounds_bytes),
            "oob_bytes": 48 * element_bytes * iterations,
        }
    elif scenario == "oob_zero":
        expected = {"hbm_bytes": 0, "hbf_bytes": 64 * iterations,
                    "oob_bytes": 192 * iterations}
    elif backend == "timing":
        expected = {"hbm_bytes": 128 * issues,
                    "hbf_bytes": 128 * issues, "oob_bytes": 0}
    else:
        expected = {"hbm_bytes": 0, "hbf_bytes": 256 * issues,
                    "oob_bytes": 0}
    for key, value in expected.items():
        require(stats[key] == value,
                f"{scenario}/{backend} {key} differs: {stats}")


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
        coverage_path = directory / "coverage.jsonl"
        environment["HBFSIM_PASS_MANIFEST_PATH"] = str(manifest_path)
        environment["HBFSIM_COVERAGE_PATH"] = str(coverage_path)
        linked = subprocess.run(
            ["ldd", str(binary)], env=environment, text=True,
            capture_output=True, check=True,
        ).stdout
        require("libcuda.so.1 => /usr/lib/" in linked,
                "live TMA test resolved the fake CUDA driver")

        nominal = ROOT / "configs/profiles/nominal.json"
        capacity_profile = directory / "capacity-profile.json"
        capacity_config = json.loads(nominal.read_text())
        capacity_config.update({"hbm_cache_bytes": 32768,
                                "queue_depth": 8,
                                "time_scale": 1})
        capacity_profile.write_text(json.dumps(capacity_config, indent=2) + "\n")

        reports: list[dict] = []
        native_by_scenario: dict[str, dict] = {}
        timing_by_scenario: dict[str, dict] = {}
        capacity_by_scenario: dict[str, dict] = {}
        for scenario in SCENARIOS:
            native, native_path = run(binary, environment, directory, scenario,
                                      "native", "hbm", nominal)
            validate_common(native, scenario)
            native_by_scenario[scenario] = native
            reports.append(native)

            timing, _ = run(binary, environment, directory, scenario,
                            "instrumented", "timing", nominal, native_path)
            validate_common(timing, scenario)
            require(timing["output_hex"] == native["output_hex"],
                    f"{scenario} timing output differs from native")
            validate_tma_stats(timing, scenario, "timing")
            timing_by_scenario[scenario] = timing
            reports.append(timing)

            repeated = 2 if scenario in ("load_2d", "store_2d") else 1
            capacity, _ = run(binary, environment, directory, scenario,
                              "instrumented", "capacity", capacity_profile,
                              native_path, repeated)
            validate_common(capacity, scenario)
            require(capacity["output_hex"] == native["output_hex"],
                    f"{scenario} capacity output differs from native")
            validate_tma_stats(capacity, scenario, "capacity")
            capacity_by_scenario[scenario] = capacity
            reports.append(capacity)

        for scenario in ("load_2d", "store_2d"):
            samples = capacity_by_scenario[scenario]["iteration_runtime"]
            require(len(samples) == 2 and
                    samples[0]["capacity_cache_misses"] == 1 and
                    samples[0]["capacity_cache_hits"] == 0 and
                    samples[1]["capacity_cache_misses"] == 1 and
                    samples[1]["capacity_cache_hits"] == 1,
                    f"{scenario} cold/warm capacity evidence differs: {samples}")
        require(capacity_by_scenario["store_2d"]["final_runtime"]
                ["capacity_dirty_writebacks"] == 1,
                "capacity store flush did not prove a dirty writeback")

        timing_delays = []
        capacity_delays = []
        for scenario in SCENARIOS:
            native_tail = (native_by_scenario[scenario]["timestamps"]["wait_end"] -
                           native_by_scenario[scenario]["timestamps"]
                           ["independent_end"])
            timing_tail = (timing_by_scenario[scenario]["timestamps"]["wait_end"] -
                           timing_by_scenario[scenario]["timestamps"]
                           ["independent_end"])
            capacity_tail = (capacity_by_scenario[scenario]["timestamps"]
                             ["wait_end"] - capacity_by_scenario[scenario]
                             ["timestamps"]["independent_end"])
            timing_delays.append(timing_tail > native_tail)
            capacity_delays.append(capacity_tail > native_tail)
        require(any(timing_delays),
                "no timing-backed conjunctive wait exceeded native")
        require(any(capacity_delays),
                "no capacity-backed conjunctive wait exceeded native")

        manifests = [json.loads(line) for line in
                     manifest_path.read_text().splitlines() if line]
        require(len(manifests) == 2 * len(SCENARIOS),
                "TMA pass emitted an unexpected manifest count")
        observed_modes = set()
        observed_dimensions = set()
        multicast_seen = False
        descriptor_copy_seen = False
        for manifest in manifests:
            require(manifest["manifest_schema_version"] == 4 and
                    manifest["tma_transform_version"] == "sm120-tma-v1",
                    "TMA schema-v4 evidence is missing")
            require(manifest["tensormap_parameters"] and
                    manifest["maximum_live_async_objects"] >= 1 and
                    manifest["tma_ambiguities"] == [],
                    f"TMA provenance/liveness differs: {manifest['kernel']}")
            table = manifest["tma_instruction_table"]
            require(table, f"TMA instruction evidence missing: {manifest['kernel']}")
            observed_modes.update(item["mode"] for item in table)
            observed_dimensions.update(item["dimensions"] for item in table)
            multicast_seen |= any(
                item["multicast"] is True and
                item["multicast_mask"] == 3 and
                item["multicast_mask_operand"].startswith("%") and
                item["multicast_mask_kind"] == "constant_register"
                for item in table
            )
            descriptor_copy_seen |= bool(manifest["descriptor_instruction_ids"] and
                                         manifest["kernel"] ==
                                         "sm120_tma_descriptor_copy_1d")
        require({"tile", "im2col", "im2col_wide"} <= observed_modes,
                f"TMA mode coverage differs: {observed_modes}")
        require(set(range(1, 6)) <= observed_dimensions,
                f"TMA dimension coverage differs: {observed_dimensions}")
        require(multicast_seen, "multicast mask evidence is absent")
        require(descriptor_copy_seen, "descriptor copy/fence evidence is absent")

        coverage = [json.loads(line) for line in
                    coverage_path.read_text().splitlines() if line]
        require(coverage and all(item["allowed"] for item in coverage),
                "a Stage 3 live launch was rejected")
        summary = {
            "schema_version": 2,
            "status": "passed",
            "gpu_compute_capability": "12.0",
            "scenario_count": len(SCENARIOS),
            "run_count": len(reports),
            "bit_exact": True,
            "tma_leaks": 0,
            "stale_generations": 0,
            "capacity_cold_warm_proved": True,
            "capacity_writeback_proved": True,
            "native_timing_capacity_equivalent": True,
            "scenarios": SCENARIOS,
            "reports": reports,
            "manifest_count": len(manifests),
            "coverage_record_count": len(coverage),
        }
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(json.dumps(summary, indent=2) + "\n")
        print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
