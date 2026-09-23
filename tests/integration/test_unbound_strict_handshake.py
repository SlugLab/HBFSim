from pathlib import Path
import ctypes, json, os, subprocess, sys, tempfile

gate = Path(sys.argv[1]).resolve()
source = Path(sys.argv[2]).read_text()
work = Path(tempfile.mkdtemp(prefix='hbfsim-unbound-handshake-'))

def fake(bits):
    c = work / f'cap{bits}.c'
    so = work / f'cap{bits}.so'
    c.write_text(f'#include <stdint.h>\nuint64_t bpftime_nv_strict_bridge_capabilities_v1(void){{return {bits}ULL;}}\n')
    subprocess.run(['cc','-shared','-fPIC',str(c),'-o',str(so)],check=True)
    return so

child = r'''
import ctypes, json, sys
if sys.argv[2] != '-': ctypes.CDLL(sys.argv[2], mode=ctypes.RTLD_GLOBAL)
g=ctypes.CDLL(sys.argv[1], mode=ctypes.RTLD_GLOBAL)
f=g.hbfsim_test_strict_direct_action_v1; f.argtypes=[ctypes.c_int]; f.restype=ctypes.c_int
print(json.dumps([f(0),f(1),f(2)]))
'''
rows={}
for label, lib in [('missing','-'),('bit0',str(fake(1))),('bit1',str(fake(2))),('both',str(fake(3)))]:
    cp=subprocess.run([sys.executable,'-c',child,str(gate),lib],text=True,capture_output=True)
    rows[label]={'rc':cp.returncode,'values':json.loads(cp.stdout) if cp.returncode==0 else None,'stderr':cp.stderr}

assert rows['missing']['values']==[0,0,0]
assert rows['bit0']['values']==[0,0,0]
assert rows['bit1']['values']==[0,0,0]
assert rows['both']['values']==[0,1,2]
assert source.count('strict_bridge_capabilities(),\n            approval_code(decision)') == 2
assert 'return finish(original(function, grid, block, arguments,' in source
assert 'return finish(original(kernel, grid, block, arguments,' in source
assert source.count('strict_direct_gate_capability_or_decision') == 2
out={'status':'PASS','binary':str(gate),'scenarios':rows,'wrapper_sites':2,'domains':['cudaLaunchKernel/cudart12','__cudaLaunchKernel/cudart13']}
print(json.dumps(out,indent=2))
