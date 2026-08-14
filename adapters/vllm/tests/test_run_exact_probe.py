import json
import pathlib
import re
import sys

import pytest


ADAPTER = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ADAPTER))

import run_exact_probe as probe_module  # noqa: E402


def test_runner_has_no_torch_or_vllm_imports():
    source = (ADAPTER / "run_exact_probe.py").read_text()

    assert "import torch" not in source
    assert "import hbfsim_loader" not in source
    assert "from run import" not in source
    assert probe_module.HBFSIM_VLLM_ABI_VERSION == 3
    assert probe_module.PROBE_RING_CAPACITY == 1024
    assert probe_module.PROBE_REQUEST_TIMEOUT_NS == 10_000_000_000
    assert "PROBE_RING_CAPACITY, {" in source
    assert "PROBE_REQUEST_TIMEOUT_NS, exact_profile_bytes" in source


def test_merges_probe_into_deferred_native_result(tmp_path):
    result_path = tmp_path / "result.json"
    result_path.write_text(json.dumps({
        "mode": "exact",
        "exact_scope": "one_shot_sideband_probe",
        "model_graph_fidelity": "native",
        "exact_probe_deferred": True,
        "exact_session_count": 0,
        "exact_post_run_finalized": False,
        "exact_probe": None,
        "output_token_ids": [[1, 2, 3]],
    }))
    probe = {
        "status": "passed",
        "bit_exact": True,
        "registered_bytes": probe_module.PROBE_BYTES,
    }

    merged = probe_module.merge_probe_result(result_path, probe)

    assert merged["output_token_ids"] == [[1, 2, 3]]
    assert merged["exact_probe_deferred"] is False
    assert merged["exact_session_count"] == 1
    assert merged["exact_post_run_finalized"] is True
    assert merged["exact_probe"] == probe
    assert json.loads(result_path.read_text()) == merged


def test_rejects_non_deferred_result(tmp_path):
    result_path = tmp_path / "result.json"
    result_path.write_text(json.dumps({"mode": "baseline"}))

    with pytest.raises(RuntimeError, match="deferred exact"):
        probe_module.merge_probe_result(
            result_path, {"status": "passed", "bit_exact": True}
        )


def test_rejects_probe_without_bit_exact_oracle(tmp_path):
    result_path = tmp_path / "result.json"
    result_path.write_text(json.dumps({
        "mode": "exact",
        "exact_scope": "one_shot_sideband_probe",
        "model_graph_fidelity": "native",
        "exact_probe_deferred": True,
        "exact_session_count": 0,
        "exact_post_run_finalized": False,
        "exact_probe": None,
    }))

    with pytest.raises(RuntimeError, match="bit-exact"):
        probe_module.merge_probe_result(
            result_path,
            {"status": "passed", "bit_exact": False},
        )


def test_probe_output_oracle_rejects_corruption():
    input_values = probe_module.probe_input_values()
    expected = probe_module.expected_probe_output(input_values)

    assert probe_module.validate_probe_output(
        input_values, expected
    ) == expected
    corrupted = list(expected)
    corrupted[17] ^= 1
    with pytest.raises(RuntimeError, match="output mismatch"):
        probe_module.validate_probe_output(input_values, corrupted)


def test_probe_output_matches_uint64_golden_vectors():
    """Golden values computed independently with wrapping uint64 operations."""
    output = probe_module.expected_probe_output(
        probe_module.probe_input_values()
    )

    assert {
        index: output[index] for index in (0, 1, 17, 127)
    } == {
        0: 0xBE92798D00E3E69B,
        1: 0x1F616B6AB17C4327,
        17: 0x9B2012CA31BD67A6,
        127: 0xAD4CC0BE9043BA1C,
    }


def test_native_preheat_precedes_exact_owner():
    source = (ADAPTER / "run_exact_probe.py").read_text()

    assert source.index('"native probe preheat"') < \
        source.index('"exact session creation"') < \
        source.index('"exact contract publication"')


def test_ordinary_channel_reservation_is_warp_coalesced():
    source = (
        ADAPTER.parents[1] / "src/cuda_runtime/device/hbf_device.cu"
    ).read_text()

    assert "reserve_sm120_channels_warp" in source
    assert "__activemask()" in source
    assert "__ffs(active)" in source
    assert "__shfl_sync(active" in source
    assert "warp_hybrid_reference_decision" in source
    assert re.search(
        r"system_fetch_add\(\s*&header->fast_request_sequence,\s*active_count\)",
        source,
    )
    assert source.count("reserve_sm120_channels_warp(") == 3
