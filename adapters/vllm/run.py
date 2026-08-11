#!/usr/bin/env python3
"""Run deterministic baseline or HBF timing-backed vLLM generation."""

from __future__ import annotations

import argparse
import contextlib
import gc
import importlib.metadata
import json
import os
import pathlib
import random
import re
import subprocess
import time
from typing import Any


@contextlib.contextmanager
def suppress_static_fatbin_scan(enabled: bool):
    """Skip expensive cubin-only extraction only while importing vLLM."""
    name = "BPFTIME_CUDA_DISABLE_CUOBJDUMP"
    previous = os.environ.get(name)
    if enabled:
        os.environ[name] = "1"
    try:
        yield
    finally:
        if enabled:
            if previous is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = previous


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("baseline", "timing"), required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--tokenizer")
    parser.add_argument("--profile")
    parser.add_argument("--report-dir", required=True)
    parser.add_argument("--num-prompts", type=int, default=4)
    parser.add_argument("--input-len", type=int, default=32)
    parser.add_argument("--output-len", type=int, default=32)
    parser.add_argument("--max-model-len", type=int, default=256)
    parser.add_argument("--max-num-batched-tokens", type=int, default=256)
    parser.add_argument("--gpu-memory-utilization", type=float, default=0.85)
    parser.add_argument("--hbf-parameter-regex", default="")
    parser.add_argument("--hbf-range-bytes", type=int, default=0)
    parser.add_argument(
        "--hbf-timing-model",
        choices=("reference", "fast", "hybrid"),
        default="hybrid",
    )
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def configure_environment(report: pathlib.Path) -> pathlib.Path:
    cache = pathlib.Path(os.environ.get(
        "HBFSIM_VLLM_CACHE", "/dev/shm/hbfsim-vllm-live-cache"
    ))
    cache.mkdir(parents=True, exist_ok=True)
    report.mkdir(parents=True, exist_ok=True)
    defaults = {
        "TMPDIR": "/dev/shm",
        "VLLM_CACHE_ROOT": str(cache / "vllm"),
        "TORCHINDUCTOR_CACHE_DIR": str(cache / "torchinductor"),
        "TRITON_CACHE_DIR": str(cache / "triton"),
        "FLASHINFER_WORKSPACE_BASE": str(cache / "flashinfer"),
        "CUDA_CACHE_PATH": str(cache / "cuda"),
        "CC": "/usr/bin/gcc-13",
        "CXX": "/usr/bin/g++-13",
        "HF_HUB_OFFLINE": "1",
        "TRANSFORMERS_OFFLINE": "1",
    }
    for name, value in defaults.items():
        os.environ.setdefault(name, value)
    # bpftime initializes CUDA state in the preloaded process. Forking a V1
    # engine core after that state exists is unsupported by the CUDA runtime.
    os.environ["VLLM_ENABLE_V1_MULTIPROCESSING"] = "0"
    os.environ["VLLM_NO_USAGE_STATS"] = "1"
    # The agent is already resident in this process. Do not inject it again
    # into Triton/FlashInfer compiler subprocesses spawned by vLLM.
    if "HBFSIM_TARGET_ORIGINAL_LD_PRELOAD" in os.environ:
        os.environ["LD_PRELOAD"] = os.environ[
            "HBFSIM_TARGET_ORIGINAL_LD_PRELOAD"
        ]
    build_dir = os.environ.get("HBFSIM_BUILD_DIR")
    if build_dir:
        os.environ.setdefault(
            "HBFSIM_DAEMON_PATH", str(pathlib.Path(build_dir) / "hbfsimd")
        )
    for value in defaults.values():
        if value.startswith("/dev/shm"):
            pathlib.Path(value).mkdir(parents=True, exist_ok=True)
    return cache


def runtime_versions() -> dict[str, str]:
    packages = ("vllm", "torch", "triton", "flashinfer-python")
    result = {}
    for package in packages:
        try:
            result[package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            result[package] = "missing"
    return result


def model_vocab_size(model: pathlib.Path) -> int:
    config = json.loads((model / "config.json").read_text())
    return int(config["vocab_size"])


def deterministic_prompts(count: int, length: int, vocab: int,
                          seed: int) -> list[dict[str, list[int]]]:
    generator = random.Random(seed)
    lower = min(100, vocab - 1)
    return [
        {"prompt_token_ids": [generator.randrange(lower, vocab)
                              for _ in range(length)]}
        for _ in range(count)
    ]


def repository_commit() -> str:
    try:
        output = subprocess.run(
            ["git", "rev-parse", "HEAD"], check=True, text=True,
            capture_output=True
        ).stdout
    except Exception:
        return "unknown"
    for line in reversed(output.splitlines()):
        candidate = line.strip()
        if re.fullmatch(r"[0-9a-fA-F]{40}", candidate):
            return candidate.lower()
    return "unknown"


def base_manifest(args: argparse.Namespace, cache: pathlib.Path) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "mode": args.mode,
        "model": str(pathlib.Path(args.model).resolve()),
        "profile": args.profile,
        "num_prompts": args.num_prompts,
        "input_len": args.input_len,
        "output_len": args.output_len,
        "max_model_len": args.max_model_len,
        "max_num_batched_tokens": args.max_num_batched_tokens,
        "seed": args.seed,
        "attention_backend": "FLASHINFER",
        "cache_root": str(cache),
        "versions": runtime_versions(),
        "git_commit": repository_commit(),
        "compiler": {"CC": os.environ["CC"], "CXX": os.environ["CXX"]},
        "hbf_selection": {
            "parameter_regex": args.hbf_parameter_regex,
            "max_bytes_per_storage": args.hbf_range_bytes,
        },
        "hbf_timing_model": args.hbf_timing_model,
    }


def validate_args(args: argparse.Namespace) -> None:
    if args.mode == "timing" and not args.profile:
        raise SystemExit("--profile is required for timing mode")
    if min(args.num_prompts, args.input_len, args.output_len,
           args.max_model_len) <= 0:
        raise SystemExit("prompt counts and lengths must be positive")
    if args.max_num_batched_tokens <= 0 or args.hbf_range_bytes < 0:
        raise SystemExit(
            "max-num-batched-tokens must be positive and hbf-range-bytes "
            "must be nonnegative"
        )
    if args.input_len + args.output_len > args.max_model_len:
        raise SystemExit("input plus output exceeds max model length")


def main() -> int:
    args = parse_args()
    validate_args(args)
    report = pathlib.Path(args.report_dir).resolve()
    cache = configure_environment(report)
    manifest = base_manifest(args, cache)
    if args.dry_run:
        print(json.dumps(manifest, indent=2, sort_keys=True))
        return 0

    model_path = pathlib.Path(args.model).resolve()
    prompts = deterministic_prompts(
        args.num_prompts, args.input_len, model_vocab_size(model_path), args.seed
    )
    load_format = "safetensors"
    loader_extra = None
    triton_binder = None
    with suppress_static_fatbin_scan(enabled=args.mode == "timing"):
        if args.mode == "timing":
            import hbfsim_loader
            import triton_binding

            hbfsim_loader.register()
            triton_binder = triton_binding.install_triton_binding(
                report, required=True
            )
            load_format = "hbfsim"
            loader_extra = {
                "profile_path": str(pathlib.Path(args.profile).resolve()),
                "report_dir": str(report),
                "underlying_load_format": "safetensors",
                "require_modeled_accesses": True,
                "allow_opaque_timing": True,
                "parameter_regex": args.hbf_parameter_regex,
                "max_bytes_per_storage": args.hbf_range_bytes,
                "timing_model": args.hbf_timing_model,
            }

        from vllm import LLM, SamplingParams

    engine_args = {
        "model": str(model_path),
        "tokenizer": str(pathlib.Path(args.tokenizer or args.model).resolve()),
        "dtype": "bfloat16",
        "max_model_len": args.max_model_len,
        "max_num_batched_tokens": args.max_num_batched_tokens,
        "gpu_memory_utilization": args.gpu_memory_utilization,
        "enforce_eager": True,
        "seed": args.seed,
        "disable_log_stats": True,
        "attention_backend": "FLASHINFER",
        "load_format": load_format,
    }
    if loader_extra is not None:
        engine_args["model_loader_extra_config"] = loader_extra
    started = time.perf_counter()
    llm = LLM(**engine_args)
    loaded = time.perf_counter()
    outputs = llm.generate(
        prompts,
        SamplingParams(
            temperature=0.0,
            max_tokens=args.output_len,
            ignore_eos=True,
            seed=args.seed,
        ),
        use_tqdm=True,
    )
    finished = time.perf_counter()
    token_ids = [list(output.outputs[0].token_ids) for output in outputs]
    output_tokens = sum(len(tokens) for tokens in token_ids)
    prompt_tokens = sum(len(prompt["prompt_token_ids"]) for prompt in prompts)
    generation_seconds = finished - loaded
    manifest.update({
        "load_seconds": loaded - started,
        "generation_seconds": generation_seconds,
        "requests_per_second": args.num_prompts / generation_seconds,
        "total_tokens_per_second": (
            prompt_tokens + output_tokens
        ) / generation_seconds,
        "output_tokens_per_second": output_tokens / generation_seconds,
        "prompt_token_ids": [prompt["prompt_token_ids"] for prompt in prompts],
        "output_token_ids": token_ids,
        "triton_exact_bindings": (
            triton_binder.bound_count if triton_binder is not None else 0
        ),
    })
    output_path = report / "result.json"
    output_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    print(json.dumps(manifest, indent=2, sort_keys=True))
    del outputs
    del llm
    gc.collect()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
