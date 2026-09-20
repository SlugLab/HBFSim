"""Disjoint incremental-energy scopes for the isolated system experiment.

Coefficients are scenario/proxy inputs, never facts inferred from an address.
The caller supplies observed/modelled activity identity and explicit placement.
"""
from __future__ import annotations

from collections import defaultdict
import math


def engineering_energy_profile():
    return {
        "read_array_j_per_byte": 40e-12,
        "read_base_j_per_byte": 10e-12,
        "program_array_j_per_byte": 0.05 * 100e-6 / 4096,
        "program_base_j_per_byte": 0.01 * 100e-6 / 4096,
        "erase_array_j_per_operation": None,
        "relay_partner_receive_j_per_byte": 2e-12,
        "relay_partner_send_j_per_byte": 2e-12,
        "hbm_array_j_per_byte": 40e-12,
        "hbm_base_j_per_byte": 2e-12,
        "ecc_j_per_byte": 0.0,
        "ecc_scope": "ZERO_INCREMENT_BASELINE_NOT_MEASURED_ZERO; scenario overrides explicit",
        "evidence": {
            "read": "USER_CONFIRMED_40_ARRAY_10_BASE_PJ_PER_B",
            "program": "DERIVED_ENGINEERING_PROXY_OLD_0.05W_0.01W_100US_4KIB; NOT_HBF_CALIBRATION",
            "relay_hbm": "REUSED_EQ3_MAINTENANCE_ENERGY_PROFILE_SCENARIO",
            "erase": "UNKNOWN_REQUIRES_EXPLICIT_DURATION_AND_POWER",
        },
    }


class EnergyMapper:
    def __init__(self, normalized, channel_map, profile):
        self.components = {c["id"] for c in normalized["components"]}
        self.channels = channel_map
        self.hbm_dies = defaultdict(list)
        for row in normalized["components"]:
            if row.get("physical_type") == "HBM" and row.get("role") == "array_die":
                self.hbm_dies[row["device_id"]].append(row["id"])
        self.profile = profile

    def map(self, activities, *, gpu_compute_j=0.0, gpu_external_j=0.0):
        sources = defaultdict(float)
        scopes = defaultdict(float)
        seen = set()

        def add(component, joules, scope):
            if component not in self.components:
                raise ValueError(f"energy component absent: {component}")
            if not math.isfinite(joules) or joules < 0:
                raise ValueError("invalid energy")
            sources[component] += joules
            scopes[scope] += joules

        for row in activities:
            if "activity_id" in row:
                if row["activity_id"] in seen:
                    raise ValueError("duplicate activity identity in energy window")
                seen.add(row["activity_id"])
            op, stack, size = row["operation"], row["stack"], row["bytes"]
            if type(size) is not int or size < 0:
                raise ValueError("activity bytes must be nonnegative integer")
            if op in {"read", "retry", "refresh_read", "migration_read"}:
                channel = str(row["channel"])
                die = self.channels[stack][channel]
                add(die, size * self.profile["read_array_j_per_byte"], op + ":array")
                add(stack + ".base", size * self.profile["read_base_j_per_byte"], op + ":base")
            elif op in {"program", "refresh_program", "migration_program"}:
                die = self.channels[stack][str(row["channel"])]
                add(die, size * self.profile["program_array_j_per_byte"], op + ":array")
                add(stack + ".base", size * self.profile["program_base_j_per_byte"], op + ":base")
            elif op == "erase":
                coefficient = self.profile["erase_array_j_per_operation"]
                if coefficient is None:
                    raise ValueError("erase energy UNKNOWN; explicit operation coefficient required")
                count = row["operations"]
                if type(count) is not int or count < 0:
                    raise ValueError("invalid erase count")
                add(self.channels[stack][str(row["channel"])], count * coefficient, "erase:array")
            elif op == "relay_receive":
                add(stack + ".base", size * self.profile["relay_partner_receive_j_per_byte"], "relay:receive")
            elif op == "relay_send":
                add(stack + ".base", size * self.profile["relay_partner_send_j_per_byte"], "relay:send")
            elif op == "hbm_read":
                dies = self.hbm_dies[stack]
                if not dies:
                    raise ValueError("HBM array source unavailable")
                # Parametric service observes stack only; uniform thermal allocation
                # is a declared scenario, not claimed observed physical die identity.
                for die in dies:
                    add(die, size * self.profile["hbm_array_j_per_byte"] / len(dies), "hbm:array_uniform_proxy")
                add(stack + ".base", size * self.profile["hbm_base_j_per_byte"], "hbm:base")
            elif op == "ecc":
                add(stack + ".base", size * self.profile["ecc_j_per_byte"], "ecc:base")
            else:
                raise ValueError(f"unsupported energy activity: {op}")
        add("gpu", gpu_compute_j, "gpu:causal_compute")
        add("gpu", gpu_external_j, "gpu:independent_external")
        return {"component_energy_j": dict(sources), "scope_energy_j": dict(scopes),
                "total_j": sum(sources.values()),
                "evidence": "CONDITIONAL_INCREMENTAL_MODELLED_ACTIVITY_NOT_CALIBRATED_POWER"}
