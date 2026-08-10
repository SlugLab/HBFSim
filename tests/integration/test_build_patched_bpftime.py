#!/usr/bin/env python3

import pathlib
import subprocess
import sys


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def main() -> int:
    helper = pathlib.Path(sys.argv[1]).resolve()
    bpftime = pathlib.Path(sys.argv[2]).resolve()
    before = subprocess.run(
        ["git", "-C", str(bpftime), "status", "--porcelain"],
        check=True,
        text=True,
        capture_output=True,
    ).stdout
    checked = subprocess.run(
        [str(helper), "--check"], text=True, capture_output=True
    )
    require(checked.returncode == 0,
            f"patched bpftime helper check failed: {checked.stderr}")
    require("bpftime patch applies to pinned source" in checked.stdout,
            f"helper did not report exact applicability: {checked.stdout}")
    after = subprocess.run(
        ["git", "-C", str(bpftime), "status", "--porcelain"],
        check=True,
        text=True,
        capture_output=True,
    ).stdout
    require(after == before,
            "patched bpftime helper dirtied the pinned submodule in check mode")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
