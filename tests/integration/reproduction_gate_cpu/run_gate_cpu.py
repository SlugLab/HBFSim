"""Exercise the built launch-gate DSO with a versioned fake cudart, no CUDA device."""
import ctypes as C
import json
import os
from pathlib import Path
import threading

os.environ["CUDA_VISIBLE_DEVICES"] = ""
os.environ["HBFSIM_INSTRUMENTATION_POLICY"] = "strict"
fixture = Path(os.environ["HBFSIM_FAKE_CUDA_DIR"]).resolve()
gate_path = Path(os.environ["HBFSIM_GATE_DSO"]).resolve()
fake = C.CDLL(str(fixture / "libcudart.so.12"), mode=os.RTLD_GLOBAL | os.RTLD_NOW)
gate = C.CDLL(str(gate_path),
              mode=os.RTLD_GLOBAL | os.RTLD_NOW)
libc = C.CDLL(None)
libc.dlvsym.argtypes = [C.c_void_p, C.c_char_p, C.c_char_p]
libc.dlvsym.restype = C.c_void_p

class Dim(C.Structure):
    _fields_ = [("x", C.c_uint), ("y", C.c_uint), ("z", C.c_uint)]

Launch = C.CFUNCTYPE(C.c_int, C.c_void_p, Dim, Dim, C.c_void_p, C.c_size_t, C.c_void_p)
wrapper_address = libc.dlvsym(gate._handle, b"cudaLaunchKernel", b"libcudart.so.12")
assert wrapper_address
launch = Launch(wrapper_address)
private_address = libc.dlvsym(gate._handle, b"__cudaLaunchKernel", b"libcudart.so.13")
assert private_address
private_launch = Launch(private_address)
gate.hbfsim_strict_original_begin_v1.argtypes = [C.c_void_p, C.c_int, C.c_int]
gate.hbfsim_strict_original_begin_v1.restype = C.c_uint64
gate.hbfsim_strict_original_consume_v1.argtypes = [C.c_uint64, C.c_void_p, C.c_int,
    C.c_int, C.c_int, C.POINTER(C.c_int), C.POINTER(C.c_int)]
gate.hbfsim_strict_original_consume_v1.restype = C.c_int
fake.fake_reset.argtypes = [C.c_int]
function = 0x123456
one = Dim(1, 1, 1)
rows = []

def begin(f=function, domain=12, api=1):
    return gate.hbfsim_strict_original_begin_v1(f, domain, api)

def complete(result):
    fake.fake_reset(result)
    ret = launch(function, one, one, None, 0, None)
    return ret, fake.fake_public_calls(), fake.fake_private_calls()

def complete_private(result):
    fake.fake_reset(result)
    ret = private_launch(function, one, one, None, 0, None)
    return ret, fake.fake_public_calls(), fake.fake_private_calls()

def consume(token, f=function, domain=12, api=1, observed=0):
    decision, result = C.c_int(-1), C.c_int(-1)
    status = gate.hbfsim_strict_original_consume_v1(token, f, domain, api,
                                                     observed, C.byref(decision), C.byref(result))
    return status, decision.value, result.value

for expected in (0, 801):
    token = begin()
    actual, public, private = complete(expected)
    got = consume(token, observed=actual)
    rows.append({"case": "public12_original", "expected": expected, "actual": actual,
                 "public_calls": public, "private_calls": private, "consume": got})
    assert actual == expected and public == 1 and private == 0 and got == (1, 1, expected)
    assert consume(token, observed=actual)[0] == 0

for expected in (0, 801):
    token = begin(domain=13, api=2)
    actual, public, private = complete_private(expected)
    got = consume(token, domain=13, api=2, observed=actual)
    rows.append({"case": "private13_original", "expected": expected, "actual": actual,
                 "public_calls": public, "private_calls": private, "consume": got})
    assert actual == expected and public == 0 and private == 1 and got == (1, 1, expected)
    assert consume(token, domain=13, api=2, observed=actual)[0] == 0

outer = begin()
inner = begin()
assert consume(outer)[0] == 0  # top token mismatch retains both frames
assert complete(0) == (0, 1, 0)
assert consume(inner) == (1, 1, 0)
assert complete(0) == (0, 1, 0)
assert consume(outer) == (1, 1, 0)
rows.append({"case": "nested_lifo_wrong_top", "status": "PASS"})

def mismatch(case, kwargs):
    token = begin()
    assert complete(0) == (0, 1, 0)
    wrong = consume(token, **kwargs)
    later = consume(token)
    assert wrong[0] == 0 and later[0] == 0
    rows.append({"case": case, "wrong": wrong, "later": later,
                 "matching_token_failure_pops_frame": True})

mismatch("wrong_function", {"f": function + 1})
mismatch("wrong_domain", {"domain": 13})
mismatch("wrong_api", {"api": 2})
mismatch("wrong_observed_return", {"observed": 801})

token = begin()
other = []
thread = threading.Thread(target=lambda: other.append(consume(token)))
thread.start(); thread.join()
assert other[0][0] == 0
assert complete(0) == (0, 1, 0)
assert consume(token) == (1, 1, 0)
rows.append({"case": "cross_thread", "other": other[0], "owner": "PASS"})

assert begin(0) == 0 and begin(function, 11, 1) == 0 and begin(function, 12, 3) == 0
rows.append({"case": "invalid_begin", "status": "PASS"})
gate.hbfsim_test_strict_direct_action_v1.argtypes = [C.c_int]
gate.hbfsim_test_strict_direct_action_v1.restype = C.c_int
actions = [gate.hbfsim_test_strict_direct_action_v1(x) for x in (0, 1, 2)]
assert actions == [0, 1, 2]
rows.append({"case": "decision_helper", "actions": actions,
             "boundary": "helper only; no actual patched driver launch"})
print(json.dumps({"status": "PASS", "scope": "real gate DSO public12/private13 wrappers; fake cudart and inert fake driver",
                  "gate": str(gate_path),
                  "cuda_visible_devices": os.environ["CUDA_VISIBLE_DEVICES"], "rows": rows}, indent=2))
