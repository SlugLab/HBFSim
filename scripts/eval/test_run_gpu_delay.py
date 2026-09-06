"""CPU/static fixtures only. These tests do not initialize CUDA or launch GPU work."""
import importlib.util
import ctypes
import json
import pathlib
import os
import re
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[2]


class DelayHelperTests(unittest.TestCase):
    def test_clock_only_wait_has_finite_timeout_zero_and_wraparound_semantics(self):
        header = ROOT / 'src/cuda_runtime/device/hbf_device.cuh'
        self.assertIn('eval_delay_clock_interval', header.read_text(), 'clock-only interval helper is missing')
        source = r'''
#include "src/cuda_runtime/device/hbf_device.cuh"
#include <cassert>
#include <cstdint>
#include <initializer_list>
#include <limits>
struct Clock {
  std::initializer_list<std::uint64_t> samples;
  unsigned* reads;
  std::uint64_t operator()() const {
    assert(*reads < samples.size()); // A nonterminating wait cannot obtain more samples.
    return samples.begin()[(*reads)++];
  }
};
int main() {
  using namespace hbfsim::device;
  unsigned reads=0;
  auto zero=eval_delay_clock_interval(0, 1000, Clock{{100}, &reads});
  assert(zero.begin_ns==100 && zero.finish_ns==100 && reads==1);
  assert(zero.status==RequestStatus::Ready);
  reads=0;
  auto exact=eval_delay_clock_interval(500, 1000, Clock{{100,200,500,600}, &reads});
  assert(exact.begin_ns==100 && exact.finish_ns==600 && reads==4);
  assert(exact.status==RequestStatus::Ready);
  reads=0;
  auto timeout=eval_delay_clock_interval(500, 250, Clock{{100,200,300,400}, &reads});
  assert(timeout.finish_ns==400 && reads==4 && timeout.status==RequestStatus::Timeout);
  reads=0;
  auto missing_timeout=eval_delay_clock_interval(500, 0, Clock{{100}, &reads});
  assert(missing_timeout.finish_ns==100 && reads==1);
  assert(missing_timeout.status==RequestStatus::DaemonLost);
  reads=0;
  auto wrapped=eval_delay_clock_interval(100, 1000,
      Clock{{std::numeric_limits<std::uint64_t>::max()-50,10,60}, &reads});
  assert(wrapped.finish_ns==60 && reads==3 && wrapped.status==RequestStatus::Ready);
  // Preserve the existing loop's completion-before-timeout precedence when a
  // clock sample has already reached the requested delay.
  reads=0;
  auto completed=eval_delay_clock_interval(500, 250, Clock{{0,1000}, &reads});
  assert(completed.status==RequestStatus::Ready && reads==2);
}
'''
        with tempfile.TemporaryDirectory(prefix='.test-delay-clock-', dir=ROOT) as tmp:
            cpp = pathlib.Path(tmp) / 'clock.cpp'; cpp.write_text(source)
            binary = pathlib.Path(tmp) / 'clock'
            subprocess.run(['g++-13', '-std=c++20', '-I', str(ROOT), str(cpp), '-o', str(binary)],
                           check=True, capture_output=True)
            subprocess.run([str(binary)], check=True, timeout=5)

    @unittest.skipUnless(os.environ.get('HBFSIM_DELAY_COMPILE_BUILD'), 'requires explicit compile-only CUDA build path')
    def test_compiled_wait_interval_has_no_host_memory_or_liveness_operations(self):
        build = pathlib.Path(os.environ['HBFSIM_DELAY_COMPILE_BUILD']).resolve()
        ptx = (build / 'generated/hbf_device.ptx').read_text()
        match = re.search(r'(?m)^(?:\.visible\s+)?\.func\b[^;{}]*eval_delay_clock_wait[^;{}]*\{', ptx)
        self.assertIsNotNone(match, 'dedicated compiled clock-only wait function is missing')
        begin = match.end(); depth = 1; end = begin
        while depth and end < len(ptx):
            depth += (ptx[end] == '{') - (ptx[end] == '}'); end += 1
        self.assertEqual(depth, 0)
        body = ptx[begin:end-1]
        self.assertGreaterEqual(body.count('%globaltimer'), 2, 'interval requires start and loop clock samples')
        self.assertNotRegex(body, r'\b(?:ld|st)\.(?!param\b)', 'measured clock helper must not load/store host or device memory')
        self.assertNotRegex(body, r'\b(?:atom\.|nanosleep\b|membar\b|fence\b|call(?:\.|\s))',
                            'measured clock helper must contain only clock/control/register operations')
        source = (ROOT / 'src/cuda_runtime/device/hbf_device.cu').read_text()
        experiment = source.split('if (experiment == hbfsim::device::EvalDelayAction::Apply) {', 1)[1].split('} else {\n            resolution =', 1)[0]
        self.assertEqual(experiment.count('eval_delay_control_ready(header, expected_generation)'), 2,
                         'D=0 and positive delays require the same pre/post safety checks')
        before, after = experiment.split('eval_delay_clock_wait(', 1)
        self.assertIn('eval_delay_control_ready(header, expected_generation)', before)
        self.assertIn('eval_delay_control_ready(header, expected_generation)', after)

    def test_experiment_contract_is_separate_from_control_abi(self):
        header = ROOT / 'src/cuda_runtime/device/hbf_device.cuh'
        self.assertIn('EvalDelayConfig', header.read_text(), 'explicit opt-in contract missing')
        source = r'''
#include "src/cuda_runtime/device/hbf_device.cuh"
#include "include/hbfsim/protocol.hpp"
#include <cassert>
int main() {
  using namespace hbfsim::device;
  static_assert(kControlAbiVersion == 4 && sizeof(SharedControlHeader) == 384);
  constexpr auto read_op=static_cast<unsigned>(hbfsim::RequestOperation::Read);
  constexpr auto write_op=static_cast<unsigned>(hbfsim::RequestOperation::Write);
  EvalDelayConfig config{};
  SharedRangeRecord range{}; range.mode=1; range.permissions=1;
  assert(eval_delay_action(config, range, read_op, 1)==EvalDelayAction::Off);
  config.magic=kEvalDelayMagic; config.trace_address=4096; config.trace_capacity=1;
  assert(eval_delay_action(config, range, read_op, 1)==EvalDelayAction::Apply);
  assert(eval_delay_remaining(999, 1, 0)==0);
  assert(eval_delay_remaining(100, 100, 500)==500);
  assert(eval_delay_remaining(601, 100, 500)==0);
  assert(eval_delay_action(config, range, write_op, 1)==EvalDelayAction::Reject);
  range.mode=2;
  assert(eval_delay_action(config, range, read_op, 1)==EvalDelayAction::Reject);
  range.mode=1;
  assert(eval_delay_action(config, range, read_op, 100)==EvalDelayAction::Reject);
  config.magic=1;
  assert(eval_delay_action(config, range, read_op, 1)==EvalDelayAction::Reject);
  config.magic=kEvalDelayMagic; config.delay_ns=20001;
  assert(eval_delay_action(config, range, read_op, 1)==EvalDelayAction::Reject);
}
'''
        with tempfile.TemporaryDirectory(prefix='.test-delay-cpu-', dir=ROOT) as tmp:
            cpp = pathlib.Path(tmp)/'contract.cpp'; cpp.write_text(source)
            binary = pathlib.Path(tmp)/'contract'
            subprocess.run(['g++-13','-std=c++20','-I',str(ROOT),str(cpp),'-o',str(binary)],check=True,capture_output=True)
            subprocess.run([str(binary)],check=True,timeout=5)

    @unittest.skipUnless(os.environ.get('HBFSIM_DELAY_COMPILE_BUILD'),'requires explicit compile-only CUDA build path')
    def test_actual_benchmark_transforms_with_embedded_helper_and_assembles(self):
        build=pathlib.Path(os.environ['HBFSIM_DELAY_COMPILE_BUILD']).resolve()
        helper=(build/'generated/hbf_device.ptx').read_text()
        ptx=(build/'benchmarks/cuda/hbf_dependent_delay.ptx').read_text()
        self.assertIn('ld.volatile.global.u32',ptx)
        self.assertIn('%globaltimer',ptx)
        self.assertIn('.b8 __hbfsim_eval_delay_config[32];',helper)
        self.assertIn('.b8 __hbfsim_eval_delay_counters[48];',helper)
        self.assertIn('.b8 __hbfsim_eval_chain_diagnostic_config[136];',helper)
        self.assertEqual(len(re.findall(r'(?m)^\s*ld\.volatile\.global\.u32\b',ptx)),1)
        self.assertEqual(len(re.findall(r'(?m)^\s*st\.global\.u64\b',ptx)),7)
        library=ctypes.CDLL(str(build/'libptxpass_hbf.so'))
        library.process_input.argtypes=[ctypes.c_char_p,ctypes.c_int,ctypes.c_char_p]
        library.process_input.restype=ctypes.c_int
        with tempfile.TemporaryDirectory(prefix='.test-delay-ptx-',dir=ROOT) as tmp:
            tmp=pathlib.Path(tmp)
            with mock.patch.dict(os.environ,{'HBFSIM_PASS_MANIFEST_PATH':str(tmp/'manifest.jsonl')}):
                request=json.dumps(dict(input=dict(full_ptx=ptx,to_patch_kernel='hbf_dependent_delay',global_ebpf_map_info_symbol='map_info',ebpf_communication_data_symbol='constData'),ebpf_instructions=[])).encode()
                response=ctypes.create_string_buffer(32*1024*1024)
                self.assertEqual(library.process_input(request,len(response),response),0)
                transformed=json.loads(response.value)
            self.assertTrue(transformed['modified'])
            manifest=json.loads((tmp/'manifest.jsonl').read_text())
            self.assertTrue(manifest['instrumented'])
            self.assertEqual(manifest['unsupported_instructions'],0)
            self.assertTrue(all(op.startswith('ld.param.') for op in transformed['coverage']['unsupported_opcodes']))
            self.assertEqual(transformed['coverage']['rewritten_instructions'],8)
            self.assertIn('__hbfsim_module_identity',transformed['output_ptx'])
            (tmp/'transformed.ptx').write_text(transformed['output_ptx'])
            assembled=subprocess.run(['/usr/local/cuda-13.0/bin/ptxas','-arch=sm_120','-v',str(tmp/'transformed.ptx'),'-o',str(tmp/'kernel.cubin')],capture_output=True,text=True,timeout=30)
            self.assertEqual(assembled.returncode,0,assembled.stderr)
            self.assertGreater((tmp/'kernel.cubin').stat().st_size,0)


class DelayRunnerTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(importlib.util.find_spec('run_gpu_delay'), 'bounded known-delay runner missing')
        import run_gpu_delay
        self.r = run_gpu_delay
        self.tmp = tempfile.TemporaryDirectory(prefix='.test-delay-', dir=ROOT)
        self.addCleanup(self.tmp.cleanup)
        self.base = pathlib.Path(self.tmp.name)

    def test_plan_uses_exact_matrix_cell_and_defaults_to_no_launch(self):
        plan = self.r.make_plan(ROOT/'docs/49-eval-audit/run-matrix.csv','gpu_delay-00003',1,64)
        self.assertEqual(plan['condition']['profile'],'hbf_logical')
        self.assertEqual(plan['condition']['delay_us'],'0')
        self.assertEqual(plan['resource_class'],'GPU_EXCLUSIVE')
        self.assertFalse(plan['execute'])
        for cell,replicate,hops in [('missing',1,64),('flash_fidelity-00211',1,64),('gpu_delay-00001',11,64),('gpu_delay-00001',1,3)]:
            with self.subTest(cell=cell,replicate=replicate,hops=hops), self.assertRaises(ValueError):
                self.r.make_plan(ROOT/'docs/49-eval-audit/run-matrix.csv',cell,replicate,hops)

    def fixture(self, delay_ns=500, treatment='hbf_logical'):
        def case(kind, applied):
            addresses=[];next_index=0
            for _ in range(64):addresses.append(4096+next_index*4096);next_index=(17*next_index+1)%4096
            return dict(schema_version=1,evidence='TEST_ONLY',treatment=kind,trace_mode='legacy',requested_delay_ns=delay_ns,applied_delay_ns=applied,
                hops=64,warps=1,occupancy='low',blocks=1,sm_count=1,theoretical_blocks_per_sm=1,registers=20,
                dynamic_shared_bytes=49152,event_ns=6400+applied*64,
                chains=[dict(block=0,warp=0,sm=0,begin_ns=100,end_ns=6500+applied*64,checksum=77,expected_checksum=77)],
                block_intervals=[dict(block=0,sm=0,begin_ns=90,end_ns=6510+applied*64)],
                covered_accesses=0 if kind=='native' else 64,covered_bytes=0 if kind=='native' else 256,
                input_base=4096,input_bytes=4096*4096,
                bypass_accesses=0 if kind=='native' else 7,bypass_bytes=0 if kind=='native' else 56,rejected_accesses=0,trace_overflow=0,
                rewritten_instructions=0 if kind=='native' else 5,unsupported_instructions=0,unknown_bytes=0,
                waits=[] if kind=='native' else [dict(thread_id=0,address=address,wait_enter_ns=100+i*(100+applied),wait_exit_ns=100+i*(100+applied)+applied,delay_ns=applied) for i,address in enumerate(addresses)])
        return dict(native=case('native',0),matched_zero=case('fast_logical',0),target=case(treatment,delay_ns if treatment=='hbf_logical' else 0))

    def per_chain_fixture(self,delay_ns=500,treatment='hbf_logical'):
        data=self.fixture(delay_ns,treatment)
        for case in data.values():
            case['trace_mode']='per_chain'
            if case['treatment']=='native':
                case['chain_diagnostic']={'enabled':False,'reason':'native_uninstrumented','rows':[]}
                continue
            chain_address=20_000_000;block_address=21_000_000
            def event(address,bytes_,operation,event_class,time):
                return dict(thread_id=0,address=address,order=0,begin_ns=time,end_ns=time,
                            bytes=bytes_,operation=operation,event_class=event_class,status=1)
            events=[event(block_address,8,1,3,91),event(block_address+16,8,1,3,92)]
            for wait in case['waits']:
                events.append(dict(thread_id=0,address=wait['address'],order=0,
                    begin_ns=wait['wait_enter_ns'],end_ns=wait['wait_exit_ns'],bytes=4,
                    operation=0,event_class=1,status=1))
            chain_end=case['chains'][0]['end_ns']
            events.extend(event(chain_address+offset,8,1,2,chain_end+1+slot)
                          for slot,offset in enumerate((0,8,16,24)))
            events.append(event(block_address+8,8,1,3,case['block_intervals'][0]['end_ns']+1))
            for order,item in enumerate(events):item['order']=order
            row=dict(row=0,covered_accesses=64,covered_bytes=256,bypass_accesses=7,bypass_bytes=56,
                     rejected_accesses=0,trace_overflow=0,event_count=71,launch_epoch=2,
                     writer_thread_id=0,writer_observed=1,reserved=0,unused_slots_zero=True,events=events)
            case['chain_diagnostic']=dict(enabled=True,magic=0x4556434841494e31,
                symbol_bytes=136,version=2,config_bytes=136,delay_ns=case['applied_delay_ns'],launch_epoch=2,
                grid_x=1,grid_y=1,grid_z=1,block_x=32,block_y=1,block_z=1,warps_per_block=1,hops=64,
                row_count=1,row_stride=4160,trace_capacity=71,storage_address=22_000_000,
                storage_bytes=4160,chain_output_address=chain_address,chain_output_bytes=32,
                block_output_address=block_address,block_output_bytes=24,rows=[row])
        return data

    def test_fixed_g2_gates_and_zero_noise(self):
        data=self.fixture()
        report=self.r.analyze(data)
        self.assertTrue(report['g2_cell_pass'])
        self.assertEqual(report['per_access_delta_ns'],[500])
        self.assertEqual(report['event_delta_ns'],32000)
        self.assertEqual(report['mean_absolute_error_limit_ns'],100)
        self.assertEqual(report['p95_absolute_error_limit_ns'],200)
        data['target']['chains'][0]['end_ns']+=64*201
        self.assertFalse(self.r.analyze(data)['g2_cell_pass'])
        noise=self.r.analyze(self.fixture(0))
        self.assertIsNone(noise['g2_cell_pass'])
        self.assertNotIn('relative_error',noise)

    def test_bad_checksum_coverage_or_pair_never_validates(self):
        for key,value in [('checksum',99),('hops',16),('covered_bytes',0),('unsupported_instructions',1),('unknown_bytes',1),('trace_overflow',1),('treatment','native')]:
            data=self.fixture()
            if key=='checksum':data['target']['chains'][0][key]=value
            else:data['target'][key]=value
            with self.subTest(key=key), self.assertRaises(ValueError):self.r.analyze(data)

    def test_per_chain_rows_bind_actual_event_identity_and_reset_state(self):
        baseline=self.per_chain_fixture()
        self.assertTrue(self.r.analyze(baseline)['g2_cell_pass'])
        mutations={
            'owner':lambda c:c['chain_diagnostic']['rows'][0].__setitem__('writer_thread_id',32),
            'epoch':lambda c:c['chain_diagnostic']['rows'][0].__setitem__('launch_epoch',1),
            'row-type':lambda c:c['chain_diagnostic']['rows'][0].__setitem__('row',False),
            'config-size':lambda c:c['chain_diagnostic'].__setitem__('config_bytes',104),
            'span-overlap':lambda c:c['chain_diagnostic'].__setitem__('storage_address',c['chain_diagnostic']['chain_output_address']),
            'address':lambda c:c['chain_diagnostic']['rows'][0]['events'][3].__setitem__('address',c['input_base']),
            'bytes':lambda c:c['chain_diagnostic']['rows'][0]['events'][2].__setitem__('bytes',8),
            'operation':lambda c:c['chain_diagnostic']['rows'][0]['events'][2].__setitem__('operation',1),
            'class':lambda c:c['chain_diagnostic']['rows'][0]['events'][2].__setitem__('event_class',2),
            'status':lambda c:c['chain_diagnostic']['rows'][0]['events'][2].__setitem__('status',5),
            'order':lambda c:c['chain_diagnostic']['rows'][0]['events'][2].__setitem__('order',3),
            'overflow':lambda c:c['chain_diagnostic']['rows'][0].__setitem__('trace_overflow',1),
            'stale-slot':lambda c:c['chain_diagnostic']['rows'][0].__setitem__('unused_slots_zero',False),
            'missing-event':lambda c:c['chain_diagnostic']['rows'][0]['events'].pop(),
            'synthetic-wait':lambda c:c['waits'][1].__setitem__('address',c['input_base']),
        }
        for name,mutate in mutations.items():
            data=json.loads(json.dumps(baseline));mutate(data['target'])
            with self.subTest(name=name),self.assertRaisesRegex(ValueError,'chain diagnostic'):
                self.r.analyze(data)

    def test_achieved_residency_uses_intervals_not_configuration_label(self):
        data=self.fixture()
        for case in data.values():case['occupancy']='high';case['theoretical_blocks_per_sm']=4
        with self.assertRaisesRegex(ValueError,'occupancy'):self.r.analyze(data)

    def sparse_sm_fixture(self):
        data=self.fixture(0)
        for case in data.values():
            case['blocks']=2;case['sm_count']=2
            case['chains'][0]['sm']=7;case['block_intervals'][0]['sm']=7
            case['chains'].append(dict(case['chains'][0],block=1,sm=29))
            case['block_intervals'].append(dict(case['block_intervals'][0],block=1,sm=29))
            if case['treatment']!='native':
                for key in ('covered_accesses','covered_bytes','bypass_accesses','bypass_bytes'):case[key]*=2
                next_index=1;additional=[]
                for wait in case['waits']:
                    additional.append(dict(wait,thread_id=32,address=case['input_base']+next_index*4096))
                    next_index=(17*next_index+1)&4095
                case['waits'].extend(additional)
        return data

    def test_sparse_sm_identifiers_are_valid_within_physical_sm_cardinality(self):
        report=self.r.analyze(self.sparse_sm_fixture())
        self.assertEqual(report['observed_peak_blocks_per_sm']['target'],{'7':1,'29':1})

    def test_observed_sm_cardinality_and_block_chain_identity_are_validated(self):
        for variant in ('excess_cardinality','inconsistent_chain',-1,True,1.5):
            data=self.sparse_sm_fixture()
            if variant=='excess_cardinality':
                for case in data.values():case['sm_count']=1
            elif variant=='inconsistent_chain':data['target']['chains'][1]['sm']=7
            else:data['target']['block_intervals'][1]['sm']=variant
            with self.subTest(variant=variant),self.assertRaisesRegex(ValueError,'SM'):
                self.r.analyze(data)

    def test_blocked_gpu_does_not_start_payload_and_preserves_immutable_diagnostics(self):
        plan=self.r.make_plan(ROOT/'docs/49-eval-audit/run-matrix.csv','gpu_delay-00001',1,64)
        called=[]
        probe=lambda uuid:dict(gpu_uuid=uuid,available=False,processes=[],error='TEST_ONLY unavailable driver')
        result=self.r.execute(plan,self.base/'attempt',self.base,ROOT/'configs/profiles/nominal.json','GPU-test',gpu_probe=probe,child_runner=lambda *args:called.append(args))
        self.assertEqual(result['state'],'BLOCKED_GPU_BUSY')
        self.assertEqual(called,[])
        self.assertTrue((self.base/'attempt/manifest.json').is_file())
        self.assertTrue((self.base/'attempt/raw.gpu.jsonl').is_file())
        with self.assertRaises(FileExistsError):self.r.execute(plan,self.base/'attempt',self.base,ROOT/'configs/profiles/nominal.json','GPU-test',gpu_probe=probe)

    def test_destinations_confined_and_test_outputs_never_formal(self):
        plan=self.r.make_plan(ROOT/'docs/49-eval-audit/run-matrix.csv','gpu_delay-00001',1,64)
        for out in (ROOT.parent/'outside-delay',ROOT/'results/runs/forbidden-delay-fixture'):
            with self.subTest(out=out),self.assertRaises(ValueError):
                self.r.execute(plan,out,self.base,ROOT/'configs/profiles/nominal.json','GPU-test',gpu_probe=lambda _:None)
            self.assertFalse(out.exists())

    def execution_fixture(self):
        build=self.base/'build';(build/'benchmarks/cuda').mkdir(parents=True);(build/'generated').mkdir()
        for name in ('benchmarks/cuda/hbf_dependent_delay','benchmarks/cuda/hbf_dependent_delay.ptx','libptxpass_hbf.so','libhbfsim_launch_gate.so','hbfsimd','CMakeCache.txt'):
            (build/name).write_text('TEST_ONLY build placeholder, never executed')
        (build/'generated/hbf_device.ptx').write_text('__hbfsim_eval_delay_config __hbfsim_eval_delay_counters __hbfsim_resolve TEST_ONLY')
        profile=self.base/'profile.json'
        profile.write_text(json.dumps(dict(time_scale=1,read_latency_ns=100,program_latency_ns=100,aggregate_bandwidth_bytes_per_s=1)))
        plan=self.r.make_plan(ROOT/'docs/49-eval-audit/run-matrix.csv','gpu_delay-00003',1,64)
        data=self.fixture(0)
        for name,case in data.items():(self.base/(name+'.json')).write_text(json.dumps(case))
        producer=self.base/'producer.py'
        producer.write_text('''import json,pathlib,sys
out=pathlib.Path.cwd()
(out/'raw.json').write_bytes(pathlib.Path(sys.argv[1]).read_bytes())
(out/'coverage.jsonl').write_text(json.dumps({'allowed':True,'modeled':True})+'\\n')
print('TEST_ONLY deterministic CPU fixture; no GPU measurements')
''')
        def child(argv,env,attempt,*args):
            env=dict(env);env.pop('LD_PRELOAD',None)
            return self.r.run_child([sys.executable,str(producer),str(self.base/(attempt.name+'.json'))],env,attempt,*args)
        probe=lambda uuid:dict(gpu_uuid=uuid,available=True,processes=[])
        return plan,build,profile,child,probe

    def test_actual_cpu_subprocess_triplet_is_test_only_with_durable_logs(self):
        plan,build,profile,child,probe=self.execution_fixture()
        result=self.r.execute(plan,self.base/'out',build,profile,'GPU-test',child_runner=child,gpu_probe=probe)
        self.assertEqual(result['state'],'TEST_ONLY_DONE')
        manifest=json.loads((self.base/'out/manifest.json').read_text())
        self.assertEqual(manifest['evidence'],'TEST_ONLY')
        self.assertIn('target/stdout.log',manifest['artifact_hashes'])
        self.assertIn('TEST_ONLY',(self.base/'out/target/stdout.log').read_text())
        self.assertEqual(len(manifest['commands']),3)

    def test_per_chain_selector_requires_symbol_and_reaches_cpu_fixture(self):
        plan,build,profile,child,probe=self.execution_fixture();calls=[]
        def counted(*args):calls.append(args);return child(*args)
        missing=self.r.execute(plan,self.base/'missing',build,profile,'GPU-test',
                               trace_mode='per_chain',child_runner=counted,gpu_probe=probe)
        self.assertEqual(missing['state'],'INVALID_GOLD_GATE');self.assertEqual(calls,[])
        helper=build/'generated/hbf_device.ptx'
        helper.write_text(helper.read_text()+' __hbfsim_eval_chain_diagnostic_config')
        for name,case in self.per_chain_fixture(0).items():
            (self.base/(name+'.json')).write_text(json.dumps(case))
        result=self.r.execute(plan,self.base/'valid',build,profile,'GPU-test',
                              trace_mode='per_chain',child_runner=child,gpu_probe=probe)
        self.assertEqual(result['state'],'TEST_ONLY_DONE',result)
        manifest=json.loads((self.base/'valid/manifest.json').read_text())
        self.assertEqual(manifest['trace_mode'],'per_chain')
        self.assertTrue(all(command[-2:]==['--trace-mode','per_chain']
                            for command in manifest['commands'].values()))

    def test_consistent_wrong_triplet_dimensions_are_rejected_against_selected_plan(self):
        plan,build,profile,child,probe=self.execution_fixture()
        variants=(('requested_delay_ns',500),('hops',16),('warps',2),('occupancy','high'))
        for field,value in variants:
            with self.subTest(field=field):
                data=self.fixture(500 if field=='requested_delay_ns' else 0)
                for name,case in data.items():
                    case[field]=value
                    (self.base/(name+'.json')).write_text(json.dumps(case))
                out=self.base/field
                result=self.r.execute(plan,out,build,profile,'GPU-test',child_runner=child,gpu_probe=probe)
                self.assertEqual(result['state'],'INVALID_GOLD_GATE')
                self.assertIn(field+' differs from selected plan',result['error'])
                self.assertTrue((out/'native/raw.json').exists())
                self.assertFalse((out/'matched_zero').exists(),'wrong first result must stop further launches')
                self.assertFalse((out/'raw.analysis.json').exists())

    def test_input_mutation_during_subprocess_prevents_done(self):
        plan,build,profile,child,probe=self.execution_fixture()
        def mutate(*args):
            code=child(*args);profile.write_text('{}');return code
        result=self.r.execute(plan,self.base/'out',build,profile,'GPU-test',child_runner=mutate,gpu_probe=probe)
        self.assertEqual(result['state'],'INVALID_GOLD_GATE')
        self.assertIn('input changed',result['error'])

    def test_analysis_rejects_missing_wait_thread_and_invalid_zero_wait(self):
        for variant in ('thread','zero_wait','duration_nan'):
            data=self.fixture(0)
            if variant=='thread':data['target']['waits'][0]['thread_id']=32
            elif variant=='zero_wait':data['target']['waits'][0]['wait_exit_ns']+=1
            else:data['target']['chains'][0]['end_ns']=float('nan')
            with self.subTest(variant=variant),self.assertRaises(ValueError):self.r.analyze(data)

    def test_profile_must_remain_positive_time_scale_one_before_payload(self):
        plan,build,profile,child,probe=self.execution_fixture()
        for field,value in (('time_scale',100),('read_latency_ns',0)):
            content=dict(time_scale=1,read_latency_ns=100,program_latency_ns=100,aggregate_bandwidth_bytes_per_s=1)
            content[field]=value;profile.write_text(json.dumps(content));calls=[]
            result=self.r.execute(plan,self.base/field,build,profile,'GPU-test',gpu_probe=probe,child_runner=lambda *args:calls.append(True))
            self.assertEqual(len(calls),0)
            self.assertEqual(result['state'],'INVALID_GOLD_GATE')

    def test_full_dynamic_access_and_permutation_coverage_is_required(self):
        for variant in ('address','outside_chain','bypass_bytes'):
            data=self.fixture()
            if variant=='address':data['target']['waits'][1]['address']=4096
            elif variant=='outside_chain':data['target']['waits'][-1]['wait_exit_ns']=1000000000
            else:data['target']['bypass_bytes']+=4
            with self.subTest(variant=variant),self.assertRaises(ValueError):self.r.analyze(data)

    def test_foreign_gpu_process_blocks_before_any_payload(self):
        plan,build,profile,child,_=self.execution_fixture();calls=[]
        probe=lambda uuid:dict(gpu_uuid=uuid,available=True,processes=[dict(pid=99999999,identity=None)])
        result=self.r.execute(plan,self.base/'out',build,profile,'GPU-test',gpu_probe=probe,child_runner=lambda *args:calls.append(True))
        self.assertEqual(result['state'],'BLOCKED_GPU_BUSY');self.assertEqual(len(calls),0)

    def test_foreign_gpu_during_run_contaminates_and_retains_diagnostics(self):
        plan,build,profile,child,_=self.execution_fixture();calls=[]
        def probe(uuid):
            calls.append(True)
            return dict(gpu_uuid=uuid,available=True,processes=[] if len(calls)<4 else [dict(pid=99999999,identity=None)])
        result=self.r.execute(plan,self.base/'out',build,profile,'GPU-test',gpu_probe=probe,child_runner=child)
        self.assertEqual(result['state'],'CONTAMINATED_EXTERNAL_GPU')
        self.assertTrue((self.base/'out/native/stdout.log').exists())
        self.assertTrue((self.base/'out/raw.gpu.jsonl').exists())


if __name__=='__main__':unittest.main()
