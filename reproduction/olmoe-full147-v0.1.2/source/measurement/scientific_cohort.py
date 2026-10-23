"""Cross-attempt scientific cohort and role contract checks."""
from pathlib import Path
import hashlib,json,math,os
COMMON=('model','versions','attention_backend','moe_backend','num_prompts','input_len','output_len','max_model_len','max_num_batched_tokens','seed','warmup_requests','prompt_token_ids','model_file_identity','output_token_ids','output_token_ids_sha256')
TIMING_KEYS=('embedding','final_norm','head')+tuple(f'layer-{i:02d}' for i in range(16))
import runpy as _diag_runpy
_DIAG = _diag_runpy.run_path(str(Path(__file__).resolve().parent / "diag_profile.py"))

def need(v,m):
    if not v: raise RuntimeError(m)
def load(row): return json.loads(Path(row['path']).read_bytes())
def digest(path): return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def timing(report):
    t=report['full_model_layer_timing']
    need(t.get('status')=='PASS' and t.get('schema')=='hbfsim.olmoe_full_model_layer_host_timing.v1' and
         t.get('method')=='cuda_synchronized_host_forward_hooks' and
         t.get('head_boundary')=='logits_processor_including_lm_head_projection_and_postprocessing',
         'timing producer/method/head boundary')
    durations=t.get('durations_ns');calls=t.get('calls')
    need(type(durations) is dict and type(calls) is dict and
         set(durations)==set(TIMING_KEYS) and set(calls)==set(TIMING_KEYS),
         'exact 19 timing duration/call keys')
    need(all(type(calls[k]) is int and calls[k]==2 for k in TIMING_KEYS),'two calls each')
    need(all(type(durations[k]) in (int,float) and not isinstance(durations[k],bool) and
             math.isfinite(durations[k]) and durations[k]>=0 for k in TIMING_KEYS),
         'finite nonnegative timing durations')
    need(t.get('errors')==[],'timing errors are nonempty')
    return t
def compare_common_scientific_cohort(u,n,z):
    for k in COMMON: need(k in u and u[k]==n[k]==z[k],'common scientific cohort: '+k)
    for r in (u,n,z): need(r['scientific_status']=='COMPLETE' and r['request_terminal_status']=='success','complete formal generate')
    return {'common_fields':list(COMMON),'calls':2,'results_equal':True}
def validate_role_contract(role,plan,result):
    if role=='U': need(plan['consumer_policy']=='NATIVE_ONLY','U native role')
    else: need(plan['consumer_policy']=='CANDIDATE_ONLY' and plan['env']['HBFSIM_SWEEP_MEASUREMENT_PLUGIN_V1']=='1' and plan['env']['HBFSIM_COMBINED_MODEL_PLUGIN_V1']=='0','candidate role')
    if role=='D_NOMINAL':
        _DIAG['profile_for'](plan)
        _DIAG['root_revision'](plan)
    if role=='N': need(plan['wait_mode']=='nominal','N role')
    if role=='Z': need(plan['wait_mode']=='zero_injected','Z role')
    timing(result); return {'role':role,'policy':plan['consumer_policy'],'wait_mode':plan['wait_mode']}
def validator_revision_acceptance(nplan,zplan):
    path=os.environ.get('HBFSIM_VALIDATOR_REVISION_ACCEPTANCE_PATH','')
    expected=os.environ.get('HBFSIM_VALIDATOR_REVISION_ACCEPTANCE_SHA256','')
    need(path and len(expected)==64 and digest(path)==expected,
         'external root validator revision acceptance is absent or changed')
    receipt=json.loads(Path(path).read_bytes())
    need(receipt.get('schema')=='hbfsim.root.candidate_validator_revision_acceptance.v1' and
         receipt.get('status')=='ROOT_ACCEPTED_LIMITED_VALIDATOR_REVISION' and
         receipt.get('historical_n_validator_sha256')==nplan['validator_sha256'] and
         receipt.get('current_z_validator_sha256')==zplan['validator_sha256'] and
         receipt.get('allowed_scope')=='candidate-validator-host-guards-only',
         'external root validator revision acceptance scope differs')
    return {'path':path,'sha256':expected,'scope':receipt['allowed_scope']}
def compare_candidate_domain(nplan,zplan):
    keys=('plugin_sha256','runner_sha256','new_agent_sha256','new_daemon_sha256','new_extension_sha256','new_gate_sha256','new_pass_sha256','new_helper_ptx_sha256','framework_cudart12_sha256')
    for k in keys: need(nplan[k]==zplan[k],'N/Z implementation domain: '+k)
    revision=zplan.get('validator_revision_binding',{})
    need(revision.get('schema')=='hbfsim.candidate_validator_revision_binding.v1' and
         revision.get('historical_n_validator_sha256')==nplan['validator_sha256'] and
         revision.get('current_z_validator_sha256')==zplan['validator_sha256'] and
         revision.get('status')=='SOURCE_PENDING_INDEPENDENT_REVIEW',
         'N/Z validator revision is not explicitly bound')
    accepted=validator_revision_acceptance(nplan,zplan)
    return {'shared_identity_keys':list(keys),'validator_revision_binding':revision,
            'validator_revision_root_acceptance':accepted,
            'bound_differences':['validator revision described by source and accepted externally','wait_mode','epoch','fresh identity/root/backing','zero source graph']}

def compare_candidate_range_policy(spec,nplan,zplan,zrun):
    rows={r['name']:r for r in spec['adopted_measurements']['files']}
    pre=json.loads(Path(rows['N_result_cell_normal-backend-pre-generation.json']['path']).read_bytes())
    need(digest(rows['N_result_cell_normal-backend-pre-generation.json']['path'])==rows['N_result_cell_normal-backend-pre-generation.json']['sha256'],
         'adopted N pre-generation bytes changed')
    validation=json.loads((Path(zrun)/'MODEL_VALIDATION.json').read_bytes())
    zobs=validation['normal_live_binding']['actual_range_observation']
    scope_path=Path(zplan['scope_manifest_path']);need(digest(scope_path)==zplan['scope_manifest_sha256'],'Z scope bytes changed')
    aliases=json.loads(scope_path.read_bytes())['registration_aliases']
    nraw=pre['range_observation'];need(len(aliases)==nraw['count']==len(nraw['rows']),'N range/alias count differs')
    need({row.get('range_id') for row in nraw['rows']}==set(range(1,len(aliases)+1)),
         'N range IDs do not bijectively index aliases')
    nrows={aliases[row['range_id']-1]:row for row in nraw['rows']}
    zrows={row['alias']:row for row in zobs['rows']}
    need(set(nrows)==set(zrows),'N/Z range aliases differ')
    fields=('length','file_offset','page_bytes','mode','permissions','cache_policy','stream_id','flags','reserved1')
    for alias in nrows:
        need(all(nrows[alias][key]==zrows[alias][key] for key in fields),
             'N/Z range policy differs: '+alias)
    for key in ('scope_manifest_sha256','full9_manifest_sha256','mqsim_native_page_bytes','application_request_bytes'):
        need(nplan[key]==zplan[key],'N/Z range profile identity: '+key)
    for key in ('HBFSIM_RANGE_LAYOUT_SHA256','HBFSIM_RANGE_LAYOUT_BUILD_RECEIPT_SHA256','HBFSIM_RANGE_PRODUCTION_BUILD_RECEIPT_SHA256'):
        need(nplan['env'][key]==zplan['env'][key],'N/Z range layout identity: '+key)
    need(nraw['layout_sha256']==zobs['layout_sha256']==zplan['env']['HBFSIM_RANGE_LAYOUT_SHA256'],
         'N/Z observed range layout differs')
    return {'aliases':len(nrows),'compared_fields':list(fields),'fresh_fields_not_compared':['base','target_pid','target_start_ticks','loaded inode','run roots','zero mode']}
