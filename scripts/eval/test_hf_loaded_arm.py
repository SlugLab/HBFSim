"""Owned-arm composition using fake runtime objects and tiny frozen metadata."""
from dataclasses import replace
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
import hf_moe_tuning as tuning
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
        fixture.prepare_tuning();runtime=fixture.collect_tuning()
        source_report=worker.hf_runtime_sources.validate_runtime_sources(runtime)
        self.assertEqual(len(source_report['artifacts']),133)
        self.assertEqual(source_report['source_extension'],'MOE_TUNING_V1')
        self.device='TEST ONLY GPU'
        (fixture.site/'vllm/model_executor/layers/fused_moe/configs').mkdir()
        metadata=load_hf_snapshot(bundle)
        tuning_snapshot=tuning.collect_tuning_inputs(metadata,runtime,self.device)
        work=self.base/'private-work';(work/'tmp').mkdir(parents=True)
        self.plan=dict(metadata_snapshot=metadata,runtime_snapshot=runtime,
            tuning_snapshot=tuning_snapshot,device_name_declared=self.device,
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
                test.constructed_llm=self
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
            report=dict(schema_version=1,evidence='RUNTIME_CONFIGURATION_OBSERVATION',
                scientific_validation_passed=False,classes={},settings={},instance_id='1234',
                layers=[],kv_layout={},sampler={})
            test.runtime_report=report;return report
        def retain(metadata,runtime_snapshot,tuning_snapshot,device,work,gpu):
            test.events.append(('tuning-retention',metadata,runtime_snapshot,tuning_snapshot,device,work,gpu))
            if test.failure=='tuning-retention':raise RuntimeError('tuning retention failed')
            retained=object();test.retained=retained;return retained
        def observe_tuning(retained,llm,*,prior_runtime_observation):
            test.events.append(('tuning-observation',retained,llm,prior_runtime_observation))
            if test.failure=='tuning-observation':raise RuntimeError('tuning observation failed')
            report=dict(schema_version=1,provenance='MOCK',test_only=True,
                device_name_declared=test.device,selection='INSTALLED_DEFAULTS',
                selected_path='TEST_ONLY/selected.json',scientific_validation_passed=False)
            test.tuning_report=report;return report
        class Collector(JsonlTraceCollector):
            def __init__(self,*args,**kwargs):
                test.events.append('collector-open')
                if test.failure=='collector':raise RuntimeError('collector failed after raw save')
                test.owner[:]=1 # Original returned owner mutates after serialization.
                super().__init__(*args,**kwargs)
        deps=dict(LLM=LLM,SamplingParams=Sampling,RequestOutputKind=Kind,capture_module=module,
            numpy=np,OwnedRouteMemory=Scope,observe_imports=imports,observe_runtime=runtime,
            retain_tuning_runtime=retain,observe_loaded_tuning=observe_tuning,Collector=Collector)
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
            names=[e[0] if isinstance(e,tuple) else e for e in self.events]
            self.assertLess(names.index('import-observation'),names.index('tuning-retention'))
            self.assertLess(names.index('tuning-retention'),names.index('ownership-enter'))
            self.assertLess(names.index('ownership-enter'),names.index('construct'))
            self.assertLess(names.index('construct'),names.index('runtime-observation'))
            self.assertLess(names.index('runtime-observation'),names.index('tuning-observation'))
            self.assertLess(names.index('tuning-observation'),names.index('generate'))
            for name in ('construct','runtime-observation','tuning-observation','generate'):
                self.assertEqual(names.count(name),1)
            retention=next(e for e in self.events if isinstance(e,tuple) and e[0]=='tuning-retention')
            self.assertIs(retention[1],self.plan['metadata_snapshot']);self.assertIs(retention[2],self.plan['runtime_snapshot'])
            self.assertIs(retention[3],self.plan['tuning_snapshot']);self.assertEqual(retention[4],self.device)
            observation=next(e for e in self.events if isinstance(e,tuple) and e[0]=='tuning-observation')
            self.assertIs(observation[1],self.retained);self.assertIs(observation[2],self.constructed_llm)
            self.assertIs(observation[3],self.runtime_report)
            self.assertTrue(result['compatibility_restored']);self.assertEqual(self.events[-1],'ownership-restored')
            self.assertEqual(json.loads((self.out/'runtime-tuning.json').read_bytes()),self.tuning_report)
            binding=json.loads((self.out/'input-binding.json').read_bytes())
            protocol_document=json.loads((self.out/'protocol.json').read_bytes())
            report=tuning.validate_tuning_inputs(self.plan['tuning_snapshot'],self.plan['metadata_snapshot'],
                self.plan['runtime_snapshot'],self.device)
            self.assertEqual(binding['selected_tuning_manifest_sha256'],worker.digest(self.plan['tuning_snapshot'].manifest_bytes))
            self.assertEqual(binding['device_name_declared'],self.device)
            self.assertEqual(binding['selected_tuning_input_report_sha256'],worker.digest(worker.canonical(report)))
            self.assertEqual((protocol_document['source_kind'],protocol_document['provenance']),('TEST_ONLY','MOCK'))
            self.assertEqual((binding['test_only'],binding['provenance']),(True,'MOCK'))
            raw=json.loads((self.out/'raw-return.json').read_bytes());self.assertEqual(raw['request_id'],'owned-real-api-id')
            if arm!='native':
                trace=json.loads((self.out/'trace-summary.json').read_bytes())
                self.assertEqual((trace['event_count'],trace['expert_access_count'],trace['tensor_access_count']),(78,78,234))
                self.assertEqual(trace['evidence_class'],'TEST_ONLY')
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

    def test_tuning_retention_failure_prevents_construction_and_generation(self):
        self.failure='tuning-retention';result=self.run_arm()
        self.assertEqual(result['status'],'FAILED');self.assertEqual(result['primary_error']['stage'],'tuning-retention')
        names=[e[0] if isinstance(e,tuple) else e for e in self.events]
        self.assertNotIn('construct',names);self.assertNotIn('generate',names)
        self.assertFalse(result['engine_cleanup_available']);self.assertFalse((self.out/'runtime-tuning.json').exists())

    def test_tuning_observation_failure_prevents_generation_and_cleans_constructed_state(self):
        self.failure='tuning-observation';result=self.run_arm()
        self.assertEqual(result['status'],'FAILED');self.assertEqual(result['primary_error']['stage'],'tuning-observation')
        names=[e[0] if isinstance(e,tuple) else e for e in self.events]
        self.assertEqual(names.count('construct'),1);self.assertNotIn('generate',names)
        for event in ('reader-cleanup','capturer-cleanup','client-shutdown','ownership-restored'):self.assertIn(event,self.events)
        self.assertFalse((self.out/'runtime-tuning.json').exists())

    def test_wrong_device_and_resealed_tuning_snapshot_reject_before_output_or_callbacks(self):
        for change in ('device','resealed'):
            plan=dict(self.plan);out=self.base/('invalid-'+change)
            if change=='device':plan['device_name_declared']='OTHER TEST GPU'
            else:
                raw=json.loads(plan['tuning_snapshot'].manifest_bytes);raw['device_name_declared']='OTHER TEST GPU'
                plan['tuning_snapshot']=replace(plan['tuning_snapshot'],manifest_bytes=worker.canonical(raw))
            self.events=[];deps=self.dependencies()
            with self.subTest(change=change),self.assertRaises(ValueError):
                worker.run_loaded_arm(plan,'capture',out,_test_dependencies=deps)
            self.assertEqual(self.events,[]);self.assertFalse(out.exists())

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

    def test_metadata_fixture_stays_mock_when_runtime_source_flag_is_false(self):
        self.deps=self.dependencies();self.out=self.base/'metadata-mock'
        source=worker.hf_runtime_sources.validate_runtime_sources(self.plan['runtime_snapshot'])
        source=dict(source);source['test_only']=False
        report=tuning.validate_tuning_inputs(self.plan['tuning_snapshot'],self.plan['metadata_snapshot'],
            self.plan['runtime_snapshot'],self.device);report=dict(report);report['test_only']=False
        # Exercise the source-false branch without importing a real runtime or
        # labelling any fake generated output as genuine runtime evidence.
        with mock.patch.object(worker.hf_runtime_sources,'validate_runtime_sources',return_value=source), \
             mock.patch.object(worker.tuning_api,'validate_tuning_inputs',return_value=report), \
             mock.patch.object(worker,'_loaded_dependencies',return_value=self.deps):
            result=worker.run_loaded_arm(self.plan,'capture',self.out)
        self.assertEqual(result['status'],'ARM_RETURNED_UNVALIDATED')
        self.assertEqual(result['provenance'],'MOCK');self.assertTrue(result['test_only'])

    def test_tuning_test_only_report_forces_mock_when_other_source_flags_are_false(self):
        self.deps=self.dependencies();self.out=self.base/'tuning-mock'
        original_unpack=worker._unpack
        def non_test_metadata(snapshot):
            receipt,artifacts,donor,rest=original_unpack(snapshot);receipt=dict(receipt)
            receipt.update(evidence='CHECKPOINT_METADATA',provenance='CHECKPOINT_METADATA')
            return receipt,artifacts,donor,rest
        report=tuning.validate_tuning_inputs(self.plan['tuning_snapshot'],self.plan['metadata_snapshot'],
            self.plan['runtime_snapshot'],self.device);report=dict(report);report['test_only']=True
        source=worker.hf_runtime_sources.validate_runtime_sources(self.plan['runtime_snapshot'])
        source=dict(source);source['test_only']=False
        with mock.patch.object(worker,'_unpack',side_effect=non_test_metadata), \
             mock.patch.object(worker.hf_runtime_sources,'validate_runtime_sources',return_value=source), \
             mock.patch.object(worker.tuning_api,'validate_tuning_inputs',return_value=report), \
             mock.patch.object(worker,'_loaded_dependencies',return_value=self.deps):
            result=worker.run_loaded_arm(self.plan,'capture',self.out)
        self.assertEqual(result['status'],'ARM_RETURNED_UNVALIDATED')
        self.assertEqual(result['provenance'],'MOCK');self.assertTrue(result['test_only'])


    def test_cuda_route_default_off_and_native_flag_never_import_adapter(self):
        with mock.patch.dict(sys.modules,{'hf_route_cuda_events':None}):
            for arm in ('native','capture'):
                result=self.run_arm(arm,'off-'+arm)
                self.assertEqual(result['status'],'ARM_RETURNED_UNVALIDATED')
                self.assertNotIn('route_cuda_events',result)
                self.assertFalse((self.out/'route-device-events.json').exists())
        self.plan['capture_cuda_route_events']=True
        with self.assertRaisesRegex(ValueError,'plan flag'):
            self.run_arm('native','bad-native')

    def test_cuda_route_sidecar_join_and_failure_restore_precede_owned_cleanup(self):
        from adapters.vllm_capacity.tests.test_hf_route_cuda_events import (
            FakeCuda,FakeCapturer,FakeReader,emit_batches)
        self.plan['capture_cuda_route_events']=True
        for failure in (None,'sync','restore'):
            self.events=[];self.owner=np.arange(78,dtype=np.int32).reshape(39,2,1)%2
            deps=self.dependencies();module=deps['capture_module'];cuda=FakeCuda()
            cap_cls=module.RoutedExpertsCapturer;read_cls=module.RoutedExpertsReader
            def init_cap(cap):FakeCapturer.__init__(cap,cuda,layers=2,top_k=1)
            def init_reader(reader):FakeReader.__init__(reader,module._global_experts_capturer)
            cap_cls.__init__=init_cap;read_cls.__init__=init_reader
            cap_cls.capture=FakeCapturer.capture;cap_cls.save_captured_experts=FakeCapturer.save_captured_experts
            read_cls.get_routed_experts=FakeReader.get_routed_experts
            original_generate=deps['LLM'].generate
            original_cleanup=cap_cls.cleanup
            original_collector=deps['Collector']
            foreign=lambda *args:None
            def generate(llm,*args,**kwargs):
                emit_batches(module._global_experts_capturer,module._global_experts_reader,cuda,
                    self.owner,np.arange(39,dtype=np.int32)*7+5)
                returned=original_generate(llm,*args,**kwargs)
                if failure=='sync':cuda.fail_sync=True
                return returned
            class Collector(original_collector):
                def __init__(self,*args,**kwargs):
                    super().__init__(*args,**kwargs)
                    # Finalize/join has succeeded before trace materialization;
                    # this case now exercises restoration-only failure.
                    if failure=='restore':module._global_experts_capturer.capture=foreign
            def cleanup(cap):
                self.assertNotIn('save_captured_experts',vars(cap))
                if failure=='restore':self.assertIs(cap.capture,foreign)
                else:self.assertNotIn('capture',vars(cap))
                self.events.append('event-restoration-observed-before-owner-cleanup')
                original_cleanup(cap)
            deps['LLM'].generate=generate;cap_cls.cleanup=cleanup;deps['cuda']=cuda;deps['Collector']=Collector
            self.out=self.base/('timed-'+str(failure))
            result=worker.run_loaded_arm(self.plan,'capture',self.out,_test_dependencies=deps)
            event_raw=(self.out/'route-device-events.json').read_bytes();event=json.loads(event_raw)
            self.assertEqual(result['route_cuda_events']['sha256'],worker.digest(event_raw))
            self.assertEqual(event['bindings']['raw_routes_sha256'],worker.digest((self.out/'raw-routes.npy').read_bytes()))
            self.assertEqual(event['bindings']['input_binding_sha256'],worker.digest((self.out/'input-binding.json').read_bytes()))
            self.assertNotIn('capture_cuda_route_events',json.loads((self.out/'input-binding.json').read_bytes()))
            self.assertEqual(event['provenance'],'MOCK')
            self.assertFalse(event['scientific_validation_passed'])
            self.assertIn('event-restoration-observed-before-owner-cleanup',self.events)
            if failure is None:
                self.assertEqual(result['status'],'ARM_RETURNED_UNVALIDATED')
                self.assertEqual(event['status'],'UNVALIDATED_ROUTE_INTERVAL_CAPTURE')
                self.assertEqual(event['counts'],dict(batch_events=16,save_batches=8,reader_calls=1,
                    saved_slots=39,decode_nodes=14,observed_intervals=13,missing_terminal=1))
                self.assertIsNone(event['decode_intervals'][-1]['elapsed_ns'])
            else:
                self.assertEqual(result['status'],'FAILED');self.assertEqual(event['status'],'FAILED')
                if failure=='sync':self.assertIsNotNone(result['primary_error'])
                else:
                    self.assertIsNone(result['primary_error'])
                    stages={row['stage'] for row in result['cleanup_errors']}
                    self.assertTrue({'route-event-restore','route-event-result'}<=stages)


if __name__=='__main__':unittest.main()
