#!/usr/bin/env python3
"""Bounded read-only evidence audit plus existing TEST_ONLY CPU unit tests."""
import os
import sys
import json
import time
import signal
import resource
import hashlib
import tempfile
import unittest
from pathlib import Path
from contextlib import redirect_stdout, redirect_stderr

OUT = Path(__file__).resolve().parent
REPO = Path('/root/hbfsim-exp/eval-base-integration')
sys.dont_write_bytecode = True
os.environ.update(CUDA_VISIBLE_DEVICES='', OMP_NUM_THREADS='1',
                  OPENBLAS_NUM_THREADS='1', MKL_NUM_THREADS='1',
                  NUMEXPR_NUM_THREADS='1', PYTHONDONTWRITEBYTECODE='1')
resource.setrlimit(resource.RLIMIT_AS, (1024**3, 1024**3))
resource.setrlimit(resource.RLIMIT_CPU, (120, 120))
resource.setrlimit(resource.RLIMIT_FSIZE, (1024**2, 1024**2))
signal.alarm(120)
cpu = min(os.sched_getaffinity(0))
os.sched_setaffinity(0, {cpu})
temporary = OUT / 'eq34-tmp'
temporary.mkdir(exist_ok=False)
tempfile.tempdir = str(temporary)
sys.path.insert(0, str(REPO / 'scripts/eval'))

def digest(path):
    h = hashlib.sha256()
    with path.open('rb') as f:
        for chunk in iter(lambda: f.read(1024**2), b''):
            h.update(chunk)
    return {'path': str(path), 'bytes': path.stat().st_size, 'sha256': h.hexdigest()}

def write(name, payload):
    (OUT / name).write_text(json.dumps(payload, indent=2, ensure_ascii=False, allow_nan=False) + '\n')

start = time.monotonic()
index_path = REPO / 'results/batches/20260907-routing-continuation/composition/B16-group0/members.json'
index = json.loads(index_path.read_bytes())
metrics_dir = index_path.parent / 'metrics'
metrics_manifest = json.loads((metrics_dir / 'manifest.json').read_bytes())
inventory_path = REPO / 'results/manifests/hf-qwen3-30b-a3b-evaluation-inventory-20260905.json'
legacy_path = REPO / 'results/manifests/qwen3-30b-a3b-inventory.json'
metadata_dir = REPO / 'results/manifests/hf-qwen3-30b-a3b-metadata-20260905'
capture_dir = REPO / 'results/gold/hf-routing-runner/real-sixteen-member-capture-attempt-001'
paths = [inventory_path, legacy_path, index_path, metrics_dir / 'manifest.json',
         capture_dir / 'triplet-status.json', metadata_dir / 'receipt.json',
         metadata_dir / 'COMPLETE.json', metadata_dir / 'tensors.json',
         metadata_dir / 'metadata/config.json']
paths += [Path(m['path']) for m in index['members']]
paths += [metrics_dir / metrics_manifest['outputs'][series]['path'] for series in ('real', 'shuffled', 'null')]
paths += [REPO / 'scripts/eval' / n for n in ('budget_fast_tier.py', 'prefetch_replay.py',
    'routing_metrics.py', 'run_prefetch.py', 'evaluation_inventory.py',
    'test_prefetch_replay.py', 'test_inventory_checkpoint.py', 'test_routing_metrics.py')]
before = {str(p): digest(p) for p in paths}
write('eq34-hashes-before.json', before)

summary = {'scope': 'TEST_ONLY_CPU_CONTRACT_VALIDATION_NOT_EXPERIMENT_RESULT',
           'gpu_used': False, 'new_capture': False, 'checkpoint_weight_payload_read': False,
           'limits': {'one_python_process': True, 'cpu_affinity': [cpu], 'wall_seconds': 120,
                      'address_space_bytes': 1024**3, 'cpu_seconds': 120,
                      'per_file_output_bytes': 1024**2, 'total_output_budget_bytes': 10*1024**2},
           'python': sys.executable, 'python_version': sys.version}
try:
    with (OUT / 'eq34-cpu-tests.log').open('w') as log, redirect_stdout(log), redirect_stderr(log):
        print('TEST_ONLY; existing tests; no measured GPU/HBF/serving performance evidence')
        print(json.dumps(summary, ensure_ascii=False))
        import test_prefetch_replay
        import test_routing_metrics
        import test_inventory_checkpoint
        from budget_fast_tier import budget_fast_tier
        from inventory_checkpoint import inventory_checkpoint
        suite = unittest.TestSuite()
        loader = unittest.TestLoader()
        suite.addTests(loader.loadTestsFromTestCase(test_prefetch_replay.PrefetchGold))
        for name in loader.getTestCaseNames(test_routing_metrics.RoutingMetricsTests):
            if name != 'test_cli_preserves_separate_series_and_rejects_trace_mutation':
                suite.addTest(test_routing_metrics.RoutingMetricsTests(name))
        suite.addTests(loader.loadTestsFromTestCase(test_inventory_checkpoint.InventoryTests))
        result = unittest.TextTestRunner(stream=log, verbosity=2).run(suite)
        summary.update(tests_run=result.testsRun, successful=result.wasSuccessful(),
                       failures=len(result.failures), errors=len(result.errors), skipped=result.skipped,
                       omitted_existing_test='routing CLI test creates a subprocess; omitted to keep one-process scope')
        fixture = test_inventory_checkpoint.InventoryTests()
        fixture.setUp()
        try:
            fixture.write()
            inv = inventory_checkpoint(fixture.path, page_bytes=16)
            budget = budget_fast_tier(inv, fast_bytes=1024, active_sequences=1,
                         context_tokens=1, kv_element_bytes=1, workspace_bytes=0, safety_bytes=0)
            assert budget['rho_requested'] > 1 and budget['rho_achieved'] == 1
            write('eq34-test-only-budget-oracle.json', dict(scope='TEST_ONLY_NOT_MODEL_EXPERIMENT', budget=budget))
            print('TEST_ONLY excess-capacity oracle:', json.dumps({k: budget[k] for k in
                  ('C_fast_effective', 'W_HBF_eligible', 'rho_requested', 'rho_achieved', 'cache_validation')}))
        finally:
            fixture.doCleanups()
        summary['test_only_extra_budget_oracle_passed'] = True
except BaseException as error:
    summary.update(successful=False, unexpected_error=repr(error))
finally:
    after = {str(p): digest(p) for p in paths}
    write('eq34-hashes-after.json', after)
    checks = []
    checks.append({'binding': 'composition inventory', 'matches': before[str(legacy_path)]['sha256'] == index['inventory_sha256']})
    for m in index['members']:
        checks.append({'binding': m['member_id'], 'matches': before[m['path']]['sha256'] == m['sha256']})
    for series in ('real', 'shuffled', 'null'):
        item = metrics_manifest['outputs'][series]
        checks.append({'binding': 'metrics ' + series, 'matches': before[str(metrics_dir / item['path'])]['sha256'] == item['sha256']})
    hf = json.loads(inventory_path.read_bytes())
    for name, field in [('receipt.json', 'receipt_sha256'), ('COMPLETE.json', 'complete_sha256'),
                        ('tensors.json', 'tensor_table_sha256'), ('metadata/config.json', 'config_artifact_sha256')]:
        checks.append({'binding': 'HF ' + field, 'matches': before[str(metadata_dir / name)]['sha256'] == hf['model_binding'][field]})
    summary.update(hash_file_count=len(paths), unchanged_before_after=before == after,
                   artifact_binding_checks=checks, artifact_bindings_match=all(x['matches'] for x in checks),
                   elapsed_wall_seconds=time.monotonic() - start,
                   peak_rss_bytes=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * 1024,
                   child_cpu_seconds=resource.getrusage(resource.RUSAGE_CHILDREN).ru_utime + resource.getrusage(resource.RUSAGE_CHILDREN).ru_stime,
                   inventory_accounting={k: hf[k] for k in ('layers', 'E', 'k', 'page_bytes',
                        'eligible_expert_bytes', 'resident_non_offloaded_bytes', 'tensor_bytes', 'weight_dtype',
                        'payload_identity_status', 'hardware_validated', 'scientific_validation_passed') if k in hf})
    write('eq34-cpu-summary.json', summary)
    temporary.rmdir()
    signal.alarm(0)
print(json.dumps(summary, ensure_ascii=False))
sys.exit(0 if summary.get('successful') and summary['unchanged_before_after'] and summary['artifact_bindings_match'] else 1)
