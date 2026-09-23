#!/usr/bin/env python3
"""Compile the seven exact staged modules with the new agent's compiler API."""
import argparse
import ctypes
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
import time

def sha(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as f:
        for chunk in iter(lambda:f.read(1<<20),b''): h.update(chunk)
    return h.hexdigest()

def write(path,obj):
    Path(path).write_text(json.dumps(obj,indent=2,sort_keys=True)+'\n')

def rewrite_target(data):
    # Matches nv_attach_impl's rewrite_ptx_target for sm_120: only first
    # .version and .target tokens change; this is compile-time input setup.
    text=data.decode('utf-8')
    version,n=re.subn(r'\.version +[^ \n]+', '.version 8.7', text, count=1)
    assert n==1
    target,n=re.subn(r'\.target[ \t]+[^ \t\r\n,]+', '.target sm_120', version, count=1)
    assert n==1
    return target.encode('utf-8')

def child(spec_path):
    s=json.loads(Path(spec_path).read_text()); out=Path(s['out']); out.mkdir(exist_ok=False)
    original=Path(s['source']).read_bytes()
    assert hashlib.sha256(original).hexdigest()==s['source_sha256']
    fixed=out/'runtime-compile-input.ptx'; fixed.write_bytes(rewrite_target(original))
    lib=ctypes.CDLL(s['compiler'])
    lib.nv_attach_impl_create_compiler.restype=ctypes.c_void_p
    lib.nv_attach_impl_compile.argtypes=[ctypes.c_void_p,ctypes.c_char_p,ctypes.POINTER(ctypes.c_char_p),ctypes.c_int]
    lib.nv_attach_impl_compile.restype=ctypes.c_int
    lib.nv_attach_impl_get_error_log.argtypes=[ctypes.c_void_p]; lib.nv_attach_impl_get_error_log.restype=ctypes.c_char_p
    lib.nv_attach_impl_get_info_log.argtypes=[ctypes.c_void_p]; lib.nv_attach_impl_get_info_log.restype=ctypes.c_char_p
    lib.nv_attach_impl_get_compiled_program.argtypes=[ctypes.c_void_p,ctypes.POINTER(ctypes.c_void_p),ctypes.POINTER(ctypes.c_size_t)]
    lib.nv_attach_impl_destroy_compiler.argtypes=[ctypes.c_void_p]
    options=[b'--gpu-name=sm_120',b'--verbose',('-O'+str(s['opt_level'])).encode(),b'--maxrregcount=64']
    arr=(ctypes.c_char_p*len(options))(*options)
    handle=lib.nv_attach_impl_create_compiler(); assert handle
    try:
        rc=lib.nv_attach_impl_compile(handle,fixed.read_bytes(),arr,len(options))
        (out/'info.log').write_bytes(lib.nv_attach_impl_get_info_log(handle) or b'')
        (out/'error.log').write_bytes(lib.nv_attach_impl_get_error_log(handle) or b'')
        if rc==0:
            ptr,size=ctypes.c_void_p(),ctypes.c_size_t()
            lib.nv_attach_impl_get_compiled_program(handle,ctypes.byref(ptr),ctypes.byref(size))
            (out/'module.cubin').write_bytes(ctypes.string_at(ptr,size.value))
    finally:
        lib.nv_attach_impl_destroy_compiler(handle)
    write(out/'COMPILE_RESULT.json',{'returncode':rc,'options':[x.decode() for x in options],
          'source_sha256':s['source_sha256'],'fixed_sha256':sha(fixed),
          'cubin_sha256':sha(out/'module.cubin') if (out/'module.cubin').exists() else None,
          'cubin_bytes':(out/'module.cubin').stat().st_size if (out/'module.cubin').exists() else None,
          'compiler_sha256':sha(s['compiler'])})
    if rc: raise SystemExit(rc)

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--stage-receipt',type=Path); ap.add_argument('--compiler',type=Path)
    ap.add_argument('--compiler-sha256'); ap.add_argument('--output',type=Path); ap.add_argument('--child',type=Path)
    ap.add_argument('--o0-raw',required=False,help='Exact raw SHA-256 that uses O0; other raws use O3')
    a=ap.parse_args()
    if a.child: child(a.child); return
    assert sha(a.compiler)==a.compiler_sha256
    stages=json.loads(a.stage_receipt.read_text())['stages']
    assert a.o0_raw in stages, 'exact O0 raw not present in stage receipt'
    out=a.output.absolute(); out.mkdir(parents=True,exist_ok=False)
    write(out/'START.json',{'time_unix_ns':time.time_ns(),'stage_receipt_sha256':sha(a.stage_receipt),
          'compiler_sha256':sha(a.compiler),'modules':list(stages)})
    results={}
    for raw,stage in sorted(stages.items()):
        assert sha(stage['path'])==stage['sha256']
        spec={'source':stage['path'],'source_sha256':stage['sha256'],'compiler':str(a.compiler),
              'out':str(out/raw),'opt_level':0 if raw==a.o0_raw else 3}
        write(out/(raw+'.spec.json'),spec)
        with (out/(raw+'.stdout')).open('xb') as stdout,(out/(raw+'.stderr')).open('xb') as stderr:
            cp=subprocess.run([sys.executable,__file__,'--child',str(out/(raw+'.spec.json'))],
                              stdout=stdout,stderr=stderr,timeout=300)
        if cp.returncode: raise RuntimeError(f'{raw} compile failed rc={cp.returncode}')
        results[raw]=json.loads((out/raw/'COMPILE_RESULT.json').read_text())
    write(out/'COMPILE_RECEIPT.json',{'status':'CPU_COMPILED_NOT_GPU_VALIDATED','results':results,
                                    'stage_receipt_sha256':sha(a.stage_receipt),'compiler_sha256':sha(a.compiler)})
    print(json.dumps({'status':'CPU_COMPILED','module_count':len(results)}))

if __name__=='__main__': main()
