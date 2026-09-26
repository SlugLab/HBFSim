#!/usr/bin/env python3
"""Validate one access-only R2 range cell against final R1 nominal."""

import argparse
import collections
import hashlib
import json
from pathlib import Path

COUNTERS = (
    "supported_accesses", "supported_bytes", "in_range_accesses",
    "in_range_intersection_bytes", "native_out_of_range_accesses",
    "native_out_of_range_bytes", "modeled_admitted_accesses",
    "modeled_admitted_bytes", "service_completed_accesses",
    "service_completed_bytes", "failed_after_issue_accesses",
    "failed_after_issue_bytes", "unsupported_preissue_accesses",
    "unsupported_preissue_bytes", "failed_preissue_accesses",
    "failed_preissue_bytes", "translation_failed_accesses",
    "translation_failed_bytes", "service_requests", "unclassified_accesses",
    "unclassified_bytes", "counter_overflow",
)
ZERO = (
    "failed_after_issue_accesses", "failed_after_issue_bytes",
    "unsupported_preissue_accesses", "unsupported_preissue_bytes",
    "failed_preissue_accesses", "failed_preissue_bytes",
    "translation_failed_accesses", "translation_failed_bytes",
    "unclassified_accesses", "unclassified_bytes", "counter_overflow",
)


def load(path):
    return json.loads(Path(path).read_text())


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def jsonl(path):
    return [json.loads(x) for x in Path(path).read_text().splitlines() if x]


def hash_map(path):
    values = {}
    for line in Path(path).read_text().splitlines():
        value, filename = line.split(maxsplit=1)
        values[filename.strip()] = value
    return values


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--plan", type=Path, required=True)
    p.add_argument("--reference-dir", type=Path, required=True)
    p.add_argument("--cell-dir", type=Path, required=True)
    p.add_argument("--expected-range", type=int, required=True)
    p.add_argument("--output", type=Path, required=True)
    a = p.parse_args()
    plan, ref = load(a.plan), load(a.reference_dir / "result.json")
    result, reg = load(a.cell_dir / "result.json"), load(a.cell_dir / "registration.json")
    checks, errors = {}, []
    def check(name, value):
        checks[name] = bool(value)
        if not value:
            errors.append(name)

    check("plan_ready", plan.get("status") == "READY_NOT_RUN")
    check("exit_zero", (a.cell_dir / "exit_code.txt").read_text().strip() == "0")
    check("request_success", result.get("scientific_status") == "COMPLETE"
          and result.get("request_terminal_status") == "success")
    check("token_matches_final_r1", result.get("output_token_ids_sha256")
          == ref.get("output_token_ids_sha256"))
    for key in (
        "schema_version", "git_commit", "model", "versions", "compiler",
        "attention_backend", "moe_backend", "moe_backend_control", "input_len",
        "output_len", "max_model_len", "max_num_batched_tokens", "num_prompts",
        "seed", "prompt_token_ids", "warmup_requests", "warmup_output_token_ids",
        "hbf_timing_model", "cache_root", "profile",
    ):
        check("paired." + key, key in result and key in ref and result[key] == ref[key])
    check("paired.input_hash_map", hash_map(a.cell_dir / "input-files.sha256")
          == hash_map(a.reference_dir / "input-files.sha256"))
    check("accounting_mode", result.get("request_accounting") is True
          and result.get("eval_delay_ns") == -1
          and result.get("accounting_epoch", 0) > 0)
    check("selection_range", (result.get("hbf_selection") or {}).get("max_bytes_per_storage")
          == a.expected_range)
    check("selection_regex", (result.get("hbf_selection") or {}).get("parameter_regex")
          == (ref.get("hbf_selection") or {}).get("parameter_regex"))
    check("registered_range", reg.get("registered_bytes") == a.expected_range)
    stores = reg.get("storages") or []
    check("single_storage", len(stores) == 1
          and int(stores[0].get("storage_bytes", 0)) == plan.get("object_full_bytes"))
    account = (result.get("access_accounting") or {}).get("access") or {}
    check("eval_inactive", "eval_delay" not in (result.get("access_accounting") or {}))
    check("snapshot_complete", account.get("status") == "COMPLETE"
          and account.get("disable_complete") is True
          and (account.get("synchronization") or {}).get("before_snapshot") is True
          and (account.get("synchronization") or {}).get("after_disable") is True)
    modules, aggregate = account.get("per_module") or [], account.get("aggregate") or {}
    check("modules_present", bool(modules))
    for i, module in enumerate(modules):
        c = module.get("counters") or {}
        prefix = f"module[{i}]"
        check(prefix + ".complete", module.get("status") == "COMPLETE")
        check(prefix + ".fields", all(k in c for k in COUNTERS))
        check(prefix + ".nonnegative", all(isinstance(c.get(k), int) and c[k] >= 0 for k in COUNTERS))
        check(prefix + ".N_M_K", c.get("in_range_accesses") == c.get("modeled_admitted_accesses")
              == c.get("service_completed_accesses"))
        check(prefix + ".bytes", c.get("in_range_intersection_bytes") == c.get("modeled_admitted_bytes")
              == c.get("service_completed_bytes"))
        check(prefix + ".count_partition", c.get("supported_accesses")
              == c.get("in_range_accesses", -1) + c.get("native_out_of_range_accesses", -1))
        check(prefix + ".byte_partition", c.get("supported_bytes")
              == c.get("in_range_intersection_bytes", -1) + c.get("native_out_of_range_bytes", -1))
        check(prefix + ".zero_failures", all(c.get(k) == 0 for k in ZERO))
    for key in COUNTERS:
        check("aggregate.sum." + key, aggregate.get(key)
              == sum((m.get("counters") or {}).get(key, -1) for m in modules))
    check("aggregate.positive", aggregate.get("in_range_accesses", 0) > 0)
    check("aggregate.N_M_K", aggregate.get("in_range_accesses")
          == aggregate.get("modeled_admitted_accesses") == aggregate.get("service_completed_accesses"))
    check("aggregate.bytes", aggregate.get("in_range_intersection_bytes")
          == aggregate.get("modeled_admitted_bytes") == aggregate.get("service_completed_bytes"))
    check("aggregate.zero_failures", all(aggregate.get(k) == 0 for k in ZERO))
    manifests = jsonl(a.cell_dir / "pass-manifests.jsonl")
    check("four_supported_manifests", len(manifests) == 4 and all(
        m.get("instrumented") is True and m.get("unsupported_instructions") == 0
        and not m.get("unsupported_opcodes") and not m.get("unsupported_parameters")
        and m.get("rewritten_instructions", 0) > 0 for m in manifests))
    check("module_identity", collections.Counter(m.get("module_id") for m in manifests)
          == collections.Counter(m.get("identity") for m in modules))
    bindings = jsonl(a.cell_dir / "triton-bindings.jsonl")
    fused = [b for b in bindings if b.get("kernel_name") == "fused_moe_kernel"]
    other = [b for b in bindings if b.get("kernel_name") != "fused_moe_kernel"]
    check("six_fused_bindings", len(fused) == 6 and all(b.get("result") == "bound" for b in fused))
    check("scoped_non_target", len(other) == 1 and other[0].get("kernel_name") == "_copy_page_indices_kernel"
          and other[0].get("result") == "variant_not_found")
    coverage = jsonl(a.cell_dir / "coverage.jsonl")
    modeled_fused = sum(x.get("kernel") == "fused_moe_kernel" and x.get("modeled") is True for x in coverage)
    opaque_fused = sum(x.get("kernel") == "fused_moe_kernel" and x.get("opaque_unmodeled") is True for x in coverage)
    opaque_total = sum(x.get("opaque_unmodeled") is True for x in coverage)
    check("target_fused_supported", modeled_fused > 0 and opaque_fused == 0)
    report = {
        "schema_version": 2, "status": "PASS" if all(checks.values()) else "FAILED_VALIDATION",
        "expected_range_bytes": a.expected_range, "checks": checks, "errors": errors,
        "aggregate": aggregate, "result_sha256": digest(a.cell_dir / "result.json"),
        "registration_sha256": digest(a.cell_dir / "registration.json"),
        "token_sha256": result.get("output_token_ids_sha256"),
        "generation_seconds_software": result.get("generation_seconds"),
        "coverage_scope": {"modeled_fused": modeled_fused, "opaque_fused": opaque_fused,
                           "whole_program_opaque_unknown": opaque_total},
        "claim_scope": "registered range and supported in-range fused-MoE accesses; not every byte in the range",
    }
    a.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["status"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
