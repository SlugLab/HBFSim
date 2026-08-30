#!/usr/bin/env python3
"""Write an auditable HBFSim package-thermal experiment manifest."""

from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import os
import pathlib
import platform
import socket
import subprocess
import sys


class ManifestError(RuntimeError):
    pass


def arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate a reproducible package-thermal experiment manifest")
    parser.add_argument("--output", type=pathlib.Path, required=True)
    parser.add_argument("--repo", type=pathlib.Path,
                        default=pathlib.Path(__file__).resolve().parents[1])
    parser.add_argument("--device-profile", type=pathlib.Path, required=True)
    parser.add_argument("--package-profile", type=pathlib.Path)
    parser.add_argument("--thermal-model", type=pathlib.Path)
    parser.add_argument("--model-kind", choices=("rom", "plugin"))
    parser.add_argument("--power-trace", type=pathlib.Path, action="append",
                        default=[])
    parser.add_argument("--thermal-mode",
                        choices=("off", "legacy_logp", "package_rc"),
                        required=True)
    parser.add_argument("--thermal-stage",
                        choices=("off", "read_only", "shadow", "active"),
                        required=True)
    parser.add_argument("--thermal-clock",
                        choices=("none", "model_time_replay", "live_monotonic"),
                        required=True)
    parser.add_argument("--three-d-ice-version")
    parser.add_argument("--three-d-ice-commit")
    parser.add_argument("--command", required=True,
                        help="exact experiment command, quoted as one argument")
    parser.add_argument("--phase2", action="store_true",
                        help="require Phase-II workload/evidence metadata")
    parser.add_argument("--experiment-id")
    parser.add_argument("--workload")
    parser.add_argument("--seed", type=int)
    parser.add_argument("--offered-byte-rate", type=float)
    parser.add_argument("--arrival-mode", choices=("periodic", "poisson", "fixed_qd"))
    parser.add_argument("--queue-depth", type=int)
    parser.add_argument("--read-ratio", type=float)
    parser.add_argument("--address-pattern", choices=("sequential", "random", "burst"))
    parser.add_argument("--stack-height", type=int, choices=(8, 16))
    parser.add_argument("--peak-byte-rate", type=float)
    parser.add_argument("--unthrottled-byte-rate", type=float)
    parser.add_argument("--evidence-grid", type=pathlib.Path)
    return parser.parse_args()


def command(items: list[str], *, optional: bool = False) -> str | None:
    try:
        result = subprocess.run(items, text=True, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, timeout=60,
                                check=False)
    except (OSError, subprocess.TimeoutExpired) as error:
        if optional:
            return None
        raise ManifestError(f"failed to run {' '.join(items)}: {error}") from error
    if result.returncode != 0:
        if optional:
            return None
        raise ManifestError(
            f"command failed ({' '.join(items)}): {result.stderr.strip()}")
    return result.stdout.strip()


def sha256(path: pathlib.Path) -> str:
    if not path.is_file():
        raise ManifestError(f"manifest input is missing: {path}")
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def artifact(path: pathlib.Path | None) -> dict[str, str] | None:
    if path is None:
        return None
    resolved = path.resolve()
    return {"path": str(resolved), "sha256": sha256(resolved)}


def git(repo: pathlib.Path, *items: str, optional: bool = False) -> str | None:
    return command(["git", "-C", str(repo), *items], optional=optional)


def submodule_sha(repo: pathlib.Path, relative: str) -> str:
    path = repo / relative
    value = git(path, "rev-parse", "HEAD", optional=True)
    if value is None:
        raise ManifestError(f"submodule is unavailable: {relative}")
    return value


def accelerator_environment() -> dict[str, str | None]:
    gpu = command(["nvidia-smi", "--query-gpu=name,driver_version",
                   "--format=csv,noheader"], optional=True)
    gpu_model = None
    gpu_driver = None
    if gpu:
        first = gpu.splitlines()[0]
        fields = [item.strip() for item in first.split(",", maxsplit=1)]
        gpu_model = fields[0]
        if len(fields) == 2:
            gpu_driver = fields[1]
    nvcc = command(["nvcc", "--version"], optional=True)
    return {"gpu_model": gpu_model, "gpu_driver": gpu_driver,
            "cuda_version_output": nvcc}


def main() -> int:
    args = arguments()
    repo = args.repo.resolve()
    if not (repo / ".git").exists() and git(repo, "rev-parse", "--git-dir",
                                             optional=True) is None:
        raise ManifestError(f"not an HBFSim git worktree: {repo}")
    if args.thermal_mode == "package_rc":
        if (args.package_profile is None or args.thermal_model is None or
                args.model_kind is None or args.thermal_stage == "off" or
                args.thermal_clock == "none"):
            raise ManifestError(
                "package_rc requires package profile, model kind/model, stage, and clock")
    elif args.package_profile is not None or args.thermal_model is not None:
        raise ManifestError(
            "non-package modes must not inspect package profile/model paths")
    evidence_grid = None
    if args.phase2:
        required = {
            "experiment_id": args.experiment_id,
            "workload": args.workload,
            "seed": args.seed,
            "offered_byte_rate": args.offered_byte_rate,
            "arrival_mode": args.arrival_mode,
            "queue_depth": args.queue_depth,
            "read_ratio": args.read_ratio,
            "address_pattern": args.address_pattern,
            "stack_height": args.stack_height,
            "peak_byte_rate": args.peak_byte_rate,
            "unthrottled_byte_rate": args.unthrottled_byte_rate,
            "evidence_grid": args.evidence_grid,
        }
        missing = [name for name, value in required.items() if value is None]
        if missing:
            raise ManifestError(
                "--phase2 requires metadata: " + ", ".join(sorted(missing)))
        if args.thermal_mode != "package_rc" or args.thermal_stage == "off":
            raise ManifestError("Phase-II campaign requires package_rc thermal mode")
        if args.offered_byte_rate <= 0 or args.peak_byte_rate <= 0 or \
                args.unthrottled_byte_rate <= 0:
            raise ManifestError("Phase-II byte rates must be positive")
        if args.queue_depth <= 0:
            raise ManifestError("Phase-II queue depth must be positive")
        if not 0.0 <= args.read_ratio <= 1.0:
            raise ManifestError("Phase-II read ratio must be in [0, 1]")
        package_document = json.loads(args.package_profile.read_text(
            encoding="utf-8"))
        if not package_document.get("timeline", {}).get("enabled", False):
            raise ManifestError("Phase-II campaign requires timeline.enabled=true")
        evidence_grid = json.loads(args.evidence_grid.read_text(
            encoding="utf-8"))
        if not isinstance(evidence_grid, dict):
            raise ManifestError("evidence grid must be a JSON object")

    status = git(repo, "status", "--porcelain=v1") or ""
    tracked_patch = git(repo, "diff", "--binary", "HEAD") or ""
    patches = []
    for path in sorted((repo / "patches/mqsim").glob("*.patch")):
        patches.append({"path": str(path.relative_to(repo)).replace("\\", "/"),
                        "sha256": sha256(path)})
    if not patches:
        raise ManifestError("no MQSim patches found")

    manifest = {
        "schema_version": 1,
        "timestamp_utc": datetime.datetime.now(
            datetime.timezone.utc).isoformat(),
        "host": socket.gethostname(),
        "platform": platform.platform(),
        "command": args.command,
        "repository": {
            "origin": git(repo, "remote", "get-url", "origin"),
            "hbf_sim_commit": git(repo, "rev-parse", "HEAD"),
            "base_remote_sha": git(repo, "rev-parse", "origin/hybrid"),
            "branch": git(repo, "branch", "--show-current"),
            "dirty": bool(status),
            "dirty_tracked_patch_sha256": hashlib.sha256(
                tracked_patch.encode("utf-8")).hexdigest(),
            "diff_stat": git(repo, "diff", "--stat") or "",
            "untracked": [line[3:] for line in status.splitlines()
                          if line.startswith("?? ")],
            "mqsim_commit": submodule_sha(repo, "third_party/mqsim"),
            "bpftime_commit": submodule_sha(repo, "third_party/bpftime"),
            "mqsim_patches": patches,
        },
        "inputs": {
            "device_profile": artifact(args.device_profile),
            "package_profile": artifact(args.package_profile),
            "thermal_model": artifact(args.thermal_model),
            "model_kind": args.model_kind,
            "power_traces": [artifact(path) for path in args.power_trace],
        },
        "thermal": {"mode": args.thermal_mode, "stage": args.thermal_stage,
                    "clock": args.thermal_clock},
        "three_d_ice": {"version": args.three_d_ice_version,
                        "commit": args.three_d_ice_commit,
                        "boundary": "external_offline_only"},
        "accelerator": accelerator_environment(),
        "evidence": {
            "hbf_temperature": "model_based_projection",
            "warning": "simulated HBF is not measured HBF silicon",
        },
    }
    if args.phase2:
        manifest.update({
            "experiment_id": args.experiment_id,
            "thermal_stage": args.thermal_stage,
            "workload": args.workload,
            "seed": args.seed,
            "offered_byte_rate": args.offered_byte_rate,
            "peak_byte_rate": args.peak_byte_rate,
            "unthrottled_byte_rate": args.unthrottled_byte_rate,
            "stack_height": args.stack_height,
            "workload_control": {
                "arrival_mode": args.arrival_mode,
                "queue_depth": args.queue_depth,
                "read_ratio": args.read_ratio,
                "address_pattern": args.address_pattern,
            },
            "evidence_grid": evidence_grid,
            "evidence_grid_artifact": artifact(args.evidence_grid),
        })
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.output.with_name(
        f"{args.output.name}.tmp.{os.getpid()}")
    temporary.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n",
                         encoding="utf-8")
    temporary.replace(args.output)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except ManifestError as error:
        print(f"package_thermal_manifest.py: {error}", file=sys.stderr)
        raise SystemExit(2)
