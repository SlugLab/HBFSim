#!/usr/bin/env python3
"""Freeze selected acquired public inputs; arithmetic summary, not calibration."""
import argparse
import csv
import hashlib
import json
from pathlib import Path
import subprocess


def digest(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


def inventory(root, name, url, license_status):
    directory = root / name
    revision = subprocess.check_output(['git', '-C', str(directory),
                                        'rev-parse', 'HEAD'], text=True).strip()
    files = [{ 'path': p.relative_to(directory).as_posix(),
               'sha256': digest(p), 'bytes': p.stat().st_size}
             for p in sorted(directory.rglob('*'))
             if p.is_file() and '.git' not in p.relative_to(directory).parts]
    return dict(repository=url, revision=revision, license=license_status,
                acquisition_date='2026-09-19', files=files)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--reference-root', type=Path, required=True)
    ap.add_argument('--output', type=Path, required=True)
    args = ap.parse_args()
    data = dict(classification='SOURCE_ACQUISITION_AND_DERIVED_ARITHMETIC',
                historical_reproduction=False, gpu_workload_launched=False)
    data['MFIT'] = inventory(args.reference_root, 'MFIT',
        'https://github.com/AlishKanani/MFIT', 'GPL-3.0; see frozen LICENSE')
    data['HBM-Power'] = inventory(args.reference_root, 'HBM-Power',
        'https://github.com/CMU-SAFARI/HBM-Power',
        'UNKNOWN root dataset license; component licenses do not imply dataset license')
    csv_path = args.reference_root/'HBM-Power/data/HBM3E_measurements.csv'
    rows = list(csv.DictReader(csv_path.open(newline='')))
    derived = []
    for r in rows:
        total, idle, bw = (float(r[k]) for k in
                          ['total_power_W', 'idle_power_W', 'read_GBs'])
        if not 0 <= idle <= total or bw <= 0:
            raise ValueError('invalid source power / bandwidth')
        derived.append(dict(sample=r['sample'], aggregate_memory_power_W=total,
            idle_memory_power_W=idle, read_GB_s=bw,
            marginal_read_energy_pJ_per_byte=(total-idle)/bw*1000))
    data['HBM3E_summary'] = dict(rows=derived, count=len(rows),
        source_sha256=digest(csv_path), temperature_trace_available=False,
        time_column_available=False, per_stack_power_available=False,
        interpretation='Memory-domain aggregate; pJ/B is derived at three close '
        'bandwidth points, not a fitted power law or HBM4/HBF transfer parameter.')
    for filename in ['all_idd_measurements.csv', 'ground_truth_allzeros.csv',
                     'ground_truth_random.csv']:
        p = args.reference_root/'HBM-Power/data'/filename
        with p.open(newline='') as f:
            reader = csv.reader(f)
            header = next(reader)
            count = sum(1 for _ in reader)
        data.setdefault('HBM2_csv_inventory', {})[filename] = dict(
            columns=header, row_count=count, sha256=digest(p),
            units='Retain raw values; IDD/IPP and power_vdd units require '
                  'upstream measurement/model confirmation before conversion')
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open('x') as f:
        json.dump(data, f, indent=2)
        f.write('\n')
    print(json.dumps(data['HBM3E_summary'], indent=2))


if __name__ == '__main__':
    main()
