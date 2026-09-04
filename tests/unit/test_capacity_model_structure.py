from __future__ import annotations

import ast
import pathlib


def _source() -> pathlib.Path:
    snapshot_candidate = (
        pathlib.Path(__file__).resolve().parent
        / "vllm_capacity"
        / "capacity_model.py"
    )
    if snapshot_candidate.is_file():
        return snapshot_candidate
    repo_candidate = (
        pathlib.Path(__file__).resolve().parents[2]
        / "adapters"
        / "vllm_capacity"
        / "capacity_model.py"
    )
    if repo_candidate.is_file():
        return repo_candidate
    return pathlib.Path(__file__).resolve().parents[3] / "adapter" / "capacity_model.py"


def test_capacity_model_has_no_full_expert_constructor():
    source = _source().read_text()
    tree = ast.parse(source)
    names = {
        node.name for node in ast.walk(tree) if isinstance(node, ast.ClassDef)
    }
    assert "CapacityQwen3MoeSparseBlock" in names
    assert "CapacityQwen3MoeForCausalLM" in names
    assert "SharedFusedMoE(" not in source
    assert "fused_experts(" in source
    assert "get_capacity_stager().stage(" in source


def test_capacity_pointer_is_not_passed_to_fused_experts():
    source = _source().read_text()
    tree = ast.parse(source)
    calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "fused_experts"
    ]
    assert len(calls) == 1
    keyword_values = {
        keyword.arg: ast.unparse(keyword.value) for keyword in calls[0].keywords
    }
    assert keyword_values["w1"] == "lease.w13"
    assert keyword_values["w2"] == "lease.w2"
    assert "logical_address" not in ast.unparse(calls[0])
    forward_source = ast.unparse(
        next(
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef)
            and node.name == "forward"
            and any(
                isinstance(child, ast.Call)
                and isinstance(child.func, ast.Name)
                and child.func.id == "fused_experts"
                for child in ast.walk(node)
            )
        )
    )
    assert "_assert_opaque_launch_arguments_are_ordinary" in forward_source
    assert forward_source.index("_assert_opaque_launch_arguments_are_ordinary") < (
        forward_source.index("fused_experts")
    )


def test_compact_fused_moe_pads_duplicate_dummy_routes_to_topk():
    source = _source().read_text()

    assert "minimum_fused_slots=self.top_k" in source
    assert "global_num_experts=lease.allocated_experts" in source


def test_topk_remap_uses_synchronized_host_ids():
    source = _source().read_text()

    assert 'topk_ids_cpu = topk_ids.detach().to(device="cpu")' in source
    assert "for value in topk_ids_cpu.reshape(-1).tolist()" in source
    assert "compact_ids = lease.remap(" in source
    assert "topk_ids_cpu," in source
    assert "lease.remap(topk_ids)" not in source
