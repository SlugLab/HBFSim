#!/usr/bin/env python3
"""One-shot native/staged HBFSim supported-weight reproduction.

The owner supplies an existing reservation. This program never creates one or
starts a queue. `preflight` and `verify` do not launch CUDA work.
"""
import argparse
import hashlib
import json
import os
import re
import signal
import subprocess
import sys
import time
from pathlib import Path


REQUIRED = ('python', 'runner', 'model_config', 'profile', 'torch_overlay',
            'vllm_overlay', 'torch_cuda', 'vllm_C', 'vllm_moe_C', 'nvcc',
            'tuned_config', 'loader_env', 'binder', 'model_identity', 'expect_layer0',
            'expect_all98')
STAGED = ('wrapper', 'bundle_manifest', 'agent', 'compiler', 'agent_build_receipt',
          'gate', 'core', 'pass_plugin',
          'probe', 'daemon', 'extension', 'binding_manifest', 'pass_manifest',
          'stage_receipt')
HEX = re.compile(r'^[0-9a-f]{64}$')


def check(condition, message):
    if not condition:
        raise ValueError(message)


def digest(path):
    h = hashlib.sha256()
    with path.open('rb') as f:
        for block in iter(lambda: f.read(1 << 20), b''):
            h.update(block)
    return h.hexdigest()


def load(path):
    return json.loads(path.read_text(encoding='utf-8'))


def save_new(path, value):
    with path.open('x', encoding='utf-8') as f:
        json.dump(value, f, indent=2, sort_keys=True)
        f.write('\n')


def resolved(root, name):
    p = Path(name)
    # Keep the lexical overlay/toolkit view. resolve() follows symlinks and can
    # silently switch PYTHONPATH or CUDA_HOME to an installed, different tree.
    return Path(os.path.abspath(p if p.is_absolute() else root / p))


def file_entry(root, row, role):
    check(isinstance(row, dict) and isinstance(row.get('path'), str), f'{role}: path missing')
    want = row.get('sha256')
    check(isinstance(want, str) and HEX.fullmatch(want), f'{role}: explicit SHA256 required')
    path = resolved(root, row['path'])
    check(path.is_file(), f'{role}: absent {path}')
    got = digest(path)
    check(got == want, f'{role}: SHA256 mismatch {got} != {want}')
    return path


def _manifest_file_rows(data):
    files = data.get('files', {})
    check(isinstance(files, dict), 'bundle files must be an object')
    return {key: row for key, row in files.items()
            if isinstance(row, dict) and 'path' in row and 'sha256' in row}


def verify_config(config_path, root, staged=True):
    cfg = load(config_path)
    check(cfg.get('schema') == 'hbfsim.supported_weight_run.v1', 'config schema')
    artifacts = cfg.get('artifacts', {})
    for key in REQUIRED + (STAGED if staged else ()):
        check(key in artifacts, f'missing artifact {key}')
    files = {key: file_entry(root, row, key) for key, row in artifacts.items()}
    model_identity = load(files['model_identity'])
    check(model_identity.get('schema') == 'hbfsim.model_file_identity.v1', 'model identity schema')
    identity_rows = model_identity.get('files', [])
    check(identity_rows and len({r['name'] for r in identity_rows}) == len(identity_rows),
          'model identity list absent or duplicated')
    for row in identity_rows:
        name = row['name']
        check(Path(name).name == name, 'model identity path must be a basename')
        path = files['model_config'].parent / name
        check(path.is_file() and path.stat().st_size == row['bytes']
              and digest(path) == row['sha256'], f'model file identity mismatch: {name}')
    scopes = cfg.get('scopes', {})
    for scope in ('layer0', 'all98'):
        row = scopes.get(scope, {})
        check(isinstance(row.get('count'), int) and row['count'] > 0, f'{scope}: count')
        check(isinstance(row.get('bytes'), int) and row['bytes'] > 0, f'{scope}: bytes')
        pats = row.get('include_patterns', [])
        check(pats and all(isinstance(p, str) for p in pats), f'{scope}: patterns')
        for p in pats:
            re.compile(p)
        exp = load(files['expect_' + scope])
        selected = exp['selected']
        check(len(selected) == row['count'], f'{scope}: expectation count')
        check(sum(x['storage_bytes'] for x in selected) == row['bytes'], f'{scope}: expectation bytes')
        check(len({tuple(x['aliases']) for x in selected}) == len(selected), f'{scope}: duplicate aliases')
    universe = load(files['expect_all98'])['selected']
    for scope in ('layer0', 'all98'):
        wanted = {tuple(x['aliases']) for x in load(files['expect_' + scope])['selected']}
        patterns = [re.compile(p) for p in scopes[scope]['include_patterns']]
        selected_by_regex = {tuple(x['aliases']) for x in universe
                             if any(p.search(alias) for alias in x['aliases'] for p in patterns)}
        check(selected_by_regex == wanted, f'{scope}: regex does not select exact expected storages')
    if staged:
        bundle = load(files['bundle_manifest'])
        for key, row in _manifest_file_rows(bundle).items():
            file_entry(root, row, 'bundle/' + key)
        for role, member in (('agent', 'runtime/agent/libbpftime-agent.so'),
                             ('gate', 'libhbfsim_launch_gate.so'),
                             ('core', 'libhbfsim.so.0.1.0'),
                             ('pass_plugin', 'libptxpass_hbf.so'),
                             ('probe', 'vllm_fused_moe_probe.bpf.o'),
                             ('daemon', 'hbfsimd')):
            check(bundle['files'][member]['sha256'] == digest(files[role]),
                  f'configured {role} differs from bundle manifest')
        check(bundle['compiler']['sha256'] == digest(files['compiler']),
              'configured compiler differs from bundle manifest')
        agent_receipt = load(files['agent_build_receipt'])
        if isinstance(agent_receipt.get('agent'), dict):
            check(agent_receipt['agent']['sha256'] == digest(files['agent']),
                  'agent does not match source-build receipt')
        if isinstance(agent_receipt.get('compiler'), dict):
            check(agent_receipt['compiler']['sha256'] == digest(files['compiler']),
                  'compiler does not match source-build receipt')
        stage_dir = resolved(root, cfg['stage_dir'])
        stage_files = sorted(stage_dir.glob('*.ptx'))
        check(stage_files, 'no staged PTX')
        stage_hashes = cfg.get('stage_sha256', {})
        check(set(stage_hashes) == {p.name for p in stage_files}, 'stage file set differs from manifest')
        for p in stage_files:
            check(re.fullmatch(r'[0-9a-f]{64}\.ptx', p.name) is not None, f'bad stage filename {p.name}')
            check(digest(p) == stage_hashes[p.name], f'stage SHA mismatch {p.name}')
        pass_rows = [json.loads(line) for line in files['pass_manifest'].read_text().splitlines() if line.strip()]
        check(pass_rows, 'empty pass manifest')
        for row in pass_rows:
            check(row.get('instrumented') is True and row.get('unsupported_instructions') == 0
                  and not row.get('unsupported_opcodes') and not row.get('unsupported_parameters'),
                  'pass row not fully transformed')
        check(len(pass_rows) >= int(cfg.get('minimum_pass_rows', 1)), 'too few pass rows')
        bindings = load(files['binding_manifest']).get('bindings', [])
        check(len(bindings) >= int(cfg.get('minimum_bindings', 1)), 'too few native bindings')
        known = {(r['module_id'], r['kernel']) for r in pass_rows}
        binding_keys = set()
        for binding in bindings:
            key = (binding['module_id'], binding['kernel'])
            check(key not in binding_keys, f'duplicate native binding {key}')
            binding_keys.add(key)
            check(key in known, f'native binding absent from pass manifest: {key}')
            if 'container_path' in binding:
                file_entry(root, {'path': binding['container_path'],
                                  'sha256': binding['container_sha256']}, 'binding/container')
            else:
                check(binding['container_sha256'] == digest(files['vllm_C']),
                      'binding container SHA does not match configured vLLM DSO')
            for path_key, sha_key in (('provenance_sidecar', 'provenance_sha256'),
                                      ('staged_path', 'staged_sha256'),
                                      ('raw_ptx_path', 'member_sha256')):
                file_entry(root, {'path': binding[path_key], 'sha256': binding[sha_key]},
                           f'binding/{path_key}')
        policy = cfg.get('norm_opt_by_raw', {})
        check(isinstance(policy, dict) and policy, 'explicit per-raw compiler policy required')
        for raw, level in policy.items():
            check(HEX.fullmatch(raw) and level in (0, 1, 2, 3), 'invalid per-raw optimization')
    return cfg, files


def process_ticks(pid):
    # /proc stat comm may contain spaces; fields after the final ')' begin at state.
    tail = Path(f'/proc/{pid}/stat').read_text().rsplit(')', 1)[1].split()
    return int(tail[19])


def resource_check(cfg, overall):
    req = cfg['resource']
    host = subprocess.run(['hostname'], capture_output=True, text=True, check=True).stdout.strip()
    check(host == req['hostname'], f'host mismatch: {host}')
    gpu = subprocess.run(['nvidia-smi', '--query-gpu=uuid,memory.total,memory.free,utilization.gpu',
                          '--format=csv,noheader,nounits'], capture_output=True, text=True,
                         check=True).stdout.strip().splitlines()
    check(len(gpu) == 1, 'expected one selected GPU')
    uuid, total, free, usage = (x.strip() for x in gpu[0].split(','))
    check(uuid == req['gpu_uuid'], 'GPU UUID mismatch')
    check(int(free) >= int(total) * .05, 'GPU free memory below 5% target')
    mem = {}
    for line in Path('/proc/meminfo').read_text().splitlines():
        parts = line.split()
        mem[parts[0].rstrip(':')] = int(parts[1])
    check(mem['MemAvailable'] >= mem['MemTotal'] * .05, 'RAM available below 5% target')
    lease = req['reservation']
    check(process_ticks(int(lease['pid'])) == int(lease['start_ticks']), 'reservation identity changed')
    watchdog = req['watchdog']
    check(process_ticks(int(watchdog['pid'])) == int(watchdog['start_ticks']), 'watchdog identity changed')
    check(time.time() + overall + int(req.get('cleanup_margin_seconds', 600)) <
          int(watchdog['deadline_unix']), 'watchdog deadline too short')
    return {'host': host, 'gpu_uuid': uuid, 'gpu_total_mib': int(total),
            'gpu_free_mib': int(free), 'gpu_utilization': int(usage),
            'mem_available_kib': mem['MemAvailable'], 'mem_total_kib': mem['MemTotal']}


def environment(cfg, files, root, out, staged):
    env = os.environ.copy()
    for key in list(env):
        if key.startswith(('HBFSIM_', 'BPFTIME_', 'NORM_', 'MOE_')) or key == 'LD_PRELOAD':
            env.pop(key, None)
    runtime = cfg['runtime']
    python = files['python']
    cuda = files['nvcc'].parent.parent
    site = resolved(root, runtime['site_packages'])
    vllm = files['vllm_overlay'].parents[1]
    torch = files['torch_overlay'].parents[1]
    loader_path = load(files['loader_env'])['ld_library_path']
    cache = out / 'cache'
    env.update({
        'PYTHONPATH': ':'.join(map(str, (vllm, torch, site))),
        'LD_LIBRARY_PATH': (str(files['gate'].parent) + ':' if staged else '') + loader_path,
        'PATH': ':'.join((str(python.parent), str(cuda / 'bin'), os.environ.get('PATH', ''))),
        'CUDA_HOME': str(cuda), 'CUDA_PATH': str(cuda), 'CUDACXX': str(files['nvcc']),
        'FLASHINFER_NVCC': str(files['nvcc']), 'HBFSIM_VLLM_CACHE': str(cache / 'vllm'),
        'TRITON_CACHE_DIR': str(cache / 'triton'),
        'VLLM_TUNED_CONFIG_FOLDER': str(files['tuned_config'].parent),
        'CUDA_VISIBLE_DEVICES': str(runtime['cuda_visible_devices']),
        'CUDA_LAUNCH_BLOCKING': '1', 'PYTHONUNBUFFERED': '1',
        'CC': str(resolved(root, runtime['cc'])),
        'CXX': str(resolved(root, runtime['cxx'])),
        'CUDAHOSTCXX': str(resolved(root, runtime['cxx'])),
    })
    if staged:
        env.update({
            'HBFSIM_BUILD_DIR': str(files['gate'].parent),
            'HBFSIM_BPFTIME_BUILD_DIR': str(files['gate'].parent),
            'HBFSIM_BPFTIME_PROBE': str(files['probe']),
            'HBFSIM_DAEMON_PATH': str(files['daemon']),
            'HBFSIM_VLLM_EXTENSION': str(files['extension']),
            'HBFSIM_CUDA_ROOT': str(cuda),
            'HBFSIM_INSTRUMENTATION_POLICY': 'partial',
            'BPFTIME_CUDA_LATE_PTX_DIR': str(resolved(root, cfg['stage_dir'])),
            'BPFTIME_CUDA_LATE_PTX_PREPATCHED': '1',
            'HBFSIM_PRESTAGED_PASS_MANIFEST_PATH': str(files['pass_manifest']),
            'HBFSIM_NATIVE_BINDING_MANIFEST_PATH': str(files['binding_manifest']),
            'HBFSIM_PASS_MANIFEST_PATH': str(out / 'pass-manifests.jsonl'),
            'HBFSIM_COVERAGE_PATH': str(out / 'coverage.jsonl'),
            'HBFSIM_NATIVE_REGISTRATION_LOG_PATH': str(out / 'native-registration.jsonl'),
            'HBFSIM_STRICT_BRIDGE_LOG_PATH': str(out / 'strict-bridge.jsonl'),
            'HBFSIM_STRICT_RUNTIME_LOG_PATH': str(out / 'strict-runtime.jsonl'),
            'HBFSIM_NVPTX_MAX_REGISTERS': str(cfg['runtime']['max_registers']),
            'HBFSIM_NVPTX_OPT_LEVEL_BY_RAW_SHA256': ','.join(
                f'{k}={v}' for k, v in sorted(cfg['norm_opt_by_raw'].items())),
        })
        env.pop('HBFSIM_NVPTX_OPT_LEVEL', None)
    return env


def command(cfg, files, root, out, scope, staged, epoch):
    w = cfg['workload']
    cmd = ([str(files['wrapper']), '--'] if staged else []) + [
        str(files['python']), str(files['runner']), '--mode', 'timing' if staged else 'baseline',
        '--model', str(files['model_config'].parent), '--profile', str(files['profile']),
        '--report-dir', str(out / 'cell'), '--num-prompts', str(w['num_prompts']),
        '--input-len', str(w['input_len']), '--output-len', str(w['output_len']),
        '--max-model-len', str(w['max_model_len']), '--max-num-batched-tokens',
        str(w['max_num_batched_tokens']), '--gpu-memory-utilization', str(w['gpu_memory_utilization']),
        '--seed', str(w['seed']), '--warmup-requests', str(w['warmup_requests']),
        '--request-timeout-ns', str(w['request_timeout_ns'])]
    if staged:
        check(isinstance(epoch, int) and epoch > 0, 'fresh positive epoch required')
        cmd += ['--request-accounting', '--accounting-epoch', str(epoch),
                '--eval-delay-ns', '-1', '--hbf-weight-selection', 'include']
        for pattern in cfg['scopes'][scope]['include_patterns']:
            cmd += ['--hbf-include-pattern', pattern]
        cmd += ['--hbf-instrumentation-policy', 'partial', '--hbf-timing-model', 'hybrid']
    return cmd


def target_maps(pid, seen):
    paths = {line.split()[-1] for line in Path(f'/proc/{pid}/maps').read_text().splitlines()
             if ('libtorch_cuda.so' in line or '_C.abi3.so' in line or '_moe_C.abi3.so' in line)}
    for name in paths:
        if name not in seen and Path(name).is_file():
            seen[name] = digest(Path(name))


def validate_native_baseline(cfg, files, baseline):
    check(baseline and baseline.is_file(), 'native baseline result required')
    check(load(baseline)['request_terminal_status'] == 'success', 'baseline not successful')
    native_start = baseline.parent.parent / 'start.json'
    check(native_start.is_file(), 'native start receipt required to bind workload')
    start = load(native_start)
    native_cmd = start.get('command_argv', start.get('command', []))
    check(isinstance(native_cmd, list), 'native command must be an argv list')
    workload = cfg['workload']
    for option, key in (('--num-prompts', 'num_prompts'), ('--input-len', 'input_len'),
                        ('--output-len', 'output_len'), ('--seed', 'seed'),
                        ('--warmup-requests', 'warmup_requests'),
                        ('--request-timeout-ns', 'request_timeout_ns')):
        check(option in native_cmd and native_cmd[native_cmd.index(option) + 1] == str(workload[key]),
              f'native baseline workload mismatch: {option}')
    check('--model' in native_cmd and native_cmd[native_cmd.index('--model') + 1] ==
          str(files['model_config'].parent), 'native baseline model mismatch')
    check('--profile' in native_cmd and native_cmd[native_cmd.index('--profile') + 1] ==
          str(files['profile']), 'native baseline profile mismatch')


def run_once(cfg, files, root, config_path, scope, staged, out, epoch, overall, baseline):
    check(sys.platform.startswith('linux'), 'execution requires Linux /proc')
    check(not out.exists(), f'result directory exists: {out}')
    resource = resource_check(cfg, overall)
    if staged:
        validate_native_baseline(cfg, files, baseline)
    out.mkdir(parents=True, exist_ok=False)
    (out / 'cache' / 'triton').mkdir(parents=True)
    env = environment(cfg, files, root, out, staged)
    cmd = command(cfg, files, root, out, scope, staged, epoch)
    save_new(out / 'start.json', {
        'status': 'STARTED', 'start_unix_ns': time.time_ns(), 'owner_pid': os.getpid(),
        'mode': 'staged' if staged else 'native', 'scope': scope, 'command_argv': cmd,
        'resource': resource, 'config_sha256': digest(config_path),
        'artifacts': {k: {'path': str(p), 'sha256': digest(p)} for k, p in files.items()},
        'environment': {k: env[k] for k in sorted(env) if k.startswith(('HBFSIM_', 'BPFTIME_'))
                        or k in ('PYTHONPATH', 'LD_LIBRARY_PATH', 'PATH', 'CUDA_HOME', 'CUDA_PATH',
                                 'CUDACXX', 'FLASHINFER_NVCC', 'CC', 'CXX', 'CUDAHOSTCXX',
                                 'TRITON_CACHE_DIR', 'VLLM_TUNED_CONFIG_FOLDER', 'CUDA_VISIBLE_DEVICES')},
        'baseline_result': str(baseline) if baseline else None,
        'epoch': epoch, 'overall_seconds': overall})
    seen, targets = {}, {}
    with (out / 'worker.stdout').open('xb') as stdout, (out / 'worker.stderr').open('xb') as stderr:
        worker = subprocess.Popen(cmd, env=env, stdout=stdout, stderr=stderr,
                                  start_new_session=True)
        save_new(out / 'worker-start.json', {'pid': worker.pid, 'pgid': os.getpgid(worker.pid),
                                            'start_ticks': process_ticks(worker.pid),
                                            'start_unix_ns': time.time_ns()})
        deadline = time.monotonic() + overall
        while worker.poll() is None and time.monotonic() < deadline:
            try:
                for path in Path('/proc').iterdir():
                    if not path.name.isdigit():
                        continue
                    pid = int(path.name)
                    try:
                        if os.getpgid(pid) != worker.pid:
                            continue
                        cmdline = (path / 'cmdline').read_bytes().replace(b'\0', b' ').decode(errors='replace')
                        if str(files['runner']) not in cmdline:
                            continue
                        targets[pid] = {'pid': pid, 'start_ticks': process_ticks(pid), 'cmdline': cmdline}
                        target_maps(pid, seen)
                    except (FileNotFoundError, ProcessLookupError, PermissionError):
                        continue
                (out / 'target-processes.json').write_text(json.dumps({'targets': list(targets.values())}, indent=2) + '\n')
                (out / 'mapped-libraries.json').write_text(json.dumps({'paths': [
                    {'path': p, 'sha256': seen[p]} for p in sorted(seen)]}, indent=2) + '\n')
            except (FileNotFoundError, ProcessLookupError):
                pass
            time.sleep(2)
        if worker.poll() is None:
            os.killpg(worker.pid, signal.SIGTERM)
            try:
                worker.wait(timeout=10)
            except subprocess.TimeoutExpired:
                os.killpg(worker.pid, signal.SIGKILL)
                worker.wait(timeout=10)
            rc = 124
        else:
            rc = worker.returncode
    remaining = subprocess.run(['pgrep', '-a', '-g', str(worker.pid)], capture_output=True,
                               text=True).stdout.splitlines()
    save_new(out / 'worker-finish.json', {'returncode': rc, 'remaining': remaining,
                                          'finish_unix_ns': time.time_ns()})
    try:
        report = verify_result(out, scope, cfg, files, baseline, staged, write_report=True)
        status = report['status']
    except Exception as exc:
        status = 'FAILED_VALIDATION'
        save_new(out / 'validation-error.json', {'error': repr(exc)})
    save_new(out / 'finish.json', {'status': status, 'returncode': 0 if status.startswith('PASS_') else 2,
                                   'finish_unix_ns': time.time_ns()})
    check(rc == 0 and not remaining and status.startswith('PASS_'), 'run did not pass; raw files retained')


def verify_result(out, scope, cfg, files, baseline, staged, write_report=False):
    worker = load(out / 'worker-finish.json')
    check(worker['returncode'] == 0 and not worker['remaining'], 'worker failed or left processes')
    result = load(out / 'cell/result.json')
    check(result['request_terminal_status'] == 'success', 'request did not succeed')
    check(result.get('scientific_status') == 'COMPLETE', 'scientific status incomplete')
    maps = load(out / 'mapped-libraries.json')['paths']
    for key, name in (('torch_cuda', '/libtorch_cuda.so'), ('vllm_C', '/_C.abi3.so'),
                      ('vllm_moe_C', '/_moe_C.abi3.so')):
        found = [r for r in maps if r['path'].endswith(name)]
        check(len(found) == 1 and found[0]['sha256'] == digest(files[key]),
              f'loaded {key} exact singleton SHA mismatch')
    if not staged:
        return {'status': 'PASS_NATIVE', 'output_sha256': result['output_token_ids_sha256']}
    native = load(baseline)
    check(result['output_token_ids_sha256'] == native['output_token_ids_sha256']
          and result['output_token_ids'] == native['output_token_ids'],
          'native output mismatch')
    registration = load(out / 'cell/registration.json')
    selected = load(files['expect_' + scope])['selected']
    expected = {tuple(row['aliases']): row['storage_bytes'] for row in selected}
    actual = {tuple(row['aliases']): row['storage_bytes'] for row in registration['storages']}
    count, total = cfg['scopes'][scope]['count'], cfg['scopes'][scope]['bytes']
    check(registration['unique_storage_count'] == count and registration['registered_bytes'] == total,
          'registered storage count/bytes mismatch')
    check(actual == expected and all(r['bytes'] == r['storage_bytes'] for r in registration['storages']),
          'registered full storage aliases/bytes mismatch')
    check(registration['selection']['instrumentation_policy'] == 'partial', 'policy mismatch')
    coverage = [json.loads(s) for s in (out / 'coverage.jsonl').read_text().splitlines() if s.strip()]
    access = result['access_accounting']['access']
    a = access['aggregate']
    names = ('in_range_accesses', 'modeled_admitted_accesses', 'service_completed_accesses')
    byte_names = ('in_range_intersection_bytes', 'modeled_admitted_bytes', 'service_completed_bytes')
    errors = ('counter_overflow', 'failed_preissue_accesses', 'failed_after_issue_accesses',
              'translation_failed_accesses', 'unsupported_preissue_accesses', 'unclassified_accesses')
    complete = (access['status'] == 'COMPLETE' and access['disable_complete']
                and a[names[0]] > 0 and a[byte_names[0]] > 0
                and len({a[k] for k in names}) == len({a[k] for k in byte_names}) == 1
                and all(a[k] == 0 for k in errors))
    modules = {row['identity']: row for row in access['per_module']}
    by_storage = []
    for storage in registration['storages']:
        base, length = storage['address'], storage['bytes']
        matches = [r for r in coverage if r.get('modeled') is True and r.get('module_id')
                   and isinstance(r.get('address'), int) and base <= r['address'] < base + length]
        active = sorted({r['module_id'] for r in matches if r['module_id'] in modules
                         and modules[r['module_id']]['status'] == 'COMPLETE'
                         and modules[r['module_id']]['counters']['modeled_admitted_accesses'] > 0
                         and len({modules[r['module_id']]['counters'][k] for k in names}) == 1
                         and len({modules[r['module_id']]['counters'][k] for k in byte_names}) == 1})
        by_storage.append({'aliases': storage['aliases'], 'base': base, 'bytes': length,
                           'modeled_coverage_rows': len(matches), 'active_complete_modules': active})
    status = (f'PASS_{count}_OF_{count}_ADDRESSED_ACTIVE' if complete and
              len(by_storage) == count and all(r['active_complete_modules'] for r in by_storage)
              else 'PARTIAL_COVERAGE')
    report = {'status': status, 'accounting_complete': complete, 'by_storage': by_storage,
              'coverage_sha256': digest(out / 'coverage.jsonl'),
              'result_sha256': digest(out / 'cell/result.json'),
              'registration_sha256': digest(out / 'cell/registration.json'),
              'note': 'Addressed coverage plus measured per-module closure; shared modules do not give separate per-storage latency.'}
    # Archive verification is read-only; a fresh run writes this once.
    if write_report and not (out / 'dynamic-coverage-validation.json').exists():
        save_new(out / 'dynamic-coverage-validation.json', report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('preflight', 'verify', 'native', 'staged'))
    parser.add_argument('--config', required=True, type=Path)
    parser.add_argument('--workspace-root', required=True, type=Path)
    parser.add_argument('--scope', choices=('layer0', 'all98'), default='layer0')
    parser.add_argument('--result-dir', type=Path)
    parser.add_argument('--baseline-result', type=Path)
    parser.add_argument('--epoch', type=int)
    parser.add_argument('--overall-seconds', type=int, default=10800)
    parser.add_argument('--check-resources', action='store_true', help='also query GPU/reservation in preflight')
    args = parser.parse_args()
    config_path = args.config.resolve()
    root = args.workspace_root.absolute()
    staged = args.action != 'native'
    cfg, files = verify_config(config_path, root, staged)
    check(0 < args.overall_seconds <= 43200, 'finite overall limit out of range')
    if args.action == 'preflight':
        resource = resource_check(cfg, args.overall_seconds) if args.check_resources else None
        print(json.dumps({'status': 'CPU_PREFLIGHT_PASS', 'scope': args.scope,
                          'artifact_count': len(files), 'resource': resource}, sort_keys=True))
        return
    check(args.result_dir is not None, '--result-dir required')
    out = args.result_dir.resolve()
    baseline = args.baseline_result.resolve() if args.baseline_result else None
    if args.action == 'verify':
        check(baseline is not None, '--baseline-result required for archive verification')
        report = verify_result(out, args.scope, cfg, files, baseline, True)
        print(json.dumps({'status': report['status'], 'storage_count': len(report['by_storage'])}, sort_keys=True))
        check(report['status'].startswith('PASS_'), 'archived run failed coverage validation')
        return
    run_once(cfg, files, root, config_path, args.scope, args.action == 'staged', out, args.epoch,
             args.overall_seconds, baseline)


if __name__ == '__main__':
    main()
