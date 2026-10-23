"""Default-off CPU-prepared sweep measurement candidate; no GPU admission claim."""
from __future__ import annotations

import ctypes
import hashlib
import json
import os
from pathlib import Path
import re
import threading
import time
from typing import Any, Callable

from .scope_contract import make_scopes, sha

_installed = False
_tls = threading.local()
_models: dict[int, dict[str, Any]] = {}
_receipt_lock = threading.Lock()
_receipt_path: Path | None = None
_LAYER_COUNTS = {4, 8, 12, 16}
_CATEGORIES = {"qkv_proj", "o_proj", "router", "lm_head"}
_LEDGER_SHA256 = "666fb5bb13bb4206f30bd74986708ec0dc6a34d01ebd22d0e470c76dc10b4fda"
_FROZEN_SCOPE_SHA256 = "857a66c8ad34ab7a624894bb38426c106256a5eb43fa1516ad3f7f9909793ea1"

def _need(ok: bool, message: str) -> None:
    if not ok:
        raise RuntimeError(message)

def _directory() -> Path:
    value = os.environ.get("HBFSIM_SWEEP_MEASUREMENT_RECEIPT_DIR")
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

def _manifest() -> dict[str, Any]:
    path = os.environ.get("HBFSIM_SWEEP_SCOPE_MANIFEST")
    pinned = os.environ.get("HBFSIM_SWEEP_SCOPE_SHA256")
    _need(bool(path) and bool(pinned), "combined scope manifest identity missing")
    raw = Path(path).read_bytes()
    _need(hashlib.sha256(raw).hexdigest() == pinned, "combined scope manifest changed")
    data = json.loads(raw)
    scope = data.get("scope")
    rows = data.get("targets")
    n = data.get("layer_count")
    registered = data.get("registration_aliases")
    _need(data.get("schema") == "hbfsim.coverage_sweep_measurement_scope.v1" and
          type(n) is int and n in _LAYER_COUNTS and scope == f"layers-{n}" and
          isinstance(rows, list) and len(rows) == 3*n+1 == data.get("selected_count") and
          isinstance(registered, list) and len(registered) == 9*n+3 == data.get("registered_count") and
          len(set(registered)) == len(registered) and
          isinstance(data.get("registered_bytes"), int) and data["registered_bytes"] > 0 and
          data.get("model_total_storage_bytes") == 13_838_323_712 and
          data.get("source_ledger_sha256") == _LEDGER_SHA256 and
          data.get("source_frozen_scope_sha256") == _FROZEN_SCOPE_SHA256,
          "sweep manifest contract")
    expected_registered = {"model.embed_tokens.weight", "model.norm.weight", "lm_head.weight"}
    for i in range(n):
        base = f"model.layers.{i}"
        expected_registered.update((
            f"{base}.mlp.experts.w13_weight", f"{base}.mlp.experts.w2_weight",
            f"{base}.input_layernorm.weight", f"{base}.post_attention_layernorm.weight",
            f"{base}.self_attn.q_norm.weight", f"{base}.self_attn.k_norm.weight",
            f"{base}.self_attn.qkv_proj.weight", f"{base}.self_attn.o_proj.weight",
            f"{base}.mlp.gate.weight"))
    _need(set(registered) == expected_registered and
          data.get("global_aliases") == ["model.embed_tokens.weight", "model.norm.weight", "lm_head.weight"] and
          data.get("hbf_weight_selection") == "include" and
          data.get("include_patterns") == ["^" + re.escape(alias) + "$" for alias in sorted(registered)],
          "registration selection differs from pinned ledger scope")
    aliases: set[str] = set()
    for row in rows:
        cat, layer = row.get("category"), row.get("layer")
        alias = row.get("alias")
        expected = "lm_head.weight" if cat == "lm_head" else (
            f"model.layers.{layer}.self_attn.qkv_proj.weight" if cat == "qkv_proj" else
            f"model.layers.{layer}.self_attn.o_proj.weight" if cat == "o_proj" else
            f"model.layers.{layer}.mlp.gate.weight")
        _need(cat in _CATEGORIES and isinstance(alias, str) and alias == expected and
              alias not in aliases and alias in registered and ((cat == "lm_head" and layer is None) or
              (cat != "lm_head" and type(layer) is int and 0 <= layer < n)),
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
    wanted = {(c, i) for i in range(n) for c in ("qkv_proj", "o_proj", "router")} | {("lm_head", None)}
    _need({(r["category"], r["layer"]) for r in rows} == wanted,
          "scope target set changed")
    pinned = Path(__file__).resolve().parent / "pinned"
    ledger_bytes = (pinned / "TARGET_LEDGER.json").read_bytes()
    frozen_bytes = (pinned / "scope-added49.json").read_bytes()
    _need(sha(ledger_bytes) == _LEDGER_SHA256 and
          sha(frozen_bytes) == _FROZEN_SCOPE_SHA256,
          "pinned ledger or frozen target identity changed")
    expected = next(row for row in make_scopes(ledger_bytes, frozen_bytes)
                    if row["layer_count"] == n)
    _need(data == expected,
          "scope differs from ledger-derived bytes, geometry or native driver metadata")
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
    order = state["next_order"]
    state["next_order"] += 1
    fields = {"alias": alias, "category": row["category"], "layer": row["layer"],
              "profile": row["profile"], "request_id": state["request_id"],
              "weight_ptr": hex(ptr), "weight_bytes": row["storage_bytes"],
              "input_ptr": hex(activation.data_ptr()),
              "input_shape": list(activation.shape),
              "input_dtype": str(activation.dtype),
              "stream_handle": hex(int(torch.cuda.current_stream().cuda_stream)),
              "selected_order": order}
    _tls.inside_selected = True
    try:
        if row["category"] == "lm_head":
            _need(int(state["model"].logits_processor.org_vocab_size) == 50304,
                  "head vocabulary contract changed")
        select, end = _agent()
        _emit("select_enter", **fields)
        select_rc = select(ptr, row["storage_bytes"])
        if select_rc != 0:
            refused = RuntimeError(f"selection refused: {select_rc}")
            try:
                _emit("select_return", select_rc=select_rc, **fields)
            except BaseException as log_error:
                refused.add_note(f"select refusal logging also failed: {log_error!r}")
            raise refused
        # Once select returns success, end must execute exactly once even if
        # logging, the candidate, or later validation raises.
        candidate = None
        end_rc = None
        primary_error: BaseException | None = None
        secondary_errors: list[BaseException] = []
        candidate_started = False
        try:
            _emit("select_return", select_rc=select_rc, **fields)
            _emit("candidate_enter", **fields)
            candidate_started = True
            candidate = run(activation)
            _emit("candidate_return", **fields)
        except BaseException as exc:
            primary_error = exc
            if candidate_started:
                try:
                    _emit("candidate_exception", exception_type=type(exc).__name__,
                          exception_text=str(exc), select_rc=select_rc, **fields)
                except BaseException as log_error:
                    secondary_errors.append(log_error)
        finally:
            try:
                _emit("end_enter", **fields)
            except BaseException as log_error:
                secondary_errors.append(log_error)
            try:
                end_rc = end()
            except BaseException as exc:
                secondary_errors.append(exc)
                try:
                    _emit("end_exception", exception_type=type(exc).__name__,
                          exception_text=str(exc), select_rc=select_rc,
                          candidate_exception_type=(type(primary_error).__name__
                                                    if primary_error is not None else None), **fields)
                except BaseException as log_error:
                    secondary_errors.append(log_error)
            else:
                try:
                    _emit("end_return", end_rc=end_rc, **fields)
                except BaseException as log_error:
                    secondary_errors.append(log_error)
        if primary_error is not None:
            for extra in secondary_errors:
                primary_error.add_note(f"cleanup or logging also failed: {extra!r}")
            raise primary_error.with_traceback(primary_error.__traceback__)
        if secondary_errors:
            first, *rest = secondary_errors
            for extra in rest:
                first.add_note(f"cleanup or logging also failed: {extra!r}")
            raise first.with_traceback(first.__traceback__)
        _need(end_rc == 0, f"selected end refused: {end_rc}")
        candidate_tensor, _ = _output(row, candidate, torch)
        _emit("selected_output", status="CANDIDATE_ONLY",
              select_rc=select_rc, end_rc=end_rc, output_shape=list(candidate_tensor.shape),
              output_dtype=str(candidate_tensor.dtype), output_bytes=row["output_bytes"],
              candidate_output_ptr=hex(candidate_tensor.data_ptr()), **fields)
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
    if os.environ.get("HBFSIM_SWEEP_MEASUREMENT_PLUGIN_V1") != "1" or _installed:
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
