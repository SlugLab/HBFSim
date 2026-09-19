"""Freeze one child of an actually user-authorized campaign. Never execute."""
import argparse,copy,hashlib,json,sys
from pathlib import Path
from eq3_experiment_gate import canonical_manifest_hash,verify_artifacts,observed_git_state,validate_gate

def sha(p):
    with p.open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()
def prepare(root,point,generated,trace,step,mesh,policy,family,deps,reason='',binary=None,model_lock=None):
    code=root/'eq3_thermal/worktree';campaign=root/'eq3_thermal/plans/campaign-v1';plan=campaign/'points'/point
    scope=json.loads((campaign/'scope.json').read_text());g=generated.resolve();r=json.loads((g/'generation_receipt.json').read_text())
    base=root/'eq3_thermal/plans/layered-R02-v1';m=json.loads((base/'experiment_manifest.json').read_text());l=json.loads((base/'launch.json').read_text())
    def art(p,role,identity=None):
        rel=p.resolve().relative_to(root).as_posix();return {'path':rel,'logical_id':identity or rel,'semantic_role':role,'sha256':sha(p)}
    def write(name,obj):
        with (plan/name).open('x') as f:json.dump(obj,f,indent=2)
    rev,diff=observed_git_state(code)
    if diff!=hashlib.sha256(b'').hexdigest():raise ValueError('commit changes before freezing child')
    contract={'stage_id':scope['stage_id'],'authorization_class':'AUTHORIZED_BY_USER_STAGE_SCOPE','point_id':point,'family':family,'trace':trace,'step_s':step,'mesh_um':mesh,'limits':scope['limits'],'fit_parameters':0,'rom_count':0,'output_policy':policy,'derivation_reason':reason,'dependencies':deps,'model_lock':model_lock,'purpose':'same-physics numerical validation; see registered family/dependencies','changes_from_parent':reason or 'registered numerical point','expected_runtime':'UNKNOWN; watchdog600s, not an estimate','expected_information':'reference discretization or same-equation RC validation, not physical calibration'}
    plan.mkdir(parents=True,exist_ok=False);(plan/'runs').mkdir();write('child.json',contract)
    files=[art(p,'generated physical/numerical input') for p in sorted(g.iterdir()) if p.is_file()]
    original_engine=m['dependencies'][0]
    l.update(experiment_id='EQ3-P2-'+point,version='campaign-v1',run_id=point,artifacts=files)
    l['command']['cwd']=g.relative_to(root).as_posix();l['output']['path']=(plan/'runs'/point).relative_to(root).as_posix()
    if family in ('rc','rc_pilot'):
        l['backend']=art(binary,'sparse same-equation RC backend','rc-backend');l['command']['argv']=[l['backend']['path'],'--run','--model','model.txt','--events','events.txt','--step-s',str(step),'--slot-s','.5','--sample-s','.1','--end-s',str(r['duration_s']),'--min-k','300','--max-k','400']
    elif policy=='lossless_gzip':
        l['backend']=art(code/'tools/eq3_campaign_stream.py','byte-lossless output adapter','stream-backend');nx,ny,nz=r['reference_shape'];l['command']['argv']=[l['backend']['path'],'--backend',original_engine['path'],'--stack','package.stk','--layers',str(nz),'--frames',str(round(r['duration_s']/step)),'--nx',str(nx),'--ny',str(ny),'--watchdog','600']
    else:l['command']['argv']=[l['backend']['path'],'package.stk']
    write('launch.json',l)
    m.update(experiment_id=l['experiment_id'],version=l['version'],approval_status='USER_APPROVED')
    # USER_APPROVED describes stage eligibility; not a fabricated per-point signature.
    m['code']={'repository_revision':rev,'dirty_diff_sha256':diff,'artifacts':[art(code/p['path'].split('eq3_thermal/worktree/')[1],'source') for p in m['code']['artifacts']]}
    seen={x['path'] for x in m['code']['artifacts']}
    for p in sorted((code/'tools').glob('eq3_campaign*')):
        if p.is_file() and p.relative_to(root).as_posix() not in seen:m['code']['artifacts'].append(art(p,'stage execution source'))
    if l['backend']['logical_id']!=original_engine['logical_id']:m['dependencies'].append(l['backend'])
    m['inputs']=files+[p for p in m['inputs'] if p['semantic_role']=='source scientific input']+[art(plan/'child.json','stage derived child scope','stage-child-contract'),art(plan/'launch.json','single run launch binding','layered-launch-manifest')]
    # Preserve original fixed-test evidence; current campaign regression is also bound.
    m['prerequisites']=[m['prerequisites'][0],{'id':'campaign-regression','status':'PASSED','evidence':art(campaign/'software-tests.log','fixed regression raw evidence')}]
    s=m['scientific_config'];s['workload_and_initial_state'].update(trace=trace,duration_s=r['duration_s'],input_energy_j=r['input_energy_j'])
    s['geometry_materials_boundaries'].update(shape=r['reference_shape'],cells=r['reference_cells'],rc_nodes=r['rc_nodes'])
    s['time_and_numerics'].update(step_s=step)
    s['scan_matrix_and_repetitions']={'runs':[{'point':point,'trace':trace,'step_s':step,'mesh_um':mesh,'repeat':1}],'automatic_followup':'stage dependencies, not unconditional','authorization_class':'AUTHORIZED_BY_USER_STAGE_SCOPE'}
    s['acceptance_abort_and_outputs'].update(outputs=l['output'],raw_full_field_retained=family=='reference',output_policy=policy,resource_estimate='updated per child after measured predecessor; memory/time UNKNOWN',field_bytes_estimate=r['reference_field_bytes_estimated'])
    m['execution_context']={'stage_id':scope['stage_id'],'code_root':code.relative_to(root).as_posix(),'environment_id':'eq3-thermal-reference-v1','authorization_class':'AUTHORIZED_BY_USER_STAGE_SCOPE'}
    m['canonical_manifest_hash']=canonical_manifest_hash(m);verify_artifacts(m,root)
    auth=json.loads((campaign/'authorization.json').read_text());validate_gate(m,auth,root,observed_code_revision=rev,observed_dirty_diff_sha256=diff)
    write('experiment_manifest.json',m);print(json.dumps({'point':point,'manifest_hash':m['canonical_manifest_hash'],'status':'AUTHORIZED_BY_USER_STAGE_SCOPE','launch_performed':False}));return plan

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--root',type=Path,required=True);p.add_argument('--point',required=True);p.add_argument('--generated',type=Path,required=True);p.add_argument('--trace',required=True);p.add_argument('--step',type=float,required=True);p.add_argument('--mesh',type=float,default=0);p.add_argument('--policy',choices=['full_text','lossless_gzip'],required=True);p.add_argument('--family',choices=['reference','rc','rc_pilot','numerical_refinement'],required=True);p.add_argument('--dependencies',type=Path,required=True);p.add_argument('--reason',default='');p.add_argument('--binary',type=Path);a=p.parse_args();prepare(a.root.resolve(),a.point,a.generated,a.trace,a.step,a.mesh,a.policy,a.family,json.loads(a.dependencies.read_text()),a.reason,a.binary)
