"""Actual plugin refuses unsupported futures and mode switching."""
import ctypes
import json
from pathlib import Path
import sys

plugin=ctypes.CDLL(str(Path(sys.argv[1]).resolve()))
plugin.process_input.argtypes=[ctypes.c_char_p,ctypes.c_int,ctypes.c_char_p]
plugin.process_input.restype=ctypes.c_int
complete=len(sys.argv)>2 and sys.argv[2]=='on'
ptx=Path('tests/fixtures/ptx/supported.ptx').read_text()
def invoke(mode,source=ptx):
    data=json.dumps({'input':{'full_ptx':source,'to_patch_kernel':'kernel','transform_mode':mode}}).encode()
    out=ctypes.create_string_buffer(16*1024*1024)
    code=plugin.process_input(data,len(out),out)
    return code,json.loads(out.value) if out.value else {}
code,result=invoke('timing_load_future_v1')
assert code!=0,'C5 request unexpectedly emitted/executed sync fallback'
assert result['error']!='timing_future_unit_incomplete' if complete else result['error']=='timing_future_unit_incomplete',result
assert 'output_ptx' not in result
identity=result.get('transform_identity')
if not complete:assert len(identity)==64
code,sync=invoke('synchronous')
assert code==0,sync
if not complete:
    assert identity!=sync['transform_identity']
    assert invoke('timing_load_future_v1')[1]['transform_identity']==identity
assert invoke('unknown')[1]['error']=='unsupported_transform_mode'
assert invoke('timing_load_future_v1',sync['output_ptx'])[1]['error']=='transform_mode_mismatch'
print('PASS: exact mode identities, C5 refusal and trusted mode-switch rejection')
