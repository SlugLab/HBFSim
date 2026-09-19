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
    def test_reject_outside_family(self):
        self.c['family']='GPU';self.write('child.json',json.dumps(self.c))
        with self.assertRaises(GateError):self.check()

if __name__=='__main__':unittest.main()
