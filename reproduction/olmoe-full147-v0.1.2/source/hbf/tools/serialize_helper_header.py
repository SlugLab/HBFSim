"""Represent the original normalized PTX bytes with explicit string_view length.

No PTX normalization or compiler option changes. Input must be the exact
existing CMake EmbedDevicePtx output. Output is an isolated successor file.
"""
import argparse,hashlib,json,pathlib
PREFIX=b'#pragma once\n#include <string_view>\nnamespace hbfsim::ptx {\ninline constexpr std::string_view kEmbeddedDevicePtx = R"HBFDEV80F5898('
SUFFIX=b')HBFDEV80F5898";\n}  // namespace hbfsim::ptx\n'
NEW_PREFIX=b'#pragma once\n#include <string_view>\nnamespace hbfsim::ptx {\nstatic constexpr char kEmbeddedDevicePtxBytes[] = R"HBFDEV80F5898('
NEW_SUFFIX=b')HBFDEV80F5898";\ninline constexpr std::string_view kEmbeddedDevicePtx{kEmbeddedDevicePtxBytes, sizeof(kEmbeddedDevicePtxBytes) - 1};\n}  // namespace hbfsim::ptx\n'
def render(data):
    if not data.startswith(PREFIX) or not data.endswith(SUFFIX):raise ValueError('unexpected original generated header grammar')
    payload=data[len(PREFIX):-len(SUFFIX)]
    if not payload or b'\0' in payload:raise ValueError('empty or embedded NUL PTX')
    if b')HBFDEV80F5898"' in payload:raise ValueError('raw literal delimiter collision')
    result=NEW_PREFIX+payload+NEW_SUFFIX
    assert result[len(NEW_PREFIX):-len(NEW_SUFFIX)]==payload
    return result,payload
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--input',type=pathlib.Path,required=True);p.add_argument('--output',type=pathlib.Path,required=True);a=p.parse_args()
    result,payload=render(a.input.read_bytes())
    with a.output.open('xb') as f:f.write(result)
    print(json.dumps({'status':'HEADER_SERIALIZATION_ONLY','payload_bytes':len(payload),'payload_sha256':hashlib.sha256(payload).hexdigest(),'embedded_NUL':False,'output_sha256':hashlib.sha256(result).hexdigest()}))
