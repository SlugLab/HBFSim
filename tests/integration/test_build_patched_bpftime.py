#!/usr/bin/env python3

import os
import pathlib
import shutil
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
    patches = [
        helper.parent.parent / "patches/bpftime/0001-exact-module-load-provenance.patch",
        helper.parent.parent / "patches/bpftime/0002-sm120-aot-bundle-load.patch",
        helper.parent.parent / "patches/bpftime/0003-libbpf-modern-libc-const.patch",
        helper.parent.parent / "patches/bpftime/0004-honor-llvm-aot-cli-option.patch",
        helper.parent.parent / "patches/bpftime/0005-cuda13-context-create.patch",
        helper.parent.parent / "patches/bpftime/0006-prepatched-bootstrap-once.patch",
    ]
    prepared = subprocess.run(
        [
            real_cmake,
            f"-DBPFTIME_SOURCE={bpftime}",
            f"-DPATCHES={';'.join(str(patch) for patch in patches)}",
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


def copy_test_layout(
    helper: pathlib.Path,
    bpftime: pathlib.Path,
    prepare: pathlib.Path,
    scratch: pathlib.Path,
) -> tuple[pathlib.Path, pathlib.Path, pathlib.Path]:
    root = scratch / "hbfsim"
    isolated_helper = root / "scripts/build_patched_bpftime.sh"
    isolated_prepare = root / "cmake/PreparePatchedBpftime.cmake"
    isolated_patch_dir = root / "patches/bpftime"
    isolated_bpftime = root / "third_party/bpftime"
    isolated_helper.parent.mkdir(parents=True)
    isolated_prepare.parent.mkdir(parents=True)
    isolated_patch_dir.mkdir(parents=True)
    isolated_bpftime.parent.mkdir(parents=True)
    shutil.copy2(helper, isolated_helper)
    shutil.copy2(prepare, isolated_prepare)
    for name in ("0001-exact-module-load-provenance.patch",
                 "0002-sm120-aot-bundle-load.patch",
                 "0003-libbpf-modern-libc-const.patch",
                 "0004-honor-llvm-aot-cli-option.patch",
                 "0005-cuda13-context-create.patch",
                 "0006-prepatched-bootstrap-once.patch"):
        shutil.copy2(helper.parent.parent / "patches/bpftime" / name,
                     isolated_patch_dir / name)
    revision = subprocess.run(
        ["git", "-C", str(bpftime), "rev-parse", "HEAD"],
        check=True,
        text=True,
        capture_output=True,
    ).stdout.strip()
    subprocess.run(
        [
            "git", "clone", "--quiet", "--no-local", "--no-checkout",
            "--no-recurse-submodules", str(bpftime), str(isolated_bpftime),
        ],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(isolated_bpftime), "checkout", "--quiet",
         "--detach", revision],
        check=True,
    )
    source_bpftool = bpftime / "third_party/bpftool"
    isolated_bpftool = isolated_bpftime / "third_party/bpftool"
    subprocess.run(
        [
            "git", "-c", "protocol.file.allow=always", "-c",
            f"submodule.third_party/bpftool.url={source_bpftool}",
            "-C", str(isolated_bpftime), "submodule", "update", "--init",
            "third_party/bpftool",
        ],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    subprocess.run(
        [
            "git", "-c", "protocol.file.allow=always", "-c",
            f"submodule.libbpf.url={source_bpftool / 'libbpf'}",
            "-C", str(isolated_bpftool), "submodule", "update", "--init",
            "libbpf",
        ],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    source_llvm_jit = bpftime / "vm/llvm-jit"
    subprocess.run(
        [
            "git", "-c", "protocol.file.allow=always", "-c",
            f"submodule.vm/llvm-jit.url={source_llvm_jit}",
            "-C", str(isolated_bpftime), "submodule", "update", "--init",
            "vm/llvm-jit",
        ],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    require(git_status(isolated_bpftime) == "",
            "isolated bpftime copy is not clean")
    return isolated_helper, isolated_bpftime, isolated_prepare


def run_isolated_worker(
    helper: pathlib.Path,
    bpftime: pathlib.Path,
    prepare: pathlib.Path,
) -> int:
    real_cmake = shutil.which("cmake")
    require(real_cmake is not None, "cmake is unavailable")
    with tempfile.TemporaryDirectory(prefix="hbfsim-bpftime-dirty-") as root:
        scratch = pathlib.Path(root)
        isolated_helper, isolated_bpftime, isolated_prepare = copy_test_layout(
            helper, bpftime, prepare, scratch
        )
        checked = subprocess.run(
            [str(isolated_helper), "--check"], text=True, capture_output=True
        )
        require(checked.returncode == 0,
                f"isolated helper check failed: {checked.stderr}")
        require("bpftime patch series applies to pinned source" in checked.stdout,
                f"isolated helper reported wrong result: {checked.stdout}")

        stale_build = scratch / "build-stale-stamp"
        stale_build.mkdir()
        stale_stamp = stale_build / "hbfsim-bpftime.provenance"
        stale_stamp.write_text("stale\n")
        fake_bin = scratch / "fake-bin-stale-stamp"
        fake_bin.mkdir()
        executable(fake_bin / "cmake", "#!/usr/bin/env bash\nexit 91\n")
        environment = os.environ.copy()
        environment["PATH"] = f"{fake_bin}:{environment['PATH']}"
        failed = subprocess.run(
            [str(isolated_helper), str(stale_build)], env=environment,
            text=True, capture_output=True
        )
        require(failed.returncode != 0,
                "simulated failed build unexpectedly succeeded")
        require(not stale_stamp.exists(),
                "failed build preserved a stale provenance stamp")

        tracked = isolated_bpftime / "CMakeLists.txt"
        original = tracked.read_bytes()
        untracked = isolated_bpftime / ".hbfsim-dirty-regression"

        tracked.write_bytes(original + b"\n# hbfsim dirty regression\n")
        require_dirty_refusal(
            "unstaged", isolated_helper, isolated_bpftime,
            isolated_prepare, scratch, real_cmake)
        tracked.write_bytes(original)
        require(git_status(isolated_bpftime) == "",
                "failed to restore isolated unstaged source")

        tracked.write_bytes(original + b"\n# hbfsim dirty regression\n")
        subprocess.run(
            ["git", "-C", str(isolated_bpftime), "add", "--",
             "CMakeLists.txt"],
            check=True,
        )
        require_dirty_refusal(
            "staged", isolated_helper, isolated_bpftime,
            isolated_prepare, scratch, real_cmake)
        subprocess.run(
            ["git", "-C", str(isolated_bpftime), "reset", "-q", "HEAD",
             "--", "CMakeLists.txt"],
            check=True,
        )
        tracked.write_bytes(original)
        require(git_status(isolated_bpftime) == "",
                "failed to restore isolated staged source")

        untracked.write_text("hbfsim dirty regression\n")
        require_dirty_refusal(
            "untracked", isolated_helper, isolated_bpftime,
            isolated_prepare, scratch, real_cmake)
        untracked.unlink()
        require(git_status(isolated_bpftime) == "",
                "failed to remove isolated untracked source")
    return 0


def main() -> int:
    if sys.argv[1] == "--isolated-worker":
        return run_isolated_worker(
            pathlib.Path(sys.argv[2]).resolve(),
            pathlib.Path(sys.argv[3]).resolve(),
            pathlib.Path(sys.argv[4]).resolve(),
        )

    helper = pathlib.Path(sys.argv[1]).resolve()
    bpftime = pathlib.Path(sys.argv[2]).resolve()
    prepare = pathlib.Path(sys.argv[3]).resolve()
    before = git_status(bpftime)
    require(before == "",
            "bpftime must be clean before the isolated dirty regression")
    require("0003-libbpf-modern-libc-const.patch" in helper.read_text(),
            "build helper omitted the pinned host-compiler compatibility patch")
    require("0004-honor-llvm-aot-cli-option.patch" in helper.read_text(),
            "build helper omitted the LLVM AOT CLI option patch")
    require("0005-cuda13-context-create.patch" in helper.read_text(),
            "build helper omitted the CUDA 13 context API patch")
    require("0006-prepatched-bootstrap-once.patch" in helper.read_text(),
            "build helper omitted the exact bootstrap scalability patch")
    helper_text = helper.read_text()
    stamp_assignment = helper_text.find(
        'stamp="$build_dir/hbfsim-bpftime.provenance"')
    stamp_removal = helper_text.find('rm -f -- "$stamp"')
    configure = helper_text.find('cmake -S "$source_copy"')
    require(0 <= stamp_assignment < stamp_removal < configure,
            "build helper can preserve a stale provenance stamp")
    require("HBFSIM_BPF_CLANG" in helper_text and
            "CLANG=\"$bpf_clang\"" in helper_text,
            "build helper does not pin bpftool's BPF compiler")
    require("HBFSIM_BPF_LLVM_STRIP" in helper_text and
            "LLVM_STRIP=\"$bpf_llvm_strip\"" in helper_text,
            "build helper does not pin bpftool's LLVM strip tool")
    require("HBFSIM_LLVM_DIR" in helper_text and
            "-DLLVM_DIR=\"$llvm_dir\"" in helper_text,
            "build helper does not pin the LLVM package used by llvmbpf")
    require("bpf_tool_bin=$(dirname" in helper_text and
            "PATH=\"$bpf_tool_bin:$PATH\"" in helper_text,
            "build helper leaves hard-coded clang commands on the caller PATH")
    checked = subprocess.run(
        [str(helper), "--check"], text=True, capture_output=True
    )
    require(checked.returncode == 0,
            f"patched bpftime helper check failed: {checked.stderr}")
    require("bpftime patch series applies to pinned source" in checked.stdout,
            f"helper did not report exact applicability: {checked.stdout}")

    command = [
        sys.executable, __file__, "--isolated-worker",
        str(helper), str(bpftime), str(prepare),
    ]
    workers = [
        subprocess.Popen(command, text=True, stdout=subprocess.PIPE,
                         stderr=subprocess.PIPE)
        for _ in range(2)
    ]
    results = [worker.communicate() for worker in workers]
    for index, (worker, (stdout, stderr)) in enumerate(zip(workers, results)):
        require(worker.returncode == 0,
                f"isolated worker {index} failed:\n{stdout}{stderr}")
    require(git_status(bpftime) == before,
            "concurrent isolated regressions dirtied the real submodule")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
