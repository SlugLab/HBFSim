#!/usr/bin/env python3
"""Launch one audited, local-only E6 capacity pilot after a continuous GPU gate."""

from __future__ import annotations

import argparse
import gc
import hashlib
import json
import os
import pathlib
import platform
import shutil
import sys
import threading
import time
from datetime import datetime, timezone
from typing import Any

from adapters.vllm_capacity.gpu_occupancy import (
    external_gpu_processes,
    gpu_occupancy_snapshot,
    process_tree,
)


PLUGIN_NAME = "hbfsim_capacity"
ENTRY_POINT = "adapters.vllm_capacity.capacity_plugin:register"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def atomic_json(path: pathlib.Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    temporary.replace(path)


def sha256(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(4 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def continuous_idle_gate(
    *, root_pid: int, seconds: float, interval: float
) -> list[dict[str, Any]]:
    if seconds <= 0 or interval <= 0:
        raise ValueError("GPU idle gate duration and interval must be positive")
    deadline = time.monotonic() + seconds
    samples: list[dict[str, Any]] = []
    while True:
        occupancy = gpu_occupancy_snapshot(root_pid)
        external = occupancy["external_processes"]
        sample = {
            "timestamp_utc": utc_now(),
            "external": external,
            "occupancy": occupancy,
        }
        samples.append(sample)
        if external:
            raise RuntimeError(f"GPU_BUSY_EXTERNAL:{external}")
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        time.sleep(min(interval, remaining))
    return samples


class ExternalGpuWatchdog:
    def __init__(
        self,
        root_pid: int,
        interval: float = 1.0,
        max_consecutive_probe_errors: int = 3,
    ) -> None:
        if interval <= 0 or max_consecutive_probe_errors <= 0:
            raise ValueError("watchdog interval and error limit must be positive")
        self.root_pid = root_pid
        self.interval = interval
        self.max_consecutive_probe_errors = max_consecutive_probe_errors
        self.stop_event = threading.Event()
        self.records: list[dict[str, Any]] = []
        self.probe_errors: list[dict[str, Any]] = []
        self.error: str | None = None
        self.known_owned_pids = {root_pid}
        self.started = False
        self.thread = threading.Thread(
            target=self._run, name="gpu-watchdog", daemon=True
        )

    def _run(self) -> None:
        consecutive_probe_errors = 0
        while not self.stop_event.wait(self.interval):
            try:
                self.known_owned_pids.update(process_tree(self.root_pid))
                external = external_gpu_processes(
                    self.root_pid,
                    known_owned_pids=self.known_owned_pids,
                )
            except BaseException as exc:
                consecutive_probe_errors += 1
                record = {
                    "timestamp_utc": utc_now(),
                    "error": repr(exc),
                    "consecutive_failures": consecutive_probe_errors,
                }
                self.probe_errors.append(record)
                if (
                    consecutive_probe_errors
                    >= self.max_consecutive_probe_errors
                ):
                    self.error = repr(exc)
                    return
                continue
            consecutive_probe_errors = 0
            if external:
                self.records.append(
                    {"timestamp_utc": utc_now(), "external": external}
                )

    def start(self) -> None:
        self.known_owned_pids.update(process_tree(self.root_pid))
        external = external_gpu_processes(
            self.root_pid,
            known_owned_pids=self.known_owned_pids,
        )
        if external:
            self.records.append(
                {"timestamp_utc": utc_now(), "external": external}
            )
            raise RuntimeError(
                f"GPU_BUSY_EXTERNAL_BEFORE_WATCHDOG_START:{external}"
            )
        self.thread.start()
        self.started = True

    def close(self) -> None:
        self.stop_event.set()
        if not self.started:
            return
        self.thread.join(timeout=max(5.0, 2 * self.interval))
        if self.thread.is_alive() and self.error is None:
            self.error = "GPU watchdog did not stop within its bounded join"


def install_dist_info_overlay(report_dir: pathlib.Path) -> pathlib.Path:
    overlay = report_dir / "plugin-overlay"
    dist_info = overlay / "hbfsim_vllm_capacity-0.1.0.dist-info"
    dist_info.mkdir(parents=True, exist_ok=True)
    (dist_info / "METADATA").write_text(
        "Metadata-Version: 2.1\n"
        "Name: hbfsim-vllm-capacity\n"
        "Version: 0.1.0\n"
    )
    (dist_info / "entry_points.txt").write_text(
        "[vllm.general_plugins]\n" f"{PLUGIN_NAME} = {ENTRY_POINT}\n"
    )
    return overlay


def validate_environment(model_root: pathlib.Path, report_dir: pathlib.Path) -> dict[str, Any]:
    if sys.version_info[:2] != (3, 13):
        raise RuntimeError(f"frozen native environment requires Python 3.13: {sys.version}")
    if not model_root.is_dir():
        raise RuntimeError(f"local model root missing: {model_root}")
    if shutil.disk_usage(report_dir).free < 4 * (1 << 30):
        raise RuntimeError("less than 4 GiB free; refusing E6 pilot")
    manifest = pathlib.Path(os.environ["HBFSIM_CAPACITY_MANIFEST"]).resolve(strict=True)
    expected_sha = os.environ["HBFSIM_CAPACITY_MANIFEST_SHA256"]
    actual_sha = sha256(manifest)
    if actual_sha != expected_sha:
        raise RuntimeError(f"manifest SHA mismatch: {actual_sha} != {expected_sha}")
    return {
        "python": sys.version,
        "platform": platform.platform(),
        "model_root": str(model_root),
        "manifest": str(manifest),
        "manifest_sha256": actual_sha,
        "disk_free_bytes": shutil.disk_usage(report_dir).free,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", type=pathlib.Path, required=True)
    parser.add_argument("--result-dir", type=pathlib.Path, required=True)
    parser.add_argument("--prompt", default="Write one short sentence about memory.")
    parser.add_argument("--max-tokens", type=int, default=8)
    parser.add_argument("--seed", type=int, default=20260902)
    parser.add_argument("--idle-seconds", type=float, default=30.0)
    parser.add_argument("--idle-interval", type=float, default=2.0)
    args = parser.parse_args()

    result_dir = args.result_dir.expanduser().resolve()
    if not result_dir.is_relative_to(pathlib.Path("/root/hbfsim-exp")):
        raise RuntimeError(f"result directory escapes write boundary: {result_dir}")
    result_dir.mkdir(parents=True, exist_ok=False)
    os.environ["HBFSIM_CAPACITY_REPORT_DIR"] = str(result_dir)
    os.environ["HBFSIM_CAPACITY_MODEL_ROOT"] = str(args.model.expanduser().resolve())
    os.environ["HBFSIM_CAPACITY_ENABLE"] = "1"
    os.environ["VLLM_PLUGINS"] = PLUGIN_NAME
    os.environ.setdefault("HF_HUB_OFFLINE", "1")
    os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
    os.environ.setdefault("VLLM_WORKER_MULTIPROC_METHOD", "spawn")

    overlay = install_dist_info_overlay(result_dir)
    sys.path.insert(0, str(overlay))
    previous_pythonpath = os.environ.get("PYTHONPATH", "")
    os.environ["PYTHONPATH"] = str(overlay) + (
        os.pathsep + previous_pythonpath if previous_pythonpath else ""
    )

    root_pid = os.getpid()
    report: dict[str, Any] = {
        "schema_version": 1,
        "phase": "E6-pilot",
        "status": "RUNNING",
        "started_at_utc": utc_now(),
        "root_pid": root_pid,
        "resource_policy": {
            "allocation": "full capacity cache during model load",
            "hold": "entire pilot",
            "release": "engine and HBFSim teardown",
            "dummy_memory_hog": False,
            "kill_other_processes": False,
        },
    }
    output_path = result_dir / "e6-capacity-pilot.json"
    watchdog: ExternalGpuWatchdog | None = None
    llm: Any | None = None
    try:
        report["environment"] = validate_environment(
            args.model.expanduser().resolve(), result_dir
        )
        report["idle_gate"] = continuous_idle_gate(
            root_pid=root_pid,
            seconds=args.idle_seconds,
            interval=args.idle_interval,
        )
        watchdog = ExternalGpuWatchdog(root_pid)
        watchdog.start()

        # Imports occur only after the no-external-compute gate.  Plugin
        # discovery sees the per-cell dist-info overlay before vLLM is loaded.
        import torch
        import vllm
        from vllm import LLM, SamplingParams

        report["environment"].update(
            {
                "torch": torch.__version__,
                "torch_cuda": torch.version.cuda,
                "vllm": vllm.__version__,
            }
        )
        if vllm.__version__ != "0.15.1":
            raise RuntimeError(f"frozen vLLM version mismatch: {vllm.__version__}")
        if torch.version.cuda != "12.8":
            raise RuntimeError(f"frozen torch CUDA mismatch: {torch.version.cuda}")

        torch.cuda.init()
        free_before, total_before = torch.cuda.mem_get_info()
        report["memory_checkpoints"] = {
            "before_model_load": {
                "free_bytes": int(free_before),
                "total_bytes": int(total_before),
            }
        }

        llm = LLM(
            model=str(args.model.expanduser().resolve()),
            load_format="hbfsim_capacity",
            tensor_parallel_size=1,
            enforce_eager=True,
            trust_remote_code=False,
            max_num_seqs=1,
            max_model_len=256,
            max_num_batched_tokens=256,
            gpu_memory_utilization=0.85,
            enable_expert_parallel=False,
            enable_chunked_prefill=False,
            seed=args.seed,
        )
        free_loaded, total_loaded = torch.cuda.mem_get_info()
        report["memory_checkpoints"]["after_engine_load"] = {
            "free_bytes": int(free_loaded),
            "total_bytes": int(total_loaded),
            "used_delta_bytes": int(free_before - free_loaded),
        }
        # The full resident capacity cache has now been allocated by the actual
        # worker and remains held through all generation work below.
        before_generation = external_gpu_processes(root_pid)
        if before_generation:
            raise RuntimeError(f"GPU_CONTENDED_BEFORE_GENERATION:{before_generation}")
        sampling = SamplingParams(
            temperature=0.0,
            max_tokens=args.max_tokens,
            seed=args.seed,
        )
        generated = llm.generate([args.prompt], sampling, use_tqdm=False)
        text = generated[0].outputs[0].text
        report["generation"] = {
            "prompt": args.prompt,
            "text": text,
            "max_tokens": args.max_tokens,
            "seed": args.seed,
            "request_count": len(generated),
        }
        if not text:
            raise RuntimeError("capacity pilot generated an empty result")
        report["status"] = "PASS"
    except BaseException as exc:
        report["status"] = "FAILED"
        report["error"] = repr(exc)
    finally:
        cleanup_errors: list[str] = []
        if watchdog is not None:
            watchdog.close()
            report["external_gpu_watchdog"] = {
                "contamination_records": watchdog.records,
                "watchdog_error": watchdog.error,
            }
            if watchdog.records or watchdog.error:
                report["status"] = "CONTAMINATED"
        if llm is not None:
            shutdown = getattr(llm, "shutdown", None)
            if callable(shutdown):
                try:
                    shutdown()
                except BaseException as exc:
                    report["shutdown_error"] = repr(exc)
                    cleanup_errors.append(f"LLM shutdown: {exc!r}")
            del llm
        gc.collect()
        try:
            from adapters.vllm_capacity.capacity_runtime import (
                capacity_runtime_if_created,
            )

            runtime = capacity_runtime_if_created()
            if runtime is not None and runtime.is_open:
                report["stats_v2"] = runtime.stats_v2()
                report["pointer_provenance"] = (
                    runtime.pointer_provenance_summary()
                )
                runtime.close()
        except BaseException as exc:
            # Parent and spawned worker do not necessarily share the context.
            report["parent_runtime_teardown_note"] = repr(exc)
            cleanup_errors.append(f"capacity teardown: {exc!r}")
        if cleanup_errors and report.get("status") != "CONTAMINATED":
            report["cleanup_errors"] = cleanup_errors
            report["status"] = "FAILED_CLEANUP"
        report["finished_at_utc"] = utc_now()
        atomic_json(output_path, report)
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
