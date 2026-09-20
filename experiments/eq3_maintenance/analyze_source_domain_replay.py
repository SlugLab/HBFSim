#!/usr/bin/env python3
"""Compare full vs own-source responses; sum only linear entity means."""
import argparse,csv,json,pathlib
p=argparse.ArgumentParser();p.add_argument('--stage',type=pathlib.Path,required=True);p.add_argument('--output',type=pathlib.Path,required=True);a=p.parse_args();a.output.mkdir(parents=True,exist_ok=False)
s=a.stage;full=[]
for row in csv.DictReader((s/'points/Q1-WEIGHT-MAINT-PILOT03/thermal.csv').open()):
 full.append(dict(time_ns=int(row['end_ns']),entities=json.loads(row['entity_temperatures_k'])))
domains=['gpu']+[f'{k}{i}' for k in ('hbf','hbm') for i in range(4)];summed=[{k:300.0 for k in row['entities']} for row in full];summary={};count=0
with (a.output/'domain-observations.csv').open('w') as f:
 w=csv.writer(f);w.writerow(['time_ns','domain','component','full_mean_k','own_source_mean_k','cross_source_mean_k','full_hotspot_k','own_source_hotspot_k'])
 for d in domains:
  point=s/'points'/('A1-SOURCE-'+d+'01');assert (point/'DONE.json').exists()
  m=dict(full_peak_k=300.,own_source_peak_k=300.,maximum_cross_source_mean_k=0.,maximum_absolute_full_own_hotspot_difference_k=0.)
  rows=0
  for index,line in enumerate((point/'observations.jsonl').open()):
   row=json.loads(line);ref=full[index];assert row['end_ns']==ref['time_ns'];rows+=1
   for entity,v in row['entity_temperatures_k'].items():
    summed[index][entity]+=v['mean_k']-300
    if entity.split('.')[0]!=d:continue
    rv=ref['entities'][entity];cross=rv['mean_k']-v['mean_k'];count+=1
    w.writerow([ref['time_ns'],d,entity,rv['mean_k'],v['mean_k'],cross,rv['hotspot_k'],v['hotspot_k']])
    m['full_peak_k']=max(m['full_peak_k'],rv['hotspot_k']);m['own_source_peak_k']=max(m['own_source_peak_k'],v['hotspot_k']);m['maximum_cross_source_mean_k']=max(m['maximum_cross_source_mean_k'],cross);m['maximum_absolute_full_own_hotspot_difference_k']=max(m['maximum_absolute_full_own_hotspot_difference_k'],abs(rv['hotspot_k']-v['hotspot_k']))
  assert rows==len(full);summary[d]=m
linear=max(abs(summed[i][k]-v['mean_k']) for i,row in enumerate(full) for k,v in row['entities'].items())
zero=max(abs(v['mean_k']-300) for line in (s/'points/A1-SOURCE-ZERO01/observations.jsonl').open() for v in json.loads(line)['entity_temperatures_k'].values())
result=dict(execution_status='COMPLETED',capability_status='SOURCE_DOMAIN_ABLATION_EXECUTED',scientific_scope='Same C/G self response and cooling; removes other source contributions, not a disconnected physical network. No nodewise hotspot summation and no policy reclosure.',windows=len(full),rows=count,domain_results=summary,linear_entity_mean_superposition_max_error_k=linear,zero_source_max_mean_deviation_k=zero)
(a.output/'result.json').write_text(json.dumps(result,indent=2))
(a.output/'REPORT.md').write_text('# A1 source-domain response ablation\n\nTen actual full-network runs (zero + nine owner-source groups), same original C/G/boundaries/20ms/10s and immutable Pilot03 energy. These are open-loop source-response comparisons, not physically disconnected components or new calibrated thermal models.\n\nLinear entity-mean superposition maximum error: '+str(linear)+' K; zero-source deviation '+str(zero)+' K. Hotspot maxima are compared at the same time but never added as linear observables.\n\n'+ '\n'.join(f'- {d}: full peak {v["full_peak_k"]:.6f} K; own-source peak {v["own_source_peak_k"]:.6f} K; maximum cross-source contribution to entity mean {v["maximum_cross_source_mean_k"]:.6f} K.' for d,v in summary.items())+'\n\nGPU power and operation coefficients remain engineering inputs. This identifies coupling in this model, not a measured product heat budget. The shared passive cooling structure is preserved in every source run.\n')
print(json.dumps(result))
