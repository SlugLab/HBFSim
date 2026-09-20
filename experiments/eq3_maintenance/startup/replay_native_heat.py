#!/usr/bin/env python3
"""Replay immutable startup native facts through unchanged energy/full thermal APIs.

This is a source-only open-loop diagnostic, not controller reclosure or a
complete host-to-HBF upload transport model. No MQSim engine is run here.
"""
import argparse
import csv
from collections import defaultdict
import hashlib
import json
import math
import platform
import shutil
from datetime import datetime, timezone
import os
from pathlib import Path
import resource
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from campaign_inputs import energy_profile
from energy_ledger import ActivityEnergyLedger
from thermal_client import ThermalService


def replay(args):
    out = args.output.resolve(); out.mkdir(parents=True, exist_ok=False)
    native = args.native.resolve(strict=True)
    summary = json.loads(args.summary.read_text())
    mapping = json.loads(args.stack_map.read_text())
    coefficient = energy_profile(); coefficient['gpu_external_w'] = 0
    coefficient['gpu_note'] = 'SOURCE_ONLY_UPLOAD_DIAGNOSTIC; idle GPU/host ingress not modelled'
    normalized = json.loads((args.model_dir/'normalized.json').read_text())
    components = [c['id'] for c in normalized['components']]
    model_files={name:hashlib.sha256((args.model_dir/name).read_bytes()).hexdigest()
                 for name in ('model.txt','rc_grid.json','rc_sensors.json','normalized.json')}
    manifest = dict(kind='NATIVE_STARTUP_OPEN_LOOP_THERMAL_REPLAY',
                    started_utc=datetime.now(timezone.utc).isoformat(),python=sys.version,platform=platform.platform(),
                    address_limit_gib=4,threads=1,gpu_count=0,disk_free_bytes=shutil.disk_usage(out).free,
                    source_native=str(native), source_summary_path=str(args.summary.resolve()),
                    native_sha256=hashlib.sha256(native.read_bytes()).hexdigest(),
                    source_summary_sha256=hashlib.sha256(args.summary.read_bytes()).hexdigest(),
                    stack_map_sha256=hashlib.sha256(args.stack_map.read_bytes()).hexdigest(),
                    thermal_binary_sha256=hashlib.sha256(args.thermal_binary.read_bytes()).hexdigest(),
                    model_dir=str(args.model_dir.resolve()),model_file_sha256=model_files,
                    source_summary=summary, energy_profile=coefficient,
                    step_ns=20_000_000, recovery_ns=args.recovery_ns,
                    no_mqsim_rerun=True, no_controller_reclosure=True,
                    host_ingress_energy='UNAVAILABLE', background_idle_power='NOT_MODELLED',
                    physical_qualification='CONDITIONAL_ENGINEERING_USE_NOT_MODEL_FREEZE')
    (out/'manifest.json').write_text(json.dumps(manifest, indent=2))
    for key in ('OMP_NUM_THREADS','OPENBLAS_NUM_THREADS','MKL_NUM_THREADS','NUMEXPR_NUM_THREADS'): os.environ[key]='1'
    os.environ['CUDA_VISIBLE_DEVICES']=''
    resource.setrlimit(resource.RLIMIT_AS, (4*1024**3,4*1024**3))
    resource.setrlimit(resource.RLIMIT_CORE, (0,0))
    window = 20_000_000
    ledger = ActivityEnergyLedger(coefficient, mapping, components, gpu_stop_ns=0)
    began = time.monotonic(); native_count=0; previous=-1; peaks={}; worst_residual=0
    energy_totals=[]; last_frame=None
    with (out/'thermal.jsonl').open('w') as frames:
        with ThermalService(args.thermal_binary,args.model_dir,out/'thermal-process',
                            artifact_root=args.artifact_root) as thermal:
            def flush():
                nonlocal last_frame, worst_residual
                start=ledger.now; totals=ledger.flush(start+window)
                last_frame=thermal.advance(start,start+window,totals)
                frames.write(json.dumps(last_frame)+'\n')
                for entity,value in last_frame['temperatures'].items():
                    peaks[entity]=max(peaks.get(entity,-math.inf),value)
                cumulative=last_frame.get('energy_j',{}).get('cumulative',{})
                input_j=cumulative.get('total_input_j',0)
                residual=cumulative.get('energy_residual_j')
                if residual is not None and input_j:worst_residual=max(worst_residual,abs(residual/input_j))
                energy_totals.append(dict(start_ns=start,end_ns=start+window,component_energy_j=totals))
            with native.open() as stream:
                for line in stream:
                    event=json.loads(line); stamp=event['time_ns']
                    if stamp<previous:raise ValueError('native observation order moved backwards')
                    while stamp>=ledger.now+window:flush()
                    ledger.native(event); native_count+=1; previous=stamp
            if ledger.active:raise ValueError('native media/transfer has no end; do not fabricate completion energy')
            stop=((max(previous,0)+window-1)//window)*window+args.recovery_ns
            while ledger.now<stop:flush()
    (out/'energy-windows.json').write_text(json.dumps(energy_totals,indent=2))
    if ledger.rows:
        with (out/'energy-activity.csv').open('w') as stream:
            writer=csv.DictWriter(stream,fieldnames=list(ledger.rows[0]));writer.writeheader();writer.writerows(ledger.rows)
    boundaries=summary['phase_boundaries_ns']
    stage_energy={name:0.0 for name in ('startup_write','verify_read','software_lifecycle_maintenance')}
    source_energy=defaultdict(float);scope_energy=defaultdict(float)
    for row in ledger.rows:
        source_energy[row['source']]+=row['energy_j']
        scope_energy[row['scope']]+=row['energy_j']
        duration=row['end_ns']-row['start_ns']
        if duration<=0:continue
        for name in stage_energy:
            low=boundaries[name+'_start'];high=boundaries[name+'_end']
            overlap=max(0,min(row['end_ns'],high)-max(row['start_ns'],low))
            stage_energy[name]+=row['energy_j']*overlap/duration
    if abs(sum(stage_energy.values())-ledger.total_j)>1e-10*max(1.0,ledger.total_j):
        raise ValueError('stage energy must conserve the observed source ledger')
    window_energy=sum(sum(item['component_energy_j'].values()) for item in energy_totals)
    row_energy=sum(row['energy_j'] for row in ledger.rows)
    thermal_input=(last_frame or {}).get('energy_j',{}).get('cumulative',{}).get('total_input_j')
    checks={
        'ledger_vs_activity_rows_abs_j':abs(ledger.total_j-row_energy),
        'ledger_vs_thermal_windows_abs_j':abs(ledger.total_j-window_energy),
        'ledger_vs_stage_split_abs_j':abs(ledger.total_j-sum(stage_energy.values())),
        'ledger_vs_source_split_abs_j':abs(ledger.total_j-sum(source_energy.values())),
        'ledger_vs_scope_split_abs_j':abs(ledger.total_j-sum(scope_energy.values())),
        'ledger_vs_thermal_service_input_abs_j':None if thermal_input is None else abs(ledger.total_j-thermal_input),
    }
    tolerance=1e-10*max(1.0,ledger.total_j)
    if any(value is None or value>tolerance for value in checks.values()):
        raise ValueError('energy conservation failed across source, stage, window, or thermal receipt')
    result=dict(execution_status='COMPLETED',capability_status='BACKEND_NATIVE_FACTS_PLUS_OPEN_LOOP_THERMAL',
                native_event_count=native_count,simulated_end_ns=ledger.now,source_only_energy_j=ledger.total_j,
                source_energy_j_by_stage=stage_energy,
                source_energy_j_by_native_source=dict(sorted(source_energy.items())),
                source_energy_j_by_scope=dict(sorted(scope_energy.items())),
                energy_conservation_abs_j=checks,energy_conservation_tolerance_j=tolerance,
                peak_k_by_entity=peaks,peak_k=max(peaks.values()),energy_relative_residual_max=worst_residual,
                wall_s=time.monotonic()-began,child_peak_rss_kib=resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss,
                sub_20ms_peak='UNRESOLVED_BY_FIXED_MACROSTEP',weight_upload_coverage='SEE_SOURCE_SUMMARY_NOT_FULL_MODEL',
                physical_status='CONDITIONAL_ENGINEERING_USE',controller_status='NOT_RECLOSED',
                host_ingress_energy='UNAVAILABLE',fabric_upload_transport='NOT_MODELLED')
    (out/'DONE.json').write_text(json.dumps(result,indent=2))
    print(json.dumps(result))


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--native',type=Path,required=True);p.add_argument('--summary',type=Path,required=True)
    p.add_argument('--stack-map',type=Path,required=True);p.add_argument('--model-dir',type=Path,required=True)
    p.add_argument('--thermal-binary',type=Path,required=True);p.add_argument('--artifact-root',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True);p.add_argument('--recovery-ns',type=int,default=200_000_000)
    a=p.parse_args()
    if a.recovery_ns<0 or a.recovery_ns%20_000_000:raise ValueError('recovery must be whole20ms windows')
    try:replay(a)
    except BaseException as error:
        if a.output.exists():(a.output/'FAILED.json').write_text(json.dumps(dict(type=type(error).__name__,error=str(error)),indent=2))
        raise
