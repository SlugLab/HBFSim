"""CPU fake-driver exercise of the actual C5 gate/load transactions."""
import ctypes as c
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from test_timing_gate_binding import GateApiV3, canonical_ptx

class Caps(c.Structure):
    _fields_=[('abi_version',c.c_uint32),('struct_bytes',c.c_uint32),('bits',c.c_uint64)]
ActivateCaps=c.CFUNCTYPE(c.c_int,c.c_size_t,c.c_size_t,c.c_size_t,c.c_int,c.POINTER(Caps),c.POINTER(c.c_uint64))
class GateApiV4(c.Structure):
    _fields_=GateApiV3._fields_+[('activate_with_capabilities',ActivateCaps)]

def main():
    gate=Path(sys.argv[1]).resolve();fake=Path(sys.argv[2]).resolve()
    if len(sys.argv)==3:
        root=Path(__file__).resolve().parents[2]
        with tempfile.TemporaryDirectory(prefix='.timing-future-loader-',dir=root) as directory:
            for scenario in ('valid','before','wrong-token','lookup-error','missing-config','short-config','wrong-helper','rollback','clear-failure','quarantine-reload','untrusted','wrong-identity','unloaded-graph'):
                env=os.environ.copy();env['LD_PRELOAD']=f'{gate}:{fake}'
                env['LD_LIBRARY_PATH']=str(fake.parent)
                env['HBFSIM_PASS_MANIFEST_PATH']=str(Path(directory)/(scenario+'.manifest'))
                env['HBFSIM_COVERAGE_PATH']=str(Path(directory)/(scenario+'.coverage'))
                r=subprocess.run([sys.executable,__file__,str(gate),str(fake),scenario],env=env,timeout=5)
                assert r.returncode==0,scenario
        print('PASS: C5 version, no-HBF admission and loader rollback scenarios (CPU fake driver)')
        return
    scenario=sys.argv[3];p=c.CDLL(None);f=c.CDLL(str(fake))
    getter=p.hbfsim_launch_gate_get_api;getter.argtypes=[c.c_uint32];getter.restype=c.POINTER(GateApiV4)
    ptr=getter(4);assert ptr,'v4 capability API absent';api=ptr.contents
    assert api.abi_version==4 and api.struct_bytes==c.sizeof(GateApiV4)
    identity=bytes([0x72])+bytes(31)
    f.fakeCudaSetModuleIdentity.argtypes=[c.c_void_p,c.c_size_t]
    data=(c.c_ubyte*32).from_buffer_copy(identity);assert f.fakeCudaSetModuleIdentity(data,32)==0
    mode={'wrong-token':2,'missing-config':3,'short-config':4,'wrong-helper':5,'lookup-error':6}.get(scenario,1)
    f.fakeCudaSetFutureContract(mode)
    caps=Caps(1,16,1);gen=c.c_uint64()
    if scenario!='before':assert api.activate_with_capabilities(10,0x9000,0xCA00,3,c.byref(caps),c.byref(gen))==0
    if scenario=='rollback':f.fakeCudaSetControlCopyFailurePosition(2)
    if scenario in ('clear-failure','quarantine-reload'):f.fakeCudaSetControlCopyFailure(1)
    begin=p.hbfsim_begin_module_load_from_ptx;begin.argtypes=[c.c_char_p,c.c_size_t];begin.restype=c.c_uint64
    source=canonical_ptx(identity);ticket=0 if scenario=='untrusted' else begin(source,len(source))
    if scenario!='untrusted':assert ticket
    if scenario=='wrong-identity':
        data[0]=0x73;assert f.fakeCudaSetModuleIdentity(data,32)==0
    load=p.cuModuleLoadDataEx;load.argtypes=[c.POINTER(c.c_void_p),c.c_void_p,c.c_uint,c.c_void_p,c.c_void_p];load.restype=c.c_int
    module=c.c_void_p();code=load(c.byref(module),c.c_char_p(source),0,None,None)
    p.hbfsim_end_module_load.argtypes=[c.c_uint64];p.hbfsim_end_module_load(ticket)
    if scenario in ('untrusted','wrong-identity'):
        assert code!=0,'untrusted explicit future loaded without classification'
        return
    if scenario=='unloaded-graph':
        unload=p.cuModuleUnload;unload.argtypes=[c.c_void_p];unload.restype=c.c_int
        assert unload(module)==0
        graph=p.cuGraphLaunch;graph.argtypes=[c.c_void_p,c.c_void_p];graph.restype=c.c_int
        assert graph(c.c_void_p(0x4444),None)!=0,'opaque graph risk lost after unload'
        assert f.fakeCudaLaunchCount()==0
        return
    if scenario=='quarantine-reload':
        f.fakeCudaSetControlCopyFailure(0)
        unload=p.cuModuleUnload;unload.argtypes=[c.c_void_p];unload.restype=c.c_int
        assert unload(module)==0
        f.fakeCudaSetFutureContract(1)
        ticket=begin(source,len(source));assert ticket
        code=load(c.byref(module),c.c_char_p(source),0,None,None);p.hbfsim_end_module_load(ticket)
        assert code!=0,'quarantined registration silently loaded an unclassified future'
        return
    if scenario in ('wrong-token','lookup-error'):assert code!=0;return
    if scenario in ('clear-failure','missing-config','short-config'):assert code!=0
    else:assert code==0,code
    if scenario=='before':assert api.activate_with_capabilities(10,0x9000,0xCA00,3,c.byref(caps),c.byref(gen))==0
    # The existing fake driver maps this fixture function to its loaded module.
    function=c.c_void_p(0x1234)
    launch=p.cuLaunchKernel
    launch.argtypes=[c.c_void_p,*([c.c_uint]*7),c.c_void_p,c.c_void_p,c.c_void_p]
    launch.restype=c.c_int
    # No parameters/manifest/ranges: refusal must precede the native fallback.
    extra=(c.c_void_p*1)(None)
    for name,args,types in [
        ('cuLaunch',[function],[c.c_void_p]),
        ('cuLaunchGrid',[function,1,1],[c.c_void_p,c.c_int,c.c_int]),
        ('cuLaunchGridAsync',[function,1,1,None],[c.c_void_p,c.c_int,c.c_int,c.c_void_p]),
        ('cuGraphLaunch',[c.c_void_p(0x4444),None],[c.c_void_p,c.c_void_p]),
        ('cudaGraphLaunch',[c.c_void_p(0x4444),None],[c.c_void_p,c.c_void_p])]:
        call=getattr(p,name);call.argtypes=types;call.restype=c.c_int
        assert call(*args)!=0,name+' bypassed future refusal'
    assert launch(function,1,1,1,32,1,1,0,None,None,extra)!=0,'extra/no-HBF bypassed future refusal'
    assert launch(function,1,1,1,32,1,1,0,None,None,None)!=0
    assert f.fakeCudaLaunchCount()==0
    assert f.fakeCudaFutureEnabled()==0
    if scenario=='clear-failure':
        token=c.c_size_t();assert api.begin_retire(10,gen.value,c.byref(token))!=0
    else:
        token=c.c_size_t();result=api.begin_retire(10,gen.value,c.byref(token))
        # Invalid/missing device contract can quarantine the owned module.
        if result==0:
            assert api.invalidate_retire(token.value)==0
            assert api.finish_retire(token.value)==0

if __name__=='__main__':main()
