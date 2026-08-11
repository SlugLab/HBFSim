#!/usr/bin/env python3

import hashlib
import os
import pathlib
import stat
import subprocess
import sys
import tempfile


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def executable(path: pathlib.Path, text: str) -> None:
    path.write_text(text)
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


def main() -> int:
    wrapper = pathlib.Path(sys.argv[1]).resolve()
    patch = pathlib.Path(sys.argv[2]).resolve()
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
        loader_started = root / "loader-started"
        executable(
            loader,
            "#!/usr/bin/env bash\n"
            "set -eu\n"
            "test -r \"$1\"\n"
            f"touch {loader_started}\n"
            "printf 'HBFSIM_BPFTIME_ATTACH_READY v1\\nshm=bpftime\\nattach_type=8\\nentries=1\\n' > \"$2\"\n"
            "exec /bin/sleep 30\n",
        )
        command = root / "command"
        output = root / "environment"
        executable(
            command,
            "#!/usr/bin/env bash\n"
            "set -eu\n"
            "printf '%s\\n%s\\n%s\\n' \"$BPFTIME_PTXPASS_LIBRARIES\" \"$BPFTIME_CUDA_ROOT\" \"$LD_PRELOAD\" > \"$1\"\n"
            "printf '%s\\n' '{\"module_id\":\"ptx:test\",\"kernel\":\"k\",\"instrumented\":true}' > \"$HBFSIM_PASS_MANIFEST_PATH\"\n"
            "printf '%s\\n' '{\"allowed\":true,\"reason\":\"allowed\",\"module_id\":\"ptx:test\",\"kernel\":\"k\",\"modeled\":true}' > \"$HBFSIM_COVERAGE_PATH\"\n",
        )

        env = os.environ.copy()
        env.update(
            {
                "HBFSIM_BUILD_DIR": str(hbfsim_build),
                "HBFSIM_BPFTIME_BUILD_DIR": str(bpftime_build),
                "HBFSIM_BPFTIME_LOADER": str(loader),
                "HBFSIM_BPFTIME_PROBE": str(probe),
                "HBFSIM_ATTACH_TIMEOUT_MS": "3000",
                "HBFSIM_PASS_MANIFEST_PATH": str(root / "manifest.jsonl"),
                "HBFSIM_COVERAGE_PATH": str(root / "coverage.json"),
                "LD_PRELOAD": str(preload),
            }
        )
        missing_stamp = subprocess.run(
            [str(wrapper), "--", str(command), str(output)],
            env=env,
            text=True,
            capture_output=True,
        )
        require(missing_stamp.returncode == 66 and
                "bpftime build provenance is missing" in missing_stamp.stderr,
                f"wrapper accepted missing provenance: {missing_stamp.stderr}")
        require(not loader_started.exists(),
                "wrapper started loader before checking missing provenance")

        commit = "ec26daecc8e787fb80fd95dd596a576404a5e36e"
        digest = hashlib.sha256(patch.read_bytes()).hexdigest()
        stamp = bpftime_build / "hbfsim-bpftime.provenance"

        def write_stamp(stamp_commit: str, stamp_digest: str,
                        version: str) -> None:
            stamp.write_text(
                f"bpftime_commit={stamp_commit}\n"
                f"patch_sha256={stamp_digest}\n"
                f"bridge_version={version}\n"
            )

        for label, stamp_commit, stamp_digest, version in (
            ("commit", "0" * 40, digest, "1"),
            ("digest", commit, "0" * 64, "1"),
            ("version", commit, digest, "2"),
        ):
            write_stamp(stamp_commit, stamp_digest, version)
            rejected_stamp = subprocess.run(
                [str(wrapper), "--", str(command), str(output)],
                env=env,
                text=True,
                capture_output=True,
            )
            require(rejected_stamp.returncode == 66 and
                    "bpftime build provenance mismatch" in rejected_stamp.stderr,
                    f"wrapper accepted wrong {label}: {rejected_stamp.stderr}")
            require(not loader_started.exists(),
                    f"wrapper started loader before rejecting wrong {label}")

        write_stamp(commit, digest, "1")
        completed = subprocess.run(
            [str(wrapper), "--", str(command), str(output)],
            env=env,
            text=True,
            capture_output=True,
        )
        require(completed.returncode == 0,
                f"wrapper failed: {completed.stderr}")
        lines = output.read_text().splitlines()
        require(lines[0] == str(hbfsim_build / "libptxpass_hbf.so"),
                "wrapper exported wrong PTX pass library")
        require(lines[1] == "/usr/local/cuda-12.8",
                "wrapper exported wrong CUDA root")
        require(
            lines[2].split(":") == [
                str(bpftime_build / "runtime/agent/libbpftime-agent.so"),
                str(hbfsim_build / "libhbfsim_launch_gate.so"),
                str(preload),
            ],
            f"wrapper exported wrong preload order: {lines[2]}",
        )

        default_timeout = env.copy()
        default_timeout.pop("HBFSIM_ATTACH_TIMEOUT_MS")
        completed_with_default = subprocess.run(
            [str(wrapper), "--", str(command), str(output)],
            env=default_timeout,
            text=True,
            capture_output=True,
        )
        require(
            completed_with_default.returncode == 0,
            "wrapper failed with its default attach timeout: "
            f"{completed_with_default.stderr}",
        )

        no_activation = env.copy()
        no_activation["HBFSIM_PASS_MANIFEST_PATH"] = str(root / "missing-manifest.jsonl")
        no_activation["HBFSIM_COVERAGE_PATH"] = str(root / "missing-coverage.json")
        inactive = subprocess.run(
            [str(wrapper), "--", "/bin/true"], env=no_activation,
            text=True, capture_output=True
        )
        require(inactive.returncode != 0,
                "wrapper accepted a run without activation artifacts")
        require(
            "target produced no valid instrumentation activation artifacts"
            in inactive.stderr,
            f"wrapper reported wrong activation failure: {inactive.stderr}",
        )

        opaque_command = root / "opaque-command"
        executable(
            opaque_command,
            "#!/usr/bin/env bash\n"
            "set -eu\n"
            "printf '%s\\n' '{\"module_id\":\"cubin:test\",\"kernel\":\"k\",\"instrumented\":false}' > \"$HBFSIM_PASS_MANIFEST_PATH\"\n"
            "printf '%s\\n' '{\"allowed\":true,\"reason\":\"opaque_unmodeled_timing\",\"module_id\":\"cubin:test\",\"kernel\":\"k\",\"modeled\":false}' > \"$HBFSIM_COVERAGE_PATH\"\n",
        )
        opaque = subprocess.run(
            [str(wrapper), "--", str(opaque_command)], env=env,
            text=True, capture_output=True
        )
        require(opaque.returncode == 70,
                "wrapper accepted opaque-only timing as modeled access")

        invalid = env.copy()
        invalid["HBFSIM_BUILD_DIR"] = "relative-build"
        rejected = subprocess.run(
            [str(wrapper), "--", "/bin/true"], env=invalid,
            text=True, capture_output=True
        )
        require(rejected.returncode != 0,
                "wrapper accepted a relative build directory")
        require("absolute directory" in rejected.stderr,
                f"wrapper reported wrong path failure: {rejected.stderr}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
