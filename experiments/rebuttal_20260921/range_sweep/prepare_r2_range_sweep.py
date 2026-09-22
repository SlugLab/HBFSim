#!/usr/bin/env python3
"""Prepare a receipt-bound R2 range matrix. Never launches a process."""
import argparse,hashlib,json,pathlib
SIZES=(16<<10,1<<20,16<<20,64<<20,256<<20)
def sha(path):
 h=hashlib.sha256()
 with path.open("rb") as f:
  for chunk in iter(lambda:f.read(1<<20),b""):h.update(chunk)
 return h.hexdigest()
def canonical_sha(value):
 return hashlib.sha256(json.dumps(value,separators=(",",":"),sort_keys=True).encode()).hexdigest()
def main():
 p=argparse.ArgumentParser();p.add_argument("--registration",type=pathlib.Path,required=True);p.add_argument("--r1-result",type=pathlib.Path,required=True);p.add_argument("--r1-input-hashes",type=pathlib.Path,required=True);p.add_argument("--r1-launch-script",type=pathlib.Path,required=True);p.add_argument("--output",type=pathlib.Path,required=True);a=p.parse_args()
 reg=json.loads(a.registration.read_text());storages=reg.get("storages",[])
 if len(storages)!=1:raise SystemExit("R2 requires exactly one selected storage")
 full=int(storages[0].get("storage_bytes",0))
 if full<=0:raise SystemExit("registration lacks positive storage_bytes")
 requested=[*SIZES,full];seen=set();cells=[]
 for value in requested:
  effective=min(value,full)
  if effective<=0 or effective>full:raise SystemExit("invalid clipped range")
  if effective in seen:continue
  seen.add(effective);cells.append({"requested_bytes":value,"effective_bytes":effective,"is_full_storage":effective==full,"status":"NOT_RUN"})
 r1=None;ready=False;blockers=[]
 if not a.r1_result.is_file():blockers.append("final_r1_result_missing")
 else:
  r1=json.loads(a.r1_result.read_text());account=(r1.get("access_accounting") or {})
  ready=(r1.get("scientific_status")=="COMPLETE" and r1.get("request_terminal_status")=="success" and account.get("access",{}).get("status")=="COMPLETE" and account.get("eval_delay",{}).get("status")=="COMPLETE")
  if not ready:blockers.append("final_r1_not_complete")
 if not a.r1_input_hashes.is_file():blockers.append("final_r1_input_hashes_missing");ready=False
 if not a.r1_launch_script.is_file():blockers.append("final_r1_launch_script_missing");ready=False
 profile=pathlib.Path((r1 or {}).get("profile") or reg.get("profile_path",""))
 if ready and not profile.is_file():blockers.append("final_r1_profile_missing");ready=False
 inheritance={"registration_path":str(a.registration.resolve()),"registration_sha256":sha(a.registration),"selected_aliases":storages[0].get("aliases"),"storage_bytes":full,"observed_d0_registered_bytes":reg.get("registered_bytes"),"r1_result_path":str(a.r1_result.resolve()),"r1_result_sha256":sha(a.r1_result) if a.r1_result.is_file() else None,"r1_input_hashes_path":str(a.r1_input_hashes.resolve()),"r1_input_hashes_sha256":sha(a.r1_input_hashes) if a.r1_input_hashes.is_file() else None,"r1_launch_script_path":str(a.r1_launch_script.resolve()),"r1_launch_script_sha256":sha(a.r1_launch_script) if a.r1_launch_script.is_file() else None,"profile_path":str(profile.resolve()) if profile else None,"profile_sha256":sha(profile) if profile.is_file() else None,"profile_configuration_rule":"inherit exact final successful R1 profile at preparation time; no num_stages value is presumed","output_token_ids_sha256":(r1 or {}).get("output_token_ids_sha256"),"prompt_token_ids_sha256":canonical_sha((r1 or {}).get("prompt_token_ids")) if r1 and "prompt_token_ids" in r1 else None}
 fixed={k:(r1 or {}).get(k) for k in ("model","input_len","output_len","max_model_len","max_num_batched_tokens","num_prompts","seed","warmup_requests","hbf_timing_model","attention_backend","gpu_memory_utilization") if k in (r1 or {})}
 plan={"schema_version":1,"status":"READY_NOT_RUN" if ready else "BLOCKED_BY_R1","gpu_execution_requested":False,"object_full_bytes":full,"candidate_rule":"clip_to_object_then_deduplicate","cells":cells,"inheritance":inheritance,"fixed_result_fields":fixed,"only_per_cell_override":"hbf_range_bytes=effective_bytes","blockers":blockers,"validator_required":True,"zero_independent_gpu_runs_performed":True}
 a.output.parent.mkdir(parents=True,exist_ok=True);a.output.write_text(json.dumps(plan,indent=2,sort_keys=True)+"\n");print(json.dumps(plan,indent=2,sort_keys=True));return 0
if __name__=="__main__":raise SystemExit(main())
