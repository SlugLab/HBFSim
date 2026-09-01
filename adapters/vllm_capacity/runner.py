#!/usr/bin/env python3
"""Capture deterministic Qwen3-30B-A3B routed-expert traces with vLLM."""

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

from model_inventory import ModelInventory
from routed_capture_compat import install_vllm_routed_experts_deferred_binding
from trace_collector import JsonlTraceCollector, TraceRequest


def canonical_sha256(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
    ).hexdigest()


def deterministic_prompts(
    count: int, length: int, vocab_size: int, seed: int
) -> list[list[int]]:
    generator = random.Random(seed)
    lower = min(100, vocab_size - 1)
    return [
        [generator.randrange(lower, vocab_size) for _ in range(length)]
        for _ in range(count)
    ]


def git_head(repository: pathlib.Path) -> str:
    return subprocess.run(
        ["git", "-C", str(repository), "rev-parse", "HEAD"],
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    ).stdout.strip()


def package_versions() -> dict[str, str]:
    result: dict[str, str] = {}
    for name in ("vllm", "torch", "triton", "flashinfer-python", "transformers"):
        try:
            result[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            result[name] = "missing"
    return result


def require_write_boundary(path: pathlib.Path) -> pathlib.Path:
    resolved = path.resolve()
    boundary = pathlib.Path("/root/hbfsim-exp").resolve()
    if resolved != boundary and boundary not in resolved.parents:
        raise ValueError(f"write path escapes /root/hbfsim-exp: {resolved}")
    return resolved


def metric_value(metrics: object, name: str) -> float | None:
    value = getattr(metrics, name, None)
    return float(value) if isinstance(value, (float, int)) else None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", required=True, type=pathlib.Path)
    parser.add_argument("--model-manifest", required=True, type=pathlib.Path)
    parser.add_argument("--repository", required=True, type=pathlib.Path)
    parser.add_argument("--output-dir", required=True, type=pathlib.Path)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--environment-fingerprint", required=True)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--num-prompts", type=int, default=4)
    parser.add_argument("--input-len", type=int, default=32)
    parser.add_argument("--output-len", type=int, default=32)
    parser.add_argument("--max-model-len", type=int, default=256)
    parser.add_argument("--gpu-memory-utilization", type=float, default=0.85)
    args = parser.parse_args()

    args.model = args.model.resolve(strict=True)
    args.model_manifest = args.model_manifest.resolve(strict=True)
    args.repository = args.repository.resolve(strict=True)
    args.output_dir = require_write_boundary(args.output_dir)
    if args.output_dir.exists():
        raise FileExistsError(args.output_dir)
    args.output_dir.mkdir(parents=True)
    if args.input_len + args.output_len > args.max_model_len:
        raise ValueError("input plus output exceeds max model length")
    if not 0 < args.gpu_memory_utilization <= 1:
        raise ValueError("gpu memory utilization must be in (0, 1]")

    required_environment = (
        "TMPDIR",
        "VLLM_CACHE_ROOT",
        "TORCHINDUCTOR_CACHE_DIR",
        "TRITON_CACHE_DIR",
        "FLASHINFER_WORKSPACE_BASE",
        "CUDA_CACHE_PATH",
    )
    for name in required_environment:
        if name not in os.environ:
            raise RuntimeError(f"missing isolated cache variable: {name}")
        require_write_boundary(pathlib.Path(os.environ[name])).mkdir(
            parents=True, exist_ok=True
        )
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

    install_vllm_routed_experts_deferred_binding()

    inventory = ModelInventory(args.model_manifest)
    config = json.loads((args.model / "config.json").read_text(encoding="utf-8"))
    prompt_ids = deterministic_prompts(
        args.num_prompts, args.input_len, int(config["vocab_size"]), args.seed
    )
    commit = git_head(args.repository)
    protocol = {
        "model_fingerprint": inventory.model_fingerprint,
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
        "enable_return_routed_experts": True,
        "routed_experts_capture_binding": "deferred-until-buffer-init",
        "readahead_pages": 0,
        "gpu_memory_utilization": args.gpu_memory_utilization,
    }

    from vllm import LLM, SamplingParams

    load_started = time.perf_counter()
    llm = LLM(
        model=str(args.model),
        tokenizer=str(args.model),
        dtype="bfloat16",
        max_model_len=args.max_model_len,
        max_num_batched_tokens=args.max_model_len,
        max_num_seqs=1,
        gpu_memory_utilization=args.gpu_memory_utilization,
        enforce_eager=True,
        seed=args.seed,
        disable_log_stats=True,
        attention_backend="FLASHINFER",
        load_format="safetensors",
        trust_remote_code=False,
        enable_prefix_caching=False,
        enable_return_routed_experts=True,
    )
    loaded = time.perf_counter()
    warmup = llm.generate(
        [{"prompt_token_ids": prompt_ids[0]}],
        SamplingParams(
            temperature=0.0, max_tokens=1, ignore_eos=True, seed=args.seed
        ),
        use_tqdm=False,
    )
    warmed = time.perf_counter()

    trace_path = args.output_dir / "routes.jsonl"
    request_results: list[dict[str, Any]] = []
    steady_started = time.perf_counter()
    with JsonlTraceCollector(
        trace_path,
        inventory,
        args.run_id,
        args.environment_fingerprint,
        commit,
    ) as collector:
        for prompt_id, tokens in enumerate(prompt_ids):
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
            request = outputs[0]
            completion = request.outputs[0]
            actual_prompt = list(request.prompt_token_ids)
            output_ids = list(completion.token_ids)
            if actual_prompt != tokens or len(output_ids) != args.output_len:
                raise RuntimeError("deterministic request contract changed")
            routed = completion.routed_experts
            if routed is None:
                raise RuntimeError("vLLM returned no routed experts")
            collector.emit_request(
                TraceRequest(
                    request_id=str(request.request_id),
                    sequence_id=0,
                    prompt_id=prompt_id,
                    input_len=args.input_len,
                    output_len=args.output_len,
                ),
                routed,
            )
            metrics = getattr(request, "metrics", None)
            request_results.append(
                {
                    "request_id": str(request.request_id),
                    "prompt_id": prompt_id,
                    "prompt_token_ids": actual_prompt,
                    "output_token_ids": output_ids,
                    "routed_experts_shape": list(routed.shape),
                    "wall_seconds": request_finished - request_started,
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
        summary = collector.summary()

    import torch

    device = torch.cuda.get_device_properties(0)
    result = {
        "schema_version": 1,
        "status": "PASS",
        "evidence_class": "REAL_QWEN_TRACE",
        "model": str(args.model),
        "model_fingerprint": inventory.model_fingerprint,
        "environment_fingerprint": args.environment_fingerprint,
        "git_commit": commit,
        "protocol": protocol,
        "protocol_fingerprint": canonical_sha256(protocol),
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
            "model_load_seconds": loaded - load_started,
            "triton_compile_and_warmup_seconds": warmed - loaded,
            "steady_trace_enabled_seconds": steady_finished - steady_started,
        },
        "warmup_output_token_ids": list(warmup[0].outputs[0].token_ids),
        "requests": request_results,
        "trace": summary,
    }
    (args.output_dir / "trace-summary.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({"status": "PASS", "trace": summary}, sort_keys=True))

    del warmup
    del llm
    gc.collect()
    if torch.distributed.is_initialized():
        torch.distributed.destroy_process_group()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
