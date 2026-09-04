"""Fail-closed GPU occupancy inspection with NVIDIA MPS client awareness."""

from __future__ import annotations

import pathlib
import subprocess
from typing import Any, Iterable


MPS_SERVER_BASENAME = "nvidia-cuda-mps-server"
COMMAND_TIMEOUT_SECONDS = 5.0


def _run_text(
    argv: list[str], *, input_text: str | None = None
) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(
        argv,
        input=input_text,
        capture_output=True,
        text=True,
        timeout=COMMAND_TIMEOUT_SECONDS,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(
            f"occupancy command failed ({completed.returncode}): "
            f"{argv!r}: {completed.stderr.strip()}"
        )
    return completed


def process_tree(root_pid: int) -> set[int]:
    if root_pid <= 0:
        raise ValueError("root_pid must be positive")
    completed = _run_text(["ps", "-eo", "pid=,ppid="])
    parents: dict[int, int] = {}
    for line in completed.stdout.splitlines():
        fields = line.split()
        if len(fields) != 2 or not all(field.isdigit() for field in fields):
            raise RuntimeError(f"unparseable ps row: {line!r}")
        parents[int(fields[0])] = int(fields[1])
    owned = {root_pid}
    changed = True
    while changed:
        changed = False
        for pid, parent in parents.items():
            if parent in owned and pid not in owned:
                owned.add(pid)
                changed = True
    return owned


def gpu_compute_processes() -> list[dict[str, Any]]:
    completed = _run_text(
        [
            "nvidia-smi",
            "--query-compute-apps=pid,process_name,used_memory",
            "--format=csv,noheader,nounits",
        ]
    )
    records: list[dict[str, Any]] = []
    for line in completed.stdout.splitlines():
        if not line.strip():
            continue
        fields = [field.strip() for field in line.split(",", 2)]
        if len(fields) != 3 or not fields[0].isdigit():
            raise RuntimeError(f"unparseable nvidia-smi compute row: {line!r}")
        memory = fields[2].split()[0]
        if not memory.isdigit():
            raise RuntimeError(f"unparseable nvidia-smi memory field: {line!r}")
        records.append(
            {
                "pid": int(fields[0]),
                "process_name": fields[1],
                "used_memory_mib": int(memory),
                "source": "nvidia-smi",
            }
        )
    return records


def _parse_pid_list(output: str, *, command: str) -> list[int]:
    pids: list[int] = []
    for line in output.splitlines():
        value = line.strip()
        if not value:
            continue
        if not value.isdigit():
            raise RuntimeError(f"unparseable {command} row: {line!r}")
        pids.append(int(value))
    if len(pids) != len(set(pids)):
        raise RuntimeError(f"duplicate PID in {command} output: {pids!r}")
    return pids


def mps_server_pids() -> list[int]:
    completed = _run_text(
        ["nvidia-cuda-mps-control"], input_text="get_server_list\n"
    )
    return _parse_pid_list(completed.stdout, command="get_server_list")


def mps_client_pids(server_pid: int) -> list[int]:
    if server_pid <= 0:
        raise ValueError("server_pid must be positive")
    completed = _run_text(
        ["nvidia-cuda-mps-control"],
        input_text=f"get_client_list {server_pid}\n",
    )
    return _parse_pid_list(
        completed.stdout, command=f"get_client_list {server_pid}"
    )


def _process_name(pid: int) -> str | None:
    try:
        value = pathlib.Path(f"/proc/{pid}/comm").read_text(
            encoding="utf-8", errors="replace"
        )
    except (FileNotFoundError, PermissionError, ProcessLookupError):
        return None
    return value.strip() or None


def _is_mps_server(record: dict[str, Any]) -> bool:
    return pathlib.PurePath(str(record["process_name"])).name == MPS_SERVER_BASENAME


def gpu_occupancy_snapshot(
    root_pid: int, *, known_owned_pids: Iterable[int] = ()
) -> dict[str, Any]:
    """Return an auditable occupancy snapshot or raise when it cannot be proven."""
    owned = process_tree(root_pid)
    owned.update(int(pid) for pid in known_owned_pids)
    if any(pid <= 0 for pid in owned):
        raise RuntimeError(f"invalid owned PID set: {sorted(owned)!r}")

    compute = gpu_compute_processes()
    mps_rows = [record for record in compute if _is_mps_server(record)]
    direct_rows = [record for record in compute if not _is_mps_server(record)]

    controlled_servers: list[int] = []
    client_records: list[dict[str, Any]] = []
    if mps_rows:
        controlled_servers = mps_server_pids()
        controlled_set = set(controlled_servers)
        visible_set = {int(record["pid"]) for record in mps_rows}
        missing = sorted(visible_set - controlled_set)
        if missing:
            raise RuntimeError(
                "visible MPS server is not queryable through the control plane: "
                f"{missing!r}"
            )
        for server_pid in sorted(visible_set):
            for client_pid in mps_client_pids(server_pid):
                client_records.append(
                    {
                        "pid": client_pid,
                        "process_name": _process_name(client_pid),
                        "used_memory_mib": None,
                        "source": "mps-control",
                        "mps_server_pid": server_pid,
                    }
                )

    external_direct = [
        record for record in direct_rows if int(record["pid"]) not in owned
    ]
    external_clients = [
        record for record in client_records if int(record["pid"]) not in owned
    ]
    owned_direct = [
        record for record in direct_rows if int(record["pid"]) in owned
    ]
    owned_clients = [
        record for record in client_records if int(record["pid"]) in owned
    ]
    external = external_direct + external_clients
    return {
        "root_pid": root_pid,
        "owned_pids": sorted(owned),
        "nvidia_compute_processes": compute,
        "mps_controlled_server_pids": controlled_servers,
        "mps_clients": client_records,
        "owned_direct_processes": owned_direct,
        "owned_mps_clients": owned_clients,
        "external_processes": external,
        "mps_daemon_only": bool(mps_rows) and not client_records,
        "exclusive_for_process_tree": not external,
    }


def external_gpu_processes(
    root_pid: int, *, known_owned_pids: Iterable[int] = ()
) -> list[dict[str, Any]]:
    return gpu_occupancy_snapshot(
        root_pid, known_owned_pids=known_owned_pids
    )["external_processes"]
