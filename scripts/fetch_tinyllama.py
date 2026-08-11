#!/usr/bin/env python3
"""Fetch the pinned TinyLlama checkpoint and convert it to F16 GGUF."""

import argparse
import pathlib
import subprocess

MODEL = "TinyLlama/TinyLlama-1.1B-Chat-v1.0"
REVISION = "fe8a4ea1ffedaf415f4da2f062534de366a451e6"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--llama-source", type=pathlib.Path, required=True)
    parser.add_argument("--output-dir", type=pathlib.Path, required=True)
    parser.add_argument("--hf", default="hf")
    args = parser.parse_args()
    checkpoint = args.output_dir / "TinyLlama-1.1B-Chat-v1.0"
    checkpoint.mkdir(parents=True, exist_ok=True)
    subprocess.run([
        args.hf, "download", MODEL, "--revision", REVISION,
        "--local-dir", str(checkpoint), "--include", "*.json", "*.model",
        "*.safetensors",
    ], check=True)
    output = args.output_dir / "tinyllama-1.1b-chat-v1.0-f16.gguf"
    subprocess.run([
        "python3", str(args.llama_source / "convert_hf_to_gguf.py"),
        str(checkpoint), "--outfile", str(output), "--outtype", "f16",
    ], check=True)
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
