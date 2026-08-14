import os
import json
import pathlib
import sys
from types import SimpleNamespace


ADAPTER = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ADAPTER))

import run as run_module  # noqa: E402
from run import configure_environment, suppress_static_fatbin_scan  # noqa: E402


def test_suppression_is_scoped(monkeypatch):
    monkeypatch.delenv("BPFTIME_CUDA_DISABLE_CUOBJDUMP", raising=False)

    with suppress_static_fatbin_scan(enabled=True):
        assert os.environ["BPFTIME_CUDA_DISABLE_CUOBJDUMP"] == "1"

    assert "BPFTIME_CUDA_DISABLE_CUOBJDUMP" not in os.environ


def test_suppression_restores_existing_value(monkeypatch):
    monkeypatch.setenv("BPFTIME_CUDA_DISABLE_CUOBJDUMP", "custom")

    with suppress_static_fatbin_scan(enabled=True):
        assert os.environ["BPFTIME_CUDA_DISABLE_CUOBJDUMP"] == "1"

    assert os.environ["BPFTIME_CUDA_DISABLE_CUOBJDUMP"] == "custom"


def test_runner_forces_single_process_v1(monkeypatch, tmp_path):
    monkeypatch.setenv("HBFSIM_VLLM_CACHE", str(tmp_path / "cache"))
    monkeypatch.setenv("VLLM_ENABLE_V1_MULTIPROCESSING", "1")
    monkeypatch.setenv("HBFSIM_BUILD_DIR", str(tmp_path / "build"))
    monkeypatch.delenv("HBFSIM_DAEMON_PATH", raising=False)

    configure_environment(tmp_path / "report")

    assert os.environ["VLLM_ENABLE_V1_MULTIPROCESSING"] == "0"
    assert os.environ["VLLM_NO_USAGE_STATS"] == "1"
    assert os.environ["HBFSIM_DAEMON_PATH"] == str(
        tmp_path / "build" / "hbfsimd"
    )


def test_repository_commit_ignores_preload_logs(monkeypatch):
    commit = "a" * 40
    monkeypatch.setattr(
        run_module.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(
            stdout=f"bpftime preload log\n{commit}\n"
        ),
    )

    assert run_module.repository_commit() == commit


def test_timing_model_defaults_to_hybrid(monkeypatch):
    monkeypatch.setattr(
        sys, "argv", ["run.py", "--mode", "timing", "--model", "/model",
                      "--profile", "/profile.json", "--report-dir", "/report"]
    )
    assert run_module.parse_args().hbf_timing_model == "hybrid"


def test_exact_contract_requires_passed_warm_l2_profile(tmp_path):
    profile = tmp_path / "exact.json"
    profile.write_text(json.dumps({
        "schema_version": 2,
        "conditions": {
            "cache_condition": "warm_l2",
            "concurrency_condition": "exclusive_process",
            "cluster_shape": {"x": 1, "y": 1, "z": 1},
        },
        "validation": {"status": "passed"},
    }))

    assert run_module.exact_contract(str(profile)) == {
        "exact_profile_path": str(profile.resolve()),
        "exact_cache_condition": "warm_l2",
        "exact_cluster_x": 1,
        "exact_cluster_y": 1,
        "exact_cluster_z": 1,
    }

    profile.write_text("{}")
    try:
        run_module.exact_contract(str(profile))
    except SystemExit as error:
        assert "schema v2" in str(error)
    else:
        raise AssertionError("malformed exact profile was accepted")


def test_exact_preheat_requires_exact_mode(monkeypatch):
    monkeypatch.setattr(
        sys, "argv", ["run.py", "--mode", "timing", "--model", "/model",
                      "--profile", "/profile.json", "--report-dir", "/report",
                      "--exact-preheat"]
    )
    args = run_module.parse_args()
    try:
        run_module.validate_args(args)
    except SystemExit as error:
        assert "--exact-preheat requires exact mode" in str(error)
    else:
        raise AssertionError("timing mode accepted exact preheat")


def test_exact_manifest_declares_sideband_scope(monkeypatch, tmp_path):
    args = SimpleNamespace(
        mode="exact", model="/model", profile="/nominal.json",
        exact_profile="/exact.json", num_prompts=1, input_len=1,
        output_len=1, max_model_len=2, max_num_batched_tokens=2,
        seed=0, hbf_parameter_regex="weight", hbf_range_bytes=8,
        hbf_timing_model="hybrid", exact_preheat=True,
    )
    monkeypatch.setattr(run_module, "runtime_versions", lambda: {})
    monkeypatch.setattr(run_module, "repository_commit", lambda: "a" * 40)
    monkeypatch.setenv("CC", "/usr/bin/gcc-13")
    monkeypatch.setenv("CXX", "/usr/bin/g++-13")

    manifest = run_module.base_manifest(args, tmp_path)

    assert manifest["exact_scope"] == "one_shot_sideband_probe"
    assert manifest["model_graph_fidelity"] == "native"
    assert manifest["model_storage_registered"] is False
    assert manifest["hbf_selection"]["registered_in_model_graph"] is False


def test_exact_wrapper_uses_probe_only_attach_target():
    wrapper = (ADAPTER / "run_timing.sh").read_text()
    builder = (ADAPTER / "build.sh").read_text()

    assert "exact_bpftime_probe=${HBFSIM_VLLM_EXACT_BPFTIME_PROBE" in wrapper
    assert "/llama_probe.bpf.o}" in wrapper
    assert 'if [[ $run_mode == exact ]]' in wrapper
    assert 'run.py" --mode exact' in wrapper
    assert '--defer-exact-probe "$@"' in wrapper
    assert 'run_exact_probe.py"' in wrapper
    assert 'run_exact_probe.py" "${probe_arguments[@]}"' in wrapper
    assert 'run_exact_probe.py" "$@"' not in wrapper
    assert "hbfsim_llama_probe hbfsim_llama_probe_ptx" in builder
    assert "hbfsim_llama_probe_module" in builder
    exact_branch = wrapper.split("if [[ -n $exact_profile ]]", 1)[1]
    assert exact_branch.index("else\n    if [[ ! -d $triton_cache ]]") < \
        exact_branch.index("prepare_triton_ptx.py")


def test_deferred_exact_probe_requires_exact_mode(monkeypatch):
    monkeypatch.setattr(
        sys, "argv", ["run.py", "--mode", "timing", "--model", "/model",
                      "--profile", "/profile.json", "--report-dir", "/report",
                      "--defer-exact-probe"]
    )
    args = run_module.parse_args()
    try:
        run_module.validate_args(args)
    except SystemExit as error:
        assert "--defer-exact-probe requires exact mode" in str(error)
    else:
        raise AssertionError("timing mode accepted deferred exact probe")
