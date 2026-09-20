"""Freeze the user-requested independent continuation behind an active stage."""
import argparse
import json
from pathlib import Path
import sys

from freeze_extension import HERE, ROOT, digest, freeze, save
from prepare_causal_main import make_main, specs
from prepare_temperature_retry_campaign import prepare


def main(stage, destination):
    destination.mkdir(parents=True, exist_ok=False)
    pilot = stage / 'temperature-retry-pilot-v1'
    prepare(stage, pilot, 'pilot', dependency=stage / 'BASE_DONE.json')
    causal = stage / 'causal-main-v2'
    causal.mkdir(); (causal / 'inputs').mkdir()
    paths = []
    for spec in specs():
        config = make_main(*spec)
        config['resource_limits']['watchdog_s'] = 2400
        path = causal / 'inputs' / (config['point_id'] + '.json')
        save(path, config); paths.append(path)
    index = freeze(stage, causal, paths, HERE / 'run_causal_point.py',
                   point_wall_s=2400, dependency=stage / 'BASE_DONE.json')
    index['resources'].update(stage_wall_s=43200, point_output_gib=4,
        sensitivity_output_gib=80, parent_combined_output_gib=200)
    index['authorization'] = 'USER_EXPLICIT_QUEUE_INDEPENDENT_ABLATIONS_AFTER_DEPENDENCY_REVIEW'
    index['dependency_review'] = {
        'ecc_mode': 'DISABLED', 'policy': 'EXISTING_FIXED_THREE_POLICIES_NOT_NEW_ECC_POLICY',
        'new_ecc_success_required': False,
        'limits': 'Results conditional on fixed no-ECC policy; no ECC+placement claim',
        'readiness': 'Existing service probes, coalescing Severe/Normal probe, migration read pin regression; HBM4 identity tests',
        'runtime_projection': 'Prior four-topology short null-ECC pilots suggest hours; 7.35h rough projection, not a measured main runtime'}
    save(causal / 'EXECUTION_INDEX.json', index)
    (causal / 'PREFLIGHT.md').write_text(
        '# Independent causal ablations\n\n'
        '35 points from the existing make_main design: four topology policy baselines, '
        'prefetch, issue/consumption, coalescing, placement, cache and static retry controls. '
        '20s input +10s drain; original weights, HBM4, thermal networks and 40+10pJ/B retained. '
        'Default ECC off; no dependency on new controller benefit. Counterfactuals use matched inputs. '
        'Tiny-model structure scaled to target tensor sizes, not native hardware token throughput.\n\n'
        'Serial CPU1/BLAS1/GPU0; 2400s/point, 43200s stage, 4GiB/point,80GiB stage, '
        '200GiB retained parent,32GiB available host RAM and100GiB disk reserve. '
        'Retain domain failures and stop stage on execution errors; preserve earlier evidence. '
        'Consume recorded commands/activity, bytes/energy conservation, causal completions and six-panel figures. '
        'No qualified thermal fast path or ECC+dynamic placement composition claimed.\n')
    sens = stage / 'sensitivity-controlled-v2'
    phases = []
    def phase(name, script, args, analysis=False):
        row = {'id': name, 'command': [sys.executable, str(HERE / script), *map(str, args)]}
        if analysis: row['analysis_timeout_s'] = 3600
        phases.append(row)
    phase('sensitivity-analysis', 'analyze_independent_stage.py',
          ['--index', sens/'EXECUTION_INDEX.json', '--output', sens/'QUEUED_ANALYSIS01'], True)
    for name, folder in [('temperature-retry', pilot), ('independent-causal', causal)]:
        phase(name+'-run', 'launch_sensitivity.py', ['--index', folder/'EXECUTION_INDEX.json'])
        phase(name+'-analysis', 'analyze_extension_stage.py',
              ['--index', folder/'EXECUTION_INDEX.json', '--output', folder/'ANALYSIS01'], True)
    locks = {str(HERE/name): digest(HERE/name) for name in (
        'continue_queue.py', 'analyze_independent_stage.py', 'analyze_extensions.py',
        'analyze_extension_stage.py', 'launch_sensitivity.py')}
    for folder in (sens, pilot, causal):
        path = folder / 'EXECUTION_INDEX.json'; locks[str(path)] = digest(path)
    manifest = {'source_root': str(ROOT), 'wait_for_stage': str(sens),
        'wait_timeout_s': 10800, 'check_interval_s': 300,
        'phases': phases, 'locks': locks, 'ai_wakeup': 'UNAVAILABLE_IN_CURRENT_TOOLS',
        'authorization': 'LATEST_USER_REQUEST_AUTOMATIC_SERIAL_QUEUE_AND_ANALYSIS',
        'failure_policy': 'Preserve failures; continue independent frozen stages only after CPU released; no input adaptation'}
    save(destination/'QUEUE.json', manifest)
    (destination/'README.md').write_text(
        '# Serial continuation\n\nCurrent sensitivity → analysis →8 ECC temperature-feedback pilots '
        '→ analysis →35 no-ECC causal/ablation points → analysis.\n\n'
        'Independent ablations do not require new controller success. New-controller joint conclusions '
        'and unsupported ECC+placement remain pending scientific review.\n\n'
        'The waiting supervisor checks completion every300s without invoking AI, then waits on child exit. '
        'Per-point launchers retain15s resource safety checks. QUEUE_DONE is an artifact, not an AI callback. '
        'Automatic agent wakeup is unavailable in current tools. Do not launch competing thermal jobs.\n')


if __name__ == '__main__':
    p = argparse.ArgumentParser(); p.add_argument('--stage', type=Path, required=True)
    p.add_argument('--destination', type=Path, required=True); a = p.parse_args()
    main(a.stage.resolve(), a.destination.resolve())
