import argparse
import os
import pathlib
import sys

import pytest


ADAPTER = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ADAPTER))

import run as run_module  # noqa: E402


def args(**updates):
    values = {
        "hbf_weight_selection": None,
        "hbf_parameter_regex": "",
        "hbf_include_pattern": [],
        "hbf_exclude_pattern": [],
        "hbf_range_bytes": 0,
        "hbf_accept_storage_closure": False,
        "hbf_instrumentation_policy": None,
    }
    values.update(updates)
    return argparse.Namespace(**values)


def test_new_default_is_all_strict():
    policy = run_module.resolve_weight_policy(args())
    assert policy["selection"] == "all"
    assert policy["instrumentation_policy"] == "strict"
    assert policy["legacy"] is False


def test_legacy_regex_and_cap_remain_partial_and_scoped():
    policy = run_module.resolve_weight_policy(args(
        hbf_parameter_regex=r"^model\.layers\.0\.", hbf_range_bytes=16384))
    assert policy["selection"] == "include"
    assert policy["include_patterns"] == [r"^model\.layers\.0\."]
    assert policy["instrumentation_policy"] == "partial"
    assert policy["legacy"] is True


def test_explicit_strict_cap_is_rejected():
    with pytest.raises(SystemExit, match="forbids storage truncation"):
        run_module.resolve_weight_policy(args(
            hbf_range_bytes=16, hbf_instrumentation_policy="strict"))


def test_off_rejects_binding_filters_and_does_not_touch_environment(monkeypatch):
    monkeypatch.delenv("HBFSIM_INSTRUMENTATION_POLICY", raising=False)
    policy = run_module.resolve_weight_policy(args(hbf_weight_selection="off"))
    run_module.configure_instrumentation_policy(policy)
    assert "HBFSIM_INSTRUMENTATION_POLICY" not in os.environ
    with pytest.raises(SystemExit, match="cannot have binding filters"):
        run_module.resolve_weight_policy(args(
            hbf_weight_selection="off", hbf_exclude_pattern=["lm_head"]))


def test_process_policy_conflict_fails_closed(monkeypatch):
    monkeypatch.setenv("HBFSIM_INSTRUMENTATION_POLICY", "partial")
    with pytest.raises(SystemExit, match="conflicting process-global"):
        run_module.configure_instrumentation_policy(
            run_module.resolve_weight_policy(args()))


def test_native_mode_call_site_can_skip_environment_activation(monkeypatch):
    # main() only calls configure_instrumentation_policy for timing mode.  This
    # test protects the helper contract used by native/off callers.
    monkeypatch.delenv("HBFSIM_INSTRUMENTATION_POLICY", raising=False)
    policy = run_module.resolve_weight_policy(args())
    assert policy["instrumentation_policy"] == "strict"
    assert "HBFSIM_INSTRUMENTATION_POLICY" not in os.environ

