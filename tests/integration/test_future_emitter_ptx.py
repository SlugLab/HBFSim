"""Assemble actual C6.1 emitted text against optimized C5 helpers; no GPU."""
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile

ROOT=Path(__file__).resolve().parents[2]
driver,helper,nvcc=map(Path,sys.argv[1:4]);sys.argv=[sys.argv[0],str(driver)]
spec=importlib.util.spec_from_file_location('emitter_cpu',Path(__file__).with_name('test_future_emitter.py'))
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
helper=helper.read_text()
# Compiled store guard must contain range traversal, full-span comparisons,
# finite width dispatch, bound checks, and current binding/liveness loads.
guard=re.search(r'\.visible\s+\.func[^{};]*__hbfsim_timing_future_native_store_guard_v1[\s\S]*?\n\}',helper)
if not guard:guard=re.search(r'\.func[^{};]*__hbfsim_timing_future_native_store_guard_v1[\s\S]*?\n\}',helper)
assert guard,'missing real compiled store guard'
body=guard[0]
assert 'ld.acquire.sys' in body and '%globaltimer' in body and '32768' in body
assert body.count('setp.')>=10 and re.search(r'\badd\.(?:s|u)64',body),'full span/range comparison implementation'
with tempfile.TemporaryDirectory(prefix='.c6-assembly-',dir=ROOT) as directory:
    base=Path(directory);commands=[]
    def run(argv):
        r=subprocess.run([str(x) for x in argv],capture_output=True,text=True,timeout=30)
        commands.append(dict(argv=[str(x) for x in argv],exit_code=r.returncode,stdout=r.stdout,stderr=r.stderr))
        if r.returncode:raise AssertionError(r.stdout+r.stderr)
        return r.stdout
    try:
        types=[(c+str(w),max(16,w)) for c in 'usb' for w in (8,16,32,64)]+[('f32',32),('f64',64)]
        types += [('s8',64),('s16',32),('s32',64)]
        fixtures=[]
        for typ,width in types:
            tag=typ+'-reg'+str(width)
            regtype=typ if typ.startswith('f') else 'b'+str(width)
            # Runtime-mutable predicates; native output prevents dead-code
            # elimination of the original value through the wait-result.
            text=m.program(f'setp.eq.u32 %p0,%r0,0; mov.b{width} %v,0; @%p0 ld.global.{typ} %v,[%rd4]; setp.ne.u32 %p0,%r0,0; mov.b{width} %got,%v; st.global.b{width} [%rd1],%got;',f'.reg .{regtype} %v; .reg .b{width} %got;')
            fixtures.append((tag,text))
        for tag,operations in [
            ('two-independent','ld.global.u32 %r1,[%rd4]; ld.global.u32 %r3,[%rd4]; add.u32 %r2,%r1,%r3; st.global.u32 [%rd1],%r2;'),
            ('overwrite','ld.global.u32 %r1,[%rd4]; @%p0 mov.u32 %r1,9; ld.global.u32 %r1,[%rd4]; st.global.u32 [%rd1],%r1;'),
            ('false-first-consumer','ld.global.u32 %r1,[%rd4]; @%p0 add.u32 %r3,%r1,1; @!%p0 add.u32 %r4,%r1,2; add.u32 %r2,%r1,3; st.global.u32 [%rd1],%r2;'),
            ('conditional-fence','ld.global.u32 %r1,[%rd4]; @%p0 fence.acq_rel.gpu; @!%p0 st.global.u32 [%rd1],%r1; add.u32 %r2,%r1,1;'),
            ('setup-arithmetic','shl.b32 %r3,%r0,2; cvt.u64.u32 %rd5,%r3; mad.wide.u32 %rd6,%r0,4,%rd0; ld.global.u32 %r1,[%rd6]; st.global.u32 [%rd1],%r1;')]:
            fixtures.append((tag,m.program('setp.eq.u32 %p0,%r0,0; '+operations)))
        simple=m.program('ld.global.u32 %r1,[%rd4]; st.global.u32 [%rd1],%r1;')
        fixtures.insert(0,('header-comment','// PTX header uses .address_size 64 below\n'+simple))
        fixtures.insert(0,('header-trailing-block',simple.replace('.address_size 64','.address_size 64 /* header comment\ncontinued */')))
        fixtures.append(('header-block-comment','/* .address_size 64 is described here */\n'+simple))
        for geometry in ('reqntid 32,1,1','maxntid 64,1,1'):
            fixtures.append((geometry.split()[0],simple.replace('output) {','output) .'+geometry+' {')))
        fixtures.append(('predicated-second-producer',m.program('setp.eq.u32 %p0,%r0,0; ld.global.u32 %r1,[%rd4]; @%p0 ld.global.u32 %r1,[%rd4+64]; st.global.u32 [%rd1],%r1;')))
        fixtures.append(('quoted-metadata-comment',simple.replace('.address_size 64','.address_size 64\n.file 1 \"quoted.metadata.ptx\"').replace('ld.global.u32 %r1','ld.global.u32 /* \"preserved comment\" */ %r1')))
        for tag,text in fixtures:
            source=base/(tag+'.input.ptx');source.write_text(text)
            if tag.startswith('header-'):
                run([nvcc.with_name('ptxas'),'-c','-O3','-arch=sm_120',source,'-o',base/(tag+'.original.cubin')])
            emitted=run([driver,source,16,32]);(base/(tag+'.emitted.ptx')).write_text(emitted)
            # Assemble unmodified emitter output in relocatable mode as well:
            # stripping externs for helper linking must not hide a bad header.
            run([nvcc.with_name('ptxas'),'-c','-O3','-arch=sm_120',base/(tag+'.emitted.ptx'),'-o',base/(tag+'.object.cubin')])
            body=re.sub(r'(?m)^\s*\.(?:version|target|address_size)[^\n]*\n','',helper)
            emitted=re.sub(r'\.extern\s+\.func[\s\S]*?;\n','',emitted)
            header=re.search(r'(?m)^\s*\.address_size[^\n]*\n',emitted);assert header
            linked=emitted[:header.end()]+body+'\n'+emitted[header.end():]
            path=base/(tag+'.linked.ptx');path.write_text(linked)
            run([nvcc.with_name('ptxas'),'-O3','-arch=sm_120',path,'-o',base/(tag+'.cubin')])
    finally:
        (base/'commands.json').write_text(json.dumps(commands,indent=2))
        if os.environ.get('HBFSIM_C6_COMPILE_ARTIFACTS'):
            out=Path(os.environ['HBFSIM_C6_COMPILE_ARTIFACTS']).resolve()
            assert out.is_relative_to(ROOT/'results/gold/timing-future-unit')
            shutil.copytree(base,out)
print(json.dumps(dict(scope='COMPILE_ONLY_NO_GPU',typed_forms=len(types),assembled_fixtures=len(fixtures),public_admission=False,native_consumer_sass_gold='NOT_RUN_C6_3')))
