"""Bind Triton cubin-loaded functions to exact bpftime PTX variants."""

from __future__ import annotations

import ctypes
import hashlib
import json
import pathlib
import time
from collections.abc import Callable, Mapping
from typing import Any


class TritonBindingError(RuntimeError):
    pass


NativeBinder = Callable[[int, bytes, str], int]
AfterBind = Callable[[], Any]


def load_native_binder() -> NativeBinder:
    library = ctypes.CDLL(None)
    try:
        function = library.bpftime_nv_bind_ptx_variant
    except AttributeError as error:
        raise TritonBindingError(
            "bpftime exact PTX variant binder is not loaded"
        ) from error
    function.argtypes = (
        ctypes.c_void_p,
        ctypes.c_char_p,
        ctypes.c_size_t,
        ctypes.c_char_p,
    )
    function.restype = ctypes.c_int

    def invoke(original: int, ptx: bytes, kernel_name: str) -> int:
        return int(function(
            ctypes.c_void_p(original), ptx, len(ptx),
            kernel_name.encode("utf-8")
        ))

    return invoke


def _ptx_path(metadata_group: Any) -> pathlib.Path | None:
    if isinstance(metadata_group, Mapping):
        value = metadata_group.get("ptx")
        if value is None:
            matches = [path for filename, path in metadata_group.items()
                       if str(filename).endswith(".ptx")]
            value = matches[0] if len(matches) == 1 else None
    else:
        assembly = getattr(metadata_group, "asm", None)
        if isinstance(assembly, Mapping):
            value = assembly.get("ptx")
        else:
            value = getattr(metadata_group, "ptx", None)
    if value is None:
        return None
    return pathlib.Path(value).resolve()


class TritonVariantBinder:
    """Callable matching Triton's kernel_load_end_hook contract."""

    def __init__(self, report_dir: pathlib.Path, native: NativeBinder,
                 *, required: bool = True, retries: int = 100,
                 retry_seconds: float = 0.05,
                 after_bind: AfterBind | None = None):
        self.report_dir = pathlib.Path(report_dir).resolve()
        self.report_dir.mkdir(parents=True, exist_ok=True)
        self.output = self.report_dir / "triton-bindings.jsonl"
        self.native = native
        self.required = required
        self.retries = retries
        self.retry_seconds = retry_seconds
        self.after_bind = after_bind
        self.bound_count = 0

    def _write(self, record: dict[str, Any]) -> None:
        with self.output.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(record, sort_keys=True) + "\n")

    def on_kernel_load(self, module: Any, function: Any, name: str,
                       metadata_group: Any, triton_hash: str) -> None:
        del module
        path = _ptx_path(metadata_group)
        if path is None or not path.is_file():
            record = {
                "schema_version": 1,
                "kernel_name": name,
                "triton_hash": triton_hash,
                "result": "missing_ptx",
            }
            self._write(record)
            if self.required and name == "fused_moe_kernel":
                raise TritonBindingError(
                    f"exact PTX variant metadata missing for {name}"
                )
            return

        payload = path.read_bytes()
        original = int(function.value if hasattr(function, "value")
                       else function)
        result = 1
        attempts = 0
        while result == 1 and attempts <= self.retries:
            result = self.native(original, payload, name)
            if result == 1 and attempts < self.retries:
                time.sleep(self.retry_seconds)
            attempts += 1
        labels = {
            0: "bound",
            1: "bootstrap_pending",
            2: "variant_not_found",
            3: "ambiguous",
            4: "launch_gate_rejected",
        }
        record = {
            "schema_version": 1,
            "kernel_name": name,
            "triton_hash": triton_hash,
            "ptx_path": str(path),
            "ptx_sha256": hashlib.sha256(payload).hexdigest(),
            "original_function": hex(original),
            "result": labels.get(result, f"native_error_{result}"),
            "native_result": result,
            "attempts": attempts,
        }
        self._write(record)
        if result == 0:
            if self.after_bind is not None:
                self.after_bind()
            self.bound_count += 1
            return
        if self.required and name == "fused_moe_kernel":
            raise TritonBindingError(
                f"exact PTX variant binding failed for {name}: "
                f"{record['result']}"
            )


def install_triton_binding(report_dir: pathlib.Path, *, required: bool = True,
                           native: NativeBinder | None = None,
                           after_bind: AfterBind | None = None,
                           ) -> TritonVariantBinder:
    import triton

    binder = TritonVariantBinder(
        report_dir, native or load_native_binder(), required=required,
        after_bind=after_bind,
    )
    triton.knobs.runtime.kernel_load_end_hook = binder.on_kernel_load
    return binder
