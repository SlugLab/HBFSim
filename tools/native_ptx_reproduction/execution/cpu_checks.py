#!/usr/bin/env python3
"""Read-only archive replay plus isolated negative fixtures; never launches CUDA."""
import argparse
import copy
import json
import tempfile
from pathlib import Path

import reproduce as r


def failed(call):
    try:
        call()
    except (ValueError, KeyError, FileNotFoundError):
        return True
    return False


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--config', type=Path, required=True)
    p.add_argument('--workspace-root', type=Path, required=True)
    p.add_argument('--baseline-result', type=Path, required=True)
    p.add_argument('--layer0-result', type=Path, required=True)
    p.add_argument('--all98-result', type=Path, required=True)
    p.add_argument('--receipt', type=Path, required=True)
    a = p.parse_args()
    cfg, files = r.verify_config(a.config, a.workspace_root, True)
    passed = {}
    for scope, out in (('layer0', a.layer0_result), ('all98', a.all98_result)):
        value = r.verify_result(out, scope, cfg, files, a.baseline_result, True)
        passed[scope] = value['status']
    r.validate_native_baseline(cfg, files, a.baseline_result)
    passed['legacy_native_start_accepted'] = True
    with tempfile.TemporaryDirectory(prefix='hbfsim-repro-new-native-') as dirname:
        native_dir = Path(dirname)
        (native_dir / 'cell').mkdir()
        result = native_dir / 'cell/result.json'
        result.symlink_to(a.baseline_result)
        cmd = r.command(cfg, files, a.workspace_root, native_dir, 'layer0', False, None)
        (native_dir / 'start.json').write_text(json.dumps({'command_argv': cmd}))
        r.validate_native_baseline(cfg, files, result)
        passed['new_command_argv_baseline_accepted'] = True
        env = r.environment(cfg, files, a.workspace_root, native_dir, True)
        passed['lexical_overlay_and_cuda_view'] = (
            env['PYTHONPATH'].split(':')[:2] == [str(files['vllm_overlay'].parents[1]),
                                                  str(files['torch_overlay'].parents[1])]
            and env['CUDA_HOME'] == str(files['nvcc'].parent.parent)
            and env['HBFSIM_VLLM_EXTENSION'] == str(files['extension'])
            and bool(env['HBFSIM_NVPTX_OPT_LEVEL_BY_RAW_SHA256']))
    with tempfile.TemporaryDirectory(prefix='hbfsim-repro-cpu-') as dirname:
        tmp = Path(dirname)
        source = a.all98_result
        (tmp / 'cell').mkdir()
        for name in ('worker-finish.json', 'mapped-libraries.json', 'coverage.jsonl'):
            (tmp / name).symlink_to(source / name)
        (tmp / 'cell/result.json').symlink_to(source / 'cell/result.json')
        reg = json.loads((source / 'cell/registration.json').read_text())
        reg['storages'] = reg['storages'][:-1]
        (tmp / 'cell/registration.json').write_text(json.dumps(reg))
        passed['missing_storage_rejected'] = failed(
            lambda: r.verify_result(tmp, 'all98', cfg, files, a.baseline_result, True))
        (tmp / 'cell/registration.json').write_text((source / 'cell/registration.json').read_text())
        (tmp / 'coverage.jsonl').unlink()
        (tmp / 'coverage.jsonl').write_text('{}\n')
        passed['missing_addressed_coverage_rejected'] = (
            r.verify_result(tmp, 'all98', cfg, files, a.baseline_result, True)['status']
            == 'PARTIAL_COVERAGE')
        (tmp / 'coverage.jsonl').unlink()
        (tmp / 'coverage.jsonl').symlink_to(source / 'coverage.jsonl')
        (tmp / 'cell/result.json').unlink()
        wrong = json.loads((source / 'cell/result.json').read_text())
        wrong['output_token_ids'] = [[-1]]
        (tmp / 'cell/result.json').write_text(json.dumps(wrong))
        passed['output_mismatch_rejected'] = failed(
            lambda: r.verify_result(tmp, 'all98', cfg, files, a.baseline_result, True))
    bad = copy.deepcopy(cfg)
    key = next(iter(bad['stage_sha256']))
    bad['stage_sha256'][key] = '0' * 64
    with tempfile.TemporaryDirectory(prefix='hbfsim-repro-config-') as dirname:
        config = Path(dirname) / 'bad.json'
        config.write_text(json.dumps(bad))
        passed['wrong_stage_sha_rejected'] = failed(
            lambda: r.verify_config(config, a.workspace_root, True))
    receipt = {'schema': 'hbfsim.reproduction_cpu_check.v1', 'status': 'PASS' if all(
        x is True or (isinstance(x, str) and x.startswith('PASS_')) for x in passed.values()) else 'FAIL',
        'checks': passed, 'note': 'No GPU launch, agent attach, daemon or reservation mutation.'}
    r.save_new(a.receipt, receipt)
    print(json.dumps(receipt, sort_keys=True))
    if receipt['status'] != 'PASS':
        raise SystemExit(1)


if __name__ == '__main__':
    main()
