"""Serial read-only-existing-core and fixed Python regression, no research run."""
import argparse,os,subprocess,sys
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('--root',type=Path,required=True);a=p.parse_args()
root=a.root.resolve();code=Path(__file__).resolve().parents[1];base=root/'eq3_thermal/build/baseline-cpu-v1-minimal'
# These are CPU-only executables already audited/built in P0; no CTest history is overwritten.
names=['protocol','page_directory','range_table','timing_binding','device_helper_abi','backing_store',
       'capacity_backing_router','hbm_cache','vmm','capacity_page_service','capacity_handoff','capacity_dispatch','capacity_worker']
for name in names:
    print('CORE_TEST',name,flush=True)
    subprocess.run([str(base/f'hbfsim_{name}_tests')],check=True)
env=os.environ.copy()
for name in ('EQ3_LAYERED_RC_RUNNER','EQ3_CAMPAIGN_RC_RUNNER'):env.pop(name,None)
print('FIXED_PYTHON_DISCOVERY (optional solver executable tests skip without opt-in)',flush=True)
subprocess.run([sys.executable,'-B','-m','unittest','discover','-s',str(code/'tools'),'-p','test_eq3_*.py','-v'],env=env,check=True)
