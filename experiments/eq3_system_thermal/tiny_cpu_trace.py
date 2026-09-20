#!/usr/bin/env python3
"""Deterministic tiny Qwen2-style CPU forward trace; not a performance model."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path

for _name in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS",
              "BLIS_NUM_THREADS", "VECLIB_MAXIMUM_THREADS", "NUMEXPR_NUM_THREADS"):
    os.environ.setdefault(_name, "1")

import numpy as np


HERE = Path(__file__).resolve().parent
CATALOG = HERE.parents[0] / "eq3_maintenance" / "sources" / "qwen2_5_weight_models.json"
ALIGNMENT = 1024 * 1024


def _canonical(value) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      allow_nan=False).encode("utf-8")


def _sha256(value) -> str:
    data = value if isinstance(value, bytes) else _canonical(value)
    return hashlib.sha256(data).hexdigest()


def _target_regions(meta: dict) -> list[dict]:
    """Independently derive logical regions from registered official metadata."""
    a, scalar = meta["architecture"], meta["bytes_per_tensor_element"]
    h, inter = a["hidden_size"], a["intermediate_size"]
    heads, kv = a["num_attention_heads"], a["num_key_value_heads"]
    if h % heads:
        raise ValueError("target hidden size must divide attention heads")
    hd, vocab = h // heads, a["vocab_size"]
    regions = [("model.embed_tokens", vocab * h * scalar)]
    for layer in range(a["num_hidden_layers"]):
        attention = ((2 * h * h + h) + 2 * (h * kv * hd + kv * hd) + h) * scalar
        mlp = (3 * h * inter + h) * scalar
        regions.extend([(f"model.layers.{layer}.attention_bundle", attention),
                        (f"model.layers.{layer}.mlp_bundle", mlp)])
    regions.extend([("model.norm", h * scalar), ("lm_head", vocab * h * scalar)])
    address, result = 0, []
    for name, byte_count in regions:
        result.append({"name": name, "logical_address_bytes": address,
                       "payload_bytes": byte_count})
        address = ((address + byte_count + ALIGNMENT - 1) // ALIGNMENT) * ALIGNMENT
    if sum(row["payload_bytes"] for row in result) != meta["tensor_payload_bytes"]:
        raise ValueError("independent region derivation disagrees with registered payload")
    return result


def target_projection(catalog_path: Path, context_tokens: int) -> dict:
    catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    result = {}
    for model_id, meta in catalog["models"].items():
        a = meta["architecture"]
        h, heads, kv = a["hidden_size"], a["num_attention_heads"], a["num_key_value_heads"]
        hd, inter, layers = h // heads, a["intermediate_size"], a["num_hidden_layers"]
        linear_macs_layer = 2 * h * h + 2 * h * kv * hd + 3 * h * inter
        attention_macs_layer = 2 * h * context_tokens
        regions = _target_regions(meta)
        result[model_id] = {
            "source_resolved_commit": meta["resolved_commit"],
            "tensor_payload_bytes": meta["tensor_payload_bytes"],
            "logical_regions": regions,
            "logical_region_address_semantics": (
                "DERIVED_1MIB_ALIGNED_SCENARIO_NOT_SAFETENSORS_FILE_OFFSETS"
            ),
            "analytical_macs_per_token_at_context": (
                layers * (linear_macs_layer + attention_macs_layer)
            ),
            "compute_cost_semantics": (
                "ANALYTICAL_DENSE_MAC_COUNT_EXCLUDES_NORMS_ROPE_SOFTMAX_AND_RUNTIME"
            ),
            "context_tokens": context_tokens,
        }
    return result


class TinyDecoder:
    def __init__(self, config: dict):
        self.config = config
        self.rng = np.random.default_rng(config["seed"])
        self.dtype = np.float32
        self.weights = {}
        self.operations = []
        self.accesses = []
        self._op_sequence = 0
        self._access_sequence = 0
        self.k_cache = [[] for _ in range(config["layers"])]
        self.v_cache = [[] for _ in range(config["layers"])]
        self._build_weights()

    def _weight(self, name, shape, scale=.02):
        value = self.rng.normal(0, scale, shape).astype(self.dtype)
        self.weights[name] = value
        return value

    def _build_weights(self):
        c, h, kvh, hd, inter = (self.config, self.config["hidden_size"],
                                self.config["num_key_value_heads"],
                                self.config["head_dim"], self.config["intermediate_size"])
        self._weight("model.embed_tokens.weight", (c["vocab_size"], h))
        for layer in range(c["layers"]):
            p = f"model.layers.{layer}"
            self.weights[p + ".input_layernorm.weight"] = np.ones(h, dtype=self.dtype)
            self._weight(p + ".self_attn.q_proj.weight", (h, h))
            self._weight(p + ".self_attn.q_proj.bias", (h,))
            self._weight(p + ".self_attn.k_proj.weight", (kvh * hd, h))
            self._weight(p + ".self_attn.k_proj.bias", (kvh * hd,))
            self._weight(p + ".self_attn.v_proj.weight", (kvh * hd, h))
            self._weight(p + ".self_attn.v_proj.bias", (kvh * hd,))
            self._weight(p + ".self_attn.o_proj.weight", (h, h))
            self.weights[p + ".post_attention_layernorm.weight"] = np.ones(h, dtype=self.dtype)
            self._weight(p + ".mlp.gate_proj.weight", (inter, h))
            self._weight(p + ".mlp.up_proj.weight", (inter, h))
            self._weight(p + ".mlp.down_proj.weight", (h, inter))
        self.weights["model.norm.weight"] = np.ones(h, dtype=self.dtype)
        self._weight("lm_head.weight", (c["vocab_size"], h))

    def _operation(self, forward_id, name, depends_on, details=None):
        op_id = f"{forward_id}:op{self._op_sequence}:{name}"
        self._op_sequence += 1
        self.operations.append({"sequence": len(self.operations), "op_id": op_id,
                                "forward_id": forward_id, "name": name,
                                "depends_on": list(depends_on), "details": details or {}})
        return op_id

    def _access(self, forward_id, op_id, name, access_shape=None):
        value = self.weights[name]
        shape = tuple(value.shape if access_shape is None else access_shape)
        byte_count = int(np.prod(shape, dtype=np.int64)) * value.dtype.itemsize
        self.accesses.append({
            "sequence": self._access_sequence, "forward_id": forward_id,
            "op_id": op_id, "weight_name": name,
            "storage_shape": list(value.shape), "access_shape": list(shape),
            "access_bytes": byte_count, "storage_dtype": str(value.dtype),
            "target_projection_dtype_bytes": 2,
            "semantics": "ACTUAL_NUMPY_ARRAY_ACCESS_BY_TINY_FORWARD",
        })
        self._access_sequence += 1
        return value

    def _rmsnorm(self, x, weight, eps):
        return x * np.reciprocal(np.sqrt(np.mean(x * x, axis=-1, keepdims=True) + eps)) * weight

    @staticmethod
    def _silu(x):
        return x / (1.0 + np.exp(-x))

    def _rope(self, x, positions):
        hd = x.shape[-1]
        inv = 1.0 / (self.config["rope_theta"] ** (np.arange(0, hd, 2) / hd))
        angles = positions[:, None] * inv[None, :]
        cos, sin = np.cos(angles)[:, None, :], np.sin(angles)[:, None, :]
        even, odd = x[..., 0::2], x[..., 1::2]
        result = np.empty_like(x)
        result[..., 0::2] = even * cos - odd * sin
        result[..., 1::2] = even * sin + odd * cos
        return result

    def forward(self, token_ids, start_position: int, forward_id: str):
        c, ids = self.config, np.asarray(token_ids, dtype=np.int64)
        embedding_op = self._operation(forward_id, "embedding_lookup", ["token_ids"],
                                       {"token_count": int(ids.size)})
        table = self._access(forward_id, embedding_op, "model.embed_tokens.weight",
                             (ids.size, c["hidden_size"]))
        x = table[ids].copy()
        previous = embedding_op
        positions = np.arange(start_position, start_position + ids.size)
        for layer in range(c["layers"]):
            p = f"model.layers.{layer}"
            norm_op = self._operation(forward_id, f"layer{layer}.input_rmsnorm", [previous])
            norm = self._access(forward_id, norm_op, p + ".input_layernorm.weight")
            n = self._rmsnorm(x, norm, c["rms_norm_eps"])
            projections = []
            for kind in ("q", "k", "v"):
                op = self._operation(forward_id, f"layer{layer}.{kind}_projection", [norm_op])
                weight = self._access(forward_id, op, p + f".self_attn.{kind}_proj.weight")
                bias = self._access(forward_id, op, p + f".self_attn.{kind}_proj.bias")
                projections.append((n @ weight.T + bias, op))
            q_raw, q_op = projections[0]
            k_raw, k_op = projections[1]
            v_raw, v_op = projections[2]
            q = self._rope(q_raw.reshape(ids.size, c["num_attention_heads"], c["head_dim"]),
                           positions)
            k = self._rope(k_raw.reshape(ids.size, c["num_key_value_heads"], c["head_dim"]),
                           positions)
            v = v_raw.reshape(ids.size, c["num_key_value_heads"], c["head_dim"])
            self.k_cache[layer].append(k)
            self.v_cache[layer].append(v)
            all_k = np.concatenate(self.k_cache[layer], axis=0)
            all_v = np.concatenate(self.v_cache[layer], axis=0)
            repeats = c["num_attention_heads"] // c["num_key_value_heads"]
            expanded_k = np.repeat(all_k, repeats, axis=1)
            expanded_v = np.repeat(all_v, repeats, axis=1)
            attention_op = self._operation(
                forward_id, f"layer{layer}.rope_gqa_causal_attention", [q_op, k_op, v_op],
                {"q_shape": list(q.shape), "kv_shape": list(k.shape),
                 "context_tokens": int(all_k.shape[0]), "kv_repeat_groups": repeats})
            scores = np.einsum("thd,shd->ths", q, expanded_k) / np.sqrt(c["head_dim"])
            absolute_q = positions[:, None]
            absolute_k = np.arange(all_k.shape[0])[None, :]
            scores = np.where(absolute_k <= absolute_q[:, :, None], scores, -1e30)
            scores -= np.max(scores, axis=-1, keepdims=True)
            probs = np.exp(scores)
            probs /= np.sum(probs, axis=-1, keepdims=True)
            context = np.einsum("ths,shd->thd", probs, expanded_v).reshape(ids.size, -1)
            out_op = self._operation(forward_id, f"layer{layer}.o_projection", [attention_op])
            out_w = self._access(forward_id, out_op, p + ".self_attn.o_proj.weight")
            x = x + context @ out_w.T
            post_op = self._operation(forward_id, f"layer{layer}.post_attention_rmsnorm", [out_op])
            post_w = self._access(forward_id, post_op, p + ".post_attention_layernorm.weight")
            post = self._rmsnorm(x, post_w, c["rms_norm_eps"])
            gate_op = self._operation(forward_id, f"layer{layer}.gate_projection", [post_op])
            gate_w = self._access(forward_id, gate_op, p + ".mlp.gate_proj.weight")
            up_op = self._operation(forward_id, f"layer{layer}.up_projection", [post_op])
            up_w = self._access(forward_id, up_op, p + ".mlp.up_proj.weight")
            down_op = self._operation(forward_id, f"layer{layer}.swiglu_down_projection",
                                      [gate_op, up_op])
            down_w = self._access(forward_id, down_op, p + ".mlp.down_proj.weight")
            x = x + (self._silu(post @ gate_w.T) * (post @ up_w.T)) @ down_w.T
            previous = down_op
        norm_op = self._operation(forward_id, "final_rmsnorm", [previous])
        norm = self._access(forward_id, norm_op, "model.norm.weight")
        x = self._rmsnorm(x, norm, c["rms_norm_eps"])
        head_op = self._operation(forward_id, "lm_head", [norm_op])
        head = self._access(forward_id, head_op, "lm_head.weight")
        logits = x @ head.T
        return logits, head_op


def build_trace(config: dict, source_path: Path = Path(__file__)) -> dict:
    required = {"schema_version", "seed", "vocab_size", "hidden_size", "layers",
                "num_attention_heads", "num_key_value_heads", "intermediate_size",
                "rms_norm_eps", "rope_theta", "prompt_token_ids", "decode_tokens",
                "projection_context_tokens"}
    if set(config) != required:
        raise ValueError("tiny trace config has missing or unknown fields")
    if config["hidden_size"] % config["num_attention_heads"]:
        raise ValueError("hidden size must divide attention heads")
    if config["num_attention_heads"] % config["num_key_value_heads"]:
        raise ValueError("query heads must divide KV heads")
    config = dict(config)
    config["head_dim"] = config["hidden_size"] // config["num_attention_heads"]
    if config["head_dim"] % 2:
        raise ValueError("RoPE head dimension must be even")
    decoder = TinyDecoder(config)
    forwards, tokens = [], list(config["prompt_token_ids"])
    logits, terminal = decoder.forward(tokens, 0, "prefill")
    forwards.append({"forward_id": "prefill", "input_tokens": tokens,
                     "context_tokens_after": len(tokens), "terminal_op_id": terminal,
                     "logits_sha256": hashlib.sha256(logits.tobytes()).hexdigest()})
    next_token = int(np.argmax(logits[-1]))
    for index in range(config["decode_tokens"]):
        forward_id = f"decode{index}"
        logits, terminal = decoder.forward([next_token], len(tokens), forward_id)
        tokens.append(next_token)
        forwards.append({"forward_id": forward_id, "input_tokens": [next_token],
                         "context_tokens_after": len(tokens), "terminal_op_id": terminal,
                         "logits_sha256": hashlib.sha256(logits.tobytes()).hexdigest()})
        next_token = int(np.argmax(logits[-1]))
    source_bytes = source_path.read_bytes()
    result = {
        "schema_version": "eq3-tiny-qwen2-cpu-trace-v1",
        "classification": "TRACE_DERIVED_TINY_RANDOM_WEIGHT_CPU_FORWARD",
        "claims_excluded": ["PRETRAINED_MODEL_QUALITY", "NATIVE_GPU_TRACE",
                            "GPU_TIMING", "TOKEN_PERFORMANCE_CALIBRATION"],
        "numpy_version": np.__version__, "blas_threads_requested": 1,
        "config": config, "config_sha256": _sha256(config),
        "source_sha256": hashlib.sha256(source_bytes).hexdigest(),
        "forwards": forwards, "generated_tokens": tokens,
        "operations": decoder.operations, "weight_accesses": decoder.accesses,
        "actual_cpu_weight_storage_bytes": sum(x.nbytes for x in decoder.weights.values()),
        "actual_access_bytes": sum(x["access_bytes"] for x in decoder.accesses),
        "target_projection": target_projection(CATALOG, config["projection_context_tokens"]),
        "target_projection_source": str(CATALOG.relative_to(HERE.parents[1])),
    }
    result["trace_sha256"] = _sha256(result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    config = json.loads(args.config.read_text(encoding="utf-8"))
    result = build_trace(config)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n",
                           encoding="utf-8")
    print(json.dumps({"output": str(args.output), "trace_sha256": result["trace_sha256"],
                      "operation_count": len(result["operations"]),
                      "weight_access_count": len(result["weight_accesses"])}))


if __name__ == "__main__":
    main()
