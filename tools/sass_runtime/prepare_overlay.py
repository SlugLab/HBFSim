#!/usr/bin/env python3
"""Create opt-in copies of the published recovered-kernel consumers.

Never modifies the evidence tree. Build the copied C++ files using the published
reproduction build recipe, and put the copied Python package on PYTHONPATH.
"""
import argparse
import hashlib
import json
from pathlib import Path
import shutil

TASK = Path('reproduction/sass-final147-20260926/evidence-tree/rebuttal_20260921/sass-lifter-20260924/task')
AGENT = TASK / 'li6-li7-combined-host-v1/source/nv_attach_impl_frida_setup.cpp'
PROVIDER = TASK / 'li6-li7-combined-host-v1/source/provider_router_exact.cpp'
ADAPTER = TASK / 'li6-li7-combined-model-v1/combined_model_adapter/__init__.py'
CPP = '''
// Execution-time recovered-SASS binding opt-in; native PTX is independent.
static bool hbfsim_sass_enabled() {
    const char *v = std::getenv("HBFSIM_ENABLE_SASS_LIFTER");
    return v && std::strcmp(v, "1") == 0;
}
extern "C" __attribute__((visibility("default"))) int hbfsim_sass_switch_abi_v1() { return 1; }
'''
PY = '''
    # Keep recovered dispatch off unless explicitly enabled. Native PTX stays available.
    import os as _sass_os
    import sys as _sass_sys
    _sass_value = _sass_os.environ.get("HBFSIM_ENABLE_SASS_LIFTER", "0")
    if _sass_value not in ("0", "1"):
        raise RuntimeError("HBFSIM_ENABLE_SASS_LIFTER must be 0 or 1")
    if _sass_value == "0":
        print("HBFSim: recovered SASS binding disabled because recovery is experimental and may not preserve every kernel's semantics; this adapter's recovered weight paths are uncovered. Native PTX remains available.", file=_sass_sys.stderr)
        if _sass_os.environ.get("HBFSIM_INSTRUMENTATION_POLICY") == "strict":
            raise RuntimeError("strict recovered-SASS scope requested while SASS binding is disabled")
        return
    import ctypes as _sass_ctypes
    _sass_process = _sass_ctypes.CDLL(None)
    try:
        for _sass_symbol in ("hbfsim_sass_switch_abi_v1", "hbfsim_sass_provider_switch_abi_v1"):
            _sass_capability = getattr(_sass_process, _sass_symbol)
            _sass_capability.restype = _sass_ctypes.c_int
            if _sass_capability() != 1:
                raise RuntimeError("unsupported SASS switch ABI")
    except AttributeError as _sass_error:
        raise RuntimeError("SASS research mode requires switch-aware agent and provider overlay builds") from _sass_error
'''

def once(text, old, new):
    if text.count(old) != 1:
        raise ValueError('source interface changed: ' + old)
    return text.replace(old, new, 1)

def transform(role, text):
    if role == 'agent':
        # The recovered selector and all its call sites already use qkv_enabled.
        export = 'extern "C" __attribute__((visibility("default"))) int hbfsim_sass_switch_abi_v1() { return 1; }'
        # qkv_enabled lives in an anonymous namespace; export capability at file scope.
        text = export + '\n' + once(text, 'bool qkv_enabled() {', CPP.replace(export, '') + '\nbool qkv_enabled() {\n    if (!hbfsim_sass_enabled()) return false;')
    elif role == 'provider':
        # Only recovered-identity queries are gated; general native observation remains.
        marker = 'extern "C" int hbfsim_qkv_live_identity_v1('
        if marker not in text:
            raise ValueError('provider identity interface changed')
        text = text.replace(marker, CPP.replace('hbfsim_sass_switch_abi_v1', 'hbfsim_sass_provider_switch_abi_v1') + '\n' + marker, 1)
        for name in ('hbfsim_qkv_live_identity_v1', 'hbfsim_qkv_live_identity_for_entry_v1'):
            start = text.index('extern "C" int ' + name + '(')
            brace = text.index('{', start)
            text = text[:brace+1] + '\n  if (!hbfsim_sass_enabled()) return 0;\n' + text[brace+1:]
    elif role == 'adapter':
        import re
        matches = list(re.finditer(r'^def register\([^\n]*\)(?:\s*->[^:]*)?:\s*\n', text, re.M))
        if len(matches) != 1:
            raise ValueError('adapter register interface changed')
        end = matches[0].end()
        text = text[:end] + PY + text[end:]
    return text

def agent_input(repository, custom=None, pin=None):
    if custom is None:
        if pin is not None:
            raise ValueError('--agent-input-sha256 requires --agent-input')
        path = repository/AGENT
    else:
        if not pin:
            raise ValueError('custom agent input requires --agent-input-sha256')
        path = custom.resolve(strict=True)
    raw = path.read_bytes()
    if custom is not None and hashlib.sha256(raw).hexdigest() != pin:
        raise ValueError('custom agent input SHA256 mismatch')
    return path, raw


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--repository', type=Path, default=Path(__file__).resolve().parents[2])
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--agent-input', type=Path)
    p.add_argument('--agent-input-sha256')
    a = p.parse_args()
    if a.output.exists():
        p.error('output must be a fresh directory')
    agent_path, agent_raw = agent_input(a.repository, a.agent_input, a.agent_input_sha256)
    originals = [('agent', agent_path, agent_raw)] + [(role, a.repository/path, (a.repository/path).read_bytes()) for role, path in [('provider', PROVIDER), ('adapter', ADAPTER)]]
    modified = [(role, path, raw, transform(role, raw.decode()).encode()) for role, path, raw in originals]
    a.output.mkdir(parents=True)
    # Preserve companion headers and package data needed by the existing recipes.
    shutil.copytree(a.repository/AGENT.parent, a.output/'source')
    shutil.copytree(a.repository/ADAPTER.parent, a.output/'combined_model_adapter')
    metadata = a.repository/ADAPTER.parent.parent/'combined_model_adapter-0.1.dist-info'
    shutil.copytree(metadata, a.output/metadata.name)
    rows = []
    for role, path, raw, output in modified:
        target = a.output / ('combined_model_adapter/__init__.py' if role == 'adapter' else 'source/'+ ('nv_attach_impl_frida_setup.cpp' if role == 'agent' else path.name))
        target.write_bytes(output)
        rows.append(dict(role=role, source=str(path), source_sha256=hashlib.sha256(raw).hexdigest(), output=str(target.relative_to(a.output)), output_sha256=hashlib.sha256(output).hexdigest()))
    known = {r['output'] for r in rows}
    for target in sorted(a.output.rglob('*')):
        if target.is_file() and target.relative_to(a.output).as_posix() not in {x.replace('\\','/') for x in known}:
            rows.append(dict(role='companion', output=target.relative_to(a.output).as_posix(), output_sha256=hashlib.sha256(target.read_bytes()).hexdigest()))
    (a.output/'SASS_SWITCH_OVERLAY.json').write_text(json.dumps(dict(schema='hbfsim.sass_switch_overlay.v1', files=rows, compiled=False, default='0', research='explicit 1'), indent=2)+'\n')

if __name__ == '__main__':
    main()
