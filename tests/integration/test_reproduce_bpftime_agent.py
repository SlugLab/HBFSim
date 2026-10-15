#!/usr/bin/env python3
"""Run real prepare/manifest code on a tiny Git fixture, with no build or network.

The fixture pin replaces ec26 only inside this imported test module. Real public
profile applicability is checked separately using pinned source byte exports.
"""
import argparse
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile


def git(root, *args):
    return subprocess.run(['git', '-c', 'core.autocrlf=false', '-C', str(root), *args],
                          check=True, capture_output=True, text=True).stdout.strip()


def refused(function, fragment):
    try:
        function()
    except RuntimeError as error:
        assert fragment in str(error), str(error)
    else:
        raise AssertionError('expected refusal: ' + fragment)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--scratch-parent', type=Path)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[2]
    spec = importlib.util.spec_from_file_location('reproduction_fixture', root / 'scripts/reproduce_bpftime_agent.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    with tempfile.TemporaryDirectory(prefix='hbfsim-source-preparation-', dir=args.scratch_parent) as directory:
        scratch = Path(directory)
        if args.scratch_parent:
            assert scratch.resolve().is_relative_to(args.scratch_parent.resolve())
        pin = scratch / 'pin'
        pin.mkdir()
        git(pin, 'init', '-q')
        (pin / 'CMakeLists.txt').write_text('project(fixture)\n')
        (pin / 'input.cpp').write_text('int value = 1;\n')
        git(pin, 'add', '.')
        git(pin, '-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid',
            'commit', '-qm', 'fixture pin')
        module.COMMIT = git(pin, 'rev-parse', 'HEAD')
        # This fixture has no nested submodules. No other production behavior is stubbed.
        patch = scratch / 'base.patch'
        patch.write_text('--- a/input.cpp\n+++ b/input.cpp\n@@ -1 +1 @@\n-int value = 1;\n+int value = 2;\n')
        compatibility = scratch / 'compat.patch'
        compatibility.write_text('--- /dev/null\n+++ b/compat.hpp\n@@ -0,0 +1 @@\n+#pragma once\n')
        args = argparse.Namespace(local_pin=pin, source=scratch / 'prepared',
                                  frida_core=None, frida_gum=None, allow_frida_download=True)
        receipt = module.prepare(args, patch, compatibility)
        source = args.source.resolve()
        check = lambda: module.verify_prepared_source(source, patch, compatibility)
        assert receipt['patch_order'] == ['base.patch', 'compat.patch']
        assert (source / 'input.cpp').read_text() == 'int value = 2;\n'
        assert (source / 'compat.hpp').read_text() == '#pragma once\n'
        assert check()['inputs'] == 3
        path = source.parent / (source.name + '.prepare.json')
        good = path.read_bytes()
        tracked = source / 'input.cpp'
        original = tracked.read_bytes()
        tracked.write_bytes(original + b'// drift\n')
        refused(check, 'source input drift')
        tracked.write_bytes(original)
        added = source / 'compat.hpp'
        original_added = added.read_bytes()
        added.write_bytes(original_added + b'// drift\n')
        refused(check, 'source input drift')
        added.write_bytes(original_added)
        stale = json.loads(good)
        stale['compatibility_patch_sha256'] = '0' * 64
        path.write_text(json.dumps(stale))
        refused(check, 'old or mismatched source profile')
        stale['schema'] = 'hbfsim.bpftime_source_prepare.v1'
        path.write_text(json.dumps(stale))
        old = path.read_bytes()
        build_args = argparse.Namespace(source=source, build=scratch / 'build', receipts=scratch / 'receipts')
        refused(lambda: module.build(build_args, scratch / 'never-run-lock.py', patch, compatibility),
                'prepare a new source directory')
        assert path.read_bytes() == old and not build_args.receipts.exists()
        path.write_bytes(good)
        assert check()['inputs'] == 3
        refused(lambda: module.prepare(args, patch, compatibility), 'destination already exists')
        print('prepare/order/input-drift/old-profile/refusal-before-build: PASS; no compiler/network/GPU')


if __name__ == '__main__':
    main()
