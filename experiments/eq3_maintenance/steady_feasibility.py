"""Conservative activity-envelope feasibility, not a repeated P2 qualification."""
import argparse,hashlib,json,os,resource,subprocess,time
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('--model-dir',type=Path,required=True);p.add_argument('--runner',type=Path,required=True);p.add_argument('--runner-source',type=Path,required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args();a.output.mkdir(parents=True,exist_ok=False)
grid=json.loads((a.model_dir/'rc_grid.json').read_text());ir=json.loads((a.model_dir/'normalized.json').read_text())
power={}
for component in ir['components']:
 c=component['id']
 if c=='gpu':power[c]=200.
 elif component['role']=='array_die' and component['physical_type']=='HBF':power[c]=.05
 elif component['role']=='array_die' and component['physical_type'].startswith('HBM'):power[c]=.0001/12
 elif component['role']=='base_die':power[c]=.1
lines=['HBFSIM_EQ3_THERMAL_EVENTS 1']
for eid,(component,watt) in enumerate(power.items(),1):
 indices=grid['component_cells'][component];volume=sum(grid['cells'][i]['volume_m3'] for i in indices)
 parts=[f"{grid['cells'][i]['id']} {watt*grid['cells'][i]['volume_m3']/volume:.17g}" for i in indices]
 lines.append(f'activity {eid} {eid} external_heat external 0 -1 -1 -1 0 1 1 '+' '.join(parts))
events=a.output/'caps.events';events.write_text('\n'.join(lines)+'\n')
def digest(path):return hashlib.sha256(path.read_bytes()).hexdigest()
command=[str(a.runner),'--steady-envelope','--model',str(a.model_dir/'model.txt'),'--events',str(events),'--step-s','.02','--slot-s','1','--end-s','1','--sample-s','.02','--min-k','300','--max-k','400','--envelope-limit-k','353.15','--model-sha256',digest(a.model_dir/'model.txt'),'--events-sha256',digest(events),'--runner-source-sha256',digest(a.runner_source),'--domain-version','ISOLATED_PILOT_ACTIVITY_ENVELOPE_V1']
(a.output/'manifest.json').write_text(json.dumps(dict(task='EQ3-ISOLATED-MAINTENANCE-CAMPAIGN-v1',scope='MATHEMATICAL_FEASIBILITY_NOT_P2_REFINEMENT',source_power_cap_w=power,command=command,wall_s=120,cpu=1,gpu=0,process_gib=4,assumptions=['Same original full2mm linear network','Each HBF physicaldie at continuous0.05W; each base0.1W conservative activity allowance','HBM fixed10rps16KiB; max20ms-window per-stack0.0001W','GPU continuously200W upper envelope, including post-input cooling','Use alpha1 output only. Other legacy alpha diagnostic rows do not change workload inputs.','Threshold353.15K is minimum selected research Light threshold, not a product safety limit'],claim='If componentwise envelope covers initial state and global max below minimum Light limit, these source coefficients and workload envelope cannot activate thermal Near even for long duration.'),indent=2))
os.sched_setaffinity(0,{min(os.sched_getaffinity(0))});resource.setrlimit(resource.RLIMIT_AS,(4*1024**3,4*1024**3));t=time.monotonic()
with (a.output/'stdout.log').open('w') as so,(a.output/'stderr.log').open('w') as se:r=subprocess.run(command,stdout=so,stderr=se,timeout=120,env=dict(os.environ,OMP_NUM_THREADS='1',OPENBLAS_NUM_THREADS='1',CUDA_VISIBLE_DEVICES=''))
result=dict(execution_status='COMPLETED' if r.returncode==0 else 'FAILED',exit_code=r.returncode,wall_s=time.monotonic()-t,max_child_rss_kib=resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss)
(a.output/'result.json').write_text(json.dumps(result,indent=2));print(json.dumps(result));print((a.output/'stdout.log').read_text()[-5000:]);print((a.output/'stderr.log').read_text()[-500:])
raise SystemExit(r.returncode)
