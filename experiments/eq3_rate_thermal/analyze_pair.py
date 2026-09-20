#!/usr/bin/env python3
"""Compare equal-energy uniform and channel-concentrated rate-thermal runs."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


BASELINE_K = 300.0
SPLIT_NS = 1_400_000_000
RECOVERY_NS = 2_200_000_000
END_NS = 3_200_000_000


def load_json(path: Path):
    return json.loads(path.read_text())


def load_jsonl(path: Path):
    return [json.loads(line) for line in path.read_text().splitlines() if line]


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def maximum(records):
    return max(records, key=lambda item: item["delta_k"])


def minimum(records):
    return min(records, key=lambda item: item["delta_k"])


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--uniform", type=Path, required=True)
    parser.add_argument("--concentrated", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=False)

    points = {"uniform": args.uniform.resolve(), "concentrated": args.concentrated.resolve()}
    done = {arm: load_json(path / "DONE.json") for arm, path in points.items()}
    manifests = {arm: load_json(path / "manifest.json") for arm, path in points.items()}
    profiles = {arm: load_json(path / "profile.json") for arm, path in points.items()}
    loads = {arm: load_json(path / "load-windows.json") for arm, path in points.items()}
    frames = {arm: load_jsonl(path / "thermal.jsonl") for arm, path in points.items()}
    for arm in points:
        if done[arm]["execution_status"] != "COMPLETED":
            raise ValueError(f"{arm} is not complete")
        if len(frames[arm]) != 160 or frames[arm][-1]["end_ns"] != END_NS:
            raise ValueError(f"{arm} frame count or end time differs from the registered pair")
    if profiles["uniform"] != profiles["concentrated"]:
        raise ValueError("profiles differ")
    identity_keys = ("thermal_binary_sha256", "model_file_sha256", "model_dir", "step_ns")
    identity = {key: manifests["uniform"].get(key) for key in identity_keys}
    if any(manifests["concentrated"].get(key) != value for key, value in identity.items()):
        raise ValueError("thermal model or executable identity differs")
    times = {arm: [frame["end_ns"] for frame in rows] for arm, rows in frames.items()}
    if times["uniform"] != times["concentrated"]:
        raise ValueError("frame timestamps differ")

    # Same requested bytes and stack energy at every window; only die placement may differ.
    energy_windows = {}
    for arm, payload in loads.items():
        energy_windows[arm] = [sum(w["component_energy_j"].values()) for w in payload["windows"]]
    byte_mismatch = []
    for wu, wc in zip(loads["uniform"]["windows"], loads["concentrated"]["windows"]):
        for stack in wu["stacks"]:
            if wu["stacks"][stack]["requested_bytes"] != wc["stacks"][stack]["requested_bytes"]:
                byte_mismatch.append((wu["end_ns"], stack))
    max_window_energy_delta = max(abs(a - b) for a, b in zip(*energy_windows.values()))
    total_energy = {arm: sum(values) for arm, values in energy_windows.items()}
    if byte_mismatch or max_window_energy_delta > 1e-12 or abs(total_energy["uniform"] - total_energy["concentrated"]) > 1e-10:
        raise ValueError("pair does not conserve the same requested bytes and energy")
    placement_windows = 0
    for wu, wc in zip(loads["uniform"]["windows"], loads["concentrated"]["windows"]):
        differing = wu["start_ns"] >= SPLIT_NS and wu["end_ns"] <= RECOVERY_NS
        if not differing:
            if wu["component_energy_j"] != wc["component_energy_j"]:
                raise ValueError("component placement differs outside the registered phase")
            continue
        placement_windows += 1
        for component, energy in wu["component_energy_j"].items():
            if not component.startswith("hbf0.die") and wc["component_energy_j"][component] != energy:
                raise ValueError("a source outside HBF0 dies differs in the placement phase")
        for arm, window, expected_active in (("uniform", wu, 16), ("concentrated", wc, 4)):
            channel_bytes = window["stacks"]["hbf0"]["channel_requested_bytes"]
            active = [value for value in channel_bytes.values() if value > 0]
            if len(active) != expected_active or max(active) != min(active):
                raise ValueError(f"{arm} channel placement does not match the registered pair")
    if placement_windows != 40:
        raise ValueError("unexpected number of channel-placement windows")

    indexed = {arm: {row["end_ns"]: row for row in rows} for arm, rows in frames.items()}
    pre_records = []
    for t in times["uniform"]:
        if t > SPLIT_NS:
            continue
        u, c = indexed["uniform"][t], indexed["concentrated"][t]
        for name in u["entity_temperatures_k"]:
            for metric in ("hotspot_k", "mean_k"):
                pre_records.append(abs(c["entity_temperatures_k"][name][metric] - u["entity_temperatures_k"][name][metric]))
        for name in u["sensor_temperatures_k"]:
            pre_records.append(abs(c["sensor_temperatures_k"][name] - u["sensor_temperatures_k"][name]))
    pre_max = max(pre_records, default=0.0)
    if pre_max > 1e-12:
        raise ValueError(f"pre-divergence states differ by {pre_max} K")

    compared = []
    names = [f"hbf0.die{i}" for i in range(16)] + ["hbf0.base"]
    for t in times["uniform"]:
        if SPLIT_NS < t <= RECOVERY_NS:
            for name in names:
                for metric in ("hotspot_k", "mean_k"):
                    compared.append({
                        "time_ns": t, "entity": name, "metric": metric,
                        "delta_k": indexed["concentrated"][t]["entity_temperatures_k"][name][metric]
                                   - indexed["uniform"][t]["entity_temperatures_k"][name][metric],
                    })
    stack_deltas = []
    for t in times["uniform"]:
        if SPLIT_NS < t <= RECOVERY_NS:
            stack_deltas.append({"time_ns": t, "entity": "hbf0", "metric": "stack_hotspot_k",
                                 "delta_k": indexed["concentrated"][t]["temperatures"]["hbf0"]
                                            - indexed["uniform"][t]["temperatures"]["hbf0"]})

    at_22 = {}
    for name in names:
        at_22[name] = {}
        for arm in points:
            state = indexed[arm][RECOVERY_NS]["entity_temperatures_k"][name]
            at_22[name][arm + "_hotspot_rise_k"] = state["hotspot_k"] - BASELINE_K
            at_22[name][arm + "_mean_rise_k"] = state["mean_k"] - BASELINE_K
        at_22[name]["hotspot_delta_k"] = (at_22[name]["concentrated_hotspot_rise_k"]
                                             - at_22[name]["uniform_hotspot_rise_k"])
        at_22[name]["mean_delta_k"] = (at_22[name]["concentrated_mean_rise_k"]
                                          - at_22[name]["uniform_mean_rise_k"])

    phase_peak = {}
    for arm in points:
        candidates = [(t, indexed[arm][t]["temperatures"]["hbf0"])
                      for t in times[arm] if SPLIT_NS < t <= RECOVERY_NS]
        t, value = max(candidates, key=lambda item: item[1])
        phase_peak[arm] = {"time_ns": t, "hbf0_stack_hotspot_rise_k": value - BASELINE_K}

    recovery = {}
    for arm in points:
        recovery[arm] = {}
        for stack in indexed[arm][END_NS]["temperatures"]:
            recovery[arm][stack] = {
                "rise_at_2p2s_k": indexed[arm][RECOVERY_NS]["temperatures"][stack] - BASELINE_K,
                "rise_at_3p2s_k": indexed[arm][END_NS]["temperatures"][stack] - BASELINE_K,
                "change_during_recovery_k": indexed[arm][END_NS]["temperatures"][stack]
                                            - indexed[arm][RECOVERY_NS]["temperatures"][stack],
            }

    hbf3_zero = {}
    for arm in points:
        requested = sum(w["stacks"]["hbf3"]["requested_bytes"] for w in loads[arm]["windows"])
        hbf3_zero[arm] = {
            "requested_bytes": requested,
            "peak_rise_k": done[arm]["peak_k_by_stack"]["hbf3"] - BASELINE_K,
            "rise_at_2p2s_k": indexed[arm][RECOVERY_NS]["temperatures"]["hbf3"] - BASELINE_K,
            "final_rise_k": indexed[arm][END_NS]["temperatures"]["hbf3"] - BASELINE_K,
        }
        if requested != 0:
            raise ValueError("hbf3 is not an unpowered coupling observation")

    aligned = []
    for t in times["uniform"]:
        if t <= SPLIT_NS:
            continue
        u, c = indexed["uniform"][t], indexed["concentrated"][t]
        aligned.append({
            "time_ns": t,
            "stack_hotspot_delta_k": {name: c["temperatures"][name] - value
                                      for name, value in u["temperatures"].items()},
            "hbf0_component_delta_k": {
                name: {metric: c["entity_temperatures_k"][name][metric] -
                               u["entity_temperatures_k"][name][metric]
                       for metric in ("hotspot_k", "mean_k")}
                for name in names
            },
        })

    conservation = {}
    for arm in points:
        cumulative = indexed[arm][END_NS]["energy_j"]["cumulative"]
        conservation[arm] = {
            **cumulative,
            "absolute_residual_fraction": abs(cumulative["energy_residual_j"]) /
                                          max(cumulative["total_input_j"], 1.0),
        }

    result = {
        "schema_version": 1,
        "status": "PASS",
        "classification": "CONDITIONAL_SIMULATED_INCREMENTAL_HEATING",
        "interpretation": "300 K is a common reference initial condition; values are simulated rises, not measured product temperatures.",
        "inputs": {
            arm: {"path": str(path), "done_sha256": sha256(path / "DONE.json"),
                  "thermal_sha256": sha256(path / "thermal.jsonl"),
                  "load_windows_sha256": sha256(path / "load-windows.json"),
                  "schedule_sha256": sha256(path / "schedule.json"),
                  "profile_sha256": sha256(path / "profile.json")}
            for arm, path in points.items()
        },
        "identity": identity,
        "frames": 160,
        "time_step_ns": 20_000_000,
        "total_energy_j": total_energy,
        "max_per_window_total_energy_difference_j": max_window_energy_delta,
        "pre_1p4s_equivalence": {"max_temperature_difference_k": pre_max, "passed": True},
        "differing_phase_ns": [SPLIT_NS, RECOVERY_NS],
        "placement_windows": placement_windows,
        "phase_hbf0_stack_peak": phase_peak,
        "phase_hbf0_stack_delta_extrema": {"maximum": maximum(stack_deltas), "minimum": minimum(stack_deltas)},
        "phase_hbf0_component_delta_extrema": {"maximum": maximum(compared), "minimum": minimum(compared)},
        "snapshot_2p2s": at_22,
        "hbf3_unpowered_coupling": hbf3_zero,
        "recovery_to_3p2s": recovery,
        "energy_conservation": conservation,
    }

    aligned_path = out / "aligned-differences.jsonl"
    aligned_path.write_text("".join(json.dumps(row, allow_nan=False) + "\n" for row in aligned))
    result["aligned_differences"] = {
        "path": aligned_path.name,
        "sha256": sha256(aligned_path),
        "frames_after_1p4s": len(aligned),
        "contents": "all stack hotspots plus HBF0 die/base hotspot and mean deltas at each aligned timestamp",
    }

    # Plot after all assertions, so a figure cannot outlive a failed comparison.
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np

    fig, axes = plt.subplots(1, 3, figsize=(15.2, 4.5))
    t_s = np.array(times["uniform"]) / 1e9
    rate = []
    for w in loads["uniform"]["windows"]:
        dt = (w["end_ns"] - w["start_ns"]) / 1e9
        rate.append(w["stacks"]["hbf0"]["requested_bytes"] / dt / 1e12)
    axes[0].step(t_s, rate, where="post", color="black", label="both arms")
    axes[0].set(title="Identical HBF0 stack rate", xlabel="Time (s)", ylabel="Prescribed rate (TB/s)")
    axes[0].legend(frameon=False)
    for arm, color in (("uniform", "#2878B5"), ("concentrated", "#D95319")):
        rise = [row["temperatures"]["hbf0"] - BASELINE_K for row in frames[arm]]
        axes[1].plot(t_s, rise, color=color, label=arm)
    axes[1].set(title="HBF0 stack hotspot rise", xlabel="Time (s)", ylabel="Rise from 300 K (K)")
    axes[1].legend(frameon=False)
    x = np.arange(17)
    width = 0.4
    for offset, arm, color in ((-width/2, "uniform", "#2878B5"), (width/2, "concentrated", "#D95319")):
        values = [at_22[f"hbf0.die{i}"][arm + "_hotspot_rise_k"] for i in range(16)]
        values.append(at_22["hbf0.base"][arm + "_hotspot_rise_k"])
        axes[2].bar(x + offset, values, width, color=color, label=arm)
    axes[2].set(title="HBF0 component hotspot rise at 2.2 s", xlabel="Thermal component", ylabel="Rise from 300 K (K)")
    axes[2].set_xticks(x, [str(i) for i in range(16)] + ["base"], rotation=55)
    axes[2].legend(frameon=False)
    for axis in axes:
        axis.axvspan(1.4, 2.2, color="#888888", alpha=0.09)
        axis.grid(alpha=0.2)
    fig.suptitle("Equal-rate, equal-energy channel placement comparison (conditional simulation)")
    fig.tight_layout()
    fig.savefig(out / "paired.png", dpi=180)
    plt.close(fig)

    result["analyzer_sha256"] = sha256(Path(__file__))
    (out / "PAIR_RESULT.json").write_text(json.dumps(result, indent=2, allow_nan=False) + "\n")

    smax = result["phase_hbf0_stack_delta_extrema"]["maximum"]
    smin = result["phase_hbf0_stack_delta_extrema"]["minimum"]
    cmax = result["phase_hbf0_component_delta_extrema"]["maximum"]
    cmin = result["phase_hbf0_component_delta_extrema"]["minimum"]
    report = f"""# OCP Grade 2 equal-energy channel-placement pair

Both arms completed 160 aligned 20 ms frames through 3.2 s. They use the same model, executable, profile, stack-rate schedule, 300 K initial condition and **{total_energy['uniform']:.6f} J** incremental read energy. Before 1.4 s their full entity/sensor temperature state agrees within **{pre_max:.3e} K**. From 1.4–2.2 s HBF0 remains at 0.384 TB/s: the uniform arm assigns 24 GB/s to each of 16 channels, while the concentrated arm assigns 96 GB/s to four channels.

The concentrated-minus-uniform HBF0 stack-hotspot difference spans **{smin['delta_k']:+.6f} to {smax['delta_k']:+.6f} K** during that phase. Across HBF0 dies/base and both hotspot/mean observations, the extrema are **{cmin['delta_k']:+.6f} K** ({cmin['entity']}, {cmin['metric']}, {cmin['time_ns']/1e9:.2f} s) and **{cmax['delta_k']:+.6f} K** ({cmax['entity']}, {cmax['metric']}, {cmax['time_ns']/1e9:.2f} s). This reports the simulated spatial response without assuming beforehand which placement must be hotter.

HBF3 receives zero requested bytes in both arms, yet reaches peak rises of **{hbf3_zero['uniform']['peak_rise_k']:.6f} K** and **{hbf3_zero['concentrated']['peak_rise_k']:.6f} K**, respectively; this is coupled-package heating. At 3.2 s HBF0's stack-hotspot rise is **{recovery['uniform']['hbf0']['rise_at_3p2s_k']:.6f} K** (uniform) and **{recovery['concentrated']['hbf0']['rise_at_3p2s_k']:.6f} K** (concentrated), after changes of **{recovery['uniform']['hbf0']['change_during_recovery_k']:+.6f} K** and **{recovery['concentrated']['hbf0']['change_during_recovery_k']:+.6f} K** during recovery.

Final thermal energy residuals are **{conservation['uniform']['energy_residual_j']:.3e} J** and **{conservation['concentrated']['energy_residual_j']:.3e} J**. These are conditional incremental-heating simulations: 300 K is a shared reference initial state, not a claim about absolute product temperature. See `PAIR_RESULT.json` for every die/base value at 2.2 s and [the paired figure](paired.png).
"""
    (out / "PAIR_RESULT.md").write_text(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
