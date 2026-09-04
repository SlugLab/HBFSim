from __future__ import annotations

import importlib
import sys
import types


def _import_plugin():
    try:
        return importlib.import_module("adapters.vllm_capacity.capacity_plugin")
    except ModuleNotFoundError:
        return importlib.import_module("capacity_plugin")


def test_plugin_constants_are_exact():
    plugin = _import_plugin()
    assert plugin.PLUGIN_NAME == "hbfsim_capacity"
    assert plugin.LOAD_FORMAT == "hbfsim_capacity"
    assert plugin.MODEL_ARCHITECTURE == "Qwen3MoeForCausalLM"
    assert plugin.MODEL_TARGET.endswith(":CapacityQwen3MoeForCausalLM")


def test_register_is_explicit_and_idempotent(monkeypatch, tmp_path):
    plugin = _import_plugin()
    plugin._REGISTERED = False
    calls = []

    class Registry:
        @staticmethod
        def register_model(architecture, target):
            calls.append(("model", architecture, target))

    fake_vllm = types.ModuleType("vllm")
    fake_vllm.ModelRegistry = Registry
    fake_loader_module = types.ModuleType("vllm.model_executor.model_loader")

    def register_model_loader(name):
        def decorate(cls):
            calls.append(("loader", name, cls))
            return cls

        return decorate

    fake_loader_module.register_model_loader = register_model_loader
    fake_capacity_loader = types.ModuleType(
        "adapters.vllm_capacity.capacity_loader"
    )
    fake_capacity_loader.CapacityModelLoader = type("CapacityModelLoader", (), {})
    monkeypatch.setitem(sys.modules, "vllm", fake_vllm)
    monkeypatch.setitem(
        sys.modules, "vllm.model_executor.model_loader", fake_loader_module
    )
    monkeypatch.setitem(
        sys.modules,
        "adapters.vllm_capacity.capacity_loader",
        fake_capacity_loader,
    )
    monkeypatch.setenv("HBFSIM_CAPACITY_ENABLE", "1")
    monkeypatch.setenv("HBFSIM_CAPACITY_REPORT_DIR", str(tmp_path))
    plugin.register()
    plugin.register()
    assert [call[0] for call in calls] == ["loader", "model"]
    assert (tmp_path / "e6-plugin-registration.json").is_file()


def test_register_fails_closed_without_enable(monkeypatch):
    plugin = _import_plugin()
    plugin._REGISTERED = False
    monkeypatch.delenv("HBFSIM_CAPACITY_ENABLE", raising=False)
    try:
        plugin.register()
    except RuntimeError as exc:
        assert "explicit enable" in str(exc)
    else:
        raise AssertionError("plugin registration unexpectedly enabled itself")
