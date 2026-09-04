"""Qwen3-MoE model variant with router-only blocks and compact expert staging."""

from __future__ import annotations

import threading
import weakref
from typing import Any

import torch
import torch.nn as nn

from vllm.distributed import get_ep_group, get_tensor_model_parallel_world_size
from vllm.model_executor.layers.fused_moe.fused_moe import fused_experts
from vllm.model_executor.layers.fused_moe.router.fused_topk_router import fused_topk
from vllm.model_executor.layers.linear import ReplicatedLinear
from vllm.model_executor.models import qwen3_moe as native_qwen

from adapters.vllm_capacity.capacity_staging import get_capacity_stager


def _assert_opaque_launch_arguments_are_ordinary(
    **tensors: torch.Tensor,
) -> None:
    """Prove that Triton's private driver launch receives no capacity pointer."""
    from adapters.vllm_capacity.capacity_runtime import get_capacity_runtime

    runtime = get_capacity_runtime()
    for label, tensor in tensors.items():
        if not tensor.is_cuda or not tensor.is_contiguous():
            raise RuntimeError(
                f"opaque fused-MoE argument must be contiguous CUDA memory: {label}"
            )
        runtime.assert_outside_capacity_mappings(
            int(tensor.data_ptr()),
            int(tensor.numel()) * int(tensor.element_size()),
            label=label,
        )


class CapacityExpertsFacade(nn.Module):
    """Metadata facade expected by vLLM's MoE interfaces, with no weights."""

    def __init__(self, owner: "CapacityQwen3MoeSparseBlock") -> None:
        super().__init__()
        self._owner_ref = weakref.ref(owner)
        self.global_num_experts = owner.n_routed_experts
        self.local_num_experts = owner.n_local_physical_experts
        self.top_k = owner.top_k

    def update_expert_map(self) -> None:
        owner = self._owner_ref()
        if owner is None:
            raise RuntimeError("capacity expert facade owner was released")
        if owner.n_redundant_experts != 0:
            raise RuntimeError("capacity pilot forbids redundant experts/EPLB")
        self.global_num_experts = owner.n_routed_experts
        self.local_num_experts = owner.n_local_physical_experts

    @staticmethod
    def maybe_all_reduce_tensor_model_parallel(tensor: torch.Tensor) -> torch.Tensor:
        # The capacity contract is single-rank.  A nontrivial reduction is a
        # configuration error checked during block construction.
        return tensor


class CapacityQwen3MoeSparseBlock(native_qwen.Qwen3MoeSparseMoeBlock):
    """A sparse block that allocates a router but no resident expert weights."""

    def __init__(self, vllm_config: Any, prefix: str = "") -> None:
        nn.Module.__init__(self)
        config = vllm_config.model_config.hf_text_config
        parallel = vllm_config.parallel_config
        if vllm_config.quant_config is not None:
            raise RuntimeError("E6 pilot supports exact unquantized BF16 only")
        if get_tensor_model_parallel_world_size() != 1:
            raise RuntimeError("E6 capacity pilot requires tensor_parallel_size=1")
        ep_group = get_ep_group()
        if ep_group.device_group.size() != 1:
            raise RuntimeError("E6 capacity pilot requires expert_parallel_size=1")
        if parallel.enable_eplb:
            raise RuntimeError("E6 capacity pilot requires EPLB disabled")
        if parallel.use_sequence_parallel_moe:
            raise RuntimeError("E6 capacity pilot forbids sequence-parallel MoE")
        if getattr(config, "shared_expert_intermediate_size", 0):
            raise RuntimeError("frozen Qwen manifest has no shared expert")
        if config.num_experts != 128 or config.num_experts_per_tok != 8:
            raise RuntimeError("unexpected Qwen expert geometry")
        if config.hidden_size != 2048 or config.moe_intermediate_size != 768:
            raise RuntimeError("unexpected Qwen expert tensor geometry")

        self.tp_size = 1
        self.ep_group = ep_group.device_group
        self.ep_rank = 0
        self.ep_size = 1
        self.n_routed_experts = int(config.num_experts)
        self.n_logical_experts = self.n_routed_experts
        self.n_redundant_experts = 0
        self.n_physical_experts = self.n_routed_experts
        self.n_local_physical_experts = self.n_routed_experts
        self.physical_expert_start = 0
        self.physical_expert_end = self.n_routed_experts
        self.is_sequence_parallel = False
        self.top_k = int(config.num_experts_per_tok)
        self.renormalize = bool(config.norm_topk_prob)
        self.scoring_func = "softmax"
        self.layer_index = native_qwen.extract_layer_index(prefix)
        self.gate = ReplicatedLinear(
            config.hidden_size,
            config.num_experts,
            bias=False,
            quant_config=None,
            prefix=f"{prefix}.gate",
        )
        self.shared_expert_gate = None
        self.shared_expert = None
        self.experts = CapacityExpertsFacade(self)

    def forward(self, hidden_states: torch.Tensor) -> torch.Tensor:
        if hidden_states.dim() not in (1, 2):
            raise RuntimeError("capacity MoE block requires 1D or 2D hidden states")
        was_1d = hidden_states.dim() == 1
        if was_1d:
            hidden_states = hidden_states.unsqueeze(0)
        hidden_states = hidden_states.contiguous()
        router_logits, _ = self.gate(hidden_states)
        topk_weights, topk_ids, _ = fused_topk(
            hidden_states,
            router_logits,
            self.top_k,
            self.renormalize,
            scoring_func=self.scoring_func,
        )
        topk_ids_cpu = topk_ids.detach().to(device="cpu")
        routed_ids = [
            int(value)
            for value in topk_ids_cpu.reshape(-1).tolist()
        ]
        lease = get_capacity_stager().stage(
            self.layer_index,
            routed_ids,
            device=hidden_states.device,
            minimum_fused_slots=self.top_k,
        )
        try:
            compact_ids = lease.remap(
                topk_ids_cpu,
                device=hidden_states.device,
            )
            _assert_opaque_launch_arguments_are_ordinary(
                hidden_states=hidden_states,
                w1=lease.w13,
                w2=lease.w2,
                topk_weights=topk_weights,
                topk_ids=compact_ids,
            )
            output = fused_experts(
                hidden_states=hidden_states,
                w1=lease.w13,
                w2=lease.w2,
                topk_weights=topk_weights,
                topk_ids=compact_ids,
                inplace=False,
                activation="silu",
                apply_router_weight_on_input=False,
                global_num_experts=lease.allocated_experts,
                expert_map=None,
            )
            # A single compact workspace is reused by the next layer.  Wait for
            # the fused consumer before making that workspace writable again.
            torch.cuda.current_stream(device=hidden_states.device).synchronize()
            lease.complete()
        except BaseException:
            lease.abort()
            raise
        return output.squeeze(0) if was_1d else output


_CONSTRUCTION_LOCK = threading.Lock()


class CapacityQwen3MoeForCausalLM(native_qwen.Qwen3MoeForCausalLM):
    """Native Qwen non-experts plus capacity-backed routed experts."""

    fall_back_to_pt_during_load = False

    def __init__(self, *, vllm_config: Any, prefix: str = "") -> None:
        config = vllm_config.model_config.hf_text_config
        architectures = list(getattr(config, "architectures", []) or [])
        if architectures and "Qwen3MoeForCausalLM" not in architectures:
            raise RuntimeError(f"unexpected model architectures: {architectures}")
        # Reuse the frozen, tested attention/decoder/top-level implementation.
        # During its constructor only, replace the sparse block factory with
        # our subclass.  The original symbol is restored before returning.
        with _CONSTRUCTION_LOCK:
            original = native_qwen.Qwen3MoeSparseMoeBlock
            native_qwen.Qwen3MoeSparseMoeBlock = CapacityQwen3MoeSparseBlock
            try:
                super().__init__(vllm_config=vllm_config, prefix=prefix)
            finally:
                native_qwen.Qwen3MoeSparseMoeBlock = original

        expert_parameter_names = [
            name for name, _ in self.named_parameters() if ".mlp.experts." in name
        ]
        if expert_parameter_names:
            raise RuntimeError(
                "capacity model allocated resident expert parameters: "
                f"{expert_parameter_names[:8]}"
            )
        sparse_layers = [
            layer
            for layer in self.model.layers
            if isinstance(getattr(layer, "mlp", None), CapacityQwen3MoeSparseBlock)
        ]
        if len(sparse_layers) != int(config.num_hidden_layers):
            raise RuntimeError(
                f"capacity sparse-layer count mismatch: {len(sparse_layers)} != "
                f"{config.num_hidden_layers}"
            )


__all__ = ["CapacityQwen3MoeForCausalLM", "CapacityQwen3MoeSparseBlock"]
