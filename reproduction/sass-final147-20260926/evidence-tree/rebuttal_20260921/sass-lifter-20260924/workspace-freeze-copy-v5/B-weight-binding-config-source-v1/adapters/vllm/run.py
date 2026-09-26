#!/usr/bin/env python3
"""Run deterministic baseline or HBF timing-backed vLLM generation."""

from __future__ import annotations

import argparse
import contextlib
import ctypes
import gc
import hashlib
import importlib.metadata
import inspect
import json
import os
import pathlib
import random
import re
import shutil
import subprocess
import time
import traceback
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
    parser.add_argument("--moe-backend", choices=("triton",), default="triton")
    parser.add_argument("--hbf-parameter-regex", default="")
    parser.add_argument("--hbf-range-bytes", type=int, default=0)
    parser.add_argument(
        "--hbf-weight-selection", choices=("all", "include", "off"),
        default=None,
        help="default: all; legacy --hbf-parameter-regex resolves to include",
    )
    parser.add_argument("--hbf-include-pattern", action="append", default=[])
    parser.add_argument("--hbf-exclude-pattern", action="append", default=[])
    parser.add_argument("--hbf-accept-storage-closure", action="store_true")
    parser.add_argument(
        "--hbf-instrumentation-policy", choices=("strict", "partial"),
        default=None,
        help="default: strict; legacy regex/range configs remain partial",
    )
    parser.add_argument(
        "--hbf-timing-model",
        choices=("reference", "fast", "hybrid"),
        default="hybrid",
    )
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--warmup-requests", type=int, default=0)
    parser.add_argument("--request-timeout-ns", type=int, default=1_000_000_000)
    parser.add_argument("--request-accounting", action="store_true")
    parser.add_argument(
        "--eval-delay-ns", type=int, default=-1,
        help="enable the established per-module eval-delay control; 0 is the matched control",
    )
    parser.add_argument("--accounting-epoch", type=int, default=1)
    parser.add_argument("--eval-trace-capacity", type=int, default=1_000_000)
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
        "HF_HUB_OFFLINE": "1",
        "TRANSFORMERS_OFFLINE": "1",
        # vLLM 0.15.1 uses Triton for unquantized BF16 MoE unless a
        # FlashInfer MoE opt-in is enabled.  Keep that opt-in disabled; the
        # actual fused_moe_kernel binding receipt remains the runtime proof.
        "VLLM_USE_FLASHINFER_MOE_FP16": "0",
    }
    for name, value in defaults.items():
        os.environ.setdefault(name, value)
    cc = os.environ.get("CC") or shutil.which("gcc-13") or shutil.which("gcc")
    cxx = os.environ.get("CXX") or shutil.which("g++-13") or shutil.which("g++")
    if not cc or not cxx:
        raise RuntimeError("no existing C/C++ compiler found for runtime JIT")
    os.environ["CC"] = cc
    os.environ["CXX"] = cxx
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
        "warmup_requests": args.warmup_requests,
        "request_timeout_ns": args.request_timeout_ns,
        "request_accounting": args.request_accounting,
        "eval_delay_ns": args.eval_delay_ns,
        "accounting_epoch": args.accounting_epoch,
        "attention_backend": "FLASHINFER",
        "moe_backend": args.moe_backend,
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
    if (args.warmup_requests < 0 or args.eval_delay_ns < -1 or
            args.request_timeout_ns <= 0):
        raise SystemExit(
            "warmup-requests must be nonnegative, eval-delay-ns must be >= -1, "
            "and request-timeout-ns must be positive")
    if args.accounting_epoch <= 0 or args.eval_trace_capacity <= 0:
        raise SystemExit("accounting epoch and trace capacity must be positive")
    if (args.request_accounting or args.eval_delay_ns >= 0) and args.mode != "timing":
        raise SystemExit("request accounting and eval delay require timing mode")
    if args.eval_delay_ns >= 0 and not args.request_accounting:
        raise SystemExit("eval-delay-ns requires --request-accounting")


def resolve_weight_policy(args: argparse.Namespace) -> dict[str, Any]:
    explicit_new_selection = args.hbf_weight_selection is not None
    if args.hbf_parameter_regex and (
            explicit_new_selection or args.hbf_include_pattern):
        raise SystemExit(
            "legacy --hbf-parameter-regex cannot be combined with new "
            "weight-selection options")
    selection = args.hbf_weight_selection
    include_patterns = list(args.hbf_include_pattern)
    legacy = bool(args.hbf_parameter_regex or args.hbf_range_bytes)
    if selection is None:
        selection = "include" if args.hbf_parameter_regex else "all"
    if args.hbf_parameter_regex:
        include_patterns = [args.hbf_parameter_regex]
    if selection == "include" and not include_patterns:
        raise SystemExit("include selection requires --hbf-include-pattern")
    if selection != "include" and include_patterns:
        raise SystemExit("include patterns require selection=include")
    if selection == "off" and (
            include_patterns or args.hbf_exclude_pattern or
            args.hbf_range_bytes or args.hbf_accept_storage_closure):
        raise SystemExit("off selection cannot have binding filters or caps")
    policy = args.hbf_instrumentation_policy or (
        "partial" if legacy else "strict")
    if policy == "strict" and args.hbf_range_bytes:
        raise SystemExit("strict instrumentation forbids storage truncation")
    return {
        "selection": selection,
        "include_patterns": include_patterns,
        "exclude_patterns": list(args.hbf_exclude_pattern),
        "accept_storage_closure": args.hbf_accept_storage_closure,
        "instrumentation_policy": policy,
        "legacy": legacy,
    }


def configure_instrumentation_policy(policy: dict[str, Any]) -> None:
    if policy["selection"] == "off":
        return
    requested = policy["instrumentation_policy"]
    current = os.environ.get("HBFSIM_INSTRUMENTATION_POLICY")
    if current not in (None, "", requested):
        raise SystemExit(
            "conflicting process-global HBFSIM_INSTRUMENTATION_POLICY: "
            f"existing={current} requested={requested}")
    os.environ["HBFSIM_INSTRUMENTATION_POLICY"] = requested


def timing_loader_extra(
        args: argparse.Namespace, report: pathlib.Path,
        weight_policy: dict[str, Any]) -> dict[str, Any]:
    """Build the exact public loader configuration passed to vLLM."""
    return {
        "profile_path": str(pathlib.Path(args.profile).resolve()),
        "report_dir": str(report),
        "ring_capacity": 64,
        "request_timeout_ns": args.request_timeout_ns,
        "underlying_load_format": "safetensors",
        "require_modeled_accesses": True,
        "allow_opaque_timing": (
            weight_policy["instrumentation_policy"] == "partial"),
        "weight_selection": weight_policy["selection"],
        "include_patterns": weight_policy["include_patterns"],
        "exclude_patterns": weight_policy["exclude_patterns"],
        "accept_storage_closure": weight_policy["accept_storage_closure"],
        "instrumentation_policy": weight_policy["instrumentation_policy"],
        "max_bytes_per_storage": args.hbf_range_bytes,
        "timing_model": args.hbf_timing_model,
    }


class GateRequestAccounting:
    """Request-scoped access to the preloaded launch gate's module snapshots."""

    def __init__(self, eval_delay_ns: int, trace_capacity: int):
        self._library = ctypes.CDLL(None)
        self._eval_delay_ns = eval_delay_ns
        self._trace_capacity = trace_capacity
        self._bind()
        self._accounting_active = False
        self._eval_active = False

    def _bind(self) -> None:
        self._library.hbfsim_access_accounting_begin_v2.argtypes = [ctypes.c_uint64]
        self._library.hbfsim_access_accounting_begin_v2.restype = ctypes.c_int
        self._library.hbfsim_access_accounting_snapshot_v2.argtypes = [
            ctypes.c_char_p, ctypes.c_size_t
        ]
        self._library.hbfsim_access_accounting_snapshot_v2.restype = ctypes.c_longlong
        self._library.hbfsim_access_accounting_abort_v2.argtypes = []
        self._library.hbfsim_access_accounting_abort_v2.restype = ctypes.c_int
        if self._eval_delay_ns >= 0:
            self._library.hbfsim_eval_delay_begin_v1.argtypes = [
                ctypes.c_uint64, ctypes.c_uint64, ctypes.c_uint64
            ]
            self._library.hbfsim_eval_delay_begin_v1.restype = ctypes.c_int
            self._library.hbfsim_eval_delay_snapshot_v1.argtypes = [
                ctypes.c_char_p, ctypes.c_size_t
            ]
            self._library.hbfsim_eval_delay_snapshot_v1.restype = ctypes.c_longlong
            self._library.hbfsim_eval_delay_abort_v1.argtypes = []
            self._library.hbfsim_eval_delay_abort_v1.restype = ctypes.c_int

    @staticmethod
    def _read_json(function: Any) -> dict[str, Any]:
        required = int(function(None, 0))
        if required <= 1:
            raise RuntimeError(f"launch-gate snapshot size failed: {required}")
        buffer = ctypes.create_string_buffer(required)
        observed = int(function(buffer, required))
        if observed != required:
            raise RuntimeError(
                f"launch-gate snapshot size changed: required={required} observed={observed}"
            )
        return json.loads(buffer.value.decode())

    def begin(self, epoch: int) -> None:
        if self._eval_delay_ns >= 0:
            status = self._library.hbfsim_eval_delay_begin_v1(
                self._eval_delay_ns, epoch, self._trace_capacity
            )
            if status != 0:
                raise RuntimeError(f"eval-delay begin failed: {status}")
            self._eval_active = True
        status = self._library.hbfsim_access_accounting_begin_v2(epoch)
        if status != 0:
            if self._eval_active:
                self._library.hbfsim_eval_delay_abort_v1()
                self._eval_active = False
            raise RuntimeError(f"access-accounting begin failed: {status}")
        self._accounting_active = True

    def snapshot(self) -> dict[str, Any]:
        result: dict[str, Any] = {}
        if self._accounting_active:
            result["access"] = self._read_json(
                self._library.hbfsim_access_accounting_snapshot_v2
            )
            self._accounting_active = False
        if self._eval_active:
            result["eval_delay"] = self._read_json(
                self._library.hbfsim_eval_delay_snapshot_v1
            )
            self._eval_active = False
        return result

    def abort(self) -> None:
        if self._accounting_active:
            self._library.hbfsim_access_accounting_abort_v2()
            self._accounting_active = False
        if self._eval_active:
            self._library.hbfsim_eval_delay_abort_v1()
            self._eval_active = False


def main() -> int:
    args = parse_args()
    validate_args(args)
    weight_policy = resolve_weight_policy(args)
    if args.mode == "timing":
        configure_instrumentation_policy(weight_policy)
    report = pathlib.Path(args.report_dir).resolve()
    cache = configure_environment(report)
    manifest = base_manifest(args, cache)
    manifest["weight_binding"] = weight_policy
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
    timing_enabled = (
        args.mode == "timing" and weight_policy["selection"] != "off")
    with suppress_static_fatbin_scan(enabled=timing_enabled):
        if timing_enabled:
            import hbfsim_loader
            import triton_binding

            hbfsim_loader.register()
            triton_binder = triton_binding.install_triton_binding(
                report,
                required=(weight_policy["legacy"] or
                          weight_policy["instrumentation_policy"] == "strict"),
                required_names=(
                    ("fused_moe_kernel",) if weight_policy["legacy"] else None
                ),
            )
            load_format = "hbfsim"
            loader_extra = timing_loader_extra(args, report, weight_policy)
        elif args.mode == "timing":
            manifest["instrumentation_status"] = "DISABLED_NATIVE"

        from vllm import LLM, SamplingParams
        from vllm.engine.arg_utils import EngineArgs

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
        "enable_prefix_caching": False,
        "attention_backend": "FLASHINFER",
        "load_format": load_format,
    }
    engine_arg_parameters = inspect.signature(EngineArgs).parameters
    if "moe_backend" in engine_arg_parameters:
        engine_args["moe_backend"] = args.moe_backend
        manifest["moe_backend_control"] = "EXPLICIT_ENGINE_ARGUMENT"
    else:
        manifest["moe_backend_control"] = (
            "VLLM_0_15_DEFAULT_TRITON_RUNTIME_BINDING_REQUIRED"
        )
    if loader_extra is not None:
        engine_args["model_loader_extra_config"] = loader_extra
    started = time.perf_counter()
    llm = LLM(**engine_args)
    loaded = time.perf_counter()
    sampling = SamplingParams(
        temperature=0.0,
        max_tokens=args.output_len,
        ignore_eos=True,
        seed=args.seed,
    )
    warmup_token_ids: list[list[list[int]]] = []
    warmup_started = time.perf_counter()
    for _ in range(args.warmup_requests):
        warmup = llm.generate(prompts, sampling, use_tqdm=False)
        warmup_token_ids.append([
            list(output.outputs[0].token_ids) for output in warmup
        ])
        del warmup
    warmup_finished = time.perf_counter()

    accounting = None
    accounting_snapshot = None
    if args.request_accounting:
        accounting = GateRequestAccounting(
            args.eval_delay_ns, args.eval_trace_capacity
        )
        accounting.begin(args.accounting_epoch)
    outputs = None
    request_error = None
    request_error_traceback = None
    snapshot_error = None
    generation_started = time.perf_counter()
    try:
        outputs = llm.generate(prompts, sampling, use_tqdm=True)
    except BaseException as error:
        request_error = error
        request_error_traceback = traceback.format_exc()
    finally:
        if accounting is not None:
            try:
                accounting_snapshot = accounting.snapshot()
            except BaseException as error:
                snapshot_error = error
                accounting.abort()
    finished = time.perf_counter()
    if request_error is not None or snapshot_error is not None:
        failure = dict(manifest)
        failure.update({
            "request_terminal_status": "failed",
            "request_error": repr(request_error) if request_error else None,
            "request_traceback": request_error_traceback,
            "snapshot_error": repr(snapshot_error) if snapshot_error else None,
            "access_accounting": accounting_snapshot,
            "warmup_output_token_ids": warmup_token_ids,
            "warmup_seconds": warmup_finished - warmup_started,
            "generation_seconds_before_failure": finished - generation_started,
            "scientific_status": "FAILED_REQUEST" if request_error else "INCOMPLETE",
        })
        (report / "request_failure.json").write_text(
            json.dumps(failure, indent=2, sort_keys=True) + "\n"
        )
        if request_error is not None:
            raise request_error
        raise snapshot_error
    assert outputs is not None
    token_ids = [list(output.outputs[0].token_ids) for output in outputs]
    token_payload = json.dumps(token_ids, separators=(",", ":")).encode()
    output_tokens = sum(len(tokens) for tokens in token_ids)
    prompt_tokens = sum(len(prompt["prompt_token_ids"]) for prompt in prompts)
    generation_seconds = finished - generation_started
    manifest.update({
        "load_seconds": loaded - started,
        "generation_seconds": generation_seconds,
        "warmup_seconds": warmup_finished - warmup_started,
        "requests_per_second": args.num_prompts / generation_seconds,
        "total_tokens_per_second": (
            prompt_tokens + output_tokens
        ) / generation_seconds,
        "output_tokens_per_second": output_tokens / generation_seconds,
        "prompt_token_ids": [prompt["prompt_token_ids"] for prompt in prompts],
        "output_token_ids": token_ids,
        "output_token_ids_sha256": hashlib.sha256(token_payload).hexdigest(),
        "warmup_output_token_ids": warmup_token_ids,
        "request_terminal_status": "success",
        "access_accounting": accounting_snapshot,
        "triton_exact_bindings": (
            triton_binder.bound_count if triton_binder is not None else 0
        ),
    })
    access_status = None
    eval_status = None
    if accounting_snapshot is not None:
        access_status = accounting_snapshot.get("access", {}).get("status")
        eval_status = accounting_snapshot.get("eval_delay", {}).get("status")
    complete = accounting_snapshot is None or (
        access_status == "COMPLETE" and
        (args.eval_delay_ns < 0 or eval_status == "COMPLETE")
    )
    manifest["scientific_status"] = "COMPLETE" if complete else "INCOMPLETE"
    output_path = report / "result.json"
    output_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    print(json.dumps(manifest, indent=2, sort_keys=True))
    if not complete:
        raise SystemExit(70)
    del outputs
    del llm
    gc.collect()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
