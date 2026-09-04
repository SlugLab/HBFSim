"""Generation-safe expert staging into ordinary compact BF16 CUDA tensors."""

from __future__ import annotations

import ctypes
import hashlib
import json
import os
import pathlib
import subprocess
import sys
import threading
from dataclasses import dataclass
from typing import Any, Iterable, Sequence

try:
    from adapters.vllm_capacity.capacity_runtime import get_launch_gate_library
except ModuleNotFoundError:
    from capacity_runtime import get_launch_gate_library


HIDDEN_SIZE = 2048
INTERMEDIATE_SIZE = 768
BYTES_PER_BF16 = 2
W13_BYTES_PER_EXPERT = 2 * INTERMEDIATE_SIZE * HIDDEN_SIZE * BYTES_PER_BF16
W2_BYTES_PER_EXPERT = HIDDEN_SIZE * INTERMEDIATE_SIZE * BYTES_PER_BF16
TOTAL_BYTES_PER_EXPERT = W13_BYTES_PER_EXPERT + W2_BYTES_PER_EXPERT
MAX_EXPERTS = 128
KERNEL_NAME = "hbfsim_capacity_copy_bf16"
CUDA_WARP_SIZE = 32
CUDA_COPY_THREADS = 256
MIN_RING_CAPACITY = 2
MAX_RING_CAPACITY = 4096
_EVIDENCE_LOCK = threading.Lock()


def stable_unique(values: Iterable[int]) -> tuple[int, ...]:
    seen: set[int] = set()
    ordered: list[int] = []
    for value in values:
        item = int(value)
        if item not in seen:
            seen.add(item)
            ordered.append(item)
    return tuple(ordered)


def fused_expert_slot_count(
    active_experts: int,
    minimum_fused_slots: int,
    *,
    max_experts: int = MAX_EXPERTS,
) -> int:
    """Keep the compact expert dimension valid when dummy routes repeat IDs."""
    if active_experts <= 0:
        raise ValueError("active_experts must be positive")
    if minimum_fused_slots <= 0:
        raise ValueError("minimum_fused_slots must be positive")
    slots = max(active_experts, minimum_fused_slots)
    if slots > max_experts:
        raise ValueError(f"fused expert slots {slots} exceed workspace {max_experts}")
    return slots


def build_remap(expert_ids: Sequence[int], *, num_experts: int = MAX_EXPERTS) -> list[int]:
    unique = stable_unique(expert_ids)
    if len(unique) > num_experts:
        raise ValueError("too many unique experts")
    table = [-1] * num_experts
    for slot, expert in enumerate(unique):
        if expert < 0 or expert >= num_experts:
            raise ValueError(f"expert id out of range: {expert}")
        table[expert] = slot
    return table


def remap_nested_ids(rows: Sequence[Sequence[int]], table: Sequence[int]) -> list[list[int]]:
    remapped: list[list[int]] = []
    for row in rows:
        output: list[int] = []
        for expert in row:
            if expert < 0 or expert >= len(table) or table[expert] < 0:
                raise ValueError(f"unstaged expert id: {expert}")
            output.append(int(table[expert]))
        remapped.append(output)
    return remapped


def capacity_copy_launch_shape(
    elements: int, *, ring_capacity: int | None = None
) -> tuple[int, int]:
    """Keep at least half the fault-service ring free while a copy is resident."""
    if elements <= 0:
        raise ValueError("copy element count must be positive")
    if ring_capacity is None:
        raw_capacity = os.environ.get("HBFSIM_CAPACITY_RING_CAPACITY", "64")
        try:
            ring_capacity = int(raw_capacity, 10)
        except ValueError as exc:
            raise ValueError(
                f"invalid HBFSIM_CAPACITY_RING_CAPACITY: {raw_capacity!r}"
            ) from exc
    if (
        ring_capacity < MIN_RING_CAPACITY
        or ring_capacity > MAX_RING_CAPACITY
        or ring_capacity & (ring_capacity - 1)
    ):
        raise ValueError(
            "HBFSIM_CAPACITY_RING_CAPACITY must be a power of two in [2, 4096]"
        )

    # A warp may still own a request while the host advances the consumer head.
    # Reserving half the slots prevents producer/consumer wraparound from turning
    # exact-capacity occupancy into an intermittent slot-reuse race.
    safe_warps = max(1, ring_capacity // 2)
    threads = min(CUDA_COPY_THREADS, safe_warps * CUDA_WARP_SIZE)
    warps_per_block = threads // CUDA_WARP_SIZE
    capacity_blocks = max(1, safe_warps // warps_per_block)
    work_blocks = (elements + threads - 1) // threads
    return min(65535, capacity_blocks, work_blocks), threads


def compare_staged_bytes(
    actual: bytes, expected: bytes, *, page_bytes: int = 16_384
) -> dict[str, Any]:
    """Return an exact CPU-side comparison without launching verification kernels."""
    if len(actual) != len(expected):
        raise ValueError("staged and expected byte strings must have equal length")
    if len(actual) < 3:
        raise ValueError("staged byte strings must contain at least three bytes")
    if page_bytes <= 0:
        raise ValueError("page_bytes must be positive")
    probe_indices = sorted(
        {
            0,
            1,
            2,
            len(actual) // 2,
            len(actual) // 2 + 1,
            len(actual) - 3,
            len(actual) - 2,
            len(actual) - 1,
        }
    )
    first_differences: list[int] = []
    differing_pages: list[int] = []
    difference_count = 0
    if actual != expected:
        for index, (left, right) in enumerate(zip(actual, expected, strict=True)):
            if left == right:
                continue
            difference_count += 1
            if len(first_differences) < 64:
                first_differences.append(index)
            page = index // page_bytes
            if len(differing_pages) < 128 and (
                not differing_pages or differing_pages[-1] != page
            ):
                differing_pages.append(page)
    return {
        "actual_sha256": hashlib.sha256(actual).hexdigest(),
        "expected_sha256": hashlib.sha256(expected).hexdigest(),
        "actual_byte_sum": sum(actual),
        "expected_byte_sum": sum(expected),
        "probe_indices": probe_indices,
        "actual_probes": [actual[index] for index in probe_indices],
        "expected_probes": [expected[index] for index in probe_indices],
        "difference_count": difference_count,
        "first_difference": first_differences[0] if first_differences else None,
        "first_differences": first_differences,
        "differing_pages": differing_pages,
        "status": "PASS" if difference_count == 0 else "FAIL",
    }


@dataclass(frozen=True)
class StageTicket:
    generation: int
    layer: int
    experts: tuple[int, ...]


class StageStateMachine:
    """Fail-closed single-workspace ownership state machine."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._generation = 0
        self._state = "idle"
        self._ticket: StageTicket | None = None

    @property
    def state(self) -> str:
        with self._lock:
            return self._state

    def begin(self, layer: int, experts: Sequence[int]) -> StageTicket:
        unique = stable_unique(experts)
        if not unique:
            raise RuntimeError("cannot stage an empty expert set")
        if len(unique) > MAX_EXPERTS:
            raise RuntimeError("active expert set exceeds model expert count")
        with self._lock:
            if self._state != "idle":
                raise RuntimeError(
                    f"duplicate/in-flight staging rejected in state {self._state}"
                )
            self._generation += 1
            self._ticket = StageTicket(self._generation, int(layer), unique)
            self._state = "staging"
            return self._ticket

    def mark_ready(self, ticket: StageTicket) -> None:
        with self._lock:
            self._require(ticket, "staging")
            self._state = "ready"

    def require_ready(self, ticket: StageTicket) -> None:
        with self._lock:
            self._require(ticket, "ready")

    def complete(self, ticket: StageTicket) -> None:
        with self._lock:
            self._require(ticket, "ready")
            self._state = "idle"
            self._ticket = None

    def abort(self, ticket: StageTicket, *, poison: bool = True) -> None:
        with self._lock:
            if self._ticket == ticket and self._state in {"staging", "ready"}:
                self._state = "poisoned" if poison else "idle"
                self._ticket = None

    def _require(self, ticket: StageTicket, state: str) -> None:
        if self._ticket != ticket:
            raise RuntimeError("stale staging generation rejected")
        if self._state != state:
            raise RuntimeError(f"use-before-completion rejected: {self._state} != {state}")


def _sha256(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _atomic_json(path: pathlib.Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    temporary.replace(path)


class CudaDriverKernel:
    """Compile, optionally instrument, load, and launch the staging PTX."""

    def __init__(self, evidence_dir: pathlib.Path) -> None:
        self.evidence_dir = evidence_dir.resolve()
        self.evidence_dir.mkdir(parents=True, exist_ok=True)
        self.source = pathlib.Path(__file__).with_name("kernels") / "capacity_staging.cu"
        self.raw_ptx = self.evidence_dir / "capacity-staging-raw.ptx"
        self.transformed_ptx = self.evidence_dir / "capacity-staging-transformed.ptx"
        self.pass_manifest = self.evidence_dir / "capacity-staging-pass-manifest.json"
        self.compile_manifest = self.evidence_dir / "capacity-staging-compile.json"
        self._driver: ctypes.CDLL | None = None
        self._launch_kernel: Any | None = None
        self._module = ctypes.c_void_p()
        self._function = ctypes.c_void_p()
        self._lock = threading.Lock()

    def _compile(self) -> pathlib.Path:
        frozen_ptx = os.environ.get("HBFSIM_CAPACITY_SELECTED_PTX")
        if frozen_ptx:
            selected = pathlib.Path(frozen_ptx).resolve(strict=True)
            expected = os.environ.get("HBFSIM_CAPACITY_SELECTED_PTX_SHA256")
            actual = _sha256(selected)
            if not expected or actual != expected:
                raise RuntimeError(
                    f"selected staging PTX SHA256 mismatch: {actual} != {expected}"
                )
            _atomic_json(
                self.compile_manifest,
                {
                    "schema_version": 1,
                    "status": "PASS",
                    "kernel": KERNEL_NAME,
                    "selected_ptx": str(selected),
                    "selected_ptx_sha256": actual,
                    "precompiled_by_static_cuda_gate": True,
                    "runtime_compilation": False,
                },
            )
            return selected
        nvcc = pathlib.Path(os.environ["HBFSIM_CAPACITY_NVCC"]).resolve(strict=True)
        architecture = os.environ.get("HBFSIM_CAPACITY_CUDA_ARCH", "compute_120")
        command = [
            str(nvcc),
            "-ptx",
            "-std=c++17",
            "-O3",
            f"-arch={architecture}",
            str(self.source.resolve()),
            "-o",
            str(self.raw_ptx),
        ]
        completed = subprocess.run(command, capture_output=True, text=True)
        if completed.returncode != 0:
            raise RuntimeError(f"staging PTX compile failed: {completed.stderr}")
        selected = self.raw_ptx
        pass_library = os.environ.get("HBFSIM_CAPACITY_PASS_LIBRARY")
        require_transform = os.environ.get("HBFSIM_CAPACITY_REQUIRE_TRANSFORM", "1") == "1"
        if pass_library:
            transformer = pathlib.Path(
                os.environ["HBFSIM_CAPACITY_PREPARE_PTX"]
            ).resolve(strict=True)
            environment = os.environ.copy()
            environment["HBFSIM_PASS_MANIFEST_PATH"] = str(self.pass_manifest)
            transformed = subprocess.run(
                [
                    sys.executable,
                    str(transformer),
                    "--transform-one",
                    "--pass-library",
                    str(pathlib.Path(pass_library).resolve(strict=True)),
                    "--kernel",
                    KERNEL_NAME,
                ],
                input=self.raw_ptx.read_bytes(),
                capture_output=True,
                env=environment,
            )
            if transformed.returncode != 0:
                raise RuntimeError(
                    "staging PTX instrumentation failed: "
                    + transformed.stderr.decode(errors="replace")
                )
            self.transformed_ptx.write_bytes(transformed.stdout)
            selected = self.transformed_ptx
        elif require_transform:
            raise RuntimeError("HBFSIM capacity PTX pass is required but unset")
        payload = {
            "schema_version": 1,
            "status": "PASS",
            "command": command,
            "kernel": KERNEL_NAME,
            "raw_ptx": str(self.raw_ptx),
            "raw_ptx_sha256": _sha256(self.raw_ptx),
            "selected_ptx": str(selected),
            "selected_ptx_sha256": _sha256(selected),
            "instrumented": selected == self.transformed_ptx,
            "pass_manifest": str(self.pass_manifest) if pass_library else None,
        }
        _atomic_json(self.compile_manifest, payload)
        return selected

    @staticmethod
    def _cuda_driver() -> ctypes.CDLL:
        candidates = (
            os.environ.get("HBFSIM_CAPACITY_CUDA_DRIVER"),
            "libcuda.so.1",
            "libcuda.so",
        )
        error: OSError | None = None
        for candidate in candidates:
            if not candidate:
                continue
            try:
                return ctypes.CDLL(candidate, mode=ctypes.RTLD_LOCAL)
            except OSError as exc:
                error = exc
        raise RuntimeError(f"unable to load CUDA driver: {error}")

    def _check(self, operation: str, status: int) -> None:
        if status == 0:
            return
        message = ctypes.c_char_p()
        if self._driver is not None:
            self._driver.cuGetErrorString(status, ctypes.byref(message))
        detail = message.value.decode(errors="replace") if message.value else "unknown"
        raise RuntimeError(f"{operation} failed: CUDA {status} ({detail})")

    @staticmethod
    def _interposed_launch_kernel() -> Any:
        """Resolve only the capacity launch through the explicit gate handle."""
        gate = get_launch_gate_library()
        try:
            launch = gate.cuLaunchKernel
        except AttributeError as exc:
            raise RuntimeError(
                "explicit HBFSim launch gate is missing cuLaunchKernel"
            ) from exc
        launch.argtypes = (
            ctypes.c_void_p,
            ctypes.c_uint,
            ctypes.c_uint,
            ctypes.c_uint,
            ctypes.c_uint,
            ctypes.c_uint,
            ctypes.c_uint,
            ctypes.c_uint,
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_void_p),
            ctypes.c_void_p,
        )
        launch.restype = ctypes.c_int
        return launch

    @staticmethod
    def _interposed_module_loader() -> tuple[Any, Any, Any]:
        """Resolve trusted static-PTX loading through the explicit gate."""
        gate = get_launch_gate_library()
        try:
            begin = gate.hbfsim_begin_module_load_from_ptx
            end = gate.hbfsim_end_module_load
            load = gate.cuModuleLoadDataEx
        except AttributeError as exc:
            raise RuntimeError(
                "explicit HBFSim launch gate is missing module provenance symbols"
            ) from exc
        begin.argtypes = (ctypes.c_char_p, ctypes.c_size_t)
        begin.restype = ctypes.c_uint64
        end.argtypes = (ctypes.c_uint64,)
        end.restype = None
        load.argtypes = (
            ctypes.POINTER(ctypes.c_void_p),
            ctypes.c_void_p,
            ctypes.c_uint,
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_void_p),
        )
        load.restype = ctypes.c_int
        return begin, end, load

    def ensure_loaded(self) -> None:
        with self._lock:
            if self._function.value:
                return
            selected = self._compile()
            driver = self._cuda_driver()
            driver.cuInit.argtypes = (ctypes.c_uint,)
            driver.cuInit.restype = ctypes.c_int
            driver.cuModuleGetFunction.argtypes = (
                ctypes.POINTER(ctypes.c_void_p),
                ctypes.c_void_p,
                ctypes.c_char_p,
            )
            driver.cuModuleGetFunction.restype = ctypes.c_int
            driver.cuGetErrorString.argtypes = (
                ctypes.c_int,
                ctypes.POINTER(ctypes.c_char_p),
            )
            driver.cuGetErrorString.restype = ctypes.c_int
            begin_module_load, end_module_load, module_load_data_ex = (
                self._interposed_module_loader()
            )
            launch_kernel = self._interposed_launch_kernel()
            self._driver = driver
            self._check("cuInit", int(driver.cuInit(0)))
            module = ctypes.c_void_p()
            ptx = selected.read_bytes()
            buffer = ctypes.create_string_buffer(ptx + b"\0")
            transaction = int(begin_module_load(ptx, len(ptx)))
            if transaction == 0:
                raise RuntimeError("launch gate rejected static PTX provenance")
            try:
                self._check(
                    "cuModuleLoadDataEx",
                    int(
                        module_load_data_ex(
                            ctypes.byref(module), buffer, 0, None, None
                        )
                    ),
                )
            finally:
                end_module_load(transaction)
            function = ctypes.c_void_p()
            self._check(
                "cuModuleGetFunction",
                int(
                    driver.cuModuleGetFunction(
                        ctypes.byref(function), module, KERNEL_NAME.encode()
                    )
                ),
            )
            self._module = module
            self._function = function
            self._launch_kernel = launch_kernel

    def copy(
        self,
        source_base: int,
        destination_base: int,
        source_offset_bytes: int,
        destination_offset_bytes: int,
        bytes_count: int,
        stream: int,
    ) -> None:
        if bytes_count <= 0 or bytes_count % BYTES_PER_BF16:
            raise ValueError(f"invalid BF16 byte count: {bytes_count}")
        if source_offset_bytes < 0 or destination_offset_bytes < 0:
            raise ValueError("staging offsets must be nonnegative")
        self.ensure_loaded()
        elements = bytes_count // BYTES_PER_BF16
        source_arg = ctypes.c_uint64(source_base)
        destination_arg = ctypes.c_uint64(destination_base)
        source_offset_arg = ctypes.c_uint64(source_offset_bytes)
        destination_offset_arg = ctypes.c_uint64(destination_offset_bytes)
        elements_arg = ctypes.c_uint64(elements)
        arguments = (ctypes.c_void_p * 5)(
            ctypes.cast(ctypes.byref(source_arg), ctypes.c_void_p),
            ctypes.cast(ctypes.byref(destination_arg), ctypes.c_void_p),
            ctypes.cast(ctypes.byref(source_offset_arg), ctypes.c_void_p),
            ctypes.cast(ctypes.byref(destination_offset_arg), ctypes.c_void_p),
            ctypes.cast(ctypes.byref(elements_arg), ctypes.c_void_p),
        )
        blocks, threads = capacity_copy_launch_shape(elements)
        assert self._driver is not None
        assert self._launch_kernel is not None
        self._check(
            "cuLaunchKernel",
            int(
                self._launch_kernel(
                    self._function,
                    blocks,
                    1,
                    1,
                    threads,
                    1,
                    1,
                    0,
                    ctypes.c_void_p(stream),
                    arguments,
                    None,
                )
            ),
        )


class StageLease:
    def __init__(
        self,
        owner: "CapacityStager",
        ticket: StageTicket,
        w13: Any,
        w2: Any,
        remap_table: Sequence[int],
        allocated_experts: int,
    ) -> None:
        self._owner = owner
        self.ticket = ticket
        self.w13 = w13
        self.w2 = w2
        self.remap_table = tuple(int(value) for value in remap_table)
        self.allocated_experts = allocated_experts
        self._completed = False

    @property
    def active_experts(self) -> int:
        return len(self.ticket.experts)

    def remap(self, topk_ids: Any, *, device: Any | None = None) -> Any:
        """Remap on the host and verify the exact H2D result before fused MoE."""
        import torch

        self._owner.state.require_ready(self.ticket)
        if topk_ids.dtype not in (torch.int32, torch.int64):
            raise RuntimeError(f"top-k IDs must be integer typed: {topk_ids.dtype}")
        topk_cpu = topk_ids.detach().to(device="cpu")
        if topk_cpu.numel() == 0 or topk_cpu.dim() == 0:
            raise RuntimeError("top-k IDs must be a non-empty tensor")
        width = int(topk_cpu.shape[-1])
        rows = topk_cpu.reshape(-1, width).tolist()
        try:
            remapped_rows = remap_nested_ids(rows, self.remap_table)
        except ValueError as exc:
            raise RuntimeError("top-k references an unstaged expert") from exc
        flat = [value for row in remapped_rows for value in row]
        if not flat or min(flat) < 0 or max(flat) >= self.active_experts:
            raise RuntimeError("host remap produced an invalid compact expert ID")
        expected_cpu = torch.tensor(
            remapped_rows, dtype=topk_cpu.dtype, device="cpu"
        ).reshape(topk_cpu.shape)
        target = torch.device(device) if device is not None else topk_ids.device
        remapped = expected_cpu.to(device=target)
        if target.type == "cuda":
            observed_cpu = remapped.detach().to(device="cpu")
            if not torch.equal(observed_cpu, expected_cpu):
                raise RuntimeError(
                    "compact expert ID H2D roundtrip failed before fused MoE"
                )
        return remapped

    def complete(self) -> None:
        if self._completed:
            raise RuntimeError("staging lease completed twice")
        self._owner.state.complete(self.ticket)
        self._completed = True

    def abort(self) -> None:
        if not self._completed:
            # A fused launch may have partially entered the CUDA stream before
            # raising.  Never recycle that workspace after an exceptional path.
            self._owner.state.abort(self.ticket, poison=True)
            self._completed = True


class CapacityStager:
    """One reusable compact workspace shared by every MoE layer in a worker."""

    def __init__(self, *, max_active_experts: int = MAX_EXPERTS) -> None:
        if max_active_experts <= 0 or max_active_experts > MAX_EXPERTS:
            raise ValueError("max_active_experts must be in [1, 128]")
        self.max_active_experts = max_active_experts
        self.state = StageStateMachine()
        self._kernel: CudaDriverKernel | None = None
        self._w13: Any | None = None
        self._w2: Any | None = None
        self._workspace_device: str | None = None
        self._lock = threading.RLock()

    @staticmethod
    def _verify_tensor_copy(
        *,
        ticket: StageTicket,
        tensor: Any,
        destination_tensor: Any,
        destination_offset: int,
        mapping: Any,
    ) -> dict[str, Any]:
        """Copy the completed destination to CPU and compare every byte."""
        import torch

        descriptor = os.open(mapping.path, os.O_RDONLY)
        try:
            expected = os.pread(
                descriptor, tensor.bytes, tensor.file_offset_begin
            )
        finally:
            os.close(descriptor)
        if len(expected) != tensor.bytes:
            raise RuntimeError(f"short oracle read for {tensor.tensor}")
        device_bytes = destination_tensor.view(torch.uint8).reshape(-1).narrow(
            0, destination_offset, tensor.bytes
        )
        actual = device_bytes.detach().cpu().contiguous().numpy().tobytes()
        comparison = compare_staged_bytes(actual, expected)
        record = {
            "generation": ticket.generation,
            "layer": ticket.layer,
            "expert_id": tensor.expert_id,
            "tensor": tensor.tensor,
            "projection": tensor.projection,
            "bytes": tensor.bytes,
            "source_shard": tensor.shard,
            "source_file_offset": tensor.file_offset_begin,
            **comparison,
        }
        return record

    @staticmethod
    def _append_verification(
        ticket: StageTicket,
        records: list[dict[str, Any]],
        *,
        allocated_experts: int,
    ) -> dict[str, Any]:
        root = pathlib.Path(os.environ["HBFSIM_CAPACITY_REPORT_DIR"])
        output = root / "e6-staging-verification.jsonl"
        generations = root / "e6-staging-generations.jsonl"
        output.parent.mkdir(parents=True, exist_ok=True)
        expected = {
            (expert_id, projection)
            for expert_id in ticket.experts
            for projection in ("gate_proj", "up_proj", "down_proj")
        }
        actual = [
            (int(record["expert_id"]), str(record["projection"]))
            for record in records
        ]
        summary = {
            "schema_version": 1,
            "generation": ticket.generation,
            "layer": ticket.layer,
            "experts": list(ticket.experts),
            "active_experts": len(ticket.experts),
            "allocated_experts": allocated_experts,
            "expected_records": len(expected),
            "actual_records": len(records),
            "unique_records": len(set(actual)),
            "record_set_complete": set(actual) == expected,
            "all_records_bit_exact": all(
                record.get("status") == "PASS" for record in records
            ),
        }
        summary["status"] = (
            "PASS"
            if summary["record_set_complete"]
            and summary["unique_records"] == summary["actual_records"]
            and summary["all_records_bit_exact"]
            else "FAIL"
        )
        with _EVIDENCE_LOCK:
            with output.open("a", encoding="utf-8") as handle:
                for record in records:
                    handle.write(json.dumps(record, sort_keys=True) + "\n")
            with generations.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(summary, sort_keys=True) + "\n")
        return summary

    def _ensure_workspace(self, device: Any) -> None:
        import torch

        resolved = torch.device(device)
        if resolved.type != "cuda":
            raise RuntimeError(f"capacity workspace requires CUDA, got {resolved}")
        if resolved.index is None:
            resolved = torch.device("cuda", torch.cuda.current_device())
        if self._w13 is None:
            self._w13 = torch.empty(
                (self.max_active_experts, 2 * INTERMEDIATE_SIZE, HIDDEN_SIZE),
                dtype=torch.bfloat16,
                device=resolved,
            )
            self._w2 = torch.empty(
                (self.max_active_experts, HIDDEN_SIZE, INTERMEDIATE_SIZE),
                dtype=torch.bfloat16,
                device=resolved,
            )
            self._workspace_device = str(resolved)
            evidence = pathlib.Path(os.environ["HBFSIM_CAPACITY_REPORT_DIR"]) / "ptx"
            self._kernel = CudaDriverKernel(evidence)
        elif self._workspace_device != str(resolved):
            raise RuntimeError(
                "capacity workspace device changed: "
                f"{self._workspace_device} != {resolved}"
            )

    def stage(
        self,
        layer: int,
        expert_ids: Sequence[int],
        *,
        device: Any,
        minimum_fused_slots: int = 1,
    ) -> StageLease:
        import torch

        unique = stable_unique(expert_ids)
        if len(unique) > self.max_active_experts:
            raise RuntimeError(
                f"active experts {len(unique)} exceed workspace {self.max_active_experts}"
            )
        allocated_experts = fused_expert_slot_count(
            len(unique),
            minimum_fused_slots,
            max_experts=self.max_active_experts,
        )
        ticket = self.state.begin(layer, unique)
        try:
            with self._lock:
                self._ensure_workspace(device)
                assert self._w13 is not None and self._w2 is not None
                assert self._kernel is not None
                from adapters.vllm_capacity.capacity_loader import get_inventory
                from adapters.vllm_capacity.capacity_runtime import get_capacity_runtime

                inventory = get_inventory()
                runtime = get_capacity_runtime()
                stream = torch.cuda.current_stream(device=device)
                stream_handle = int(stream.cuda_stream)
                copy_completion = torch.cuda.Event(
                    enable_timing=False, blocking=True
                )
                verification_inputs: list[tuple[Any, Any, int, Any]] = []
                for slot, expert_id in enumerate(unique):
                    expert = inventory.expert(layer, expert_id)
                    if expert.total_bytes != TOTAL_BYTES_PER_EXPERT:
                        raise RuntimeError("unexpected per-expert byte size")
                    w13_tensor = self._w13[slot]
                    w2_tensor = self._w2[slot]
                    w13_base = int(w13_tensor.data_ptr())
                    w2_base = int(w2_tensor.data_ptr())
                    copies = (
                        (expert.gate, w13_tensor, w13_base, 0),
                        (expert.up, w13_tensor, w13_base, expert.gate.bytes),
                        (expert.down, w2_tensor, w2_base, 0),
                    )
                    for tensor, destination_tensor, destination_base, destination_offset in copies:
                        mapping = runtime.shard_mapping(tensor.shard)
                        runtime.logical_address(
                            tensor.shard, tensor.file_offset_begin, tensor.bytes
                        )
                        try:
                            self._kernel.copy(
                                mapping.logical_address,
                                destination_base,
                                tensor.file_offset_begin,
                                destination_offset,
                                tensor.bytes,
                                stream_handle,
                            )
                        except BaseException as exc:
                            raise RuntimeError(
                                "capacity staging tensor failed: "
                                f"layer={layer} expert={expert_id} "
                                f"tensor={tensor.tensor}"
                            ) from exc
                        verification_inputs.append(
                            (tensor, destination_tensor, destination_offset, mapping)
                        )
                try:
                    copy_completion.record(stream)
                    copy_completion.synchronize()
                    runtime.flush()
                except BaseException as exc:
                    raise RuntimeError(
                        "capacity staging generation completion failed: "
                        f"layer={layer} experts={unique}"
                    ) from exc
                if os.environ.get("HBFSIM_CAPACITY_VERIFY_STAGE", "1") != "1":
                    raise RuntimeError("per-stage checksum/probe verification is mandatory")
                verification_records = [
                    self._verify_tensor_copy(
                        ticket=ticket,
                        tensor=tensor,
                        destination_tensor=destination_tensor,
                        destination_offset=destination_offset,
                        mapping=mapping,
                    )
                    for tensor, destination_tensor, destination_offset, mapping in verification_inputs
                ]
                verification_summary = self._append_verification(
                    ticket,
                    verification_records,
                    allocated_experts=allocated_experts,
                )
                failures = [
                    record for record in verification_records if record["status"] != "PASS"
                ]
                if failures or verification_summary["status"] != "PASS":
                    raise RuntimeError(
                        "staging verification failed: "
                        + (
                            failures[0]["tensor"]
                            if failures
                            else repr(verification_summary)
                        )
                    )
                remap = tuple(build_remap(unique))
                self.state.mark_ready(ticket)
                return StageLease(
                    self,
                    ticket,
                    self._w13[:allocated_experts],
                    self._w2[:allocated_experts],
                    remap,
                    allocated_experts,
                )
        except BaseException:
            self.state.abort(ticket, poison=True)
            raise


_STAGER_LOCK = threading.Lock()
_STAGER: CapacityStager | None = None


def get_capacity_stager() -> CapacityStager:
    global _STAGER
    with _STAGER_LOCK:
        if _STAGER is None:
            maximum = int(os.environ.get("HBFSIM_CAPACITY_MAX_ACTIVE_EXPERTS", "128"))
            _STAGER = CapacityStager(max_active_experts=maximum)
        return _STAGER


__all__ = [
    "CapacityStager",
    "StageLease",
    "StageStateMachine",
    "StageTicket",
    "build_remap",
    "capacity_copy_launch_shape",
    "compare_staged_bytes",
    "fused_expert_slot_count",
    "get_capacity_stager",
    "remap_nested_ids",
    "stable_unique",
]
