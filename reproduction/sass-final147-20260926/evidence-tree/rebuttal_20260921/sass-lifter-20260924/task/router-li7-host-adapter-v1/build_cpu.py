from pathlib import Path
import hashlib,json,os,resource,shutil,subprocess,time
ROOT=Path('/root/hbfsim-exp/rebuttal_20260921'); TASK=Path(__file__).parent
BASE=ROOT/'native-supported-partial-exact-agent-build-v1/build'
LI6=TASK.parent/'new-category-li6-adapter-v1/build-agent-v1'
def sha(p):
 h=hashlib.sha256(); h.update(p.read_bytes()); return h.hexdigest()
def limit(): os.sched_setaffinity(0,{47}); resource.setrlimit(resource.RLIMIT_AS,(2*1024**3,2*1024**3))
def run(out,name,argv,cwd=BASE):
 (out/f'{name}.argv.json').write_text(json.dumps(argv,indent=2)+'\n')
 with (out/f'{name}.stdout').open('wb') as a,(out/f'{name}.stderr').open('wb') as b:
  p=subprocess.run(argv,cwd=cwd,stdout=a,stderr=b,timeout=600,preexec_fn=limit,env={**os.environ,'CUDA_VISIBLE_DEVICES':''})
 (out/f'{name}.rc').write_text(str(p.returncode)+'\n')
 if p.returncode: raise RuntimeError(f'{name} rc={p.returncode}')
def transformed(path,repls):
 a=json.loads(path.read_text()); return [repls.get(x,x) for x in a]
def main():
 started=time.time(); receipt={'schema':'hbfsim.li7_host_cpu_build.v1','status':'STARTED','gpu_used':False}
 ao=TASK/'build-agent-v2'; po=TASK/'build-provider-v2'; go=TASK/'build-gate-v2'
 for x in (ao,po,go):
  if x.exists(): raise RuntimeError(f'existing {x}')
  x.mkdir()
 try:
  archive=ao/'libbpftime_nv_attach_impl.a'; shutil.copyfile(LI6/'libbpftime_nv_attach_impl.a',archive)
  old=str(LI6); sources=[('agent_impl_compile','nv_attach_impl.cpp.o',TASK/'source/nv_attach_impl_router_scoped.cpp'),('agent_setup_compile','nv_attach_impl_frida_setup.cpp.o',TASK/'source/nv_attach_impl_frida_setup.cpp')]
  for label,obj,src in sources:
   av=json.loads((LI6/f'{label}.argv.json').read_text())
   av=[str(src) if x.endswith(('nv_attach_impl_qkv_scoped.cpp','new-category-li6-adapter-v1/nv_attach_impl_frida_setup.cpp')) else x.replace(old,str(ao)) for x in av]
   run(ao,label,av); run(ao,label.replace('compile','archive'),['/usr/bin/x86_64-linux-gnu-ar','r',str(archive),str(ao/obj)])
  run(ao,'agent_ranlib',['/usr/bin/x86_64-linux-gnu-ranlib',str(archive)])
  av=json.loads((LI6/'agent_link.argv.json').read_text()); av=[x.replace(old,str(ao)).replace(str(TASK.parent/'qkv-real-model-agent-abi-v2/agent.version.model'),str(TASK/'source/agent.version.model')) for x in av]
  run(ao,'agent_link',av); run(ao,'defined_symbols',['/usr/bin/x86_64-linux-gnu-nm','-D','--defined-only',str(ao/'libbpftime-agent.so')]); run(ao,'dynamic_resolution',['/usr/bin/ldd','-r',str(ao/'libbpftime-agent.so')])
  oldp=TASK.parent/'qkv-model-dispatch-repair-v1/build-provider-v1/provider_compile.argv.json'; av=json.loads(oldp.read_text()); av=[str(TASK/'source/provider_router_exact.cpp') if x.endswith('provider_qkv_exact.cpp') else str(TASK/'source/library_identity_core.cpp') if x.endswith('library_identity_core.cpp') else f'-I{TASK}/source' if x==f'-I{TASK.parent}/qkv-model-dispatch-repair-v1' else x for x in av]; av[av.index('-o')+1]=str(po/'libprovider_router.so'); run(po,'provider_compile',av,TASK); run(po,'defined_symbols',['/usr/bin/x86_64-linux-gnu-nm','-D','--defined-only',str(po/'libprovider_router.so')],TASK); run(po,'dynamic_resolution',['/usr/bin/ldd','-r',str(po/'libprovider_router.so')],TASK)
  inc13=ROOT/'env-restore-v1/toolchain-download-v1/root/usr/local/cuda-13.0/targets/x86_64-linux/include'; av=['/usr/bin/g++-15','-DHBFSIM_ENABLE_TEST_HOOKS=1','-Dhbfsim_launch_gate_EXPORTS','-I',str(inc13),'-I',str(inc13/'cccl'),'-I',str(ROOT/'native-unbound-handshake-gate-source-v3/third_party/bpftime/third_party'),'-I',str(ROOT/'native-unbound-handshake-gate-source-v3/include'),'-I',str(ROOT/'native-moe-align-scoped-unbound-source-v2/src/cuda_runtime'),'-O3','-DNDEBUG','-std=gnu++20','-fPIC','-c',str(TASK/'source/launch_gate.cpp'),'-o',str(go/'launch_gate.cpp.o')]; run(go,'gate_compile',av,TASK)
  av=['/usr/bin/g++-15','-fPIC','-O3','-DNDEBUG','-shared','-Wl,-soname,libhbfsim_launch_gate.so',f'-Wl,--version-script={ROOT}/first-fault-build-v3/cudart-versions.map','-o',str(go/'libhbfsim_launch_gate.so'),str(go/'launch_gate.cpp.o'),str(ROOT/'native-unbound-policy-build-v3/libhbfsim_core.a'),'-ldl','/usr/lib/x86_64-linux-gnu/libcrypto.so','/usr/lib/x86_64-linux-gnu/libcudart.so','/usr/lib/x86_64-linux-gnu/libcuda.so','-lrt',str(ROOT/'native-unbound-policy-build-v3/libmqsim_hbf.a')]; run(go,'gate_link',av,TASK); run(go,'defined_symbols',['nm','-D','--defined-only',str(go/'libhbfsim_launch_gate.so')],TASK); run(go,'dynamic_resolution',['ldd','-r',str(go/'libhbfsim_launch_gate.so')],TASK)
  receipt['products']={str(p.relative_to(TASK)):{'sha256':sha(p),'bytes':p.stat().st_size} for p in (ao/'libbpftime_nv_attach_impl.a',ao/'libbpftime-agent.so',po/'libprovider_router.so',go/'libhbfsim_launch_gate.so')}; receipt['status']='CPU_BUILD_PASS_NO_GPU'
 except Exception as e: receipt['status']='CPU_FAILED'; receipt['error']=f'{type(e).__name__}: {e}'; raise
 finally: receipt['elapsed_seconds']=time.time()-started; (TASK/'CPU_BUILD_RECEIPT_V2.json').write_text(json.dumps(receipt,indent=2,sort_keys=True)+'\n')
if __name__=='__main__': main()
