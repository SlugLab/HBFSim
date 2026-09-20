#!/usr/bin/env python3
"""Freeze a single R01 reference pilot for user review; never write approval/run.

The full calibration matrix remains blocked by fine-grid output limits and
requires a separately reviewed RC numerical version. This package cannot launch
other points under an R01 confirmation.
"""
import argparse
import hashlib
import json
from pathlib import Path
from eq3_experiment_gate import canonical_manifest_hash, observed_git_state, verify_artifacts


def digest(path):
    h=hashlib.sha256()
    with path.open('rb') as stream:
        for b in iter(lambda:stream.read(1024*1024),b''): h.update(b)
    return h.hexdigest()


def prepare(root, code_root, generated, backend, environment, tests, parse_receipt, output):
    root=root.resolve(); code_root=code_root.resolve(); generated=generated.resolve(); output=output.resolve()
    def artifact(path, role, identity=None):
        path=path.resolve(); rel=path.relative_to(root).as_posix()
        return {'logical_id':identity or rel,'semantic_role':role,'sha256':digest(path),'path':rel}
    def write(path,obj): path.write_text(json.dumps(obj,indent=2,ensure_ascii=False)+'\n')
    r=json.loads((generated/'generation_receipt.json').read_text())
    ir=json.loads((generated/'normalized.json').read_text())
    parse_binding=json.loads(parse_receipt.read_text())
    parsed=parse_binding['parsed']
    raw=root/parse_binding['raw_log_path']
    if digest(raw)!=parse_binding['raw_log_sha256'] or json.loads(raw.read_text())!=parsed:
        raise ValueError('parse raw log changed')
    if parse_binding['generated_sha256']!=r['generated_sha256']:
        raise ValueError('parse receipt does not bind the current generated inputs')
    if parsed.get('status')!='PARSE_ONLY_PASS' or parsed.get('solve_performed') is not False:
        raise ValueError('requires genuine parse-only receipt')
    if (parsed['cells']!=r['reference_cells'] or r['duration_s']!=100 or not math_close(r['input_energy_j'],1365)
            or r['reference_shape']!=[16,16,63] or r['step_s']!=.02 or r['observation_s']!=.1
            or ir['power']['trace_id']!='train'
            or any(not math_close(b-a,.004) for axis in json.loads((generated/'reference_grid.json').read_text())['axes_m'][:2]
                   for a,b in zip(axis,axis[1:]))
            or any(not math_close(row['end_s']-row['start_s'],.5) for row in ir['power']['intervals'])):
        raise ValueError('this R01 execution binding requires the registered first-example training trace')
    if r['reference_output_readiness']!='PENDING_RESOURCE_PILOT_APPROVAL':
        raise ValueError('reference output exceeds current envelope')
    if 'FAILED' in tests.read_text() or '\nOK\n' not in tests.read_text():
        raise ValueError('fixed-test evidence is not passing')
    for name,h in r['generated_sha256'].items():
        if digest(generated/name)!=h: raise ValueError('stale generated artifact '+name)
    revision,diff=observed_git_state(code_root)
    if diff!=hashlib.sha256(b'').hexdigest(): raise ValueError('commit reviewed code before execution binding')
    output.mkdir(parents=True,exist_ok=False)
    run_parent=output/'runs'; run_parent.mkdir()
    files=[artifact(p,'generated physical/numerical input') for p in sorted(generated.iterdir()) if p.is_file()]
    engine=artifact(backend,'stock numerical reference backend','stock-3dice-backend')
    launch={'schema_version':'eq3-layered-launch-v1','experiment_id':'EQ3-P2-LAYERED-R01','version':'1',
            'run_id':'R01','stage':'thermal_only','readiness':'READY','backend':engine,'artifacts':files,
            'command':{'argv':[engine['path'],'package.stk'],'cwd':generated.relative_to(root).as_posix()},
            'output':{'path':(run_parent/'R01').relative_to(root).as_posix(),'max_new_gib':4},
            'limits':{'threads':1,'ram_gib':12,'watchdog_seconds':600,'gpu_compute_minutes':0}}
    write(output/'launch.json',launch)
    code_files=sorted((code_root/'tools').glob('eq3_layered*'))
    code_files += [code_root/'tools'/name for name in ('eq3_experiment_gate.py','eq3_3dice_parse_only.c','eq3_build_parse_only.py')]
    code_files += sorted((code_root/'src/eq3_thermal').glob('*.cpp'))+[code_root/'include/hbfsim/eq3_thermal/thermal.hpp']
    scientific_inputs=sorted((code_root/'configs/eq3_thermal/research').glob('*.json'))
    resources={'cpu_configurations':1,'executions_per_configuration':1,'cpu_hours':600/3600,
               'build_threads':1,'ram_gib':12,'disk_gib':4,'gpu_compute_minutes':0}
    science={
        'research_question_and_hypothesis':{'question':'Can the declared layered first-example reference execute inside the CPU safety envelope and conserve energy?',
                                          'hypothesis':'Declared source map and thermal boundaries produce a complete finite reference trace; failure is allowed'},
        'evidence_type_and_limits':{'type':'NEW_REFERENCE / CONDITIONAL_SIMULATED','no_claims':
            ['physical HBF calibration','all-topology validation','RC model accepted','system behavior','GPU measurement']},
        'device_topology':{'profile_id':ir['profile_id'],'topology':ir['topology'],'external_GDDR':'excluded from package by user decision'},
        'geometry_materials_boundaries':{'entities':r['entity_count'],'z_layers':r['z_layers'],'shape':r['reference_shape'],
            'cells':r['reference_cells'],'materials':ir['materials'],'boundaries':ir['boundaries'],'all_geometry':'bound normalized.json'},
        'workload_and_initial_state':{'trace':'train','duration_s':r['duration_s'],'initial_k':ir['boundaries']['initial_temperature_k'],
            'input_energy_j':r['input_energy_j'],'development_blind':'inputs statically generated only; no solving/viewing outputs authorized'},
        'power_reliability_control':{'power':'prescribed independent synthetic components/groups; nonuniform supported',
            'powered_regions':r['powered_components'],'activity_power_calibration':False,'controller':'OFF','reliability':'NOT_USED'},
        'scan_matrix_and_repetitions':{'runs':[{'run_id':'R01','repeat':1,'mesh_um':4000,'step_s':.02,'duration_s':100}],
            'automatic_followup':False,'after_R01':'review actual RSS/time/output/energy before requesting any further reference/RC execution',
            'full_matrix_status':'BLOCKED_FINE_OUTPUT_POLICY_AND_RC_NUMERICAL_VERSION_REVIEW'},
        'time_and_numerics':{'step_s':r['step_s'],'power_input_slot_s':.5,'backend_slot_s':.1,'observation_s':.1,
            'field_output':'every solver step, first frame dt; t0 declared input','fit_parameter_count':0,
            'ROM_candidates':0,'reference_binary_revision':'e0bb6850c5e446363e26936586d625270c87f224'},
        'controls_ablations_observations':{'sensors':r['sensor_count'],'controls':'no strategy/ablation, no tuning',
            'observations':['all fields','component/base/die means and hotspots','array-only and base+array stack statistics','storage and boundary energy'],
            'future_comparison_targets':{'neighbor_difference_k':.25,'region_mae_k':1,'hotspot_max_error_k':2,'normalized_mae':.05}},
        'acceptance_abort_and_outputs':{'process_abort':{'wall_seconds':600,'process_ram_gib':12,'output_gib':4},
            'task_memory_gib':16,'task_new_disk_gib':20,'gpu':0,'cloud_cost':0,
            'resource_estimate':'UNMEASURED; pilot establishes actual costs, no extrapolated runtime promise',
            'field_bytes_estimate':r['reference_field_bytes_estimated'],
            'numeric_checks':{'relative_energy_residual_max':.001,'temperature_domain_k':ir['temperature_domain_k'],
                              'domain_check':'post-run full-field validation, not an upstream live-temperature abort'},
            'failure_policy':'preserve raw/FAILED; no automatic retry, resource expansion or alternative physics',
            'outputs':launch['output'],'model_freeze_allowed':False}}
    manifest={'schema_version':'eq3-experiment-manifest-v1','experiment_id':launch['experiment_id'],'version':'1',
        'approval_status':'PENDING_USER_APPROVAL','canonical_manifest_hash':'0'*64,'scientific_config':science,
        'code':{'repository_revision':revision,'dirty_diff_sha256':diff,'artifacts':[artifact(p,'source') for p in code_files]},
        'dependencies':[engine,artifact(environment,'frozen private environment')],
        'inputs':files+[artifact(p,'source scientific input') for p in scientific_inputs]+[artifact(raw,'parse-only raw log')]+
            [artifact(output/'launch.json','single run launch binding','layered-launch-manifest')],
        'resource_budget':{'requested':resources,'limits':dict(resources)},
        'prerequisites':[{'id':'fixed-software-tests','status':'PASSED','evidence':artifact(tests,'fixed tests raw log')},
                         {'id':'reference-parse-only','status':'PASSED','evidence':artifact(parse_receipt,'no-solve parser receipt')}],
        'execution_context':{'environment_id':'eq3-thermal-reference-v1','code_root':code_root.relative_to(root).as_posix(),
                             'readiness':'READY_FOR_EXECUTION_APPROVAL_R01_ONLY'}}
    manifest['canonical_manifest_hash']=canonical_manifest_hash(manifest)
    verify_artifacts(manifest,root)
    write(output/'experiment_manifest.json',manifest)
    return manifest


def math_close(a,b):
    import math
    return math.isclose(a,b,rel_tol=1e-10)


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('root','code-root','generated','backend','environment','tests','parse-receipt','output'):
        p.add_argument('--'+name,type=Path,required=True)
    a=p.parse_args(); m=prepare(a.root,a.code_root,a.generated,a.backend,a.environment,a.tests,a.parse_receipt,a.output)
    print(json.dumps({'status':'READY_FOR_EXECUTION_APPROVAL_R01_ONLY','approval_status':'PENDING_USER_APPROVAL',
                      'canonical_manifest_hash':m['canonical_manifest_hash'],'code_revision':m['code']['repository_revision'],
                      'launch_performed':False}))


if __name__=='__main__': main()
