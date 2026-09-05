"""Opt-in, reversible in-process binding for the X vLLM 0.15.1 capture shim.

Selective donor: 37144843906b3bd71f3fbac1fecc6b5080d82b95, same path.
Some FusedMoE layers bind callbacks before the real routed-experts singleton is
initialized. A deferred proxy preserves that callback and forwards the exact
IDs once initialization completes. Early dummy calls are counted and ignored.

Importing this module does not import vLLM/torch, change environment variables,
or edit installed packages. Explicit installation changes only the supplied
class in the current process; use the returned context manager around model
construction and generation, or call close() afterward. Existing bound proxies
fail after close rather than silently dropping future captures. Other runtime
versions and actual callback coverage require independent verification.

Binding/forwarding counters are diagnostics only, never real-route gold. Calls
made directly to a preexisting real singleton bypass the proxy counters.
"""
from __future__ import annotations

import inspect
import threading
from typing import Any

DONOR_COMMIT = '37144843906b3bd71f3fbac1fecc6b5080d82b95'
_MARKER = '_hbfsim_deferred_capture_binding'
_LOCK = threading.RLock()


class _DeferredRoutedExpertsCapturer:
    def __init__(self, binding: DeferredCaptureBinding):
        self.binding = binding

    def capture(self, layer_id: int, topk_ids: Any) -> Any:
        binding = self.binding
        if binding.closed:
            raise RuntimeError('deferred capture binding is closed')
        capturer = binding.original_get_instance()
        if capturer is None:
            binding.ignored_before_init += 1
            return None
        if capturer is self or not callable(getattr(capturer, 'capture', None)):
            raise RuntimeError('routed-expert singleton has an incompatible capture API')
        result = capturer.capture(layer_id, topk_ids)
        binding.forwarded_calls += 1
        return result


class DeferredCaptureBinding:
    def __init__(self, cls: type[Any]):
        self.cls = cls
        self.original_get_instance = cls.get_instance
        if not callable(self.original_get_instance):
            raise ValueError('capturer get_instance must be callable')
        self.original_descriptor = inspect.getattr_static(cls, 'get_instance')
        self.original_was_local = 'get_instance' in cls.__dict__
        self.closed = False
        self.ignored_before_init = self.forwarded_calls = self._contexts = 0
        self.proxy = _DeferredRoutedExpertsCapturer(self)

        def get_instance():
            instance = self.original_get_instance()
            return instance if instance is not None else self.proxy

        self.getter = get_instance
        cls.get_instance = staticmethod(get_instance)
        setattr(cls, _MARKER, self)

    def __enter__(self):
        if self.closed:
            raise RuntimeError('deferred capture binding is closed')
        self._contexts += 1
        return self

    def __exit__(self, *_):
        self._contexts = max(0, self._contexts - 1)
        if not self._contexts:
            self.close()

    def close(self):
        with _LOCK:
            if self.closed:
                return
            # Never clobber another hook installed after this one.
            if self.cls.get_instance is self.getter:
                if self.original_was_local:
                    self.cls.get_instance = self.original_descriptor
                else:
                    delattr(self.cls, 'get_instance')
            if self.cls.__dict__.get(_MARKER) is self:
                delattr(self.cls, _MARKER)
            self.closed = True

    def summary(self):
        return dict(status='BINDING_CLOSED' if self.closed else 'BINDING_INSTALLED_UNVALIDATED',
                    donor_commit=DONOR_COMMIT, ignored_before_init=self.ignored_before_init,
                    forwarded_calls=self.forwarded_calls, scientific_validation_passed=False,
                    boundary='Proxy diagnostics do not prove route origin, native equivalence, or callback coverage.')


def install_vllm_routed_experts_deferred_binding(capturer_class: type[Any] | None = None) -> DeferredCaptureBinding:
    """Explicitly install once; importing the module never loads optional vLLM."""
    if capturer_class is None:
        from vllm.model_executor.layers.fused_moe.routed_experts_capturer import RoutedExpertsCapturer
        capturer_class = RoutedExpertsCapturer
    with _LOCK:
        existing = capturer_class.__dict__.get(_MARKER)
        if existing is not None:
            if not isinstance(existing, DeferredCaptureBinding) or existing.closed or capturer_class.get_instance is not existing.getter:
                raise RuntimeError('conflicting deferred capture binding')
            return existing
        if getattr(capturer_class, '_hbfsim_deferred_binding_installed', False):
            raise RuntimeError('untracked donor capture hook already installed')
        return DeferredCaptureBinding(capturer_class)
