from __future__ import annotations

import os
import pathlib

import pytest


@pytest.mark.skipif(
    os.environ.get("HBFSIM_E6_RUN_CAPACITY_STAGE_TEST") != "1",
    reason="requires exclusive GPU and full HBFSim capacity allocation",
)
def test_capacity_stage_is_bit_exact_to_frozen_safetensors():
    torch = pytest.importorskip("torch")
    if not torch.cuda.is_available():
        pytest.skip("CUDA is unavailable")
    from safetensors import safe_open

    from adapters.vllm_capacity.capacity_loader import (
        CapacityInventory,
        install_inventory,
    )
    from adapters.vllm_capacity.capacity_runtime import get_capacity_runtime
    from adapters.vllm_capacity.capacity_staging import CapacityStager

    inventory = CapacityInventory(
        pathlib.Path(os.environ["HBFSIM_CAPACITY_MANIFEST"]),
        pathlib.Path(os.environ["HBFSIM_CAPACITY_MODEL_ROOT"]),
    )
    inventory.validate_headers()
    install_inventory(inventory)
    runtime = get_capacity_runtime()
    runtime.map_shards(inventory.model_root, inventory.files)
    stager = CapacityStager(max_active_experts=1)
    lease = stager.stage(0, [0], device=torch.device("cuda"))
    expert = inventory.expert(0, 0)

    def load(tensor):
        with safe_open(
            inventory.model_root / tensor.shard, framework="pt", device="cpu"
        ) as handle:
            return handle.get_tensor(tensor.tensor)

    gate = load(expert.gate)
    up = load(expert.up)
    down = load(expert.down)
    assert torch.equal(lease.w13[0, :768].cpu(), gate)
    assert torch.equal(lease.w13[0, 768:].cpu(), up)
    assert torch.equal(lease.w2[0].cpu(), down)
    lease.complete()
    runtime.close()
