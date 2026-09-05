"""Owned-arm composition using fake runtime objects and tiny frozen metadata."""
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace,ModuleType
import unittest
from unittest import mock
import numpy as np

ROOT=Path(__file__).resolve().parents[2]
sys.path[:0]=[str(ROOT/'adapters/vllm_capacity'),str(ROOT)]
import hf_routing_worker as protocol
import hf_loaded_arm as worker
from trace_collector import JsonlTraceCollector
from evaluation_inventory import load_hf_snapshot
from test_evaluation_inventory import hf_fixture
import test_hf_runtime_sources as source_tests


class LoadedArmTests(unittest.TestCase):
    def setUp(self):
        base=ROOT/'results/gold/hf-routing-runner';base.mkdir(parents=True,exist_ok=True)
        temp=tempfile.TemporaryDirectory(prefix='.loaded-arm-test-',dir=base);self.addCleanup(temp.cleanup)
        self.base=Path(temp.name);meta=self.base/'metadata';meta.mkdir()
        checkpoint,bundle=hf_fixture(meta)
        fixture=source_tests.RuntimeSourceTests();fixture.setUp();self.addCleanup(fixture.doCleanups)
        work=self.base/'private-work';(work/'tmp').mkdir(parents=True)
        self.plan=dict(metadata_snapshot=load_hf_snapshot(bundle),runtime_snapshot=fixture.collect(),
            work_dir=work,gpu_uuid='GPU-TEST_ONLY',device_capability=(12,0),
            prompt_token_ids=[i%16 for i in range(32)],run_id='TEST_ONLY-arm',
            git_commit='a'*40,environment_fingerprint='b'*64)
        self.checkpoint=checkpoint;self.events=[];self.failure=None;self.scope=None
        self.owner=np.arange(78,dtype=np.int32).reshape(39,2,1)%2

    def dependencies(self):
        test=self;module=ModuleType('TEST_ONLY.capturer');module.shared_memory=object()
        module._global_experts_capturer=module._global_experts_reader=None
        class Capturer:
            @staticmethod
            def get_instance():return module._global_experts_capturer
            def cleanup(self):
                test.events.append('capturer-cleanup')
                if test.failure=='capturer-cleanup':raise RuntimeError('capturer cleanup failed')
        class Reader:
            def cleanup(self):
                test.events.append('reader-cleanup')
                if test.failure=='reader-cleanup':raise KeyboardInterrupt('reader cleanup interrupted')
        module.RoutedExpertsCapturer=Capturer;module.RoutedExpertsReader=Reader
        class Scope:
            def __init__(self,mod,path):self.module=mod;self.records=[];self.original=mod.shared_memory;test.scope=self
            def __enter__(self):test.events.append('ownership-enter');self.module.shared_memory=self;return self
            def reconcile(self,instance,rank):test.events.append(('reconcile',instance,rank))
            def finish(self,capturer=None,reader=None,client=None):
                errors=[]
                for name,obj,method in [('reader',reader,'cleanup'),('capturer',capturer,'cleanup'),('engine',client,'shutdown')]:
                    if obj is None:continue
                    try:getattr(obj,method)()
                    except BaseException as e:errors.append(name+':'+type(e).__name__)
                self.module.shared_memory=self.original;test.events.append('ownership-restored')
                return dict(status='FAILED_MEMORY_CLEANUP' if errors else 'OWNED_MEMORY_CLOSED',errors=errors,records=self.records,scientific_validation_passed=False)
        class Client:
            def __init__(self):self.engine_core=SimpleNamespace(scheduler=SimpleNamespace(routed_experts_reader=None))
            def shutdown(self):
                test.events.append('client-shutdown')
                if test.failure=='client-shutdown':raise RuntimeError('shutdown failed')
        class LLM:
            def __init__(self,**kwargs):
                test.events.append(('construct',kwargs));self.kwargs=kwargs
                if test.failure=='before-engine':raise RuntimeError('construction before engine')
                client=Client();cfg=SimpleNamespace(instance_id='1234',parallel_config=SimpleNamespace(data_parallel_rank=0))
                self.llm_engine=SimpleNamespace(engine_core=client,vllm_config=cfg)
                if kwargs['enable_return_routed_experts']:
                    test.assertIsNotNone(Capturer.get_instance())
                    module._global_experts_capturer=Capturer();module._global_experts_reader=Reader()
                    test.scope.records=[dict(create=True,complete=True),dict(create=False,complete=True)]
                    client.engine_core.scheduler.routed_experts_reader=module._global_experts_reader
                else:test.assertIsNone(Capturer.get_instance())
                if test.failure=='wrong-reader':client.engine_core.scheduler.routed_experts_reader=Reader()
                if test.failure=='native-reader-absent':del client.engine_core.scheduler.routed_experts_reader
                if test.failure=='after-engine':raise RuntimeError('construction after engine')
            def generate(self,prompts,sampling,use_tqdm):
                test.events.append(('generate',prompts,vars(sampling),use_tqdm))
                if test.failure=='generate':raise RuntimeError('generation failed')
                if test.failure=='binding-replaced':Capturer.get_instance=staticmethod(lambda:None)
                if test.failure=='binding-class-removed':del module.RoutedExpertsCapturer
                if test.failure=='reader-global-removed':del module._global_experts_reader
                if test.failure=='output-parent-alias':
                    moved=test.base/'moved-candidate';test.out.parent.rename(moved)
                    test.out.parent.symlink_to(moved,target_is_directory=True)
                routes=test.owner if self.kwargs['enable_return_routed_experts'] else None
                output=list(range(8)) if test.failure!='serialize' else [True]*8
                return [SimpleNamespace(request_id='owned-real-api-id',prompt_token_ids=list(test.plan['prompt_token_ids']),finished=True,num_cached_tokens=0,outputs=[SimpleNamespace(index=0,token_ids=output,finish_reason='length',stop_reason=None,routed_experts=routes)])]
        class Sampling:
            def __init__(self,**kwargs):vars(self).update(kwargs)
        class Kind:FINAL_ONLY=object()
        def imports(*args):test.events.append('import-observation');return dict(provenance='MOCK',scientific_validation_passed=False)
        def runtime(*args):
            test.events.append('runtime-observation')
            if test.failure=='runtime-observation':raise RuntimeError('wrong backend')
            return dict(provenance='MOCK',scientific_validation_passed=False)
        class Collector(JsonlTraceCollector):
            def __init__(self,*args,**kwargs):
                test.events.append('collector-open')
                if test.failure=='collector':raise RuntimeError('collector failed after raw save')
                test.owner[:]=1 # Original returned owner mutates after serialization.
                super().__init__(*args,**kwargs)
        deps=dict(LLM=LLM,SamplingParams=Sampling,RequestOutputKind=Kind,capture_module=module,
            numpy=np,OwnedRouteMemory=Scope,observe_imports=imports,observe_runtime=runtime,Collector=Collector)
        return deps

    def run_arm(self,arm='capture',name='out'):
        self.deps=self.dependencies();self.out=self.base/name
        return worker.run_loaded_arm(self.plan,arm,self.out,_test_dependencies=self.deps)

    def test_three_arms_generate_once_with_exact_parameters_and_unvalidated_mock_artifacts(self):
        for arm in ('native','capture','repeat'):
            self.events=[];result=self.run_arm(arm,arm)
            self.assertEqual(result['status'],'ARM_RETURNED_UNVALIDATED');self.assertEqual(result['provenance'],'MOCK')
            self.assertFalse(result['scientific_validation_passed']);self.assertTrue(result['process_exit_required'])
            calls=[e for e in self.events if isinstance(e,tuple) and e[0]=='generate'];self.assertEqual(len(calls),1)
            self.assertEqual(calls[0][1],[{'prompt_token_ids':self.plan['prompt_token_ids']}]);self.assertFalse(calls[0][3])
            self.assertEqual(calls[0][2],protocol.sampling_arguments(self.deps['RequestOutputKind'].FINAL_ONLY))
            kwargs=next(e[1] for e in self.events if isinstance(e,tuple) and e[0]=='construct')
            self.assertEqual(kwargs,protocol.llm_arguments(str(self.checkpoint),arm!='native'))
            self.assertLess(self.events.index('import-observation'),self.events.index('ownership-enter'))
            self.assertTrue(result['compatibility_restored']);self.assertEqual(self.events[-1],'ownership-restored')
            raw=json.loads((self.out/'raw-return.json').read_bytes());self.assertEqual(raw['request_id'],'owned-real-api-id')
            if arm!='native':
                trace=json.loads((self.out/'trace-summary.json').read_bytes())
                self.assertEqual((trace['event_count'],trace['expert_access_count'],trace['tensor_access_count']),(78,78,234))
            self.assertFalse((self.out/'COMPLETE.json').exists());self.assertFalse((self.out/'DONE').exists())

    def test_raw_route_copy_survives_owner_mutation_and_collector_failure(self):
        expected=self.owner.copy();result=self.run_arm()
        self.assertEqual(result['status'],'ARM_RETURNED_UNVALIDATED')
        np.testing.assert_array_equal(np.load(self.out/'raw-routes.npy',allow_pickle=False),expected)
        events=[json.loads(x) for x in (self.out/'routing.jsonl').read_text().splitlines()]
        self.assertEqual([e['topk_expert_ids'][0] for e in events],expected.reshape(-1).tolist())
        self.failure='collector';result=self.run_arm(name='failed-collector')
        self.assertEqual(result['status'],'FAILED');self.assertTrue((self.out/'raw-return.json').exists())
        self.assertTrue((self.out/'raw-routes.npy').exists());self.assertIn('client-shutdown',self.events)

    def test_partial_construction_records_unreachable_client_and_cleans_reachable_one(self):
        for failure in ('before-engine','after-engine'):
            self.failure=failure;self.events=[];result=self.run_arm(name=failure)
            self.assertEqual(result['status'],'FAILED');self.assertTrue(result['process_exit_required'])
            self.assertEqual(result['engine_cleanup_available'],failure=='after-engine')
            self.assertEqual('client-shutdown' in self.events,failure=='after-engine')
            self.assertIn('ownership-restored',self.events)

    def test_generation_serialization_backend_and_singleton_failures_never_publish_success(self):
        for failure in ('generate','serialize','runtime-observation','wrong-reader'):
            self.failure=failure;self.events=[];result=self.run_arm(name=failure)
            self.assertEqual(result['status'],'FAILED');self.assertIn('client-shutdown',self.events)
            self.assertFalse((self.out/'raw-return.json').exists())

    def test_cleanup_failures_attempt_other_owners_and_preserve_raw(self):
        for failure in ('reader-cleanup','capturer-cleanup','client-shutdown'):
            self.failure=failure;self.events=[];result=self.run_arm(name=failure)
            self.assertEqual(result['status'],'FAILED');self.assertTrue((self.out/'raw-return.json').exists())
            for event in ('reader-cleanup','capturer-cleanup','client-shutdown','ownership-restored'):self.assertIn(event,self.events)
            self.assertTrue(result['cleanup_errors'])

    def test_binding_replacement_is_reported_without_clobbering_it(self):
        self.failure='binding-replaced';result=self.run_arm()
        self.assertEqual(result['status'],'FAILED');self.assertFalse(result['compatibility_restored'])
        self.assertIsNone(self.deps['capture_module'].RoutedExpertsCapturer.get_instance())

    def test_existing_output_and_bad_arm_reject_before_any_runtime_callback(self):
        deps=self.dependencies();out=self.base/'existing';out.mkdir()
        with self.assertRaises(ValueError):worker.run_loaded_arm(self.plan,'bad-arm',out,_test_dependencies=deps)
        with self.assertRaises(FileExistsError):worker.run_loaded_arm(self.plan,'capture',out,_test_dependencies=deps)
        self.assertEqual(self.events,[])

    def test_restoration_observation_failure_still_saves_failed_diagnostics(self):
        self.failure='binding-class-removed';result=self.run_arm()
        self.assertEqual(result['status'],'FAILED');self.assertTrue((self.out/'raw-return.json').exists())
        self.assertEqual(json.loads((self.out/'worker-status.json').read_bytes())['status'],'FAILED')

    def test_parent_alias_change_cannot_publish_provisional_success(self):
        (self.base/'candidate').mkdir();self.failure='output-parent-alias'
        result=self.run_arm(name='candidate/out')
        self.assertEqual(result['status'],'FAILED')
        self.assertTrue((self.base/'moved-candidate/out/raw-return.json').exists())

    def test_native_accepts_installed_absent_scheduler_reader_field(self):
        self.failure='native-reader-absent';result=self.run_arm('native')
        self.assertEqual(result['status'],'ARM_RETURNED_UNVALIDATED')
        self.assertEqual(len([e for e in self.events if isinstance(e,tuple) and e[0]=='generate']),1)

    def test_removed_singleton_field_cannot_skip_owned_cleanup(self):
        self.failure='reader-global-removed';result=self.run_arm()
        self.assertEqual(result['status'],'FAILED')
        for event in ('reader-cleanup','capturer-cleanup','client-shutdown','ownership-restored'):self.assertIn(event,self.events)
        self.assertIs(self.deps['capture_module'].shared_memory,self.scope.original)
        self.assertTrue((self.out/'raw-routes.npy').exists())

    def test_final_status_write_failure_retains_primary_and_closes_directory(self):
        for failure in ('generate',None):
            self.failure=failure;self.events=[];opened=[];original=worker.os.open
            def open_file(path,flags,*args,**kwargs):
                if path=='worker-status.json':raise OSError('TEST_ONLY final status write failure')
                fd=original(path,flags,*args,**kwargs)
                if flags & worker.os.O_DIRECTORY:opened.append(fd)
                return fd
            with mock.patch.object(worker.os,'open',side_effect=open_file):
                result=self.run_arm(name='status-failed-'+str(failure))
            self.assertEqual(result['status'],'FAILED')
            self.assertEqual(result['primary_error']['message'] if failure else result['primary_error'],'generation failed' if failure else None)
            self.assertTrue(any(e['stage']=='status-persistence' for e in result['cleanup_errors']))
            for event in ('reader-cleanup','capturer-cleanup','client-shutdown','ownership-restored'):self.assertIn(event,self.events)
            self.assertFalse((self.out/'worker-status.json').exists())
            for fd in opened:
                with self.assertRaises(OSError):worker.os.fstat(fd)


if __name__=='__main__':unittest.main()
