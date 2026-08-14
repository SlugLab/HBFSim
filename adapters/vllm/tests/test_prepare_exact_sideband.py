import hashlib
import json
import pathlib
import sys

import pytest


ADAPTER = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ADAPTER))

from prepare_exact_sideband import (  # noqa: E402
    SidebandArtifactError,
    prepare_exact_sideband,
)


def fixture(tmp_path):
    probe = tmp_path / "probe.ptx"
    probe.write_text("probe-original")
    original_sha = hashlib.sha256(probe.read_bytes()).hexdigest()
    transformed = b"probe-transformed"
    transformed_sha = hashlib.sha256(transformed).hexdigest()
    prepatched = tmp_path / "all" / "prepatched-ptx"
    prepatched.mkdir(parents=True)
    (prepatched / f"{original_sha}.ptx").write_bytes(transformed)
    other = "a" * 64
    (prepatched / f"{other}.ptx").write_text("must-not-be-staged")
    record = {
        "manifest_schema_version": 4,
        "module_id": f"ptx:sha256:{original_sha}",
        "kernel": "hbfsim_llama_probe_kernel",
        "instrumented": True,
        "original_ptx_sha256": original_sha,
        "transformed_ptx_sha256": transformed_sha,
        "unsupported_instructions": [],
        "unsupported_opcodes": [],
        "unsupported_parameters": [],
    }
    manifest = tmp_path / "all" / "pass-manifest.jsonl"
    manifest.write_text(
        json.dumps({**record, "original_ptx_sha256": other}) + "\n" +
        json.dumps(record) + "\n"
    )
    profile = tmp_path / "profile.json"
    profile.write_text(json.dumps({"runtime_artifacts": {
        "bundle_root": str(tmp_path / "bundles"),
        "prepatched_ptx_dir": str(prepatched),
        "pass_manifest": str(manifest),
    }}))
    return profile, probe, record, transformed


def test_stages_only_exact_sideband_probe(tmp_path):
    profile, probe, record, transformed = fixture(tmp_path)

    prepatched, manifest = prepare_exact_sideband(
        profile, probe, tmp_path / "report"
    )

    members = list(prepatched.glob("*.ptx"))
    assert len(members) == 1
    assert members[0].read_bytes() == transformed
    assert json.loads(manifest.read_text()) == record


def test_rejects_transformed_hash_mismatch(tmp_path):
    profile, probe, _, _ = fixture(tmp_path)
    data = json.loads(profile.read_text())
    manifest = pathlib.Path(data["runtime_artifacts"]["pass_manifest"])
    record = json.loads(manifest.read_text().splitlines()[1])
    record["transformed_ptx_sha256"] = "0" * 64
    manifest.write_text(json.dumps(record) + "\n")

    with pytest.raises(SidebandArtifactError, match="not admissible"):
        prepare_exact_sideband(profile, probe, tmp_path / "report")
