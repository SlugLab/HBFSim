"""CPU-only mixed3 plan template from frozen representative inputs; no resource read."""
import copy
import hashlib
import json
from pathlib import Path

TASK=Path(__file__).resolve().parent
ROOT=TASK.parents[1]
BASE=ROOT/"task/li6-oproj-only-v1/PLAN_TEMPLATE.json"
OUT=TASK/"MIXED3_PLAN_TEMPLATE.json"
RUN=ROOT/"runs/li6-li7-combined-mixed3-v1"
BUNDLE=TASK/"bundle-combined-v1/build"
STAGE=TASK/"prepared-v2/stage"
SCOPE=TASK/"prepared-v2/scope-mixed3.json"
HOST=ROOT/"task/li6-li7-combined-host-v1/build-v5"
LI7=json.loads((ROOT/"task/router-li7-model-representative-v1/MODEL_TARGET_MANIFEST.json").read_text())
JOIN=json.loads((TASK/"prepared-v2/STAGE_JOIN_RECEIPT.json").read_text())
def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()

def main():
    if OUT.exists():raise RuntimeError("mixed3 template already exists")
    base=json.loads(BASE.read_text());p=copy.deepcopy(base)
    cmd=p["command"]
    cmd[cmd.index("--report-dir")+1]=str(RUN/"result/cell")
    cmd[cmd.index("--accounting-epoch")+1]="6710"
    cmd[cmd.index("--hbf-include-pattern")+1]=(
        r"^model\.layers\.0\.(self_attn\.(qkv_proj|o_proj)|mlp\.gate)\.weight$")
    # Preserve the passed per-request 480s service-wait bound. The 1800s worker
    # window, not this per-request timer, bounds aggregate selected-call wall time.
    env=p["env"]
    for key in ("HBFSIM_LI6_MODEL_PLUGIN_V1","HBFSIM_LI6_MODEL_RECEIPT_DIR",
                "HBFSIM_LI6_REPRESENTATIVE_V1","HBFSIM_QKV_TARGET_KIND",
                "HBFSIM_QKV_SOURCE_PTX_PATH","HBFSIM_QKV_STAGED_PTX_PATH",
                "HBFSIM_QKV_STAGED_PTX_SHA256","HBFSIM_QKV_ABI_MAP_SHA256",
                "CUDA_VISIBLE_DEVICES"):
        env.pop(key,None)
    env.update({
        "HBFSIM_BUILD_DIR":str(BUNDLE),"HBFSIM_BPFTIME_BUILD_DIR":str(BUNDLE),
        "HBFSIM_BPFTIME_PROBE":str(BUNDLE/"vllm_fused_moe_probe.bpf.o"),
        "HBFSIM_DAEMON_PATH":str(BUNDLE/"hbfsimd"),
        "HBFSIM_QKV_COMBINED_V1":"1",
        "HBFSIM_QKV_MODEL_PLUGIN_V1":"0",
        "HBFSIM_LI6_MODEL_PLUGIN_V1":"0",
        "HBFSIM_ROUTER_MODEL_PLUGIN_V1":"0",
        "HBFSIM_COMBINED_MODEL_PLUGIN_V1":"1",
        "HBFSIM_NATIVE_BINDING_MANIFEST_PATH":str(TASK/"prepared-v2/native-bindings-union.json"),
        "HBFSIM_COMBINED_SCOPE_MANIFEST":str(SCOPE),
        "HBFSIM_COMBINED_SCOPE_SHA256":JOIN["scope_manifest_sha256"]["mixed3"],
        "HBFSIM_COMBINED_MODEL_RECEIPT_DIR":str(RUN/"result/combined-plugin"),
        "HBFSIM_LI6_SOURCE_PTX_PATH":str(ROOT/"task/qkv-output-repair-v1/cpu-build-v1/qkv.quarantine.ptx"),
        "HBFSIM_LI6_STAGED_PTX_PATH":JOIN["source_to_stage"]["li6"]["staged_path"],
        "HBFSIM_LI6_STAGED_PTX_SHA256":JOIN["source_to_stage"]["li6"]["staged_sha256"],
        "HBFSIM_LI6_ABI_MAP_SHA256":"20754dd201073fb033f724c0a61ee0177b39eb2c920807c870e5825136096ed9",
        "HBFSIM_LI7_SOURCE_PTX_PATH":LI7["source_ptx"]["path"],
        "HBFSIM_LI7_STAGED_PTX_PATH":JOIN["source_to_stage"]["li7"]["staged_path"],
        "HBFSIM_LI7_STAGED_PTX_SHA256":JOIN["source_to_stage"]["li7"]["staged_sha256"],
        "HBFSIM_LI7_ABI_MAP_SHA256":LI7["candidate_abi_map"]["sha256"],
        "BPFTIME_CUDA_LATE_PTX_DIR":str(STAGE),
        "HBFSIM_PRESTAGED_PASS_MANIFEST_PATH":str(TASK/"prepared-v2/pass-manifests.jsonl"),
        "HBFSIM_TARGET_EXTRA_LD_PRELOAD":str(HOST/"libprovider_router.so"),
        "HBFSIM_QKV_ABI_DECISION_LOG":str(RUN/"logs/combined-abi-decision.jsonl"),
        "HBFSIM_COVERAGE_PATH":str(RUN/"logs/coverage.jsonl"),
        "HBFSIM_PROVIDER_CORRELATION_LOG":str(RUN/"logs/correlation.jsonl"),
        "HBFSIM_PROVIDER_MODULE_DIR":str(RUN/"logs/provider-modules"),
        "HBFSIM_PROVIDER_TRACE_LOG":str(RUN/"logs/blas.jsonl"),
        "HBFSIM_PASS_MANIFEST_PATH":str(RUN/"logs/pass-manifests.jsonl"),
        "HBFSIM_NATIVE_REGISTRATION_LOG_PATH":str(RUN/"logs/native-registration.jsonl"),
        "HBFSIM_STRICT_BRIDGE_LOG_PATH":str(RUN/"logs/strict-bridge.jsonl"),
        "HBFSIM_STRICT_RUNTIME_LOG_PATH":str(RUN/"logs/strict-runtime.jsonl"),
        "HBFSIM_VLLM_CACHE":str(RUN/"result/cache/vllm"),
        "TRITON_CACHE_DIR":str(RUN/"result/cache/triton"),
        "HBFSIM_QKV_EPOCH":"6710",
        "PYTHONPATH":str(TASK)+":"+":".join(
            x for x in env["PYTHONPATH"].split(":")
            if x != str(ROOT/"task/li6-oproj-only-v1") and
               x != str(ROOT/"task/new-category-li6-adapter-v1")),
        "LD_LIBRARY_PATH":str(BUNDLE)+":"+":".join(
            x for x in env["LD_LIBRARY_PATH"].split(":") if x !=
            str(ROOT/"task/all-weights-category-representatives-v1/bundle-li6-v1/build"))})
    p={k:v for k,v in p.items() if k not in
       ("bundle_manifest_sha256","runtime_spec_sha256","gpu_uuid")}
    p["plugin_path"]=str(TASK/"combined_model_adapter/__init__.py")
    p["guard_script"]=str(TASK/"resource_guard_selected_gpu.py")
    p["guard_sha256"]=sha(TASK/"resource_guard_selected_gpu.py")
    p.update({"schema":"hbfsim.combined_mixed3_plan_template.v1",
              "stage_id":"li6-li7-combined-mixed3-v1-once",
              "scope":"mixed3","scope_manifest_path":str(SCOPE),
              "scope_manifest_sha256":sha(SCOPE),
              "stage_join_sha256":sha(TASK/"prepared-v2/STAGE_JOIN_RECEIPT.json"),
              "bundle_join_sha256":sha(TASK/"bundle-combined-v1/BUNDLE_JOIN.json"),
              "plugin_sha256":sha(TASK/"combined_model_adapter/__init__.py"),
              "epoch":6710,"output_dir":str(RUN/"controller"),
              "worker_seconds":1800,"outer_seconds":2100,"collection_seconds":600,
              "startup_cleanup_seconds":300,"full_admission_seconds":3000,
              "head_model_dependency":"PENDING_MODEL_CONNECTED_PASS_LM_HEAD",
              "request_timer_reason":"Inherited successful o_proj 480s per-request service-wait bound; aggregate candidate wall time is bounded by worker1800",
              "configured_gpu_index":"REQUIRED_AT_PREPARE",
              "diagnostic_status":"CPU_TEMPLATE_NO_GPU_START",
              "note":"Old98 remains staged but is excluded by exact three-alias registration selector."})
    OUT.write_text(json.dumps(p,indent=2,sort_keys=True)+"\n")

if __name__=="__main__":main()
