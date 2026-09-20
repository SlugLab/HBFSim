#!/usr/bin/env python3
"""Explicit read-rate thermal scenario; never creates MQSim transactions."""
from __future__ import annotations

import argparse
import csv
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import platform
import resource
import shutil
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'experiments' / 'eq3_maintenance'))
from thermal_client import ThermalService
from rate_inputs import build_windows


def save(path, value):
    path.write_text(json.dumps(value, indent=2, allow_nan=False) + '\n')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('profile', 'schedule', 'model-dir', 'thermal-binary', 'artifact-root', 'output'):
        parser.add_argument('--' + name, type=Path, required=True)
    parser.add_argument('--address-limit-gib', type=int, default=4)
    args = parser.parse_args()
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=False)
    started = time.monotonic()
    try:
        for key in ('OMP_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'MKL_NUM_THREADS', 'NUMEXPR_NUM_THREADS'):
            os.environ[key] = '1'
        os.environ['CUDA_VISIBLE_DEVICES'] = ''
        profile = json.loads(args.profile.read_text())
        schedule = json.loads(args.schedule.read_text())
        normalized = json.loads((args.model_dir / 'normalized.json').read_text())
        inputs = build_windows(profile, schedule, normalized)
        save(out / 'profile.json', profile)
        save(out / 'schedule.json', schedule)
        save(out / 'load-windows.json', inputs)
        manifest = dict(
            experiment_kind='READ_RATE_DRIVEN_INCREMENTAL_THERMAL',
            started_utc=datetime.now(timezone.utc).isoformat(),
            python=sys.version, platform=platform.platform(),
            source_sha256={name: hashlib.sha256((Path(__file__).parent / name).read_bytes()).hexdigest()
                           for name in ('run_rate_thermal.py', 'rate_inputs.py')},
            thermal_binary=str(args.thermal_binary.resolve()),
            thermal_binary_sha256=hashlib.sha256(args.thermal_binary.read_bytes()).hexdigest(),
            model_dir=str(args.model_dir.resolve()),
            model_domain_k=[300, 400], step_ns=20_000_000,
            parameter_provenance=profile.get('provenance'),
            requested_rate_is='SCENARIO_INPUT_NOT_MEASURED_BACKEND_THROUGHPUT',
            idle_power='UNKNOWN_NOT_INCLUDED', gpu_external_power='ZERO_INCREMENT_NOT_PHYSICAL_IDLE',
            mqsim_used=False, active_thermal_control=False,
            physical_qualification='CONDITIONAL_ENGINEERING_USE_NOT_MODEL_FREEZE',
            address_limit_gib=args.address_limit_gib, cpu_threads=1, gpu_count=0,
            disk_free_bytes=shutil.disk_usage(out).free)
        save(out / 'manifest.json', manifest)
        if args.address_limit_gib <= 0:
            raise ValueError('address limit must be positive')
        resource.setrlimit(resource.RLIMIT_AS, (args.address_limit_gib * 1024**3,) * 2)
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
        total_input = 0.0
        last = None
        peaks = {}
        maximum_energy_error = 0.0
        with (out / 'thermal.jsonl').open('w') as frames, (out / 'stack-temperatures.csv').open('w') as csvfile:
            writer = csv.DictWriter(csvfile, fieldnames=['time_ns', 'stack', 'temperature_k',
                                                         'increment_above_300k', 'guard_observation'])
            writer.writeheader()
            with ThermalService(args.thermal_binary, args.model_dir, out / 'thermal-process',
                                artifact_root=args.artifact_root) as thermal:
                manifest['model_file_sha256'] = thermal.lock
                save(out / 'manifest.json', manifest)
                for window in inputs['windows']:
                    energy = window['component_energy_j']
                    total_input += sum(energy.values())
                    last = thermal.advance(window['start_ns'], window['end_ns'], energy)
                    frames.write(json.dumps(last, allow_nan=False) + '\n')
                    cumulative = last['energy_j']['cumulative']
                    error = abs(cumulative['total_input_j'] - total_input)
                    maximum_energy_error = max(maximum_energy_error, error)
                    if error > 1e-9 * max(1.0, total_input):
                        raise ValueError('load energy differs from thermal input receipt')
                    for stack, temperature in last['temperatures'].items():
                        peaks[stack] = max(peaks.get(stack, temperature), temperature)
                        writer.writerow(dict(time_ns=window['end_ns'], stack=stack,
                                             temperature_k=temperature, increment_above_300k=temperature - 300.0,
                                             guard_observation=last['stack_states'].get(stack, 'UNKNOWN')))
        if last is None:
            raise ValueError('no thermal window')
        save(out / 'DONE.json', dict(
            execution_status='COMPLETED', capability_status='RATE_TO_ENERGY_TO_COUPLED_THERMAL',
            scientific_scope='CONDITIONAL_INCREMENTAL_READ_HEATING',
            simulated_end_ns=last['end_ns'], input_energy_j=total_input,
            thermal_input_error_max_j=maximum_energy_error,
            thermal_energy_receipt=last['energy_j'],
            peak_k_by_stack=peaks,
            peak_increment_k_by_stack={key: value - 300.0 for key, value in peaks.items()},
            final_k_by_stack=last['temperatures'],
            wall_s=time.monotonic() - started,
            child_peak_rss_kib=resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss,
            actual_reads_or_writes=False, idle_power_included=False,
            thermal_control='OBSERVED_ONLY_NOT_APPLIED', source_hashes=manifest['source_sha256']))
        return 0
    except BaseException as exc:
        save(out / 'FAILED.json', dict(execution_status='FAILED', error=repr(exc),
                                      wall_s=time.monotonic() - started,
                                      evidence='thermal-process transcript retains trial failure; no clamp'))
        raise


if __name__ == '__main__':
    raise SystemExit(main())
