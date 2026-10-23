#!/usr/bin/env python3
"""Read-only layer-0 nine-storage paired derivative of the combined validator."""
from __future__ import annotations
import runpy

import argparse
import hashlib
import json
import os
from pathlib import Path

# Preserve the original validator's baseline, scope and plug-in source roots.
# This derivative lives outside the frozen task and only rebinds the staged
# full9 identity below. No original validator or evidence file is edited.
TASK = Path("/root/hbfsim-exp/rebuttal_20260921/sass-lifter-20260924/task/li6-li7-combined-model-v1")
ROOT = TASK.parents[1]
BASELINE = ROOT / "runs/old-baseline-v3/native/result/cell/result.json"
FULL9 = Path("/root/hbfsim-exp/ucie-interface-20260929/hybrid-phase-diag-v1/full9-v1/FULL9_JOIN_RECEIPT.json")
FULL9_STAGE = FULL9.parent / "stage"
LI6 = "ptx:sha256:6db074711d19c3e31cbcc98c6e170f0b4330259f220c2d518ce8aee42b9c9eab"
LI7 = "ptx:sha256:e70c7c4bb6429dba28291f4c22d9897b96de3d82e6d87b73689b9beff4bec2d5"
COUNTS = {f"layers-{n}": (3*n+1, 9*n+3) for n in (4,8,12,16)}
SCOPE_BYTES = {"layers-4":3768651776,"layers-8":7125209088,"layers-12":10481766400,"layers-16":13838323712}
LEDGER = ROOT / "task/full-coverage-ledger-v1/TARGET_LEDGER.json"
PRIVATE_CUDART13 = Path("/root/hbfsim-exp/rebuttal_20260921/cuda13-glibc-rsqrt-overlay-v1/root/cuda-13.0/lib64/libcudart.so.13")
PRIVATE_CUDART13_SHA = "96c42e418cec19054186b9429c321603cc190bf26a18104e19408117a2a817b0"
ACCESS = ("in_range_accesses", "modeled_admitted_accesses", "service_completed_accesses")
BYTES = ("in_range_intersection_bytes", "modeled_admitted_bytes", "service_completed_bytes")
ERRORS = ("counter_overflow", "failed_preissue_accesses", "failed_after_issue_accesses",
          "translation_failed_accesses", "unsupported_preissue_accesses", "unclassified_accesses")

import runpy as _diag_runpy
_DIAG = _diag_runpy.run_path(str(Path(__file__).resolve().parents[1] / "diag_profile.py"))

def need(ok, message):
    if not ok: raise RuntimeError(message)

def load(path): return json.loads(Path(path).read_text())
def rows(path): return [json.loads(x) for x in Path(path).read_text().splitlines() if x.strip()]
def sha(path): return hashlib.sha256(Path(path).read_bytes()).hexdigest()

def original_association_before_patched(driver,original_function,image_sha256,patched_correlation_id):
    """Bind the fallback handle chain only to associations known by this callback."""
    functions=[d for d in driver if d.get("schema")=="hbfsim.provider.function_association.v1" and
               d.get("function_handle")==original_function and isinstance(d.get("correlation_id"),int) and
               d["correlation_id"]<=patched_correlation_id]
    modules=[d for d in driver if d.get("schema")=="hbfsim.provider.module_association.v1" and
             isinstance(d.get("correlation_id"),int) and d["correlation_id"]<=patched_correlation_id and
             any(d.get("module_handle")==f.get("module_handle") for f in functions)]
    libraries=[d for d in driver if d.get("schema")=="hbfsim.provider.library_association.v1" and
               isinstance(d.get("correlation_id"),int) and d["correlation_id"]<=patched_correlation_id and
               d.get("api_success") is True and
               any(d.get("library_handle")==m.get("library_handle") for m in modules) and
               image_sha256 in d.get("blob_sha256",[])]
    blobs=[d for d in driver if d.get("schema")=="hbfsim.provider.module_blob.v2" and
           d.get("sha256")==image_sha256 and d.get("durable") is True and
           d.get("byte_kind")=="FATBIN_BYTES"]
    need(len(functions)==len(modules)==len(libraries)==len(blobs)==1,
         "unambiguous original function/module/library/durable FATBIN association no later than patched callback")
    return {"function_correlation_id":functions[0]["correlation_id"],
            "module_correlation_id":modules[0]["correlation_id"],
            "library_correlation_id":libraries[0]["correlation_id"],
            "library_generation":libraries[0].get("library_generation"),
            "blob_sha256":image_sha256}

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

def runtime_required_env_keys(plan):
    """Return the exact environment identity checked again after target start."""
    keys = ("VLLM_PLUGINS", "HBFSIM_COMBINED_MODEL_PLUGIN_V1", "HBFSIM_SWEEP_SCOPE_MANIFEST",
            "HBFSIM_SWEEP_SCOPE_SHA256", "HBFSIM_SWEEP_MEASUREMENT_RECEIPT_DIR",
            "HBFSIM_QKV_COMBINED_V1", "HBFSIM_LI6_SOURCE_PTX_PATH",
            "HBFSIM_LI6_STAGED_PTX_PATH", "HBFSIM_LI6_STAGED_PTX_SHA256",
            "HBFSIM_LI6_ABI_MAP_SHA256", "HBFSIM_LI7_SOURCE_PTX_PATH",
            "HBFSIM_LI7_STAGED_PTX_PATH", "HBFSIM_LI7_STAGED_PTX_SHA256",
            "HBFSIM_LI7_ABI_MAP_SHA256", "BPFTIME_CUDA_LATE_PTX_DIR",
            "HBFSIM_TARGET_EXTRA_LD_PRELOAD", "HBFSIM_QKV_ABI_DECISION_LOG",
            "HBFSIM_PROVIDER_CORRELATION_LOG", "HBFSIM_COVERAGE_PATH",
            "PYTHONPATH", "LD_LIBRARY_PATH", "CUDA_LAUNCH_BLOCKING",
            "HBFSIM_TARGET_CUDART12_PRELOAD", "CUDA_DEVICE_ORDER", "CUDA_VISIBLE_DEVICES")
    if plan["backend"] == "ucie":
        keys += ("HBFSIM_UCIE_TYPED_RPC", "HBFSIM_UCIE_SHM_RPC", "HBFSIM_UCIE_WAIT_MODE",
                 "HBFSIM_UCIE_COALESCER_EMPTY_WAIT", "HBFSIM_UCIE_RELEASABLE_CHILD_INDEX_V1",
                 "HBFSIM_UCIE_ORDERED_ADMISSION_V1", "HBFSIM_UCIE_CAPACITY_ZERO_INACTIVE_V1",
                 "HBFSIM_UCIE_COMPACT_CONSUME_ORDERED_V1", "HBFSIM_UCIE_COMPACT_CONSUME_WAVE_V1",
                 "HBFSIM_UCIE_DEFERRED_INITIAL_HELLO_V1")
    return keys

def validate_runtime_env_key_closure(plan):
    diag_profile = _DIAG["profile_for"](plan)
    if diag_profile is not None:
        _DIAG["root_revision"](plan, diag_profile)
    keys = runtime_required_env_keys(plan)
    missing = [key for key in keys if key not in plan.get("env", {})]
    need(not missing, "plan missing runtime-required environment keys: " + ",".join(missing))
    need(plan["env"]["HBFSIM_COMBINED_MODEL_PLUGIN_V1"] == "0",
         "candidate role must explicitly disable the combined correctness plugin")
    need(plan["env"].get("HBFSIM_SWEEP_MEASUREMENT_PLUGIN_V1") == "1" and
         plan["env"].get("VLLM_PLUGINS") == "hbfsim_model_loader,hbfsim_sweep_measurement_adapter",
         "candidate measurement plugin activation/allowlist differs")
    need(plan["env"]["CUDA_DEVICE_ORDER"] == "PCI_BUS_ID" and
         plan["env"]["CUDA_VISIBLE_DEVICES"] == str(plan["cuda_visible_ordinal"]),
         "candidate CUDA visibility identity differs")
    expected = {
        "HBFSIM_HYBRID_PHASE_DIAG_V1": "1" if diag_profile is not None else "0",
        "HBFSIM_STORAGE_BYTE_IDENTITY_V1": "1",
        "HBFSIM_NORMAL_BACKEND": "legacy_hbf",
        "HBFSIM_HBF_MEDIA_PAGE_BYTES": "4096",
        "HBFSIM_HOST_ROLE_POLICY": "independent_framework_controller_v1",
    }
    if plan["cell_id"] == "timing-N16-nominal": expected["HBFSIM_HBF_WAIT_MODE"] = "nominal"
    if diag_profile is not None: expected["HBFSIM_HBF_WAIT_MODE"] = "nominal"
    if plan["cell_id"] == "timing-N16-zero_injected": expected["HBFSIM_HBF_WAIT_MODE"] = "zero_injected"
    missing_role = [key for key in ("HBFSIM_CONTROL_OBSERVER_SOURCE", "HBFSIM_CONTROL_OBSERVER_SHA256",
        "HBFSIM_NORMAL_REPORT_DIR", "HBFSIM_NORMAL_DAEMON_SHA256", "HBFSIM_NORMAL_ROLE_CPUS_FILE",
        "HBFSIM_PHASE_CANDIDATE_VERSION") if not plan["env"].get(key)]
    # Older reviewed plans spell this version key with the HYBRID prefix.
    if "HBFSIM_PHASE_CANDIDATE_VERSION" in missing_role and plan["env"].get("HBFSIM_HYBRID_PHASE_CANDIDATE_VERSION"):
        missing_role.remove("HBFSIM_PHASE_CANDIDATE_VERSION")
    need(not missing_role, "candidate source-role environment incomplete: " + ",".join(missing_role))
    need(all(plan["env"].get(key) == value for key, value in expected.items()),
         "candidate source-role environment value differs")
    need(plan.get("measurement_role_contract") == {
        "schema": "hbfsim.candidate_layer_timing_role.v1", "candidate_only": True,
        "formal_generate_calls": 1, "prior_correctness_replay": False,
        "warmup_before_timing_hooks": True, "measured_layer_intervals": 16,
        "measured_global_intervals": 3, "derived_only_layer_counts": [4, 8, 12]},
         "candidate timing role contract differs")
    return keys

def expected_manifest(plan):
    qualification = _DIAG['optimized'](plan)
    full9_path = FULL9 if qualification is None else Path(qualification['effective']['full9']['path'])
    full9_stage = FULL9_STAGE if qualification is None else Path(qualification['effective']['stage_root'])
    scope = plan.get("scope")
    need(scope in COUNTS, "unknown combined scope")
    path = Path(plan["scope_manifest_path"])
    need(path.is_file() and sha(path) == plan["scope_manifest_sha256"], "scope manifest identity")
    manifest = load(path)
    expected = {alias: row["storage_bytes"] for row in load(LEDGER)["entries"]
                for alias in row["aliases"] if alias in manifest["registration_aliases"]}
    from measurement_adapter.scope_contract import make_scopes
    package = Path(plan["plugin_source_path"]).parent
    frozen = package / "pinned/scope-added49.json"
    expected_scope = next(s for s in make_scopes(LEDGER.read_bytes(), frozen.read_bytes()) if s["scope"] == scope)
    need(manifest == expected_scope and len(expected) == COUNTS[scope][1] and
         sum(expected.values()) == SCOPE_BYTES[scope], "pinned derived scope mismatch")
    command = plan["command"]
    includes = [command[i + 1] for i, arg in enumerate(command[:-1])
                if arg == "--hbf-include-pattern"]
    need(includes == manifest.get("include_patterns") and len(includes) == COUNTS[scope][1] and
         manifest.get("hbf_weight_selection") == "include",
         "actual runner did not select exactly the manifest aliases")
    staged = load(full9_path)
    need(plan.get("full9_join_receipt_sha256") == sha(full9_path) and
         staged.get("module_file_count") == 9 and staged.get("row_count") == 10 and
         staged.get("old_new_manifest_objects_equal_in_order") is True and
         staged.get("new_full_manifest_sha256") == plan.get("full9_manifest_sha256") ==
         sha(full9_stage / "pass-manifests.jsonl") and
         plan.get("plugin_sha256") == sha(plan["plugin_source_path"]),
         "plugin/stage/scope review identity")
    files = staged.get("files", [])
    need(len(files) == 9 and len({r.get("module_id") for r in files}) == 9 and
         all(Path(r["output"]) == full9_stage / (r["module_id"].removeprefix("ptx:sha256:") + ".ptx") and
             sha(r["output"]) == r["output_sha256"] for r in files),
         "full9 staged PTX identity")
    for label, module in (("LI6", LI6), ("LI7", LI7)):
        row = next(r for r in files if r["module_id"] == module)
        need(plan["env"].get(f"HBFSIM_{label}_STAGED_PTX_PATH") == row["output"] and
             plan["env"].get(f"HBFSIM_{label}_STAGED_PTX_SHA256") == row["output_sha256"],
             f"{label} staged PTX identity")
    need(plan.get("output_dir") and isinstance(plan.get("epoch"), int) and
         plan.get("env", {}).get("HBFSIM_SWEEP_MEASUREMENT_PLUGIN_V1") == "1" and
         plan["env"].get("VLLM_PLUGINS") == "hbfsim_model_loader,hbfsim_sweep_measurement_adapter" and
         plan["env"].get("HBFSIM_COMBINED_MODEL_PLUGIN_V1") == "0" and
         plan["env"].get("HBFSIM_SWEEP_SCOPE_MANIFEST") == str(path) and
         plan["env"].get("HBFSIM_SWEEP_SCOPE_SHA256") == plan["scope_manifest_sha256"],
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
    framework_cudart12 = Path(plan["env"]["HBFSIM_TARGET_CUDART12_PRELOAD"])
    cudart_stat = framework_cudart12.stat()
    need(maps_match(maps, str(framework_cudart12.resolve(strict=True)),
                    cudart_stat.st_dev, cudart_stat.st_ino),
         "framework CUDA12 not loaded in actual target")
    private_stat = PRIVATE_CUDART13.stat()
    need(sha(PRIVATE_CUDART13) == PRIVATE_CUDART13_SHA and
         maps_match(maps, str(PRIVATE_CUDART13.resolve(strict=True)),
                    private_stat.st_dev, private_stat.st_ino),
         "private CUDA13 not loaded from reviewed runtime")
    actual_env = load(cap / "target-start-env.json")
    keys = validate_runtime_env_key_closure(plan)
    for key in keys:
        need(key in plan["env"] and actual_env.get(key) == plan["env"][key],
             f"target environment differs: {key}")
    cuda_env=load(cap/"selected-cuda-env.json")
    need(cuda_env.get("target_pid")==target["pid"] and
         cuda_env.get("target_start_ticks")==target["start_ticks"] and
         cuda_env.get("env",{}).get("CUDA_DEVICE_ORDER") ==
         plan["env"].get("CUDA_DEVICE_ORDER") == "PCI_BUS_ID" and
         cuda_env["env"].get("CUDA_VISIBLE_DEVICES") == plan["env"].get("CUDA_VISIBLE_DEVICES") ==
         str(plan["cuda_visible_ordinal"]),"configured CUDA device order/visibility changed")
    occupancy=load(cap/"selected-gpu-occupancy.json")
    need(occupancy.get("status")=="TARGET_ON_CONFIGURED_GPU" and
         occupancy.get("configured_gpu_index")==plan["configured_gpu_index"] and
         occupancy.get("observed_selected_uuid")==plan.get("bound_gpu_uuid") and
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
    expected = {alias: row["storage_bytes"] for row in load(LEDGER)["entries"]
                for alias in row["aliases"] if alias in manifest["registration_aliases"]}
    need(len(expected) == COUNTS[scope][1] and sum(expected.values()) == SCOPE_BYTES[scope] and
         sorted(expected) == manifest["registration_aliases"],
         "scope ledger/manifest registration disagreement")
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
    pass_rows = rows(FULL9_STAGE / "pass-manifests.jsonl")
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
        need(output.get("status") == "CANDIDATE_ONLY" and
             output.get("select_rc") == output.get("end_rc") == 0 and
             output.get("request_id") == request and output.get("os_thread_id") == tid == complete.get("os_thread_id") and
             output.get("profile") == row["profile"] and
             output.get("category") == row["category"] and output.get("layer") == row["layer"] and
             output.get("weight_ptr") == installed_row.get("base") == hex(base) and
             output.get("weight_bytes") == installed_row.get("bytes") == size and
             output.get("output_shape") == row["output_shape"] and
             output.get("output_dtype") == "torch.bfloat16" and
             output.get("output_bytes") == row["output_bytes"] and
             positive_hex(output.get("candidate_output_ptr")),
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
        links = [d for d in driver if common_driver(d, decision["patched_function"]) and
                 d.get("identity_state") == "TOPOLOGY_ONLY" and
                 isinstance(d.get("correlation_id"), int) and
                 d["correlation_id"] > last_patched_correlation and
                 d["correlation_id"] not in used_patched_correlations]
        need(links, f"unique actual patched topology/API/CBID/thread link: {alias}")
        chosen = min(links, key=lambda d: d["correlation_id"])
        association_deadline = chosen["correlation_id"]
        if originals:
            original_route = "exact_launch"
        else:
            original_association_before_patched(driver,decision["original_function"],
                                                driver_profile["image_sha256"],association_deadline)
            original_route = "function_module_library_blob_association"
        used_patched_correlations.add(chosen["correlation_id"])
        last_patched_correlation = chosen["correlation_id"]
        selected_summary.append({"alias": alias, "base": base, "bytes": size,
                                 "profile": row["profile"], "decision_ordinal": decision["call_ordinal"],
                                 "original_image_route": original_route,
                                 "association_token": decision["association_token"],
                                 "patched_correlation_id": chosen["correlation_id"],
                                 "image_sha256": driver_profile["image_sha256"]})
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
            "nonselected_storage_count": len(reg)-len(selected),
            "selected_count": len(selected)}

def check_result(result, baseline, epoch):
    from native_B_contract import COHORT_KEYS
    for key in COHORT_KEYS:
        need(key in result and key in baseline and result[key]==baseline[key], 'current native/candidate actual cohort differs: '+key)
    need(result.get("accounting_epoch") == epoch and
         result.get("request_terminal_status") == "success" and
         result.get("scientific_status") == "COMPLETE" and
         result.get("output_token_ids") == baseline.get("output_token_ids") and
         result.get("output_token_ids_sha256") == baseline.get("output_token_ids_sha256"),
         "model result/epoch/native baseline mismatch")

def check_ucie_backend(run):
    """Close real HBF-page/UCIe-service counts, separate from HYBRID fast counts."""
    report = load(run / "result/cell/ucie-backend-report.json")
    need(report.get("schema") == "hbfsim.ucie.host_backend_report.v1" and
         report.get("wait_mode") in ("nominal", "zero_injected") and
         isinstance(report.get("published"), int) and report["published"] > 0 and
         report.get("failed") == report.get("unsupported") == 0 and
         report.get("caller_outstanding") == 0 and
         report.get("post_publish_cleanup_failures") == 0,
         "UCIe nominal real-request terminal/cleanup status")
    page_bytes = 16384  # original nominal Profile; verify from actual profile at admission
    need(report.get("original_hbf_request_bytes_submitted") ==
         report.get("original_frontend_bytes") == report["published"] * page_bytes and
         report.get("axi_payload_bytes") == report.get("axi_child_transactions", -1) * 64 and
         report["axi_payload_bytes"] >= report["original_frontend_bytes"] and
         report.get("worker_ar_wire_bytes", 0) > 0 and
         report.get("worker_r_wire_bytes", 0) > 0 and
         report.get("media_submit_bytes", 0) > 0 and
         report.get("native_commands", 0) > 0 and
         report.get("sim_service_delta_sum_ns", 0) > 0,
         "UCIe HBF-page/AXI/wire/media count identity")
    stacks = report.get("stacks")
    live = load(run / "workers-live.json")
    daemon = load(run / "daemon-live.json")
    processes = live.get("workers")
    local = live.get("local_stack")
    need(isinstance(stacks, list) and len(stacks) == 4 and
         [row.get("stack") for row in stacks] == list(range(4)) and
         stacks[0].get("transport") == "local" and
         stacks[0].get("process_id") == 0 and
         stacks[0].get("physical_ipc_send_count") == 0 and
         stacks[0].get("physical_ipc_receive_count") == 0 and
         isinstance(processes, list) and len(processes) == 3 and
         [row.get("stack") for row in processes] == [1, 2, 3] and
         isinstance(local, dict) and local.get("stack") == 0 and
         local.get("transport") == "local" and local.get("process_id") == 0 and
         local.get("daemon_pid") == daemon.get("pid") and
         local.get("daemon_start_ticks") == daemon.get("start_ticks") and
         all(row.get("transport") == "process" and
             type(row.get("process_id")) is int and row["process_id"] > 0 and
             row["process_id"] == live["workers"][row["stack"] - 1]["pid"]
             for row in stacks[1:]) and
         len({row.get("process_id") for row in stacks[1:]}) == 3 and
         all(row.get("physical_outstanding") == 0 and
             row.get("failed", 0) == 0 and
             row.get("native_commands", -1) >= 0 for row in stacks) and
         sum(row["native_commands"] for row in stacks) == report["native_commands"] and
         sum(row["media_submit_bytes"] for row in stacks) == report["media_submit_bytes"],
         "one local stack, three owned workers and actual per-stack native drain")
    return {"published_hbf_pages": report["published"],
            "original_hbf_request_bytes": report["original_hbf_request_bytes_submitted"],
            "axi_payload_bytes": report["axi_payload_bytes"],
            "native_commands": report["native_commands"],
            "per_stack_native_commands": [row["native_commands"] for row in stacks],
            "limit": "HYBRID analytic-fast aggregate counts are separate from UCIe real-request counts."}

def build_identity(plan):
    """Pin the same software/stage bytes for later N-sweep comparisons."""
    qualification = _DIAG['optimized'](plan)
    full9_path = FULL9 if qualification is None else Path(qualification['effective']['full9']['path'])
    full9_stage = FULL9_STAGE if qualification is None else Path(qualification['effective']['stage_root'])
    loader_path = plan.get("loader_source_path")
    loader_sha = plan.get("loader_sha256")
    support = plan.get("support_source_sha256")
    need(isinstance(loader_path, str) and isinstance(loader_sha, str),
         "candidate loader source not bound")
    need(isinstance(support, dict), "candidate loader support-source map not bound")
    plugin = Path(plan["plugin_source_path"])
    package = plugin.parent
    entry = package.parent / "measurement_adapter-0.1.dist-info/entry_points.txt"
    for path in (plugin, package / "scope_contract.py", entry,
                 package / "pinned/TARGET_LEDGER.json",
                 package / "pinned/scope-added49.json"):
        need(path.is_file() and support.get(str(path)) == sha(path),
             f"fullweights plugin import closure not hash-bound: {path}")
    need("hbfsim_sweep_measurement_adapter = measurement_adapter:register"
         in entry.read_text(), "fullweights plugin entry point changed")
    loader = Path(loader_path)
    need(loader.is_absolute() and loader.name == "hbfsim_loader.py" and
         sha(loader) == loader_sha and
         support.get(str(loader)) == loader_sha,
         "candidate loader source/hash/support identity changed")
    need(plan["env"].get("PYTHONPATH", "").split(":") ==
         [str(plugin.parent.parent), str(loader.parent),
          *plan["accepted_pythonpath_tail"]],
         "candidate loader and placement import order changed")
    runner_path = plan.get("runner_source_path")
    runner_sha = plan.get("runner_sha256")
    need(isinstance(runner_path, str) and isinstance(runner_sha, str),
         "candidate runner source not bound")
    runner = Path(runner_path)
    need(runner.is_absolute() and runner.name == "run.py" and
         plan["command"][3] == str(runner) and sha(runner) == runner_sha and
         support.get(str(runner)) == runner_sha,
         "candidate runner command/source/hash/support identity changed")
    identities = plan.get("backing_identities")
    need(isinstance(identities, list), "fresh backing identities not bound")
    def configured(name):
        matches = [row["configured"] for row in identities
                   if Path(row["configured"]).name == name]
        need(len(matches) == 1, f"missing unique {name} backing")
        return matches[0]
    pin_join = runpy.run_path(str(Path(__file__).resolve().parents[1] / "stage_pin_contract.py"))["validate"](plan)
    actual = {
        "agent": sha(configured("libbpftime-agent.so")),
        "gate": sha(configured("libhbfsim_launch_gate.so")),
        "provider": sha(configured("libprovider_router.so")),
        "daemon": sha(plan["env"]["HBFSIM_DAEMON_PATH"]),
        "extension": sha(plan["env"]["HBFSIM_VLLM_EXTENSION"]),
        "stage_manifest": sha(full9_stage / "pass-manifests.jsonl"),
        "full9_receipt": sha(full9_path),
        "helper_ptx": plan["new_helper_ptx_sha256"],
        "runner": runner_sha,
        "combined_plugin": sha(plugin),
        "scope": plan["scope_manifest_sha256"],
        "loader": loader_sha,
    }
    if plan["backend"] == "ucie":
        actual["worker"] = sha(plan["env"]["HBFSIM_UCIE_WORKER"])
    for key in ("agent", "gate", "daemon", "worker", "extension"):
        if key in actual:
            need(actual[key] == plan[f"new_{key}_sha256"], f"actual {key} bytes changed")
    need(actual["stage_manifest"] == plan["full9_manifest_sha256"] and
         actual["full9_receipt"] == plan["full9_join_receipt_sha256"] and
         actual["combined_plugin"] == plan["plugin_sha256"],
         "stage/plugin bytes changed")
    actual["stage_agent_pin_join"] = pin_join
    encoded = json.dumps(actual, sort_keys=True, separators=(",", ":")).encode()
    return actual, hashlib.sha256(encoded).hexdigest()

def validate(plan_path, run, posthoc_path=None, posthoc_sha256=None):
    plan = load(plan_path)
    plan_sha = sha(plan_path)
    need(not any(k in plan for k in ("measurement_result_path", "measurement_result_sha256", "loaded_zero_wait_receipt_path", "loaded_zero_wait_receipt_sha256")), "current-run hashes must not self-bind frozen run plan")
    posthoc = runpy.run_path(str(Path(__file__).resolve().parents[1] / "posthoc_evidence_contract.py"))["validate_posthoc"](posthoc_path,posthoc_sha256,plan_path,run)
    need(str(run / "controller") == plan.get("output_dir") and
         sha(run / "plan.json") == plan_sha and
         load(run / "owner-start.json").get("plan_sha256") == plan_sha and
         load(run / "controller/controller-start.json").get("plan_sha256") == plan_sha,
         "run/owner/controller plan identity differs")
    manifest = expected_manifest(plan)
    phase_mode = plan.get("env", {}).get("HBFSIM_HYBRID_PHASE_DIAG_V1")
    need(phase_mode in ("0", "1"), "bound plan lacks explicit 0/1 phase diagnostic flag")
    diag = load(run / "result/cell/hybrid-phase-window.json")
    need(diag.get("requested_flag") == phase_mode and
         diag.get("candidate_version") == plan["env"]["HBFSIM_HYBRID_PHASE_CANDIDATE_VERSION"],
         "hybrid phase receipt does not match bound plan")
    if phase_mode == "1":
        need(diag.get("diagnostic_status") == "PASS" and
             diag.get("snapshot_error") is None and
             not diag.get("validation_errors") and
             isinstance(diag.get("before"), dict) and
             isinstance(diag.get("after"), dict) and
             isinstance(diag.get("delta"), dict),
             "enabled hybrid phase diagnostic incomplete")
    else:
        disabled_null_keys = ("before", "after", "delta", "snapshot_error",
                              "before_getter_seconds", "after_getter_seconds")
        need(all(key in diag for key in disabled_null_keys),
             "disabled hybrid phase receipt missing explicit null field")
        need(diag.get("diagnostic_status") == "DISABLED_BY_PLAN" and
             diag.get("before") is None and diag.get("after") is None and
             diag.get("delta") is None and diag.get("snapshot_error") is None and
             diag.get("validation_errors") == [] and
             diag.get("before_getter_seconds") is None and
             diag.get("after_getter_seconds") is None,
             "disabled hybrid phase receipt invalid")
    target = validate_capture(run, plan)
    control = load(run / "controller/controller-finish.json")
    need(control.get("status") == "PROCESS_RC0_PENDING_RESULT_VALIDATION" and
         control.get("worker_returncode") == 0 and control.get("guard_clean") is True,
         "bounded controller/guard/worker incomplete")
    need(control.get("guard_returncode") == 0 and control.get("plan_sha256") == plan_sha,
         "bounded controller/guard/worker incomplete")
    result = load(run / "result/cell/result.json")
    need(result.get("bound_plan_sha256") == plan_sha and
         result.get("hybrid_phase_diag_requested") == phase_mode,
         "host process phase mode/bound-plan identity differs")
    evidence = runpy.run_path(str(Path(__file__).resolve().parents[1] / "measurement_evidence_contract.py"))
    baseline = evidence["native_B"](plan)
    check_result(result, baseline, plan["epoch"])
    registration = load(run / "result/cell/registration.json")
    logs = list(Path(plan["env"]["HBFSIM_SWEEP_MEASUREMENT_RECEIPT_DIR"]).glob("worker-*.jsonl"))
    need(len(logs) == 1 and logs[0].parent == run / "result/combined-plugin" and
         logs[0].stem == f"worker-{target['pid']}", "plugin target log identity")
    events = rows(logs[0])
    need(all(x.get("pid") == target["pid"] for x in events), "plugin event PID")
    decision_path = Path(plan["env"]["HBFSIM_QKV_ABI_DECISION_LOG"])
    need(decision_path == run / "logs/combined-abi-decision.jsonl", "decision path join")
    need(Path(plan["env"]["HBFSIM_PROVIDER_CORRELATION_LOG"]) == run / "logs/correlation.jsonl" and
         Path(plan["env"]["HBFSIM_COVERAGE_PATH"]) == run / "logs/coverage.jsonl",
         "provider/coverage path differs from this run")
    output_binding = load(run / "large-output-binding.json")
    provider_leaf = Path(plan["env"]["HBFSIM_PROVIDER_MODULE_DIR"])
    logical_provider = run / "logs/provider-modules"
    leaf_stat = provider_leaf.lstat()
    need(output_binding.get("status") == "REAL_PROVIDER_CREATED_DIRECTORY_ATTESTED" and
         provider_leaf == Path(plan["large_raw_root"]) and not provider_leaf.is_symlink() and
         provider_leaf.is_dir() and logical_provider.is_symlink() and
         logical_provider.resolve(strict=True) == provider_leaf.resolve(strict=True) and
         (leaf_stat.st_dev, leaf_stat.st_ino) ==
         (output_binding.get("device"), output_binding.get("inode")),
         "provider output constructor/real leaf/archive alias identity")
    decisions = rows(decision_path)
    driver = rows(Path(plan["env"]["HBFSIM_PROVIDER_CORRELATION_LOG"]))
    coverage = rows(Path(plan["env"]["HBFSIM_COVERAGE_PATH"]))
    detail = check_receipts(plan["scope"], manifest, registration, events, decisions,
                            driver, coverage, result["access_accounting"]["access"])
    need(not any(e.get("event", "").startswith("native_") for e in events),
         "duplicate native computation contaminated measurement")
    need(plan.get("consumer_policy") == "CANDIDATE_ONLY" and
         plan.get("native_recompute_in_generation") is False,
         "measurement plan does not exclude duplicate native work")
    zero = plan.get("wait_mode") == "zero_injected"
    need(plan.get("wait_mode") in ("nominal", "zero_injected"), "unknown actual injection mode")
    need(plan['backend']=='legacy_hbf','direct HBF actual backend required')
    hbf = runpy.run_path(str(Path(__file__).with_name('hbf_cold_contract.py')))['validate'](run,plan)
    control_evidence = runpy.run_path(str(Path(__file__).resolve().parents[1] / 'control_capture_contract.py'))['check_control_capture'](run / 'logs/control-progress.jsonl',run,plan,target,hbf['report'])
    need(control_evidence['zero_injected_wait_bit'] is zero,'actual HBF control wait bit differs')
    if _DIAG["requested"](plan):
        _DIAG["phase_window"](result.get("hybrid_phase_window", {}))
    identity, identity_sha = build_identity(plan)
    normal_binding = runpy.run_path(str(Path(__file__).with_name("normal_contract.py")))["validate"](run, plan, target)
    full_scope = runpy.run_path(str(Path(__file__).resolve().parents[1] / "scope_contract.py"))["validate_scope"](plan["full147_scope_path"], plan["full147_scope_sha256"])
    evidence["full147_join"](plan,full_scope,normal_binding,result,identity)
    need(plan["backend"] == "legacy_hbf", "measurement requires direct HBF service")
    evidence["zero_wait_join"](plan,posthoc,target,control_evidence,identity,decisions)
    need(sha(run / "result/cell/result.json") == posthoc["measurement_result_sha256"], "actual timing result pin differs")
    return {"schema": "hbfsim.candidate_only_measurement_validation.v1",
            "status": "MODEL_CONNECTED_PASS_CANDIDATE_ONLY_MEASUREMENT",
            "scope": plan["scope"], "epoch": plan["epoch"],
            "backend": plan["backend"],
            "target_pid": target["pid"], "target_start_ticks": target["start_ticks"],
            "registered_storage_count": COUNTS[plan["scope"]][1], **detail,
            "candidate_only_count": detail["selected_count"],
            "candidate_output_finite": "NOT_COLLECTED_IN_TIMING_SCOPE",
            "generation_seconds": result.get("generation_seconds"),
            "load_seconds": result.get("load_seconds"),
            "validated_plan_sha256": plan_sha,
            "posthoc_evidence_manifest_path": str(posthoc_path),
            "posthoc_evidence_manifest_sha256": posthoc_sha256,
            "build_identity": identity,
            "build_identity_sha256": identity_sha,
            "hbf_real_service": hbf,
            "normal_live_binding": normal_binding,
            "limit": "Per-storage modeled address and selected call join; shared module counters do not prove per-storage latency."}

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--plan", required=True, type=Path)
    parser.add_argument("--run", required=True, type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--posthoc-evidence", required=True, type=Path)
    parser.add_argument("--posthoc-evidence-sha256", required=True)
    args = parser.parse_args()
    report = validate(args.plan.resolve(strict=True), args.run.resolve(strict=True), args.posthoc_evidence.resolve(strict=True), args.posthoc_evidence_sha256)
    if args.output:
        with args.output.open("x") as stream:
            json.dump(report, stream, indent=2, sort_keys=True); stream.write("\n")
    print(json.dumps(report, sort_keys=True))

if __name__ == "__main__": main()
