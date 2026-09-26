"""Default-off exact 3/45/49 Olmoe selected-consumer adapter."""
from __future__ import annotations

import ctypes
import hashlib
import json
import os
from pathlib import Path
import threading
import time
from typing import Any, Callable

_installed = False
_tls = threading.local()
_models: dict[int, dict[str, Any]] = {}
_receipt_lock = threading.Lock()
_receipt_path: Path | None = None
_ROW_COUNTS = {"mixed3": 3, "remaining45": 45, "added49": 49}
_CATEGORIES = {"qkv_proj", "o_proj", "router", "lm_head"}

def _need(ok: bool, message: str) -> None:
    if not ok:
        raise RuntimeError(message)

def _directory() -> Path:
    value = os.environ.get("HBFSIM_COMBINED_MODEL_RECEIPT_DIR")
    _need(bool(value), "combined receipt directory not configured")
    path = Path(value)
    _need(path.is_dir(), "combined receipt directory absent")
    return path

def _emit(event: str, **fields: Any) -> None:
    global _receipt_path
    row = {"schema_version": 1, "event": event, "pid": os.getpid(),
           "time_ns": time.time_ns(), "monotonic_ns": time.monotonic_ns(),
           "os_thread_id": threading.get_native_id(), **fields}
    data = (json.dumps(row, sort_keys=True) + "\n").encode()
    with _receipt_lock:
        if _receipt_path is None:
            _receipt_path = _directory() / f"worker-{os.getpid()}.jsonl"
            fd = os.open(_receipt_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        else:
            fd = os.open(_receipt_path, os.O_WRONLY | os.O_APPEND)
        try:
            view = memoryview(data)
            while view:
                view = view[os.write(fd, view):]
            os.fsync(fd)
        finally:
            os.close(fd)

def _write_new(name: str, data: bytes) -> None:
    fd = os.open(_directory() / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        view = memoryview(data)
        while view:
            view = view[os.write(fd, view):]
        os.fsync(fd)
    finally:
        os.close(fd)

def _manifest() -> dict[str, Any]:
    path = os.environ.get("HBFSIM_COMBINED_SCOPE_MANIFEST")
    pinned = os.environ.get("HBFSIM_COMBINED_SCOPE_SHA256")
    _need(bool(path) and bool(pinned), "combined scope manifest identity missing")
    raw = Path(path).read_bytes()
    _need(hashlib.sha256(raw).hexdigest() == pinned, "combined scope manifest changed")
    data = json.loads(raw)
    scope = data.get("scope")
    rows = data.get("targets")
    _need(data.get("schema") == "hbfsim.combined_selected_scope.v1" and
          scope in _ROW_COUNTS and isinstance(rows, list) and
          len(rows) == _ROW_COUNTS[scope] == data.get("selected_count") and
          data.get("final_loader_registration_count") == 147 and
          data.get("old98_registration_count") == 98,
          "combined scope manifest contract")
    aliases: set[str] = set()
    for row in rows:
        cat, layer = row.get("category"), row.get("layer")
        alias = row.get("alias")
        expected = "lm_head.weight" if cat == "lm_head" else (
            f"model.layers.{layer}.self_attn.qkv_proj.weight" if cat == "qkv_proj" else
            f"model.layers.{layer}.self_attn.o_proj.weight" if cat == "o_proj" else
            f"model.layers.{layer}.mlp.gate.weight")
        _need(cat in _CATEGORIES and isinstance(alias, str) and alias == expected and
              alias not in aliases and ((cat == "lm_head" and layer is None) or
              (cat != "lm_head" and isinstance(layer, int) and 0 <= layer < 16)),
              "duplicate/unsupported target alias")
        aliases.add(alias)
        shapes = {"qkv_proj": ((6144, 2048), 25165824, (1, 6144), 12288, "li6"),
                  "o_proj": ((2048, 2048), 8388608, (1, 2048), 4096, "li6"),
                  "router": ((64, 2048), 262144, (1, 64), 128, "li7"),
                  "lm_head": ((50304, 2048), 206045184, (1, 50304), 100608, "li6")}
        shape, size, outshape, outbytes, profile = shapes[cat]
        _need(tuple(row.get("shape", ())) == shape and row.get("storage_bytes") == size and
              tuple(row.get("output_shape", ())) == outshape and
              row.get("output_bytes") == outbytes and row.get("profile") == profile,
              "target shape/profile mismatch")
    wanted = {"mixed3": {(c, 0) for c in ("qkv_proj", "o_proj", "router")},
              "remaining45": {(c, i) for i in range(1, 16) for c in ("qkv_proj", "o_proj", "router")},
              "added49": {(c, i) for i in range(16) for c in ("qkv_proj", "o_proj", "router")} | {("lm_head", None)}}
    _need({(r["category"], r["layer"]) for r in rows} == wanted[scope],
          "scope target set changed")
    return data

def _agent() -> tuple[Any, Any]:
    process = ctypes.CDLL(None)
    select = process.bpftime_nv_qkv_select_weight_storage_v1
    select.argtypes = (ctypes.c_uint64, ctypes.c_size_t)
    select.restype = ctypes.c_int
    end = process.bpftime_nv_qkv_end_selected_call_v1
    end.argtypes = ()
    end.restype = ctypes.c_int
    return select, end

def _raw(tensor: Any, torch: Any) -> bytes:
    return tensor.detach().contiguous().view(torch.uint8).cpu().numpy().tobytes()

def _weight(row: dict[str, Any], tensor: Any, torch: Any) -> int:
    size = row["storage_bytes"]
    _need(isinstance(tensor, torch.Tensor) and
          tuple(tensor.shape) == tuple(row["shape"]) and
          tensor.dtype == torch.bfloat16 and tensor.device.type == "cuda" and
          tensor.storage_offset() == 0 and tensor.numel() * tensor.element_size() == size and
          tensor.untyped_storage().nbytes() == size and
          tensor.untyped_storage().data_ptr() == tensor.data_ptr(),
          f"{row['alias']} is not full BF16 storage")
    return int(tensor.data_ptr())

def _output(row: dict[str, Any], result: Any, torch: Any) -> tuple[Any, Any]:
    cat = row["category"]
    if cat in ("qkv_proj", "o_proj"):
        _need(isinstance(result, tuple) and len(result) == 2 and result[1] is None,
              f"{cat} tuple changed")
        tensor, auxiliary = result
    elif cat == "router":
        _need(isinstance(result, tuple) and len(result) == 2,
              "router tuple changed")
        tensor, auxiliary = result
    else:
        tensor, auxiliary = result, None
    _need(isinstance(tensor, torch.Tensor) and
          tuple(tensor.shape) == tuple(row["output_shape"]) and
          tensor.dtype == torch.bfloat16 and tensor.device.type == "cuda",
          f"{cat} output tensor changed")
    return tensor, auxiliary

def _selected_call(state: dict[str, Any], item: dict[str, Any], activation: Any,
                   run: Callable[[Any], Any], torch: Any) -> Any:
    row = item["row"]
    alias = row["alias"]
    _need(getattr(_tls, "model", None) is state or row["category"] == "lm_head" and
          state["head_pending"] and state["decode_tid"] == threading.get_native_id(),
          "selected call outside scheduled request/thread")
    _need(not getattr(_tls, "inside_selected", False) and not item["done"],
          "nested or duplicate selected child")
    _need(isinstance(activation, torch.Tensor) and tuple(activation.shape) == (1, 2048) and
          activation.dtype == torch.bfloat16 and activation.device.type == "cuda",
          "selected activation changed")
    ptr = _weight(row, item["weight"], torch)
    _need(ptr == item["ptr"], "selected storage address changed")
    key = f"worker-{os.getpid()}-{row['category']}-{row['layer'] if row['layer'] is not None else 'head'}"
    activation_bytes = _raw(activation, torch)
    _need(len(activation_bytes) == 4096, "activation byte extent changed")
    _write_new(key + "-activation.bin", activation_bytes)
    order = state["next_order"]
    state["next_order"] += 1
    fields = {"alias": alias, "category": row["category"], "layer": row["layer"],
              "profile": row["profile"], "request_id": state["request_id"],
              "weight_ptr": hex(ptr), "weight_bytes": row["storage_bytes"],
              "input_ptr": hex(activation.data_ptr()),
              "input_sha256": hashlib.sha256(activation_bytes).hexdigest(),
              "stream_handle": hex(int(torch.cuda.current_stream().cuda_stream)),
              "selected_order": order}
    _tls.inside_selected = True
    try:
        _emit("native_enter", **fields)
        native = run(activation.clone())
        _emit("native_return", **fields)
        native_tensor, native_aux = _output(row, native, torch)
        if row["category"] == "lm_head":
            _need(int(state["model"].logits_processor.org_vocab_size) == 50304,
                  "head vocabulary contract changed")
        native_bytes = _raw(native_tensor, torch)
        _need(len(native_bytes) == row["output_bytes"], "native output extent changed")
        _write_new(key + "-native.bin", native_bytes)
        _emit("native_output_saved", output_bytes=len(native_bytes), **fields)
        select, end = _agent()
        _emit("select_enter", **fields)
        select_rc = select(ptr, row["storage_bytes"])
        _emit("select_return", select_rc=select_rc, **fields)
        _need(select_rc == 0, f"selection refused: {select_rc}")
        candidate = None
        end_rc = None
        candidate_error = None
        try:
            _emit("candidate_enter", **fields)
            candidate = run(activation)
            _emit("candidate_return", **fields)
        except BaseException as exc:
            candidate_error = exc
            _emit("candidate_exception", exception_type=type(exc).__name__,
                  exception_text=str(exc), select_rc=select_rc, **fields)
            raise
        finally:
            _emit("end_enter", **fields)
            try:
                end_rc = end()
            except BaseException as exc:
                _emit("end_exception", exception_type=type(exc).__name__,
                      exception_text=str(exc), select_rc=select_rc,
                      candidate_exception_type=(type(candidate_error).__name__
                                                if candidate_error is not None else None), **fields)
                if candidate_error is None:
                    raise
                if hasattr(candidate_error, "add_note"):
                    candidate_error.add_note(
                        f"selected end also raised {type(exc).__name__}: {exc}")
            else:
                _emit("end_return", end_rc=end_rc, **fields)
        _need(end_rc == 0, f"selected end refused: {end_rc}")
        candidate_tensor, candidate_aux = _output(row, candidate, torch)
        if row["category"] == "router":
            _need(candidate_aux is native_aux, "router bias identity changed")
        _need(candidate_tensor.device == native_tensor.device,
              "native/candidate output devices differ")
        _need(candidate_tensor.data_ptr() != native_tensor.data_ptr(),
              "native/candidate outputs alias")
        torch.cuda.current_stream().synchronize()
        candidate_bytes = _raw(candidate_tensor, torch)
        _write_new(key + "-candidate.bin", candidate_bytes)
        equal = candidate_bytes == native_bytes and len(candidate_bytes) == row["output_bytes"]
        _emit("selected_output", status="BYTE_EQUAL" if equal else "BYTE_MISMATCH",
              select_rc=select_rc, end_rc=end_rc, output_shape=list(candidate_tensor.shape),
              output_dtype=str(candidate_tensor.dtype), output_bytes=len(candidate_bytes),
              native_output_ptr=hex(native_tensor.data_ptr()),
              candidate_output_ptr=hex(candidate_tensor.data_ptr()),
              native_sha256=hashlib.sha256(native_bytes).hexdigest(),
              candidate_sha256=hashlib.sha256(candidate_bytes).hexdigest(), **fields)
        _need(equal, "selected output differs from native reference")
        item["done"] = True
        return candidate
    finally:
        _tls.inside_selected = False

def _child(model: Any, row: dict[str, Any]) -> Any:
    if row["category"] == "lm_head": return model.lm_head
    layer = model.model.layers[row["layer"]]
    if row["category"] == "qkv_proj": return layer.self_attn.qkv_proj
    if row["category"] == "o_proj": return layer.self_attn.o_proj
    return layer.mlp.gate

def _install(model: Any, manifest: dict[str, Any], torch: Any) -> None:
    _need(not _models, "combined adapter expects one loaded model")
    state: dict[str, Any] = {"model": model, "scope": manifest["scope"],
        "items": {}, "prefill": False, "request_id": None, "decode": False,
        "decode_tid": None, "head_pending": False, "complete": False,
        "next_order": 1}
    pointers: set[int] = set()
    for row in manifest["targets"]:
        try: child = _child(model, row)
        except (AttributeError, IndexError, TypeError) as exc:
            raise RuntimeError(f"selected child missing: {row['alias']}") from exc
        weight = child.weight
        ptr = _weight(row, weight, torch)
        _need(ptr not in pointers and row["alias"] not in state["items"],
              "selected storage aliases another target")
        pointers.add(ptr)
        original = model.compute_logits if row["category"] == "lm_head" else child.forward
        item = {"row": row, "weight": weight, "ptr": ptr, "done": False,
                "original": original}
        state["items"][row["alias"]] = item
        if row["category"] == "lm_head":
            def head_forward(hidden: Any, _item: dict[str, Any] = item) -> Any:
                if not state["head_pending"]:
                    _need(not state["decode"] or not _item["done"],
                          "second logits after selected head")
                    return _item["original"](hidden)
                result = _selected_call(state, _item, hidden, _item["original"], torch)
                state["head_pending"] = False
                return result
            model.compute_logits = head_forward
        else:
            def forward(hidden: Any, *args: Any, _item: dict[str, Any] = item,
                        **kwargs: Any) -> Any:
                run = lambda x: _item["original"](x, *args, **kwargs)
                if getattr(_tls, "model", None) is not state:
                    return run(hidden)
                return _selected_call(state, _item, hidden, run, torch)
            child.forward = forward
    _models[id(model)] = state
    _emit("installed", status="READY", scope=state["scope"],
          selected_storage_count=len(state["items"]),
          targets=[{"alias":x["row"]["alias"],"profile":x["row"]["profile"],
                    "base":hex(x["ptr"]),"bytes":x["row"]["storage_bytes"]}
                   for x in state["items"].values()])

def _positions(input_ids: Any, positions: Any, torch: Any) -> list[int] | None:
    if not isinstance(input_ids, torch.Tensor) or not isinstance(positions, torch.Tensor):
        return None
    if positions.numel() != input_ids.numel() or positions.numel() not in (1, 2):
        return None
    return [int(x) for x in positions.detach().reshape(-1).cpu().tolist()]

def register() -> None:
    """vllm.general_plugins entry; import remains inert without exact opt-in."""
    global _installed
    if os.environ.get("HBFSIM_COMBINED_MODEL_PLUGIN_V1") != "1" or _installed:
        return
    for key in ("HBFSIM_QKV_MODEL_PLUGIN_V1", "HBFSIM_LI6_MODEL_PLUGIN_V1",
                "HBFSIM_ROUTER_MODEL_PLUGIN_V1"):
        _need(os.environ.get(key) != "1", f"conflicting plugin activation: {key}")
    manifest = _manifest()
    import hbfsim_loader
    hbfsim_loader.register()
    from vllm.v1.worker.gpu_model_runner import GPUModelRunner
    import torch
    old_load = hbfsim_loader.HbfSimModelLoader.load_model
    old_forward = GPUModelRunner._model_forward
    old_execute = GPUModelRunner.execute_model

    def load_model(self: Any, *args: Any, **kwargs: Any) -> Any:
        model = old_load(self, *args, **kwargs)
        _install(model, manifest, torch)
        return model

    def model_forward(self: Any, *args: Any, **kwargs: Any) -> Any:
        model = self.model
        state = _models.get(id(model))
        if state is None:
            _need(not _models, "scheduled model differs from registered combined model")
            return old_forward(self, *args, **kwargs)
        ids = kwargs.get("input_ids", args[0] if args else None)
        positions = kwargs.get("positions", args[1] if len(args) > 1 else None)
        pos = _positions(ids, positions, torch)
        batch = self.input_batch
        requests = list(batch.req_ids)
        one = int(batch.num_reqs) == 1 and len(requests) == 1
        spec = bool(batch.is_spec_decode or self.vllm_config.speculative_config)
        if pos == [0, 1] and not state["prefill"] and one and not spec:
            result = old_forward(self, *args, **kwargs)
            state["prefill"] = True
            state["request_id"] = requests[0]
            _emit("scheduled_prefill", request_id=requests[0], positions=pos,
                  scope=state["scope"])
            return result
        if state["prefill"] and pos is not None and len(pos) == 1 and pos[0] > 0 and not state["decode"]:
            _need(one and requests[0] == state["request_id"] and not spec,
                  "selected decode request identity changed")
            _need(getattr(_tls, "model", None) is None, "nested selected decode")
            state["decode"] = True
            state["decode_tid"] = threading.get_native_id()
            state["head_pending"] = "lm_head.weight" in state["items"]
            _emit("scheduled_decode", request_id=requests[0], positions=pos,
                  selected_count=len(state["items"]), scope=state["scope"])
            _tls.model = state
            try:
                result = old_forward(self, *args, **kwargs)
                _need(all(x["done"] for x in state["items"].values()
                          if x["row"]["category"] != "lm_head"),
                      "selected decode lacked a forward child")
                return result
            finally:
                _tls.model = None
        _need(not state["decode"] or state["complete"],
              "selected decode incomplete")
        return old_forward(self, *args, **kwargs)

    def execute_model(self: Any, *args: Any, **kwargs: Any) -> Any:
        model = getattr(self, "model", None)
        state = _models.get(id(model)) if model is not None else None
        had_decode = bool(state and state["decode"])
        result = old_execute(self, *args, **kwargs)
        if state and state["decode"] and not had_decode:
            _need(not state["head_pending"] and
                  all(x["done"] for x in state["items"].values()),
                  "execute_model lacked selected completion")
            state["complete"] = True
            _emit("selected_execute_complete", status="ALL_SELECTED_COMPLETE",
                  request_id=state["request_id"], scope=state["scope"],
                  selected_count=len(state["items"]))
        return result

    hbfsim_loader.HbfSimModelLoader.load_model = load_model
    GPUModelRunner._model_forward = model_forward
    GPUModelRunner.execute_model = execute_model
    _installed = True
