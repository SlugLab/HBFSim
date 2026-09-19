"""Lossless canonical stock Tmap codec: integer milli-K frame deltas + zlib.

No float32 or rounding. Noncanonical numeric rows are rejected. Headers and
blank lines are literal records; canonical rows reconstruct byte-for-byte.
This is storage-only; callers retain the experiment gate and resource limits.
"""
from array import array
import hashlib
from pathlib import Path
import re
import struct
import zlib

MAGIC = b'EQ3TMK1\n'
HEADER = struct.Struct('<III')
UINT = struct.Struct('<I')
TOKEN = re.compile(rb'-?\d+\.\d{3}\Z')
CHUNK = 65536


def native_value(value):
    sign = b'-' if value < 0 else b''
    value = abs(value)
    return (sign + str(value // 1000).encode() + b'.' +
            f'{value % 1000:03d}'.encode()).rjust(7) + b'  '


class Encoder:
    def __init__(self, path, nx, ny, frames, level=1):
        if min(nx, ny, frames) < 1 or nx * ny > 400000 or not 0 <= level <= 9:
            raise ValueError('invalid codec dimensions/level')
        self.path = Path(path)
        self.file = self.path.open('xb')
        self.file.write(MAGIC + HEADER.pack(nx, ny, frames))
        self.z = zlib.compressobj(level)
        self.nx, self.ny, self.frames = nx, ny, frames
        self.previous = array('i', [0]) * (nx * ny)
        self.pending = b''
        self.rows = self.bytes = 0
        self.digest = hashlib.sha256()

    def feed(self, data):
        self.digest.update(data)
        self.bytes += len(data)
        lines = (self.pending + data).split(b'\n')
        self.pending = lines.pop()
        if len(self.pending) > max(CHUNK, self.nx * 32):
            raise ValueError('unbounded native line')
        for line in lines:
            line += b'\n'
            stripped = line.strip()
            if not stripped or stripped.startswith(b'%'):
                self.file.write(self.z.compress(b'L' + UINT.pack(len(line)) + line))
                continue
            tokens = stripped.split()
            if len(tokens) != self.nx or any(not TOKEN.fullmatch(t) for t in tokens):
                raise ValueError('not a finite three-decimal canonical numeric row')
            values = []
            for token in tokens:
                negative = token.startswith(b'-')
                whole, fraction = token.lstrip(b'-').split(b'.')
                value = (int(whole) * 1000 + int(fraction)) * (-1 if negative else 1)
                if not -2**31 <= value < 2**31:
                    raise ValueError('milli-K outside int32 range')
                values.append(value)
            if b''.join(native_value(v) for v in values) + b'\n' != line:
                raise ValueError('native row is not exactly reconstructible canonical format')
            if self.rows >= self.frames * self.ny:
                raise ValueError('extra native rows')
            offset = (self.rows % self.ny) * self.nx
            deltas = [v - self.previous[offset+i] for i, v in enumerate(values)]
            if any(not -2**31 <= d < 2**31 for d in deltas):
                raise ValueError('frame delta outside int32 range')
            self.file.write(self.z.compress(b'D' + struct.pack(f'<{self.nx}i', *deltas)))
            self.previous[offset:offset+self.nx] = array('i', values)
            self.rows += 1

    def finish(self):
        if self.pending or self.rows != self.frames * self.ny:
            raise ValueError('truncated native fields')
        self.file.write(self.z.flush())
        self.file.close()
        return {'codec': 'EQ3TMK1', 'raw_full_field_retained': True,
                'byte_lossless': True, 'native_sha256': self.digest.hexdigest(),
                'native_bytes': self.bytes, 'compressed_bytes': self.path.stat().st_size,
                'frames': self.frames, 'rows': self.rows}


def decoded_lines(path):
    """Bounded decoder; consumes and validates the zlib footer and physical EOF."""
    with Path(path).open('rb') as stream:
        if stream.read(len(MAGIC)) != MAGIC:
            raise ValueError('codec magic mismatch')
        header = stream.read(HEADER.size)
        if len(header) != HEADER.size:
            raise ValueError('truncated codec header')
        nx, ny, frames = HEADER.unpack(header)
        if min(nx, ny, frames) < 1 or nx * ny > 400000:
            raise ValueError('invalid codec dimensions')
        previous = array('i', [0]) * (nx * ny)
        rows = 0
        decoder = zlib.decompressobj()
        packed = b''
        pending = b''
        exhausted = False
        while True:
            if not packed and not exhausted:
                packed = stream.read(CHUNK)
                exhausted = not packed
            if packed:
                pending += decoder.decompress(packed, CHUNK)
                packed = decoder.unconsumed_tail
                if decoder.unused_data:
                    raise ValueError('trailing compressed payload')
            while pending:
                tag = pending[:1]
                if tag == b'D':
                    length = 1 + nx * 4
                    if len(pending) < length:
                        break
                    if rows >= ny * frames:
                        raise ValueError('extra codec rows')
                    delta = struct.unpack(f'<{nx}i', pending[1:length])
                    offset = rows % ny * nx
                    values = [previous[offset+i]+d for i,d in enumerate(delta)]
                    if any(not -2**31 <= v < 2**31 for v in values):
                        raise ValueError('decoded value outside int32')
                    previous[offset:offset+nx] = array('i', values)
                    rows += 1
                    yield b''.join(native_value(v) for v in values) + b'\n'
                elif tag == b'L':
                    if len(pending) < 5:
                        break
                    count = UINT.unpack(pending[1:5])[0]
                    if count > max(CHUNK, nx*32):
                        raise ValueError('literal exceeds bounded line')
                    length = 5 + count
                    if len(pending) < length:
                        break
                    literal = pending[5:length]
                    if not literal.endswith(b'\n') or (literal.strip() and not literal.strip().startswith(b'%')):
                        raise ValueError('invalid literal line')
                    yield literal
                else:
                    raise ValueError('invalid codec record')
                pending = pending[length:]
            if exhausted and not packed:
                break
        if not decoder.eof or pending or rows != ny*frames:
            raise ValueError('truncated codec stream or frames')


def replay(source, destination, nx, ny, frames, level=1):
    encoder = Encoder(destination, nx, ny, frames, level)
    try:
        with Path(source).open('rb') as stream:
            while data := stream.read(CHUNK):
                encoder.feed(data)
        receipt = encoder.finish()
    finally:
        encoder.file.close()
    digest = hashlib.sha256()
    count = 0
    for line in decoded_lines(destination):
        digest.update(line)
        count += len(line)
    if digest.hexdigest() != receipt['native_sha256'] or count != receipt['native_bytes']:
        raise ValueError('byte lossless verification failed')
    receipt['decoded_sha256_verified'] = True
    return receipt
