"""Audit each frozen causal/ECC point, preserving failed and unstarted points."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys

from freeze_extension import digest, save


def run(index_path, output):
    index = json.loads(index_path.read_text())
    output.mkdir(parents=True, exist_ok=False)
    results = []
    for row in index['points']:
        point = Path(row['output']); destination = output / row['point_id']
        entry = {'point_id': row['point_id']}
        if (point / 'DONE.json').is_file():
            if digest(point / 'config.json') != row['config_sha256']:
                entry.update(status='IDENTITY_FAILURE')
            else:
                with (output / (row['point_id'] + '.log')).open('x') as log:
                    result = subprocess.run([sys.executable, str(Path(__file__).with_name('analyze_extensions.py')),
                        '--point', str(point), '--output', str(destination)], stdout=log, stderr=subprocess.STDOUT)
                entry.update(status='AUDITED' if result.returncode == 0 else 'ANALYSIS_FAILED')
                if result.returncode == 0:
                    analysis = json.loads((destination / 'analysis.json').read_text())
                    entry['causal_tokens'] = analysis.get('causal_tokens')
                    entry['causal_consumers'] = analysis.get('causal_consumers')
                    config = json.loads((point / 'config.json').read_text())
                    summary = json.loads((point / 'DONE.json').read_text())['summary']
                    entry['comparison_arm'] = config.get('comparison_arm')
                    entry['topology'] = config['topology']
                    entry['p85'] = config.get('hbf_read_cost_proxy', {}).get('profile', {}).get('recoverable_read_probability_at_85c')
                    entry['metrics'] = {
                        'completed_causal_tokens': summary['completed_tokens'],
                        'delivered_service_bytes': sum(summary['delivered_useful_bytes_by_stack'].values()),
                        'pending_external_jobs': summary['pending_external_jobs'],
                        'uninstantiated_batches': summary['uninstantiated_batches'],
                        'energy_j': summary['energy_j'],
                        'peak_k': max(summary['peak_k_by_owner'].values())}
        elif (point / 'FAILED.json').exists():
            entry.update(status='RETAINED_FAILURE', failure=json.loads((point / 'FAILED.json').read_text()))
        else:
            entry.update(status='NOT_RUN')
        results.append(entry)
        save(output / 'STATUS.json', {'points_processed': len(results), 'points_expected': len(index['points'])})
    pairs = []
    for entry in results:
        if entry.get('comparison_arm') != 'feedback' or entry.get('p85') is None:
            continue
        baseline = next((r for r in results if r.get('comparison_arm') == 'guard'
                         and r.get('topology') == entry['topology'] and r.get('p85') == entry['p85']), None)
        if baseline:
            pairs.append({'left': baseline['point_id'], 'right': entry['point_id'],
                'delta_feedback_minus_guard': {k: v - baseline['metrics'][k] for k, v in entry['metrics'].items()},
                'interpretation': 'EXPLORATORY; no automatic model selection or hardware claim'})
    save(output / 'RESULT.json', {'points': results, 'ecc_pairs': pairs, 'scientific_pass': 'NOT_ASSESSED',
        'claim': 'CONDITIONAL_SIMULATED; requires paired interpretation; all failures retained'})
    return 0 if all(r['status'] == 'AUDITED' for r in results) else 1


if __name__ == '__main__':
    parser = argparse.ArgumentParser(); parser.add_argument('--index', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True); args = parser.parse_args()
    raise SystemExit(run(args.index, args.output))
