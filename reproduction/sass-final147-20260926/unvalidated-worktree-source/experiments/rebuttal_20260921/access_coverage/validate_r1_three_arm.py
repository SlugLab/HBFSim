#!/usr/bin/env python3
"""Strict validator for the paired R1 native, D0-control, and nominal arms."""

from __future__ import annotations

import argparse
import collections
import hashlib
import json
from pathlib import Path
from typing import Any


COUNTERS = (
    "supported_accesses", "supported_bytes",
    "in_range_accesses", "in_range_intersection_bytes",
    "native_out_of_range_accesses", "native_out_of_range_bytes",
    "modeled_admitted_accesses", "modeled_admitted_bytes",
    "service_completed_accesses", "service_completed_bytes",
    "failed_after_issue_accesses", "failed_after_issue_bytes",
    "unsupported_preissue_accesses", "unsupported_preissue_bytes",
    "failed_preissue_accesses", "failed_preissue_bytes",
    "translation_failed_accesses", "translation_failed_bytes",
    "service_requests", "unclassified_accesses", "unclassified_bytes",
    "counter_overflow",
)
ZERO_COUNTERS = (
    "failed_after_issue_accesses", "failed_after_issue_bytes",
    "unsupported_preissue_accesses", "unsupported_preissue_bytes",
    "failed_preissue_accesses", "failed_preissue_bytes",
    "translation_failed_accesses", "translation_failed_bytes",
    "unclassified_accesses", "unclassified_bytes", "counter_overflow",
)
PAIRED_FIELDS = (
    "schema_version", "git_commit", "model", "versions", "compiler", "attention_backend", "moe_backend",
    "moe_backend_control", "input_len", "output_len", "max_model_len",
    "max_num_batched_tokens", "num_prompts", "seed", "prompt_token_ids",
    "warmup_requests", "warmup_output_token_ids",
    "hbf_timing_model", "cache_root",
)
ALLOWED_ARM_DIFFERENCES = (
    "mode", "profile", "request_accounting", "eval_delay_ns",
    "accounting_epoch", "access_accounting", "generation_seconds",
    "requests_per_second", "output_tokens_per_second",
    "total_tokens_per_second", "load_seconds", "scientific_status",
    "triton_exact_bindings",
)


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text())


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def add(checks: dict[str, bool], errors: list[str], name: str, value: Any) -> None:
    checks[name] = bool(value)
    if not value:
        errors.append(name)


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def hash_map(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in path.read_text().splitlines():
        digest, filename = line.split(maxsplit=1)
        values[filename.strip()] = digest
    return values


def validate_access(arm: str, directory: Path, result: dict[str, Any],
                    checks: dict[str, bool], errors: list[str]) -> dict[str, Any]:
    access = (result.get("access_accounting") or {}).get("access") or {}
    aggregate = access.get("aggregate") or {}
    modules = access.get("per_module") or []
    add(checks, errors, f"{arm}.access.complete",
        access.get("status") == "COMPLETE"
        and access.get("disable_complete") is True
        and (access.get("synchronization") or {}).get("before_snapshot") is True
        and (access.get("synchronization") or {}).get("after_disable") is True)
    add(checks, errors, f"{arm}.access.modules_present", bool(modules))
    for index, module in enumerate(modules):
        counters = module.get("counters") or {}
        prefix = f"{arm}.module[{index}]"
        add(checks, errors, prefix + ".complete", module.get("status") == "COMPLETE")
        add(checks, errors, prefix + ".all_fields", all(k in counters for k in COUNTERS))
        add(checks, errors, prefix + ".nonnegative",
            all(isinstance(counters.get(k), int) and counters[k] >= 0 for k in COUNTERS))
        add(checks, errors, prefix + ".N_eq_M_eq_K",
            counters.get("in_range_accesses") == counters.get("modeled_admitted_accesses")
            == counters.get("service_completed_accesses"))
        add(checks, errors, prefix + ".byte_closure",
            counters.get("in_range_intersection_bytes") == counters.get("modeled_admitted_bytes")
            == counters.get("service_completed_bytes"))
        add(checks, errors, prefix + ".count_partition",
            counters.get("supported_accesses")
            == (counters.get("in_range_accesses", -1)
                + counters.get("native_out_of_range_accesses", -1)))
        add(checks, errors, prefix + ".byte_partition",
            counters.get("supported_bytes")
            == (counters.get("in_range_intersection_bytes", -1)
                + counters.get("native_out_of_range_bytes", -1)))
        add(checks, errors, prefix + ".zero_failures",
            all(counters.get(k) == 0 for k in ZERO_COUNTERS))
    for key in COUNTERS:
        add(checks, errors, f"{arm}.aggregate.sum.{key}",
            isinstance(aggregate.get(key), int)
            and aggregate[key] == sum((m.get("counters") or {}).get(key, -1) for m in modules))
    add(checks, errors, f"{arm}.aggregate.positive_N", aggregate.get("in_range_accesses", 0) > 0)
    add(checks, errors, f"{arm}.aggregate.N_eq_M_eq_K",
        aggregate.get("in_range_accesses") == aggregate.get("modeled_admitted_accesses")
        == aggregate.get("service_completed_accesses"))
    add(checks, errors, f"{arm}.aggregate.byte_closure",
        aggregate.get("in_range_intersection_bytes") == aggregate.get("modeled_admitted_bytes")
        == aggregate.get("service_completed_bytes"))
    add(checks, errors, f"{arm}.aggregate.zero_failures",
        all(aggregate.get(k) == 0 for k in ZERO_COUNTERS))

    manifests = read_jsonl(directory / "pass-manifests.jsonl")
    manifest_ids = collections.Counter(m.get("module_id") for m in manifests)
    snapshot_ids = collections.Counter(m.get("identity") for m in modules)
    add(checks, errors, f"{arm}.four_supported_manifests",
        len(manifests) == 4 and all(
            m.get("instrumented") is True
            and m.get("unsupported_instructions") == 0
            and not m.get("unsupported_opcodes")
            and not m.get("unsupported_parameters")
            and m.get("rewritten_instructions", 0) > 0
            for m in manifests))
    add(checks, errors, f"{arm}.module_identity_multiset", snapshot_ids == manifest_ids)
    bindings = read_jsonl(directory / "triton-bindings.jsonl")
    fused_bindings = [b for b in bindings if b.get("kernel_name") == "fused_moe_kernel"]
    non_target_bindings = [b for b in bindings if b.get("kernel_name") != "fused_moe_kernel"]
    add(checks, errors, f"{arm}.six_exact_fused_bindings",
        len(fused_bindings) == 6 and all(
            b.get("kernel_name") == "fused_moe_kernel" and b.get("result") == "bound"
            for b in fused_bindings))
    add(checks, errors, f"{arm}.non_target_binding_scope",
        len(non_target_bindings) == 1
        and all(b.get("kernel_name") == "_copy_page_indices_kernel"
            and b.get("result") == "variant_not_found" for b in non_target_bindings))
    return {
        "aggregate": aggregate,
        "module_identities": sorted(snapshot_ids.elements()),
        "non_target_bindings": non_target_bindings,
    }


def coverage_scope(directory: Path) -> dict[str, int]:
    counts = collections.Counter()
    for row in read_jsonl(directory / "coverage.jsonl"):
        fused = row.get("kernel") == "fused_moe_kernel"
        if row.get("modeled") is True:
            counts["modeled_total"] += 1
            counts["modeled_fused"] += fused
        if row.get("opaque_unmodeled") is True:
            counts["opaque_unknown_total"] += 1
            counts["opaque_unknown_fused"] += fused
    return dict(counts)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--native", type=Path, required=True)
    parser.add_argument("--d0", type=Path, required=True)
    parser.add_argument("--nominal", type=Path, required=True)
    parser.add_argument("--joint-bundle-manifest", type=Path, required=True)
    parser.add_argument("--native-launcher", type=Path, required=True)
    parser.add_argument("--d0-launcher", type=Path, required=True)
    parser.add_argument("--nominal-launcher", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    directories = {"native": args.native, "d0": args.d0, "nominal": args.nominal}
    results = {arm: load_json(path / "result.json") for arm, path in directories.items()}
    checks: dict[str, bool] = {}
    errors: list[str] = []

    for arm, directory in directories.items():
        result = results[arm]
        add(checks, errors, f"{arm}.exit_zero",
            (directory / "exit_code.txt").is_file()
            and (directory / "exit_code.txt").read_text().strip() == "0")
        add(checks, errors, f"{arm}.request_success",
            result.get("request_terminal_status") == "success"
            and result.get("scientific_status") == "COMPLETE")
    reference = results["native"]
    for arm in ("d0", "nominal"):
        add(checks, errors, f"{arm}.token_matches_native",
            results[arm].get("output_token_ids_sha256") == reference.get("output_token_ids_sha256"))
        for key in PAIRED_FIELDS:
            add(checks, errors, f"{arm}.paired.{key}",
                key in results[arm] and key in reference
                and results[arm][key] == reference[key])

    add(checks, errors, "native.accounting_off",
        reference.get("request_accounting") is False and reference.get("eval_delay_ns") == -1)
    add(checks, errors, "native.selection_off",
        reference.get("hbf_selection") == {"max_bytes_per_storage": 0, "parameter_regex": ""})
    add(checks, errors, "d0.control_mode",
        results["d0"].get("request_accounting") is True
        and results["d0"].get("eval_delay_ns") == 0
        and results["d0"].get("accounting_epoch", 0) > 0)
    add(checks, errors, "nominal.mode",
        results["nominal"].get("request_accounting") is True
        and results["nominal"].get("eval_delay_ns") == -1
        and results["nominal"].get("accounting_epoch", 0) > 0)
    expected_selection = {
        "max_bytes_per_storage": 16384,
        "parameter_regex": r"^model\.layers\.0\.mlp\.experts\.w13_weight$",
    }
    add(checks, errors, "instrumented.selection_pair",
        results["d0"].get("hbf_selection") == expected_selection
        and results["nominal"].get("hbf_selection") == expected_selection)
    for arm, launcher in (
        ("native", args.native_launcher),
        ("d0", args.d0_launcher),
        ("nominal", args.nominal_launcher),
    ):
        launcher_text = launcher.read_text()
        add(checks, errors, f"{arm}.gpu_memory_launcher_0_60",
            "--gpu-memory-utilization 0.60" in launcher_text)

    arm_access = {
        arm: validate_access(arm, directories[arm], results[arm], checks, errors)
        for arm in ("d0", "nominal")
    }
    d0_eval = (results["d0"].get("access_accounting") or {}).get("eval_delay") or {}
    add(checks, errors, "d0.eval.complete",
        d0_eval.get("status") == "COMPLETE"
        and d0_eval.get("delay_ns") == 0
        and d0_eval.get("disable_complete") is True
        and d0_eval.get("free_complete") is True
        and d0_eval.get("final_sync") is True)
    add(checks, errors, "d0.eval.same_epoch",
        d0_eval.get("epoch") == results["d0"].get("accounting_epoch"))
    add(checks, errors, "d0.access.same_epoch",
        ((results["d0"].get("access_accounting") or {}).get("access") or {}).get("epoch")
        == results["d0"].get("accounting_epoch"))
    eval_aggregate = d0_eval.get("aggregate") or {}
    add(checks, errors, "d0.eval.no_overflow",
        "trace_overflow" in eval_aggregate and eval_aggregate["trace_overflow"] == 0)
    add(checks, errors, "d0.eval.no_rejected",
        "rejected_accesses" in eval_aggregate and eval_aggregate["rejected_accesses"] == 0)
    add(checks, errors, "d0.eval.module_identity_matches_access",
        collections.Counter(m.get("identity") for m in d0_eval.get("per_module", []))
        == collections.Counter(arm_access["d0"]["module_identities"]))
    eval_counter_fields = (
        "covered_accesses", "covered_bytes", "bypass_accesses", "bypass_bytes",
        "rejected_accesses", "trace_overflow",
    )
    for index, module in enumerate(d0_eval.get("per_module", [])):
        counters = module.get("counters") or {}
        traces = module.get("traces") or []
        prefix = f"d0.eval.module[{index}]"
        add(checks, errors, prefix + ".complete", module.get("status") == "COMPLETE")
        add(checks, errors, prefix + ".all_fields",
            all(key in counters for key in eval_counter_fields))
        add(checks, errors, prefix + ".zero_failures",
            counters.get("rejected_accesses") == 0 and counters.get("trace_overflow") == 0)
        add(checks, errors, prefix + ".trace_count",
            len(traces) == counters.get("covered_accesses"))
        add(checks, errors, prefix + ".trace_records",
            all(trace.get("delay_ns") == 0
                and isinstance(trace.get("wait_enter_ns"), int)
                and trace.get("wait_exit_ns") == trace.get("wait_enter_ns")
                for trace in traces))
    for key in eval_counter_fields:
        add(checks, errors, f"d0.eval.aggregate.sum.{key}",
            key in eval_aggregate
            and eval_aggregate[key] == sum(
                (m.get("counters") or {}).get(key, -1)
                for m in d0_eval.get("per_module", [])))
    add(checks, errors, "d0.eval.covered_matches_services",
        eval_aggregate.get("covered_accesses")
        == arm_access["d0"]["aggregate"].get("service_requests"))
    add(checks, errors, "d0.eval.trace_drop_unavailable",
        d0_eval.get("trace_drop") is None
        and all(m.get("trace_drop") is None for m in d0_eval.get("per_module", [])))
    add(checks, errors, "nominal.eval_absent",
        "eval_delay" not in (results["nominal"].get("access_accounting") or {}))

    d0_inputs = hash_map(args.d0 / "input-files.sha256")
    nominal_inputs = hash_map(args.nominal / "input-files.sha256")
    add(checks, errors, "paired.instrumented_input_map", d0_inputs == nominal_inputs)
    manifest_entries = hash_map(args.joint_bundle_manifest)
    gate_path = next((p for p in d0_inputs if p.endswith("/libhbfsim_launch_gate.so")), None)
    adapter_path = next((p for p in d0_inputs if p.endswith("/adapters/vllm/run.py")), None)
    add(checks, errors, "paired.gate_in_manifest",
        gate_path is not None and any(
            path.endswith("/libhbfsim_launch_gate.so") and digest == d0_inputs[gate_path]
            for path, digest in manifest_entries.items()))
    add(checks, errors, "paired.adapter_frozen",
        adapter_path is not None and d0_inputs[adapter_path] == sha256(Path(adapter_path)))

    d0_profile = load_json(Path(results["d0"]["profile"]))
    nominal_profile = load_json(Path(results["nominal"]["profile"]))
    d0_without_scale = dict(d0_profile)
    nominal_without_scale = dict(nominal_profile)
    d0_scale = d0_without_scale.pop("time_scale", None)
    nominal_scale = nominal_without_scale.pop("time_scale", None)
    add(checks, errors, "d0.profile_scale_one", d0_scale == 1)
    add(checks, errors, "d0.profile_only_scale_diff",
        d0_without_scale == nominal_without_scale and nominal_scale != d0_scale)

    scopes = {arm: coverage_scope(directories[arm]) for arm in ("d0", "nominal")}
    for arm, scope in scopes.items():
        add(checks, errors, f"{arm}.target_fused_no_opaque",
            scope.get("modeled_fused", 0) > 0 and scope.get("opaque_unknown_fused", 0) == 0)
    add(checks, errors, "joint_bundle_manifest_present", bool(manifest_entries))

    report = {
        "schema_version": 1,
        "status": "PASS" if all(checks.values()) else "FAILED_VALIDATION",
        "checks": checks,
        "errors": errors,
        "arms": {arm: {
            "directory": str(directory.resolve()),
            "result_sha256": sha256(directory / "result.json"),
            "input_files_sha256": sha256(directory / "input-files.sha256"),
            "token_sha256": results[arm].get("output_token_ids_sha256"),
            "generation_seconds_software": results[arm].get("generation_seconds"),
        } for arm, directory in directories.items()},
        "access": arm_access,
        "coverage_scope": scopes,
        "allowed_arm_differences": ALLOWED_ARM_DIFFERENCES,
        "claim_scope": {
            "target": "supported fused_moe_kernel sites in the four instrumented PTX module identities",
            "whole_program_opaque": "UNKNOWN and reported separately",
            "d0": "INSTRUMENTATION_ZERO_DELAY_CONTROL; not nominal HBF service",
            "wall_time": "software execution receipt only; not hardware latency",
            "service_requests": "grouped service operations; not required to equal logical accesses",
            "trace_drop": "UNKNOWN/UNAVAILABLE; trace_overflow=0 and trace records equal covered service operations",
        },
        "joint_bundle_manifest": str(args.joint_bundle_manifest.resolve()),
        "joint_bundle_manifest_sha256": sha256(args.joint_bundle_manifest),
        "launcher_receipts": {
            arm: {"path": str(path.resolve()), "sha256": sha256(path)}
            for arm, path in (
                ("native", args.native_launcher),
                ("d0", args.d0_launcher),
                ("nominal", args.nominal_launcher),
            )
        },
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["status"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
