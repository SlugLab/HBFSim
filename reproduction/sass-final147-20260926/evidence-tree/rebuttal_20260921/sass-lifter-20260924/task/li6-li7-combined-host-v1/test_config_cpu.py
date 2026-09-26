"""Compile the exact immutable-config source block without loading CUDA."""
import json
import os
from pathlib import Path
import re
import resource
import subprocess

TASK=Path(__file__).resolve().parent
BUILD=TASK/"build-v5"
SOURCE=(TASK/"source/nv_attach_impl_frida_setup.cpp").read_text()
NAMES=("kQkvAbiMapSha","kLi7AbiMapSha","kQkvStagedPtxSha","kLi7StagedPtxSha")

def limit(): resource.setrlimit(resource.RLIMIT_AS,(1024**3,1024**3))

def main():
    cpp=BUILD/"config_fixture.cpp"
    exe=BUILD/"config_fixture"
    if cpp.exists() or exe.exists(): raise RuntimeError("config fixture exists")
    constants=[]
    for name in NAMES:
        match=re.search(r"constexpr char "+name+r"\[\]\s*=\s*\n\s*\"([^\"]+)\";",SOURCE)
        if not match: raise RuntimeError(f"missing source constant {name}")
        constants.append(f'constexpr char {name}[]="{match.group(1)}";')
    start=SOURCE.index("struct CombinedConfig {")
    end=SOURCE.index("enum class ExactProfile",start)
    block=SOURCE[start:end]
    cpp.write_text("#include <cstdlib>\n#include <cstring>\n#include <string>\n#include <iostream>\n"+
                   "\n".join(constants)+"\n"+block+"\nint main(){"
                   "const auto &c=combined_config();"
                   'std::cout<<c.enabled<<" "<<c.valid<<" "<<c.source[0]<<" ";'
                   "setenv(\"HBFSIM_LI6_SOURCE_PTX_PATH\",\"MUTATED\",1);"
                   "std::cout<<combined_config().source[0]<<\"\\n\";return 0;}\n")
    args=["/usr/bin/g++-13","-std=c++20","-O0",str(cpp),"-o",str(exe)]
    (BUILD/"config_fixture_compile.argv.json").write_text(json.dumps(args,indent=2)+"\n")
    proc=subprocess.run(args,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=120,preexec_fn=limit,
                        env={**os.environ,"CUDA_VISIBLE_DEVICES":""})
    (BUILD/"config_fixture_compile.stderr").write_bytes(proc.stderr)
    if proc.returncode: raise RuntimeError("config fixture compile failed")
    matches={n:re.search(r"constexpr char "+n+r"\[\]\s*=\s*\n\s*\"([^\"]+)\";",SOURCE).group(1) for n in NAMES}
    base={"CUDA_VISIBLE_DEVICES":"","HBFSIM_QKV_COMBINED_V1":"1",
          "HBFSIM_LI6_SOURCE_PTX_PATH":"LI6_SOURCE",
          "HBFSIM_LI6_STAGED_PTX_PATH":"LI6_STAGE",
          "HBFSIM_LI6_ABI_MAP_SHA256":matches["kQkvAbiMapSha"],
          "HBFSIM_LI6_STAGED_PTX_SHA256":matches["kQkvStagedPtxSha"],
          "HBFSIM_LI7_SOURCE_PTX_PATH":"LI7_SOURCE",
          "HBFSIM_LI7_STAGED_PTX_PATH":"LI7_STAGE",
          "HBFSIM_LI7_ABI_MAP_SHA256":matches["kLi7AbiMapSha"],
          "HBFSIM_LI7_STAGED_PTX_SHA256":matches["kLi7StagedPtxSha"]}
    cases={"valid":({},"1 1 LI6_SOURCE LI6_SOURCE"),
           "missing_other_row":({"HBFSIM_LI7_STAGED_PTX_PATH":None},"1 0"),
           "wrong_other_pin":({"HBFSIM_LI7_ABI_MAP_SHA256":"BAD"},"1 0"),
           "conflicting_target":({"HBFSIM_QKV_TARGET_KIND":"router_li7"},"1 0"),
           "default_off":({"HBFSIM_QKV_COMBINED_V1":None},"0 0")}
    for name,(change,expected) in cases.items():
        env={**os.environ,**base}
        env.pop("HBFSIM_QKV_TARGET_KIND",None)
        for key,value in change.items():
            if value is None: env.pop(key,None)
            else: env[key]=value
        result=subprocess.run([str(exe)],env=env,stdout=subprocess.PIPE,stderr=subprocess.PIPE,
                              timeout=10,preexec_fn=limit)
        if result.returncode or not result.stdout.decode().startswith(expected):
            raise RuntimeError(f"{name}: {result.returncode} {result.stdout!r} {result.stderr!r}")
    (BUILD/"config_fixture_result.json").write_text(json.dumps({
        "schema":"hbfsim.combined_config_cpu_fixture.v1","status":"PASS_NO_GPU",
        "source_block":"nv_attach_impl_frida_setup.cpp CombinedConfig exact extracted block",
        "cases":list(cases),"valid_case_proves_later_env_mutation_ignored":True},indent=2)+"\n")

if __name__=="__main__": main()
