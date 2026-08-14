#!/usr/bin/env python3

import json
import os
import pathlib
import subprocess
import sys

import pytest


ROOT = pathlib.Path(__file__).resolve().parents[2]


def test_real_vllm_exact_workload(request) -> None:
    requested = request.config.getoption("--exact-profile")
    if requested is None:
        pytest.skip("exact workload was not requested")
    exact_profile = pathlib.Path(requested).resolve()
    model = pathlib.Path(os.environ.get(
        "HBFSIM_VLLM_MODEL", "/home/victoryang00/Qwen3-30B-A3B"
    )).resolve()
    build = pathlib.Path(os.environ.get(
        "HBFSIM_BUILD_DIR", ROOT / "build-sm120-exact"
    )).resolve()
    bpftime = pathlib.Path(os.environ.get(
        "HBFSIM_BPFTIME_BUILD_DIR", ROOT / "build-bpftime-hbfsim"
    )).resolve()
    for path, label in ((exact_profile, "exact profile"),
                        (model / "config.json", "vLLM model"),
                        (build / "libhbfsim_vllm_extension.so", "extension")):
        assert path.is_file(), f"missing {label}: {path}"
    report = exact_profile.parent / "vllm-exact-workload"
    baseline = report / "baseline"
    exact = report / "exact"
    environment = os.environ.copy()
    environment.update({
        "HBFSIM_BUILD_DIR": str(build),
        "HBFSIM_BPFTIME_BUILD_DIR": str(bpftime),
        "HBFSIM_VLLM_EXTENSION": str(
            build / "libhbfsim_vllm_extension.so"
        ),
        "HBFSIM_VLLM_CACHE": str(report / "cache"),
    })
    plugin = pathlib.Path(environment.get(
        "HBFSIM_VLLM_PLUGIN", "/dev/shm/hbfsim-vllm-plugin"
    ))
    environment["PYTHONPATH"] = str(plugin) + (
        ":" + environment["PYTHONPATH"]
        if environment.get("PYTHONPATH") else ""
    )
    common = [
        "--model", str(model),
        "--profile", str(ROOT / "configs/profiles/nominal.json"),
        "--num-prompts", "1", "--input-len", "32", "--output-len", "8",
        "--max-model-len", "64", "--max-num-batched-tokens", "64",
        "--hbf-parameter-regex",
        r"^model\.layers\.0\.mlp\.experts\.w13_weight$",
        "--hbf-range-bytes", "16384", "--seed", "0",
    ]
    subprocess.run([
        sys.executable, str(ROOT / "adapters/vllm/run.py"),
        "--mode", "baseline", "--report-dir", str(baseline), *common,
    ], cwd=ROOT, env=environment, check=True, timeout=600)
    subprocess.run([
        str(ROOT / "adapters/vllm/run_timing.sh"),
        "--exact-profile", str(exact_profile),
        "--exact-preheat", "--report-dir", str(exact), *common,
    ], cwd=ROOT, env=environment, check=True, timeout=1200)
    native = json.loads((baseline / "result.json").read_text())
    instrumented = json.loads((exact / "result.json").read_text())
    assert native["output_token_ids"] == instrumented["output_token_ids"]
    assert instrumented["exact_post_run_finalized"] is True
    assert instrumented["exact_session_count"] > 0
    assert instrumented["exact_scope"] == "one_shot_sideband_probe"
    assert instrumented["model_graph_fidelity"] == "native"
    assert instrumented["model_storage_registered"] is False
    assert instrumented["exact_probe"]["status"] == "passed"
    coverage = [json.loads(line) for line in
                (exact / "coverage.jsonl").read_text().splitlines()]
    post_run = [record for record in coverage
                if record.get("reason") == "exact_post_run_passed"]
    assert post_run
    assert all(record["admitted_fidelity"] == "exact" for record in post_run)
    assert all(record["future_issued"] == record["future_drained"] == 393216
               for record in post_run)
    assert all(record["future_faults"] == record["future_leaked"] == 0
               for record in post_run)


def main() -> int:
    probe = pathlib.Path(sys.argv[1])
    objdump = pathlib.Path(sys.argv[2])
    completed = subprocess.run(
        [str(objdump), "-h", "-t", str(probe)],
        text=True,
        capture_output=True,
        check=True,
    )
    assert "kprobe/fused_moe_kernel" in completed.stdout
    assert "cuda__hbf_moe" in completed.stdout
    assert "kprobe/unsupported_hbf_kernel" not in completed.stdout
    assert "license" in completed.stdout
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
