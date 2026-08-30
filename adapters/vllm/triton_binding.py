"""Bind Triton cubin-loaded functions to exact bpftime PTX variants."""

from __future__ import annotations

import ctypes
import hashlib
import json
import os
import pathlib
import time
from collections.abc import Callable, Mapping, Sequence
from typing import Any


class TritonBindingError(RuntimeError):
    pass


NativeBinder = Callable[[int, bytes, str], int]
NativeApprover = Callable[[int, Any], int]
_DIRECT_GATE_LIBRARY: Any | None = None
_ACTIVE_NATIVE_LAUNCH_GATE: "TritonNativeLauncherGate | None" = None


def load_direct_gate_library() -> Any:
    global _DIRECT_GATE_LIBRARY
    if _DIRECT_GATE_LIBRARY is not None:
        return _DIRECT_GATE_LIBRARY
    raw_path = os.environ.get("HBFSIM_LAUNCH_GATE_LIBRARY", "")
    path = pathlib.Path(raw_path)
    if not raw_path or not path.is_absolute() or not path.is_file():
        raise TritonBindingError(
            "HBFSIM_LAUNCH_GATE_LIBRARY must name the absolute launch-gate "
            "library"
        )
    try:
        _DIRECT_GATE_LIBRARY = ctypes.CDLL(
            str(path), mode=ctypes.RTLD_GLOBAL
        )
    except OSError as error:
        raise TritonBindingError(
            f"cannot load HBFSim launch gate {path}: {error}"
        ) from error
    return _DIRECT_GATE_LIBRARY


def load_native_binder() -> NativeBinder:
    if os.environ.get("HBFSIM_NATIVE_TRITON_BINDING") == "1":
        library = load_direct_gate_library()
        try:
            function = library.hbfsim_bind_native_cuda_function
        except AttributeError as error:
            raise TritonBindingError(
                "HBFSim direct native PTX binder is not loaded"
            ) from error
        function.argtypes = (
            ctypes.c_void_p,
            ctypes.c_char_p,
            ctypes.c_char_p,
        )
        function.restype = ctypes.c_int

        def invoke_direct(original: int, ptx: bytes,
                          kernel_name: str) -> int:
            module_id = (
                "ptx:sha256:" + hashlib.sha256(ptx).hexdigest()
            ).encode("ascii")
            return int(function(
                ctypes.c_void_p(original), module_id,
                kernel_name.encode("utf-8")
            ))

        return invoke_direct

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


def load_native_approver() -> NativeApprover:
    library = load_direct_gate_library()
    try:
        function = library.hbfsim_approve_original_cuda_function
    except AttributeError as error:
        raise TritonBindingError(
            "HBFSim direct native launch approver is not loaded"
        ) from error
    function.argtypes = (
        ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(ctypes.c_void_p),
    )
    function.restype = ctypes.c_int

    def invoke(original: int, parameters: Any) -> int:
        return int(function(
            ctypes.c_void_p(original), parameters, None
        ))

    return invoke


def _argument_value(argument: Any) -> int:
    if argument is None:
        return 0
    data_ptr = getattr(argument, "data_ptr", None)
    if callable(data_ptr):
        value = int(data_ptr())
    elif isinstance(argument, bool):
        value = int(argument)
    elif isinstance(argument, int):
        value = argument
    elif isinstance(argument, float):
        raise TritonBindingError(
            "floating launch parameters require an explicit ABI packer"
        )
    else:
        try:
            value = int(argument)
        except (TypeError, ValueError, OverflowError) as error:
            raise TritonBindingError(
                f"unsupported Triton launch parameter: "
                f"{type(argument).__name__}"
            ) from error
    if value < 0:
        value &= (1 << 64) - 1
    if value >= 1 << 64:
        raise TritonBindingError("Triton launch parameter exceeds 64 bits")
    return value


class TritonNativeLauncherGate:
    def __init__(self, native: NativeApprover):
        self.native = native
        self.bound_functions: set[int] = set()

    def mark_bound(self, function: int) -> None:
        self.bound_functions.add(int(function))

    def approve(self, function: int, arguments: Sequence[Any],
                parameter_indices: tuple[int, ...] | None,
                global_scratch_size: int,
                profile_scratch_size: int) -> None:
        function = int(function)
        if function not in self.bound_functions:
            return
        if parameter_indices is None:
            raise TritonBindingError(
                "bound Triton signature needs unsupported flattening"
            )
        if global_scratch_size != 0 or profile_scratch_size != 0:
            raise TritonBindingError(
                "bound Triton function uses unmodeled launcher scratch"
            )
        try:
            selected = [arguments[index] for index in parameter_indices]
        except IndexError as error:
            raise TritonBindingError(
                "Triton launcher argument count does not match its signature"
            ) from error
        values = [_argument_value(argument) for argument in selected]
        # Triton appends global and profile scratch pointers to every CUDA
        # parameter vector. The bound fused-MoE variants prove both sizes zero.
        values.extend((0, 0))
        storage = [ctypes.c_uint64(value) for value in values]
        parameters = (ctypes.c_void_p * len(storage))(*[
            ctypes.cast(ctypes.byref(item), ctypes.c_void_p)
            for item in storage
        ])
        if self.native(function, parameters) != 1:
            raise TritonBindingError(
                f"HBFSim rejected native Triton launch 0x{function:x}"
            )


def _simple_parameter_indices(signature: Mapping[Any, Any]
                              ) -> tuple[int, ...] | None:
    values = list(signature.values())
    if any(isinstance(value, tuple) or
           (isinstance(value, str) and value.startswith("tensordesc"))
           for value in values):
        return None
    return tuple(
        index for index, value in enumerate(values)
        if value != "constexpr"
    )


def install_native_launcher_gate(
    launcher_class: type[Any], gate: TritonNativeLauncherGate
) -> None:
    global _ACTIVE_NATIVE_LAUNCH_GATE
    _ACTIVE_NATIVE_LAUNCH_GATE = gate
    if getattr(launcher_class, "_hbfsim_native_gate_installed", False):
        return
    original_init = launcher_class.__init__
    original_call = launcher_class.__call__

    def gated_init(self: Any, src: Any, metadata: Any) -> None:
        original_init(self, src, metadata)
        self._hbfsim_parameter_indices = _simple_parameter_indices(
            src.signature
        )

    def gated_call(self: Any, grid_x: int, grid_y: int, grid_z: int,
                   stream: int, function: int, *arguments: Any) -> Any:
        active = _ACTIVE_NATIVE_LAUNCH_GATE
        function_value = int(
            function.value if hasattr(function, "value") else function
        )
        if active is not None and function_value in active.bound_functions:
            if len(arguments) < 4:
                raise TritonBindingError(
                    "Triton launcher omitted its control arguments"
                )
            active.approve(
                function_value, arguments[4:],
                self._hbfsim_parameter_indices,
                int(self.global_scratch_size),
                int(self.profile_scratch_size),
            )
        return original_call(
            self, grid_x, grid_y, grid_z, stream, function, *arguments
        )

    launcher_class.__init__ = gated_init
    launcher_class.__call__ = gated_call
    launcher_class._hbfsim_native_gate_installed = True


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
                 on_bound: Callable[[int], None] | None = None):
        self.report_dir = pathlib.Path(report_dir).resolve()
        self.report_dir.mkdir(parents=True, exist_ok=True)
        self.output = self.report_dir / "triton-bindings.jsonl"
        self.native = native
        self.on_bound = on_bound
        self.required = required
        self.retries = retries
        self.retry_seconds = retry_seconds
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
            self.bound_count += 1
            if self.on_bound is not None:
                self.on_bound(original)
            return
        if self.required and name == "fused_moe_kernel":
            raise TritonBindingError(
                f"exact PTX variant binding failed for {name}: "
                f"{record['result']}"
            )


def install_triton_binding(report_dir: pathlib.Path, *, required: bool = True,
                           native: NativeBinder | None = None
                           ) -> TritonVariantBinder:
    import triton

    on_bound = None
    if os.environ.get("HBFSIM_NATIVE_TRITON_BINDING") == "1":
        from triton.backends.nvidia.driver import CudaLauncher

        launcher_gate = TritonNativeLauncherGate(load_native_approver())
        install_native_launcher_gate(CudaLauncher, launcher_gate)
        on_bound = launcher_gate.mark_bound

    binder = TritonVariantBinder(
        report_dir, native or load_native_binder(), required=required,
        on_bound=on_bound,
    )
    triton.knobs.runtime.kernel_load_end_hook = binder.on_kernel_load
    return binder
