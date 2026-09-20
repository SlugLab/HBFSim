#!/usr/bin/env python3
"""Isolated conditional four-topology flow/energy/thermal feedback runner."""
from __future__ import annotations
import argparse
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import platform
import resource
import subprocess
import sys
import time

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
MAINTENANCE = ROOT / 'experiments' / 'eq3_maintenance'
sys.path.insert(0, str(MAINTENANCE))
from thermal_client import ThermalService
from read_rate_policy import EngineeringProfile, ReadRatePolicy, StackWindowFacts, WindowFacts
from energy import EnergyMapper
from rate_workload import RateWorkload
from topology_service import TopologyService

WINDOW_NS = 20_000_000


def save(path, obj):
    Path(path).write_text(json.dumps(obj, indent=2, allow_nan=False) + '\n')


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def energy_activities(service):
    """Select disjoint scopes; fabric stage observations are not extra reads."""
    result = []
    for raw in service['activities']:
        row = dict(raw)
        phase = row['phase']
        if phase.startswith('media_'):
            if row['stack'].startswith('hbm') and row['operation'] == 'read':
                row['operation'] = 'hbm_read'
            result.append(row)
        elif phase == 'relay_receive':
            row['operation'] = 'relay_receive'
            result.append(row)
        elif phase == 'partner_gpu_drain':
            row['operation'] = 'relay_send'
            result.append(row)
    return result


def channel_map(normalized, config):
    result = {}
    for stack in config['fabric']['hbf']:
        dies = sorted((r for r in normalized['components']
                       if r['device_id'] == stack and r['role'] == 'array_die'),
                      key=lambda r: r['die_index'])
        channels = sorted(config['channels'][stack], key=int)
        if len(dies) != len(channels):
            raise ValueError('explicit one-channel/one-die scenario requires matching geometry')
        result[stack] = {c: d['id'] for c, d in zip(channels, dies)}
    return result


def percentile(histogram, pct=95):
    total = sum(r['bytes'] for r in histogram)
    cursor = 0
    for row in sorted(histogram, key=lambda r: r['delay_ns']):
        cursor += row['bytes']
        if cursor >= (total * pct + 99) // 100:
            return row['delay_ns']
    return None


def execute(config, normalized, thermal, sink):
    service = TopologyService(config['service'])
    mapping = channel_map(normalized, config['service'])
    workload = RateWorkload(mapping, config['workload'])
    energy = EnergyMapper(normalized, mapping, config['energy'])
    stacks = sorted(config['service']['channels'])
    hbf = sorted(mapping)
    baseline = {s: sum(config['service']['channels'][s].values()) * WINDOW_NS // 10**9
                for s in stacks}
    budgets = dict(baseline)
    states = {s: 'normal' for s in stacks}
    profiles = {s: EngineeringProfile(
        profile_id=config['point_id'] + ':' + s, enabled=True,
        strategy=config['strategy'], window_ns=WINDOW_NS,
        target_bytes_per_s=min(config['workload']['per_stack_Bps'],
                              sum(config['service']['channels'][s].values()) * 4 // 5),
        step_bytes=baseline[s] // 20, minimum_budget_bytes=baseline[s] // 10,
        maximum_budget_bytes=baseline[s], severe_budget_bytes=0, light_fraction=.5)
        for s in stacks}
    policies = {s: ReadRatePolicy(p) for s, p in profiles.items()}
    total_energy = 0.0
    peak = {}
    first = {}
    state_duration = {s: {v: 0 for v in ('normal','light','severe','shutdown')} for s in stacks}
    end = config['workload']['active_ns'] + config['recovery_ns']
    if end % WINDOW_NS:
        raise ValueError('run duration must align with thermal window')
    for start in range(0, end, WINDOW_NS):
        stop = start + WINDOW_NS
        offered = workload.advance(start, stop)
        receipt = service.advance(start, stop, offered, budgets, states)
        mapped = energy.map(energy_activities(receipt),
                            gpu_external_j=config.get('gpu_external_w', 0) * WINDOW_NS / 1e9)
        total_energy += mapped['total_j']
        heat = thermal.advance(start, stop, mapped['component_energy_j'])
        for owner, temperature in heat['temperatures'].items():
            peak[owner] = max(peak.get(owner, temperature), temperature)
            thresholds = thermal.limits['gpu' if owner == 'gpu' else owner[:3]]
            for label, limit in zip(('light','severe','shutdown'), thresholds):
                if temperature >= limit:
                    first.setdefault(owner + ':' + label, stop)
        observed = {s: heat['stack_states'][s] for s in stacks}
        next_budgets = {}
        decisions = {}
        for s in stacks:
            r = receipt['stacks'][s]
            delivered = r['delivered_effective_bytes']
            backlog = r['backlog_effective_bytes']
            utilization = min(1.0, delivered / baseline[s])
            facts = StackWindowFacts(
                stack_id=s, offered_bytes=r['offered_effective_bytes'], delivered_bytes=delivered,
                backlog_bytes=backlog, oldest_wait_ns=r['oldest_wait_ns'] or 0,
                latency_p95_ns=percentile(r['delivered_delay_histogram_bytes']),
                censored_requests=0, gate_limited=backlog > 0 and delivered >= budgets[s],
                backend_busy_fraction=utilization, resource_busy=utilization >= 1)
            decision = policies[s].evaluate(WindowFacts(
                start_ns=start, end_ns=stop, guard_state=observed[s], stacks=(facts,),
                current_budget_bytes={s: budgets[s]}, guard_states={s: observed[s]},
                hysteresis_budget_bytes={s: heat['hysteresis_budget_bytes'][s]}))
            next_budgets[s] = decision.stack_decisions[0].budget_bytes
            decisions[s] = asdict(decision)
            state_duration[s][observed[s]] += WINDOW_NS
        if config.get('control_disabled', False):
            next_budgets = dict(baseline)
            next_states = {s: 'normal' for s in stacks}
        else:
            next_states = observed
        row = {'start_ns': start, 'end_ns': stop, 'service': receipt, 'energy': mapped,
               'thermal': heat, 'control': {'budgets': budgets, 'next_budgets': next_budgets,
               'observed_states': observed, 'decisions': decisions,
               'facts': 'MODELLED_FLUID_NOT_NATIVE_BUSY_OR_BACKEND_LATENCY'},
               'reliability': {'status': 'NO_MAINTENANCE_DEMAND_IN_BASE_RATE_WORKLOAD'},
               'causal': None}
        sink.write(json.dumps(row, separators=(',', ':'), allow_nan=False) + '\n')
        budgets, states = next_budgets, next_states
    delivered = sum(receipt['stacks'][s]['cumulative_delivered_effective_bytes'] for s in hbf)
    backlog = sum(receipt['stacks'][s]['backlog_effective_bytes'] for s in hbf)
    if workload.total != delivered + backlog:
        raise AssertionError('end-to-end effective-byte conservation failed')
    thermal_energy = heat['energy_j']['cumulative']['total_input_j']
    if abs(thermal_energy-total_energy) > 1e-9 * max(1, total_energy):
        raise AssertionError('activity-to-thermal energy mismatch')
    return {'offered_bytes': workload.total, 'delivered_bytes': delivered,
            'backlog_bytes': backlog, 'energy_j': total_energy, 'peak_k_by_stack': peak,
            'first_threshold_ns': first, 'state_duration_ns': state_duration,
            'final_stacks': receipt['stacks'], 'thermal_energy_receipt': heat['energy_j']['cumulative'],
            'service_facts': service.immutable_facts(),
            'token_throughput': 'UNAVAILABLE_RATE_WORKLOAD_HAS_NO_TOKEN_DEPENDENCY_DAG',
            'external_gddr_temperature': 'UNAVAILABLE_OUTSIDE_PACKAGE_DOMAIN',
            'scope': 'CONDITIONAL_SIMULATED_NOT_MQSIM_TBPS_OR_CALIBRATED_HARDWARE'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('config', 'model-dir', 'thermal-binary', 'artifact-root', 'output'):
        parser.add_argument('--' + name, type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    started = time.monotonic()
    try:
        for key in ('OMP_NUM_THREADS','OPENBLAS_NUM_THREADS','MKL_NUM_THREADS','NUMEXPR_NUM_THREADS'):
            os.environ[key] = '1'
        os.environ['CUDA_VISIBLE_DEVICES'] = ''
        config = json.loads(args.config.read_text())
        save(args.output / 'config.json', config)
        manifest = {'started_utc': datetime.now(timezone.utc).isoformat(),
                    'environment_id': 'eq3-thermal-cpu-v1', 'python':sys.version,
                    'platform':platform.platform(), 'input_sha256':digest(args.config),
                    'source_sha256':{str(p.relative_to(ROOT)):digest(p) for p in
                        [HERE/name for name in ('run_system_point.py','energy.py','rate_workload.py','topology_service.py')]
                        + [MAINTENANCE/'thermal_client.py',MAINTENANCE/'read_rate_policy.py',ROOT/'tools'/'eq3_basic_fabric.py']},
                    'thermal_binary_sha256':digest(args.thermal_binary),
                    'source_revision':subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
                    'model_dir':str(args.model_dir.resolve()), 'cpu_threads':1,'gpu_count':0,
                    'resource_limits':config['resource_limits']}
        save(args.output / 'manifest.json', manifest)
        limit = config['resource_limits']['address_space_gib'] * 1024**3
        resource.setrlimit(resource.RLIMIT_AS, (limit,limit))
        resource.setrlimit(resource.RLIMIT_CORE, (0,0))
        normalized = json.loads((args.model_dir/'normalized.json').read_text())
        baseline = {s:sum(c.values())*WINDOW_NS//10**9 for s,c in config['service']['channels'].items()}
        with ThermalService(args.thermal_binary,args.model_dir,args.output/'thermal-process',
                            artifact_root=args.artifact_root,baseline_budgets=baseline,
                            limits=config.get('thermal_limits_k')) as thermal:
            manifest.update(thermal_model_lock=thermal.lock,thermal_header=thermal.header)
            save(args.output/'manifest.json',manifest)
            with (args.output/'windows.jsonl').open('w') as sink:
                summary = execute(config,normalized,thermal,sink)
        save(args.output/'DONE.json',{'status':'COMPLETED','summary':summary,
            'wall_s':time.monotonic()-started,
            'child_peak_rss_kib':resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss})
    except BaseException as exc:
        save(args.output/'FAILED.json',{'status':'FAILED','error':repr(exc),
                                     'wall_s':time.monotonic()-started,'raw_preserved':True})
        raise


if __name__ == '__main__':
    main()
