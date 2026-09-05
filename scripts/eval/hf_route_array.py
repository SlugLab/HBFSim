"""Bounded frozen route-array decoding, without NumPy or origin claims."""
from __future__ import annotations

import ast
from dataclasses import dataclass
import re
import sys

MAX_ARRAY_BYTES=1<<20
MAX_HEADER_BYTES=4096


@dataclass(frozen=True)
class DecodedRoutes:
    shape:tuple[int,int,int]
    dtype:str
    values:tuple[int,...]


def _require(condition,message):
    if not condition:raise ValueError('HF route array: '+message)


def decode_route_array(raw,protocol,*,expected_dtype):
    """Decode only the fixed control's integer C-order NPY v1/v2 subset.

    The byte/header/shape bounds precede payload decoding. Values are detached
    immutable Python integers; no file, runtime package or GPU is accessed.
    Matching values alone do not authenticate their origin or validate a trace.
    """
    _require(type(raw) is bytes and 10<=len(raw)<=MAX_ARRAY_BYTES,'bounded bytes required')
    _require(type(protocol) is dict and type(expected_dtype) is str,'protocol/dtype types')
    shape=protocol.get('route_shape');experts=protocol.get('experts')
    _require(type(shape) is list and len(shape)==3 and all(type(n) is int and n>0 for n in shape),'protocol shape')
    shape=tuple(shape)
    _require(type(experts) is int and experts>0 and shape[2]<=experts,'protocol expert bound')
    count=shape[0]*shape[1]*shape[2]
    _require(count<=MAX_ARRAY_BYTES,'protocol element bound')
    _require(raw[:6]==b'\x93NUMPY' and raw[6:8] in (b'\x01\x00',b'\x02\x00'),'magic/version')
    prefix=10 if raw[6]==1 else 12
    _require(len(raw)>=prefix,'truncated header length')
    length=int.from_bytes(raw[8:prefix],'little');end=prefix+length
    _require(0<length<=MAX_HEADER_BYTES and end<=len(raw),'bounded complete header')
    header=raw[prefix:end]
    _require(header.endswith(b'\n'),'header newline')
    try:
        tree=ast.parse(header.decode('ascii').strip(),mode='eval').body
        _require(type(tree) is ast.Dict and len(tree.keys)==3,'exact header fields')
        _require(all(type(k) is ast.Constant and type(k.value) is str for k in tree.keys),'literal header keys')
        _require({k.value for k in tree.keys}=={'descr','fortran_order','shape'},'unique header fields')
        _require(sum(1 for _ in ast.walk(tree))<=64,'header expression bound')
        fields=ast.literal_eval(tree)
    except (SyntaxError,UnicodeError,TypeError,RecursionError) as error:
        raise ValueError('HF route array: invalid literal header') from error
    actual=fields['shape']
    _require(type(actual) is tuple and all(type(n) is int for n in actual) and actual==shape,'exact protocol shape')
    _require(fields['fortran_order'] is False,'C-order required')
    descr=fields['descr'];match=re.fullmatch(r'([<>|])([iu])([1248])',descr) if type(descr) is str else None
    _require(match is not None,'supported integer dtype required')
    order,kind,width=match.groups();width=int(width)
    _require(order!='|' or width==1,'byte-order independent multi-byte dtype')
    native='<' if sys.byteorder=='little' else '>'
    dtype=('int' if kind=='i' else 'uint')+str(width*8) if width==1 or order==native else descr
    _require(dtype==expected_dtype,'raw-return dtype mismatch')
    _require(count<=(MAX_ARRAY_BYTES-end)//width and len(raw)-end==count*width,'exact bounded payload size')
    byteorder='big' if order=='>' else 'little'
    values=tuple(int.from_bytes(raw[pos:pos+width],byteorder,signed=kind=='i')
                 for pos in range(end,len(raw),width))
    _require(all(0<=value<experts for value in values),'expert ID outside inventory')
    topk=shape[2]
    _require(all(len(set(values[pos:pos+topk]))==topk for pos in range(0,count,topk)),'duplicate top-k expert')
    return DecodedRoutes(shape,dtype,values)
