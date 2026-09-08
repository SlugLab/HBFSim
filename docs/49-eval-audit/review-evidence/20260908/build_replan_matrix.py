#!/usr/bin/env python3
"""Generate reviewed planning cells; never launches experiments or fabricates results."""
import csv,json,hashlib,itertools
from pathlib import Path
root=Path('/root/hbfsim-exp/eval-base-integration'); report=root.parent/'reports/eq-replan-20260908'; rows=[]
SHA='254d65a66279fbaffc5c185d04fbe41dc8dbba44'
def add(cid,eq,group,status,entry=None,params=None,gate='REPLAN_REGISTRATION',resource='CPU_ONLY',repeats=1,formal=3,cap=120,out_mib=100,**extra):
    p=Path(entry) if entry else None
    if p and not p.is_absolute(): p=(report/entry if (report/entry).exists() else root/entry)
    if p: assert p.exists(), p
    row=dict(cell_id=cid,eq=eq,group=group,branch='eval/eq1-eq4-implementation',git_sha=SHA,implementation_status=status,blocking_gate=gate+';REPLAN_REVIEWED_REGISTRY',repeats=repeats,confirmation_repeats_min=formal,resource_class=resource,entrypoint=str(p) if p else '',entrypoint_sha256=hashlib.sha256(p.read_bytes()).hexdigest() if p and p.is_file() else '',parameters_json=json.dumps(params or {},separators=(',',':')),argv_json='[]',initial_state='fresh run; source/build/input hashes frozen; cold/warm state explicitly paired; model time starts at 0 where applicable',pairing='same frozen original demand/seed/profile/initial state; completion-driven arrival may change',pilot_wall_cap_seconds=cap,pilot_rss_cap_MiB=2048,pilot_output_cap_MiB=out_mib,cost_provenance='PLANNING_CAP_NOT_MEASUREMENT',pilot_measured='no',time_scale=1,execution_authorized_this_round='no',provenance_after_gate='PROJECTED',evidence_kind='literature_constrained_projection',failure_rule='Preserve failure; no data substitution; stop expansion on gate/mismatch/OOM/timeout/mount/space failure',minimum_configuration='yes',**extra)
    rows.append(row); return row
for x in json.loads((report/'eq1-experiments.json').read_text())['rows']:
    entry='eq1-oracle-check.py' if x['cell_id']=='Q1-ORACLE' else x['entrypoint']
    row=add(x['cell_id'],'EQ1','eq1_'+x['cell_id'][3:].lower().replace('-','_'),x['status'],entry,x['parameters'],gate=';'.join(x['dependencies']) or 'G0',resource=x['resource_class'],formal=10,cap=x['pilot_budget_cap_seconds'],out_mib=x['pilot_artifact_cap_MiB'])
    row.update(evidence_kind=x['evidence_kind'].lower().replace(' ','_').replace('-','_'),execution_authorized_this_round='completed_cpu_only' if x['execution_this_round'] else 'no')
    if x['execution_this_round']:
        argv=['/opt/miniconda3/bin/python3',row['entrypoint']]
        if x['cell_id']=='Q1-CPU':argv.append(str(report/'eq1-future-driver'))
        row.update(pilot_measured='yes',provenance_after_gate='MOCK',evidence_kind='mock',scope='CPU engineering with synthetic fixtures or analytical arithmetic only; not a performance measurement',argv_json=json.dumps(argv,separators=(',',':')))
        row['pilot_rss_cap_MiB']=1024 if x['cell_id']=='Q1-CPU' else 128
# Expanded D/W tranche. W is calibrated around two observed knees; arbitrary K is not called W.
for d,w,arm in itertools.product([0,.5,1,2,5,10,20],['below_L0','between_L0_L1','above_L1'],['native','instrumented_zero','old_issue_stall','deferred']):
    status='BLOCKED_IDENTIFIABLE_W_AND_GENERAL_HARNESS'
    if d==0 and w=='between_L0_L1':status='INFEASIBLE_EMPTY_INTERVAL'
    add(f'eq1-d{str(d).replace(".","p")}-{w}-{arm}','EQ1','eq1_incremental_stall',status,params={'extra_D_us':d,'L0':'native pilot measured','L1':'L0+D','W_rule':w,'arm':arm,'heldout':'after pilot freeze'},gate='Q1-STRUCTURE;Q1-OVERLAP',resource='GPU_EXCLUSIVE',formal=10,cap=120)
for mlp,occ in itertools.product([1,2,4,8,16,32],['low','medium','high']):
    add(f'eq1-mlp{mlp}-{occ}','EQ1','eq1_contention','BLOCKED_HARNESS_AND_BASE_GATE',params={'MLP_QD':mlp,'occupancy_requested':occ,'D':'minimum identifiable pilot delay','W':'held-out knee-near window'},gate='Q1-OVERLAP;Q1-STRUCTURE',resource='GPU_EXCLUSIVE',formal=10)
thermal=json.loads((report/'thermal-eq2-cells.json').read_text())['cells']
for x in thermal:
    row=add(x['cell_id'],'EQ2','eq2_controlled_history' if x['entrypoint'] else 'eq2_missing_mechanism',x['status'],x['entrypoint'],params={k:x[k] for k in ['mode','seed','model_duration_ns','input_files','pilot_override'] if k in x},gate=';'.join(x['dependencies']),formal=3)
    row.update(git_sha=x.get('source_sha',SHA),branch='historical_prototype_49f9b2d' if x['entrypoint'] else 'eval/eq1-eq4-implementation',scope=x.get('scope',x.get('reason','')),evidence_kind='controlled_test_only_model',argv_json=json.dumps(([x['entrypoint']]+x['argv']) if x['entrypoint'] else [],separators=(',',':')))
    if x['entrypoint']:
        historical=[x['entrypoint']]+x['argv']; pilot=list(historical)
        pilot[pilot.index('--duration-ns')+1]='100000000'
        pilot[pilot.index('--output')+1]+='-pilot'
        row.update(historical_replay_argv_json=json.dumps(historical,separators=(',',':')),pilot_argv_json=json.dumps(pilot,separators=(',',':')),argv_json=json.dumps(pilot,separators=(',',':')))
    if x['cell_id']=='eq2-live-llm':
        row.update(resource_class='GPU_EXCLUSIVE',pilot_wall_cap_seconds=300,pilot_rss_cap_MiB=4096,pilot_output_cap_MiB=256)
for stack,domain,arm in itertools.product([8,16],['negative_control','near_boundary','sustained_possible_crossing'],['off','shadow','active']):
    add(f'eq2-{stack}hi-{domain}-{arm}','EQ2','eq2_nominal','BLOCKED_PHYSICAL_INPUTS_TRUE_OFF_COMPLETION_BINS',params={'die_stack':stack,'domain':domain,'arm':arm,'duration':'at least 5*tau of frozen ROM plus 100ms analysis windows','nominal_threshold':'freeze from applicable product evidence; no lowering to force separation'},gate='EQ2_PHYSICAL_INPUTS;EQ2_TRUE_OFF_RUNNER;EQ2_COMPLETION_BIN_ACCOUNTING',formal=3)
# Capacity hypotheses are requested design points; mapping to achievable resources is gated.
profiles=[('slow',20,256),('middle',5,1536),('fast',1,4883)]
for name,tr,n in profiles:
    for path,r in itertools.product(['fixed_total','fixed_HBM'],[1,2,3,4,6,8,12,16,24,32]):
        add(f'eq3-r-{path}-{name}-{r}','EQ3','eq3_physical_capacity','BLOCKED_TOPOLOGY_AND_BUDGET_SERVICE_BINDING',params={'r':r,'capacity_path':path,'C_total_GiB_requested':128 if path=='fixed_total' else None,'C_HBM_GiB_requested':16 if path=='fixed_HBM' else None,'capacity_reference':'PROJECTED_DESIGN_POINT_NOT_HOST_GDDR_CAPACITY','media_profile':name,'tR_us':tr,'N_requested':n,'N_unit':'independent sense resources; mapping unverified','scaling':'capacity_only','thermal':'fixed shadow/off after parity gate'},gate='NC-TOPOLOGY;NC-BUDGET;G6',formal=5)
    for k in [0,.1,.2,.3,.4,.5,.6,.7,.8]:
        add(f'eq3-kappa-{name}-{int(k*10)}','EQ3','eq3_hbm_allocation','BLOCKED_STAGING_RESERVATION_API',entry='scripts/eval/budget_fast_tier.py',params={'kappa':k,'beta':.08,'r':8,'C_HBM_GiB_requested':16,'profile':name,'tR_us':tr,'N_requested':n,'kind':'RESERVATION_ABLATION','existing_entrypoint_scope':'partial accounting only; does not implement these axes'},gate='NC-BUDGET;NC-STAGING;G6',formal=5)
    for b in [0,.01,.02,.04,.08,.12,.16,.24,.32]:
        add(f'eq3-beta-{name}-{int(b*100)}','EQ3','eq3_hbm_allocation','INFEASIBLE_NO_LEGAL_ZERO_STAGING' if b==0 else 'BLOCKED_STAGING_RESERVATION_API',entry='scripts/eval/budget_fast_tier.py',params={'kappa':.4,'beta':b,'r':8,'C_HBM_GiB_requested':16,'profile':name,'tR_us':tr,'N_requested':n,'kind':'RESERVATION_ABLATION','zero_staging_rule':'requires demonstrated no-staging implementation'},gate='NC-BUDGET;NC-STAGING;G6',formal=5)
    for case in ['all_HBM','physically_coupled_capacity','real_KV_context','thermal_boundary_slice']:
        add(f'eq3-{name}-{case}','EQ3','eq3_controls','BLOCKED_REQUIRED_MATCHED_IMPLEMENTATION',params={'case':case,'profile':name,'r':0 if case=='all_HBM' else 8,'actual_fit_required':True,'media_scaling_changes':'freeze die/channel/power if coupled','thermal':'active only at selected already measured boundary'},gate='NC-TOPOLOGY;NC-BUDGET;G6;NT-PAIR',formal=5)
# Candidate tR/N union is explicit but not full Cartesian product or fictional hardware.
for tr,n in [(1,256),(2,1024),(4,1536),(5,3000),(10,4883),(20,15000)]:
    add(f'eq3-topology-tr{tr}-n{n}','EQ3','eq3_topology_audit','BLOCKED_SOURCE_BACKED_SENSE_MAPPING',params={'tR_us':tr,'N_candidate':n,'scope':'parameter-audit pairing only, not validated coupled device'},gate='NC-TOPOLOGY',formal=1)
for b,route in itertools.product([1,2,4,8,16],['real','shuffled','uniform_null']):
    add(f'eq4-route-b{b}-{route}','EQ4','eq4_routing','BLOCKED_FROZEN_CAPTURE_AND_NULL_MATCH',entry='scripts/eval/hf_route_horizon_inputs.py',params={'B_composed':b,'route_control':route,'actual_active_sequences':'derive per step','E_k':'from frozen checkpoint','model':'Qwen3-30B-A3B','scope':'TRACE_COMPOSED; never live concurrency'},gate='G8;G9;NC-BUDGET',formal=5)
for b,policy in itertools.product([1,8,16],['none','on_demand','one_layer_ahead']):
    add(f'eq4-prefetch-b{b}-{policy}','EQ4','eq4_prefetch','BLOCKED_MATCHED_REPLAY_REGISTRATION',entry='scripts/eval/prefetch_replay.py',params={'B_composed':b,'policy':policy,'entrypoint_kind':'Python replay API, not standalone CLI','scope':'TRACE_COMPOSED','r':8,'kappa':.4,'beta':.08,'pure_prefetch_pair':'on_demand vs one_layer_ahead with matched demand concurrency; none separately serializes'},gate='G6;G7;G9;G10;NC-STAGING',formal=5)
for kind in ['capacity_matched_dense','active_compute_matched_dense','live_serving','runtime_prefetch']:
    row=add('eq4-'+kind,'EQ4','eq4_live_and_dense','BLOCKED_MATCHED_MODEL_OR_LIVE_RUNTIME',params={'comparison':kind,'B_requested':[1,8,16],'no_model_download':True},gate='G2;G5;G8;G9;G10;NC-BUDGET',resource='GPU_EXCLUSIVE',formal=5,cap=300,out_mib=256)
    row['pilot_rss_cap_MiB']=4096
keys=list(dict.fromkeys(k for r in rows for k in r))
path=root/'docs/49-eval-audit/run-matrix.csv'
with path.open('w',newline='') as f:w=csv.DictWriter(f,fieldnames=keys,lineterminator='\n');w.writeheader();w.writerows(rows)
summary={'schema_version':1,'planning_only':True,'auto_execute':False,'source_sha':SHA,'cells':len(rows),'pilot_runs_upper_bound':sum(r['repeats'] for r in rows),'confirmation_runs_min_after_gates':sum(r['confirmation_repeats_min'] for r in rows),'executed_this_round_cells':[r['cell_id'] for r in rows if r['execution_authorized_this_round']=='completed_cpu_only'],'by_eq':{eq:sum(r['eq']==eq for r in rows) for eq in ['EQ1','EQ2','EQ3','EQ4']},'by_status':{s:sum(r['implementation_status']==s for r in rows) for s in sorted(set(r['implementation_status'] for r in rows))},'matrix_sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'refinement_rule':'After reviewed pilot freezes physical profiles and noise/CI thresholds, bisect only adjacent feasible cells bracketing 5/10/20 percent slowdown or OOM boundary; at most two midpoints per bracket per axis; freeze confirmation set before new seeds. No crossing => no contour.'}
(report/'matrix-summary.json').write_text(json.dumps(summary,indent=2)+'\n');print(json.dumps(summary,indent=2))
