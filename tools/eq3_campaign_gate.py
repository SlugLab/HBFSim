"""Small stage-scope gate, not a user-signature synthesizer or scheduler."""
import hashlib,json,re,copy
from pathlib import Path
from eq3_experiment_gate import _fail,canonical_manifest_hash

def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def canonical(o):return hashlib.sha256(json.dumps(o,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()).hexdigest()
def prefix_ir(ir,end):
    result=copy.deepcopy(ir);power=result['power']
    power['intervals']=[dict(row,end_s=min(end,row['end_s'])) for row in power['intervals'] if row['start_s']<end]
    power['component_energy_j']={cid:sum((row['end_s']-row['start_s'])*row['power_w'].get(cid,0) for row in power['intervals']) for cid in power['component_energy_j']}
    power['total_energy_j']=sum(power['component_energy_j'].values());return result
def resolve(root,path):
    p=(root/path).resolve()
    if root not in p.parents or not p.is_file():_fail('STAGE_PATH_INVALID',path)
    return p

def validate_stage(authorization,manifest,root):
    root=Path(root).resolve()
    if authorization.get('schema_version')!='eq3-stage-authorization-v1' or authorization.get('record_kind')!='USER_STAGE_AUTHORIZATION' or authorization.get('is_test_fixture') is not False:
        _fail('STAGE_AUTH_INVALID','not actual stage confirmation')
    source=authorization['source']
    if source['type']!='codex_user_message' or sha(resolve(root,source['transcript_path']))!=source['transcript_sha256']:
        _fail('STAGE_SOURCE_INVALID','user adoption transcript missing/changed')
    scope_path=resolve(root,authorization['scope_path']);scope=json.loads(scope_path.read_text())
    if sha(scope_path)!=authorization['scope_sha256'] or scope['stage_id']!=authorization['stage_id']:
        _fail('STAGE_SCOPE_CHANGED','stage scope binding changed')
    for p,h in scope['frozen_scientific_inputs'].items():
        if sha(resolve(root,p))!=h:_fail('STAGE_PHYSICS_CHANGED',p)
    matches=[x for x in manifest['inputs'] if x['logical_id']=='stage-child-contract']
    if len(matches)!=1:_fail('STAGE_CHILD_MISSING','requires bound child contract')
    contract=json.loads(resolve(root,matches[0]['path']).read_text())
    if contract['authorization_class']!='AUTHORIZED_BY_USER_STAGE_SCOPE' or contract['stage_id']!=scope['stage_id']:
        _fail('STAGE_CHILD_INVALID','not a derived stage child')
    family=contract['family']
    if family not in scope['allowed_families']:_fail('STAGE_POINT_OUT_OF_SCOPE',family)
    if not re.fullmatch(r'[A-Za-z0-9_.-]+',contract['point_id']):_fail('STAGE_POINT_OUT_OF_SCOPE','invalid id')
    if contract['trace'] not in scope['traces']:_fail('STAGE_TRACE_INVALID','unknown trace')
    if contract['trace']=='new_blind' and not contract.get('model_lock'):_fail('BLIND_LOCK_REQUIRED','not locked')
    required=scope['initial_dependencies'].get(contract['point_id'])
    if required is None and not contract.get('derivation_reason'):_fail('STAGE_CHILD_INVALID','supplement needs recorded causal reason')
    if required and not set(required).issubset({d['id'] for d in contract['dependencies']}):_fail('STAGE_DEPENDENCY_BLOCKED','missing registered predecessor')
    for dep in contract['dependencies']:
        p=resolve(root,dep['path'])
        if sha(p)!=dep['sha256']:_fail('STAGE_DEPENDENCY_CHANGED',dep['path'])
        data=json.loads(p.read_text())
        if data.get('status') not in dep['allowed_status']:_fail('STAGE_DEPENDENCY_BLOCKED',dep['path'])
    if contract.get('model_lock'):
        lock=contract['model_lock'];p=resolve(root,lock['path'])
        if sha(p)!=lock['sha256'] or json.loads(p.read_text()).get('status')!='LOCKED_BEFORE_BLIND':_fail('BLIND_LOCK_REQUIRED','invalid model lock')
    norm=next((x for x in manifest['inputs'] if x['path'].endswith('/normalized.json')),None)
    if not norm:_fail('STAGE_INPUT_MISSING','normalized physical input absent')
    ir=json.loads(resolve(root,norm['path']).read_text())
    expected_hash=scope['traces'][contract['trace']]['normalized_sha256']
    if family in ('rc_pilot','reference_pilot'):
        from eq3_layered_ir import normalize
        paths=scope['frozen_scientific_inputs']
        profile=next(p for p in paths if p.endswith('/candidate_profile.json'));power=next(p for p in paths if p.endswith('/calibration_power.json'))
        full=normalize(json.loads(resolve(root,profile).read_text()),json.loads(resolve(root,power).read_text()),contract['trace'])
        if canonical(full)!=expected_hash:_fail('STAGE_PHYSICS_CHANGED','reconstructed full trace differs')
        end=manifest['scientific_config']['workload_and_initial_state']['duration_s']
        if not 0<end<scope['traces'][contract['trace']]['duration_s']:_fail('STAGE_TRACE_INVALID','resource pilot must be a proper original prefix')
        expected_hash=canonical(prefix_ir(full,end))
    if canonical(ir)!=expected_hash:_fail('STAGE_PHYSICS_CHANGED','IR differs from frozen physical/power/sensor input or exact authorized prefix')
    r=manifest['resource_budget']['requested']
    if r['ram_gib']>12 or r['disk_gib']>4 or r['build_threads']!=1 or r['gpu_compute_minutes']!=0 or r['cpu_configurations']!=1 or r['executions_per_configuration']!=1:_fail('STAGE_RESOURCE_EXCEEDED','stage envelope')
    if contract['limits']!=scope['limits']:_fail('STAGE_RESOURCE_EXCEEDED','child must retain stage safety limits')
    if contract['fit_parameters']!=0 or contract['rom_count']!=0:_fail('STAGE_METHOD_OUT_OF_SCOPE','no fit/ROM')
    if not 0<contract['step_s']<=.02:_fail('STAGE_NUMERICS_INVALID','invalid step')
    ratio=.02/contract['step_s']
    if abs(ratio-round(ratio))>1e-8 or round(ratio)&(round(ratio)-1):_fail('STAGE_NUMERICS_INVALID','step must be dyadic refinement')
    if contract['output_policy'] not in scope['allowed_output_policies']:_fail('STAGE_OUTPUT_OUT_OF_SCOPE','unsupported evidence policy')
    science=manifest['scientific_config']
    if science['time_and_numerics']['step_s']!=contract['step_s']:_fail('STAGE_NUMERICS_INVALID','step differs from scientific manifest')
    trace=scope['traces'][contract['trace']]
    workload=science['workload_and_initial_state']
    expected_energy=ir['power']['total_energy_j'] if family in ('rc_pilot','reference_pilot') else trace['energy_j']
    expected_duration=max(row['end_s'] for row in ir['power']['intervals']) if family in ('rc_pilot','reference_pilot') else trace['duration_s']
    if workload['input_energy_j']!=expected_energy or workload['duration_s']!=expected_duration:_fail('STAGE_TRACE_INVALID','duration or power differs')
    if family in ('reference','reference_pilot'):
        mesh=contract['mesh_um'];ratio=4000/mesh
        if mesh<=0 or abs(ratio-round(ratio))>1e-8 or round(ratio)&(round(ratio)-1):_fail('STAGE_NUMERICS_INVALID','non-dyadic reference grid')
    return {'status':'AUTHORIZED_BY_USER_STAGE_SCOPE','stage_id':scope['stage_id'],'point_id':contract['point_id']}
