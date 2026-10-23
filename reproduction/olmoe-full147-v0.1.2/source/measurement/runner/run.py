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


_LAYER_TIMING_ATTRIBUTE = "_hbfsim_full_model_layer_timing_v1"


def install_layer_timing(model):
    """Install host-only boundaries on the actual OLMoE worker model."""
    import torch

    if hasattr(model, _LAYER_TIMING_ATTRIBUTE):
        raise RuntimeError("layer timing hooks already installed")
    if not hasattr(model, "model") or not hasattr(model.model, "layers"):
        raise RuntimeError("actual worker is not an OLMoE causal LM")
    layers = model.model.layers
    if len(layers) != 16 or model.model.start_layer != 0 or model.model.end_layer != 16:
        raise RuntimeError("full 16-layer OLMoE model is required")
    if not hasattr(model, "lm_head") or not hasattr(model, "logits_processor"):
        raise RuntimeError("OLMoE logits path is unavailable")
    modules = {"embedding": model.model.embed_tokens,
               **{f"layer-{i:02d}": layer for i, layer in enumerate(layers)},
               "final_norm": model.model.norm, "head": model.logits_processor}
    state = {"active": False, "open": {}, "durations_ns": {k: 0 for k in modules},
             "calls": {k: 0 for k in modules}, "handles": [], "errors": []}

    for name, module in modules.items():
        def before(_module, _args, key=name):
            if not state["active"]:
                return
            torch.cuda.synchronize()
            if key in state["open"]:
                state["errors"].append("overlapping call: " + key)
            state["open"][key] = time.perf_counter_ns()

        def after(_module, _args, _output, key=name):
            if not state["active"]:
                return
            torch.cuda.synchronize()
            ended = time.perf_counter_ns()
            started = state["open"].pop(key, None)
            if started is None:
                state["errors"].append("missing entry: " + key)
            else:
                state["durations_ns"][key] += ended - started
                state["calls"][key] += 1

        state["handles"].append(module.register_forward_pre_hook(before))
        state["handles"].append(module.register_forward_hook(after))
    setattr(model, _LAYER_TIMING_ATTRIBUTE, state)
    return {"status": "INSTALLED", "modules": list(modules),
            "method": "synchronized_host_forward_hooks"}


def begin_layer_timing(model):
    state = getattr(model, _LAYER_TIMING_ATTRIBUTE)
    if state["active"] or any(state["calls"].values()):
        raise RuntimeError("layer timing was already started")
    state["active"] = True
    return {"status": "ACTIVE", "modules": list(state["calls"])}


def end_layer_timing(model):
    import torch

    state = getattr(model, _LAYER_TIMING_ATTRIBUTE)
    torch.cuda.synchronize()
    state["active"] = False
    errors = list(state["errors"])
    if state["open"]:
        errors.append("unfinished modules: " + ",".join(sorted(state["open"])))
    if any(count <= 0 for count in state["calls"].values()):
        errors.append("one or more full-model modules were not called")
    layer_counts = [state["calls"][f"layer-{i:02d}"] for i in range(16)]
    if len(set(layer_counts)) != 1:
        errors.append("layer call counts differ")
    receipt = {"schema": "hbfsim.olmoe_full_model_layer_host_timing.v1",
               "status": "PASS" if not errors else "INCOMPLETE",
               "method": "cuda_synchronized_host_forward_hooks",
               "head_boundary": "logits_processor_including_lm_head_projection_and_postprocessing",
               "durations_ns": dict(state["durations_ns"]),
               "calls": dict(state["calls"]), "errors": errors}
    for handle in state["handles"]:
        handle.remove()
    delattr(model, _LAYER_TIMING_ATTRIBUTE)
    return receipt


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
    parser.add_argument("--bound-plan", required=True, type=pathlib.Path)
    parser.add_argument("--num-prompts", type=int, default=4)
    parser.add_argument("--input-len", type=int, default=32)
    parser.add_argument("--output-len", type=int, default=32)
    parser.add_argument("--max-model-len", type=int, default=256)
    parser.add_argument("--max-num-batched-tokens", type=int, default=256)
    parser.add_argument("--gpu-memory-utilization", type=float, default=0.85)
    parser.add_argument("--moe-backend", choices=("triton",), default="triton")
    parser.add_argument("--hbf-parameter-regex", default="")
    parser.add_argument("--hbf-range-bytes", type=int, default=0)
    parser.add_argument("--hbf-ring-capacity", type=int, choices=(256,), default=256)
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
            not 0 < args.request_timeout_ns <= (1 << 64) - 1):
        raise SystemExit(
            "warmup-requests must be nonnegative, eval-delay-ns must be >= -1, "
            "and request-timeout-ns must be positive and fit uint64")
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
        "ring_capacity": args.hbf_ring_capacity,
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


def close_hbf_backend_session(llm: Any, loader: Any, report_dir: pathlib.Path) -> dict[str, Any]:
    results=llm.apply_model(loader.close_model_session)
    if not isinstance(results,list) or not results or any(value is not None for value in results):
        raise RuntimeError('HBF model-worker session close not acknowledged')
    report_path=report_dir/'hbf-backend-report.json'
    import runpy
    contract=runpy.run_path(str(pathlib.Path(__file__).resolve().parents[1]/'validator/hbf_cold_contract.py'))
    raw=report_path.read_bytes()
    contract['validate_report'](json.loads(raw),os.environ['HBFSIM_HBF_WAIT_MODE'])
    return {'status':'CLOSED_WITH_FINAL_HBF_REPORT','worker_count':len(results),
            'report_sha256':hashlib.sha256(raw).hexdigest()}


def close_ucie_backend_session(llm: Any, loader: Any,
                               report_dir: pathlib.Path) -> dict[str, Any]:
    """Close the real model worker's session before vLLM tears CUDA down."""
    close_results = llm.apply_model(loader.close_model_session)
    if not isinstance(close_results, list) or not close_results or any(
        result is not None for result in close_results
    ):
        raise RuntimeError("UCIe model-worker close did not acknowledge every worker")
    report_path = report_dir / "ucie-backend-report.json"
    report = json.loads(report_path.read_text())
    expected_wait = (
        "zero_injected" if os.environ["HBFSIM_UCIE_WAIT_MODE"] == "zero"
        else "nominal"
    )
    if (report.get("schema") != "hbfsim.ucie.host_backend_report.v1"
            or report.get("wait_mode") != expected_wait
            or report.get("control_generation", 0) <= 0
            or report.get("published", 0) <= 0
            or report.get("caller_outstanding") != 0
            or report.get("failed") != 0):
        raise RuntimeError("UCIe final backend report is incomplete")
    return {
        "status": "CLOSED_WITH_FINAL_REPORT",
        "worker_count": len(close_results),
        "report_sha256": hashlib.sha256(report_path.read_bytes()).hexdigest(),
    }


def main() -> int:
    args = parse_args()
    validate_args(args)
    bound_plan_bytes = args.bound_plan.read_bytes()
    bound_plan = json.loads(bound_plan_bytes)
    plan_env = bound_plan.get("env")
    if not isinstance(plan_env, dict):
        raise RuntimeError("bound plan has no environment")
    phase_mode = plan_env.get("HBFSIM_HYBRID_PHASE_DIAG_V1")
    if phase_mode not in ("0", "1"):
        raise RuntimeError("bound plan requires explicit 0/1 phase diagnostic flag")
    if os.environ.get("HBFSIM_HYBRID_PHASE_DIAG_V1") != phase_mode:
        raise RuntimeError("phase diagnostic flag differs between bound plan and process")
    candidate_version = plan_env.get("HBFSIM_HYBRID_PHASE_CANDIDATE_VERSION")
    if not isinstance(candidate_version, str) or not candidate_version or os.environ.get("HBFSIM_HYBRID_PHASE_CANDIDATE_VERSION") != candidate_version:
        raise RuntimeError("phase candidate version differs between bound plan and process")
    weight_policy = resolve_weight_policy(args)
    import runpy
    native_contract=pathlib.Path(__file__).resolve().parents[1]/'native_environment_contract.py'
    if hashlib.sha256(native_contract.read_bytes()).hexdigest()!=bound_plan['support_source_sha256'].get(str(native_contract)):
        raise RuntimeError('native/common input identity source changed')
    input_contract=runpy.run_path(str(native_contract))
    if bound_plan['consumer_policy']=='NATIVE_ONLY':
        input_contract['configuration'](args,bound_plan)
    if args.mode == "timing":
        configure_instrumentation_policy(weight_policy)
    report = pathlib.Path(args.report_dir).resolve()
    cache = configure_environment(report)
    manifest = base_manifest(args, cache)
    manifest["weight_binding"] = weight_policy
    manifest["model_file_identity"]=input_contract["model_identity"](args)
    manifest["bound_plan_sha256"] = hashlib.sha256(bound_plan_bytes).hexdigest()
    manifest["hybrid_phase_diag_requested"] = phase_mode
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
        "compilation_config": {"mode": 0},
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
    # Default-off timestamp observer: no scientific call, synchronization or order changes.
    host_timeline = None
    if phase_mode == "1":
        import runpy as _host_runpy
        host_timeline = _host_runpy.run_path(str(pathlib.Path(__file__).resolve().parents[1] / "timing_observation.py"))["HostTimeline"]()
        host_timeline.mark("LLM.enter")
    started = time.perf_counter()
    llm = LLM(**engine_args)
    if host_timeline is not None: host_timeline.mark("LLM.exit")
    normal_preparation = None
    normal_preparation_seconds = None
    normal_preparation_status = "NOT_APPLICABLE_NATIVE"
    if timing_enabled:
        normal_preparation_started = time.perf_counter()
        if host_timeline is not None: host_timeline.mark("normal_prepare.enter")
        normal_preparation = llm.apply_model(hbfsim_loader.prepare_normal_model_backend)
        if host_timeline is not None: host_timeline.mark("normal_prepare.exit")
        if (not isinstance(normal_preparation, list) or len(normal_preparation) != 1 or
                normal_preparation[0].get("status") != "PASS"):
            raise RuntimeError("normal worker/context pre-generation preparation failed")
        normal_preparation_seconds = time.perf_counter() - normal_preparation_started
        normal_preparation_status = "ACTUAL_HBF_PREPARATION_PASS"
    if not timing_enabled:
        import runpy
        native_contract=pathlib.Path(__file__).resolve().parents[1]/'native_environment_contract.py'
        expected=bound_plan['support_source_sha256'].get(str(native_contract))
        if hashlib.sha256(native_contract.read_bytes()).hexdigest()!=expected:
            raise RuntimeError('native identity consumer source changed')
        role_source=pathlib.Path(bound_plan['env']['HBFSIM_HOST_ROLE_SOURCE'])
        if hashlib.sha256(role_source.read_bytes()).hexdigest()!=bound_plan['env']['HBFSIM_HOST_ROLE_SHA256']:
            raise RuntimeError('native host role source changed')
        role_contract=runpy.run_path(str(role_source))
        roles=role_contract['read_roles'](os.environ)
        placement=role_contract['place_framework'](roles,input_contract['ticks'](os.getpid()))
        input_contract['save'](report/'native-framework-role.json',placement)
        input_contract['capture'](report,args,bound_plan)
    loaded = time.perf_counter()
    sampling = SamplingParams(
        temperature=0.0,
        max_tokens=args.output_len,
        ignore_eos=True,
        seed=args.seed,
    )
    warmup_token_ids: list[list[list[int]]] = []
    if host_timeline is not None: host_timeline.mark("warmup.enter")
    warmup_started = time.perf_counter()
    for _ in range(args.warmup_requests):
        warmup = llm.generate(prompts, sampling, use_tqdm=False)
        warmup_token_ids.append([
            list(output.outputs[0].token_ids) for output in warmup
        ])
        del warmup
    warmup_finished = time.perf_counter()
    if host_timeline is not None: host_timeline.mark("warmup.exit")
    if host_timeline is not None: host_timeline.mark("install_hooks.enter")
    installed = llm.apply_model(install_layer_timing)
    if host_timeline is not None: host_timeline.mark("install_hooks.exit")
    if not isinstance(installed, list) or len(installed) != 1 or installed[0].get("status") != "INSTALLED":
        raise RuntimeError("full-model layer timing hooks were not installed")

    accounting = None
    accounting_snapshot = None
    if host_timeline is not None: host_timeline.mark("accounting_setup.enter")
    if args.request_accounting:
        accounting = GateRequestAccounting(
            args.eval_delay_ns, args.eval_trace_capacity
        )
        accounting.begin(args.accounting_epoch)
    if host_timeline is not None: host_timeline.mark("accounting_setup.exit")
    outputs = None
    request_error = None
    request_error_traceback = None
    snapshot_error = None
    def phase_snapshot() -> dict[str, int]:
        snapshots = llm.apply_model(
            hbfsim_loader.snapshot_model_hybrid_phase_diag_v1
        )
        if not isinstance(snapshots, list) or len(snapshots) != 1 or not isinstance(snapshots[0], dict):
            raise RuntimeError("expected one real model-worker timing snapshot")
        return snapshots[0]

    phase_before = None
    phase_before_getter_seconds = None
    if phase_mode == "1":
        phase_before_getter_started = time.perf_counter()
        if host_timeline is not None: host_timeline.mark("diag_before.enter")
        phase_before = phase_snapshot()
        if host_timeline is not None: host_timeline.mark("diag_before.exit")
        phase_before_getter_seconds = time.perf_counter() - phase_before_getter_started
        if (phase_before.get("enabled") != 1 or
                phase_before.get("schema") != 0x4842465048444931 or
                (phase_before.get("fast_requests", 0) > 0 and
                 phase_before.get("fast_total_ns", 0) == 0) or
                (phase_before.get("reference_requests", 0) > 0 and
                 phase_before.get("reference_total_ns", 0) == 0)):
            (report / "hybrid-phase-activation-failure.json").write_text(
                json.dumps(phase_before, indent=2, sort_keys=True) + "\n"
            )
            raise RuntimeError("model worker has not activated HYBRID phase timing")
    generation_core_finished = None
    if host_timeline is not None: host_timeline.mark("begin_hooks.enter")
    begun = llm.apply_model(begin_layer_timing)
    if host_timeline is not None: host_timeline.mark("begin_hooks.exit")
    if not isinstance(begun, list) or len(begun) != 1 or begun[0].get("status") != "ACTIVE":
        raise RuntimeError("full-model layer timing did not begin")
    if host_timeline is not None: host_timeline.mark("generate.enter")
    generation_started = time.perf_counter()
    try:
        outputs = llm.generate(prompts, sampling, use_tqdm=False)
    except BaseException as error:
        request_error = error
        request_error_traceback = traceback.format_exc()
    finally:
        # Preserve the original generation_seconds boundary: accounting stays
        # inside that window, while the new getter runs only outside it.
        generation_core_finished = time.perf_counter()
        if host_timeline is not None: host_timeline.mark("generate.exit")
        if host_timeline is not None: host_timeline.mark("accounting_snapshot.enter")
        if accounting is not None:
            try:
                accounting_snapshot = accounting.snapshot()
            except BaseException as error:
                snapshot_error = error
                accounting.abort()
    finished = time.perf_counter()
    if host_timeline is not None: host_timeline.mark("accounting_snapshot.exit")
    if host_timeline is not None: host_timeline.mark("end_hooks.enter")
    ended = llm.apply_model(end_layer_timing)
    if host_timeline is not None: host_timeline.mark("end_hooks.exit")
    if not isinstance(ended, list) or len(ended) != 1 or not isinstance(ended[0], dict):
        raise RuntimeError("full-model layer timing did not close")
    layer_timing = ended[0]
    if host_timeline is not None: host_timeline.mark("layer_receipt.enter")
    (report / "full-model-layer-timing.json").write_text(
        json.dumps(layer_timing, indent=2, sort_keys=True) + "\n")
    if host_timeline is not None: host_timeline.mark("layer_receipt.exit")
    phase_after = None
    phase_snapshot_error = None
    phase_after_getter_seconds = None
    if phase_mode == "1":
        phase_after_getter_started = time.perf_counter()
        if host_timeline is not None: host_timeline.mark("diag_after.enter")
        try:
            phase_after = phase_snapshot()
        except BaseException as error:
            phase_snapshot_error = repr(error)
        phase_after_getter_seconds = time.perf_counter() - phase_after_getter_started
        if host_timeline is not None: host_timeline.mark("diag_after.exit")
        (report / "diag-host-timeline.json").write_text(json.dumps(host_timeline.receipt(), sort_keys=True, indent=2) + "\n")
    phase_receipt = {
        "schema": "hbfsim.hybrid_phase_window.v1",
        "candidate_version": os.environ.get("HBFSIM_HYBRID_PHASE_CANDIDATE_VERSION", "unspecified_phase_diag_v1"),
        "requested_flag": phase_mode,
        "before": phase_before,
        "after": phase_after,
        "delta": None,
        "snapshot_error": phase_snapshot_error,
        "before_getter_seconds": phase_before_getter_seconds,
        "after_getter_seconds": phase_after_getter_seconds,
        "generation_core_seconds": generation_core_finished - generation_started,
        "generation_seconds_original_boundary": finished - generation_started,
    }
    phase_errors = []
    if phase_after is not None:
        if phase_after["generation"] != phase_before["generation"] or phase_after["schema"] != phase_before["schema"]:
            phase_errors.append("generation_or_schema_changed")
        fields = ("requests_submitted", "requests_completed", "fast_requests",
                  "reference_requests", "fast_modeled_ns", "fast_total_ns",
                  "reference_total_ns", "reference_ring_wait_ns",
                  "reference_post_target_loop_wait_ns")
        delta = {field: phase_after[field] - phase_before[field] for field in fields}
        phase_receipt["delta"] = delta
        if any(value < 0 for value in delta.values()):
            phase_errors.append("counter_regressed")
        if (delta["requests_submitted"] != delta["requests_completed"] or
                delta["reference_requests"] != delta["requests_completed"]):
            phase_errors.append("reference_host_completion_mismatch")
        if delta["reference_requests"] > 0 and delta["reference_total_ns"] == 0:
            phase_errors.append("reference_elapsed_missing")
        if delta["fast_requests"] > 0 and delta["fast_total_ns"] == 0:
            phase_errors.append("fast_elapsed_missing")
        if delta["reference_ring_wait_ns"] + delta["reference_post_target_loop_wait_ns"] > delta["reference_total_ns"]:
            phase_errors.append("reference_partition_exceeds_total")
    phase_receipt["validation_errors"] = phase_errors
    phase_receipt["diagnostic_status"] = (
        "DISABLED_BY_PLAN" if phase_mode == "0"
        else "INCOMPLETE" if phase_snapshot_error is not None or phase_after is None
        else "FAILED" if phase_errors else "PASS"
    )
    (report / "hybrid-phase-window.json").write_text(
        json.dumps(phase_receipt, indent=2, sort_keys=True) + "\n"
    )
    if request_error is not None or snapshot_error is not None:
        failure = dict(manifest)
        failure.update({
            "request_terminal_status": "failed",
            "request_error": repr(request_error) if request_error else None,
            "request_traceback": request_error_traceback,
            "snapshot_error": repr(snapshot_error) if snapshot_error else None,
            "access_accounting": accounting_snapshot,
            "hybrid_phase_window": phase_receipt,
            "full_model_layer_timing": layer_timing,
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
        "normal_preparation_seconds_within_load": normal_preparation_seconds,
        "normal_backend_preparation_status": normal_preparation_status,
        "generation_seconds": generation_seconds,
        "generation_core_seconds": generation_core_finished - generation_started,
        "hybrid_phase_window": phase_receipt,
        "full_model_layer_timing": layer_timing,
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
    backend_close_error = None
    if timing_enabled and os.environ.get('HBFSIM_NORMAL_BACKEND')=='legacy_hbf':
        try:
            manifest['hbf_session_close']=close_hbf_backend_session(llm,hbfsim_loader,report)
        except Exception as error:
            backend_close_error=error
            manifest['hbf_session_close']={'status':'FAILED','error':repr(error)}
    access_status = None
    eval_status = None
    if accounting_snapshot is not None:
        access_status = accounting_snapshot.get("access", {}).get("status")
        eval_status = accounting_snapshot.get("eval_delay", {}).get("status")
    complete = layer_timing["status"] == "PASS" and backend_close_error is None and phase_snapshot_error is None and not phase_errors and (accounting_snapshot is None or (
        access_status == "COMPLETE" and
        (args.eval_delay_ns < 0 or eval_status == "COMPLETE")
    ))
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
