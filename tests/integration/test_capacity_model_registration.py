from __future__ import annotations

import importlib

import pytest


def test_frozen_vllm_registration(monkeypatch, tmp_path):
    vllm = pytest.importorskip("vllm")
    assert vllm.__version__ == "0.15.1"
    monkeypatch.setenv("HBFSIM_CAPACITY_ENABLE", "1")
    monkeypatch.setenv("HBFSIM_CAPACITY_REPORT_DIR", str(tmp_path))
    plugin = importlib.import_module("adapters.vllm_capacity.capacity_plugin")
    plugin._REGISTERED = False
    plugin.register()
    from vllm.model_executor.model_loader import get_model_loader
    from vllm.config.load import LoadConfig

    loader = get_model_loader(LoadConfig(load_format="hbfsim_capacity"))
    assert loader.__class__.__name__ == "CapacityModelLoader"
    assert (tmp_path / "e6-plugin-registration.json").is_file()
