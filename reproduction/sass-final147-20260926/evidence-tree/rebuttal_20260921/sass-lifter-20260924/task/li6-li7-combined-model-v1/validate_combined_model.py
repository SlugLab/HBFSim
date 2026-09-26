#!/usr/bin/env python3
"""Read-only, fail-closed combined model evidence validator; never starts a worker."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path

TASK = Path(__file__).resolve().parent
ROOT = TASK.parents[1]
BASELINE = ROOT / "runs/old-baseline-v3/native/result/cell/result.json"
LI6 = "ptx:sha256:6db074711d19c3e31cbcc98c6e170f0b4330259f220c2d518ce8aee42b9c9eab"
LI7 = "ptx:sha256:e70c7c4bb6429dba28291f4c22d9897b96de3d82e6d87b73689b9beff4bec2d5"
COUNTS = {"mixed3": (3, 3), "remaining45": (45, 45), "added49": (49, 147)}
ACCESS = ("in_range_accesses", "modeled_admitted_accesses", "service_completed_accesses")
BYTES = ("in_range_intersection_bytes", "modeled_admitted_bytes", "service_completed_bytes")
ERRORS = ("counter_overflow", "failed_preissue_accesses", "failed_after_issue_accesses",
          "translation_failed_accesses", "unsupported_preissue_accesses", "unclassified_accesses")

def need(ok, message):
    if not ok: raise RuntimeError(message)

def load(path): return json.loads(Path(path).read_text())
def rows(path): return [json.loads(x) for x in Path(path).read_text().splitlines() if x.strip()]
def sha(path): return hashlib.sha256(Path(path).read_bytes()).hexdigest()

def maps_match(text, path, device, inode):
    for line in text.splitlines():
        fields = line.split(maxsplit=5)
        if len(fields) != 6 or fields[5] != path: continue
        try:
            major, minor = (int(x, 16) for x in fields[3].split(":"))
            if (os.makedev(major, minor), int(fields[4])) == (device, inode): return True
        except ValueError: pass
    return False

def closed(counters):
    return (isinstance(counters, dict) and
            all(isinstance(counters.get(k), int) for k in ACCESS + BYTES + ERRORS) and
            counters["in_range_accesses"] > 0 and counters["in_range_intersection_bytes"] > 0 and
            len({counters[k] for k in ACCESS}) == 1 and
            len({counters[k] for k in BYTES}) == 1 and
            all(counters[k] == 0 for k in ERRORS))

def positive_hex(value):
    try: return isinstance(value, str) and value.startswith("0x") and int(value, 16) > 0
    except ValueError: return False

def expected_manifest(plan):
    scope = plan.get("scope")
    need(scope in COUNTS, "unknown combined scope")
    path = Path(plan["scope_manifest_path"])
    need(path.is_file() and sha(path) == plan["scope_manifest_sha256"], "scope manifest identity")
    manifest = load(path)
    need(manifest.get("schema") == "hbfsim.combined_selected_scope.v1" and
         manifest.get("scope") == scope and len(manifest.get("targets", [])) == COUNTS[scope][0],
         "scope manifest count")
    staged = load(TASK / "prepared-v2/STAGE_JOIN_RECEIPT.json")
    need(plan.get("stage_join_sha256") == sha(TASK / "prepared-v2/STAGE_JOIN_RECEIPT.json") and
         staged["scope_manifest_sha256"][scope] == plan["scope_manifest_sha256"] and
         plan.get("plugin_sha256") == sha(TASK / "combined_model_adapter/__init__.py"),
         "plugin/stage/scope review identity")
    need(plan.get("output_dir") and isinstance(plan.get("epoch"), int) and
         plan.get("env", {}).get("HBFSIM_COMBINED_MODEL_PLUGIN_V1") == "1" and
         plan["env"].get("HBFSIM_COMBINED_SCOPE_MANIFEST") == str(path) and
         plan["env"].get("HBFSIM_COMBINED_SCOPE_SHA256") == plan["scope_manifest_sha256"],
         "plan activation/scope identity")
    return manifest

def validate_capture(run, plan):
    cap = run / "live-capture-resolved-v1"
    joins = load(cap / "path-join.json")
    target = load(cap / "target-start-capture.json")
    worker = load(run / "controller/worker-start.json")
    owner = load(run / "owner-start.json")
    need(joins.get("status") == "EXACT_CONFIGURED_TO_RESOLVED_INODE_JOIN" and
         joins.get("owner_start_sha256") == sha(run / "owner-start.json") and
         target.get("status") == "CAPTURED" and target.get("required_maps_present") is True and
         target.get("pid") == joins.get("target_pid") and
         target.get("start_ticks") == joins.get("target_start_ticks") and
         target.get("parent_pid") == worker.get("pid") and
         target.get("wrapper", {}).get("pid") == worker.get("pid") and
         target["wrapper"].get("start_ticks") == worker.get("start_ticks"),
         "owner/worker/target capture linkage")
    expected = plan.get("backing_identities")
    need(isinstance(expected, list) and len(expected) >= 3, "reviewed backing identities absent")
    captured = {r["configured_path"]: r for r in joins["libraries"]}
    need(set(captured) == {r["configured"] for r in expected}, "actual captured library set")
    maps = (cap / "target-start-maps.txt").read_text()
    for identity in expected:
        configured = Path(identity["configured"])
        stat = configured.stat()
        row = captured[str(configured)]
        need(row["resolved_path"] == str(configured.resolve(strict=True)) == identity["resolved"] and
             (stat.st_dev, stat.st_ino) == (row["device"], row["inode"]) ==
             (identity["device"], identity["inode"]) and
             maps_match(maps, row["resolved_path"], stat.st_dev, stat.st_ino),
             f"loaded map/backing path-device-inode mismatch: {configured}")
    actual_env = load(cap / "target-start-env.json")
    keys = ("HBFSIM_COMBINED_MODEL_PLUGIN_V1", "HBFSIM_COMBINED_SCOPE_MANIFEST",
            "HBFSIM_COMBINED_SCOPE_SHA256", "HBFSIM_COMBINED_MODEL_RECEIPT_DIR",
            "HBFSIM_QKV_COMBINED_V1", "HBFSIM_LI6_SOURCE_PTX_PATH",
            "HBFSIM_LI6_STAGED_PTX_PATH", "HBFSIM_LI6_STAGED_PTX_SHA256",
            "HBFSIM_LI6_ABI_MAP_SHA256", "HBFSIM_LI7_SOURCE_PTX_PATH",
            "HBFSIM_LI7_STAGED_PTX_PATH", "HBFSIM_LI7_STAGED_PTX_SHA256",
            "HBFSIM_LI7_ABI_MAP_SHA256", "BPFTIME_CUDA_LATE_PTX_DIR",
            "HBFSIM_TARGET_EXTRA_LD_PRELOAD", "HBFSIM_QKV_ABI_DECISION_LOG",
            "HBFSIM_PROVIDER_CORRELATION_LOG", "HBFSIM_COVERAGE_PATH")
    for key in keys:
        need(key in plan["env"] and actual_env.get(key) == plan["env"][key],
             f"target environment differs: {key}")
    cuda_env=load(cap/"selected-cuda-env.json")
    need(cuda_env.get("target_pid")==target["pid"] and
         cuda_env.get("target_start_ticks")==target["start_ticks"] and
         cuda_env.get("env",{}).get("CUDA_DEVICE_ORDER") ==
         plan["env"].get("CUDA_DEVICE_ORDER") == "PCI_BUS_ID" and
         cuda_env["env"].get("CUDA_VISIBLE_DEVICES") == plan["env"].get("CUDA_VISIBLE_DEVICES") ==
         str(plan["configured_gpu_index"]),"configured CUDA device order/visibility changed")
    occupancy=load(cap/"selected-gpu-occupancy.json")
    need(occupancy.get("status")=="TARGET_ON_CONFIGURED_GPU" and
         occupancy.get("configured_gpu_index")==plan["configured_gpu_index"] and
         occupancy.get("target_pid")==target["pid"] and
         occupancy.get("observed_target_gpu_rows")==
         [[occupancy.get("observed_selected_uuid"),str(target["pid"])]],
         "actual selected-device target occupancy not proven")
    return target

def check_receipts(scope, manifest, registration, events, decisions, driver, coverage, access):
    """Evidence relationships only; file identities and raw byte files are checked by validate()."""
    selected = manifest["targets"]
    alias_rows = {row["alias"]: row for row in selected}
    need(len(alias_rows) == len(selected), "duplicate scope target")
    registered = registration.get("storages", [])
    need(registration.get("unique_storage_count") == COUNTS[scope][1] == len(registered) and
         registration.get("selection", {}).get("instrumentation_policy") == "partial" and
         registration.get("registered_bytes") == sum(r.get("bytes", 0) for r in registered),
         "registration count/policy/bytes")
    reg = {}
    for row in registered:
        need(len(row.get("aliases", [])) == 1 and row["aliases"][0] not in reg and
             isinstance(row.get("address"), int) and row["address"] > 0 and
             row.get("bytes") == row.get("storage_bytes") and row["bytes"] > 0,
             "duplicate/invalid registered storage")
        reg[row["aliases"][0]] = row
    if scope == "added49":
        ledger = load(ROOT / "task/full-coverage-ledger-v1/TARGET_LEDGER.json")
        expected = {alias: row["storage_bytes"] for row in ledger["entries"]
                    for alias in row["aliases"]}
        need(len(expected) == 147 and sum(expected.values()) == 13838323712,
             "final 147 storage ledger/bytes changed")
    else:
        expected = {row["alias"]: row["storage_bytes"] for row in selected}
    need({alias: row["bytes"] for alias, row in reg.items()} == expected,
         "registration aliases/bytes differ from exact scope")
    need(events and [e.get("event") for e in events[:3]] ==
         ["installed", "scheduled_prefill", "scheduled_decode"] and
         events[-1].get("event") == "selected_execute_complete" and
         not any(e.get("event") in ("candidate_exception", "end_exception") for e in events),
         "plugin lifecycle missing/incomplete")
    installed, prefill, decode, complete = events[0], events[1], events[2], events[-1]
    request, tid = decode.get("request_id"), decode.get("os_thread_id")
    need(request and prefill.get("request_id") == request == complete.get("request_id") and
         prefill.get("positions") == [0, 1] and
         isinstance(decode.get("positions"), list) and len(decode["positions"]) == 1 and
         isinstance(decode["positions"][0], int) and decode["positions"][0] > 0 and
         complete.get("status") == "ALL_SELECTED_COMPLETE" and
         complete.get("selected_count") == len(selected) and
         installed.get("selected_storage_count") == len(selected),
         "scheduled request/completion identity")
    installed_targets = {x["alias"]: x for x in installed.get("targets", [])}
    need(len(installed_targets) == len(selected) and set(installed_targets) == set(alias_rows),
         "installed target set")
    outputs = [e for e in events if e.get("event") == "selected_output"]
    need(len(outputs) == len(selected) and
         {e.get("alias") for e in outputs} == set(alias_rows) and
         len({e.get("selected_order") for e in outputs}) == len(selected),
         "missing/duplicate selected output")
    outputs.sort(key=lambda e: e["selected_order"])
    need([e["selected_order"] for e in outputs] == list(range(1, len(selected)+1)),
         "selected order gap/duplication")
    patched = [d for d in decisions if d.get("decision") == "PATCHED_SELECTED"]
    need(len(patched) == len(selected) and not any(d.get("decision") == "REJECTED" for d in decisions),
         "selected decision count/rejection")
    need(len({d.get("call_ordinal") for d in patched}) == len(patched) and
         all(isinstance(d.get("call_ordinal"), int) and d["call_ordinal"] > 0 for d in patched),
         "decision ordinal identity")
    module_rows = access.get("per_module", [])
    modules = {r.get("identity"): r for r in module_rows}
    need(len(modules) == len(module_rows) and access.get("status") == "COMPLETE" and
         access.get("disable_complete") is True and closed(access.get("aggregate")),
         "aggregate access/service closure")
    for module in (LI6, LI7):
        need(module in modules and modules[module].get("status") == "COMPLETE" and
             closed(modules[module].get("counters")), "selected module service closure")
    pass_rows = rows(TASK / "prepared-v2/pass-manifests.jsonl")
    old_ids = {r["module_id"] for r in pass_rows} - {LI6, LI7}
    need(len(old_ids) == 7, "old98 module set changed")
    by_storage = []
    for alias, storage in reg.items():
        target = alias_rows.get(alias)
        allowed = {LI6 if target["profile"] == "li6" else LI7} if target else old_ids
        base, size = storage["address"], storage["bytes"]
        matched = [r for r in coverage if r.get("modeled") is True and
                   r.get("module_id") in allowed and isinstance(r.get("address"), int) and
                   base <= r["address"] < base + size and
                   r["module_id"] in modules and
                   modules[r["module_id"]].get("status") == "COMPLETE" and
                   closed(modules[r["module_id"]].get("counters"))]
        need(matched, f"storage lacks address-matched modeled active service: {alias}")
        by_storage.append({"alias": alias, "base": base, "bytes": size,
                           "active_complete_modules": sorted({r["module_id"] for r in matched})})
    matched_decisions = set()
    used_patched_correlations = set()
    last_patched_correlation = -1
    selected_summary = []
    for output in outputs:
        alias = output["alias"]; row = alias_rows[alias]; storage = reg[alias]
        installed_row = installed_targets[alias]
        base, size = storage["address"], storage["bytes"]
        need(output.get("status") == "BYTE_EQUAL" and
             output.get("select_rc") == output.get("end_rc") == 0 and
             output.get("request_id") == request and output.get("os_thread_id") == tid == complete.get("os_thread_id") and
             output.get("profile") == row["profile"] and
             output.get("category") == row["category"] and output.get("layer") == row["layer"] and
             output.get("weight_ptr") == installed_row.get("base") == hex(base) and
             output.get("weight_bytes") == installed_row.get("bytes") == size and
             output.get("output_shape") == row["output_shape"] and
             output.get("output_dtype") == "torch.bfloat16" and
             output.get("output_bytes") == row["output_bytes"] and
             output.get("native_output_ptr") != output.get("candidate_output_ptr") and
             output.get("native_sha256") == output.get("candidate_sha256"),
             f"selected output/request/storage contract: {alias}")
        candidates = [(i,d) for i,d in enumerate(patched) if
                      d.get("selected_base") == hex(base) and d.get("selected_bytes") == size and
                      d.get("profile") == row["profile"] and d.get("os_thread_id") == tid]
        need(len(candidates) == 1 and candidates[0][0] not in matched_decisions,
             f"missing/duplicate exact selected decision: {alias}")
        i, decision = candidates[0]; matched_decisions.add(i)
        driver_profile = row["native_driver"]
        need(decision.get("reason") == "one_patched_call" and decision.get("cuda_result") == 0 and
             decision.get("original_function") != decision.get("patched_function") and
             isinstance(decision.get("association_token"), int) and decision["association_token"] > 0 and
             all(positive_hex(decision.get(k)) for k in
                 ("original_function", "patched_function", "context")) and
             decision.get("grid") == driver_profile["grid"] and
             decision.get("block") == driver_profile["block"] and
             decision.get("shared_bytes") == driver_profile["dynamic_shared_bytes"],
             f"selected exact geometry/context: {alias}")
        def common_driver(d, function):
            return (d.get("schema") == "hbfsim.provider.driver_launch.v3" and
                    d.get("function_handle") == function and
                    d.get("api") == driver_profile["api"] and
                    d.get("cbid") == driver_profile["cbid"] and
                    d.get("callback_tid") == tid and
                    d.get("symbol_observation") == driver_profile["symbol"])
        originals = [d for d in driver if common_driver(d, decision["original_function"]) and
                     d.get("identity_state") == "EXACT" and
                     any(c.get("sha256") == driver_profile["image_sha256"] and
                         c.get("durable") is True and c.get("byte_kind") == "FATBIN_BYTES"
                         for c in d.get("module_candidates", []))]
        need(originals, f"actual original exact image/API/CBID/thread link: {alias}")
        links = [d for d in driver if common_driver(d, decision["patched_function"]) and
                 d.get("identity_state") == "TOPOLOGY_ONLY" and
                 isinstance(d.get("correlation_id"), int) and
                 d["correlation_id"] > last_patched_correlation and
                 d["correlation_id"] not in used_patched_correlations]
        need(links, f"unique actual patched topology/API/CBID/thread link: {alias}")
        chosen = min(links, key=lambda d: d["correlation_id"])
        used_patched_correlations.add(chosen["correlation_id"])
        last_patched_correlation = chosen["correlation_id"]
        selected_summary.append({"alias": alias, "base": base, "bytes": size,
                                 "profile": row["profile"], "decision_ordinal": decision["call_ordinal"]})
    need(len(matched_decisions) == len(patched) and
         [x["decision_ordinal"] for x in selected_summary] ==
         sorted(x["decision_ordinal"] for x in selected_summary),
         "selected plugin/agent order linkage")
    patched_handles = {d["patched_function"] for d in patched}
    actual_patched = [d for d in driver if d.get("schema") == "hbfsim.provider.driver_launch.v3" and
                      d.get("function_handle") in patched_handles and d.get("callback_tid") == tid]
    need(len(actual_patched) == len(selected) and
         len({d.get("correlation_id") for d in actual_patched}) == len(selected) and
         all(isinstance(d.get("correlation_id"), int) for d in actual_patched),
         "surplus/duplicate selected patched callback")
    return {"by_storage": by_storage, "selected": selected_summary,
            "old98_count": len(reg)-len(selected), "selected_count": len(selected)}

def check_result(result, baseline, epoch):
    need(result.get("accounting_epoch") == epoch and
         result.get("request_terminal_status") == "success" and
         result.get("scientific_status") == "COMPLETE" and
         result.get("output_token_ids") == baseline.get("output_token_ids") and
         result.get("output_token_ids_sha256") == baseline.get("output_token_ids_sha256"),
         "model result/epoch/native baseline mismatch")

def validate(plan_path, run):
    plan = load(plan_path)
    plan_sha = sha(plan_path)
    need(str(run / "controller") == plan.get("output_dir") and
         sha(run / "plan.json") == plan_sha and
         load(run / "owner-start.json").get("plan_sha256") == plan_sha and
         load(run / "controller/controller-start.json").get("plan_sha256") == plan_sha,
         "run/owner/controller plan identity differs")
    manifest = expected_manifest(plan)
    target = validate_capture(run, plan)
    control = load(run / "controller/controller-finish.json")
    need(control.get("status") == "PROCESS_RC0_PENDING_RESULT_VALIDATION" and
         control.get("worker_returncode") == 0 and control.get("guard_clean") is True,
         "bounded controller/guard/worker incomplete")
    need(control.get("guard_returncode") == 0 and control.get("plan_sha256") == plan_sha,
         "bounded controller/guard/worker incomplete")
    result = load(run / "result/cell/result.json")
    baseline = load(BASELINE)
    check_result(result, baseline, plan["epoch"])
    registration = load(run / "result/cell/registration.json")
    logs = list(Path(plan["env"]["HBFSIM_COMBINED_MODEL_RECEIPT_DIR"]).glob("worker-*.jsonl"))
    need(len(logs) == 1 and logs[0].parent == run / "result/combined-plugin" and
         logs[0].stem == f"worker-{target['pid']}", "plugin target log identity")
    events = rows(logs[0])
    need(all(x.get("pid") == target["pid"] for x in events), "plugin event PID")
    decision_path = Path(plan["env"]["HBFSIM_QKV_ABI_DECISION_LOG"])
    need(decision_path == run / "logs/combined-abi-decision.jsonl", "decision path join")
    need(Path(plan["env"]["HBFSIM_PROVIDER_CORRELATION_LOG"]) == run / "logs/correlation.jsonl" and
         Path(plan["env"]["HBFSIM_COVERAGE_PATH"]) == run / "logs/coverage.jsonl",
         "provider/coverage path differs from this run")
    decisions = rows(decision_path)
    driver = rows(Path(plan["env"]["HBFSIM_PROVIDER_CORRELATION_LOG"]))
    coverage = rows(Path(plan["env"]["HBFSIM_COVERAGE_PATH"]))
    detail = check_receipts(plan["scope"], manifest, registration, events, decisions,
                            driver, coverage, result["access_accounting"]["access"])
    selected = {x["alias"]: x for x in manifest["targets"]}
    for row in [e for e in events if e.get("event") == "selected_output"]:
        target_row = selected[row["alias"]]
        key = f"worker-{target['pid']}-{target_row['category']}-{target_row['layer'] if target_row['layer'] is not None else 'head'}"
        folder = logs[0].parent
        for suffix, size, hash_key in (("activation", 4096, "input_sha256"),
                                       ("native", target_row["output_bytes"], "native_sha256"),
                                       ("candidate", target_row["output_bytes"], "candidate_sha256")):
            path = folder / f"{key}-{suffix}.bin"
            need(path.is_file() and path.stat().st_size == size and sha(path) == row.get(hash_key),
                 f"selected raw {suffix} size/hash: {row['alias']}")
    return {"schema": "hbfsim.combined_model_validation.v1",
            "status": "MODEL_CONNECTED_PASS_COMBINED_" + plan["scope"].upper(),
            "scope": plan["scope"], "epoch": plan["epoch"],
            "target_pid": target["pid"], "target_start_ticks": target["start_ticks"],
            "registered_storage_count": COUNTS[plan["scope"]][1], **detail,
            "limit": "Per-storage modeled address and selected call join; shared module counters do not prove per-storage latency."}

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--plan", required=True, type=Path)
    parser.add_argument("--run", required=True, type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = validate(args.plan.resolve(strict=True), args.run.resolve(strict=True))
    if args.output:
        with args.output.open("x") as stream:
            json.dump(report, stream, indent=2, sort_keys=True); stream.write("\n")
    print(json.dumps(report, sort_keys=True))

if __name__ == "__main__": main()
