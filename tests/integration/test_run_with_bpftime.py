#!/usr/bin/env python3

import os
import pathlib
import stat
import subprocess
import sys
import tempfile


def executable(path: pathlib.Path, text: str) -> None:
    path.write_text(text)
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


def main() -> int:
    wrapper = pathlib.Path(sys.argv[1]).resolve()
    with tempfile.TemporaryDirectory(prefix="hbfsim-wrapper-") as directory:
        root = pathlib.Path(directory)
        hbfsim_build = root / "hbfsim-build"
        bpftime_build = root / "bpftime-build"
        (bpftime_build / "runtime/agent").mkdir(parents=True)
        (bpftime_build / "runtime/syscall-server").mkdir(parents=True)
        hbfsim_build.mkdir()

        preload = pathlib.Path("/usr/lib/x86_64-linux-gnu/libm.so.6")
        for artifact in [
            hbfsim_build / "libhbfsim_launch_gate.so",
            hbfsim_build / "libptxpass_hbf.so",
            bpftime_build / "runtime/agent/libbpftime-agent.so",
            bpftime_build / "runtime/syscall-server/libbpftime-syscall-server.so",
        ]:
            artifact.symlink_to(preload)

        probe = root / "probe.bpf.o"
        probe.touch()
        loader = root / "loader"
        executable(
            loader,
            "#!/usr/bin/env bash\n"
            "set -eu\n"
            "test -r \"$1\"\n"
            "printf 'HBFSIM_BPFTIME_ATTACH_READY v1\\nshm=bpftime\\nattach_type=8\\nentries=1\\n' > \"$2\"\n"
            "while :; do sleep 1; done\n",
        )
        command = root / "command"
        output = root / "environment"
        executable(
            command,
            "#!/usr/bin/env bash\n"
            "set -eu\n"
            "printf '%s\\n%s\\n%s\\n' \"$BPFTIME_PTXPASS_LIBRARIES\" \"$BPFTIME_CUDA_ROOT\" \"$LD_PRELOAD\" > \"$1\"\n",
        )

        env = os.environ.copy()
        env.update(
            {
                "HBFSIM_BUILD_DIR": str(hbfsim_build),
                "HBFSIM_BPFTIME_BUILD_DIR": str(bpftime_build),
                "HBFSIM_BPFTIME_LOADER": str(loader),
                "HBFSIM_BPFTIME_PROBE": str(probe),
                "HBFSIM_ATTACH_TIMEOUT_MS": "1000",
                "LD_PRELOAD": str(preload),
            }
        )
        completed = subprocess.run(
            [str(wrapper), "--", str(command), str(output)],
            env=env,
            text=True,
            capture_output=True,
        )
        assert completed.returncode == 0, completed.stderr
        lines = output.read_text().splitlines()
        assert lines[0] == str(hbfsim_build / "libptxpass_hbf.so")
        assert lines[1] == "/usr/local/cuda-12.8"
        assert lines[2].split(":") == [
            str(bpftime_build / "runtime/agent/libbpftime-agent.so"),
            str(hbfsim_build / "libhbfsim_launch_gate.so"),
            str(preload),
        ]

        invalid = env.copy()
        invalid["HBFSIM_BUILD_DIR"] = "relative-build"
        rejected = subprocess.run(
            [str(wrapper), "--", "/bin/true"], env=invalid,
            text=True, capture_output=True
        )
        assert rejected.returncode != 0
        assert "absolute directory" in rejected.stderr
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
