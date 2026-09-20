#!/usr/bin/env python3
"""Freeze explicitly authorized four-topology base matrix and its pilots."""
import argparse
import hashlib
import json
from pathlib import Path
from energy import engineering_energy_profile
from topology_service import default_config, media_cost_from_service_rate

HERE = Path(__file__).resolve().parent
STRATEGIES = ['guard_only','thermal_hysteresis_guard','read_rate_feedback_thermal_guard_v1']
TOPOLOGIES = ['mixed_direct','relay','dash','all_hbf_direct']


def save(path, obj):
    path.write_text(json.dumps(obj,indent=2,allow_nan=False)+'\n')


def config(point_id, topology, rate, strategy, active_s=20, recovery_s=10):
    service = default_config(topology)
    # Existing basic fabric engineering parameters, separate from OCP media cap.
    for row in service['fabric']['hbf'].values():
        for name in ('fill','direct_link','relay_link'):
            if row[name] is not None:
                row[name]['latency_ns'] = 10
    for row in service['fabric']['hbm'].values():
        row['gpu_link'] = {'latency_ns':10,'bandwidth_bytes_per_s':2_048_000_000_000}
    # Explicit plane-parallel engineering proxy, not claimed OCP program speed.
    program_Bps_per_channel = 16 * 4096 * 10_000
    service['operation_media_cost']['program'] = media_cost_from_service_rate(96_000_000_000,program_Bps_per_channel)
    service['operation_media_cost']['erase'] = media_cost_from_service_rate(96_000_000_000,16*4096*256*1000)
    service['operation_cost_evidence'] = 'PROXY_16_PLANES_4KIB_100US_PROGRAM_1MS_ERASE_256PAGES_PER_BLOCK'
    profile = engineering_energy_profile()
    profile['erase_array_j_per_operation'] = .05 * .001
    profile['evidence']['erase'] = 'DERIVED_ENGINEERING_PROXY_0.05W_TIMES_NATIVE_FIXTURE_1MS_NOT_HBF_MEASUREMENT'
    return {'schema_version':'eq3-system-point-v1','point_id':point_id,'topology':topology,
            'strategy':strategy,'service':service,'energy':profile,
            'workload':{'active_ns':int(active_s*1e9),'per_stack_Bps':rate,
                        'pattern':'continuous','channel_distribution':'uniform'},
            'recovery_ns':int(recovery_s*1e9),'gpu_external_w':0,
            'thermal_limits_k':{'hbf':[353.15,363.15,378.15],
                                'hbm':[353.15,363.15,378.15],
                                'gpu':[363.15,373.15,383.15]},
            'resource_limits':{'address_space_gib':8,'watchdog_s':600},
            'scope':'CONDITIONAL_SIMULATED','unknown_idle_power':'EXCLUDED_NOT_ZERO_MEASUREMENT'}


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('stage','mixed-model','all-hbf-model','thermal-binary','artifact-root'):
        p.add_argument('--'+name,type=Path,required=True)
    a=p.parse_args()
    directory=a.stage/'inputs';directory.mkdir(exist_ok=False)
    points=[]
    for topology in TOPOLOGIES:
        specs=[('pilot',1_536_000_000_000,STRATEGIES[2],8,4)]
        specs += [('base',rate,strategy,20,10) for rate in
                  (384_000_000_000,768_000_000_000,1_152_000_000_000,1_536_000_000_000,1_920_000_000_000)
                  for strategy in STRATEGIES]
        for kind,rate,strategy,active,recovery in specs:
            point_id=f'{kind}-{topology}-{rate//10**9}-{STRATEGIES.index(strategy)}-01'
            path=directory/(point_id+'.json')
            save(path,config(point_id,topology,rate,strategy,active,recovery))
            points.append({'point_id':point_id,'kind':kind,'topology':topology,
                'config':str(path.resolve()),'config_sha256':hashlib.sha256(path.read_bytes()).hexdigest(),
                'model_dir':str((a.all_hbf_model if topology=='all_hbf_direct' else a.mixed_model).resolve()),
                'thermal_binary':str(a.thermal_binary.resolve()),'artifact_root':str(a.artifact_root.resolve()),
                'output':str((a.stage/'points'/point_id).resolve())})
    points.sort(key=lambda row:(row['kind']!='pilot',row['point_id']))
    save(a.stage/'RUN_INDEX.json',{'schema_version':'eq3-system-run-index-v1',
        'authorization':'USER_EXPLICIT_FOUR_TOPOLOGY_PLAN','points':points,
        'source_locks':{name:hashlib.sha256((HERE/name).read_bytes()).hexdigest() for name in
                        ('run_system_point.py','energy.py','rate_workload.py','topology_service.py','launch_stage.py')},
        'resources':{'stage_wall_s':21600,'point_wall_s':600,'point_output_gib':1,
                     'stage_output_gib':80,'host_ram_reserve_gib':32,'host_disk_reserve_gib':100}})


if __name__=='__main__':main()
