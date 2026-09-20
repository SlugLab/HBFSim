import copy,json,tempfile,unittest,hashlib
from pathlib import Path
from eq3_campaign_gate import validate_stage,canonical,sha
from eq3_experiment_gate import GateError

class StageGateTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        self.write('user.md','actual test double user text');self.write('science.json','{}');self.write('normalized.json','{}')
        self.scope={'stage_id':'S','frozen_scientific_inputs':{'science.json':sha(self.root/'science.json')},'allowed_families':['reference'],'traces':{'train':{'normalized_sha256':canonical({}),'duration_s':100,'energy_j':1365}},'limits':{'task':16},'allowed_output_policies':['full_text'],'initial_dependencies':{'R02':['R01']}}
        self.write('dep.json',json.dumps({'status':'PASS'}))
        self.c={'authorization_class':'AUTHORIZED_BY_USER_STAGE_SCOPE','stage_id':'S','family':'reference','point_id':'R02','trace':'train','dependencies':[{'id':'R01','path':'dep.json','sha256':sha(self.root/'dep.json'),'allowed_status':['PASS']}],'limits':{'task':16},'fit_parameters':0,'rom_count':0,'step_s':.02,'mesh_um':2000,'output_policy':'full_text'}
        self.write('scope.json',json.dumps(self.scope));self.write('child.json',json.dumps(self.c))
        self.a={'schema_version':'eq3-stage-authorization-v1','record_kind':'USER_STAGE_AUTHORIZATION','is_test_fixture':False,'stage_id':'S','scope_path':'scope.json','scope_sha256':sha(self.root/'scope.json'),'source':{'type':'codex_user_message','transcript_path':'user.md','transcript_sha256':sha(self.root/'user.md')}}
        self.m={'inputs':[{'logical_id':'stage-child-contract','path':'child.json'},{'logical_id':'norm','path':'x/normalized.json'}],'resource_budget':{'requested':{'ram_gib':12,'disk_gib':4,'build_threads':1,'gpu_compute_minutes':0,'cpu_configurations':1,'executions_per_configuration':1}},'scientific_config':{'time_and_numerics':{'step_s':.02},'workload_and_initial_state':{'duration_s':100,'input_energy_j':1365}}}
        (self.root/'x').mkdir();self.write('x/normalized.json','{}')
    def tearDown(self):self.tmp.cleanup()
    def write(self,p,s):(self.root/p).write_text(s)
    def check(self):return validate_stage(self.a,self.m,self.root)
    def test_scope_double_only_no_launch(self):self.assertEqual(self.check()['status'],'AUTHORIZED_BY_USER_STAGE_SCOPE')
    def test_reject_modified_science(self):
        self.write('science.json','{"power":2}')
        with self.assertRaises(GateError):self.check()
    def test_reject_missing_dependency(self):
        self.c['dependencies']=[];self.write('child.json',json.dumps(self.c))
        with self.assertRaises(GateError):self.check()
    def test_reject_unlocked_blind(self):
        self.c['trace']='new_blind';self.write('child.json',json.dumps(self.c))
        with self.assertRaises(GateError):self.check()
    def test_reject_ram16_per_process(self):
        self.m['resource_budget']['requested']['ram_gib']=16
        with self.assertRaises(GateError):self.check()
    def enable_per_experiment_resources(self):
        self.scope['resource_policy']='PER_EXPERIMENT_USER_CONFIRMED'
        self.scope['limits']={'task_ram_gib':24,'process_ram_gib':16,'threads':1,'watchdog_s':3600,
          'point_disk_gib':6,'task_disk_gib':40,'min_free_disk_gib':10,'gpu':0,'cloud':0}
        self.c['limits']=self.scope['limits'];self.m['resource_budget']['requested'].update(ram_gib=16,disk_gib=6)
        self.write('scope.json',json.dumps(self.scope));self.a['scope_sha256']=sha(self.root/'scope.json');self.write('child.json',json.dumps(self.c))
    def test_explicit_per_experiment_resources_replace_legacy_caps(self):
        self.enable_per_experiment_resources();result=self.check()
        self.assertEqual(result['resource_policy'],'PER_EXPERIMENT_USER_CONFIRMED')
        self.assertEqual(result['resource_limits']['watchdog_s'],3600)
    def test_per_experiment_child_must_bind_exact_scope_limits(self):
        self.enable_per_experiment_resources();self.c['limits']['watchdog_s']=3599;self.write('child.json',json.dumps(self.c))
        with self.assertRaises(GateError):self.check()
    def test_per_experiment_resources_reject_nonfinite(self):
        self.enable_per_experiment_resources();self.scope['limits']['watchdog_s']=float('inf');self.c['limits']=self.scope['limits']
        self.write('scope.json',json.dumps(self.scope));self.a['scope_sha256']=sha(self.root/'scope.json');self.write('child.json',json.dumps(self.c))
        with self.assertRaises(GateError):self.check()
    def test_resource_values_without_explicit_policy_keep_legacy_caps(self):
        self.scope['limits']={'task_ram_gib':24,'process_ram_gib':16,'threads':1,'watchdog_s':3600,
          'point_disk_gib':6,'task_disk_gib':40,'min_free_disk_gib':10,'gpu':0,'cloud':0}
        self.c['limits']=self.scope['limits'];self.m['resource_budget']['requested']['ram_gib']=16
        self.write('scope.json',json.dumps(self.scope));self.a['scope_sha256']=sha(self.root/'scope.json');self.write('child.json',json.dumps(self.c))
        with self.assertRaises(GateError):self.check()
    def test_reject_outside_family(self):
        self.c['family']='GPU';self.write('child.json',json.dumps(self.c))
        with self.assertRaises(GateError):self.check()

    def reference_prefix(self,end=4.,mesh=1000,corrupt=False):
        from unittest.mock import patch
        from eq3_campaign_gate import prefix_ir
        full={'power':{'intervals':[{'start_s':0.,'end_s':100.,'power_w':{'x':13.65}}],
            'component_energy_j':{'x':1365.},'total_energy_j':1365.}}
        self.scope['allowed_families'].append('reference_pilot')
        self.scope['traces']['train']['normalized_sha256']=canonical(full)
        (self.root/'config').mkdir()
        for name in ('config/candidate_profile.json','config/calibration_power.json'):
            self.write(name,'{}');self.scope['frozen_scientific_inputs'][name]=sha(self.root/name)
        self.write('scope.json',json.dumps(self.scope));self.a['scope_sha256']=sha(self.root/'scope.json')
        ir=prefix_ir(full,end)
        if corrupt:ir['power']['intervals'][0]['power_w']={'x':20.}
        self.write('x/normalized.json',json.dumps(ir))
        self.c.update(family='reference_pilot',point_id='REFERENCE-PREFIX',derivation_reason='exact-prefix resource diagnostic',mesh_um=mesh)
        self.write('child.json',json.dumps(self.c))
        self.m['scientific_config']['workload_and_initial_state'].update(duration_s=end,input_energy_j=ir['power']['total_energy_j'])
        with patch('eq3_layered_ir.normalize',return_value=full):return self.check()

    def test_reference_prefix_exact_pass(self):
        self.assertEqual(self.reference_prefix()['status'],'AUTHORIZED_BY_USER_STAGE_SCOPE')
    def test_reference_prefix_reject_changed_power(self):
        with self.assertRaises(GateError):self.reference_prefix(corrupt=True)
    def test_reference_prefix_reject_full_duration(self):
        with self.assertRaises(GateError):self.reference_prefix(end=100.)
    def test_reference_prefix_reject_non_dyadic_mesh(self):
        with self.assertRaises(GateError):self.reference_prefix(mesh=700)

if __name__=='__main__':unittest.main()
