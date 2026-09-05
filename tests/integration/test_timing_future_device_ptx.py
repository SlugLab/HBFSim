"""Compile-only evidence for the complete C5 token/metadata call ABI; no GPU."""
import json
import os
import shutil
from pathlib import Path
import re
import subprocess
import sys
import tempfile

def function_body(ptx, name):
    match=re.search(r'\.func\b[^{};]*\b'+re.escape(name)+r'\s*\([^;]*?\)\s*\{',ptx)
    assert match, name
    start=match.end();depth=1
    for end in range(start,len(ptx)):
        depth+=(ptx[end]=='{')-(ptx[end]=='}')
        if depth==0:return ptx[start:end]
    raise AssertionError('unterminated helper '+name)

def check_executable_logic(helper):
    issue=function_body(helper,'__hbfsim_timing_future_issue_v1')
    poll=function_body(helper,'__hbfsim_timing_future_poll_v1')
    wait=function_body(helper,'__hbfsim_timing_future_wait_v1')
    # The optional implementation must survive optimization despite public
    # admission staying closed. These inspect operations inside actual helpers.
    assert issue.count('match.any.sync.b32')==3,'range plus full 64-bit page grouping'
    assert 'shfl.sync' in issue and 'atom.cas' in issue,'group broadcast and service reservation'
    for body in (issue,poll,wait):
        assert 'isspacep.local' in body,'kernel-local token/metadata validation'
        assert '%globaltimer' in body and 'ld.acquire.sys' in body,'fresh clock and binding/liveness reads'
    labels={m[1]:m.start() for m in re.finditer(r'(?m)^([\w$]+):',wait)}
    backedges=[(labels[m[1]],m.start()) for m in re.finditer(r'\bbra\s+([\w$]+)\s*;',wait)
               if m[1] in labels and labels[m[1]]<m.start()]
    assert any('%globaltimer' in wait[a:b] for a,b in backedges),'poll loop reloads GPU time'
    assert 'st.param' in issue and 'st.param' in wait,'actual returned token/value payload'

def main():
    helper=Path(sys.argv[1]).read_text();nvcc=Path(sys.argv[2]);ptxas=nvcc.with_name('ptxas')
    root=Path(__file__).resolve().parents[2]
    names=['__hbfsim_timing_future_issue_v1','__hbfsim_timing_future_poll_v1','__hbfsim_timing_future_wait_v1']
    for name in names:assert name in helper,f'missing complete helper: {name}'
    check_executable_logic(helper)
    for corrupted in (helper.replace('match.any.sync.b32','match.broken'),helper.replace('%globaltimer','%clock64')):
        try:check_executable_logic(corrupted)
        except AssertionError:pass
        else:raise AssertionError('compiled-code negative fixture was accepted')
    assert re.search(r'\.func\s+\(\.param\s+\.align\s+16\s+\.b8\s+\w+\[64\]\)\s*__hbfsim_timing_future_issue_v1',helper)
    with tempfile.TemporaryDirectory(prefix='.future-ptx-abi-',dir=root) as directory:
        base=Path(directory);source=base/'probe.cu';ptx=base/'probe.ptx'
        source.write_text(r'''
#include "hbf_device.cuh"
using namespace hbfsim::timing_future;
extern "C" __device__ __constant__ unsigned token_layout[11]={
 sizeof(DeviceTimingFutureV1),alignof(DeviceTimingFutureV1),
 offsetof(DeviceTimingFutureV1,control_alias),offsetof(DeviceTimingFutureV1,control_generation),
 offsetof(DeviceTimingFutureV1,issue_ns),offsetof(DeviceTimingFutureV1,ready_ns),
 offsetof(DeviceTimingFutureV1,deadline_ns),offsetof(DeviceTimingFutureV1,original_address),
 offsetof(DeviceTimingFutureV1,reservation_id),offsetof(DeviceTimingFutureV1,state),offsetof(DeviceTimingFutureV1,status)};
extern "C" __device__ __constant__ unsigned metadata_layout[9]={
 sizeof(TimingFutureLaneMetadataV1),alignof(TimingFutureLaneMetadataV1),
 offsetof(TimingFutureLaneMetadataV1,abi_version),offsetof(TimingFutureLaneMetadataV1,struct_bytes),
 offsetof(TimingFutureLaneMetadataV1,instruction_id),offsetof(TimingFutureLaneMetadataV1,bytes),
 offsetof(TimingFutureLaneMetadataV1,group_mask),offsetof(TimingFutureLaneMetadataV1,group_leader),
 offsetof(TimingFutureLaneMetadataV1,reservation_id)};
extern "C" __global__ void probe(unsigned long long address,unsigned long long* output) {
 TimingFutureLaneMetadataV1 metadata;
 auto future=__hbfsim_timing_future_issue_v1(address,4,9,0,&metadata);
 auto polled=__hbfsim_timing_future_poll_v1(&future,&metadata,9,4);
 auto value=__hbfsim_timing_future_wait_v1(&future,&metadata,123,9,4,0);
 output[0]=value.native_bits;output[1]=value.status;output[2]=polled;
 output[3]=future.control_alias;output[4]=future.control_generation;output[5]=future.issue_ns;
 output[6]=future.ready_ns;output[7]=future.deadline_ns;output[8]=future.original_address;
 output[9]=future.reservation_id;output[10]=unsigned(future.state);output[11]=future.status;
 output[12]=metadata.instruction_id;output[13]=metadata.bytes;output[14]=metadata.group_mask;
 output[15]=metadata.group_leader;output[16]=metadata.reservation_id;
}
''')
        argv=[str(nvcc),'--ptx','-std=c++20','-O3','--gpu-architecture=compute_120',
            '--compiler-bindir=/usr/bin/g++-13','-DHBFSIM_ENABLE_TIMING_FUTURES=1',
            '-I'+str(root/'include'),'-I'+str(root/'src/cuda_runtime/device'),str(source),'-o',str(ptx)]
        def run(argv):
            result=subprocess.run(argv,capture_output=True,text=True)
            print(json.dumps(dict(argv=argv,exit_code=result.returncode)))
            if result.returncode: print(result.stdout+result.stderr)
            result.check_returncode()
        run(argv)
        text=ptx.read_text()
        for name,expected in [('token_layout',[64,16,0,8,16,24,32,40,48,56,60]),
                              ('metadata_layout',[32,8,0,4,8,12,16,20,24])]:
            match=re.search(name+r'\[[0-9]+\]\s*=\s*\{([^}]+)\}',text);assert match,name
            raw=[int(n.strip(),0) for n in match[1].split(',')]
            # NVCC can represent the u32 constant arrays as byte arrays.
            actual=raw if len(raw)==len(expected) else [int.from_bytes(bytes(raw[i:i+4]),'little') for i in range(0,len(raw),4)]
            assert actual==expected,(name,actual)
        for name in names:assert re.search(r'call(?:\.uni)?[\s\S]{0,160}'+name,text),name
        declarations=re.compile(r'\.extern\s+\.func[\s\S]*?;\n')
        text=declarations.sub('',text)
        body=re.sub(r'(?m)^\s*\.(?:version|target|address_size)[^\n]*\n','',helper)
        header=re.search(r'(?m)^\s*\.address_size[^\n]*\n',text)
        assert header
        merged=base/'linked.ptx';merged.write_text(text[:header.end()]+'\n'+body+'\n'+text[header.end():])
        try:
            run([str(ptxas),'-arch=sm_120',str(merged),'-o',str(base/'probe.cubin')])
        finally:
            if os.environ.get('HBFSIM_FUTURE_COMPILE_ARTIFACTS'):
                destination=Path(os.environ['HBFSIM_FUTURE_COMPILE_ARTIFACTS']).resolve()
                assert destination.is_relative_to(root/'results/gold/timing-future-unit')
                shutil.copytree(base,destination)
    print(json.dumps(dict(scope='COMPILE_ONLY_NO_GPU',token_bytes=64,metadata_bytes=32,
        native_dependency_runtime='NOT_PROVEN',linked_helpers=names)))

if __name__=='__main__':main()
