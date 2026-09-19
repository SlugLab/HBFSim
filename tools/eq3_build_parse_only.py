#!/usr/bin/env python3
"""Build only the isolated parse probe against an existing private 3D-ICE build."""
import argparse
from pathlib import Path
import subprocess


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--include", type=Path, action="append", required=True)
    parser.add_argument("--library", type=Path, action="append", required=True)
    parser.add_argument("--link-arg", action="append", default=[])
    parser.add_argument("--cc", default="cc")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("output exists; choose a new isolated build path")
    for path in [args.source, *args.library]:
        if not path.is_file():
            parser.error(f"missing input file: {path}")
    for path in args.include:
        if not path.is_dir():
            parser.error(f"missing include directory: {path}")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    command = [args.cc, "-std=gnu17", "-O2", "-Wall", "-Wextra"]
    command += [f"-I{path.resolve()}" for path in args.include]
    command += [str(args.source.resolve())]
    command += [str(path.resolve()) for path in args.library]
    command += args.link_arg
    command += ["-o", str(args.output.resolve())]
    subprocess.run(command, check=True)


if __name__ == "__main__":
    main()
