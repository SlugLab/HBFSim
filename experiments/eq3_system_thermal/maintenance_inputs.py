"""Freeze bounded maintenance-pilot additions without launching a run."""
from __future__ import annotations

import copy

try:
    from .reliability import DAY_NS
except ImportError:
    from reliability import DAY_NS


def add_maintenance(base_config: dict, mode: str) -> dict:
    if mode not in {"disabled", "shared", "ideal_independent"}:
        raise ValueError("unknown maintenance mode")
    result = copy.deepcopy(base_config)
    result["point_id"] = result["point_id"] + ":maintenance:" + mode
    result["maintenance"] = {
        "mode": mode,
        "ea_ev": 1.04,
        "refresh_trigger": "equivalent_age_or_wall",
        # Every arm gets the same literal near-due scenario input.  Disabled is
        # still a fresh no-maintenance baseline and does not instantiate age.
        "initial_equivalent_age_ns": DAY_NS - 2_000_000_000,
        "initial_wall_age_ns": 0,
        "initial_temperature_k": 300.0,
        "block_bytes": 4096 * 256,
        "pages_per_block": 256,
        "aged_blocks_per_stack": 4096,
        "spares_per_channel": 16,
        "max_blocks_per_cohort": 16,
        "program_energy_j_per_byte": 0.05 * 100e-6 / 4096,
        "erase_energy_j_per_operation": 0.05 * 1e-3,
        "energy_evidence": {
            "program": "DERIVED_ENGINEERING_PROXY_0.05W_100US_NOT_HBF_CALIBRATION",
            "erase": "DERIVED_ENGINEERING_PROXY_0.05W_NATIVE_ADAPTER_1MS_NOT_HBF_CALIBRATION",
        },
    }
    result["maintenance_scope"] = {
        "aged_subset_bytes_per_hbf_stack": 4 * 1024**3,
        "aged_subset_semantics": "EXPLICIT_SUBSET_NOT_WHOLE_MODEL_OR_PRODUCT_CAPACITY",
        "spares_per_stack": 16 * 16,
        "spare_ownership": "16_PER_EACH_OF_16_CHANNELS_NO_CROSS_CHANNEL_MOVE",
        "backend": "AGGREGATE_RATE_SERVICE_NOT_NATIVE_MQSIM",
        "initial_age_fairness": (
            "SHARED_AND_IDEAL_ARMS_USE_IDENTICAL_DAY_MINUS_2S_EQUIVALENT_AGE;"
            "DISABLED_IS_FRESH_NO_MAINTENANCE_DIAGNOSTIC;NOT_24H_TIME_COMPRESSION"
        ),
    }
    return result


__all__ = ["add_maintenance"]
