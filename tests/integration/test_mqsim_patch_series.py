#!/usr/bin/env python3
"""Prove the ordered MQSim patch series applies to a pinned clean copy."""

from __future__ import annotations

import pathlib
import shutil
import subprocess
import sys
import tempfile


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def main() -> int:
    require(len(sys.argv) == 5, "expected MQSim source and three ordered patches")
    source = pathlib.Path(sys.argv[1]).resolve()
    patches = [pathlib.Path(item).resolve() for item in sys.argv[2:]]
    require((source / ".git").exists(), "MQSim submodule is not initialized")
    require(all(path.is_file() for path in patches), "MQSim patch is missing")
    require([path.name for path in patches] == [
        "0001-online-hbf-api.patch", "0002-qlc-support.patch",
        "0003-physical-media-observer.patch"], "MQSim patch order changed")
    with tempfile.TemporaryDirectory(prefix="hbfsim-mqsim-patches-") as directory:
        copy = pathlib.Path(directory) / "mqsim"
        shutil.copytree(source, copy, ignore=shutil.ignore_patterns(
            ".git", "build", "MQSim"))
        for patch in patches:
            for check_only in (True, False):
                command = ["git", "-C", str(copy), "apply", "--unsafe-paths"]
                if check_only:
                    command.append("--check")
                command.append(str(patch))
                result = subprocess.run(command, text=True,
                                        stdout=subprocess.PIPE,
                                        stderr=subprocess.PIPE, check=False)
                require(result.returncode == 0,
                        f"{patch.name} failed to "
                        f"{'check' if check_only else 'apply'}: {result.stderr}")
        observer = patches[-1].read_text(encoding="utf-8")
        for token in ("Media_Activity_Observer", "struct Media_Activity",
                      "command_start_time", "Expected_finish_time",
                      "ChannelID", "ChipID", "DieID", "PlaneID", "BlockID",
                      "PageID", "Bytes", "Media_Transaction_Source"):
            require(token in observer,
                    f"observer patch lacks required contract token: {token}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
