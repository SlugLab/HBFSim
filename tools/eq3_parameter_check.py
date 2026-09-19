#!/usr/bin/env python3
"""Read-only research-input checks. No solver, workload, or approval writing."""
import argparse
import hashlib
import itertools
import json
import math
from pathlib import Path

FIELDS = set('parameter_id physical_meaning consumer_path profile_id device_or_material '
             'scope source_value source_unit si_value_or_bounds normalized_unit source_id '
             'source_version_or_commit source_page_table_lines source_hash source_conditions '
             'evidence_kind transfer_assumptions selected_value_or_interval selection_reason '
             'derivation parent_parameters constraints_and_correlations status '
             'sensitivity_required claim_effect_if_unknown'.split())


def check(root):
    directory = root / 'configs/eq3_thermal/research'
    def load(name):
        return json.loads((directory / name).read_text())
    p, registry, power, plan = [load(n) for n in
        ('candidate_profile.json', 'parameter_registry.json', 'calibration_power.json', 'calibration_plan.json')]
    rows = registry['parameters']
    assert all(FIELDS <= set(r) for r in rows), 'missing decision fields'
    ids = {r['parameter_id'] for r in rows}
    assert len(ids) == len(rows), 'duplicate parameter id'
    for r in rows:
        assert r['source_id'] in registry['sources']
        for parent in r['parent_parameters']:
            assert parent in ids or '*' in parent or parent == 'package.geometry', parent
    b, materials = p['blocks'], p['materials']
    assert len({x['id'] for x in b}) == len(b)
    for x in b:
        assert x['material'] in materials
        assert all(math.isfinite(t) and t > 0 for t in x['size_um'])
        assert all(0 <= a and a+t <= bound for a,t,bound in
                   zip(x['xyz_um'], x['size_um'], p['package_size_um']))
    for a,c in itertools.combinations(b, 2):
        assert not all(min(x+t,y+u) > max(x,y) for x,t,y,u in
            zip(a['xyz_um'],a['size_um'],c['xyz_um'],c['size_um'])), (a['id'],c['id'])
    for m in materials.values():
        assert m['rho_kg_m3'] > 0 and m['cp_j_kg_k'] > 0
        assert all(k > 0 for k in m['k_w_m_k'])
    powered = [x for x in b if x['power_group']]
    assert len(powered) == 121 and len({x['power_group'] for x in powered}) == 17
    assert set(power['group_order']) == {x['power_group'] for x in powered}
    for device, count in [('HBM4',48), ('HBF',64)]:
        assert sum(x['device']==device and '.die' in x['id'] for x in powered)==count
    cap = sum(math.prod(x['size_um'])*1e-18*materials[x['material']]['rho_kg_m3']*
              materials[x['material']]['cp_j_kg_k'] for x in b)
    area = math.prod(p['package_size_um'][:2])*1e-12
    bg = area*900e-6-sum(math.prod(x['size_um'])*1e-18 for x in b if x['device']!='package')
    assert bg > 0
    cap += bg*materials['underfill']['rho_kg_m3']*materials['underfill']['cp_j_kg_k']
    z = {q for x in b for q in (x['xyz_um'][2], x['xyz_um'][2]+x['size_um'][2])}
    energies = {}
    for name,t in power['traces'].items():
        assert len(t['slots_W'])*power['slot_s'] == t['duration_s']
        assert all(len(v)==17 and all(0 <= w <= c for w,c in zip(v,power['caps_W']))
                   for v in t['slots_W'])
        energies[name] = math.fsum(math.fsum(v)*power['slot_s'] for v in t['slots_W'])
    # Independent nonzero single-group rows prove full column rank of these17 inputs.
    isolated = {next(i for i,w in enumerate(v) if w) for v in power['traces']['train']['slots_W']
                if sum(w != 0 for w in v)==1}
    assert len(isolated)==17
    assert len(plan['runs'])==10 and sum(r['duration_s'] for r in plan['runs'])==856
    assert sum(r['solver']=='reference' for r in plan['runs'])==7
    assert plan['status']=='PENDING_USER_APPROVAL' and plan['approval_record'] is None
    assert plan['construction']['physical_fit_parameter_count']==0
    for r in rows:
        if r['parameter_id'] in ('ocp.low','ocp.middle','ocp.high'):
            assert r['selected_value_or_interval']['virtual_AXI_multiplicity']==[1,2,4]
    return dict(status='STATIC_CHECK_PASSED_NOT_MODEL_VALIDATION', parameter_rows=len(rows),
                explicit_blocks=len(b), powered_regions=len(powered), source_rank=17,
                z_slabs=len(z)-1, cells={str(d):int(area/(d*1e-6)**2*(len(z)-1))
                                        for d in p['mesh_candidates_um']},
                total_C_J_K=cap, approximate_C_over_boundary_G_s=cap/((1400+25)*area),
                input_energy_J=energies, numerical_runs_started=0,
                input_sha256={f.name:hashlib.sha256(f.read_bytes()).hexdigest()
                              for f in sorted(directory.glob('*.json'))})


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path('.'))
    print(json.dumps(check(parser.parse_args().root), indent=2, sort_keys=True))
