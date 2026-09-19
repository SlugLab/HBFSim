"""Recreate the isolated integration targets; no experiment or host installation."""
import argparse,subprocess
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('--root',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
a=p.parse_args();root=a.root.resolve();source=Path(__file__).resolve().parents[1]
base=root/'eq3_thermal/build/baseline-cpu-v1-minimal'
subprocess.run(['/usr/bin/cmake','-S',str(source/'src/eq3_thermal'),'-B',str(a.output),
    '-DCMAKE_BUILD_TYPE=Release','-DBUILD_TESTING=ON',
    '-DHBFSIM_EQ3_HOST_CORE_ARCHIVE='+str(base/'libhbfsim_core.a'),
    '-DHBFSIM_EQ3_MQSIM_ARCHIVE='+str(base/'libmqsim_hbf.a')],check=True)
subprocess.run(['/usr/bin/cmake','--build',str(a.output),'--parallel','1'],check=True)
