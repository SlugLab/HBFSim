"""Synthetic negative-only relationships; no model/GPU acceptance evidence."""
import copy
import importlib.util
import json
from pathlib import Path

TASK=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location("validate_combined_model",TASK/"validate_combined_model.py")
v=importlib.util.module_from_spec(spec);spec.loader.exec_module(v)
manifest=json.loads((TASK/"prepared-v2/scope-mixed3.json").read_text())

def counters():
    out={x:3 for x in v.ACCESS}
    out.update({x:24 for x in v.BYTES})
    out.update({x:0 for x in v.ERRORS})
    return out

def fixture():
    reg=[]; installed=[]; outputs=[]; decisions=[]; driver=[]; coverage=[]
    for i,row in enumerate(manifest["targets"],1):
        base=0x10000000+i*0x10000000
        reg.append({"aliases":[row["alias"]],"address":base,
                    "bytes":row["storage_bytes"],"storage_bytes":row["storage_bytes"]})
        installed.append({"alias":row["alias"],"base":hex(base),
                          "bytes":row["storage_bytes"],"profile":row["profile"]})
        outputs.append({"event":"selected_output","pid":42,"alias":row["alias"],
                        "category":row["category"],"layer":row["layer"],
                        "profile":row["profile"],"request_id":"req", "os_thread_id":42,
                        "selected_order":i,"weight_ptr":hex(base),
                        "weight_bytes":row["storage_bytes"],"status":"BYTE_EQUAL",
                        "select_rc":0,"end_rc":0,"output_shape":row["output_shape"],
                        "output_dtype":"torch.bfloat16","output_bytes":row["output_bytes"],
                        "native_output_ptr":hex(base+100),"candidate_output_ptr":hex(base+200),
                        "native_sha256":"a","candidate_sha256":"a"})
        profile=row["native_driver"]
        decisions.append({"decision":"PATCHED_SELECTED","reason":"one_patched_call",
                          "cuda_result":0,"original_function":hex(0x3000+i),
                          "patched_function":hex(0x4000+i),"association_token":i,
                          "context":"0x1234","grid":profile["grid"],
                          "block":profile["block"],"shared_bytes":profile["dynamic_shared_bytes"],
                          "selected_base":hex(base),"selected_bytes":row["storage_bytes"],
                          "profile":row["profile"],"os_thread_id":42,"call_ordinal":i})
        driver.append({"schema":"hbfsim.provider.driver_launch.v3",
                       "function_handle":hex(0x3000+i),"api":"cuLaunchKernel",
                       "cbid":307,"callback_tid":42,"symbol_observation":profile["symbol"],
                       "identity_state":"EXACT","correlation_id":i*10,
                       "module_candidates":[{"sha256":profile["image_sha256"],
                                             "durable":True,"byte_kind":"FATBIN_BYTES"}]})
        driver.append({"schema":"hbfsim.provider.driver_launch.v3",
                       "function_handle":hex(0x4000+i),"api":"cuLaunchKernel",
                       "cbid":307,"callback_tid":42,"symbol_observation":profile["symbol"],
                       "identity_state":"TOPOLOGY_ONLY","correlation_id":i*10+1,
                       "module_candidates":[]})
        coverage.append({"modeled":True,"module_id":v.LI6 if row["profile"]=="li6" else v.LI7,
                         "address":base})
    registration={"unique_storage_count":3,"registered_bytes":sum(x["bytes"] for x in reg),
                  "selection":{"instrumentation_policy":"partial"},"storages":reg}
    events=[{"event":"installed","selected_storage_count":3,"targets":installed},
            {"event":"scheduled_prefill","request_id":"req","positions":[0,1]},
            {"event":"scheduled_decode","request_id":"req","os_thread_id":42,"positions":[2]}]
    events+=outputs
    events.append({"event":"selected_execute_complete","request_id":"req",
                   "os_thread_id":42,"status":"ALL_SELECTED_COMPLETE","selected_count":3})
    access={"status":"COMPLETE","disable_complete":True,"aggregate":counters(),
            "per_module":[{"identity":m,"status":"COMPLETE","counters":counters()}
                          for m in (v.LI6,v.LI7)]}
    return [registration,events,decisions,driver,coverage,access]

def rejected(label, mutate):
    data=copy.deepcopy(fixture());mutate(data)
    try:v.check_receipts("mixed3",manifest,*data)
    except RuntimeError:return label
    raise AssertionError(f"accepted synthetic {label}")

def main():
    v.check_receipts("mixed3",manifest,*fixture())
    cases=[
        rejected("missing_selected",lambda d:d[1].pop(3)),
        rejected("duplicate_selected",lambda d:d[1].insert(4,copy.deepcopy(d[1][3]))),
        rejected("native_fallback",lambda d:d[1][3].update(status="NATIVE_FALLBACK")),
        rejected("wrong_base",lambda d:d[2][0].update(selected_base="0xdeadbeef")),
        rejected("wrong_profile",lambda d:d[2][0].update(profile="li7")),
        rejected("wrong_api",lambda d:d[3][1].update(api="cuLaunchKernelEx")),
        rejected("wrong_thread",lambda d:d[3][1].update(callback_tid=99)),
        rejected("wrong_original_image",lambda d:d[3][0]["module_candidates"][0].update(sha256="wrong")),
        rejected("one_patched_callback_reused",lambda d:d[3].pop(3)),
        rejected("extra_patched_callback",lambda d:d[3].append({**d[3][1],"correlation_id":99})),
        rejected("incomplete_module",lambda d:d[5]["per_module"][0].update(status="INCOMPLETE")),
    ]
    baseline={"output_token_ids":[1],"output_token_ids_sha256":"abc"}
    result={"accounting_epoch":9000,"request_terminal_status":"success",
            "scientific_status":"COMPLETE",**baseline}
    v.check_result(result,baseline,9000)
    try:v.check_result({**result,"scientific_status":"FAILED"},baseline,9000)
    except RuntimeError:cases.append("failed_result")
    else:raise AssertionError("accepted failed result")
    receipt={"schema":"hbfsim.combined_validator_synthetic.v1",
             "status":"PASS_SYNTHETIC_CPU_ONLY","negative_cases":cases,
             "limitation":"Fixture rows are fabricated; no model, CUDA, provider, or service proof."}
    (TASK/"VALIDATOR_SYNTHETIC_RESULT.json").write_text(json.dumps(receipt,indent=2)+"\n")

if __name__=="__main__":main()
