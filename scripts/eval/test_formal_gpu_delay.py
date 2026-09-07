"""CPU contract checks; never launch a GPU or create scientific evidence."""
import copy
import json
import pathlib
import tempfile
import unittest
from unittest import mock

import formal_gpu_delay as formal
from run_gpu_delay import make_plan
import test_run_gpu_delay


class FormalD0Tests(unittest.TestCase):
    def setUp(self):
        self.plan = make_plan(formal.ROOT/'docs/49-eval-audit/run-matrix.csv', 'gpu_delay-00003')

    def test_only_original_d0_k64_slice(self):
        formal.require_d0(self.plan)
        for field, value in [('hops', 1), ('delay_ns', 500)]:
            altered=copy.deepcopy(self.plan); altered[field]=value
            with self.assertRaises(ValueError): formal.require_d0(altered)
        altered=copy.deepcopy(self.plan); altered['condition']['cell_id']='gpu_delay-00004'
        with self.assertRaises(ValueError): formal.require_d0(altered)

    def test_dead_or_absent_scheduler_never_authorizes_producer(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises((ValueError, OSError)):
                formal.scheduler_context(pathlib.Path(tmp))

    def test_rows_bind_exact_condition_and_keep_signed_noise(self):
        report=dict(per_access_delta_ns=[-900, 100],event_delta_ns=-1200)
        rows=formal.result_rows(self.plan,report,'gpu_delay-00003-r001',
                                dict(git_sha='a'*40,branch='test'),'GPU-test')
        by_metric={r['metric']:r for r in rows}
        self.assertEqual(by_metric['critical_delta_us']['value'],'-0.4')
        self.assertEqual(by_metric['event_delta_us']['value'],'-1.2')
        self.assertEqual(by_metric['checksum_ok']['value'],'1')
        self.assertTrue(all(r['profile']=='hbf_logical' and r['replicate']=='1' for r in rows))

    def test_payload_hash_change_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=pathlib.Path(tmp); (root/'raw.json').write_text('{}')
            hashes={'raw.json':formal.sha256(root/'raw.json')}
            formal.verify_payloads(root,hashes)
            (root/'raw.json').write_text('{"changed":true}')
            with self.assertRaises(ValueError): formal.verify_payloads(root,hashes)

    def cases(self):
        return test_run_gpu_delay.DelayRunnerTests().fixture(0)

    def write_cases(self, root, cases):
        for name,case in cases.items():
            (root/name).mkdir()
            (root/name/'raw.json').write_text(json.dumps(case))
            (root/name/'coverage.jsonl').write_text('{"allowed":true,"modeled":true}\n')

    def test_test_only_raw_rejected_even_when_arithmetic_valid(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=pathlib.Path(tmp);self.write_cases(root,self.cases())
            with self.assertRaisesRegex(ValueError,'evidence'):
                formal.validate_cases(root,self.plan,'legacy')

    def test_d0_noise_has_no_nonzero_claim_threshold(self):
        cases=self.cases()
        # Physical tags here exercise the parser only; no receipt is published.
        for case in cases.values():case['evidence']='GPU_ACQUISITION'
        cases['target']['chains'][0]['end_ns']+=64*10000
        with tempfile.TemporaryDirectory() as tmp:
            root=pathlib.Path(tmp);self.write_cases(root,cases)
            report=formal.validate_cases(root,self.plan,'legacy')
            self.assertIsNone(report['g2_cell_pass'])
            self.assertFalse(report['g2_gate_closed'])
            self.assertEqual(report['zero_delay_absolute_noise_ns'],[10000])
            cases['target']['covered_bytes']=0
            (root/'target/raw.json').write_text(json.dumps(cases['target']))
            with self.assertRaisesRegex(ValueError,'coverage'):
                formal.validate_cases(root,self.plan,'legacy')

    def test_mock_pilot_cannot_generate_pass_receipt(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=pathlib.Path(tmp)
            (root/'manifest.json').write_text('{"evidence":"TEST_ONLY"}')
            (root/'status.json').write_text('{"state":"DONE"}')
            with self.assertRaisesRegex(ValueError,'physical acquisition'):
                formal.validate_pilot(root)

    def test_no_owner_means_no_arm_process(self):
        with tempfile.TemporaryDirectory() as tmp,mock.patch.object(formal.subprocess,'Popen') as launch:
            with self.assertRaisesRegex(ValueError,'owner exited'):
                formal.run_arm(['/bin/true'],{},pathlib.Path(tmp),None)
            launch.assert_not_called()

    def test_actual_cpu_child_is_acknowledged_and_has_observed_exit(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=pathlib.Path(tmp)
            formal.run_arm(['/bin/true'],dict(formal.os.environ),root,
                           formal.process_identity(formal.os.getpid()))
            ownership=json.loads((root/'ownership.json').read_text())
            exited=json.loads((root/'exit.json').read_text())
            self.assertEqual(exited['exit_code'],0)
            self.assertTrue(exited['observed'])
            self.assertEqual(ownership['child']['pid'],exited['child']['pid'])

    def test_owner_death_before_ack_never_executes_payload(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=pathlib.Path(tmp);marker=root/'must-not-exist'
            argv=[formal.sys.executable,'-c','from pathlib import Path; Path('+repr(str(marker))+').touch()']
            with mock.patch.object(formal,'identity_alive',side_effect=[True,False]):
                with self.assertRaisesRegex(ValueError,'before acknowledgement'):
                    formal.run_arm(argv,dict(formal.os.environ),root,formal.process_identity(formal.os.getpid()))
            self.assertFalse(marker.exists())

    def test_fifo_payload_rejected_before_hash_read(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=pathlib.Path(tmp);formal.os.mkfifo(root/'raw.pipe')
            with self.assertRaises(ValueError):
                formal.verify_payloads(root,{'raw.pipe':'a'*64})

    def test_independent_validator_recomputes_rows_and_rejects_changed_metric(self):
        with tempfile.TemporaryDirectory() as tmp:
            attempt=pathlib.Path(tmp);root=attempt/'raw.triplet';root.mkdir()
            cases=self.cases()
            for case in cases.values():case['evidence']='GPU_ACQUISITION'
            self.write_cases(root,cases)
            child=dict(pid=123,boot_id='fixture-only',start_time=456)
            for name in cases:
                (root/name/'ownership.json').write_text(json.dumps(dict(child=child)))
                (root/name/'exit.json').write_text(json.dumps(dict(child=child,exit_code=0,observed=True,owned_remaining=[])))
            config=dict(matrix=self.plan['matrix'],gpu_uuid='GPU-fixture-only',trace_mode='legacy',
                        artifacts=[dict(path=str(formal.SOURCE),sha256=formal.sha256(formal.SOURCE),role='build')])
            config_path=attempt/'config.json';config_path.write_text(json.dumps(config))
            manifest=dict(condition=self.plan['condition'],replicate=1,run_id='gpu_delay-00003-r001',
                          git=dict(git_sha='a'*40,branch='CPU_FIXTURE'))
            (attempt/'manifest.json').write_text(json.dumps(manifest))
            report=formal.validate_cases(root,self.plan,'legacy')
            files,_=formal.artifact_inventory(root)
            acquisition=dict(evidence='GPU_ACQUISITION',plan=self.plan,config_sha256=formal.sha256(config_path),
                run_id=manifest['run_id'],gpu_uuid=config['gpu_uuid'],trace_mode='legacy',analysis=report,
                artifacts={name:formal.sha256(path) for name,path in files.items()})
            (attempt/'raw.acquisition.json').write_text(json.dumps(acquisition))
            rows=formal.result_rows(self.plan,report,manifest['run_id'],manifest['git'],config['gpu_uuid'])
            (attempt/'raw.results.json').write_text(json.dumps(rows))
            self.assertFalse(formal.validate_attempt(attempt,config_path)['g2_gate_closed'])
            rows[0]['value']='999'
            (attempt/'raw.results.json').write_text(json.dumps(rows))
            with self.assertRaisesRegex(ValueError,'raw recomputation'):
                formal.validate_attempt(attempt,config_path)


if __name__=='__main__': unittest.main()
