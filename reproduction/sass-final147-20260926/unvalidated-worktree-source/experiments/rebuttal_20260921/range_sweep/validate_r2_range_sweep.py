#!/usr/bin/env python3
"""Validate completed R2 cells and emit N/M/K/failure/drop/output closure."""
import argparse,hashlib,json,pathlib
REQ=("supported_accesses","supported_bytes","in_range_accesses","in_range_intersection_bytes","native_out_of_range_accesses","native_out_of_range_bytes","modeled_admitted_accesses","modeled_admitted_bytes","service_completed_accesses","service_completed_bytes","failed_after_issue_accesses","failed_after_issue_bytes","unsupported_preissue_accesses","unsupported_preissue_bytes","failed_preissue_accesses","failed_preissue_bytes","translation_failed_accesses","translation_failed_bytes","service_requests","unclassified_accesses","unclassified_bytes","counter_overflow")
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def main():
 p=argparse.ArgumentParser();p.add_argument("--plan",type=pathlib.Path,required=True);p.add_argument("--result-root",type=pathlib.Path,required=True);p.add_argument("--output",type=pathlib.Path,required=True);a=p.parse_args();plan=json.loads(a.plan.read_text());reference=plan["inheritance"].get("output_token_ids_sha256");rows=[];overall=True
 for cell in plan["cells"]:
  size=int(cell["effective_bytes"]);d=a.result_root/f"range-{size}";errors=[];rp=d/"result.json";gp=d/"registration.json";ep=d/"exit_code.txt"
  result=json.loads(rp.read_text()) if rp.is_file() else {};reg=json.loads(gp.read_text()) if gp.is_file() else {}
  if not rp.is_file():errors.append("result_missing")
  if not gp.is_file():errors.append("registration_missing")
  if not ep.is_file() or ep.read_text().strip()!="0":errors.append("exit_nonzero_or_missing")
  if reg.get("registered_bytes")!=size:errors.append("effective_range_mismatch")
  stores=reg.get("storages",[])
  if len(stores)!=1 or int(stores[0].get("storage_bytes",0))!=plan["object_full_bytes"]:errors.append("storage_binding_mismatch")
  access=(result.get("access_accounting") or {}).get("access",{});agg=access.get("aggregate") or {}
  if result.get("scientific_status")!="COMPLETE" or result.get("request_terminal_status")!="success":errors.append("request_incomplete")
  if access.get("status")!="COMPLETE":errors.append("accounting_incomplete")
  missing=[k for k in REQ if k not in agg]
  if missing:errors.append("aggregate_fields_missing:"+",".join(missing))
  output_ok=bool(reference) and result.get("output_token_ids_sha256")==reference
  if not output_ok:errors.append("output_token_mismatch")
  eval_delay=(result.get("access_accounting") or {}).get("eval_delay",{})
  if eval_delay.get("status")!="COMPLETE":errors.append("eval_delay_incomplete")
  evagg=eval_delay.get("aggregate") or {};trace_drop=eval_delay.get("trace_drop")
  fail=sum(int(agg.get(k,0)) for k in ("failed_after_issue_accesses","failed_preissue_accesses","translation_failed_accesses"))
  row={"effective_bytes":size,"status":"PASS" if not errors else "INCOMPLETE","N_supported_accesses":agg.get("supported_accesses"),"M_modeled_admitted_accesses":agg.get("modeled_admitted_accesses"),"K_service_completed_accesses":agg.get("service_completed_accesses"),"failed_accesses":fail if not missing else None,"unsupported_preissue_accesses":agg.get("unsupported_preissue_accesses"),"unclassified_accesses":agg.get("unclassified_accesses"),"counter_overflow":agg.get("counter_overflow"),"eval_trace_overflow":evagg.get("trace_overflow"),"trace_drop":trace_drop if trace_drop is not None else "UNKNOWN_NOT_EXPOSED","opaque_overlap":"UNKNOWN_NOT_MEASURED","output_token_ids_sha256":result.get("output_token_ids_sha256"),"output_matches_r1":output_ok,"errors":errors,"result_sha256":sha(rp) if rp.is_file() else None}
  rows.append(row);overall=overall and not errors
 report={"schema_version":1,"status":"PASS" if overall and rows else "INCOMPLETE","plan_sha256":sha(a.plan),"cell_count":len(rows),"rows":rows,"scope":{"N":"supported_accesses","M":"modeled_admitted_accesses","K":"service_completed_accesses","failed_accesses":"failed_after_issue + failed_preissue + translation_failed","drop":"trace_drop is UNKNOWN unless explicitly exposed","opaque_overlap":"always UNKNOWN_NOT_MEASURED for R2"}};a.output.parent.mkdir(parents=True,exist_ok=True);a.output.write_text(json.dumps(report,indent=2,sort_keys=True)+"\n");print(json.dumps(report,indent=2,sort_keys=True));return 0 if report["status"]=="PASS" else 2
if __name__=="__main__":raise SystemExit(main())
