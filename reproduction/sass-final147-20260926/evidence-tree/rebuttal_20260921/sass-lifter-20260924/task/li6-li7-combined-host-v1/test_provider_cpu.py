"""Injected lineage/entry fixture; no CUDA context or model/GPU launch."""
import ctypes
import json
import os
from pathlib import Path
import resource
import subprocess

TASK = Path(__file__).resolve().parent
BUILD = TASK / "build-v5"
ARGV = BUILD / "provider_compile.argv.json"
TEST_DSO = BUILD / "libprovider_identity_cpu_test.so"
IMAGE = b"d15cf2497649902226341d260eee82160e485d38e76cdcb9cb581eca82167eb0"
LI6 = b"_ZN8internal5gemvx6kernelIii13__nv_bfloat16S2_S2_fLb0ELb1ELb1ELb0ELi6ELb0E18cublasGemvParamsExIi30cublasGemvTensorStridedBatchedIKS2_ES6_S4_IS2_EfEEENSt9enable_ifIXntT5_EvE4typeET11_"
LI7 = LI6.replace(b"ELi6E", b"ELi7E")

class Identity(ctypes.Structure):
    _fields_ = [("struct_size", ctypes.c_uint32), ("reserved", ctypes.c_uint32),
                ("context", ctypes.c_uint64), ("module", ctypes.c_uint64),
                ("association_token", ctypes.c_uint64), ("image_sha256", ctypes.c_char * 65)]

def limit():
    resource.setrlimit(resource.RLIMIT_AS, (3 * 1024**3, 3 * 1024**3))

def main():
    if TEST_DSO.exists():
        raise RuntimeError("CPU test DSO exists; no overwrite")
    args = json.loads(ARGV.read_text())
    args.insert(1, "-DHBFSIM_QKV_IDENTITY_CPU_TEST=1")
    args[args.index("-o") + 1] = str(TEST_DSO)
    (BUILD/"provider_fixture_compile.argv.json").write_text(json.dumps(args,indent=2)+"\n")
    with (BUILD/"provider_fixture_compile.stdout").open("wb") as out, (BUILD/"provider_fixture_compile.stderr").open("wb") as err:
        proc = subprocess.run(args, cwd=TASK, stdout=out, stderr=err, timeout=300,
                              preexec_fn=limit, env={**os.environ,"CUDA_VISIBLE_DEVICES":""})
    (BUILD/"provider_fixture_compile.rc").write_text(f"{proc.returncode}\n")
    if proc.returncode: raise RuntimeError("provider CPU fixture compilation failed")
    os.environ.pop("HBFSIM_PROVIDER_CORRELATION_LOG",None)
    os.environ.pop("HBFSIM_PROVIDER_MODULE_DIR",None)
    os.environ["CUDA_VISIBLE_DEVICES"] = ""
    os.environ["HBFSIM_QKV_COMBINED_V1"] = "1"
    dso = ctypes.CDLL(str(TEST_DSO))
    inject = dso.hbfsim_qkv_test_inject
    inject.argtypes = [ctypes.c_uint64,ctypes.c_uint64,ctypes.c_uint64,
                       ctypes.c_uint64,ctypes.c_char_p,ctypes.c_char_p]
    typed = dso.hbfsim_qkv_live_identity_for_entry_v1
    typed.argtypes = [ctypes.c_uint64,ctypes.c_uint64,ctypes.c_char_p,
                      ctypes.POINTER(Identity)]
    typed.restype = ctypes.c_int
    legacy = dso.hbfsim_qkv_live_identity_v1
    legacy.argtypes = [ctypes.c_uint64,ctypes.c_uint64,ctypes.POINTER(Identity)]
    legacy.restype = ctypes.c_int
    unload = dso.hbfsim_qkv_test_unload_module
    unload.argtypes = [ctypes.c_uint64]
    callback = dso.hbfsim_qkv_test_callback_fields
    callback.argtypes = [ctypes.c_char_p,ctypes.c_int]
    callback.restype = ctypes.c_char_p
    def query(fn, symbol, context=41):
        out = Identity(); out.struct_size = ctypes.sizeof(Identity)
        return typed(fn,context,symbol,ctypes.byref(out)),out
    assert ctypes.sizeof(Identity) == 104
    inject(11,101,201,41,LI6,IMAGE)
    inject(12,102,202,41,LI7,IMAGE)
    assert query(11,LI6)[0] == 1 and query(12,LI7)[0] == 1
    assert query(11,LI7)[0] == 0 and query(12,LI6)[0] == 0
    for symbol in (LI6,LI7):
        fields=callback(symbol,211).decode()
        assert '"cbid":211' in fields and '"callback_tid":' in fields
    assert callback(b"wrong-symbol",211) == b""
    assert query(11,b"not-an-exact-entry")[0] == -1
    assert query(11,LI6,42)[0] == 0
    out=Identity();out.struct_size=104
    assert legacy(11,41,ctypes.byref(out)) == 1
    out=Identity();out.struct_size=104
    assert legacy(12,41,ctypes.byref(out)) == 0
    unload(101)
    assert query(11,LI6)[0] == 0 and query(12,LI7)[0] == 1
    inject(12,102,202,41,LI7,b"0"*64)
    assert query(12,LI7)[0] == 0
    result={"schema":"hbfsim.combined_provider_cpu_fixture.v1",
            "status":"PASS_NO_GPU", "cases":["interleaved_li6_li7",
            "wrong_entry_same_image","invalid_entry","wrong_context",
            "legacy_v1_unchanged","stale_module","wrong_image",
            "both_exact_callback_fields"],
            "cuda_context_created":False}
    (BUILD/"provider_fixture_result.json").write_text(json.dumps(result,indent=2)+"\n")

if __name__ == "__main__": main()
