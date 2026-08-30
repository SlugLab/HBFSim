#!/usr/bin/env python3
"""Run an explicitly selected external 3D-ICE 4.0 emulator fail-closed."""

from __future__ import annotations

import argparse
import os
import pathlib
import subprocess
import sys

from _common import (OfflineError, load_json, portable_relative_path,
                     require_keys, sha256_file, write_json)


def arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Execute sweep-plan cases with external 3D-ICE-Emulator 4.0")
    parser.add_argument("--solver", type=pathlib.Path, required=True)
    parser.add_argument("--solver-manifest", type=pathlib.Path, required=True,
                        help="release/commit/binary SHA-256 provenance")
    parser.add_argument("--plan", type=pathlib.Path, required=True)
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = arguments()
    provenance = load_json(args.solver_manifest)
    require_keys(provenance,
                 {"schema_version", "project", "version", "commit",
                  "executable_sha256", "source_url"}, "solver manifest")
    if (provenance["schema_version"] != 1 or
            provenance["project"] != "esl-epfl/3d-ice" or
            provenance["version"] != "4.0"):
        raise OfflineError("solver manifest must identify esl-epfl/3d-ice 4.0")
    plan = load_json(args.plan)
    if plan.get("schema_version") != 1 or plan.get("three_d_ice_version") != "4.0":
        raise OfflineError("sweep plan is not a 3D-ICE 4.0 plan")
    root = args.plan.parent.resolve()
    commands: list[tuple[pathlib.Path, list[str], dict, pathlib.Path]] = []
    for relative in plan.get("cases", []):
        case_path = (root / portable_relative_path(
            relative, "sweep-plan case path")).resolve()
        if root not in case_path.parents:
            raise OfflineError("case path escapes sweep-plan directory")
        case = load_json(case_path)
        stack_relative = portable_relative_path(
            case.get("stack_file"), f"stack path for {case_path}")
        stack = (case_path.parent / stack_relative).resolve()
        if case_path.parent not in stack.parents or not stack.is_file():
            raise OfflineError(f"invalid stack file for {case_path}")
        command_path = stack.relative_to(case_path.parent).as_posix()
        commands.append((case_path.parent,
                         [str(args.solver.resolve()), command_path], case, stack))
    if not commands:
        raise OfflineError("sweep plan contains no cases")
    if args.dry_run:
        for cwd, command, _, _ in commands:
            print(f"cwd={cwd} command={command}")
        return 0
    if not args.solver.is_file() or not os.access(args.solver, os.X_OK):
        raise OfflineError(f"3D-ICE executable is missing or not executable: {args.solver}")
    actual_hash = sha256_file(args.solver)
    if actual_hash != provenance["executable_sha256"]:
        raise OfflineError("3D-ICE executable SHA-256 does not match solver manifest")
    runs = []
    for cwd, command, case, stack in commands:
        completed = subprocess.run(command, cwd=cwd, text=True,
                                   stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, check=False)
        (cwd / "3dice.stdout").write_text(completed.stdout, encoding="utf-8")
        (cwd / "3dice.stderr").write_text(completed.stderr, encoding="utf-8")
        if completed.returncode != 0:
            raise OfflineError(
                f"3D-ICE failed for {case['trace_id']} with status {completed.returncode}")
        runs.append({"trace_id": case["trace_id"], "returncode": 0,
                     "stack_sha256": sha256_file(stack)})
    write_json(root / "3dice-run-manifest.json",
               {"schema_version": 1, "solver": provenance,
                "solver_manifest_sha256": sha256_file(args.solver_manifest),
                "runs": runs})
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except OfflineError as error:
        print(f"run_3dice_sweeps.py: {error}", file=sys.stderr)
        raise SystemExit(2)
