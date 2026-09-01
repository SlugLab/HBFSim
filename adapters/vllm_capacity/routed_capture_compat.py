"""Compatibility binding for vLLM's routed-expert capturer.

The frozen vLLM 0.15.1 runtime creates the routed-expert singleton after the
model's ``FusedMoE`` layers are constructed.  Those layers only bind a capture
callback when ``get_instance()`` is non-null at construction time, so the
unmodified ordering leaves the shared route buffer zero-filled.

This module does not modify vLLM or its environment.  It makes the early
``get_instance()`` call return a tiny deferred proxy.  Calls made before the
real singleton exists are ignored (matching vLLM's dummy-run intent); after
initialization, the same bound proxy forwards each real top-k tensor to the
frozen capturer.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any


class _DeferredRoutedExpertsCapturer:
    """Forward capture calls once vLLM has created its real singleton."""

    def __init__(self, get_instance: Callable[[], Any | None]) -> None:
        self._get_instance = get_instance

    def capture(self, layer_id: int, topk_ids: Any) -> None:
        capturer = self._get_instance()
        if capturer is not None:
            capturer.capture(layer_id, topk_ids)


def install_vllm_routed_experts_deferred_binding(
    capturer_class: type[Any] | None = None,
) -> None:
    """Install an idempotent deferred binding before vLLM constructs the model."""

    if capturer_class is None:
        from vllm.model_executor.layers.fused_moe.routed_experts_capturer import (
            RoutedExpertsCapturer,
        )

        capturer_class = RoutedExpertsCapturer

    marker = "_hbfsim_deferred_binding_installed"
    if getattr(capturer_class, marker, False):
        return

    original_get_instance = capturer_class.get_instance
    deferred = _DeferredRoutedExpertsCapturer(original_get_instance)

    def get_instance() -> Any:
        instance = original_get_instance()
        return instance if instance is not None else deferred

    capturer_class.get_instance = staticmethod(get_instance)
    setattr(capturer_class, marker, True)

