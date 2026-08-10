#!/usr/bin/env python3

import os
import pathlib
import stat
import subprocess
import sys
import tempfile


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def git_status(bpftime: pathlib.Path) -> str:
    return subprocess.run(
        ["git", "-C", str(bpftime), "status", "--porcelain",
         "--untracked-files=all"],
        check=True,
        text=True,
        capture_output=True,
    ).stdout


def executable(path: pathlib.Path, text: str) -> None:
    path.write_text(text)
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


def require_dirty_refusal(
    label: str,
    helper: pathlib.Path,
    bpftime: pathlib.Path,
    prepare: pathlib.Path,
    scratch: pathlib.Path,
    real_cmake: str,
) -> None:
    expected = "pinned bpftime source worktree is dirty"
    checked = subprocess.run(
        [str(helper), "--check"], text=True, capture_output=True
    )
    require(checked.returncode != 0 and expected in checked.stderr,
            f"helper accepted {label} bpftime state: {checked.stderr}")

    fake_bin = scratch / f"fake-bin-{label}"
    fake_bin.mkdir()
    cmake_started = scratch / f"cmake-started-{label}"
    executable(
        fake_bin / "cmake",
        "#!/usr/bin/env bash\n"
        "set -eu\n"
        f"touch {cmake_started}\n"
        "exit 91\n",
    )
    build_dir = scratch / f"build-{label}"
    environment = os.environ.copy()
    environment["PATH"] = f"{fake_bin}:{environment['PATH']}"
    built = subprocess.run(
        [str(helper), str(build_dir)], env=environment,
        text=True, capture_output=True
    )
    require(built.returncode != 0 and expected in built.stderr,
            f"build helper accepted {label} bpftime state: {built.stderr}")
    require(not cmake_started.exists(),
            f"build helper invoked CMake for {label} bpftime state")
    require(not (build_dir / "hbfsim-bpftime.provenance").exists(),
            f"build helper stamped {label} bpftime state")

    output_source = scratch / f"prepare-{label}" / "bpftime-hbfsim-src"
    patch = helper.parent.parent / "patches/bpftime/0001-exact-module-load-provenance.patch"
    prepared = subprocess.run(
        [
            real_cmake,
            f"-DBPFTIME_SOURCE={bpftime}",
            f"-DPATCH={patch}",
            f"-DOUTPUT_SOURCE={output_source}",
            "-P", str(prepare),
        ],
        text=True,
        capture_output=True,
    )
    require(prepared.returncode != 0 and expected in prepared.stderr,
            f"prepare script accepted {label} bpftime state: "
            f"{prepared.stderr}")
    require(not output_source.exists(),
            f"prepare script copied {label} bpftime state")


def main() -> int:
    helper = pathlib.Path(sys.argv[1]).resolve()
    bpftime = pathlib.Path(sys.argv[2]).resolve()
    prepare = pathlib.Path(sys.argv[3]).resolve()
    real_cmake = subprocess.run(
        ["bash", "-lc", "command -v cmake"], check=True, text=True,
        capture_output=True,
    ).stdout.strip()
    require(git_status(bpftime) == "",
            "bpftime must be clean before the dirty-source regression")
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

    tracked = bpftime / "CMakeLists.txt"
    original = tracked.read_bytes()
    untracked = bpftime / ".hbfsim-dirty-regression"
    require(not untracked.exists(),
            f"dirty-source regression path already exists: {untracked}")
    with tempfile.TemporaryDirectory(prefix="hbfsim-bpftime-dirty-") as root:
        scratch = pathlib.Path(root)
        try:
            tracked.write_bytes(original + b"\n# hbfsim dirty regression\n")
            require_dirty_refusal(
                "unstaged", helper, bpftime, prepare, scratch, real_cmake)
            tracked.write_bytes(original)
            require(git_status(bpftime) == "",
                    "failed to restore unstaged bpftime source")

            tracked.write_bytes(original + b"\n# hbfsim dirty regression\n")
            subprocess.run(
                ["git", "-C", str(bpftime), "add", "--", "CMakeLists.txt"],
                check=True,
            )
            require_dirty_refusal(
                "staged", helper, bpftime, prepare, scratch, real_cmake)
            subprocess.run(
                ["git", "-C", str(bpftime), "reset", "-q", "HEAD", "--",
                 "CMakeLists.txt"],
                check=True,
            )
            tracked.write_bytes(original)
            require(git_status(bpftime) == "",
                    "failed to restore staged bpftime source")

            untracked.write_text("hbfsim dirty regression\n")
            require_dirty_refusal(
                "untracked", helper, bpftime, prepare, scratch, real_cmake)
            untracked.unlink()
            require(git_status(bpftime) == "",
                    "failed to remove untracked bpftime source")
        finally:
            subprocess.run(
                ["git", "-C", str(bpftime), "reset", "-q", "HEAD", "--",
                 "CMakeLists.txt"],
                check=False,
            )
            tracked.write_bytes(original)
            untracked.unlink(missing_ok=True)
    require(git_status(bpftime) == "",
            "dirty-source regression did not restore clean bpftime source")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
