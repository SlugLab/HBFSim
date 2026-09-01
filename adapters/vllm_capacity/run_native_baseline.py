#!/usr/bin/env python3
"""Run the frozen native Qwen3 MoE smoke protocol with split timings."""

from __future__ import annotations

import argparse
import gc
import hashlib
import importlib.metadata
import json
import os
import pathlib
import random
import subprocess
import time
from typing import Any


def canonical_sha256(value: object) -> str:
    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def package_versions() -> dict[str, str]:
    result: dict[str, str] = {}
    for name in (
        "vllm",
        "torch",
        "triton",
        "flashinfer-python",
        "transformers",
        "tokenizers",
        "safetensors",
    ):
        try:
            result[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            result[name] = "missing"
    return result


def git_head(repository: pathlib.Path) -> str:
    completed = subprocess.run(
        ["git", "-C", str(repository), "rev-parse", "HEAD"],
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    return completed.stdout.strip()


def deterministic_prompts(
    count: int, length: int, vocab_size: int, seed: int
) -> list[list[int]]:
    generator = random.Random(seed)
    lower = min(100, vocab_size - 1)
    return [
        [generator.randrange(lower, vocab_size) for _ in range(length)]
        for _ in range(count)
    ]


def metric_value(metrics: object, name: str) -> float | None:
    value = getattr(metrics, name, None)
    if isinstance(value, (float, int)):
        return float(value)
    return None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", required=True, type=pathlib.Path)
    parser.add_argument("--repository", required=True, type=pathlib.Path)
    parser.add_argument("--output", required=True, type=pathlib.Path)
    parser.add_argument("--model-fingerprint", required=True)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--num-prompts", type=int, default=4)
    parser.add_argument("--input-len", type=int, default=32)
    parser.add_argument("--output-len", type=int, default=32)
    parser.add_argument("--max-model-len", type=int, default=256)
    args = parser.parse_args()

    args.model = args.model.resolve(strict=True)
    args.repository = args.repository.resolve(strict=True)
    args.output = args.output.resolve()
    if args.output.exists():
        raise FileExistsError(args.output)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    if args.input_len + args.output_len > args.max_model_len:
        raise ValueError("input plus output exceeds max model length")

    required_environment = (
        "TMPDIR",
        "VLLM_CACHE_ROOT",
        "TORCHINDUCTOR_CACHE_DIR",
        "TRITON_CACHE_DIR",
        "FLASHINFER_WORKSPACE_BASE",
        "CUDA_CACHE_PATH",
    )
    missing = [name for name in required_environment if not os.environ.get(name)]
    if missing:
        raise RuntimeError(f"missing isolated cache variables: {missing}")
    for name in required_environment:
        path = pathlib.Path(os.environ[name]).resolve()
        if not str(path).startswith("/root/hbfsim-exp/"):
            raise RuntimeError(f"{name} escapes write boundary: {path}")
        path.mkdir(parents=True, exist_ok=True)

    os.environ.update(
        {
            "CC": "/usr/bin/gcc-13",
            "CXX": "/usr/bin/g++-13",
            "HF_HUB_OFFLINE": "1",
            "TRANSFORMERS_OFFLINE": "1",
            "VLLM_ENABLE_V1_MULTIPROCESSING": "0",
            "VLLM_NO_USAGE_STATS": "1",
            "PYTHONNOUSERSITE": "1",
        }
    )

    config = json.loads((args.model / "config.json").read_text(encoding="utf-8"))
    prompt_ids = deterministic_prompts(
        args.num_prompts, args.input_len, int(config["vocab_size"]), args.seed
    )
    protocol_inputs = {
        "model_fingerprint": args.model_fingerprint,
        "prompt_token_ids": prompt_ids,
        "num_prompts": args.num_prompts,
        "input_len": args.input_len,
        "output_len": args.output_len,
        "max_model_len": args.max_model_len,
        "max_num_batched_tokens": args.max_model_len,
        "concurrency": 1,
        "seed": args.seed,
        "temperature": 0.0,
        "ignore_eos": True,
        "runtime_dtype": "bfloat16",
        "attention_backend": "FLASHINFER",
        "moe_backend_required": "TRITON",
        "enforce_eager": True,
        "load_format": "safetensors",
        "warmup": "one unmeasured one-token request before four sequential requests",
    }

    from vllm import LLM, SamplingParams

    started = time.perf_counter()
    llm = LLM(
        model=str(args.model),
        tokenizer=str(args.model),
        dtype="bfloat16",
        max_model_len=args.max_model_len,
        max_num_batched_tokens=args.max_model_len,
        max_num_seqs=1,
        gpu_memory_utilization=0.85,
        enforce_eager=True,
        seed=args.seed,
        disable_log_stats=True,
        attention_backend="FLASHINFER",
        load_format="safetensors",
        trust_remote_code=False,
        enable_prefix_caching=False,
    )
    loaded = time.perf_counter()
    warmup_output = llm.generate(
        [{"prompt_token_ids": prompt_ids[0]}],
        SamplingParams(
            temperature=0.0,
            max_tokens=1,
            ignore_eos=True,
            seed=args.seed,
        ),
        use_tqdm=False,
    )
    warmed = time.perf_counter()

    requests: list[dict[str, Any]] = []
    steady_started = time.perf_counter()
    for prompt_index, tokens in enumerate(prompt_ids):
        request_started = time.perf_counter()
        outputs = llm.generate(
            [{"prompt_token_ids": tokens}],
            SamplingParams(
                temperature=0.0,
                max_tokens=args.output_len,
                ignore_eos=True,
                seed=args.seed,
            ),
            use_tqdm=False,
        )
        request_finished = time.perf_counter()
        output = outputs[0]
        actual_prompt = list(output.prompt_token_ids)
        actual_output = list(output.outputs[0].token_ids)
        if actual_prompt != tokens:
            raise RuntimeError(f"prompt {prompt_index} token ids changed")
        if len(actual_output) != args.output_len:
            raise RuntimeError(f"prompt {prompt_index} output length changed")
        metrics = getattr(output, "metrics", None)
        arrival = metric_value(metrics, "arrival_time")
        first = metric_value(metrics, "first_token_time")
        last = metric_value(metrics, "last_token_time")
        finished = metric_value(metrics, "finished_time")
        ttft = first - arrival if first is not None and arrival is not None else None
        end = finished if finished is not None else last
        tpot = (
            (end - first) / max(1, len(actual_output) - 1)
            if end is not None and first is not None
            else None
        )
        requests.append(
            {
                "prompt_id": prompt_index,
                "prompt_token_ids": actual_prompt,
                "output_token_ids": actual_output,
                "output_text": output.outputs[0].text,
                "wall_seconds": request_finished - request_started,
                "ttft_seconds": ttft,
                "tpot_seconds": tpot,
                "engine_metrics": {
                    name: metric_value(metrics, name)
                    for name in (
                        "arrival_time",
                        "first_token_time",
                        "last_token_time",
                        "finished_time",
                        "scheduler_time",
                        "model_forward_time",
                        "model_execute_time",
                    )
                },
            }
        )
        del outputs
    steady_finished = time.perf_counter()

    import torch

    device = torch.cuda.get_device_properties(0)
    result = {
        "schema_version": 1,
        "status": "PASS",
        "evidence_class": "REAL_GPU_MEASURED",
        "model": str(args.model),
        "model_fingerprint": args.model_fingerprint,
        "git_commit": git_head(args.repository),
        "protocol": protocol_inputs,
        "protocol_fingerprint": canonical_sha256(protocol_inputs),
        "versions": package_versions(),
        "runtime": {
            "python": os.sys.version,
            "torch_cuda": torch.version.cuda,
            "device_name": device.name,
            "compute_capability": [device.major, device.minor],
            "compiler": {"CC": os.environ["CC"], "CXX": os.environ["CXX"]},
            "cache_paths": {name: os.environ[name] for name in required_environment},
        },
        "timings": {
            "model_load_seconds": loaded - started,
            "triton_compile_and_warmup_seconds": warmed - loaded,
            "steady_all_requests_seconds": steady_finished - steady_started,
            "steady_output_tokens_per_second": (
                args.num_prompts * args.output_len
            ) / (steady_finished - steady_started),
        },
        "warmup_output_token_ids": list(warmup_output[0].outputs[0].token_ids),
        "requests": requests,
    }
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))

    del warmup_output
    del llm
    gc.collect()
    if torch.distributed.is_initialized():
        torch.distributed.destroy_process_group()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
