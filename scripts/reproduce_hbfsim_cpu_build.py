#!/usr/bin/env python3
"""Build the two observed HBFSim CPU profiles without running a GPU workload."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import time


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def resource_check(path: Path) -> None:
    stat = os.statvfs(path)
    if stat.f_bavail / stat.f_blocks < 0.05:
        raise RuntimeError("fewer than 5% filesystem blocks available")
    values = {}
    for line in Path("/proc/meminfo").read_text().splitlines():
        key, _, value = line.partition(":")
        values[key] = int(value.strip().split()[0])
    if values["MemAvailable"] / values["MemTotal"] < 0.05:
        raise RuntimeError("fewer than 5% memory available")


def run(label: str, argv: list[str], cwd: Path, directory: Path,
        deadline: float, environment: dict[str, str], receipt: dict) -> None:
    resource_check(directory)
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError("build deadline reached before " + label)
    start = time.time_ns()
    process = subprocess.Popen(argv, cwd=cwd, env=environment, stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, start_new_session=True)
    try:
        stdout, stderr = process.communicate(timeout=remaining)
        rc = process.returncode
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGTERM)
        try:
            stdout, stderr = process.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            stdout, stderr = process.communicate()
        rc = 124
    (directory / f"{label}.stdout").write_bytes(stdout)
    (directory / f"{label}.stderr").write_bytes(stderr)
    receipt["commands"].append({"label": label, "argv": argv, "cwd": str(cwd),
        "start_ns": start, "end_ns": time.time_ns(), "returncode": rc,
        "stdout_sha256": digest(directory / f"{label}.stdout"),
        "stderr_sha256": digest(directory / f"{label}.stderr")})
    (directory / "CPU_BUILD_RECEIPT.json").write_text(json.dumps(receipt, indent=2) + "\n")
    if rc:
        raise RuntimeError(f"{label} failed with rc {rc}; raw logs retained")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--cuda-root", type=Path, required=True)
    parser.add_argument("--llvm-root", type=Path, required=True)
    parser.add_argument("--jobs", type=int, default=2)
    parser.add_argument("--deadline-seconds", type=int, default=3600)
    parser.add_argument("--aggregate-only", action="store_true")
    args = parser.parse_args()
    source = args.source.resolve(strict=True)
    output = args.output.resolve()
    cuda = args.cuda_root.resolve(strict=True)
    llvm = args.llvm_root.resolve(strict=True)
    if args.jobs < 1 or args.jobs > 2:
        parser.error("jobs must be one or two")
    if output.exists():
        parser.error("output directory already exists; use a fresh version")
    if not (source / "CMakeLists.txt").is_file():
        parser.error("source is not an HBFSim source tree")
    tools = {"cmake": Path(shutil.which("cmake") or ""),
             "ninja": Path(shutil.which("ninja") or ""),
             "g++-15": Path("/usr/bin/g++-15"),
             "g++-13": Path("/usr/bin/g++-13"),
             "nvcc": cuda / "bin/nvcc", "ptxas": cuda / "bin/ptxas",
             "clang": llvm / "bin/clang", "llvm-nm": llvm / "bin/llvm-nm",
             "llvm-objdump": llvm / "bin/llvm-objdump"}
    for name, tool in tools.items():
        if not tool.is_file():
            parser.error(f"missing {name}: {tool}")
    expected_map = b"libcudart.so.12 {\n};\nlibcudart.so.13 {\n} libcudart.so.12;\n"
    version_map = source / "cmake/cudart-versions.map"
    if version_map.read_bytes() != expected_map:
        parser.error("unexpected CUDA 12/13 gate version map")
    if not (source / "third_party/bpftime/third_party").is_dir():
        parser.error("bpftime submodule source is missing")
    if not (source / "third_party/mqsim/src").is_dir():
        parser.error("MQSim submodule source is missing")
    output.mkdir(parents=True)
    env = os.environ.copy()
    env["PATH"] = str(cuda / "bin") + os.pathsep + env.get("PATH", "")
    receipt = {"schema": "hbfsim.main_reproduction.cpu_build.v1",
               "source": str(source), "output": str(output),
               "source_head": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=source,
                                                     text=True).strip(),
               "source_dirty_paths": subprocess.check_output(["git", "status", "--short"],
                                                             cwd=source, text=True).splitlines(),
               "plugin_sha256": digest(source / "src/ptxpass_hbf/plugin.cpp"),
               "aggregate_plugin_sha256": digest(source / "src/ptxpass_hbf/plugin_aggregate_metadata.cpp"),
               "gate_sha256": digest(source / "src/cuda_runtime/launch_gate.cpp"),
               "version_map_sha256": digest(version_map),
               "tools": {name: {"path": str(path), "sha256": digest(path)}
                         for name, path in tools.items()},
               "profiles": {}, "commands": [], "artifacts": {}}
    (output / "CPU_BUILD_RECEIPT.json").write_text(json.dumps(receipt, indent=2) + "\n")
    deadline = time.monotonic() + args.deadline_seconds
    common = ["-G", "Ninja", "-S", str(source),
              "-DCMAKE_BUILD_TYPE=Release", "-DCMAKE_CXX_COMPILER=/usr/bin/g++-15",
              "-DCMAKE_CUDA_COMPILER=" + str(cuda / "bin/nvcc"),
              "-DCMAKE_CUDA_HOST_COMPILER=/usr/bin/g++-13",
              "-DCMAKE_CUDA_ARCHITECTURES=120", "-DHBFSIM_CUDA_ARCHITECTURE=120",
              "-DCUDAToolkit_ROOT=" + str(cuda), "-DHBFSIM_ENABLE_CUDA=ON",
              "-DHBFSIM_ENABLE_MQSIM=ON", "-DHBFSIM_ENABLE_LLM_TESTS=OFF",
              "-DHBFSIM_NVCC_HOST_COMPILER=/usr/bin/g++-13",
              "-DHBFSIM_PTXAS_EXECUTABLE=" + str(cuda / "bin/ptxas"),
              "-DHBFSIM_CLANG_EXECUTABLE=" + str(llvm / "bin/clang"),
              "-DHBFSIM_LLVM_OBJDUMP_EXECUTABLE=" + str(llvm / "bin/llvm-objdump"),
              "-DHBFSIM_NM_EXECUTABLE=" + str(llvm / "bin/llvm-nm"),
              "-DHBFSIM_PYTHON3_EXECUTABLE=/usr/bin/python3", "-DBUILD_TESTING=ON"]
    profiles = [
        ("runtime-futures-on", ["-DHBFSIM_ENABLE_TIMING_FUTURES=ON",
                                "-DHBFSIM_ENABLE_EVAL_TOOLS=ON",
                                "-DCUDA_CUDART:FILEPATH=" + str(cuda / "lib64/libcudart.so"),
                                "-DCUDA_cudart_LIBRARY:FILEPATH=" + str(cuda / "lib64/libcudart.so"),
                                "-DCUDA_cuda_driver_LIBRARY:FILEPATH=" + str(cuda / "targets/x86_64-linux/lib/stubs/libcuda.so"),
                                "-DCMAKE_CXX_FLAGS=-fconstexpr-loop-limit=1048576"],
         ["hbfsim", "hbfsimd", "ptxpass_hbf_plugin", "hbfsim_vllm_extension"]),
        ("gate-core-futures-off", ["-DHBFSIM_ENABLE_TIMING_FUTURES=OFF",
                                   "-DHBFSIM_ENABLE_EVAL_TOOLS=OFF",
                                   "-DCUDA_CUDART:FILEPATH=/usr/lib/x86_64-linux-gnu/libcudart.so",
                                   "-DCUDA_cudart_LIBRARY:FILEPATH=/usr/lib/x86_64-linux-gnu/libcudart.so",
                                   "-DCUDA_cuda_driver_LIBRARY:FILEPATH=/usr/lib/x86_64-linux-gnu/libcuda.so",
                                   "-DHBFSIM_GATE_TEST_HOOKS=ON",
                                   "-DHBFSIM_GATE_CUDART_VERSION_MAP=" + str(version_map)],
         ["hbfsim_launch_gate"]),
        ("aggregate-pass-futures-on-system12", ["-DHBFSIM_ENABLE_TIMING_FUTURES=ON",
                                                  "-DHBFSIM_ENABLE_EVAL_TOOLS=ON",
                                                  "-DCUDA_CUDART:FILEPATH=/usr/lib/x86_64-linux-gnu/libcudart.so",
                                                  "-DCUDA_cudart_LIBRARY:FILEPATH=/usr/lib/x86_64-linux-gnu/libcudart.so",
                                                  "-DCUDA_cuda_driver_LIBRARY:FILEPATH=/usr/lib/x86_64-linux-gnu/libcuda.so",
                                                  "-DCMAKE_CXX_FLAGS=-fconstexpr-loop-limit=1048576"],
         ["ptxpass_hbf_aggregate_plugin"]),
    ]
    profiles = profiles[2:] if args.aggregate_only else profiles[:2]
    try:
        for name, flags, targets in profiles:
            build = output / name
            receipt["profiles"][name] = {"flags": flags, "targets": targets}
            run(name + "-configure", [str(tools["cmake"]), *common,
                                      "-B", str(build), *flags], source, output, deadline, env, receipt)
            run(name + "-build", [str(tools["cmake"]), "--build", str(build),
                                  "--parallel", str(args.jobs), "--target", *targets],
                source, output, deadline, env, receipt)
        for name, relative_files in {
            "runtime-futures-on": ["libhbfsim.so", "hbfsimd", "libptxpass_hbf.so",
                                   "libhbfsim_vllm_extension.so", "libhbfsim_core.a",
                                   "libhbfsim_eval_ptx.a"],
            "gate-core-futures-off": ["libhbfsim_launch_gate.so", "libhbfsim_core.a"],
            "aggregate-pass-futures-on-system12": ["libptxpass_hbf_aggregate.so", "libhbfsim_core.a", "libhbfsim_future_emitter.a", "libhbfsim_eval_ptx.a"]
        }.items():
            for relative in relative_files:
                path = output / name / relative
                if path.is_file():
                    receipt["artifacts"][name + "/" + relative] = {
                        "bytes": path.stat().st_size, "sha256": digest(path)}
        receipt["status"] = "BUILD_PASS"
    except Exception as exc:
        receipt["status"] = "BUILD_FAILED"
        receipt["error"] = str(exc)
    receipt["end_ns"] = time.time_ns()
    (output / "CPU_BUILD_RECEIPT.json").write_text(json.dumps(receipt, indent=2) + "\n")
    return 0 if receipt["status"] == "BUILD_PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
