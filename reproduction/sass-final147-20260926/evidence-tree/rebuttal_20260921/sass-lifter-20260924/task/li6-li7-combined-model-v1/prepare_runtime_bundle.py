"""CPU-only isolated runtime symlink bundle; no copies of unchanged large binaries."""
import json
from pathlib import Path

TASK=Path(__file__).resolve().parent
ROOT=TASK.parents[1]
BASE=ROOT/"task/all-weights-category-representatives-v1/bundle-li6-v1/build"
HOST=ROOT/"task/li6-li7-combined-host-v1/build-v5"
GATE=ROOT/"task/router-li7-host-adapter-v1/build-gate-v2/libhbfsim_launch_gate.so"
OUT=TASK/"bundle-combined-v1/build"

def main():
    if OUT.parent.exists():raise RuntimeError("combined bundle already exists")
    receipt=json.loads((HOST.parent/"CPU_BUILD_RECEIPT.json").read_text())
    if receipt.get("status")!="CPU_BUILD_PASS_NO_GPU":raise RuntimeError("combined host build absent")
    roles={}
    for path in BASE.rglob("*"):
        if not (path.is_file() or path.is_symlink()):continue
        relative=path.relative_to(BASE)
        roles[str(relative)]=path.resolve(strict=True)
    roles["runtime/agent/libbpftime-agent.so"]=(HOST/"libbpftime-agent.so").resolve(strict=True)
    roles["libhbfsim_launch_gate.so"]=GATE.resolve(strict=True)
    roles["libprovider_router.so"]=(HOST/"libprovider_router.so").resolve(strict=True)
    if len(roles)<10:raise RuntimeError("base bundle inventory incomplete")
    for name,target in sorted(roles.items()):
        link=OUT/name;link.parent.mkdir(parents=True,exist_ok=True)
        link.symlink_to(target)
    manifest={"schema":"hbfsim.combined_runtime_bundle.v1",
              "status":"CPU_SYMLINK_BUNDLE_NO_GPU","configured_root":str(OUT),
              "members":{name:{"resolved":str(target),"bytes":target.stat().st_size,
                               "device":target.stat().st_dev,"inode":target.stat().st_ino}
                         for name,target in sorted(roles.items())},
              "changed_agent_sha256_from_build_receipt":receipt["products"]["libbpftime-agent.so"]["sha256"],
              "changed_provider_sha256_from_build_receipt":receipt["products"]["libprovider_router.so"]["sha256"],
              "unchanged_gate":str(GATE)}
    with (OUT.parent/"BUNDLE_JOIN.json").open("x") as stream:
        json.dump(manifest,stream,indent=2,sort_keys=True);stream.write("\n")

if __name__=="__main__":main()
