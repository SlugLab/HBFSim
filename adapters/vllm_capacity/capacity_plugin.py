"""Opt-in vLLM general plugin for the audited HBFSim capacity path."""

from __future__ import annotations

import json
import hashlib
import os
import pathlib
import threading


PLUGIN_NAME = "hbfsim_capacity"
LOAD_FORMAT = "hbfsim_capacity"
MODEL_ARCHITECTURE = "Qwen3MoeForCausalLM"
MODEL_TARGET = (
    "adapters.vllm_capacity.capacity_model:CapacityQwen3MoeForCausalLM"
)

_LOCK = threading.Lock()
_REGISTERED = False


def _write_registration_evidence() -> None:
    report_dir = os.environ.get("HBFSIM_CAPACITY_REPORT_DIR")
    if not report_dir:
        return
    root = pathlib.Path(report_dir).resolve()
    path = root / "e6-plugin-processes" / f"{os.getpid()}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    module_path = pathlib.Path(__file__).resolve(strict=True)
    module_sha256 = hashlib.sha256(module_path.read_bytes()).hexdigest()
    payload = {
        "schema_version": 1,
        "status": "PASS",
        "plugin_name": PLUGIN_NAME,
        "load_format": LOAD_FORMAT,
        "model_architecture": MODEL_ARCHITECTURE,
        "model_target": MODEL_TARGET,
        "pid": os.getpid(),
        "ppid": os.getppid(),
        "module_path": str(module_path),
        "module_sha256": module_sha256,
        "process_role": os.environ.get("VLLM_PROCESS_ROLE", "unspecified"),
        "opt_in": True,
        "site_packages_modified": False,
    }
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    temporary.replace(path)
    summary = root / "e6-plugin-registration.json"
    summary_tmp = summary.with_suffix(summary.suffix + f".{os.getpid()}.tmp")
    summary_tmp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    summary_tmp.replace(summary)


def register() -> None:
    """Register the lazy model target and custom loader exactly once."""
    global _REGISTERED
    if os.environ.get("HBFSIM_CAPACITY_ENABLE") != "1":
        raise RuntimeError("hbfsim_capacity plugin loaded without explicit enable flag")
    with _LOCK:
        if _REGISTERED:
            return
        from vllm import ModelRegistry
        from vllm.model_executor.model_loader import register_model_loader

        from adapters.vllm_capacity.capacity_loader import CapacityModelLoader

        register_model_loader(LOAD_FORMAT)(CapacityModelLoader)
        ModelRegistry.register_model(MODEL_ARCHITECTURE, MODEL_TARGET)
        _REGISTERED = True
        _write_registration_evidence()


__all__ = ["register"]
