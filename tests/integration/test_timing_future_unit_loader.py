"""TEST_ONLY C6.2 actual plugin -> one-shot loader -> fake CUDA launch proof."""
import ctypes as c
import json
import hashlib
import os
import re
from pathlib import Path
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from test_timing_future_loader import Caps, GateApiV4
from test_timing_gate_binding import GateApi

ROOT = Path(__file__).resolve().parents[2]

def build_runtime_fixture(directory):
    """Payload-free TEST_ONLY runtime; each original only records forwarding."""
    source = Path(directory)/'fake-runtime.cpp'
    library = Path(directory)/'fake-runtime.so'
    source.write_text('''#include <cstddef>
struct D { unsigned x,y,z; };
static int count=0;
extern "C" int cudaGetFuncBySymbol(void** function,const void* symbol) {
    if(!function || symbol!=reinterpret_cast<void*>(0xface))return 1;
    *function=reinterpret_cast<void*>(0x1234);return 0;
}
#define RUNTIME(name) extern "C" int name(const void*,D,D,void**,std::size_t,void*) { ++count;return 0; }
RUNTIME(cudaLaunchKernel)
RUNTIME(cudaLaunchKernel_ptsz)
RUNTIME(cudaLaunchCooperativeKernel)
RUNTIME(cudaLaunchCooperativeKernel_ptsz)
RUNTIME(__cudaLaunchKernel)
RUNTIME(__cudaLaunchKernel_ptsz)
extern "C" int fakeRuntimeLaunchCount() { return count; }
''')
    argv = ['c++', '-shared', '-fPIC', str(source), '-o', str(library)]
    run = subprocess.run(argv, capture_output=True, text=True, timeout=30)
    source.with_suffix('.compile.json').write_text(json.dumps(dict(argv=argv, returncode=run.returncode,
                                                                 stdout=run.stdout, stderr=run.stderr), indent=2)+'\n')
    assert run.returncode == 0, run.stderr
    return library


def runtime_case(p, f, scenario):
    class D(c.Structure):
        _fields_ = [('x', c.c_uint), ('y', c.c_uint), ('z', c.c_uint)]
    ordinary = ['cudaLaunchKernel', 'cudaLaunchKernel_ptsz', '__cudaLaunchKernel', '__cudaLaunchKernel_ptsz']
    cooperative = ['cudaLaunchCooperativeKernel', 'cudaLaunchCooperativeKernel_ptsz']
    symbols = ordinary if scenario == 'runtime-ordinary' else cooperative if scenario == 'runtime-sync-cooperative' else [
        cooperative[int(scenario.endswith('-ptsz'))]]
    f.fakeCudaSetKernelFunction.argtypes = [c.c_void_p, c.c_void_p]
    f.fakeCudaSetKernelFunction(c.c_void_p(0x5678), c.c_void_p(0x1234))
    allowed = scenario in ('runtime-ordinary', 'runtime-sync-cooperative')
    for symbol in symbols:
        function = getattr(p, symbol)
        function.argtypes = [c.c_void_p, D, D, c.c_void_p, c.c_size_t, c.c_void_p]
        before = p.fakeRuntimeLaunchCount()
        handle = 0x5678 if symbol.startswith('__') else 0xface
        code = function(c.c_void_p(handle), D(1,1,1), D(32,1,1), None, 0, None)
        count = p.fakeRuntimeLaunchCount()-before
        print(json.dumps(dict(test_only=True, scenario=scenario, symbol=symbol, returncode=code,
                              original_runtime_calls=count)), flush=True)
        assert (code == 0) == allowed and count == int(allowed), 'unexpected original runtime forwarding'
    assert f.fakeCudaLaunchCount() == 0, 'payload-free runtime must not launch a driver kernel'


def image_case(p, f, transform, scenario):
    """Actual immutable image bytes, independent TEST_ONLY module storage."""
    original = '''.version 8.7
.target sm_120
.address_size 64
'''
    for name in ('kernel', 'other'):
        original += f'''.visible .entry {name}(.param .u64 .ptr .global input, .param .u64 .ptr .global output)
{{ .reg .b64 %rd<3>; .reg .b32 %r;
ld.param.u64 %rd1,[input]; ld.param.u64 %rd2,[output];
ld.global.u32 %r,[%rd1]; st.global.u32 [%rd2],%r; ret; }}
'''
    transform.process_input.argtypes = [c.c_char_p, c.c_int, c.c_char_p]
    def emit(text, kernel):
        output = c.create_string_buffer(16*1024*1024)
        request = json.dumps({'input': dict(full_ptx=text, to_patch_kernel=kernel,
                                           transform_mode='timing_load_future_v1')}).encode()
        assert transform.process_input(request, len(output), output) == 0, output.value[:1000]
        return json.loads(output.value)
    manifest_path = Path(os.environ['HBFSIM_PASS_MANIFEST_PATH'])
    a = emit(original, 'kernel')
    manifest = json.loads(manifest_path.read_text().splitlines()[-1])
    f.fakeCudaSetModuleIdentity.argtypes = [c.c_void_p, c.c_size_t]
    assert f.fakeCudaSetModuleIdentity(c.c_char_p(bytes.fromhex(a['transform_identity'])), 32) == 0
    f.fakeCudaSetFutureContract(1)
    f.fakeCudaSetFutureHelperHash.argtypes = [c.c_void_p, c.c_size_t]
    assert f.fakeCudaSetFutureHelperHash(c.c_char_p(bytes.fromhex(manifest['future_contract']['helper_sha256'])), 32) == 0
    f.fakeCudaEnableImageFixtures()
    f.fakeCudaImageEnabled.argtypes = [c.c_void_p]
    f.fakeCudaImageFunction.argtypes = [c.c_void_p, c.c_char_p]
    f.fakeCudaImageFunction.restype = c.c_void_p
    getter = p.hbfsim_launch_gate_get_api; getter.argtypes = [c.c_uint32]; getter.restype = c.POINTER(GateApiV4)
    api = getter(4).contents; caps = Caps(1, 16, 1); generation = c.c_uint64()
    assert api.activate_with_capabilities(10, 0x9000, 0xCA00, 3, c.byref(caps), c.byref(generation)) == 0
    begin = p.hbfsim_begin_module_load_from_ptx; begin.argtypes = [c.c_char_p, c.c_size_t]; begin.restype = c.c_uint64
    end = p.hbfsim_end_module_load; end.argtypes = [c.c_uint64]
    load = p.cuModuleLoadDataEx; load.argtypes = [c.POINTER(c.c_void_p), c.c_void_p, c.c_uint, c.c_void_p, c.c_void_p]
    launch = p.cuLaunchKernel; launch.argtypes = [c.c_void_p, *([c.c_uint]*7), c.c_void_p, c.c_void_p, c.c_void_p]
    def load_image(text, tag):
        manifest_path.with_name(scenario+'-'+tag+'.ptx').write_text(text)
        data = text.encode(); ticket = begin(data, len(data)); assert ticket
        module = c.c_void_p()
        try: code = load(c.byref(module), c.c_char_p(data), 0, None, None)
        finally: end(ticket)
        assert code == 0, (tag, code)
        return module
    def invoke(module, name):
        function = f.fakeCudaImageFunction(module, name.encode()); assert function
        before = f.fakeCudaLaunchCount()
        code = launch(function, 1, 1, 1, 32, 1, 1, 0, None, None, None)
        assert f.fakeCudaLaunchCount() == before + int(code == 0)
        return code
    first = load_image(a['output_ptx'], 'A')
    assert f.fakeCudaImageEnabled(first) == 1
    assert invoke(first, 'kernel') == 0 and invoke(first, 'other') != 0
    b = emit(a['output_ptx'], 'other')
    assert a['transform_identity'] == b['transform_identity']
    assert invoke(first, 'kernel') == 0 and invoke(first, 'other') != 0
    constants = re.compile(r'\.visible \.const \.align 8 \.b8 __hbfsim_timing_future_kernel_[a-f0-9]{64}_v1\[32\] = \{[^}]*\};')
    assert len(constants.findall(a['output_ptx'])) == 1
    assert len(constants.findall(b['output_ptx'])) == 2
    image = b['output_ptx']
    if scenario not in ('image-coexistence', 'image-reload'):
        if scenario == 'image-missing-all': image = constants.sub('', image)
        elif scenario in ('image-mismatch', 'image-short'):
            symbol = '__hbfsim_timing_future_kernel_'+hashlib.sha256(b'other').hexdigest()+'_v1'
            pattern = re.compile(r'(\.b8 '+symbol+r')\[32\] = \{[^}]*\}')
            width = 31 if scenario == 'image-short' else 32
            image, count = pattern.subn(lambda match: match[1]+f'[{width}] = '+'{'+','.join(['0']*width)+'}', image)
            assert count == 1
        elif scenario == 'image-inaccessible': f.fakeCudaImageKernelLookupFailure(999)
        else: raise AssertionError(scenario)
        rejected = load_image(image, 'rejected')
        assert f.fakeCudaImageEnabled(rejected) == 0, 'invalid image was enabled'
        assert invoke(rejected, 'kernel') != 0 and invoke(rejected, 'other') != 0
        return
    second = load_image(image, 'B')
    assert second.value != first.value
    assert f.fakeCudaImageEnabled(second) == 1
    assert invoke(second, 'kernel') == 0 and invoke(second, 'other') == 0
    assert invoke(first, 'kernel') == 0 and invoke(first, 'other') != 0
    if scenario == 'image-coexistence':
        token = c.c_size_t(); assert api.begin_retire(10, generation.value, c.byref(token)) == 0
        assert api.invalidate_retire(token.value) == 0
        assert f.fakeCudaImageEnabled(first) == 0 and f.fakeCudaImageEnabled(second) == 0
        assert api.finish_retire(token.value) == 0
        assert api.activate_with_capabilities(10, 0xA000, 0xCA00, 3, c.byref(caps), c.byref(generation)) == 0
        assert f.fakeCudaImageEnabled(first) == 1, 'historical manifest superset invalidated image A on reactivation'
        assert f.fakeCudaImageEnabled(second) == 1
        assert invoke(first, 'kernel') == 0 and invoke(first, 'other') != 0
    unload = p.cuModuleUnload; unload.argtypes = [c.c_void_p]
    assert unload(first) == 0
    reloaded = load_image(a['output_ptx'], 'A-reloaded')
    assert reloaded.value not in (first.value, second.value)
    assert f.fakeCudaImageEnabled(reloaded) == 1, 'historical manifest superset invalidated image A on reload'
    assert invoke(reloaded, 'kernel') == 0 and invoke(reloaded, 'other') != 0
    assert invoke(second, 'kernel') == 0 and invoke(second, 'other') == 0
    assert unload(reloaded) == 0 and unload(second) == 0

def main():
    gate, fake, plugin = map(lambda x: Path(x).resolve(), sys.argv[1:4])
    if len(sys.argv) == 4:
        cases = ['valid', 'before', 'legacy', 'cross-context', 'stale', 'retire', 'budget',
                 'req-axis', 'max-axis', 'zero-grid', 'overflow-grid', 'block-bound',
                 'missing-manifest', 'swapped-helper', 'conflicting-manifest', 'untrusted',
                 'missing-trace', 'short-trace', 'short-counter', 'unaligned-trace',
                 'wrong-token', 'wrong-helper', 'short-config', 'clear-failure']
        cases += ['tampered-manifest', 'missing-kernel-symbol', 'unload-clear-failure',
                  'legacy-v2', 'legacy-v3', 'enqueue-failure', 'postload-missing-manifest',
                  'missing-helper','short-helper']
        cases += [f'copy-{n}' for n in range(1, 7)]
        cases += ['image-coexistence', 'image-reload', 'image-missing-all', 'image-mismatch', 'image-short', 'image-inaccessible']
        cases += ['runtime-cooperative', 'runtime-cooperative-ptsz', 'runtime-ordinary', 'runtime-sync-cooperative']
        with tempfile.TemporaryDirectory(prefix='.c6-unit-loader-', dir=ROOT) as directory:
            runtime = build_runtime_fixture(directory)
            for scenario in cases:
                env = os.environ.copy()
                env.update(LD_PRELOAD=f'{gate}:{fake}', LD_LIBRARY_PATH=str(fake.parent),
                           HBFSIM_PASS_MANIFEST_PATH=str(Path(directory)/(scenario+'.manifest')),
                           HBFSIM_COVERAGE_PATH=str(Path(directory)/(scenario+'.coverage')))
                if scenario.startswith('runtime-'): env['LD_PRELOAD'] += ':'+str(runtime)
                run = subprocess.run([sys.executable, __file__, str(gate), str(fake), str(plugin), scenario],
                                     env=env, capture_output=True, text=True, timeout=10)
                assert run.returncode == 0, (scenario, run.stdout, run.stderr)
        print(f'PASS: {len(cases)} actual-plugin / fake-driver closure cases; no GPU execution')
        return
    scenario = sys.argv[4]
    p, f, transform = c.CDLL(None), c.CDLL(str(fake)), c.CDLL(str(plugin))
    if scenario.startswith('image-'):
        image_case(p, f, transform, scenario)
        return
    geometry = '.reqntid 8,4,1' if scenario == 'req-axis' else '.maxntid 8,4,1' if scenario == 'max-axis' else ''
    source = f'''.version 8.7
.target sm_120
.address_size 64
.visible .entry kernel(.param .u64 .ptr .global input, .param .u64 .ptr .global output)
{geometry}
{{ .reg .b64 %rd<3>; .reg .b32 %r;
ld.param.u64 %rd1,[input]; ld.param.u64 %rd2,[output];
ld.global.u32 %r,[%rd1]; st.global.u32 [%rd2],%r; ret; }}
'''
    transform.process_input.argtypes = [c.c_char_p, c.c_int, c.c_char_p]
    output = c.create_string_buffer(16*1024*1024)
    synchronous = scenario == 'runtime-sync-cooperative'
    request = json.dumps({'input': {'full_ptx': source, 'to_patch_kernel': 'kernel',
                                  'transform_mode': 'synchronous' if synchronous else 'timing_load_future_v1'}}).encode()
    assert transform.process_input(request, len(output), output) == 0, output.value[:1000]
    result = json.loads(output.value); compiled = result['output_ptx'].encode()
    manifest_path = Path(os.environ['HBFSIM_PASS_MANIFEST_PATH'])
    manifest = json.loads(manifest_path.read_text().splitlines()[-1])
    manifest_hash=hashlib.sha256(json.dumps(manifest,sort_keys=True,separators=(',',':')).encode()).digest()
    if hasattr(f,'fakeCudaSetFutureKernelHash'):
        f.fakeCudaSetFutureKernelHash.argtypes=[c.c_void_p,c.c_size_t]
        assert f.fakeCudaSetFutureKernelHash(c.c_char_p(manifest_hash),32)==0
    if scenario=='tampered-manifest':
        changed=dict(manifest);changed['future_kernel']=dict(manifest['future_kernel'],static_producers=2)
        manifest_path.write_text(json.dumps(changed)+'\n')
    identity = (c.c_ubyte*32).from_buffer_copy(bytes.fromhex(result['transform_identity']))
    f.fakeCudaSetModuleIdentity.argtypes = [c.c_void_p, c.c_size_t]
    assert f.fakeCudaSetModuleIdentity(identity, 32) == 0
    mode = {'wrong-token': 2, 'short-config': 4, 'wrong-helper': 5,
            'missing-trace': 7, 'short-trace': 8, 'short-counter': 9, 'unaligned-trace': 11,
            'missing-kernel-symbol':12,'missing-helper':13,'short-helper':14}.get(scenario, 1)
    f.fakeCudaSetFutureContract(0 if synchronous else mode)
    helper = bytes(32) if synchronous else bytes.fromhex(manifest['future_contract']['helper_sha256'])
    if scenario == 'swapped-helper': helper = bytes(32)
    f.fakeCudaSetFutureHelperHash.argtypes = [c.c_void_p, c.c_size_t]
    assert f.fakeCudaSetFutureHelperHash(c.c_char_p(helper), 32) == 0
    if scenario == 'missing-manifest': manifest_path.unlink()
    if scenario == 'conflicting-manifest':
        bad = dict(manifest); bad['future_kernel'] = dict(manifest['future_kernel'], static_producers=2)
        with manifest_path.open('a') as out: out.write(json.dumps(bad)+'\n')
    getter = p.hbfsim_launch_gate_get_api; getter.argtypes = [c.c_uint32]; getter.restype = c.POINTER(GateApiV4)
    api = getter(4).contents; caps = Caps(1, 16, 0 if scenario == 'legacy' else 1); generation = c.c_uint64()
    if scenario in ('legacy-v2','legacy-v3'):
        version=int(scenario[-1]);legacy=c.cast(getter(version),c.POINTER(GateApi)).contents
        assert legacy.abi_version==version
        assert legacy.activate(10,0x9000,0xCA00,3,c.byref(generation))==0
    elif scenario != 'before':
        assert api.activate_with_capabilities(10, 0x9000, 0xCA00, 3, c.byref(caps), c.byref(generation)) == 0
    if scenario.startswith('copy-'): f.fakeCudaSetControlCopyFailurePosition(int(scenario[5:]))
    if scenario == 'clear-failure': f.fakeCudaSetControlCopyFailure(1)
    begin = p.hbfsim_begin_module_load_from_ptx; begin.argtypes = [c.c_char_p, c.c_size_t]; begin.restype = c.c_uint64
    ticket = 0 if scenario == 'untrusted' else begin(compiled, len(compiled))
    load = p.cuModuleLoadDataEx; load.argtypes = [c.POINTER(c.c_void_p), c.c_void_p, c.c_uint, c.c_void_p, c.c_void_p]; load.restype = c.c_int
    module = c.c_void_p(); code = load(c.byref(module), c.c_char_p(compiled), 0, None, None)
    p.hbfsim_end_module_load.argtypes = [c.c_uint64]; p.hbfsim_end_module_load(ticket)
    if scenario.startswith('runtime-'):
        assert code == 0 and f.fakeCudaFutureEnabled() == int(not synchronous)
        runtime_case(p, f, scenario)
        return
    if scenario == 'untrusted': assert code != 0; return
    if scenario == 'before':
        assert f.fakeCudaFutureEnabled() == 0
        assert api.activate_with_capabilities(10, 0x9000, 0xCA00, 3, c.byref(caps), c.byref(generation)) == 0
    positive = scenario in ('valid', 'before', 'cross-context', 'stale', 'retire', 'budget', 'req-axis', 'max-axis', 'zero-grid', 'overflow-grid', 'block-bound', 'unload-clear-failure', 'enqueue-failure', 'postload-missing-manifest')
    enabled=positive
    assert f.fakeCudaFutureEnabled() == int(enabled), (scenario, code, f.fakeCudaFutureEnabled())
    if positive and hasattr(f,'fakeCudaFutureCopyCount'):
        events=[f.fakeCudaFutureCopyEvent(i) for i in range(f.fakeCudaFutureCopyCount())]
        assert events[-6:]==[1,2,3,4,5,6], events
    launch = p.cuLaunchKernel; launch.argtypes = [c.c_void_p, *([c.c_uint]*7), c.c_void_p, c.c_void_p, c.c_void_p]; launch.restype = c.c_int
    function = c.c_void_p(0x1234)
    def invoke(grid=(1,1,1), block=(32,1,1)):
        return launch(function, *grid, *block, 0, None, None, None)
    if scenario == 'cross-context':
        f.fakeCudaSetCurrentDomain.argtypes = [c.c_size_t, c.c_int]
        f.fakeCudaSetCurrentDomain(0xDA00, 3); assert invoke() != 0; assert f.fakeCudaLaunchCount() == 0; return
    if scenario == 'postload-missing-manifest':
        manifest_path.unlink();assert invoke()!=0, 'missing live manifest reused cached admission';return
    if scenario == 'stale':
        unload = p.cuModuleUnload; unload.argtypes = [c.c_void_p]
        assert unload(module) == 0; assert f.fakeCudaFutureEnabled() == 0; assert invoke() != 0; return
    if scenario == 'unload-clear-failure':
        unload=p.cuModuleUnload;unload.argtypes=[c.c_void_p]
        f.fakeCudaSetControlCopyFailure(1)
        assert unload(module)!=0
        f.fakeCudaSetControlCopyFailure(0)
        assert invoke()!=0, 'failed clear left active module launchable'
        token=c.c_size_t();assert api.begin_retire(10,generation.value,c.byref(token))!=0
        return
    if scenario == 'retire':
        assert invoke() == 0
        token = c.c_size_t(); assert api.begin_retire(10, generation.value, c.byref(token)) == 0
        assert api.invalidate_retire(token.value) == 0; assert f.fakeCudaFutureEnabled() == 0
        assert api.finish_retire(token.value) == 0; assert invoke() != 0
        old = generation.value
        assert api.activate_with_capabilities(10, 0xA000, 0xCA00, 3, c.byref(caps), c.byref(generation)) == 0
        assert generation.value > old; assert f.fakeCudaFutureEnabled() == 1; assert invoke() == 0; return
    if scenario == 'budget':
        with ThreadPoolExecutor(max_workers=8) as pool:
            codes = list(pool.map(lambda _: invoke(block=(1024,1,1)), range(32)))
        assert sum(code == 0 for code in codes) == 65536//(3*1024), codes
        assert f.fakeCudaLaunchCount() == 65536//(3*1024); return
    if scenario == 'enqueue-failure':
        f.fakeCudaSetLaunchFailure(1)
        for _ in range(32):assert invoke(block=(1024,1,1))!=0
        assert f.fakeCudaLaunchCount()==65536//(3*1024)
        f.fakeCudaSetLaunchFailure(0)
        assert invoke(block=(1024,1,1))!=0, 'unproved enqueue failure returned budget'
        return
    if scenario in ('req-axis', 'max-axis'):
        assert invoke(block=(16,2,1)) != 0; assert invoke(block=(8,4,1)) == 0; return
    if scenario == 'zero-grid': assert invoke(grid=(0,1,1)) != 0; return
    if scenario == 'overflow-grid': assert invoke(grid=(0xffffffff,)*3) != 0; return
    if scenario == 'block-bound': assert invoke(block=(1024,2,1)) != 0; return
    assert (invoke() == 0) == positive, (scenario, code)
    assert f.fakeCudaLaunchCount() == int(positive)
    if scenario.startswith('copy-'):
        assert code==0, 'safe rollback must remain a disabled loaded module'
        f.fakeCudaControlAlias.restype=c.c_uint64;f.fakeCudaControlGeneration.restype=c.c_uint64
        assert f.fakeCudaControlAlias()==0 and f.fakeCudaControlGeneration()==0
        token=c.c_size_t();assert api.begin_retire(10,generation.value,c.byref(token))==0
        assert api.invalidate_retire(token.value)==0
        assert api.finish_retire(token.value)==0
    if scenario=='clear-failure':
        assert code==801
        token=c.c_size_t();assert api.begin_retire(10,generation.value,c.byref(token))!=0

if __name__ == '__main__': main()
