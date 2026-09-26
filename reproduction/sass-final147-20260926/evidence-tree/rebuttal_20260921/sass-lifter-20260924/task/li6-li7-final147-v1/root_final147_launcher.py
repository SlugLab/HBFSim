#!/usr/bin/env python3
"""Root-only future final147 prepare/launch; never starts on import."""
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

PACKET=Path(__file__).resolve().parent
ROOT=PACKET.parents[1]
TASK=ROOT/"task/li6-li7-combined-model-v1"
RUN=ROOT/"runs/li6-li7-final147-v1"
TEMPLATE=PACKET/"FINAL147_PLAN_TEMPLATE.json"
LIVE=PACKET/"FINAL147_LIVE_PLAN.json"
REVIEW=PACKET/"ROOT_REVIEWED_FINAL147.json"
BUNDLE=TASK/"bundle-combined-v1/build"
HOST=ROOT/"task/li6-li7-combined-host-v1/build-v5"
CONTROLLER=TASK/"run_bounded_selected_gpu_stage.py"
GUARD=TASK/"resource_guard_selected_gpu.py"
VALIDATOR=TASK/"validate_combined_model.py"
HELPER=ROOT/"task/all-weights-category-representatives-v1/li6_preflight_v2.py"
RUNNER=ROOT/"workspace-freeze-copy-v5/B-weight-binding-config-source-v1/adapters/vllm/run.py"
OWNER_LOCK=ROOT/"task/resume-giga-20260925-v1/gpu-owner.lock"
_owner_lock_fd=None

def need(ok,msg):
    if not ok:raise RuntimeError(msg)
def load(path):return json.loads(Path(path).read_text())
def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def save_new(path,value):
    with Path(path).open("x") as stream:
        json.dump(value,stream,indent=2,sort_keys=True);stream.write("\n")
        stream.flush();os.fsync(stream.fileno())
def ticks(pid):return int(Path(f"/proc/{pid}/stat").read_text().rsplit(")",1)[1].split()[19])
def boot():return Path("/proc/sys/kernel/random/boot_id").read_text().strip()

def dependency(path, status, scope, label):
    receipt=load(path)
    need(receipt.get("status")==status and receipt.get("scope")==scope and
         isinstance(receipt.get("epoch"),int),f"{label} model PASS dependency absent")
    return {f"{label}_validation_path":str(Path(path).resolve(strict=True)),
            f"{label}_validation_sha256":sha(path),
            f"{label}_validation_epoch":receipt["epoch"]}

def dependencies(remaining45,mixed3,head):
    head_row=load(head)
    need(head_row.get("status")=="MODEL_CONNECTED_PASS_LM_HEAD" and
         isinstance(head_row.get("epoch"),int),"head model PASS dependency absent")
    return {**dependency(remaining45,"MODEL_CONNECTED_PASS_COMBINED_REMAINING45","remaining45","remaining45"),
            **dependency(mixed3,"MODEL_CONNECTED_PASS_COMBINED_MIXED3","mixed3","mixed3"),
            "head_validation_path":str(Path(head).resolve(strict=True)),
            "head_validation_sha256":sha(head),
            "head_validation_epoch":head_row["epoch"]}

def finite_identity(receipt):
    if receipt.get("status")=="FRESH_FINITE_RESERVATION_START":
        return (receipt["reservation_pid"],receipt["reservation_start_ticks"],
                receipt["watchdog_pid"],receipt["watchdog_start_ticks"],
                receipt["deadline_unix"],receipt["boot_id"])
    need(receipt.get("status")=="FINITE_WATCHDOG_HANDOFF_COMPLETE",
         "fresh START or completed finite renewal receipt required")
    return (receipt["reservation_pid"],receipt["reservation_start_ticks"],
            receipt["new_watchdog_pid"],receipt["new_watchdog_start_ticks"],
            receipt["new_deadline_unix"],None)

def artifacts(plan):
    need(plan["template_sha256"]==sha(TEMPLATE) and
         plan["plugin_sha256"]==sha(TASK/"combined_model_adapter/__init__.py") and
         plan["validator_sha256"]==sha(VALIDATOR) and
         plan["controller_sha256"]==sha(CONTROLLER) and
         plan["guard_sha256"]==sha(GUARD) and
         plan["capture_helper_sha256"]==sha(HELPER) and
         plan["launcher_sha256"]==sha(Path(__file__)) and
         plan["bundle_join_sha256"]==sha(TASK/"bundle-combined-v1/BUNDLE_JOIN.json") and
         plan["stage_join_sha256"]==sha(TASK/"prepared-v2/STAGE_JOIN_RECEIPT.json") and
         plan["scope_manifest_sha256"]==sha(TASK/"prepared-v2/scope-added49.json"),
         "reviewed combined artifacts changed")
    join=load(TASK/"bundle-combined-v1/BUNDLE_JOIN.json")
    critical=("runtime/agent/libbpftime-agent.so","libhbfsim_launch_gate.so",
              "libprovider_router.so")
    identities=[]
    for name in critical:
        configured=BUNDLE/name; actual=configured.resolve(strict=True); stat=configured.stat()
        recorded=join["members"][name]
        need((str(actual),stat.st_dev,stat.st_ino,stat.st_size)==
             (recorded["resolved"],recorded["device"],recorded["inode"],recorded["bytes"]),
             f"critical DSO backing changed: {name}")
        identities.append({"configured":str(configured),"resolved":str(actual),
                           "device":stat.st_dev,"inode":stat.st_ino,
                           "bytes":stat.st_size})
    need(plan.get("backing_identities")==identities,"reviewed DSO identities changed")
    stage=load(TASK/"prepared-v2/STAGE_JOIN_RECEIPT.json")
    need(set(Path(stage["stage_dir"]).glob("*.ptx"))==
         {Path(stage["stage_dir"])/x for x in stage["stage_sha256"]},
         "two-profile staged filename set changed")
    for key in ("li6","li7"):
        row=stage["source_to_stage"][key];path=Path(row["staged_path"])
        need(path.name==row["source_sha256"]+".ptx" and path.is_symlink() and
             path.resolve(strict=True).is_file(),f"{key} source/stage path join")
    return identities

def gpu_selected(index):
    query=subprocess.run(["nvidia-smi","--query-gpu=index,uuid,memory.total,memory.free",
                          "--format=csv,noheader,nounits"],capture_output=True,text=True,
                         check=True,timeout=20).stdout.splitlines()
    rows={}
    for line in query:
        parts=[x.strip() for x in line.split(",")]
        need(len(parts)==4,"GPU inventory row malformed")
        rows[int(parts[0])]=(parts[1],int(parts[2]),int(parts[3]))
    need(index in rows,"configured GPU index absent from actual inventory")
    return rows[index]

def preflight(plan,renewal,live):
    need(boot()==plan["boot_id"]==live["boot_id"],"host boot changed")
    need(ticks(plan["reservation_pid"])==plan["reservation_start_ticks"] and
         ticks(plan["watchdog_pid"])==plan["watchdog_start_ticks"],
         "finite reservation/watchdog PID/start changed")
    finite=finite_identity(renewal)
    need((finite[5] is None or finite[5]==plan["boot_id"]) and
         finite[:5]==
         (plan["reservation_pid"],plan["reservation_start_ticks"],
          plan["watchdog_pid"],plan["watchdog_start_ticks"],plan["reserve_deadline_unix"]),
         "finite START/renewal identity/deadline changed")
    hard=renewal.get("reservation_hard_deadline_unix")
    need(hard is None or plan["reserve_deadline_unix"]<=hard,
         "watchdog deadline exceeds reservation self-deadline")
    identities={r["role"]:r for r in live.get("identities",[])}
    for role,prefix in (("reserve","reservation"),("watchdog","watchdog")):
        need((identities.get(role,{}).get("pid"),identities.get(role,{}).get("start_ticks"))==
             (plan[f"{prefix}_pid"],plan[f"{prefix}_start_ticks"]),
             f"live {role} receipt differs")
    need(time.time()+plan["full_admission_seconds"]<plan["reserve_deadline_unix"],
         "finite lease cannot cover full window")
    uuid,total,free=gpu_selected(plan["configured_gpu_index"])
    need(free>=max(plan["minimum_free_mib"],int(total*.05)+1),
         "selected GPU free memory below margin")
    apps=subprocess.run(["nvidia-smi","--query-compute-apps=gpu_uuid,pid",
                         "--format=csv,noheader,nounits"],capture_output=True,
                        text=True,check=True,timeout=20).stdout.splitlines()
    selected_pids=set()
    for line in apps:
        parts=[x.strip() for x in line.split(",")]
        if len(parts)==2 and parts[0]==uuid and parts[1].isdigit():selected_pids.add(int(parts[1]))
    need(selected_pids=={plan["reservation_pid"]},
         "selected GPU has another compute owner")
    mem={x[0].rstrip(":"):int(x[1]) for line in Path("/proc/meminfo").read_text().splitlines()
         if (x:=line.split())}
    need(mem["MemAvailable"]>=int(mem["MemTotal"]*.05)+1,"RAM margin insufficient")
    need(shutil.disk_usage(plan["disk_path"]).free>=plan["minimum_disk_gib"]*1024**3,
         "disk margin insufficient")
    return {"selected_gpu_index":plan["configured_gpu_index"],"observed_gpu_uuid":uuid,
            "observed_total_mib":total,"observed_free_mib":free}

def prepare(args):
    need(not LIVE.exists() and not RUN.exists(),"final147 live plan/run already exists")
    need(args.epoch > 0 and args.gpu_index >= 0,"positive epoch and nonnegative configured GPU index required")
    dep=dependencies(args.remaining45_validation,args.mixed3_validation,args.head_validation)
    renewal=load(args.renewal_receipt);live=load(args.live_resource_receipt)
    plan=load(TEMPLATE)
    need(plan["scope"]=="added49" and
         all(isinstance(plan.get(k),int) and plan[k]>0 for k in
             ("worker_seconds","outer_seconds","collection_seconds",
              "startup_cleanup_seconds","full_admission_seconds")) and
         plan["outer_seconds"]>plan["worker_seconds"] and
         plan["full_admission_seconds"]>=plan["outer_seconds"]+
         plan["collection_seconds"]+plan["startup_cleanup_seconds"],
         "final147 finite budget pending root decision")
    plan["epoch"]=args.epoch
    plan["command"][plan["command"].index("--accounting-epoch")+1]=str(args.epoch)
    plan["env"]["HBFSIM_QKV_EPOCH"]=str(args.epoch)
    plan["env"]["CUDA_VISIBLE_DEVICES"]=str(args.gpu_index)
    plan["env"]["CUDA_DEVICE_ORDER"]="PCI_BUS_ID"
    finite=finite_identity(renewal)
    plan.update({"configured_gpu_index":args.gpu_index,
                 "reservation_pid":finite[0],
                 "reservation_start_ticks":finite[1],
                 "watchdog_pid":finite[2],
                 "watchdog_start_ticks":finite[3],
                 "reserve_deadline_unix":finite[4],
                 "boot_id":live["boot_id"],
                 "renewal_receipt_path":str(args.renewal_receipt.resolve(strict=True)),
                 "renewal_receipt_sha256":sha(args.renewal_receipt),
                 "live_resource_receipt_path":str(args.live_resource_receipt.resolve(strict=True)),
                 "live_resource_receipt_sha256":sha(args.live_resource_receipt),
                 "template_sha256":sha(TEMPLATE),"launcher_sha256":sha(Path(__file__)),
                 "controller_sha256":sha(CONTROLLER),"guard_sha256":sha(GUARD),
                 "validator_sha256":sha(VALIDATOR),"capture_helper_sha256":sha(HELPER),
                 "model_dependencies":"HEAD_MIXED3_REMAINING45_PASS_VERIFIED",**dep})
    join=load(TASK/"bundle-combined-v1/BUNDLE_JOIN.json")
    plan["backing_identities"]=[{"configured":str(BUNDLE/name),
        "resolved":join["members"][name]["resolved"],
        "device":join["members"][name]["device"],
        "inode":join["members"][name]["inode"],
        "bytes":join["members"][name]["bytes"]}
        for name in ("runtime/agent/libbpftime-agent.so","libhbfsim_launch_gate.so",
                     "libprovider_router.so")]
    artifacts(plan)
    plan["observed_gpu_at_prepare"]=preflight(plan,renewal,live)
    save_new(LIVE,plan)
    print(json.dumps({"status":"FINAL147_LIVE_PLAN_PREPARED_NO_GPU_START",
                      "plan":str(LIVE),"plan_sha256":sha(LIVE)}))

def capture(owner,plan):
    sys.path.insert(0,str(ROOT/"task/all-weights-category-representatives-v1"))
    sys.path.insert(0,str(TASK))
    import li6_preflight_v2
    before=[(x["resolved"],x["device"],x["inode"]) for x in artifacts(plan)]
    outcome=li6_preflight_v2.capture_target_once(owner,RUN,[x[0] for x in before],
                                                   str(RUNNER),timeout_seconds=180)
    cap=RUN/"live-capture-resolved-v1";cap.mkdir(exist_ok=False)
    for name in ("target-start-capture.json","target-start-maps.txt","target-start-env.json"):
        path=RUN/name
        if path.exists():path.rename(cap/name)
    from validate_combined_model import maps_match
    maps=(cap/"target-start-maps.txt").read_text() if (cap/"target-start-maps.txt").exists() else ""
    after=[(x["resolved"],x["device"],x["inode"]) for x in artifacts(plan)]
    ok=(outcome.get("status")=="CAPTURED" and outcome.get("required_maps_present") is True and
        before==after and all(maps_match(maps,*x) for x in after))
    save_new(cap/"path-join.json",{"status":"EXACT_CONFIGURED_TO_RESOLVED_INODE_JOIN"
             if ok else "NOT_CAPTURED", "libraries":[{"configured_path":x["configured"],
             "resolved_path":x["resolved"],"device":x["device"],"inode":x["inode"]}
             for x in plan["backing_identities"]],"owner_start_sha256":sha(RUN/"owner-start.json"),
             "target_pid":outcome.get("pid"),"target_start_ticks":outcome.get("start_ticks")})
    need(ok,"actual target path/device/inode capture failed; retained NOT_CAPTURED receipt")
    need(ticks(outcome["pid"])==outcome["start_ticks"],"target PID changed before CUDA order capture")
    environ=Path(f"/proc/{outcome['pid']}/environ").read_bytes().split(b"\0")
    selected_env={}
    for item in environ:
        key,sep,value=item.partition(b"=")
        if sep and key in (b"CUDA_DEVICE_ORDER",b"CUDA_VISIBLE_DEVICES"):
            selected_env[key.decode()]=value.decode("utf-8","replace")
    need(ticks(outcome["pid"])==outcome["start_ticks"],"target PID changed after CUDA order capture")
    save_new(cap/"selected-cuda-env.json",{"target_pid":outcome["pid"],
             "target_start_ticks":outcome["start_ticks"],"env":selected_env})
    selected_uuid,_,_=gpu_selected(plan["configured_gpu_index"])
    apps=subprocess.run(["nvidia-smi","--query-compute-apps=gpu_uuid,pid",
                         "--format=csv,noheader,nounits"],check=True,capture_output=True,
                        text=True,timeout=20).stdout.splitlines()
    target_rows=[]
    for line in apps:
        parts=[x.strip() for x in line.split(",")]
        if len(parts)==2 and parts[1]==str(outcome["pid"]):target_rows.append(parts)
    matched=len(target_rows)==1 and target_rows[0][0]==selected_uuid
    save_new(cap/"selected-gpu-occupancy.json",{
        "status":"TARGET_ON_CONFIGURED_GPU" if matched else "WRONG_OR_MISSING_GPU",
        "configured_gpu_index":plan["configured_gpu_index"],
        "observed_selected_uuid":selected_uuid,"target_pid":outcome["pid"],
        "observed_target_gpu_rows":target_rows})
    need(matched,"actual target process is not on configured selected GPU")
    return outcome

def launch():
    need(os.environ.get("HBFSIM_EXECUTION_OWNER")=="1", "root GPU execution owner gate absent")
    need(not RUN.exists(),"final147 run already exists")
    plan=load(LIVE);review=load(REVIEW)
    need(review.get("status")=="ROOT_REVIEWED_COMBINED_FINAL147_V1" and
         review.get("plan_sha256")==sha(LIVE) and
         review.get("launcher_sha256")==sha(Path(__file__)) and
         review.get("validator_sha256")==sha(VALIDATOR),"exact root review absent")
    need(all(dependencies(plan["remaining45_validation_path"],
                          plan["mixed3_validation_path"],
                          plan["head_validation_path"])[key]==plan[key]
             for key in ("remaining45_validation_sha256",
                         "mixed3_validation_sha256","head_validation_sha256")),
         "final model PASS dependencies changed")
    need(sha(Path(plan["renewal_receipt_path"]))==plan["renewal_receipt_sha256"] and
         sha(Path(plan["live_resource_receipt_path"]))==plan["live_resource_receipt_sha256"],
         "finite lease receipts changed")
    artifacts(plan)
    preflight(plan,load(plan["renewal_receipt_path"]),load(plan["live_resource_receipt_path"]))
    RUN.mkdir(exist_ok=False)
    for name in ("result/cell","result/combined-plugin","logs","result/cache/tmp",
                 "result/cache/hf","result/cache/xdg","result/cache/torch",
                 "result/cache/cuda","result/cache/inductor","result/cache/vllm",
                 "result/cache/triton"):
        (RUN/name).mkdir(parents=True,exist_ok=True)
    save_new(RUN/"plan.json",plan)
    parent_env={k:v for k,v in os.environ.items() if k!="LD_PRELOAD" and
                not k.startswith(("HBFSIM_","BPFTIME_"))}
    with (RUN/"owner.stdout").open("xb") as stdout,(RUN/"owner.stderr").open("xb") as stderr:
        owner=subprocess.Popen([sys.executable,str(CONTROLLER),"--plan",str(RUN/"plan.json"),
                                "--plan-sha256",sha(RUN/"plan.json")],cwd=ROOT.parent,
                               env=parent_env,stdin=subprocess.DEVNULL,stdout=stdout,stderr=stderr,
                               start_new_session=True,pass_fds=(_owner_lock_fd,))
    save_new(RUN/"owner-start.json",{"schema":"hbfsim.combined_final147_owner.v1",
             "pid":owner.pid,"start_ticks":ticks(owner.pid),"start_unix_ns":time.time_ns(),
             "plan_sha256":sha(RUN/"plan.json")})
    print(json.dumps({"owner_pid":owner.pid,"target_capture":capture(owner,plan)},sort_keys=True))

def main():
    parser=argparse.ArgumentParser()
    group=parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--prepare",action="store_true")
    group.add_argument("--launch",action="store_true")
    parser.add_argument("--remaining45-validation",type=Path)
    parser.add_argument("--mixed3-validation",type=Path)
    parser.add_argument("--head-validation",type=Path)
    parser.add_argument("--renewal-receipt",type=Path)
    parser.add_argument("--live-resource-receipt",type=Path)
    parser.add_argument("--gpu-index",type=int)
    parser.add_argument("--epoch",type=int)
    args=parser.parse_args()
    global _owner_lock_fd
    with OWNER_LOCK.open("a+") as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        _owner_lock_fd=lock.fileno()
        if args.prepare:
            need(all(x is not None for x in (args.remaining45_validation,args.mixed3_validation,args.head_validation,args.renewal_receipt,
                 args.live_resource_receipt,args.gpu_index,args.epoch)),
                 "prepare requires head, mixed3 and remaining45 PASS, finite lease, configured GPU and epoch")
            prepare(args)
        else:launch()

if __name__=="__main__":main()
