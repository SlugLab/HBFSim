from __future__ import annotations

import subprocess
import time

import pytest

from adapters.vllm_capacity import gpu_occupancy
from adapters.vllm_capacity import run_capacity_pilot


def completed(stdout: str = "", stderr: str = "", code: int = 0):
    return subprocess.CompletedProcess([], code, stdout=stdout, stderr=stderr)


def install_fake_commands(monkeypatch, responses):
    calls = []

    def fake_run(argv, **kwargs):
        key = (tuple(argv), kwargs.get("input"))
        calls.append(key)
        response = responses[key]
        if isinstance(response, BaseException):
            raise response
        return response

    monkeypatch.setattr(gpu_occupancy.subprocess, "run", fake_run)
    return calls


PS = (("ps", "-eo", "pid=,ppid="), None)
SMI = (
    (
        "nvidia-smi",
        "--query-compute-apps=pid,process_name,used_memory",
        "--format=csv,noheader,nounits",
    ),
    None,
)
SERVERS = (("nvidia-cuda-mps-control",), "get_server_list\n")


def test_idle_mps_daemon_is_exclusive(monkeypatch):
    clients = (("nvidia-cuda-mps-control",), "get_client_list 200\n")
    install_fake_commands(
        monkeypatch,
        {
            PS: completed("100 1\n101 100\n"),
            SMI: completed("200, nvidia-cuda-mps-server, 52\n"),
            SERVERS: completed("200\n"),
            clients: completed(),
        },
    )
    snapshot = gpu_occupancy.gpu_occupancy_snapshot(100)
    assert snapshot["external_processes"] == []
    assert snapshot["mps_daemon_only"] is True
    assert snapshot["exclusive_for_process_tree"] is True


def test_external_mps_client_is_reported(monkeypatch):
    clients = (("nvidia-cuda-mps-control",), "get_client_list 200\n")
    install_fake_commands(
        monkeypatch,
        {
            PS: completed("100 1\n101 100\n"),
            SMI: completed("200, nvidia-cuda-mps-server, 52\n"),
            SERVERS: completed("200\n"),
            clients: completed("101\n999\n"),
        },
    )
    monkeypatch.setattr(
        gpu_occupancy, "_process_name", lambda pid: f"process-{pid}"
    )
    snapshot = gpu_occupancy.gpu_occupancy_snapshot(100)
    assert [item["pid"] for item in snapshot["owned_mps_clients"]] == [101]
    assert [item["pid"] for item in snapshot["external_processes"]] == [999]
    assert snapshot["exclusive_for_process_tree"] is False


def test_known_exited_descendant_remains_owned(monkeypatch):
    clients = (("nvidia-cuda-mps-control",), "get_client_list 200\n")
    install_fake_commands(
        monkeypatch,
        {
            PS: completed("100 1\n"),
            SMI: completed("200, nvidia-cuda-mps-server, 52\n"),
            SERVERS: completed("200\n"),
            clients: completed("101\n"),
        },
    )
    monkeypatch.setattr(gpu_occupancy, "_process_name", lambda _pid: None)
    snapshot = gpu_occupancy.gpu_occupancy_snapshot(
        100, known_owned_pids={101}
    )
    assert snapshot["external_processes"] == []
    assert [item["pid"] for item in snapshot["owned_mps_clients"]] == [101]


def test_unqueryable_visible_mps_server_fails_closed(monkeypatch):
    install_fake_commands(
        monkeypatch,
        {
            PS: completed("100 1\n"),
            SMI: completed("200, nvidia-cuda-mps-server, 52\n"),
            SERVERS: completed("201\n"),
        },
    )
    with pytest.raises(RuntimeError, match="not queryable"):
        gpu_occupancy.gpu_occupancy_snapshot(100)


def test_command_failure_and_malformed_output_fail_closed(monkeypatch):
    install_fake_commands(
        monkeypatch,
        {
            PS: completed("100 1\n"),
            SMI: completed(stderr="driver error", code=1),
        },
    )
    with pytest.raises(RuntimeError, match="occupancy command failed"):
        gpu_occupancy.gpu_occupancy_snapshot(100)

    install_fake_commands(
        monkeypatch,
        {
            PS: completed("100 1\n"),
            SMI: completed("not,a,valid,row\n"),
        },
    )
    with pytest.raises(RuntimeError, match="unparseable"):
        gpu_occupancy.gpu_occupancy_snapshot(100)


def test_watchdog_prestart_contamination_closes_without_join_error(monkeypatch):
    monkeypatch.setattr(run_capacity_pilot, "process_tree", lambda _pid: {100})
    monkeypatch.setattr(
        run_capacity_pilot,
        "external_gpu_processes",
        lambda *_args, **_kwargs: [{"pid": 999, "source": "mps-control"}],
    )
    watchdog = run_capacity_pilot.ExternalGpuWatchdog(100, interval=0.01)
    with pytest.raises(RuntimeError, match="BEFORE_WATCHDOG_START"):
        watchdog.start()
    watchdog.close()
    assert watchdog.started is False
    assert watchdog.records[0]["external"][0]["pid"] == 999


def test_watchdog_clean_start_and_close(monkeypatch):
    monkeypatch.setattr(run_capacity_pilot, "process_tree", lambda _pid: {100})
    monkeypatch.setattr(
        run_capacity_pilot,
        "external_gpu_processes",
        lambda *_args, **_kwargs: [],
    )
    watchdog = run_capacity_pilot.ExternalGpuWatchdog(100, interval=10.0)
    watchdog.start()
    watchdog.close()
    assert watchdog.started is True
    assert watchdog.thread.is_alive() is False


def test_watchdog_retries_one_transient_probe_error(monkeypatch):
    monkeypatch.setattr(run_capacity_pilot, "process_tree", lambda _pid: {100})
    call_count = 0

    def transient_then_clean(*_args, **_kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 2:
            raise RuntimeError("transient probe failure")
        return []

    monkeypatch.setattr(
        run_capacity_pilot,
        "external_gpu_processes",
        transient_then_clean,
    )
    watchdog = run_capacity_pilot.ExternalGpuWatchdog(
        100, interval=0.005, max_consecutive_probe_errors=3
    )
    watchdog.start()
    deadline = time.monotonic() + 1.0
    while len(watchdog.probe_errors) < 1 and time.monotonic() < deadline:
        time.sleep(0.005)
    watchdog.close()
    assert watchdog.error is None
    assert watchdog.probe_errors[0]["consecutive_failures"] == 1


def test_watchdog_fails_after_consecutive_probe_error_limit(monkeypatch):
    monkeypatch.setattr(run_capacity_pilot, "process_tree", lambda _pid: {100})
    call_count = 0

    def clean_start_then_fail(*_args, **_kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return []
        raise RuntimeError("persistent probe failure")

    monkeypatch.setattr(
        run_capacity_pilot,
        "external_gpu_processes",
        clean_start_then_fail,
    )
    watchdog = run_capacity_pilot.ExternalGpuWatchdog(
        100, interval=0.005, max_consecutive_probe_errors=3
    )
    watchdog.start()
    deadline = time.monotonic() + 1.0
    while watchdog.error is None and time.monotonic() < deadline:
        time.sleep(0.005)
    watchdog.close()
    assert "persistent probe failure" in str(watchdog.error)
    assert len(watchdog.probe_errors) == 3
