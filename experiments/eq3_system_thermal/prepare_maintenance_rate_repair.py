#!/usr/bin/env python3
"""Correct the observed384GB/s pilot input to its registered1536GB/s value."""
import argparse
from copy import deepcopy
import json
from pathlib import Path
from freeze_extension import freeze,save

HERE=Path(__file__).resolve().parent

def prepare(stage,destination):
    old=json.loads((stage/'maintenance-v1/PILOT_INDEX.json').read_text())
    destination.mkdir(parents=True,exist_ok=False);(destination/'inputs').mkdir()
    configs=[];changes=[]
    for row in old['points']:
        original=json.loads(Path(row['config']).read_text());fixed=deepcopy(original)
        if original['workload']['per_stack_Bps']!=384_000_000_000:raise ValueError('unexpected original reproducer')
        fixed['workload']['per_stack_Bps']=1_536_000_000_000
        fixed['point_id']=f"maint-rate-repair-{row['topology']}-01"
        assert fixed['maintenance']==original['maintenance']
        path=destination/'inputs'/(fixed['point_id']+'.json');save(path,fixed);configs.append(path)
        changes.append({'old_point':row['point_id'],'new_point':fixed['point_id'],
            'old_actual_per_stack_Bps':384_000_000_000,'registered_and_corrected_per_stack_Bps':1_536_000_000_000,
            'unchanged':'age,wear,physicalparameters,energy,guard,workloadtiming,topology,maintenancepolicy'})
    index=freeze(stage,destination,configs,HERE/'run_maintenance_point.py',point_wall_s=600,
                 dependency=stage/'ecc-pilot-v1/DONE.json')
    index['resources'].update(stage_wall_s=3600,sensitivity_output_gib=8)
    save(destination/'PILOT_INDEX.json',index)
    preflight={'status':'FROZEN_WAITING_ECC_SERIAL_COMPLETION','classification':'CONFIRMED_INPUT_PREPARATION_BUG',
      'source_preflight':str(stage/'maintenance-v1/PREFLIGHT.md'),
      'source_failure_evidence':str(stage/'maintenance-v1/PILOT_AUDIT.json'),
      'root_cause':'input snapshot384GB/s contradicts registered1536GB/s; lowactualinputkeptagedextents belowdue',
      'fix':'correct workloadrate only; noage orphysics tuning',
      'forecast':'existing mixed1.536TB/s no-maintenance raw first8s yields3.868..6.235 equivalent seconds at85C; above original2s remaining margin',
      'forecast_limit':'other topology and maintenance interaction need actual pilot; no guaranteed benefit',
      'environment_id':'eq3-thermal-cpu-v1','changes':changes,'points':4,'duration':'8sinput+4sdrain',
      'resources':index['resources'],'approval_scope':'existing explicit1536GB/s fourtopology maintenancepilot scope',
      'pass':['conserved bytes/energy','nonzero actualdue/terminalcommit/program/erase','no falseage reset','validsource/destinationversions','sharedresourcecontentionreceipts'],
      'stop':['400K domainfailure retainsraw','600sperpoint3600sstage','source/input mismatch','hostresource reserves'],
      'limits':'NO_ECC_REFRESH_BENEFIT_CLAIM;ECC_READ_AGE_NOT_YET_MAPPED_TO_THESE_EXTENTS'}
    save(destination/'PREFLIGHT.json',preflight)
    (destination/'PREFLIGHT.md').write_text('# Maintenance rate preparation repair\n\n'+json.dumps(preflight,indent=2)+'\n')

if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('--stage',type=Path,required=True);ap.add_argument('--destination',type=Path,required=True)
    a=ap.parse_args();prepare(a.stage.resolve(),a.destination.resolve())
