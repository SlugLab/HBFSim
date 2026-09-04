from __future__ import annotations

import pathlib

import pytest


def test_frozen_manifest_headers_without_expert_materialization():
    manifest_env = __import__("os").environ.get("HBFSIM_CAPACITY_MANIFEST")
    model_env = __import__("os").environ.get("HBFSIM_CAPACITY_MODEL_ROOT")
    if not manifest_env or not model_env:
        pytest.skip("frozen E6 model environment is not active")
    from adapters.vllm_capacity.capacity_loader import CapacityInventory

    inventory = CapacityInventory(pathlib.Path(manifest_env), pathlib.Path(model_env))
    report = inventory.validate_headers()
    assert report["status"] == "PASS"
    assert report["expert_tensors_validated_without_get_tensor"] == 18_432
    nonexperts = inventory.nonexpert_weight_map()
    assert nonexperts
    assert not any(".mlp.experts." in name for name in nonexperts)
