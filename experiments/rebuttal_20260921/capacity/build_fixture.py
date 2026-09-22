#!/usr/bin/env python3
"""Configure one isolated HBFSim build and compile the R3 payload fixture.

This only builds; it never executes a CUDA binary.
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import shlex
import subprocess


def run(command: list[str], *, cwd: pathlib.Path) -> None:
    subprocess.run(command, cwd=cwd, check=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-root", type=pathlib.Path, required=True)
    parser.add_argument("--build-dir", type=pathlib.Path, required=True)
    parser.add_argument("--cuda-root", type=pathlib.Path,
                        default=pathlib.Path("/usr/local/cuda-13.1"))
    parser.add_argument("--host-cxx", type=pathlib.Path,
                        default=pathlib.Path("/usr/bin/g++-13"))
    parser.add_argument("--cuda-overlay", type=pathlib.Path, required=True)
    parser.add_argument("--jobs", type=int, default=8)
    args = parser.parse_args()

    root = args.source_root.resolve()
    build = args.build_dir.resolve()
    here = pathlib.Path(__file__).resolve().parent
    build.mkdir(parents=True, exist_ok=True)
    overlay = args.cuda_overlay.resolve()
    if not (overlay / "crt/math_functions.h").is_file():
        raise SystemExit(f"invalid CUDA/glibc overlay: {overlay}")
    os.environ["NVCC_PREPEND_FLAGS"] = f"-I{overlay}"
    run([
        "/usr/bin/cmake", "-S", str(root), "-B", str(build),
        "-DBUILD_TESTING=ON",
        "-DHBFSIM_ENABLE_CUDA=ON",
        "-DHBFSIM_CUDA_ARCHITECTURE=120",
        "-DCMAKE_CUDA_ARCHITECTURES=120",
        f"-DCMAKE_CXX_COMPILER={args.host_cxx}",
        f"-DCMAKE_CUDA_HOST_COMPILER={args.host_cxx}",
        f"-DCMAKE_CUDA_COMPILER={args.cuda_root / 'bin/nvcc'}",
        f"-DCUDAToolkit_ROOT={args.cuda_root}",
        "-DCMAKE_EXPORT_COMPILE_COMMANDS=ON",
    ], cwd=root)
    if args.jobs < 1 or args.jobs > 32:
        raise SystemExit("--jobs must be in [1, 32]")
    run(["/usr/bin/cmake", "--build", str(build), "--target",
         "capacity_runtime_live_test", "--parallel", str(args.jobs)], cwd=root)

    commands = json.loads((build / "compile_commands.json").read_text())
    template = next(item for item in commands
                    if item["file"].endswith("capacity_runtime_live_test.cu"))
    compile_command = shlex.split(template.get("command", ""))
    if not compile_command:
        compile_command = list(template["arguments"])
    old_source = pathlib.Path(template["file"]).resolve()
    old_object = next(pathlib.Path(compile_command[i + 1])
                      for i, value in enumerate(compile_command[:-1])
                      if value == "-o")
    new_object = build / "capacity_payload_fixture.o"
    compile_command = [str(here / "capacity_payload_fixture.cu")
                       if pathlib.Path(value).resolve() == old_source else value
                       for value in compile_command]
    for index, value in enumerate(compile_command[:-1]):
        if value == "-o":
            compile_command[index + 1] = str(new_object)
            break
    run(compile_command, cwd=pathlib.Path(template["directory"]))

    link_path = build / "CMakeFiles/capacity_runtime_live_test.dir/link.txt"
    link_command = shlex.split(link_path.read_text())
    new_binary = build / "capacity_payload_fixture"
    replaced_object = False
    for index, value in enumerate(link_command):
        candidate = pathlib.Path(value)
        if value.endswith("capacity_runtime_live_test.cu.o"):
            link_command[index] = str(new_object)
            replaced_object = True
        elif value == "-o" and index + 1 < len(link_command):
            link_command[index + 1] = str(new_binary)
    if not replaced_object:
        raise RuntimeError(f"did not find fixture object in {link_path}")
    run(link_command, cwd=build)
    print(new_binary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
