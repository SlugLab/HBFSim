"""Build frozen v7 lifter in a fresh path and assemble its QKV PTX, CPU only."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import resource
import shutil
import signal
import subprocess
import time


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def checked(path: str, expected: str, keep_tool_name: bool = False) -> Path:
    declared = Path(path).absolute()
    candidate = declared.resolve(strict=True)
    if len(expected) != 64 or digest(candidate) != expected:
        raise RuntimeError(f"source/tool/input identity mismatch: {candidate}")
    # Rustup uses the invoked basename to distinguish cargo from rustc.
    return declared if keep_tool_name else candidate


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    profile_path = args.profile.resolve(strict=True)
    profile = json.loads(profile_path.read_text(encoding="utf-8"))
    if profile.get("schema") != "hbfsim.qkv_v7_cpu_source_profile.v1":
        raise RuntimeError("unsupported source profile")
    output = args.output.resolve(strict=False)
    if output.exists():
        raise RuntimeError("output must be a fresh path")
    output.parent.resolve(strict=True)
    source = Path(profile["source_root"]).resolve(strict=True)
    source_files = {}
    for name, expected in profile["source_sha256"].items():
        if name.startswith("/") or ".." in Path(name).parts:
            raise RuntimeError("unsafe source relative path")
        source_files[name] = checked(str(source / name), expected)
    licenses = {name: checked(item["path"], item["sha256"])
                for name, item in profile["licenses"].items()}
    sass = checked(profile["sass"]["path"], profile["sass"]["sha256"])
    cargo = checked(profile["tools"]["cargo"]["path"],
                    profile["tools"]["cargo"]["sha256"], True)
    rustc = checked(profile["tools"]["rustc"]["path"],
                    profile["tools"]["rustc"]["sha256"], True)
    ptxas = checked(profile["tools"]["ptxas"]["path"],
                    profile["tools"]["ptxas"]["sha256"], True)
    for tool, path in (("cargo", cargo), ("rustc", rustc), ("ptxas", ptxas)):
        version = subprocess.check_output([str(path), "--version"], text=True)
        if profile["tools"][tool]["version_fragment"] not in version:
            raise RuntimeError(f"{tool} version mismatch")

    limit = profile["limits"]
    cpus = set(limit["cpu_affinity"])
    if not cpus or not cpus <= os.sched_getaffinity(0):
        raise RuntimeError("requested CPU is unavailable")
    memory = int(limit["address_space_bytes"])
    seconds = int(limit["total_seconds"])
    jobs = int(limit["cargo_jobs"])
    if memory <= 0 or seconds <= 0 or jobs < 1 or jobs > len(cpus):
        raise RuntimeError("invalid finite CPU build limit")

    def child_limits() -> None:
        os.sched_setaffinity(0, cpus)
        resource.setrlimit(resource.RLIMIT_AS, (memory, memory))

    output.mkdir(mode=0o700)
    crate = output / "crate"
    crate.mkdir()
    for name, source_path in source_files.items():
        destination = crate / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source_path, destination)
    for name, source_path in licenses.items():
        shutil.copyfile(source_path, crate / name)
    input_file = output / "qkv-real.sass"
    shutil.copyfile(sass, input_file)
    started = time.monotonic()
    receipt = {"schema": "hbfsim.qkv_v7_fresh_cpu.v1", "status": "STARTED",
               "profile_sha256": digest(profile_path),
               "source_lifter_sha256": digest(crate / "src/sass/lifter.rs"),
               "cargo_lock_sha256": digest(crate / "Cargo.lock"),
               "rustc_sha256": digest(rustc),
               "sass_sha256": digest(input_file), "steps": {}}

    def run(label: str, argv: list[str], expected_rc: int = 0) -> None:
        remaining = seconds - (time.monotonic() - started)
        if remaining <= 0:
            raise TimeoutError("CPU reproduction deadline exhausted")
        (output / f"{label}.argv.json").write_text(json.dumps(argv, indent=2) + "\n")
        with (output / f"{label}.stdout").open("xb") as stdout, \
                (output / f"{label}.stderr").open("xb") as stderr:
            proc = subprocess.Popen(
                argv, cwd=output, stdout=stdout, stderr=stderr,
                start_new_session=True, preexec_fn=child_limits,
                env={**os.environ, "CUDA_VISIBLE_DEVICES": "",
                     "RUSTC": str(rustc),
                     "CARGO_TARGET_DIR": str(output / "target")})
            try:
                rc = proc.wait(timeout=remaining)
            except subprocess.TimeoutExpired:
                os.killpg(proc.pid, signal.SIGTERM)
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    os.killpg(proc.pid, signal.SIGKILL)
                    proc.wait(timeout=5)
                raise TimeoutError(f"{label} exceeded CPU reproduction deadline")
        receipt["steps"][label] = rc
        (output / f"{label}.rc").write_text(f"{rc}\n")
        if rc != expected_rc:
            raise RuntimeError(f"{label} returned {rc}, expected {expected_rc}")

    try:
        run("cargo_build", [str(cargo), "build", "--locked", "--offline",
                            "--jobs", str(jobs), "--manifest-path",
                            str(crate / "Cargo.toml")])
        binary = output / "target/debug/hbfsim_sass_recovery_diagnostic"
        fixture = output / "target/debug/output_repro"
        run("output_repro", [str(fixture)])
        quarantine = output / "qkv.quarantine.ptx"
        candidate = output / "qkv.candidate.ptx"
        diagnostics = output / "qkv.diagnostics.json"
        run("qkv_recovery_strict", [str(binary), "--input", str(input_file),
                                    "--kernel", profile["kernel"], "--sm",
                                    profile["sm"], "--ptx-out", str(candidate),
                                    "--diagnostics-out", str(diagnostics),
                                    "--quarantine-out", str(quarantine)], 1)
        record = json.loads(diagnostics.read_text())
        if (record.get("status") != "REJECTED" or candidate.exists() or
                not quarantine.is_file() or
                not any(x.get("code") == "REQUIRES_SEMANTIC_PROOF"
                        for x in record.get("reasons", []))):
            raise RuntimeError("strict QKV diagnostic/quarantine contract changed")
        assembled = output / "qkv-assembled.cubin"
        run("ptxas", [str(ptxas), "-arch=" + profile["sm"],
                      str(quarantine), "-o", str(assembled)])
        receipt["artifacts_sha256"] = {
            "ptx": digest(quarantine), "cubin": digest(assembled),
            "diagnostics": digest(diagnostics), "binary": digest(binary)}
        receipt["status"] = "CPU_BUILD_AND_ASSEMBLY_PASS_NOT_GPU_VALIDATED"
    except Exception as exc:
        receipt["status"] = "FAILED"
        receipt["error"] = f"{type(exc).__name__}: {exc}"
        raise
    finally:
        receipt["elapsed_seconds"] = time.monotonic() - started
        (output / "RECEIPT.json").write_text(json.dumps(receipt, indent=2,
                                                         sort_keys=True) + "\n")


if __name__ == "__main__":
    main()
