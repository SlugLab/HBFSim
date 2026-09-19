#!/usr/bin/env python3
"""Stream stock per-layer fields into common sensors and energy receipts.

Reads existing output only. No solver invocation. Field snapshots correspond to
completed time steps, not t=0. Initial state is the declared input state.
"""
import argparse
import csv
import gzip
import json
import math
from pathlib import Path
from contextlib import ExitStack, contextmanager

from eq3_layered_export import network, close


@contextmanager
def open_field(path):
    """Read retained native bytes without an uncompressed temporary file."""
    path = Path(path)
    compressed = path.with_name(path.name + '.gz')
    encoded = path.with_name(path.name + '.tmk')
    available = [p for p in (path, compressed, encoded) if p.is_file()]
    if len(available) != 1:
        raise ValueError('missing or ambiguous retained field artifacts')
    if encoded in available:
        from eq3_campaign_nativecodec import decoded_lines
        lines = decoded_lines(encoded)
        try:
            yield (line.decode('ascii') for line in lines)
        finally:
            lines.close()
    else:
        with (path.open() if path in available else gzip.open(compressed, 'rt')) as stream:
            yield stream


def frames(stream, nx, ny):
    rows=[]
    for line in stream:
        if not line.strip() or line.lstrip().startswith('%'):
            continue
        try: values=[float(v) for v in line.split()]
        except ValueError as exc: raise ValueError('invalid field row') from exc
        if len(values)!=nx or any(not math.isfinite(v) for v in values):
            raise ValueError('invalid field width/nonfinite temperature')
        rows.extend(values)
        if len(rows)==nx*ny:
            yield rows; rows=[]
    if rows: raise ValueError('truncated field frame')


def sensor_mapping(ir, grid):
    mapping=[]
    for sensor in ir['sensors']:
        if sensor['reduction']=='weighted_mean':
            weights=[]
            for part in sensor['weights']:
                indices=grid['component_cells'][part['component_id']]
                volume=sum(grid['cells'][i]['volume_m3'] for i in indices)
                weights += [[i,part['weight']*grid['cells'][i]['volume_m3']/volume] for i in indices]
            if not close(sum(w for _,w in weights),1): raise ValueError('sensor weights not conserved')
            mapping.append({'id':sensor['id'],'reduction':'weighted_mean','cell_weights':weights})
        elif sensor['reduction']=='max':
            indices=sorted({i for cid in sensor['components'] for i in grid['component_cells'][cid]})
            mapping.append({'id':sensor['id'],'reduction':'max','cell_indices':indices})
        else: raise ValueError('unsupported sensor reduction')
    return mapping


def readings(mapping, temperatures, grid):
    out=[]
    for s in mapping:
        if s['reduction']=='weighted_mean':
            out.append((s['id'],sum(temperatures[i]*w for i,w in s['cell_weights']),None))
        else:
            i=max(s['cell_indices'],key=lambda n:temperatures[n])
            out.append((s['id'],temperatures[i],grid['cells'][i]['id']))
    return out


def rc_frames(stream, grid, dt):
    ids={c['id']:c['index'] for c in grid['cells']}; current={}; index=1
    initial=set()
    for row in csv.DictReader(stream):
        if 'record_type' in row and row['record_type']!='node': continue
        time=float(row['time_s'])
        key=row.get('node_id',row.get('location')); value=float(row.get('temperature_k',row.get('value_k')))
        if time==0:
            if key not in ids or key in initial or not math.isfinite(value):
                raise ValueError('invalid RC initial frame')
            initial.add(key); continue
        if not close(time,index*dt):
            if len(current)!=len(ids) or not close(time,(index+1)*dt):
                raise ValueError('RC time/node coverage mismatch')
            yield [current[c['id']] for c in grid['cells']]
            current={}; index+=1
        if key not in ids or key in current or not math.isfinite(value):
            raise ValueError('invalid RC node output')
        current[key]=value
    if len(current)!=len(ids): raise ValueError('truncated RC node frame')
    if initial and len(initial)!=len(ids): raise ValueError('truncated RC initial frame')
    yield [current[c['id']] for c in grid['cells']]


def observe(run_dir, generated, output, kind='reference'):
    output=Path(output)
    if output.exists(): raise FileExistsError('refusing to overwrite derived output')
    ir=json.loads((generated/'normalized.json').read_text())
    grid=json.loads((generated/('reference_grid.json' if kind=='reference' else 'rc_grid.json')).read_text())
    receipt=json.loads((generated/'generation_receipt.json').read_text())
    rc_energy=None
    if kind=='rc':
        rc_energy=json.loads((run_dir/'rc_energy_receipt.json').read_text())
        if not close(rc_energy['end_s'],receipt['duration_s']): raise ValueError('RC duration differs')
    dt=receipt['step_s'] if kind=='reference' else rc_energy['sample_s']
    duration=receipt['duration_s']; count=round(duration/dt)
    if not close(duration/dt,count): raise ValueError('duration not step aligned')
    mapping=sensor_mapping(ir,grid); _,bounds=network(ir,grid)
    nx,ny,nz=grid['shape']; initial=ir['boundaries']['initial_temperature_k']
    loss=0.; extrema=[initial,initial]; stored=0.
    # Preserve output on any failure as a visibly incomplete artifact.
    output.mkdir(parents=True)
    with ExitStack() as stack:
        series=stack.enter_context((output/'sensors.csv').open('w',newline=''))
        writer=csv.writer(series); writer.writerow(['time_s','sensor_id','temperature_k','hotspot_cell_id'])
        streams=([frames(stack.enter_context(open_field(run_dir/f'field_{z}.txt')),nx,ny) for z in range(nz)]
                 if kind=='reference' else [rc_frames(stack.enter_context((run_dir/'stdout.log').open()),grid,dt)])
        for step in range(1,count+1):
            values=[]
            for stream in streams:
                try: values.extend(next(stream))
                except StopIteration as exc: raise ValueError('truncated research field output') from exc
            extrema=[min(extrema[0],min(values)),max(extrema[1],max(values))]
            domain=ir['temperature_domain_k']
            if extrema[0]<domain[0]-1e-3 or extrema[1]>domain[1]+1e-3:
                raise ValueError('DOMAIN_FAILED: outside declared constant-property domain')
            loss += dt*sum(g*(v-t) for (g,t),v in zip(bounds,values))
            stored=sum(c['capacity_j_k']*(v-initial) for c,v in zip(grid['cells'],values))
            t=step*dt
            if close(t/receipt['observation_s'],round(t/receipt['observation_s'])) or step==count:
                for sid,value,cell in readings(mapping,values,grid): writer.writerow([t,sid,value,cell])
        for stream in streams:
            if next(stream,None) is not None: raise ValueError('unexpected extra field frames')
    energy=ir['power']['total_energy_j']
    if rc_energy:
        if not close(rc_energy['total_input_energy_j'],energy): raise ValueError('RC injected energy differs')
        loss=rc_energy['boundary_loss_j']; stored=rc_energy['stored_energy_change_j']
        extrema=[rc_energy['min_observed_k'],rc_energy['max_observed_k']]
    residual=energy-loss-stored
    result={'status':'POSTPROCESSED_NOT_MODEL_ACCEPTED','evidence':'CONDITIONAL_SIMULATED','backend':kind,
            'frames':count,'observations_s':receipt['observation_s'],'input_energy_j':energy,
            'storage_delta_j':stored,'boundary_energy_j':loss,'energy_residual_j':residual,
            'relative_energy_residual':abs(residual)/max(abs(energy),1.0),
            'temperature_range_k':extrema,'field_output_quantization_k':.001 if kind=='reference' else None,
            'boundary_integration':('backward Euler every solver-step field' if kind=='reference'
                                    else 'runner receipt integrated every solver step before output decimation'),
            'sensor_mapping':mapping,'initial_state_kind':'DECLARED_INPUT_NOT_MEASUREMENT'}
    (output/'observation_receipt.json').write_text(json.dumps(result,indent=2)+'\n')
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ('run-dir','generated','output'): parser.add_argument('--'+name,type=Path,required=True)
    parser.add_argument('--kind',choices=('reference','rc'),default='reference')
    args=parser.parse_args(); result=observe(args.run_dir,args.generated,args.output,args.kind)
    print(json.dumps({k:v for k,v in result.items() if k!='sensor_mapping'}))


if __name__=='__main__': main()
