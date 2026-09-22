#!/usr/bin/env python3
"""Gated R3 page protocol; preflight only unless both earlier gates pass."""
import argparse,hashlib,json,os,pathlib,subprocess
PAGES=(4096,8192,16384,32768,65536); LOGICAL=110*1024**3; CACHE=2*1024**3
def sha(p):
 h=hashlib.sha256()
 with p.open("rb") as f:
  for b in iter(lambda:f.read(1<<20),b""):h.update(b)
 return h.hexdigest()
def matrix():
 for page in PAGES:
  for workload,bytes_ in (("fixed_dense",65536),("fixed_sparse",4096)):
   yield {"kind":"fixed","page_bytes":page,"workload":workload,"denominator":1,"passes":["cold","warm"],"valid_bytes":65*bytes_}
  for den in (16,4,1):
   c={"kind":"relative","page_bytes":page,"workload":"relative","denominator":den,"passes":["cold"],"valid_bytes":65*(page//den)}
   if page==65536 and den==1:c["reuse"]="fixed/page_65536/fixed_dense/result.json#stages[0]"
   yield c
def main():
 p=argparse.ArgumentParser()
 for n in ("source-root","build-dir","dependency-build","bpftime-build-dir","profile","result-root","backing-dir"):p.add_argument("--"+n,type=pathlib.Path,required=True)
 p.add_argument("--execute",action="store_true");p.add_argument("--r1-gate",choices=("PASS","NOT_PASS"),default="NOT_PASS");p.add_argument("--r2-gate",choices=("PASS","NOT_PASS"),default="NOT_PASS");a=p.parse_args()
 root=a.source_root.resolve();dep=a.dependency_build.resolve();build=a.build_dir.resolve();binary=build/"r3_public_capacity_bench";ptx=build/"r3_public_capacity_kernel.ptx";probe=build/"r3_public_capacity_probe.bpf.o";cells=list(matrix());artifacts_ready=all(x.is_file() for x in (binary,ptx,probe))
 plan={"schema_version":3,"status":"READY_NOT_RUN" if artifacts_ready else "BUILD_REQUIRED","route":"public_hbfsim_map_file_to_instrumented_GPU_load","logical_bytes":LOGICAL,"cache_bytes":CACHE,"page_bytes":PAGES,"region_count":65,"region_alignment_bytes":65536,"fixed_stage_count":20,"relative_cell_count":15,"relative_new_stage_count":14,"total_stage_records":35,"fresh_runtime_per_case":True,"same_order_warm_for_fixed":True,"sequential":True,"eviction_requested":False,"request_count_is_fault_count":False,"capacity_counters":"REQUIRED_SERVICE_SCOPED_V1;GLOBAL_DEVICE_HIT_RATE_NA","backing_read_scope":"SUCCESSFUL_SOFTWARE_BACKING_PAYLOAD;PHYSICAL_STORAGE_IO_UNKNOWN","h2d_fill_scope":"SUCCESSFUL_PAYLOAD_COPY;PCIE_TRANSACTION_BYTES_UNKNOWN","frame_identity_status":"UNKNOWN_NO_PUBLIC_GETTER","matrix":cells,"binary":str(binary),"binary_sha256":sha(binary) if binary.is_file() else None,"ptx":str(ptx),"ptx_sha256":sha(ptx) if ptx.is_file() else None,"probe":str(probe),"probe_sha256":sha(probe) if probe.is_file() else None,"probe_binding":"kprobe/r3_page_read_kernel","gpu_execution_requested":a.execute,"r1_gate":a.r1_gate,"r2_gate":a.r2_gate}
 print(json.dumps(plan,indent=2))
 if not a.execute:return 0
 if a.r1_gate!="PASS" or a.r2_gate!="PASS":raise SystemExit("R1/R2 gate is not PASS")
 if plan["status"]!="READY_NOT_RUN":raise SystemExit("binary/PTX/probe missing")
 results=a.result_root.resolve()
 if results.exists() and any(results.iterdir()):raise SystemExit("nonempty result root")
 results.mkdir(parents=True,exist_ok=True);(results/"campaign-manifest.json").write_text(json.dumps(plan,indent=2)+"\n");base=json.loads(a.profile.read_text())
 for c in cells:
  if "reuse" in c:continue
  page,den=c["page_bytes"],c["denominator"];rel=c["kind"]=="relative";name=f"relative/page_{page}/fraction_1_{den}" if rel else f"fixed/page_{page}/{c['workload']}"
  out=results/name;out.mkdir(parents=True);profile=dict(base);profile.update({"name":"r3-"+name.replace("/","-"),"capacity_bytes":LOGICAL,"hbm_cache_bytes":CACHE,"page_bytes":page,"time_scale":1})
  pf=out/"profile.json";pf.write_text(json.dumps(profile,indent=2)+"\n");env=os.environ.copy();env.update({"HBFSIM_BUILD_DIR":str(dep),"HBFSIM_BPFTIME_BUILD_DIR":str(a.bpftime_build_dir.resolve()),"HBFSIM_COVERAGE_PATH":str(out/"coverage.jsonl"),"HBFSIM_PASS_MANIFEST_PATH":str(out/"pass-manifests.jsonl"),"HBFSIM_DAEMON_PATH":str(dep/"hbfsimd"),"HBFSIM_BPFTIME_PROBE":str(probe),"HBFSIM_CAPACITY_STATS_V1":"1"})
  cmd=[str(root/"scripts/run_with_bpftime.sh"),"--",str(binary),"--profile",str(pf),"--report-dir",str(out),"--backing-dir",str(a.backing_dir.resolve()),"--output",str(out/"result.json"),"--ptx",str(ptx),"--page-bytes",str(page),"--workload",c["workload"],"--coverage-denominator",str(den)]
  (out/"command.json").write_text(json.dumps(cmd,indent=2)+"\n")
  try:
   done=subprocess.run(cmd,cwd=root,env=env,text=True,capture_output=True,timeout=1800);(out/"stdout.log").write_text(done.stdout);(out/"stderr.log").write_text(done.stderr)
  except subprocess.TimeoutExpired as exc:
   def text(value):
    if value is None:return ""
    return value.decode(errors="replace") if isinstance(value,bytes) else value
   (out/"stdout.log").write_text(text(exc.stdout));(out/"stderr.log").write_text(text(exc.stderr)+"\nTIMEOUT\n");(out/"FAILED.json").write_text(json.dumps({"status":"TIMEOUT","timeout_seconds":1800,"case":name},indent=2)+"\n");raise SystemExit("case timeout: "+name)
  if done.returncode:
   (out/"FAILED.json").write_text(json.dumps({"status":"NONZERO_EXIT","returncode":done.returncode,"case":name},indent=2)+"\n");raise SystemExit("case failed: "+name)
 reuse=results/"relative/page_65536/fraction_1_1";reuse.mkdir(parents=True);(reuse/"REUSED.json").write_text(json.dumps({"status":"REUSED","source":"../../../fixed/page_65536/fixed_dense/result.json","source_stage":"cold","reason":"same 65 bases, 65536 bytes, order and pattern","do_not_substitute_for_smaller_page_full_cells":True},indent=2)+"\n");return 0
if __name__=="__main__":raise SystemExit(main())
