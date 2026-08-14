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
    patch1 = pathlib.Path(sys.argv[2]).resolve()
    patch2 = pathlib.Path(sys.argv[3]).resolve()
    patch3 = pathlib.Path(sys.argv[4]).resolve()
    patch4 = pathlib.Path(sys.argv[5]).resolve()
    patch5 = pathlib.Path(sys.argv[6]).resolve()
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
        validation_preload = root / "validation-preload"
        bin_dir = root / "bin"
        bin_dir.mkdir()
        executable(
            bin_dir / "python3",
            "#!/usr/bin/env bash\n"
            "set -eu\n"
            f"printf '%s\\n' \"${{LD_PRELOAD:-}}\" >> {validation_preload}\n"
            "exec /usr/bin/python3 \"$@\"\n",
        )
        executable(
            command,
            "#!/usr/bin/env bash\n"
            "set -eu\n"
            "printf '%s\\n%s\\n%s\\n%s\\n%s\\n%s\\n%s\\n' \"$BPFTIME_PTXPASS_LIBRARIES\" \"$BPFTIME_CUDA_ROOT\" \"$LD_PRELOAD\" \"${HBFSIM_EXACT_PROFILE_PATH:-}\" \"${HBFSIM_EXACT_BUNDLE_DIR:-}\" \"${BPFTIME_CUDA_LATE_PTX_DIR:-}\" \"${BPFTIME_CUDA_LATE_PTX_PREPATCHED:-}\" > \"$1\"\n"
            "printf '%s\\n' '{\"module_id\":\"ptx:test\",\"kernel\":\"k\",\"instrumented\":true}' > \"$HBFSIM_PASS_MANIFEST_PATH\"\n"
            "if [[ -n ${HBFSIM_EXACT_PROFILE_PATH:-} ]]; then\n"
            "  printf '%s\\n' '{\"allowed\":true,\"reason\":\"exact_post_run_passed\",\"module_id\":\"ptx:test\",\"kernel\":\"k\",\"modeled\":true,\"requested_fidelity\":\"exact\",\"admitted_fidelity\":\"exact\",\"aot_verified\":true,\"validation_passed\":true,\"post_run_validation_passed\":true,\"future_issued\":1,\"future_drained\":1,\"future_faults\":0,\"future_leaked\":0,\"tma_faults\":0,\"tma_leaked\":0,\"tma_stale_generations\":0,\"channel_gnic_count\":4,\"channel_gpc_count\":2,\"channel_gnic_requests\":1,\"channel_gpc_requests\":0,\"channel_saturated_requests\":0,\"channel_counter_residual_failed\":false,\"channel_migration_visible_sm_mismatch\":false,\"exact_rejection_reasons\":[]}' > \"$HBFSIM_COVERAGE_PATH\"\n"
            "else\n"
            "  printf '%s\\n' '{\"allowed\":true,\"reason\":\"allowed\",\"module_id\":\"ptx:test\",\"kernel\":\"k\",\"modeled\":true}' > \"$HBFSIM_COVERAGE_PATH\"\n"
            "fi\n",
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
                "PATH": f"{bin_dir}:{env['PATH']}",
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
        digest1 = hashlib.sha256(patch1.read_bytes()).hexdigest()
        digest2 = hashlib.sha256(patch2.read_bytes()).hexdigest()
        digest3 = hashlib.sha256(patch3.read_bytes()).hexdigest()
        digest4 = hashlib.sha256(patch4.read_bytes()).hexdigest()
        digest5 = hashlib.sha256(patch5.read_bytes()).hexdigest()
        stamp = bpftime_build / "hbfsim-bpftime.provenance"

        def write_stamp(stamp_commit: str, stamp_digest1: str,
                        stamp_digest2: str, stamp_digest3: str,
                        stamp_digest4: str, stamp_digest5: str, version: str,
                        cuda_root: str = "/usr/local/cuda-13.0",
                        cuda_release: str = "13.0") -> None:
            stamp.write_text(
                f"bpftime_commit={stamp_commit}\n"
                f"patch_0001_sha256={stamp_digest1}\n"
                f"patch_0002_sha256={stamp_digest2}\n"
                f"patch_0003_sha256={stamp_digest3}\n"
                f"patch_0004_sha256={stamp_digest4}\n"
                f"patch_0005_sha256={stamp_digest5}\n"
                f"aot_bridge_version={version}\n"
                f"cuda_root={cuda_root}\n"
                f"cuda_release={cuda_release}\n"
            )

        for (label, stamp_commit, stamp_digest1, stamp_digest2,
             stamp_digest3, stamp_digest4, stamp_digest5, version) in (
            ("commit", "0" * 40, digest1, digest2, digest3, digest4, digest5, "1"),
            ("digest1", commit, "0" * 64, digest2, digest3, digest4, digest5, "1"),
            ("digest2", commit, digest1, "0" * 64, digest3, digest4, digest5, "1"),
            ("digest3", commit, digest1, digest2, "0" * 64, digest4, digest5, "1"),
            ("digest4", commit, digest1, digest2, digest3, "0" * 64, digest5, "1"),
            ("digest5", commit, digest1, digest2, digest3, digest4, "0" * 64, "1"),
            ("version", commit, digest1, digest2, digest3, digest4, digest5, "0"),
        ):
            write_stamp(stamp_commit, stamp_digest1, stamp_digest2,
                        stamp_digest3, stamp_digest4, stamp_digest5, version)
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

        write_stamp(commit, digest1, digest2, digest3, digest4, digest5, "1")
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
        require(lines[1] == "/usr/local/cuda-13.0",
                "wrapper exported wrong CUDA root")
        require(
            lines[2].split(":") == [
                str(bpftime_build / "runtime/agent/libbpftime-agent.so"),
                str(hbfsim_build / "libhbfsim_launch_gate.so"),
                str(preload),
            ],
            f"wrapper exported wrong preload order: {lines[2]}",
        )
        require(validation_preload.read_text().splitlines()[-1] == str(preload),
                "wrapper kept the bpftime agent preloaded for artifact validation")
        require(lines[3:] == ["", "", "", ""],
                "emulation run unexpectedly exported exact AOT state")

        exact_profile = root / "exact-profile.json"
        exact_profile.write_text('{"schema_version":1}\n')
        exact_bundles = root / "exact-bundles"
        exact_bundles.mkdir()
        prepatched = root / "prepatched-ptx"
        prepatched.mkdir()
        (prepatched / ("a" * 64 + ".ptx")).write_text(
            ".version 9.0\n.target sm_120\n.address_size 64\n")
        exact_run = subprocess.run(
            [str(wrapper), "--exact-profile", str(exact_profile),
             "--exact-bundle-dir", str(exact_bundles),
             "--prepatched-ptx-dir", str(prepatched),
             "--", str(command), str(output)],
            env=env, text=True, capture_output=True,
        )
        require(exact_run.returncode == 0,
                f"wrapper rejected valid exact inputs: {exact_run.stderr}")
        exact_lines = output.read_text().splitlines()
        require(exact_lines[3:] == [str(exact_profile), str(exact_bundles),
                                    str(prepatched), "1"],
                f"wrapper exported wrong exact state: {exact_lines[3:]}")

        nonfinal_exact = root / "nonfinal-exact"
        executable(
            nonfinal_exact,
            "#!/usr/bin/env bash\n"
            "set -eu\n"
            "printf '%s\\n' '{\"module_id\":\"ptx:test\",\"kernel\":\"k\"}' > \"$HBFSIM_PASS_MANIFEST_PATH\"\n"
            "printf '%s\\n' '{\"allowed\":true,\"reason\":\"allowed\",\"modeled\":true}' > \"$HBFSIM_COVERAGE_PATH\"\n",
        )
        missing_post_run = subprocess.run(
            [str(wrapper), "--exact-profile", str(exact_profile),
             "--exact-bundle-dir", str(exact_bundles),
             "--prepatched-ptx-dir", str(prepatched),
             "--", str(nonfinal_exact)],
            env=env, text=True, capture_output=True,
        )
        require(missing_post_run.returncode == 70,
                "wrapper accepted exact mode without a post-run exact record")

        incomplete_exact = subprocess.run(
            [str(wrapper), "--exact-profile", str(exact_profile),
             "--", str(command), str(output)],
            env=env, text=True, capture_output=True,
        )
        require(incomplete_exact.returncode == 64 and
                "exact mode requires" in incomplete_exact.stderr,
                "wrapper accepted an incomplete exact configuration")

        prestaged = root / "prestaged-manifest.jsonl"
        prestaged.write_text(
            '{"module_id":"ptx:sha256:prestage","kernel":"k",'
            '"instrumented":true}\n'
        )
        coverage_only = root / "coverage-only-command"
        executable(
            coverage_only,
            "#!/usr/bin/env bash\n"
            "set -eu\n"
            "printf '%s\\n' '{\"allowed\":true,\"reason\":\"allowed\","
            "\"module_id\":\"ptx:test\",\"kernel\":\"k\","
            "\"modeled\":true}' > \"$HBFSIM_COVERAGE_PATH\"\n",
        )
        prestaged_env = env.copy()
        prestaged_env.update({
            "HBFSIM_PRESTAGED_PASS_MANIFEST_PATH": str(prestaged),
            "HBFSIM_PASS_MANIFEST_PATH": str(root / "copied-manifest.jsonl"),
            "HBFSIM_COVERAGE_PATH": str(root / "prestaged-coverage.jsonl"),
        })
        prestaged_run = subprocess.run(
            [str(wrapper), "--", str(coverage_only)], env=prestaged_env,
            text=True, capture_output=True,
        )
        require(prestaged_run.returncode == 0,
                f"wrapper rejected prestaged pass manifest: "
                f"{prestaged_run.stderr}")
        require(
            pathlib.Path(prestaged_env["HBFSIM_PASS_MANIFEST_PATH"])
            .read_text() == prestaged.read_text(),
            "wrapper did not preserve the prestaged pass manifest",
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
