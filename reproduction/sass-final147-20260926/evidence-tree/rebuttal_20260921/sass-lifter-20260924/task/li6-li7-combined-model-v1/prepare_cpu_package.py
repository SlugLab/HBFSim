"""One CPU-only, no-overwrite manifest/stage preparation from frozen receipts."""
from pathlib import Path
import copy
import hashlib
import json

TASK=Path(__file__).resolve().parent
ROOT=TASK.parent
OUT=TASK/"prepared-v2"
LEDGER=ROOT/"full-coverage-ledger-v1/TARGET_LEDGER.json"
AUDIT=ROOT/"full-coverage-ledger-v1/NATIVE_PROFILE_REUSE.json"
OLD=ROOT/"qkv-model-combined-v1/prepared-v1"
LI7=ROOT/"router-li7-model-representative-v1"

def need(ok,msg):
    if not ok: raise RuntimeError(msg)
def sha(path): return hashlib.sha256(path.read_bytes()).hexdigest()
def write(path,data):
    path.parent.mkdir(parents=True,exist_ok=True)
    with path.open("xb") as f:f.write(data)
    return sha(path)
def jwrite(path,data):return write(path,(json.dumps(data,indent=2,sort_keys=True)+"\n").encode())

def main():
    need(not OUT.exists(),"prepared-v2 already exists")
    ledger=json.loads(LEDGER.read_text());audit=json.loads(AUDIT.read_text())
    entries=ledger["entries"]
    need(len(entries)==147 and len([x for x in entries if x["category"]=="old98"])==98,
         "ledger147/old98 identity")
    profiles={"qkv_proj":((6144,2048),25165824,(1,6144),12288,"li6"),
              "o_proj":((2048,2048),8388608,(1,2048),4096,"li6"),
              "router":((64,2048),262144,(1,64),128,"li7"),
              "lm_head":((50304,2048),206045184,(1,50304),100608,"li6")}
    li6_symbol="_ZN8internal5gemvx6kernelIii13__nv_bfloat16S2_S2_fLb0ELb1ELb1ELb0ELi6ELb0E18cublasGemvParamsExIi30cublasGemvTensorStridedBatchedIKS2_ES6_S4_IS2_EfEEENSt9enable_ifIXntT5_EvE4typeET11_"
    li7_symbol=li6_symbol.replace("ELi6E","ELi7E")
    image="d15cf2497649902226341d260eee82160e485d38e76cdcb9cb581eca82167eb0"
    grids={"qkv_proj":1536,"o_proj":512,"router":16,"lm_head":12576}
    rows=[]
    for item in entries:
        cat=item["category"]
        if cat=="old98":continue
        need(cat in profiles and len(item["aliases"])==1 and len(item["tensor_profiles"])==1,
             "ambiguous target")
        shape,size,outshape,outbytes,profile=profiles[cat]
        tensor=item["tensor_profiles"][0]
        need(tuple(tensor["shape"])==shape and item["storage_bytes"]==size and
             tensor["dtype"]=="torch.bfloat16" and tensor["storage_offset"]==0,
             "target storage profile mismatch")
        native=audit["categories"][cat]
        need(item["aliases"][0] in native["aliases"] and
             native["all_layers_same_profile"] and
             len(native["profiles"])==1,
             "native profile mismatch")
        observed=native["profiles"][0]
        expected_blas={"api":"cublasGemmEx","m":shape[0],"n":1,"k":2048,
                       "trans_a":1,"trans_b":0,"a_type":14,"b_type":14,
                       "c_type":14,"lda":2048,"ldb":2048,"ldc":shape[0],
                       "compute_type":68,"algorithm":99}
        need(all(observed["blas"].get(k)==v for k,v in expected_blas.items()) and
             observed.get("operand")=="A" and observed.get("offset_bytes")==0,
             "native BLAS/operand profile mismatch")
        driver=observed["exact_driver"]
        expected_driver={"api":"cuLaunchKernel","cbid":307,"image_sha256":image,
                         "symbol":li7_symbol if profile=="li7" else li6_symbol,
                         "grid":[grids[cat],1,1],
                         "block":[32 if profile=="li7" else 16,4,1],
                         "dynamic_shared_bytes":528 if profile=="li7" else 272}
        need(len(driver)==1 and all(driver[0].get(k)==v for k,v in expected_driver.items()),
             "exact entry mismatch")
        layer=item["layer"]
        need((cat=="lm_head" and layer is None) or
             (cat!="lm_head" and isinstance(layer,int) and 0<=layer<16),
             "layer mismatch")
        rows.append({"alias":item["aliases"][0],"category":cat,"layer":layer,
                     "profile":profile,"shape":list(shape),"storage_bytes":size,
                     "output_shape":list(outshape),"output_bytes":outbytes,
                     "native_driver":driver[0]})
    need(len(rows)==49 and len({x["alias"] for x in rows})==49,
         "49 unique aliases")
    selections={"mixed3":[x for x in rows if x["layer"]==0],
                "remaining45":[x for x in rows if x["layer"] in range(1,16)],
                "added49":rows}
    need([len(v) for v in selections.values()]==[3,45,49],"scope sizes")
    OUT.mkdir()
    scope_hashes={}
    for name,selected in selections.items():
        scope_hashes[name]=jwrite(OUT/f"scope-{name}.json",{
            "schema":"hbfsim.combined_selected_scope.v1","scope":name,
            "selected_count":len(selected),"final_loader_registration_count":147,
            "old98_registration_count":98,"targets":selected,
            "source_ledger":str(LEDGER),"source_native_audit":str(AUDIT)})
    stage=OUT/"stage";stage.mkdir()
    old_stage=OLD/"stage"
    old_names={p.name for p in old_stage.glob("*.ptx")}
    need(len(old_names)==8,"old98+QKV staged file count")
    li7_meta=json.loads((LI7/"MODEL_TARGET_MANIFEST.json").read_text())
    li7_path=Path(li7_meta["staged_ptx"]["path"])
    need(li7_path.name==li7_meta["source_ptx"]["sha256"]+".ptx" and
         li7_path.name not in old_names and li7_path.is_file(),"Li7 stage path join")
    for source in [*(old_stage/n for n in sorted(old_names)),li7_path]:
        (stage/source.name).symlink_to(source.resolve(strict=True))
    stage_hashes=json.loads((ROOT/"qkv-model-combined-v1/config-final-v2/runtime-config.pre-resource-combined.json").read_text())["stage_sha256"]
    need(set(stage_hashes)==old_names,"old stage identity set")
    stage_hashes[li7_path.name]=li7_meta["staged_ptx"]["sha256"]
    old_pass=(OLD/"pass-manifests.jsonl").read_bytes()
    li7_pass=(LI7/"model-stage-v1/pass-manifests.jsonl").read_bytes()
    need(old_pass.count(b"\n")==9 and li7_pass.count(b"\n")==1,"pass manifest rows")
    pass_sha=write(OUT/"pass-manifests.jsonl",old_pass+li7_pass)
    bindings=json.loads((OLD/"native-bindings-union.json").read_text())
    need(len(bindings["bindings"])==4,"old98 binding count")
    sidecars={}
    for row in bindings["bindings"]:
        oldpath=Path(row["staged_path"])
        need(oldpath.parent==old_stage and oldpath.name in old_names,"old binding stage")
        newpath=stage/oldpath.name
        oldside=Path(row["provenance_sidecar"])
        if str(oldside) not in sidecars:
            parsed=json.loads(oldside.read_text())
            for join in parsed["module_joins"]:
                joined=Path(join["staged_path"])
                need(joined.parent==old_stage and joined.name in old_names,"old sidecar join")
                join["staged_path"]=str(stage/joined.name)
            target=OUT/"sidecars"/oldside.name
            sidecars[str(oldside)]=(str(target),jwrite(target,parsed))
        row["staged_path"]=str(newpath)
        row["provenance_sidecar"],row["provenance_sha256"]=sidecars[str(oldside)]
    binding_sha=jwrite(OUT/"native-bindings-union.json",bindings)
    li7_side=json.loads((LI7/"model-sidecar.json").read_text())
    need(len(li7_side["module_joins"])==1 and
         li7_side["module_joins"][0]["raw_sha256"]==li7_meta["source_ptx"]["sha256"],
         "Li7 sidecar source")
    li7_side["module_joins"][0]["staged_path"]=str(stage/li7_path.name)
    li7_side_sha=jwrite(OUT/"sidecars/li7-model-sidecar.json",li7_side)
    jwrite(OUT/"STAGE_JOIN_RECEIPT.json",{
        "schema":"hbfsim.combined_two_profile_stage.v1","status":"CPU_PREPARED_NO_GPU",
        "stage_dir":str(stage),"stage_sha256":stage_hashes,
        "source_to_stage":{"li6":{"source_sha256":"6db074711d19c3e31cbcc98c6e170f0b4330259f220c2d518ce8aee42b9c9eab",
                                "staged_path":str(stage/"6db074711d19c3e31cbcc98c6e170f0b4330259f220c2d518ce8aee42b9c9eab.ptx"),
                                "staged_sha256":stage_hashes["6db074711d19c3e31cbcc98c6e170f0b4330259f220c2d518ce8aee42b9c9eab.ptx"]},
                           "li7":{"source_sha256":li7_meta["source_ptx"]["sha256"],
                                  "staged_path":str(stage/li7_path.name),
                                  "staged_sha256":li7_meta["staged_ptx"]["sha256"]}},
        "pass_manifest_sha256":pass_sha,"binding_manifest_sha256":binding_sha,
        "li7_sidecar_sha256":li7_side_sha,"scope_manifest_sha256":scope_hashes,
        "stage_files":9,"old98_registration_count":98,"selected_added_count":49})

if __name__=="__main__":main()
