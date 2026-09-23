from __future__ import annotations

import hashlib
import importlib.util
import json
import pathlib
import subprocess
import sys

import pytest


MODULE_PATH = pathlib.Path(__file__).parents[1] / "adapters" / "vllm" / "auto_prepare_ptx.py"
if not MODULE_PATH.is_file():
    MODULE_PATH = pathlib.Path(__file__).with_name("auto_prepare_ptx.py")
PLUGIN = pathlib.Path(
    "/root/hbfsim-exp/rebuttal_20260921/first-fault-compact-runtime-v2/libptxpass_hbf.so"
)


def load_module():
    spec = importlib.util.spec_from_file_location("auto_prepare_ptx", MODULE_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


TWO_ENTRY = rb""".version 8.8
.target sm_120
.address_size 64

.visible .entry kernel_a(
    .param .u64 kernel_a_param_0
)
{
    .reg .b64 %rd<3>;
    .reg .b32 %r<2>;
    ld.param.u64 %rd1, [kernel_a_param_0];
    ld.global.u32 %r1, [%rd1];
    st.global.u32 [%rd1], %r1;
    ret;
}

.visible .entry kernel_b(
    .param .u64 kernel_b_param_0
)
{
    .reg .b64 %rd<3>;
    .reg .b32 %r<2>;
    ld.param.u64 %rd1, [kernel_b_param_0];
    ld.global.u32 %r1, [%rd1];
    st.global.u32 [%rd1], %r1;
    ret;
}
"""


UNSUPPORTED_SECOND = rb""".version 8.8
.target sm_120
.address_size 64

.visible .entry kernel_a(
    .param .u64 kernel_a_param_0
)
{
    .reg .b64 %rd<3>;
    .reg .b32 %r<2>;
    ld.param.u64 %rd1, [kernel_a_param_0];
    ld.global.u32 %r1, [%rd1];
    st.global.u32 [%rd1], %r1;
    ret;
}

.visible .entry kernel_b(
    .param .u64 kernel_b_param_0,
    .param .u64 kernel_b_param_1
)
{
    .reg .b64 %rd<3>;
    ld.param.u64 %rd1, [kernel_b_param_0];
    ld.param.u64 %rd2, [kernel_b_param_1];
    cp.async.ca.shared.global [%rd1], [%rd2], 16;
    ret;
}
"""


def stage(module, tmp_path: pathlib.Path, payload: bytes, *, policy: str = "strict"):
    cache = tmp_path / "cache"
    staging = tmp_path / "staging"
    cache.mkdir()
    (cache / "module.ptx").write_bytes(payload)
    pass_manifest = tmp_path / "pass-manifests.jsonl"
    result = module.stage_all_ptx(
        cache, staging, PLUGIN, pass_manifest, policy=policy
    )
    return result, staging, pass_manifest


@pytest.mark.skipif(not PLUGIN.is_file(), reason="frozen real PTX pass is unavailable")
def test_real_two_entry_accumulates_one_trusted_identity(tmp_path: pathlib.Path):
    module = load_module()
    result, staging, pass_manifest = stage(module, tmp_path, TWO_ENTRY)
    raw_sha = hashlib.sha256(TWO_ENTRY).hexdigest()
    assert result["status"] == "READY"
    assert len(result["variants"]) == 1
    variant = result["variants"][0]
    assert variant["raw_sha256"] == raw_sha
    assert variant["module_id"] == f"ptx:sha256:{raw_sha}"
    assert variant["kernel_entries"] == ["kernel_a", "kernel_b"]
    assert [row["status"] for row in variant["entry_results"]] == [
        "SUPPORTED_TRANSFORMED", "SUPPORTED_TRANSFORMED"
    ]
    transformed = (staging / f"{raw_sha}.ptx").read_bytes()
    complete = json.loads((staging / "COMPLETE.json").read_text())
    assert complete["status"] == "READY"
    assert complete["manifest_sha256"] == hashlib.sha256(
        (staging / "ptx-staging-manifest.json").read_bytes()
    ).hexdigest()
    assert module.verify_stage_completion(staging, pass_manifest)["status"] == "READY"
    assert transformed.count(b"__hbfsim_module_identity") == 1
    assert transformed.count(b"__hbfsim_resolve") >= 2
    rows = [json.loads(line) for line in pass_manifest.read_text().splitlines()]
    assert {row["kernel"] for row in rows} == {"kernel_a", "kernel_b"}
    assert {row["module_id"] for row in rows} == {f"ptx:sha256:{raw_sha}"}
    assert all(row["instrumented"] for row in rows)
    assert {row["kernel"] for row in variant["raw_attempt_records"]} == {
        "kernel_a", "kernel_b"
    }

    # A fresh child must not trust a previously emitted module identity.
    emitted = tmp_path / "already-emitted.ptx"
    emitted.write_bytes(transformed)
    child_manifest = tmp_path / "fresh-child-manifest.jsonl"
    completed = subprocess.run(
        [
            sys.executable, str(MODULE_PATH), "--transform-module-child",
            "--source", str(emitted), "--pass-library", str(PLUGIN),
            "--pass-manifest", str(child_manifest),
            "--entries-json", '["kernel_b"]',
        ],
        text=True, capture_output=True, check=True,
    )
    child = json.loads(completed.stdout)
    assert child["entry_results"][0]["status"] == "UNSUPPORTED"
    assert "untrusted preexisting" in child["entry_results"][0]["reason"]


@pytest.mark.skipif(not PLUGIN.is_file(), reason="frozen real PTX pass is unavailable")
def test_unsupported_second_entry_is_not_strict_ready(tmp_path: pathlib.Path):
    module = load_module()
    result, staging, pass_manifest = stage(module, tmp_path, UNSUPPORTED_SECOND)
    assert result["status"] == "NOT_READY"
    variant = result["variants"][0]
    assert variant["staged_path"] is None
    assert not list(staging.glob("*.ptx"))
    by_kernel = {row["kernel"]: row for row in variant["entry_results"]}
    assert by_kernel["kernel_a"]["status"] == "SUPPORTED_TRANSFORMED"
    assert by_kernel["kernel_b"]["status"] == "UNSUPPORTED"
    assert pass_manifest.read_bytes() == b""


def test_manifest_retry_dedup_conflict_and_zero_rejected():
    module = load_module()
    base = {
        "module_id": "ptx:sha256:" + "a" * 64,
        "kernel": "kernel_a",
        "instrumented": True,
    }
    assert module._deduplicate_manifest_records([base, dict(base)]) == [base]
    conflict = dict(base, instrumented=False)
    with pytest.raises(ValueError, match="conflicting"):
        module._deduplicate_manifest_records([base, conflict])
    zero = dict(base, module_id="ptx:sha256:" + "0" * 64)
    with pytest.raises(ValueError, match="zero"):
        module._deduplicate_manifest_records([zero])


def test_noninline_memory_function_is_unresolved(tmp_path: pathlib.Path):
    module = load_module()
    payload = TWO_ENTRY + rb"""
.visible .func helper(.param .b64 p) {
    .reg .b64 %rd<2>;
    .reg .b32 %r<2>;
    ld.param.u64 %rd1, [p];
    ld.global.u32 %r1, [%rd1];
    ret;
}
"""
    result, staging, _ = stage(module, tmp_path, payload)
    assert result["status"] == "NOT_READY"
    variant = result["variants"][0]
    assert variant["call_graph_status"] == "UNRESOLVED_NONINLINE_MEMORY_FUNCTION"
    assert variant["unresolved_functions"] == ["helper"]
    assert {row["status"] for row in variant["entry_results"]} == {"UNRESOLVED"}
    assert not list(staging.glob("*.ptx"))


@pytest.mark.parametrize("instruction", [
    b"ld.u32 %r1, [%rd1];",
    b"ld.volatile.global.u32 %r1, [%rd1];",
    b"ld.acquire.sys.global.u32 %r1, [%rd1];",
    b"cp.async.ca.shared.global [%rd1], [%rd2], 16;",
])
def test_function_memory_qualifier_forms_are_unresolved(instruction: bytes):
    module = load_module()
    payload = b""".version 8.8
.target sm_120
.address_size 64
.visible .func helper() {
  .reg .b64 %rd<3>;
  .reg .b32 %r<2>;
""" + instruction + b"\n  ret;\n}\n"
    status, functions = module._call_graph_status(payload)
    assert status == "UNRESOLVED_NONINLINE_MEMORY_FUNCTION"
    assert functions == ["helper"]


def test_calls_are_unresolved_but_comments_are_ignored():
    module = load_module()
    status, reason = module._call_graph_status(
        b"// call external_helper;\n/* call.uni other; */\n"
    )
    assert status == "NO_UNRESOLVED_MEMORY_FUNCTION"
    assert reason == []
    status, reason = module._call_graph_status(b"call.uni external_helper, ();\n")
    assert status == "UNRESOLVED_CALL_DEPENDENCY"
    assert reason == ["call"]


@pytest.mark.skipif(not PLUGIN.is_file(), reason="frozen real PTX pass is unavailable")
def test_pattern_inventory_and_failed_generation_publication(tmp_path: pathlib.Path):
    module = load_module()
    cache = tmp_path / "cache"
    cache.mkdir()
    (cache / "a.ptx").write_bytes(TWO_ENTRY.replace(b"kernel_b", b"kernel_c"))
    (cache / "b.ptx").write_bytes(
        TWO_ENTRY.replace(b"kernel_a", b"kernel_x").replace(b"kernel_b", b"kernel_y")
    )
    staging = tmp_path / "stage"
    pass_manifest = tmp_path / "pass.jsonl"
    result = module.stage_all_ptx(
        cache, staging, PLUGIN, pass_manifest,
        policy="strict", kernel_patterns=("kernel_a",),
    )
    assert result["status"] == "NOT_READY"
    assert len(result["variants"]) == 2
    assert any(v["call_graph_status"] == "SKIPPED_PATTERN" for v in result["variants"])
    assert not list(staging.glob("*.ptx"))
    assert pass_manifest.read_bytes() == b""
    assert json.loads((staging / "COMPLETE.json").read_text())["status"] == "NOT_READY"


def test_duplicate_entry_and_incomplete_generation_are_rejected(tmp_path: pathlib.Path):
    module = load_module()
    entries, duplicates = module._entry_inventory(
        b".entry repeated() { ret; }\n.entry repeated() { ret; }\n"
    )
    assert entries == ("repeated",)
    assert duplicates == ("repeated",)

    staging = tmp_path / "incomplete"
    staging.mkdir()
    (staging / "orphan.ptx").write_text("partial")
    with pytest.raises(FileExistsError, match="incomplete staging generation"):
        module._check_generation_directory(staging)


@pytest.mark.skipif(not PLUGIN.is_file(), reason="frozen real PTX pass is unavailable")
def test_preflight_conflict_leaves_no_partial_stage(tmp_path: pathlib.Path):
    module = load_module()
    cache = tmp_path / "cache"
    cache.mkdir()
    (cache / "module.ptx").write_bytes(TWO_ENTRY)
    staging = tmp_path / "stage"
    pass_manifest = tmp_path / "pass.jsonl"
    pass_manifest.write_text("foreign\n")
    with pytest.raises(FileExistsError, match="differing artifact"):
        module.stage_all_ptx(cache, staging, PLUGIN, pass_manifest)
    assert list(staging.iterdir()) == []
    assert pass_manifest.read_text() == "foreign\n"


@pytest.mark.skipif(not PLUGIN.is_file(), reason="frozen real PTX pass is unavailable")
def test_completion_verifier_rejects_extra_ptx(tmp_path: pathlib.Path):
    module = load_module()
    _, staging, pass_manifest = stage(module, tmp_path, TWO_ENTRY)
    (staging / "foreign.ptx").write_text("untrusted")
    with pytest.raises(ValueError, match="PTX file set mismatch"):
        module.verify_stage_completion(staging, pass_manifest)


@pytest.mark.skipif(not PLUGIN.is_file(), reason="frozen real PTX pass is unavailable")
def test_child_timeout_is_bounded_and_diagnostic(tmp_path: pathlib.Path, monkeypatch):
    module = load_module()
    cache = tmp_path / "cache"
    cache.mkdir()
    (cache / "module.ptx").write_bytes(TWO_ENTRY)
    staging = tmp_path / "stage"
    pass_manifest = tmp_path / "pass.jsonl"

    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(args[0], kwargs.get("timeout", 0), output=b"partial")

    monkeypatch.setattr(module.subprocess, "run", timeout)
    result = module.stage_all_ptx(
        cache, staging, PLUGIN, pass_manifest, module_timeout_seconds=0.25
    )
    assert result["status"] == "NOT_READY"
    variant = result["variants"][0]
    assert variant["child_diagnostic"]["child_status"] == "TIMEOUT"
    assert variant["child_diagnostic"]["timeout_seconds"] == 0.25
    assert {row["reason"] for row in variant["entry_results"]} == {"CHILD_TIMEOUT"}
    assert json.loads((staging / "COMPLETE.json").read_text())["status"] == "NOT_READY"
    assert pass_manifest.read_bytes() == b""
