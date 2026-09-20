#!/usr/bin/env python3
"""Static, conservative layered input export; never imports or starts a solver.

Uniform xy for stock 3D-ICE; orthogonal geometry-edge Cartesian RC discretization.
The latter is deliberately NOT truncated to a requested node budget.
"""
import argparse
from collections import defaultdict
import hashlib
import json
import math
from pathlib import Path


def number(x):
    return format(x, '.17g')


def close(a, b):
    return math.isclose(a, b, rel_tol=1e-10, abs_tol=1e-14)


def axes(ir, mesh_m=None):
    out = []
    for axis, extent in enumerate(ir['package_size_m']):
        planes = {0.0, extent}
        for c in ir['components']:
            planes.update((c['xyz_m'][axis], c['xyz_m'][axis]+c['size_m'][axis]))
        bg = ir.get('background')
        if bg:
            planes.update(bg['z_range_m'] if axis == 2 else
                          (bg['xy_extent_m'][axis], bg['xy_extent_m'][axis+2]))
        # Numerical coordinate deduplication, not geometry movement.
        unique = []
        for x in sorted(planes):
            if not unique or not close(unique[-1], x):
                unique.append(x)
        if axis < 2 and mesh_m is not None:
            if not math.isfinite(mesh_m) or mesh_m <= 0:
                raise ValueError('mesh must be finite and positive')
            if any(not close(x/mesh_m, round(x/mesh_m)) for x in unique):
                raise ValueError('BACKEND_GAP: uniform xy grid must align every entity edge; no snapping')
            unique = [i*mesh_m for i in range(round(extent/mesh_m)+1)]
        out.append(unique)
    return out


def discretize(ir, mesh_m=None):
    a = axes(ir, mesh_m)
    nx, ny, nz = (len(v)-1 for v in a)
    if nx*ny*nz > 400000:
        raise ValueError('static export safety guard: >400000 cells; review generation memory first')
    cells, mapping = [], defaultdict(list)
    components = sorted(ir['components'], key=lambda c: c['id'])
    bg = ir.get('background')
    for z in range(nz):
        active = [c for c in components if
                  c['xyz_m'][2]-1e-14 <= a[2][z] and
                  c['xyz_m'][2]+c['size_m'][2]+1e-14 >= a[2][z+1]]
        for y in range(ny):
            for x in range(nx):
                xyz = [a[0][x], a[1][y], a[2][z]]
                size = [a[i][j+1]-a[i][j] for i,j in enumerate((x,y,z))]
                center = [v+d/2 for v,d in zip(xyz,size)]
                owners = [c for c in active if all(c['xyz_m'][i]-1e-14 <= center[i] <
                          c['xyz_m'][i]+c['size_m'][i]-1e-14 for i in (0,1))]
                if len(owners) > 1:
                    raise ValueError('overlapping entity cells')
                if owners:
                    owner = owners[0]
                    cid, mat = owner['id'], owner['material']
                elif bg and bg['z_range_m'][0]-1e-14 <= center[2] <= bg['z_range_m'][1]+1e-14 and all(
                        bg['xy_extent_m'][i]-1e-14 <= center[i] <= bg['xy_extent_m'][i+2]+1e-14 for i in (0,1)):
                    cid, mat = '__background__', bg['material']
                else:
                    raise ValueError(f'unfilled geometry at {center}')
                material = ir['materials'][mat]
                vol = math.prod(size)
                cell = {'id':f'n{z}_{y}_{x}', 'index':len(cells), 'xyz_index':[x,y,z],
                        'component':cid, 'material':mat, 'xyz_m':xyz, 'size_m':size,
                        'center_m':center, 'volume_m3':vol,
                        'capacity_j_k':vol*material['cv_j_m3_k'],
                        'k_xyz_w_m_k':material['k_xyz_w_m_k']}
                mapping[cid].append(len(cells)); cells.append(cell)
    for c in components:
        if not close(sum(cells[i]['volume_m3'] for i in mapping[c['id']]), math.prod(c['size_m'])):
            raise ValueError('entity volume not conserved: '+c['id'])
    return {'axes_m':a, 'shape':[nx,ny,nz], 'cells':cells,
            'component_cells':dict(mapping)}


def network(ir, grid):
    cells = grid['cells']; nx,ny,nz = grid['shape']
    edges, boundaries = [], []
    for c in cells:
        x,y,z = c['xyz_index']; size = c['size_m']; i=c['index']
        for axis, valid, shift in ((0,x+1<nx,1),(1,y+1<ny,nx),(2,z+1<nz,nx*ny)):
            if not valid:
                continue
            d=cells[i+shift]
            area=math.prod(size[j] for j in range(3) if j!=axis)
            resistance=size[axis]/(2*c['k_xyz_w_m_k'][axis])+d['size_m'][axis]/(2*d['k_xyz_w_m_k'][axis])
            edges.append([i,i+shift,area/resistance])
        conductance, heat_rhs = 0.0,0.0
        for side, enabled in (('bottom',z==0),('top',z==nz-1)):
            if not enabled:
                continue
            bc=ir['boundaries'][side]; h=bc['h_w_m2_k']
            g=0.0 if h==0 else size[0]*size[1]/(size[2]/(2*c['k_xyz_w_m_k'][2])+1/h)
            conductance+=g; heat_rhs+=g*bc['ambient_k']
        ambient=heat_rhs/conductance if conductance else ir['boundaries']['initial_temperature_k']
        boundaries.append([conductance,ambient])
    return edges,boundaries


def sampled_power(ir, slot_s):
    intervals=ir['power']['intervals']
    duration=max(row['end_s'] for row in intervals)
    if not math.isfinite(slot_s) or slot_s<=0 or not close(duration/slot_s,round(duration/slot_s)):
        raise ValueError('sampling interval must divide trace duration')
    # No time averaging of discontinuities: stock slots must express the input exactly.
    for row in intervals:
        for key in ('start_s','end_s'):
            if not close(row[key]/slot_s,round(row[key]/slot_s)):
                raise ValueError('BACKEND_GAP: power transition not aligned to output/power slot')
    ids=sorted(c['id'] for c in ir['components'] if c['powered'])
    result={cid:[0.0]*round(duration/slot_s) for cid in ids}
    for row in intervals:
        for cid,w in row['power_w'].items():
            for i in range(round(row['start_s']/slot_s),round(row['end_s']/slot_s)):
                result[cid][i]+=w
    return result


def reference_files(ir, grid, step_s, sample_s):
    nx,ny,nz=grid['shape']; a=grid['axes_m']
    if nz==1:
        raise ValueError('BACKEND_GAP: stock single z layer cannot have both HTC boundaries')
    if not math.isfinite(step_s) or step_s<=0 or not close(sample_s/step_s,round(sample_s/step_s)):
        raise ValueError('solver step must divide sampling/power slot')
    materials={name:f'M{i}' for i,name in enumerate(sorted(ir['materials']))}
    lines=[]; files={}; floor_maps=[]
    for name, token in materials.items():
        mat=ir['materials'][name]
        lines += [f'material {token} :', ' thermal conductivity '+', '.join(number(k*1e-6) for k in mat['k_xyz_w_m_k'])+' ;',
                  f" volumetric heat capacity {number(mat['cv_j_m3_k']*1e-18)} ;"]
    for side in ('top','bottom'):
        bc=ir['boundaries'][side]
        lines += [f'{side} heat sink :',f" heat transfer coefficient {number(bc['h_w_m2_k']*1e-12)} ;",
                  f" temperature {number(bc['ambient_k'])} ;"]
    lines += ['dimensions :',f' chip length {number(a[0][-1]*1e6)}, width {number(a[1][-1]*1e6)} ;',
              f' cell length {number((a[0][1]-a[0][0])*1e6)}, width {number((a[1][1]-a[1][0])*1e6)} ;']
    powers=sampled_power(ir,sample_s); component={c['id']:c for c in ir['components']}
    for z in range(nz):
        slice_cells=grid['cells'][z*nx*ny:(z+1)*nx*ny]
        mats=defaultdict(list); flp=[]; layer_map=[]
        for cell in slice_cells:
            x,y,_=cell['xyz_m']; dx,dy,dz=cell['size_m']; cid=cell['component']
            rect=', '.join(number(v*1e6) for v in (x,y,dx,dy))
            mats[materials[cell['material']]].append(f' rectangle ({rect});')
            token=f'C{cell["index"]}'
            # Each floorplan rectangle is exactly one cell, so stock area allocation
            # cannot duplicate component total watts across cells or z slices.
            weight=cell['volume_m3']/math.prod(component[cid]['size_m']) if cid in powers else 0.0
            values=[p*weight for p in powers[cid]] if cid in powers else [0.0]*len(next(iter(powers.values())))
            flp += [f'{token} :',f' position {number(x*1e6)}, {number(y*1e6)} ;',
                    f' dimension {number(dx*1e6)}, {number(dy*1e6)} ;',
                    ' power values '+', '.join(number(v) for v in values)+' ;']
            layer_map.append({'cell_id':cell['id'],'floorplan_id':token,'component':cid,'source_weight':weight})
        files[f'L{z}.layout']='\n'.join(token+' :\n'+'\n'.join(rects) for token,rects in sorted(mats.items()))+'\n'
        files[f'L{z}.flp']='\n'.join(flp)+'\n'
        lines += [f'layer L{z} :',f' height {number((a[2][z+1]-a[2][z])*1e6)} ;',
                  f' material {next(iter(materials.values()))} ;',f' layout "L{z}.layout" ;']
        floor_maps.append(layer_map)
    for z in range(nz): lines += [f'die D{z} :',f' source L{z} ;']
    lines += ['stack:']+[f' die S{z} D{z} floorplan "L{z}.flp" ;' for z in reversed(range(nz))]
    lines += ['solver:',f' transient step {number(step_s)}, slot {number(sample_s)} ;',
              f" initial temperature {number(ir['boundaries']['initial_temperature_k'])} ;",' numofcores 1 ;','output:']
    # Full fields at every solver step are required for a backward-Euler boundary
    # energy integral. Observation decimation happens only after that accounting.
    lines += [f' Tmap ( S{z}, "field_{z}.txt", step );' for z in range(nz)]
    files['package.stk']='\n'.join(lines)+'\n'
    return files,floor_maps


def rc_files(ir, grid):
    edges,boundaries=network(ir,grid)
    comp={c['id']:c for c in ir['components']}
    lines=['HBFSIM_EQ3_THERMAL_MODEL 1','coupling on']
    for c,(g,t) in zip(grid['cells'],boundaries):
        entity=comp.get(c['component'],{}); kind=entity.get('physical_type','package').upper()
        physical='hbm' if kind.startswith('HBM') else 'hbf' if kind=='HBF' else 'gpu' if kind=='GPU' else 'other'
        role={'hbm':'fast_memory','hbf':'capacity_memory','gpu':'compute','other':'package'}[physical]
        lines.append(f"node {c['id']} {physical} {role} {c['component']} -1 {number(c['capacity_j_k'])} {number(ir['boundaries']['initial_temperature_k'])} 0 {number(g)} {number(t)}")
    lines += [f"edge {grid['cells'][i]['id']} {grid['cells'][j]['id']} {number(g)} component" for i,j,g in edges]
    events=['HBFSIM_EQ3_THERMAL_EVENTS 1']; eid=0; emitted=0.0
    for row in ir['power']['intervals']:
        dt=row['end_s']-row['start_s']
        for cid,w in sorted(row['power_w'].items()):
            if not w: continue
            eid+=1; assignments=[]; totalvol=math.prod(comp[cid]['size_m'])
            for i in grid['component_cells'][cid]:
                c=grid['cells'][i]; e=w*dt*c['volume_m3']/totalvol
                assignments.append(f"{c['id']} {number(e)}"); emitted+=e
            events.append(f"activity {eid} {eid} external_heat external 0 -1 -1 -1 {number(row['start_s'])} {number(row['end_s'])} {number(row['end_s'])} "+' '.join(assignments))
    if not close(emitted,ir['power']['total_energy_j']): raise ValueError('RC energy not conserved')
    return {'model.txt':'\n'.join(lines)+'\n','events.txt':'\n'.join(events)+'\n'}, {'edge_count':len(edges),'emitted_energy_j':emitted}


def generate(ir, output, mesh_m, step_s, sample_s=0.1, rc_node_budget=512,
             rc_mesh_m=None):
    from eq3_layered_observe import sensor_mapping
    if ir['boundaries'].get('additional_area_contact_resistance_m2_k_W',0)!=0:
        raise ValueError('BACKEND_GAP: nonzero residual contact not supported by stock exporter')
    if not str(ir['boundaries']['sides']).startswith('adiabatic'):
        raise ValueError('BACKEND_GAP: lateral boundary must be explicitly adiabatic')
    ref=discretize(ir,mesh_m); rc=discretize(ir,rc_mesh_m)
    files,floormaps=reference_files(ir,ref,step_s,sample_s)
    rcfiles,rcreceipt=rc_files(ir,rc); files.update(rcfiles)
    receipt={'status':'GENERATED_NOT_SOLVED','schema_version':'eq3-layered-export-v1',
        'entity_count':len(ir['components']),'z_layers':ref['shape'][2],
        'reference_shape':ref['shape'],'reference_cells':len(ref['cells']),
        'rc_shape':rc['shape'],'rc_nodes':len(rc['cells']),
        'rc_discretization':('orthogonal Cartesian all-geometry-edge planes; no fit, no removed layers'
                             if rc_mesh_m is None else
                             'uniform XY aligned to every geometry edge; original Z interface planes; no fit, no removed layers'),
        'rc_mesh_m':rc_mesh_m,
        'rc_numerical_variant':'geometry_edges' if rc_mesh_m is None else 'explicit_uniform_xy',
        'rc_proposed_node_budget':rc_node_budget,
        'rc_execution_readiness':'BLOCKED_NODE_BUDGET' if len(rc['cells'])>rc_node_budget else 'PENDING_EXECUTION_APPROVAL',
        'rc_dense_bytes_per_matrix':8*len(rc['cells'])**2,
        'rc_memory_estimate_kind':'ANALYTICAL_LOWER_BOUND_PER_MATRIX_NOT_MEASURED; factor cache may grow',
        'powered_components':sum(c['powered'] for c in ir['components']),
        'sensor_count':len(ir['sensors']), 'power_interval_count':len(ir['power']['intervals']),
        'duration_s':max(r['end_s'] for r in ir['power']['intervals']),
        'input_energy_j':ir['power']['total_energy_j'],
        'reference_capacity_j_k':sum(c['capacity_j_k'] for c in ref['cells']),
        'rc_capacity_j_k':sum(c['capacity_j_k'] for c in rc['cells']),
        'step_s':step_s,'observation_s':sample_s,'research_solver_started':False,
        'reference_parse_status':'NOT_YET_PARSED', **rcreceipt}
    receipt['reference_field_bytes_estimated']=len(ref['cells'])*round(receipt['duration_s']/step_s)*9
    receipt['reference_output_estimate_kind']='ANALYTICAL_9_BYTES_PER_CELL_STEP_EXCLUDES_HEADERS_AND_INPUTS_NOT_MEASURED'
    receipt['reference_output_readiness']=('BLOCKED_OUTPUT_BUDGET' if receipt['reference_field_bytes_estimated']>4*1024**3
                                            else 'PENDING_RESOURCE_PILOT_APPROVAL')
    receipt['base_die_mapping']=[{'component_id':c['id'],
        'reference_cells':[ref['cells'][i]['id'] for i in ref['component_cells'][c['id']]],
        'rc_nodes':[rc['cells'][i]['id'] for i in rc['component_cells'][c['id']]],
        'capacity_j_k':sum(rc['cells'][i]['capacity_j_k'] for i in rc['component_cells'][c['id']]),
        'input_energy_j':ir['power'].get('component_energy_j',{}).get(c['id']),
        'sensor_ids':[s['id'] for s in ir['sensors'] if s['id'].startswith('component:'+c['id']+':')]}
        for c in ir['components'] if c.get('role')=='base_die']
    if not close(receipt['reference_capacity_j_k'],receipt['rc_capacity_j_k']):
        raise ValueError('backend capacities differ')
    # Mapping and native inputs are independent of machine paths.
    for name,obj in [('normalized.json',ir),('reference_grid.json',ref),('rc_grid.json',rc),('floorplan_map.json',floormaps),
                     ('reference_sensors.json',sensor_mapping(ir,ref)),('rc_sensors.json',sensor_mapping(ir,rc))]:
        files[name]=json.dumps(obj,sort_keys=True,separators=(',',':'),allow_nan=False)+'\n'
    receipt['generated_sha256']={name:hashlib.sha256(text.encode()).hexdigest() for name,text in sorted(files.items())}
    output=Path(output); output.mkdir(parents=True,exist_ok=False)
    for name,text in files.items(): (output/name).write_text(text)
    (output/'generation_receipt.json').write_text(json.dumps(receipt,indent=2)+'\n')
    return receipt


def main():
    from eq3_layered_ir import normalize
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('profile','power','output'): p.add_argument('--'+name,type=Path,required=True)
    p.add_argument('--trace',required=True); p.add_argument('--mesh-um',type=float,required=True)
    p.add_argument('--rc-mesh-um',type=float,default=None,
                   help='Optional independent RC XY mesh; preserves all original edges/Z interfaces; no snapping')
    p.add_argument('--step-s',type=float,required=True); p.add_argument('--sample-s',type=float,default=.1)
    args=p.parse_args()
    ir=normalize(json.loads(args.profile.read_text()),json.loads(args.power.read_text()),args.trace)
    print(json.dumps(generate(ir,args.output,args.mesh_um*1e-6,args.step_s,args.sample_s,
                             rc_mesh_m=None if args.rc_mesh_um is None else args.rc_mesh_um*1e-6),indent=2))


if __name__=='__main__': main()
