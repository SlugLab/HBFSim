import hashlib
import json
import pathlib
import sys
from types import SimpleNamespace

import pytest


ADAPTER = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ADAPTER))

import prepare_triton_ptx as staging  # noqa: E402
import triton_binding as binding  # noqa: E402


PTX_A = b".version 8.8\n.visible .entry fused_moe_kernel() { ret; }\n"
PTX_B = b".version 8.8\n.visible .entry fused_moe_kernel() { nop; ret; }\n"


def test_staging_preserves_same_name_variants_and_deduplicates(tmp_path):
    cache = tmp_path / "cache"
    (cache / "a").mkdir(parents=True)
    (cache / "b").mkdir(parents=True)
    (cache / "c").mkdir(parents=True)
    (cache / "a" / "fused_moe_kernel.ptx").write_bytes(PTX_A)
    (cache / "b" / "fused_moe_kernel.ptx").write_bytes(PTX_B)
    (cache / "c" / "fused_moe_kernel.ptx").write_bytes(PTX_A)

    manifest = staging.stage_ptx(cache, tmp_path / "stage")

    assert len(manifest["variants"]) == 2
    assert {item["kernel_names"] for item in manifest["variants"]} == {
        ("fused_moe_kernel",)
    }
    assert {item["sha256"] for item in manifest["variants"]} == {
        hashlib.sha256(PTX_A).hexdigest(),
        hashlib.sha256(PTX_B).hexdigest(),
    }
    assert all(pathlib.Path(item["staged_path"]).is_file()
               for item in manifest["variants"])
    persisted = json.loads(
        (tmp_path / "stage" / "ptx-staging-manifest.json").read_text()
    )
    assert len(persisted["variants"]) == 2


def test_staging_can_prepatch_while_retaining_original_digest(tmp_path):
    cache = tmp_path / "cache"
    cache.mkdir()
    source = cache / "fused_moe_kernel.ptx"
    source.write_bytes(PTX_A)
    calls = []

    def transform(payload, kernel):
        calls.append((payload, kernel))
        return payload + b"// prepatched\n"

    manifest = staging.stage_ptx(
        cache, tmp_path / "stage", kernel="fused_moe_kernel",
        transformer=transform,
    )

    variant = manifest["variants"][0]
    original_digest = hashlib.sha256(PTX_A).hexdigest()
    assert calls == [(PTX_A, "fused_moe_kernel")]
    assert variant["sha256"] == original_digest
    assert variant["prepatched"] is True
    assert pathlib.Path(variant["staged_path"]).name == f"{original_digest}.ptx"
    assert pathlib.Path(variant["staged_path"]).read_bytes().endswith(
        b"// prepatched\n"
    )


def test_hook_binds_exact_function_and_ptx_bytes(tmp_path):
    ptx = tmp_path / "fused_moe_kernel.ptx"
    ptx.write_bytes(PTX_A)
    calls = []

    def native(original, payload, name):
        calls.append((original, payload, name))
        return 0

    hook = binding.TritonVariantBinder(tmp_path / "report", native)
    metadata = SimpleNamespace(asm={"ptx": str(ptx)})

    hook.on_kernel_load(None, 0x1234, "fused_moe_kernel", metadata,
                        "triton-hash-a")

    assert calls == [(0x1234, PTX_A, "fused_moe_kernel")]
    record = json.loads(
        (tmp_path / "report" / "triton-bindings.jsonl").read_text()
    )
    assert record["ptx_sha256"] == hashlib.sha256(PTX_A).hexdigest()
    assert record["original_function"] == "0x1234"
    assert record["result"] == "bound"


def test_hook_keeps_same_name_variants_distinct(tmp_path):
    paths = []
    calls = []
    for index, payload in enumerate((PTX_A, PTX_B)):
        path = tmp_path / str(index) / "fused_moe_kernel.ptx"
        path.parent.mkdir()
        path.write_bytes(payload)
        paths.append(path)

    hook = binding.TritonVariantBinder(
        tmp_path / "report",
        lambda original, payload, name: calls.append(
            (original, hashlib.sha256(payload).hexdigest(), name)
        ) or 0,
    )
    for index, path in enumerate(paths):
        hook.on_kernel_load(
            None, 0x2000 + index, "fused_moe_kernel",
            SimpleNamespace(asm={"ptx": str(path)}), f"hash-{index}"
        )

    assert len(calls) == 2
    assert calls[0][1] != calls[1][1]
    assert calls[0][2] == calls[1][2] == "fused_moe_kernel"


@pytest.mark.parametrize("result", (2, 3))
def test_required_binding_rejects_missing_or_ambiguous_variant(tmp_path,
                                                               result):
    ptx = tmp_path / "fused_moe_kernel.ptx"
    ptx.write_bytes(PTX_A)
    hook = binding.TritonVariantBinder(
        tmp_path / "report", lambda *_: result, required=True, retries=0
    )

    with pytest.raises(binding.TritonBindingError, match="exact PTX variant"):
        hook.on_kernel_load(
            None, 0x1234, "fused_moe_kernel",
            SimpleNamespace(asm={"ptx": str(ptx)}), "hash"
        )


def test_metadata_group_path_is_supported(tmp_path):
    ptx = tmp_path / "fused_moe_kernel.ptx"
    ptx.write_bytes(PTX_A)
    hook = binding.TritonVariantBinder(
        tmp_path / "report", lambda *_: 0
    )

    hook.on_kernel_load(
        None, 0x1234, "fused_moe_kernel", {"ptx": str(ptx)}, "hash"
    )

    assert hook.bound_count == 1


def test_real_triton_metadata_group_filename_key_is_supported(tmp_path):
    ptx = tmp_path / "fused_moe_kernel.ptx"
    ptx.write_bytes(PTX_A)
    calls = []
    hook = binding.TritonVariantBinder(
        tmp_path / "report", lambda *args: calls.append(args) or 0
    )

    hook.on_kernel_load(
        None, 0x1234, "fused_moe_kernel",
        {"fused_moe_kernel.ptx": str(ptx),
         "fused_moe_kernel.cubin": str(tmp_path / "kernel.cubin")},
        "hash",
    )

    assert calls == [(0x1234, PTX_A, "fused_moe_kernel")]
