"""Derive train/development DOMAIN_V2 inputs from one frozen steady alpha."""
import argparse
import copy
import hashlib
import json
import math
from pathlib import Path


ALPHAS = (1.0, 0.75, 0.5, 0.25)
ALLOWED_TRACES = ("train", "development")


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def finite_nonnegative(value, label):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label} must be numeric")
    value = float(value)
    if not math.isfinite(value) or value < 0:
        raise ValueError(f"{label} must be finite and nonnegative")
    return value


def selected_alpha(receipt):
    if (receipt.get("schema_version") != "eq3-steady-envelope-v1" or
            receipt.get("status") != "PREDICTED_ENVELOPE" or
            receipt.get("reference_qualified") is not False):
        raise ValueError("steady receipt is not a conditional predicted envelope")
    alpha = finite_nonnegative(receipt.get("selected_alpha"), "selected_alpha")
    if alpha not in ALPHAS:
        raise ValueError("selected_alpha is outside the frozen candidate set")
    candidates = receipt.get("candidates")
    if not isinstance(candidates, list) or [item.get("alpha") for item in candidates] != list(ALPHAS):
        raise ValueError("steady receipt does not retain the frozen candidate order")
    eligible = [float(item["alpha"]) for item in candidates
                if item.get("within_limit") is True and item.get("initial_covered") is True]
    if not eligible or alpha != eligible[0]:
        raise ValueError("selected_alpha is not the largest eligible frozen candidate")
    return alpha


def derive(power, receipt, traces):
    traces = tuple(traces)
    if not traces or len(traces) != len(set(traces)) or any(item not in ALLOWED_TRACES for item in traces):
        raise ValueError("only unique train/development traces may be derived")
    alpha = selected_alpha(receipt)
    order = power.get("group_order")
    caps = power.get("caps_W")
    if (not isinstance(order, list) or len(order) != 17 or len(set(order)) != 17 or
            not isinstance(caps, list) or len(caps) != len(order)):
        raise ValueError("power source ledger must retain 17 ordered groups and caps")
    original_caps = [finite_nonnegative(value, f"caps_W[{index}]")
                     for index, value in enumerate(caps)]
    source_traces = power.get("traces")
    if not isinstance(source_traces, dict):
        raise ValueError("power source ledger lacks traces")
    derived_traces = {}
    energy = {}
    slot_s = finite_nonnegative(power.get("slot_s"), "slot_s")
    if slot_s <= 0:
        raise ValueError("slot_s must be positive")
    for trace_id in traces:
        source = source_traces.get(trace_id)
        if not isinstance(source, dict):
            raise ValueError(f"missing requested trace {trace_id}")
        slots = source.get("slots_W")
        if not isinstance(slots, list) or not slots:
            raise ValueError(f"trace {trace_id} lacks slots_W")
        scaled_slots = []
        original_j = 0.0
        scaled_j = 0.0
        for slot_index, raw in enumerate(slots):
            if not isinstance(raw, list) or len(raw) != len(order):
                raise ValueError(f"trace {trace_id} slot {slot_index} violates frozen vector order")
            values = [finite_nonnegative(value, f"{trace_id}.slots_W[{slot_index}]")
                      for value in raw]
            if any(value > cap + 1e-12 for value, cap in zip(values, original_caps, strict=True)):
                raise ValueError(f"trace {trace_id} exceeds the public group cap")
            scaled = [value * alpha for value in values]
            scaled_slots.append(scaled)
            original_j += slot_s * math.fsum(values)
            scaled_j += slot_s * math.fsum(scaled)
        trace = copy.deepcopy(source)
        trace["slots_W"] = scaled_slots
        derived_traces[trace_id] = trace
        energy[trace_id] = {"original_energy_j": original_j,
                            "scaled_energy_j": scaled_j}
    derived = {key: copy.deepcopy(value) for key, value in power.items() if key != "traces"}
    derived["schema_version"] = "eq3-domain-v2-power-v1"
    derived["status"] = "DOMAIN_V2_IN_RANGE_DERIVED_INPUT"
    derived["caps_W"] = [value * alpha for value in original_caps]
    derived["traces"] = derived_traces
    derived["domain_v2"] = {
        "alpha": alpha,
        "alpha_candidates": list(ALPHAS),
        "source_semantics": "uniform scale of every variable source group; static model sources unchanged",
        "blind_trace_included": False,
        "future_blind_rule": "after explicit unseal, apply this same frozen alpha without reselection",
        "reference_qualification_inherited": False,
    }
    result = {
        "schema_version": "eq3-domain-v2-input-receipt-v1",
        "status": "DERIVED_INPUT_NOT_EXECUTED",
        "alpha": alpha,
        "traces_read": list(traces),
        "blind_trajectory_read": False,
        "future_blind_rule": "same frozen alpha after dependency-controlled unseal",
        "group_count": len(order),
        "original_caps_w": dict(zip(order, original_caps, strict=True)),
        "scaled_caps_w": dict(zip(order, derived["caps_W"], strict=True)),
        "energy": energy,
        "reference_qualification_inherited": False,
    }
    return derived, result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--power", type=Path, required=True)
    parser.add_argument("--steady-receipt", type=Path, required=True)
    parser.add_argument("--trace", action="append", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--receipt-output", type=Path, required=True)
    args = parser.parse_args()
    for output in (args.output, args.receipt_output):
        if output.exists():
            raise FileExistsError(f"refusing to overwrite {output}")
    derived, receipt = derive(json.loads(args.power.read_text()),
                              json.loads(args.steady_receipt.read_text()), args.trace)
    receipt["source_sha256"] = {
        "power": sha256(args.power), "steady_receipt": sha256(args.steady_receipt)}
    text = json.dumps(derived, indent=2) + "\n"
    receipt["derived_power_sha256"] = hashlib.sha256(text.encode()).hexdigest()
    args.output.write_text(text)
    args.receipt_output.write_text(json.dumps(receipt, indent=2) + "\n")
    print(json.dumps({"status": receipt["status"], "alpha": receipt["alpha"],
                      "traces": receipt["traces_read"]}))


if __name__ == "__main__":
    main()
