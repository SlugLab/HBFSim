"""Root-only SOURCE preparer/finite CPU runner. Never CUDA or DSO load."""
from pathlib import Path
import argparse,hashlib,json,subprocess,time,os
HERE=Path(__file__).resolve().parent.parent

def need(v,m):
 if not v:raise ValueError(m)
def pin(r,raw=False):
 p=Path(r['path']);need(p.is_absolute()and p.resolve(strict=True)==p and not p.is_symlink()and p.is_file()and p.stat().st_size<2097152,'canonical bounded input');b=p.read_bytes();need(hashlib.sha256(b).hexdigest()==r['sha256']and('bytes'not in r or len(b)==r['bytes']),'input pin');return b if raw else json.loads(b)
def extract(s,begin,end):
 need(s.count(begin)==1,'one extraction start');a=s.index(begin);return s[a:s.index(end,a)]
def main():
 p=argparse.ArgumentParser();p.add_argument('--inputs',required=True);p.add_argument('--inputs-sha256',required=True);p.add_argument('--output-root',required=True);p.add_argument('--execute',action='store_true');a=p.parse_args();q=pin(dict(path=a.inputs,sha256=a.inputs_sha256));mf=pin(q['source_manifest']);review=pin(q['source_review'])
 need(q['source_manifest']['path']==str(HERE/'SOURCE_MANIFEST.json')and review['schema']=='hbfsim.independent.binding_auth.ptx_input_cache_source_review.v1'and review['status']=='SOURCE_ACCEPTED_FOR_NEW_TARGETED_ADAPTER_CPU_NOT_BUILD_OR_RUNTIME'and review['source_manifest']==q['source_manifest']and review['blocking_findings']==[],'genuine source review')
 for r in mf['files']:
  rel=Path(r['relative']);need(not rel.is_absolute()and '..'not in rel.parts,'safe source member');b=(HERE/rel).read_bytes();need(len(b)==r['bytes']and hashlib.sha256(b).hexdigest()==r['sha256'],'exact source member')
 source=(HERE/'source/nv_attach_impl.cpp').read_text();parent=pin(mf['parent_core_source'],True).decode()
 bind_begin='int nv_attach_impl::bind_ptx_variant(';bind_end='\nint nv_attach_impl::native_binding_candidate('
 auto_begin='int nv_attach_impl::auto_bind_registered_function(';auto_end='\nextern "C" __attribute__((visibility("default")))\nint bpftime_nv_bind_ptx_variant'
 current=extract(source,bind_begin,bind_end)+extract(source,auto_begin,auto_end)
 original=(extract(parent,bind_begin,bind_end)+extract(parent,auto_begin,auto_end)).replace('nv_attach_impl::','original_nv_attach_impl::')
 # Reverse the precisely recorded source insertion, not a copied algorithm test.
 delta=json.loads((HERE/'PATCH_OPERATIONS.json').read_text());inverse=source.replace(delta['ON_block'],'',1).replace(delta['new_digest'],delta['old_digest'],1).replace(delta['include'],'',1);need(inverse==parent,'full original parent inverse bytes')
 us=pin(q['sha_source'],True).decode();start=us.index('std::string sha256(const void *data, size_t length)');end=us.index('\n}\n',start)+2;sha=us[start:end].replace('std::string sha256(','std::string actual_original_sha256(',1)
 key=extract(source,'static std::string ptx_variant_key(','\nvoid nv_attach_impl::record_patched_kernel_variant(')
 reader=extract(source,'\tauto read_bytes = []','\n\ttry {')
 scaffold=(HERE/'CPU/scaffold.hpp').read_text();generated=scaffold.replace('/*ACTUAL_SHA*/',sha).replace('/*ACTUAL_KEY*/',key).replace('/*ACTUAL_FUNCTIONS*/',current+'\n'+original+'\nstatic auto actual_reader(){\n'+reader+'\nreturn read_bytes;}\n')
 out=Path(a.output_root);need(out.is_absolute()and out.parent.resolve(strict=True)==out.parent and not out.exists()and not out.is_symlink(),'fresh bounded CPU output')
 prefix=['taskset','-c','6','numactl','--membind=1','nice','-n','19'];need(q['resource_prefix']==prefix,'fixed serial CPU prefix')
 cmd=prefix+[q['compiler'],'-std=c++20','-O2','-g0','-fmax-errors=8','-pthread','-rdynamic','-DSPDLOG_HEADER_ONLY','-DFMT_HEADER_ONLY','-I'+str(HERE/'source'),'-I'+str(out),'-I'+q['json_include'],'-I'+q['spdlog_include'],str(HERE/'CPU/harness.cpp'),'-lcrypto','-ldl','-o',str(out/'harness')]
 if not a.execute:print(json.dumps(dict(status='PLAN_ONLY_NOT_COMPILED',compile_argv=cmd,run_argv=prefix+[str(out/'harness'),str(out/'scratch')])));return
 need(q['root_resource_admitted']is True,'root actual resource gate, not SOURCE approval');out.mkdir();(out/'actual_extracted.hpp').write_text(generated);records=[];env=dict(os.environ,OMP_NUM_THREADS='1',OPENBLAS_NUM_THREADS='1',MKL_NUM_THREADS='1')
 for name,argv in [('compile',cmd),('fixed',prefix+[str(out/'harness'),str(out/'scratch')])]:
  begin=time.monotonic()
  try:r=subprocess.run(argv,env=env,capture_output=True,timeout=300);rc=r.returncode;stdout=r.stdout;stderr=r.stderr;timeout=False
  except subprocess.TimeoutExpired as e:rc=None;stdout=e.stdout or b'';stderr=e.stderr or b'';timeout=True
  (out/(name+'.stdout')).write_bytes(stdout);(out/(name+'.stderr')).write_bytes(stderr);records.append(dict(phase=name,argv=argv,returncode=rc,timeout=timeout,wall_s=time.monotonic()-begin));(out/'EXECUTION.json').write_text(json.dumps(dict(schema='hbfsim.binding_auth.ptx_input_cache_actual_CPU_execution.v1',scope='SYNTHETIC_CUDA_HANDLES_ACTUAL_ADAPTER_NOT_GPU_OR_PERFORMANCE',records=records,source_manifest=q['source_manifest'],source_review=q['source_review']),indent=2)+'\n');need(rc==0 and not timeout,name+' failed; preserve, no retry')
if __name__=='__main__':main()
