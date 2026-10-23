"""Pure source contract; no CUDA import or tools."""
from pathlib import Path
import hashlib,json

def check(root):
    source=root/'source/src/cuda_runtime/device/hbf_device.cu'
    s=source.read_text();body=(root/'build-prep/ORIGINAL_ACCOUNT_BODY.txt').read_text()
    begin=s.index('__device__ void account_add(')
    tail=s[begin:s.index('\n__device__ std::uint64_t range_intersection_bytes(',begin)]
    default=tail.split('#else',1)[1].rsplit('#endif',1)[0]
    assert default==body,'default0 scalar body changed'
    scalar=s[s.index('__device__ __forceinline__ void account_add_scalar_original'):]
    scalar=scalar[scalar.index('{')+1:scalar.index('\n}\n')+1]
    assert scalar==body,'enabled fallback scalar body changed'
    copied=(root/'tests/scalar_candidate.inc').read_text()
    assert copied[copied.index('{')+1:copied.rindex('}')]==body,'CPU scalar is not actual original body'
    # After subtracting the declared exact hunks, full original CU identity returns.
    start=s.index('#if HBFSIM_ENABLE_UNIFORM_ACCOUNT_AGGREGATION && defined(__CUDA_ARCH__)')
    finish=s.index('\n__device__ std::uint64_t range_intersection_bytes(',start)
    original_function='__device__ void account_add(std::uint64_t* field, std::uint64_t value)\n{'+body+'}'
    restored=s[:start]+original_function+'\n'+s[finish:]
    prologue='''\n\n#ifndef HBFSIM_ENABLE_UNIFORM_ACCOUNT_AGGREGATION
#define HBFSIM_ENABLE_UNIFORM_ACCOUNT_AGGREGATION 0
#endif
#if HBFSIM_ENABLE_UNIFORM_ACCOUNT_AGGREGATION
#include "uniform_account_logic.hpp"
#endif'''
    assert restored.count(prologue)==1
    restored=restored.replace(prologue,'',1)
    digest=hashlib.sha256(restored.encode()).hexdigest()
    assert digest=='0de9f5688e6dad245555b618a97e17f8578ff4681074661734c2a0f30e37f056',digest
    assert hashlib.sha256((source.parent/'hbf_device.cuh').read_bytes()).hexdigest()=='5a484beb055275869b02767ac22be3811f61d8aab4479ab93428fc889c0e9032'
    return {'status':'PASS','default_scalar_body_exact':True,'fallback_scalar_body_exact':True,'CPU_scalar_body_exact':True,'outside_declared_hunks_reconstructed_original_sha256':digest,'cuh_unchanged':True,'GPU_calls':0}

if __name__=='__main__':print(json.dumps(check(Path(__file__).resolve().parents[1])))
