#!/usr/bin/env python3
"""Bind a reviewed extension input list to the existing serial launcher."""
import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path

HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[1]

def digest(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def save(path,obj):Path(path).write_text(json.dumps(obj,indent=2,allow_nan=False)+'\n')

def freeze(stage,destination,configs,runner,*,point_wall_s=900,dependency=None):
    destination.mkdir(parents=True,exist_ok=True)
    base=json.loads((stage/'RUN_INDEX.json').read_text())
    templates={r['topology']:r for r in base['points']}
    names=['endpoint_policy.py','topology_service.py','energy.py','rate_workload.py','run_system_point.py',
           'maintenance_driver.py','reliability.py',runner.name]
    if runner.name=='run_causal_point.py':
        names+=['causal_service.py','causal_workload.py','causal_maintenance_age.py','tiny_cpu_trace.py']
    sources={str((HERE/n).relative_to(ROOT)):digest(HERE/n) for n in sorted(set(names))}
    for n in ('thermal_client.py','read_rate_policy.py'):
        p=ROOT/'experiments'/'eq3_maintenance'/n
        sources[str(p.relative_to(ROOT))]=digest(p)
    sources['tools/eq3_basic_fabric.py']=digest(ROOT/'tools'/'eq3_basic_fabric.py')
    if runner.name=='run_causal_point.py':
        catalogue=ROOT/'experiments/eq3_maintenance/sources/qwen2_5_weight_models.json'
        sources[str(catalogue.relative_to(ROOT))]=digest(catalogue)
        device_profile=ROOT/'configs/eq3_thermal/research/candidate_profile.json'
        sources[str(device_profile.relative_to(ROOT))]=digest(device_profile)
    points=[]
    for path in configs:
        cfg=json.loads(path.read_text());row=deepcopy(templates[cfg['topology']])
        if cfg.get('hbf_read_cost_proxy',{}).get('mode','disabled')!='disabled':
            for name in ('ecc_cost_proxy.py','ecc_service_adapter.py'):
                sources[str((HERE/name).relative_to(ROOT))]=digest(HERE/name)
        row.update(point_id=cfg['point_id'],config=str(path.resolve()),config_sha256=digest(path),
                   output=str(destination/'points'/cfg['point_id']))
        points.append(row)
        if cfg.get('trace',{}).get('dependency_mode')=='tiny_cpu_template':
            trace_path=ROOT/cfg['trace']['tiny_trace_path']
            sources[str(trace_path.relative_to(ROOT))]=digest(trace_path)
    locks={m:{n:digest(Path(m)/n) for n in ('model.txt','normalized.json','rc_grid.json','rc_sensors.json')}
           for m in sorted({r['model_dir'] for r in points})}
    result={'schema_version':'eq3-extension-frozen-index-v1','status':'PENDING_DEPENDENCIES_BASE_MATRIX',
        'authorization':'USER_EXPLICIT_FOUR_TOPOLOGY_PLAN','runner':str(runner),'runner_sha256':digest(runner),
        'dependencies':{'base_done':str(dependency or stage/'BASE_DONE.json')},
        'runtime_source_locks_sha256':sources,'model_locks_sha256':locks,
        'thermal_binary_sha256':digest(points[0]['thermal_binary']),'points':points,
        'resources':{'point_wall_s':point_wall_s,'stage_wall_s':21600,'point_output_gib':2,
                     'sensitivity_output_gib':30,'parent_combined_output_gib':100,
                     'host_ram_reserve_gib':32,'host_disk_reserve_gib':100},
        'failure_contract':'DOMAIN_FAILURE_RETAINED_CONTINUE; OTHER_FAILURE_STOP_AND_DIAGNOSE',
        'cpu_ownership':'ROOT_AFTER_P2_DIAGNOSTICS_QUEUE_RELEASE_ONLY'}
    return result

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--stage',type=Path,required=True);p.add_argument('--destination',type=Path,required=True)
    p.add_argument('--runner',type=Path,required=True);p.add_argument('--config',type=Path,action='append',required=True)
    p.add_argument('--point-wall-s',type=int,default=900);p.add_argument('--output',type=Path,required=True)
    a=p.parse_args()
    if a.output.exists():raise FileExistsError(a.output)
    save(a.output,freeze(a.stage.resolve(),a.destination.resolve(),a.config,a.runner.resolve(),point_wall_s=a.point_wall_s))
