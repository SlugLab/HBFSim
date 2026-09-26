"""Narrow fake tensor/agent checks; no torch, vLLM, CUDA context, or GPU."""
import hashlib
import importlib.util
import importlib.metadata
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace
from types import ModuleType

TASK=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location("combined_model_adapter",TASK/"combined_model_adapter/__init__.py")
plugin=importlib.util.module_from_spec(spec)
spec.loader.exec_module(plugin)

class Tensor:
    def __init__(self,shape,data,ptr):
        self.shape=shape;self.data=data;self.ptr=ptr
        self.dtype="bf16";self.device=SimpleNamespace(type="cuda")
    def data_ptr(self):return self.ptr
    def clone(self):return Tensor(self.shape,self.data,self.ptr+1)
    def storage_offset(self):return 0
    def numel(self):
        result=1
        for dim in self.shape:result*=dim
        return result
    def element_size(self):return 2
    def untyped_storage(self):
        return SimpleNamespace(nbytes=lambda:self.numel()*2,data_ptr=lambda:self.ptr)

class Torch:
    Tensor=Tensor
    bfloat16="bf16"
    cuda=SimpleNamespace(current_stream=lambda:SimpleNamespace(cuda_stream=77,synchronize=lambda:None))

def check_manifest():
    # Import/register stays inert without torch or vLLM.
    os.environ.pop("HBFSIM_COMBINED_MODEL_PLUGIN_V1",None)
    assert plugin.register() is None
    entries=[ep for dist in importlib.metadata.distributions(path=[str(TASK)])
             for ep in dist.entry_points if ep.group=="vllm.general_plugins"]
    assert len(entries)==1 and entries[0].name=="hbfsim_combined_model_adapter"
    assert entries[0].load()() is None
    for scope,count in (("mixed3",3),("remaining45",45),("added49",49)):
        path=TASK/"prepared-v2"/f"scope-{scope}.json"
        os.environ["HBFSIM_COMBINED_SCOPE_MANIFEST"]=str(path)
        os.environ["HBFSIM_COMBINED_SCOPE_SHA256"]=hashlib.sha256(path.read_bytes()).hexdigest()
        data=plugin._manifest()
        assert len(data["targets"])==count
    bad=json.loads((TASK/"prepared-v2/scope-mixed3.json").read_text())
    bad["targets"][1]=dict(bad["targets"][0])
    malformed=TASK/"cpu-fixture-duplicate.json"
    malformed.write_text(json.dumps(bad))
    os.environ["HBFSIM_COMBINED_SCOPE_MANIFEST"]=str(malformed)
    os.environ["HBFSIM_COMBINED_SCOPE_SHA256"]=hashlib.sha256(malformed.read_bytes()).hexdigest()
    try:plugin._manifest()
    except RuntimeError:pass
    else:raise AssertionError("duplicate alias accepted")

def selected_fixture():
    row=next(x for x in json.loads((TASK/"prepared-v2/scope-mixed3.json").read_text())["targets"]
             if x["category"]=="qkv_proj")
    weight=Tensor(tuple(row["shape"]),b"",10000)
    activation=Tensor((1,2048),b"A"*4096,20000)
    state={"request_id":"req-1","next_order":1,"head_pending":False,"decode_tid":123}
    item={"row":row,"weight":weight,"ptr":10000,"done":False}
    plugin._tls.model=state
    events=[]; saved={};calls=[]
    plugin._emit=lambda event,**fields:events.append((event,fields))
    plugin._write_new=lambda name,data:saved.setdefault(name,data)
    plugin._raw=lambda tensor,torch:tensor.data
    plugin._agent=lambda:(lambda base,size:calls.append(("select",base,size)) or 0,
                         lambda:calls.append(("end",)) or 0)
    outputs=[(Tensor((1,6144),b"X"*12288,30000),None),
             (Tensor((1,6144),b"X"*12288,40000),None)]
    def run(value):return outputs.pop(0)
    result=plugin._selected_call(state,item,activation,run,Torch)
    assert result[0].ptr==40000 and item["done"]
    assert [x[0] for x in calls]==["select","end"]
    assert [x[0] for x in events].index("native_output_saved") < [x[0] for x in events].index("select_enter")
    assert next(f for e,f in events if e=="selected_output")["status"]=="BYTE_EQUAL"
    item["done"]=False
    different=[(Tensor((1,6144),b"X"*12288,31000),None),
               (Tensor((1,6144),b"X"*12288,41000),None)]
    different[1][0].device=SimpleNamespace(type="cuda",index=1)
    try:plugin._selected_call(state,item,activation,lambda value:different.pop(0),Torch)
    except RuntimeError as exc:assert "devices differ" in str(exc)
    else:raise AssertionError("different output devices accepted")
    item["done"]=True
    try:plugin._selected_call(state,item,activation,run,Torch)
    except RuntimeError:pass
    else:raise AssertionError("duplicate selection accepted")
    item["done"]=False;calls.clear();events.clear()
    def failing(value):
        if value.ptr!=activation.ptr:return (Tensor((1,6144),b"Y"*12288,50000),None)
        raise ValueError("candidate failure marker")
    try:plugin._selected_call(state,item,activation,failing,Torch)
    except ValueError as exc:assert str(exc)=="candidate failure marker"
    else:raise AssertionError("candidate failure swallowed")
    assert [x[0] for x in calls]==["select","end"]
    assert any(e=="candidate_exception" for e,_ in events)
    item["done"]=False;events.clear()
    def end_failure():raise OSError("end failure marker")
    plugin._agent=lambda:(lambda base,size:0,end_failure)
    try:plugin._selected_call(state,item,activation,failing,Torch)
    except ValueError as exc:
        assert str(exc)=="candidate failure marker"
        assert any("end failure marker" in note for note in getattr(exc,"__notes__",[]))
    else:raise AssertionError("end failure masked candidate failure")
    assert [x[0] for x in events].index("candidate_exception") < [x[0] for x in events].index("end_exception")
    headrow=next(x for x in json.loads((TASK/"prepared-v2/scope-added49.json").read_text())["targets"]
                 if x["category"]=="lm_head")
    headitem={"row":headrow,"weight":Tensor(tuple(headrow["shape"]),b"",60000),
              "ptr":60000,"done":False}
    plugin._tls.model=None
    state["head_pending"]=True;state["decode_tid"]=-1
    try:plugin._selected_call(state,headitem,activation,lambda value:None,Torch)
    except RuntimeError:pass
    else:raise AssertionError("head request/thread mismatch accepted")

def runner_fixture():
    import threading
    loader=ModuleType("hbfsim_loader")
    loader.register=lambda:None
    class Loader:
        def load_model(self,*args,**kwargs):return self.model
    loader.HbfSimModelLoader=Loader
    sys.modules["hbfsim_loader"]=loader
    for name in ("vllm","vllm.v1","vllm.v1.worker"):
        sys.modules[name]=ModuleType(name)
    runner_mod=ModuleType("vllm.v1.worker.gpu_model_runner")
    class Runner:
        def __init__(self,model):
            self.model=model;self.input_batch=SimpleNamespace(num_reqs=1,req_ids=["req-1"],is_spec_decode=False)
            self.vllm_config=SimpleNamespace(speculative_config=None)
            self.call_head=True
        def _model_forward(self,*args,**kwargs):
            pos=kwargs.get("positions")
            if pos==[2] and self.mark_forward:
                for x in plugin._models[id(self.model)]["items"].values():
                    if x["row"]["category"]!="lm_head":x["done"]=True
            return "forward"
        def execute_model(self,*args,**kwargs):
            result=self._model_forward(*args,**kwargs)
            if self.call_head:self.model.compute_logits(None)
            return result
    runner_mod.GPUModelRunner=Runner
    sys.modules[runner_mod.__name__]=runner_mod
    sys.modules["torch"]=Torch
    plugin._installed=False;plugin._models.clear()
    plugin._manifest=lambda:json.loads((TASK/"prepared-v2/scope-added49.json").read_text())
    plugin._positions=lambda ids,pos,torch:pos
    plugin._emit=lambda event,**fields:None
    os.environ["HBFSIM_COMBINED_MODEL_PLUGIN_V1"]="1"
    for key in ("HBFSIM_QKV_MODEL_PLUGIN_V1","HBFSIM_LI6_MODEL_PLUGIN_V1","HBFSIM_ROUTER_MODEL_PLUGIN_V1"):
        os.environ.pop(key,None)
    plugin.register()
    assert plugin._installed
    def scenario(mark_forward,call_head,request="req-1"):
        model=SimpleNamespace()
        state={"model":model,"scope":"added49","items":{
            "q": {"row":{"category":"qkv_proj"},"done":False},
            "h": {"row":{"category":"lm_head"},"done":False}},
            "prefill":False,"request_id":None,"decode":False,"decode_tid":None,
            "head_pending":False,"complete":False,"next_order":1}
        def compute_logits(value):
            state["items"]["h"]["done"]=True
            state["head_pending"]=False
            return "logits"
        model.compute_logits=compute_logits
        plugin._models.clear();plugin._models[id(model)]=state
        runner=Runner(model);runner.mark_forward=mark_forward;runner.call_head=call_head
        runner._model_forward(input_ids="x",positions=[0,1])
        runner.input_batch.req_ids=[request]
        return runner,state
    good,state=scenario(True,True)
    assert good.execute_model(input_ids="x",positions=[2])=="forward" and state["complete"]
    no_forward,_=scenario(False,True)
    try:no_forward.execute_model(input_ids="x",positions=[2])
    except RuntimeError:pass
    else:raise AssertionError("missing forward child accepted")
    no_head,_=scenario(True,False)
    try:no_head.execute_model(input_ids="x",positions=[2])
    except RuntimeError:pass
    else:raise AssertionError("missing post-forward head accepted")
    mismatch,_=scenario(True,True,"other-request")
    try:mismatch.execute_model(input_ids="x",positions=[2])
    except RuntimeError:pass
    else:raise AssertionError("wrong request accepted")
    os.environ.pop("HBFSIM_COMBINED_MODEL_PLUGIN_V1",None)

def main():
    check_manifest();selected_fixture();runner_fixture()
    (TASK/"CPU_CONTRACT_RESULT.json").write_text(json.dumps({
        "schema":"hbfsim.combined_model_cpu_contract.v1","status":"PASS_NO_GPU",
        "scope_counts":[3,45,49],
        "cases":["default_off_import_without_torch_vllm","entrypoint_discovery_default_off",
                 "manifest_count_and_duplicate",
                 "native_early_save_select_candidate_end","candidate_exception_end",
                 "native_candidate_device_mismatch",
                 "dual_candidate_and_end_failure_keeps_candidate",
                 "duplicate_child","head_thread_mismatch","missing_forward_child",
                 "head_postforward_completion","missing_head","wrong_request"]},indent=2)+"\n")

if __name__=="__main__":main()
