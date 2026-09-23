#!/usr/bin/env python3
"""Stage one PTX entry after a fail-closed, entry-scoped call proof."""
from __future__ import annotations
import argparse, base64, hashlib, importlib.util, json, pathlib, re, sys

CALL_RE = re.compile(rb"\bcall(?:\.uni)?\b")
CALL_TARGET_RE = re.compile(
  rb"(?:@[!A-Za-z0-9_.$%-]+\s+)?call(?:\.uni)?\s+"
  rb"(?:\([^)]*\)\s*,\s*)?([.$A-Za-z_][.$A-Za-z0-9_]*)\s*,")
REG = rb"%[A-Za-z][A-Za-z0-9_]*"
SYM = rb"[$A-Za-z_][.$A-Za-z0-9_$]*"

def sha(b: bytes) -> str: return hashlib.sha256(b).hexdigest()

def lexical_mask(payload: bytes) -> bytes:
    out=bytearray(payload); i=0; state="code"; quote=0
    while i<len(payload):
        b=payload[i]; n=payload[i+1] if i+1<len(payload) else -1
        if state=="code":
            if b==47 and n==47: out[i:i+2]=b"  "; i+=2; state="line"; continue
            if b==47 and n==42: out[i:i+2]=b"  "; i+=2; state="block"; continue
            if b in (34,39): quote=b; out[i]=32; i+=1; state="quote"; continue
            i+=1; continue
        if state=="line":
            if b in (10,13): state="code"
            else: out[i]=32
            i+=1; continue
        if state=="block":
            out[i]=32
            if b==42 and n==47: out[i+1]=32; i+=2; state="code"
            else: i+=1
            continue
        out[i]=32
        if b==92 and i+1<len(payload): out[i+1]=32; i+=2
        elif b==quote: i+=1; state="code"
        else: i+=1
    if state in {"block","quote"}: raise ValueError(f"unterminated lexical construct: {state}")
    return bytes(out)

def entry_body(payload: bytes, entry: str) -> tuple[bytes,bytes,dict]:
    clean=lexical_mask(payload)
    pat=re.compile(rb"(?m)^\s*(?:\.visible\s+)?\.entry\s+"+re.escape(entry.encode())+rb"\s*\(")
    matches=list(pat.finditer(clean))
    if len(matches)!=1: raise ValueError(f"entry declaration count {len(matches)} != 1")
    brace=clean.find(b"{",matches[0].end())
    if brace<0: raise ValueError("entry opening brace absent")
    depth=1; cursor=brace+1
    while cursor<len(clean) and depth:
        if clean[cursor]==123: depth+=1
        elif clean[cursor]==125: depth-=1
        cursor+=1
    if depth: raise ValueError("entry braces unbalanced")
    raw_body=payload[brace+1:cursor-1]; clean_body=clean[brace+1:cursor-1]
    return raw_body,clean_body,{"declaration_start":matches[0].start(),"body_start":brace+1,
      "body_end":cursor-1,"body_bytes":len(raw_body),"body_sha256":sha(raw_body)}

def assertfail_abi_ok(clean: bytes) -> bool:
    extern=re.compile(
      rb"\.extern\s+\.func\s+__assertfail\s*\(\s*"
      rb"\.param\s+\.b64\s+\w+\s*,\s*\.param\s+\.b64\s+\w+\s*,\s*"
      rb"\.param\s+\.b32\s+\w+\s*,\s*\.param\s+\.b64\s+\w+\s*,\s*"
      rb"\.param\s+\.b64\s+\w+\s*\)\s*;",re.S)
    if len(extern.findall(clean))!=1: return False
    # No defined body with the same symbol.
    return re.search(rb"\.func\b[^{;]*\b__assertfail\b[^{;]*\{",clean,re.S) is None

def helper_definition(payload: bytes, name: bytes) -> dict:
    clean=lexical_mask(payload)
    if name==b"__hbfsim_fault":
        pattern=re.compile(
          rb"(?m)^[ \t]*\.visible\s+\.func\s+__hbfsim_fault"
          rb"\s*\(\s*\.param\s+\.b32\s+__hbfsim_fault_param_0\s*\)\s*\{")
        expected_sha="1cd6cf977ac03dfd1ba30a864aad6cc61a5beec34ffaa18af7c112afba4c3c84"
        abi="void(u32)"
    elif name==b"__hbfsim_resolve":
        pattern=re.compile(
          rb"(?m)^[ \t]*\.visible\s+\.func\s+\(\s*"
          rb"\.param\s+\.align\s+8\s+\.b8\s+func_retval0\[16\]\s*\)\s+"
          rb"__hbfsim_resolve\s*\(\s*"
          rb"\.param\s+\.b64\s+__hbfsim_resolve_param_0\s*,\s*"
          rb"\.param\s+\.b32\s+__hbfsim_resolve_param_1\s*,\s*"
          rb"\.param\s+\.b32\s+__hbfsim_resolve_param_2\s*\)\s*\{")
        expected_sha="a9a98c3ec616564efdd2def20b464420e7c03613075de2ccc6851eeae0cd4bbe"
        abi="ret16(u64,u32,u32)"
    else:
        raise ValueError(f"helper is not admitted: {name!r}")
    matches=list(pattern.finditer(clean))
    if len(matches)!=1: raise ValueError(f"helper definition/ABI count differs: {name!r}")
    brace=clean.find(b"{",matches[0].start(),matches[0].end()+1); depth=1; cursor=brace+1
    while cursor<len(clean) and depth:
        if clean[cursor]==123: depth+=1
        elif clean[cursor]==125: depth-=1
        cursor+=1
    if depth: raise ValueError(f"helper body braces unbalanced: {name!r}")
    definition=payload[matches[0].start():cursor]
    actual_sha=sha(definition)
    if actual_sha!=expected_sha:
        raise ValueError(f"helper definition hash differs: {name!r} {actual_sha}")
    return {"name":name.decode(),"abi":abi,"definition_bytes":len(definition),
            "definition_sha256":actual_sha}

def prove_static_assertfail(payload: bytes, entry: str,
                            allowed_instrumentation_helpers: tuple[bytes,...]=()) -> dict:
    raw_body,body,span=entry_body(payload,entry)
    calls=list(CALL_TARGET_RE.finditer(body)); targets=[m.group(1) for m in calls]
    assert_calls=[m for m in calls if m.group(1)==b"__assertfail"]
    extras=[target for target in targets if target!=b"__assertfail"]
    if len(assert_calls)!=1 or any(target not in allowed_instrumentation_helpers for target in extras):
        raise ValueError(f"selected entry call targets are {[x.decode() for x in targets]}")
    if len(CALL_RE.findall(body))!=len(calls): raise ValueError("unparsed call instruction present")
    helper_proofs=[helper_definition(payload,name) for name in sorted(set(extras))]
    clean=lexical_mask(payload)
    if not assertfail_abi_ok(clean): raise ValueError("exact extern __assertfail ABI not proven")
    call=assert_calls[0]; block_start=body.rfind(b"{",0,call.start()); block_end=body.find(b"}",call.end())
    if block_start<0 or block_end<0: raise ValueError("assertfail parameter block not bounded")
    block=body[block_start:block_end+1]
    # The only admitted setup is six consecutive, unconditional instructions.
    # No label, branch, predicate, or other instruction may occur between them
    # and the parameter block.
    setup_re=re.compile(
      rb"(?m)^[ \t]*mov\.u64\s+("+REG+rb")\s*,\s*("+SYM+rb")\s*;[ \t]*\r?\n"
      rb"^[ \t]*cvta\.global\.u64\s+("+REG+rb")\s*,\s*\1\s*;[ \t]*\r?\n"
      rb"^[ \t]*mov\.u64\s+("+REG+rb")\s*,\s*("+SYM+rb")\s*;[ \t]*\r?\n"
      rb"^[ \t]*cvta\.global\.u64\s+("+REG+rb")\s*,\s*\4\s*;[ \t]*\r?\n"
      rb"^[ \t]*mov\.u64\s+("+REG+rb")\s*,\s*("+SYM+rb")\s*;[ \t]*\r?\n"
      rb"^[ \t]*cvta\.global\.u64\s+("+REG+rb")\s*,\s*\7\s*;[ \t]*(?:\r?\n)?\Z")
    setup=setup_re.search(body[:block_start])
    if setup is None: raise ValueError("assertfail static setup is not an unconditional consecutive sequence")
    source_regs=(setup.group(1),setup.group(4),setup.group(7))
    symbols=(setup.group(2),setup.group(5),setup.group(8))
    pointer_regs=(setup.group(3),setup.group(6),setup.group(9))
    if len(set(source_regs+pointer_regs))!=6:
        raise ValueError("assertfail setup registers alias across pointer pairs")
    # Full-match the block so an extra overwrite cannot hide among known stores.
    ws=rb"\s*"
    block_re=re.compile(
      rb"\{"+ws+
      rb"\.param\s+\.b64\s+param0\s*;"+ws+
      rb"st\.param\.b64\s+\[param0\+0\]\s*,\s*"+re.escape(pointer_regs[0])+rb"\s*;"+ws+
      rb"\.param\s+\.b64\s+param1\s*;"+ws+
      rb"st\.param\.b64\s+\[param1\+0\]\s*,\s*"+re.escape(pointer_regs[1])+rb"\s*;"+ws+
      rb"\.param\s+\.b32\s+param2\s*;"+ws+
      rb"st\.param\.b32\s+\[param2\+0\]\s*,\s*1478\s*;"+ws+
      rb"\.param\s+\.b64\s+param3\s*;"+ws+
      rb"st\.param\.b64\s+\[param3\+0\]\s*,\s*"+re.escape(pointer_regs[2])+rb"\s*;"+ws+
      rb"\.param\s+\.b64\s+param4\s*;"+ws+
      rb"st\.param\.b64\s+\[param4\+0\]\s*,\s*1\s*;"+ws+
      rb"call(?:\.uni)?\s+__assertfail\s*,\s*\("+ws+
      rb"param0\s*,\s*param1\s*,\s*param2\s*,\s*param3\s*,\s*param4"+ws+
      rb"\)\s*;"+ws+rb"\}",re.S)
    if block_re.fullmatch(block) is None: raise ValueError("assertfail parameter block differs or has extra instructions")
    proven=[]
    for store_index,source_reg,symbol,value in zip((0,1,3),source_regs,symbols,pointer_regs):
        declaration=re.compile(
          rb"(?m)^\.global\s+\.align\s+1\s+\.b8\s+"+re.escape(symbol)+
          rb"\s*\[(\d+)\]\s*=\s*\{([^}]*)\}\s*;")
        declarations=declaration.findall(clean)
        if len(declarations)!=1:
            raise ValueError(f"static module-global declaration not unique: {symbol!r}")
        declared_size=int(declarations[0][0])
        try: initializer=[int(part.strip()) for part in declarations[0][1].split(b",") if part.strip()]
        except ValueError as exc: raise ValueError("non-integer static global initializer") from exc
        if not initializer or len(initializer)>declared_size or any(value_<0 or value_>255 for value_ in initializer):
            raise ValueError(f"static global initializer/size mismatch: {symbol!r}")
        materialized=initializer+[0]*(declared_size-len(initializer))
        if 0 not in materialized:
            raise ValueError(f"static assert string is not NUL terminated: {symbol!r}")
        proven.append({"parameter":store_index,"symbol":symbol.decode(),"source_register":source_reg.decode(),
                       "pointer_register":value.decode(),"global_bytes":declared_size,
                       "global_initializer_sha256":sha(bytes(materialized))})
    proof={"proof":"KNOWN_SYSTEM_CALL_DEPENDENCY_ASSERTFAIL","entry":entry,
      **span,"total_call_count":len(calls),"assertfail_call_count":1,
      "callee":"__assertfail","external_system_call":True,
      "assertfail_line":1478,"assertfail_char_size":1,"pointer_arguments":proven,
      "comment_and_string_aware":True,"reachable_unknown_callee_count":0,
      "instrumentation_helper_call_count":len(extras),"instrumentation_helpers":helper_proofs}
    return proof

