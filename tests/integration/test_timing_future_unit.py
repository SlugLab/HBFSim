"""TEST_ONLY actual-plugin and optimized assembler C6.2 contract checks (no CUDA launch)."""
import ctypes
import hashlib
import json
import os
import re
import shutil
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

PLUGIN = Path(sys.argv.pop(1)).resolve()
ENABLED = sys.argv.pop(1) == 'on'
NVCC = Path(sys.argv.pop(1)) if len(sys.argv) > 1 else None
ROOT = Path(__file__).resolve().parents[2]


def source(name='kernel', geometry=''):
    return '''.version 8.7
.target sm_120
.address_size 64
.visible .entry NAME(.param .u64 .ptr .global input, .param .u64 .ptr .global output)
GEOMETRY
{
.reg .b64 %rd<4>;
.reg .b32 %r<3>;
.reg .pred %p;
ld.param.u64 %rd1, [input]; ld.param.u64 %rd2, [output];
mov.u32 %r0, %tid.x; mul.wide.u32 %rd3, %r0, 4;
add.u64 %rd1, %rd1, %rd3; add.u64 %rd2, %rd2, %rd3;
setp.eq.u32 %p, %r0, 0;
@%p ld.global.u32 %r1, [%rd1];
@%p add.u32 %r2, %r1, 1;
@%p st.global.u32 [%rd2], %r2;
ret;
}
'''.replace('NAME', name).replace('GEOMETRY', geometry)


class Unit(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.plugin = ctypes.CDLL(str(PLUGIN))
        cls.plugin.process_input.argtypes = [ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p]
        cls.plugin.process_input.restype = ctypes.c_int

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix='.c6-unit-', dir=ROOT)
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name)
        self.manifest = self.path/'manifest.jsonl'
        self.previous_manifest = os.environ.get('HBFSIM_PASS_MANIFEST_PATH')
        os.environ['HBFSIM_PASS_MANIFEST_PATH'] = str(self.manifest)
        self.addCleanup(self.restore_env)

    def tearDown(self):
        if os.environ.get('HBFSIM_C6_UNIT_ARTIFACTS'):
            target=Path(os.environ['HBFSIM_C6_UNIT_ARTIFACTS']).resolve()
            self.assertTrue(target.is_relative_to(ROOT/'results/gold/timing-future-unit'))
            shutil.copytree(self.path,target/self._testMethodName)

    def restore_env(self):
        if self.previous_manifest is None:
            os.environ.pop('HBFSIM_PASS_MANIFEST_PATH', None)
        else:
            os.environ['HBFSIM_PASS_MANIFEST_PATH'] = self.previous_manifest

    def invoke(self, text=None, kernel='kernel', mode='timing_load_future_v1', **options):
        request = {'full_ptx': text or source(), 'to_patch_kernel': kernel,
                   'transform_mode': mode, **options}
        out = ctypes.create_string_buffer(16*1024*1024)
        code = self.plugin.process_input(json.dumps({'input': request}).encode(), len(out), out)
        return code, json.loads(out.value) if out.value else {}

    def accepted(self, *args, **kwargs):
        code, result = self.invoke(*args, **kwargs)
        self.assertEqual(code, 0, result)
        return result

    def test_actual_plugin_complete_or_off(self):
        code, result = self.invoke()
        if not ENABLED:
            self.assertNotEqual(code, 0)
            self.assertNotIn('output_ptx', result)
            self.assertFalse(self.manifest.exists())
            return
        self.assertEqual(code, 0, result)
        manifest = json.loads(self.manifest.read_text().splitlines()[-1])
        self.assertEqual(manifest['transform_mode'], 'timing_load_future_v1')
        self.assertEqual(manifest['future_contract']['original_ptx_sha256'], hashlib.sha256(source().encode()).hexdigest())
        self.assertEqual(manifest['future_contract']['trace_capacity'], 65536)
        self.assertEqual(manifest['future_contract']['trace_record_bytes'], 64)
        self.assertEqual(manifest['future_contract']['maximum_records_per_producer'], 3)
        self.assertEqual(manifest['future_kernel']['static_producers'], 1)
        self.assertEqual(manifest['future_kernel']['maximum_block_threads'], 1024)
        header=(PLUGIN.parent/'generated/hbf_device_ptx.hpp').read_text()
        embedded=re.search(r'R"(\w+)\(([\s\S]*)\)\1";',header).group(2)
        self.assertEqual(manifest['future_contract']['helper_sha256'],hashlib.sha256(embedded.encode()).hexdigest())
        self.assertIn(embedded,result['output_ptx'])
        self.assertTrue(manifest['instrumented'])
        self.assertIn('__hbfsim_timing_future_requirements_v1', result['output_ptx'])
        self.assertIn('__hbfsim_timing_future_trace_v1', result['output_ptx'])
        self.assertIn('call', result['output_ptx'])
        if NVCC:
            ptx = self.path/'actual-plugin.ptx'; ptx.write_text(result['output_ptx'])
            run = subprocess.run([str(NVCC.parent/'ptxas'), '-O3', '-arch=sm_120', str(ptx), '-o', str(self.path/'unit.cubin')], capture_output=True, text=True, timeout=60)
            self.assertEqual(run.returncode, 0, run.stderr)
            self.assertGreater((self.path/'unit.cubin').stat().st_size, 0)

    @unittest.skipUnless(ENABLED, 'future build disabled')
    def test_future_parameters_bind_validated_entry_spans(self):
        misleading='.entry kernel(.param .b8 misleading[16]) { ret; }'
        prefix='.visible .entry kernel_long(.param .b8 misleading[16]) { ret; }\n'
        fixtures={'comment':'// '+misleading+'\n'+source(),
                  'prefix':source().replace('.visible .entry kernel(',prefix+'.visible .entry kernel(',1),
                  'annotations':source().replace('.param .u64 .ptr .global input',
                      '.param /* .param .b8 false[16] */ .u64 .ptr .global .align 8 input').replace(
                      '.param .u64 .ptr .global output',
                      '.param .u64 /* output annotation */ .ptr .global .align 8 output')}
        expected=[dict(index=0,offset=0,width=8,kind='pointer'),
                  dict(index=1,offset=8,width=8,kind='pointer')]
        commands=[]
        for tag,original in fixtures.items():
            with self.subTest(tag=tag):
                result=self.accepted(original)
                manifest=json.loads(self.manifest.read_text().splitlines()[-1])
                (self.path/(tag+'.manifest.json')).write_text(json.dumps(manifest,indent=2)+'\n')
                for suffix,text in [('original',original),('emitted',result['output_ptx'])]:
                    ptx=self.path/(tag+'.'+suffix+'.ptx');ptx.write_text(text)
                    if NVCC:
                        argv=[str(NVCC.parent/'ptxas'),'-O3','-arch=sm_120',str(ptx),'-o',str(self.path/(tag+'.'+suffix+'.cubin'))]
                        assembled=subprocess.run(argv,capture_output=True,text=True,timeout=30)
                        commands.append(dict(argv=argv,returncode=assembled.returncode,stderr=assembled.stderr))
                        self.assertEqual(assembled.returncode,0,assembled.stderr)
                self.assertEqual(manifest['parameters'],expected)
                self.assertEqual(manifest['unsupported_parameters'],[])
        (self.path/'commands.json').write_text(json.dumps(commands,indent=2)+'\n')

    @unittest.skipUnless(ENABLED, 'future build disabled')
    def test_original_identity_multikernel_and_modes(self):
        original = source() + source('other').split('.visible', 1)[0].replace(source().split('.visible', 1)[0], '') + '.visible' + source('other').split('.visible', 1)[1]
        first = self.accepted(original)
        second = self.accepted(first['output_ptx'], kernel='other')
        self.assertEqual(first['transform_identity'], second['transform_identity'])
        self.assertNotEqual(self.invoke(second['output_ptx'], mode='synchronous')[0], 0)
        self.assertNotEqual(self.invoke(source().replace('sm_120', 'sm_90'))[0], 0)
        spoof='/*\n.target sm_120\n*/\n'+source().replace('sm_120','sm_90')
        self.assertNotEqual(self.invoke(spoof)[0], 0, 'comment supplied a false helper target')
        forged = source() + '\n.visible .global .u64 __hbfsim_timing_future_config_v1;\n'
        self.assertNotEqual(self.invoke(forged)[0], 0)
        again=self.accepted(second['output_ptx'])
        self.assertEqual(again['output_ptx'],second['output_ptx'])
        if NVCC:
            commands=[]
            for name,result in [('image-A',first),('image-B',second)]:
                ptx=self.path/(name+'.ptx');ptx.write_text(result['output_ptx'])
                argv=[str(NVCC.parent/'ptxas'),'-O3','-arch=sm_120',str(ptx),'-o',str(self.path/(name+'.cubin'))]
                assembled=subprocess.run(argv,capture_output=True,text=True,timeout=30)
                commands.append(dict(argv=argv,returncode=assembled.returncode,stderr=assembled.stderr))
                self.assertEqual(assembled.returncode,0,assembled.stderr)
            (self.path/'image-commands.json').write_text(json.dumps(commands,indent=2)+'\n')

    @unittest.skipUnless(ENABLED,'future build disabled')
    def test_actual_typed_operations_and_compile(self):
        saved=sys.argv;sys.argv=[sys.argv[0]]
        try:
            from test_future_emitter import Machine,program
        finally:sys.argv=saved
        cases=[(kind+str(width),width,max(16,width),(1<<(width-1))+1)
               for kind in 'usb' for width in (8,16,32,64)]
        cases += [('s8',8,64,0x80),('s16',16,32,0x8001),('s32',32,64,0x80000001),
                  ('f32',32,32,0x7fc01234),('f64',64,64,0x7ff8123456789abc)]
        commands=[]
        for typ,width,destbits,bits in cases:
            with self.subTest(type=typ):
                regtype=typ if typ.startswith('f') else 'b'+str(destbits)
                body=f'mov.b{destbits} %v,0; setp.eq.u32 %p0,%r0,0; @%p0 ld.global.{typ} %v,[%rd4]; setp.ne.u32 %p0,%r0,0; mov.b{destbits} %got,%v; st.global.b{destbits} [%rd1],%got;'
                original=program(body,f'.reg .{regtype} %v; .reg .b{destbits} %got;')
                emitted=self.accepted(original)['output_ptx']
                machine=Machine(emitted);machine.write(machine.memory,0x1000,width//8,bits);machine.run()
                expected=bits
                if typ.startswith('s') and bits>>(width-1):expected|=((1<<destbits)-1)^((1<<width)-1)
                self.assertEqual(machine.reg[0]['%got'],expected)
                self.assertEqual(machine.count['issued'],1);self.assertEqual(machine.count['consumed'],1)
                self.assertEqual(machine.count['pending'],0)
                tag=typ+'-reg'+str(destbits);path=self.path/(tag+'.ptx');path.write_text(emitted)
                (self.path/(tag+'.original.ptx')).write_text(original)
                if NVCC:
                    argv=[str(NVCC.parent/'ptxas'),'-O3','-arch=sm_120',str(path),'-o',str(self.path/(tag+'.cubin'))]
                    result=subprocess.run(argv,capture_output=True,text=True,timeout=30)
                    commands.append(dict(argv=argv,returncode=result.returncode,stderr=result.stderr))
                    self.assertEqual(result.returncode,0,result.stderr)
        (self.path/'commands.json').write_text(json.dumps(commands,indent=2)+'\n')
        text=self.accepted(program('ld.global.u32 %r1,[%rd4]; add.u32 %r2,%r1,1;'))['output_ptx']
        machine=Machine(text,lanes=8,base=0x1ff0,ranges=((0x1000,4096,4096),)).run()
        self.assertEqual(machine.count['issued'],4);self.assertEqual(machine.count['native_loads'],4)
        self.assertEqual(machine.count['pending'],0)

if __name__ == '__main__':
    unittest.main()
