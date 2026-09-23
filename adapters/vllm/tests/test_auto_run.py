import json
import os
import pathlib
import subprocess
import sys
import time

import pytest


ADAPTER = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ADAPTER))
import auto_run  # noqa: E402


def arguments(tmp_path, *extra):
    return auto_run.parse_args([
        "--model", "/model", "--profile", "/profile.json",
        "--report-dir", str(tmp_path / "report"),
        "--cache-root", str(tmp_path / "cache"),
        "--build-dir", str(tmp_path / "build"),
        "--bpftime-build-dir", str(tmp_path / "bpftime"),
        "--min-free-disk-gib", "0", *extra,
    ])


def publish_stage(command):
    staging = pathlib.Path(command[command.index("--staging-dir") + 1])
    pass_manifest = pathlib.Path(command[command.index("--pass-manifest") + 1])
    staging.mkdir(parents=True)
    staged = staging / ("a" * 64 + ".ptx")
    staged.write_text("transformed")
    pass_manifest.write_text(
        json.dumps({"module_id": "ptx:sha256:" + "a" * 64,
                    "kernel": "kernel.a", "instrumented": True,
                    "rewritten_instructions": 1}) + "\n" +
        json.dumps({"module_id": "ptx:sha256:" + "a" * 64,
                    "kernel": "kernel_b", "instrumented": True,
                    "rewritten_instructions": 1}) + "\n")
    manifest = {
        "status": "READY", "variants": [{
            "status": "READY", "staged_path": str(staged),
            "raw_sha256": "a" * 64,
            "staged_sha256": auto_run._sha256(staged),
            "entry_results": [
                {"kernel": "kernel.a", "status": "SUPPORTED_TRANSFORMED"},
                {"kernel": "kernel_b", "status": "SUPPORTED_TRANSFORMED"},
            ],
        }],
    }
    manifest_path = staging / "ptx-staging-manifest.json"
    manifest_path.write_text(json.dumps(manifest))
    artifacts = {str(path): auto_run._sha256(path)
                 for path in (staged, pass_manifest, manifest_path)}
    (staging / "COMPLETE.json").write_text(json.dumps({
        "status": "READY",
        "manifest_sha256": auto_run._sha256(manifest_path),
        "pass_manifest_path": str(pass_manifest),
        "pass_manifest_sha256": auto_run._sha256(pass_manifest),
        "artifact_sha256": artifacts,
    }))


def test_workflow_orders_baseline_stage_build_then_fresh_launch(tmp_path):
    calls = []

    def run(command, **kwargs):
        calls.append(("run", list(command)))
        if "auto_prepare_ptx.py" in str(command[1]):
            publish_stage(command)

    def build(entries, paths, args, env):
        calls.append(("build", list(entries)))
        paths.probe_object.write_bytes(b"probe")

    result = auto_run.execute(arguments(tmp_path), process_runner=run,
                              probe_builder=build,
                              resource_checker=lambda *_: {"mock": True},
                              timing_validator=lambda *_: {"mock": True},
                              bundle_checker=lambda *_: {"mock": True})

    assert result["status"] == "SUCCESS"
    assert [item[0] for item in calls] == ["run", "run", "build", "run"]
    assert "--mode" in calls[0][1] and "baseline" in calls[0][1]
    assert calls[2] == ("build", ["kernel.a", "kernel_b"])
    assert calls[3][1][1] == "--"
    assert calls[3][1][0].endswith("run_with_bpftime.sh")


def test_explicit_request_timeout_reaches_both_child_commands(tmp_path):
    calls = []

    def run(command, **kwargs):
        calls.append(list(command))
        if "auto_prepare_ptx.py" in str(command[1]):
            publish_stage(command)

    def build(entries, paths, args, env):
        paths.probe_object.write_bytes(b"probe")

    auto_run.execute(
        arguments(tmp_path, "--request-timeout-ns", "300000000000"),
        process_runner=run, probe_builder=build,
        resource_checker=lambda *_: {"mock": True},
        timing_validator=lambda *_: {"mock": True},
        bundle_checker=lambda *_: {"mock": True},
    )
    baseline = calls[0]
    timing = calls[-1]
    assert baseline[baseline.index("--request-timeout-ns") + 1] == "300000000000"
    assert timing[timing.index("--request-timeout-ns") + 1] == "300000000000"


def test_strict_not_ready_never_builds_or_launches_target(tmp_path):
    calls = []

    def run(command, **kwargs):
        calls.append(list(command))
        if "auto_prepare_ptx.py" in str(command[1]):
            publish_stage(command)
            staging = pathlib.Path(command[command.index("--staging-dir") + 1])
            manifest_path = staging / "ptx-staging-manifest.json"
            manifest = json.loads(manifest_path.read_text())
            manifest["status"] = "NOT_READY"
            manifest_path.write_text(json.dumps(manifest))
            complete_path = staging / "COMPLETE.json"
            complete = json.loads(complete_path.read_text())
            complete["manifest_sha256"] = auto_run._sha256(manifest_path)
            complete["status"] = "NOT_READY"
            complete["artifact_sha256"][str(manifest_path)] = auto_run._sha256(
                manifest_path)
            complete_path.write_text(json.dumps(complete))

    with pytest.raises(auto_run.WorkflowError, match="strict PTX staging"):
        auto_run.execute(arguments(tmp_path), process_runner=run,
                         probe_builder=lambda *_: pytest.fail("built probe"),
                         resource_checker=lambda *_: {"mock": True},
                         timing_validator=lambda *_: {"mock": True},
                         bundle_checker=lambda *_: {"mock": True})
    assert len(calls) == 2
    saved = json.loads((tmp_path / "report" / "workflow.json").read_text())
    assert saved["status"] == "FAILED"
    assert saved["failed_phase"] == "PTX_STAGE"


def test_off_is_one_direct_native_process_without_stage_or_agent(tmp_path):
    calls = []
    result = auto_run.execute(
        arguments(tmp_path, "--hbf-weight-selection", "off"),
        process_runner=lambda command, **kwargs: calls.append(
            (list(command), kwargs["env"])),
        probe_builder=lambda *_: pytest.fail("built probe"),
        resource_checker=lambda *_: {"mock": True},
        native_validator=lambda *_: {"mock": True},
    )

    assert result["instrumentation_status"] == "DISABLED_NATIVE"
    assert len(calls) == 1
    command, env = calls[0]
    assert command[0] == sys.executable
    assert command[1].endswith("run.py")
    assert command[command.index("--mode") + 1] == "baseline"
    assert "run_with_bpftime.sh" not in " ".join(command)
    assert "LD_PRELOAD" not in env or env["LD_PRELOAD"] == __import__("os").environ.get("LD_PRELOAD")
    assert not (tmp_path / "report" / "ptx-stage").exists()


def test_native_environment_removes_inherited_first_denial_path():
    native = auto_run._native_environment({
        auto_run.STRICT_DENIAL_ENV: "/stale/first-denial.json",
        "HBFSIM_INSTRUMENTATION_POLICY": "strict",
        "UNCHANGED": "yes",
    })
    assert auto_run.STRICT_DENIAL_ENV not in native
    assert "HBFSIM_INSTRUMENTATION_POLICY" not in native
    assert native["UNCHANGED"] == "yes"


def test_strict_capability_query_requires_bit_zero(tmp_path, monkeypatch):
    build = tmp_path / "build"
    bpftime = tmp_path / "bpftime"
    (bpftime / "runtime/agent").mkdir(parents=True)
    (bpftime / "runtime/syscall-server").mkdir(parents=True)
    build.mkdir()
    artifacts = [
        build / "libptxpass_hbf.so", build / "libhbfsim_launch_gate.so",
        build / "hbfsim_bpftime_attach_loader",
        build / "libhbfsim_vllm_extension.so", build / "hbfsimd",
        bpftime / "runtime/agent/libbpftime-agent.so",
        bpftime / "runtime/syscall-server/libbpftime-syscall-server.so",
        bpftime / "hbfsim-bpftime.provenance", tmp_path / "profile.json",
    ]
    for path in artifacts:
        path.write_bytes(b"artifact")
    (build / "hbfsim_bpftime_attach_loader").chmod(0o755)
    args = arguments(
        tmp_path / "args", "--build-dir", str(build),
        "--bpftime-build-dir", str(bpftime),
        "--profile", str(tmp_path / "profile.json"),
    )
    symbols = "\n".join((
        "hbfsim_instrumentation_policy_capabilities_v1",
        "bpftime_nv_strict_bridge_capabilities_v1",
        auto_run.STRICT_DENIAL_CAPABILITY,
    ))
    monkeypatch.setattr(auto_run.subprocess, "run", lambda *a, **k:
                        subprocess.CompletedProcess(a[0], 0, symbols, ""))
    monkeypatch.setattr(auto_run, "_query_capability", lambda *a, **k: 0)
    with pytest.raises(auto_run.WorkflowError, match="capability bit 0"):
        auto_run._preflight_bundle(args, {})


def test_off_does_not_require_profile_or_builds(tmp_path):
    args = auto_run.parse_args([
        "--model", "/model", "--report-dir", str(tmp_path / "report"),
        "--hbf-weight-selection", "off", "--min-free-disk-gib", "0",
    ])
    assert args.profile is None and args.build_dir is None


def test_failure_receipt_is_preserved(tmp_path):
    def fail(*args, **kwargs):
        raise auto_run.WorkflowError("baseline failed")

    with pytest.raises(auto_run.WorkflowError, match="baseline failed"):
        auto_run.execute(arguments(tmp_path), process_runner=fail,
                         resource_checker=lambda *_: {"mock": True},
                         timing_validator=lambda *_: {"mock": True},
                         bundle_checker=lambda *_: {"mock": True})
    saved = json.loads((tmp_path / "report" / "workflow.json").read_text())
    assert saved["status"] == "FAILED"
    assert saved["failed_phase"] == "BASELINE_DISCOVERY"
    assert "baseline failed" in saved["error"]


def test_strict_target_denial_is_structured_but_remains_failed(tmp_path):
    calls = []

    def run(command, **kwargs):
        calls.append((list(command), dict(kwargs["env"])))
        if "auto_prepare_ptx.py" in str(command[1]):
            publish_stage(command)
        if "run_with_bpftime.sh" in str(command[0]):
            report = tmp_path / "report"
            denial_path = pathlib.Path(
                kwargs["env"][auto_run.STRICT_DENIAL_ENV])
            maps_path = report / "first-denial.maps"
            maps_path.write_text("mapped libraries\n")
            denial_path.write_text(json.dumps({
                "schema_version": 1, "event": "strict_first_denial",
                "api": "cuLaunchKernel", "domain": "libcudart.so.12",
                "symbol_version": "libcudart.so.12",
                "reason": "strict_module_identity_required",
                "module_id": "", "kernel": "native_kernel",
                "original_function": "0x1", "lookup_function": "0x2",
                "launch_function": "0x3", "original_dso": "native.so",
                "lookup_dso": "libcudart.so.12",
                "maps_path": str(maps_path), "exit_code": 86,
            }))
            (report / "timing.stdout.receipt.json").write_text(json.dumps({
                "returncode": 86,
            }))
            raise auto_run.WorkflowError("process exited 86")

    def build(entries, paths, args, env):
        paths.probe_object.write_bytes(b"probe")

    with pytest.raises(auto_run.WorkflowError, match="exited 86"):
        auto_run.execute(
            arguments(tmp_path), process_runner=run, probe_builder=build,
            resource_checker=lambda *_: {"mock": True},
            timing_validator=lambda *_: pytest.fail("validated failed target"),
            bundle_checker=lambda *_: {"mock": True},
        )
    baseline_env = calls[0][1]
    target_env = calls[-1][1]
    assert auto_run.STRICT_DENIAL_ENV not in baseline_env
    assert target_env[auto_run.STRICT_DENIAL_ENV].endswith(
        "/report/first-denial.json")
    saved = json.loads((tmp_path / "report/workflow.json").read_text())
    assert saved["status"] == "FAILED"
    assert saved["failed_phase"] == "FRESH_TIMING_TARGET"
    assert saved["strict_first_denial"]["status"] == "VALIDATED_FAILURE"
    assert saved["strict_first_denial"]["scientific_success"] is False
    assert saved["strict_first_denial"]["fields"]["api"] == "cuLaunchKernel"


def test_first_denial_receipt_rejects_empty_maps_and_non86_process(tmp_path):
    report = tmp_path / "report"
    report.mkdir()
    denial_path = report / "first-denial.json"
    maps_path = report / "first-denial.maps"
    maps_path.write_text("")
    process_path = report / "timing.stdout.receipt.json"
    denial_path.write_text(json.dumps({
        "schema_version": 1, "event": "strict_first_denial",
        "api": "cuLaunchKernel", "domain": "libcudart.so.12",
        "reason": "denied", "kernel": "kernel",
        "original_dso": "native.so", "maps_path": str(maps_path),
        "exit_code": 86,
    }))
    process_path.write_text(json.dumps({"returncode": 86}))
    with pytest.raises(auto_run.WorkflowError, match="maps receipt"):
        auto_run._strict_denial_summary(report, denial_path, process_path)
    maps_path.write_text("maps\n")
    process_path.write_text(json.dumps({"returncode": 0}))
    with pytest.raises(auto_run.WorkflowError, match="exit 86"):
        auto_run._strict_denial_summary(report, denial_path, process_path)


def test_probe_has_one_safe_section_per_unique_entry():
    source = auto_run._probe_source(["foo", "bar.1"])
    assert source.count('SEC("kprobe/foo")') == 1
    assert source.count('SEC("kprobe/bar.1")') == 1
    assert "cuda__auto_0" in source and "cuda__auto_1" in source
    with pytest.raises(auto_run.WorkflowError, match="unsafe probe entry"):
        auto_run._probe_source(['bad\\"name'])


def test_timeout_preserves_output_and_terminates_process_group(tmp_path):
    stdout = tmp_path / "stdout"
    stderr = tmp_path / "stderr"
    program = (
        "import subprocess,sys,time; "
        "child=subprocess.Popen([sys.executable,'-c',"
        "'import signal,time; signal.signal(signal.SIGTERM, signal.SIG_IGN); "
        "time.sleep(30)']); "
        "print('parent-output', child.pid, flush=True); time.sleep(30)"
    )
    with pytest.raises(auto_run.WorkflowError, match="exceeded"):
        auto_run._run(
            [sys.executable, "-c", program], env=os.environ.copy(), timeout=1,
            stdout_path=stdout, stderr_path=stderr)
    words = stdout.read_text().split()
    assert words[0] == "parent-output"
    child_pid = int(words[1])
    for _ in range(20):
        state_path = pathlib.Path(f"/proc/{child_pid}/stat")
        if not state_path.exists() or state_path.read_text().split()[2] == "Z":
            break
        time.sleep(0.05)
    else:
        pytest.fail("timed-out grandchild remained running")
    receipt = json.loads((tmp_path / "stdout.receipt.json").read_text())
    assert receipt["timed_out"] is True
    assert receipt["returncode"] is not None


def test_parent_failure_also_terminates_ignoring_descendant(tmp_path):
    stdout = tmp_path / "failed.stdout"
    stderr = tmp_path / "failed.stderr"
    program = (
        "import subprocess,sys; "
        "child=subprocess.Popen([sys.executable,'-c',"
        "'import signal,time; signal.signal(signal.SIGTERM, signal.SIG_IGN); "
        "time.sleep(30)']); print(child.pid, flush=True); sys.exit(1)"
    )
    with pytest.raises(auto_run.WorkflowError, match="exited 1"):
        auto_run._run(
            [sys.executable, "-c", program], env=os.environ.copy(), timeout=5,
            stdout_path=stdout, stderr_path=stderr)
    child_pid = int(stdout.read_text().strip())
    for _ in range(20):
        state_path = pathlib.Path(f"/proc/{child_pid}/stat")
        if not state_path.exists() or state_path.read_text().split()[2] == "Z":
            break
        time.sleep(0.05)
    else:
        pytest.fail("failed parent's grandchild remained running")
    receipt = json.loads((tmp_path / "failed.stdout.receipt.json").read_text())
    assert receipt["descendant_cleanup_required"] is True


def test_parent_success_also_terminates_ignoring_descendant(tmp_path):
    stdout = tmp_path / "success.stdout"
    stderr = tmp_path / "success.stderr"
    program = (
        "import subprocess,sys; "
        "child=subprocess.Popen([sys.executable,'-c',"
        "'import signal,time; signal.signal(signal.SIGTERM, signal.SIG_IGN); "
        "time.sleep(30)']); print(child.pid, flush=True)"
    )
    auto_run._run(
        [sys.executable, "-c", program], env=os.environ.copy(), timeout=5,
        stdout_path=stdout, stderr_path=stderr)
    child_pid = int(stdout.read_text().strip())
    for _ in range(20):
        state_path = pathlib.Path(f"/proc/{child_pid}/stat")
        if not state_path.exists() or state_path.read_text().split()[2] == "Z":
            break
        time.sleep(0.05)
    else:
        pytest.fail("successful parent's grandchild remained running")
    receipt = json.loads((tmp_path / "success.stdout.receipt.json").read_text())
    assert receipt["returncode"] == 0
    assert receipt["descendant_cleanup_required"] is True


def test_runtime_validation_matches_tokens_and_rejects_strict_opaque(tmp_path):
    report = tmp_path / "report"
    paths = auto_run._paths(report)
    paths.baseline_report.mkdir(parents=True)
    paths.timing_report.mkdir(parents=True)
    common = {
        "request_terminal_status": "success", "scientific_status": "COMPLETE",
        "model": "/model", "num_prompts": 1, "input_len": 2,
        "output_len": 2, "max_model_len": 4,
        "max_num_batched_tokens": 4, "seed": 0, "warmup_requests": 0,
        "prompt_token_ids": [[1, 2]], "output_token_ids": [[3, 4]],
        "output_token_ids_sha256": "same",
    }
    (paths.baseline_report / "result.json").write_text(json.dumps(common))
    timing = dict(common, triton_exact_bindings=1)
    (paths.timing_report / "result.json").write_text(json.dumps(timing))
    (paths.timing_report / "triton-bindings.jsonl").write_text(
        json.dumps({"result": "bound", "required": True,
                    "original_function": "0x10", "kernel_name": "kernel"}) + "\n")
    (report / "strict-bridge.jsonl").write_text(json.dumps({
        "schema_version": 1, "api": "cuLaunchKernel", "gate_decision": 2,
        "exact_alias_found": True, "original_function": "0x10",
        "patched_function": "0x20", "selected_function": "0x20",
        "selected_path": "PATCHED", "cuda_result": 0,
        "kernel_name": "kernel",
    }) + "\n")
    coverage = report / "coverage.jsonl"
    coverage.write_text(json.dumps({
        "allowed": True, "modeled": False,
        "requires_instrumented_execution": True,
        "opaque_unmodeled": False, "reason": "outside_registered_range",
    }) + "\n")

    result = auto_run._validate_timing_results(paths, arguments(
        tmp_path / "unused"))
    assert result["coverage_status"] == "STRICT_DECISION_REQUIRES_INSTRUMENTATION"
    assert result["modeled_records"] == 0
    assert result["required_no_direct_hit_records"] == 1
    assert result["strict_bridge"]["joined_to_exact_binding_records"] == 1
    bridge_path = report / "strict-bridge.jsonl"
    bad_bridge = json.loads(bridge_path.read_text())
    bad_bridge.update(selected_path="ORIGINAL", selected_function="0x10")
    bridge_path.write_text(json.dumps(bad_bridge) + "\n")
    with pytest.raises(auto_run.WorkflowError, match="strict bridge"):
        auto_run._validate_timing_results(paths, arguments(tmp_path / "badbridge"))
    bad_bridge.update(selected_path="PATCHED", selected_function="0x20")
    bridge_path.write_text(json.dumps(bad_bridge) + "\n")
    coverage.write_text(
        json.dumps({"allowed": True, "modeled": False,
                    "requires_instrumented_execution": True,
                    "opaque_unmodeled": False, "reason": "no_hit"}) + "\n" +
        json.dumps({"allowed": True, "modeled": False,
                    "requires_instrumented_execution": False,
                    "opaque_unmodeled": True,
                    "reason": "opaque_unmodeled_timing"}) + "\n")
    with pytest.raises(auto_run.WorkflowError, match="strict runtime coverage"):
        auto_run._validate_timing_results(paths, arguments(tmp_path / "unused2"))
