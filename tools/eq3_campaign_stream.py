#!/usr/bin/env python3
"""Output-only stock 3D-ICE FIFO bridge; launch only via the campaign gate.

All native bytes are retained in independent gzip streams. FIFO writer anchors
survive stock's header/frame reopen cycles, then close after producer exit.
"""
import argparse
import gzip
import hashlib
import json
import math
import os
from pathlib import Path
import resource
import selectors
import subprocess
import time
import zlib

CHUNK = 65536


class Field:
    def __init__(self, path, nx, ny, expected):
        self.path = Path(path)
        self.file = self.path.open('xb')
        self.compressor = zlib.compressobj(1, zlib.DEFLATED, 31)
        self.hash = hashlib.sha256()
        self.bytes = self.rows = self.frames = 0
        self.nx, self.ny, self.expected = nx, ny, expected
        self.partial = b''
        self.low, self.high = math.inf, -math.inf

    def feed(self, data):
        self.hash.update(data)
        self.bytes += len(data)
        self.file.write(self.compressor.compress(data))
        lines = (self.partial + data).split(b'\n')
        self.partial = lines.pop()
        if len(self.partial) > max(CHUNK, self.nx * 64):
            raise ValueError('field line exceeds bounded buffer')
        for line in lines:
            line = line.strip()
            if not line or line.startswith(b'%'):
                continue
            values = [float(v) for v in line.split()]
            if len(values) != self.nx or not all(map(math.isfinite, values)):
                raise ValueError('field width or finite-value check failed')
            self.low = min(self.low, min(values))
            self.high = max(self.high, max(values))
            self.rows += 1
            self.frames = self.rows // self.ny
            if self.rows > self.expected * self.ny:
                raise ValueError('extra field rows')

    def finish(self):
        self.file.write(self.compressor.flush())
        self.file.close()
        if self.partial or self.rows != self.expected * self.ny:
            raise ValueError('truncated field line/frame count')
        return self.record()

    def record(self):
        return {'path': self.path.name, 'uncompressed_sha256': self.hash.hexdigest(),
                'uncompressed_bytes': self.bytes, 'frames': self.frames,
                'rows': self.rows, 'temperature_min_k': self.low if self.rows else None,
                'temperature_max_k': self.high if self.rows else None,
                'domain_300_400_k': self.rows > 0 and self.low >= 300 and self.high <= 400,
                'compressed_bytes': self.path.stat().st_size}

    def close_failed(self):
        if not self.file.closed:
            try:
                self.file.write(self.compressor.flush())
            finally:
                self.file.close()


class NativeField:
    """EQ3TMK1 acceleration, with independent incoming-byte identity."""
    def __init__(self, path, nx, ny, expected, library):
        from eq3_campaign_nativecodec import Encoder
        self.path = Path(path)
        self.encoder = Encoder(path, nx, ny, expected, library=library)
        self.hash = hashlib.sha256()
        self.bytes = 0
        self.finished = None

    def feed(self, data):
        self.hash.update(data)
        self.bytes += len(data)
        self.encoder.feed(data)

    def finish(self):
        self.finished = self.encoder.finish()
        if (self.finished['native_sha256'] != self.hash.hexdigest() or
                self.finished['native_bytes'] != self.bytes):
            raise ValueError('native codec incoming-byte identity mismatch')
        return self.record()

    def record(self):
        result = self.finished or {}
        return {'path': self.path.name, 'codec': 'EQ3TMK1',
                'uncompressed_sha256': self.hash.hexdigest(),
                'uncompressed_bytes': self.bytes,
                'frames': result.get('frames'), 'rows': result.get('rows'),
                'temperature_min_k': None, 'temperature_max_k': None,
                'domain_300_400_k': 'PENDING_POSTRUN_OBSERVATION',
                'compressed_bytes': self.path.stat().st_size}

    def close_failed(self):
        # Do not manufacture a valid footer for a truncated/invalid native stream.
        # Retain its raw encoded prefix and FIFO as failed transport evidence.
        self.encoder.close()


def verify_gzip(path, expected_hash, expected_bytes):
    """Streaming replay reads through gzip footer; no uncompressed disk copy."""
    digest = hashlib.sha256()
    count = 0
    with gzip.open(path, 'rb') as stream:
        while data := stream.read(CHUNK):
            digest.update(data)
            count += len(data)
    if digest.hexdigest() != expected_hash or count != expected_bytes:
        raise ValueError('gzip byte-lossless check failed')
    return count


def replay(source, destination, nx, ny, frames):
    field = Field(destination, nx, ny, frames)
    try:
        with Path(source).open('rb') as stream:
            while data := stream.read(CHUNK):
                field.feed(data)
        record = field.finish()
        verify_gzip(destination, record['uncompressed_sha256'], record['uncompressed_bytes'])
        return record
    finally:
        field.file.close()


def run_stream(argv, directory, layers, frames, nx, ny, watchdog=600,
               max_bytes=4 * 1024**3, policy='gzip', codec_library=None):
    if min(layers, frames, nx, ny) < 1 or not 0 < watchdog <= 600:
        raise ValueError('invalid dimensions or watchdog')
    if policy not in ('gzip', 'lossless_EQ3TMK1'):
        raise ValueError('unknown output policy')
    if policy == 'lossless_EQ3TMK1' and codec_library is None:
        raise ValueError('native output policy requires explicit codec library')
    directory = Path(directory)
    if (directory / 'stream_receipt.json').exists():
        raise FileExistsError('stream receipt already exists')
    for z in range(layers):
        for suffix in ('.txt', '.txt.gz', '.txt.tmk', '.tmk'):
            if (directory / f'field_{z}{suffix}').exists():
                raise FileExistsError('field output already exists')
    started = time.monotonic()
    selector = selectors.DefaultSelector()
    anchors, readers, fields, pipes = [], [], [], []
    process = None
    status, error, records = 'FAILED', None, []
    def child_limits():
        resource.setrlimit(resource.RLIMIT_AS, (12 * 1024**3, 12 * 1024**3))
    try:
        for z in range(layers):
            pipe = directory / f'field_{z}.txt'
            os.mkfifo(pipe, 0o600)
            pipes.append(pipe)
            reader = os.open(pipe, os.O_RDONLY | os.O_NONBLOCK)
            readers.append(reader)
            anchors.append(os.open(pipe, os.O_WRONLY | os.O_NONBLOCK))
            field = (Field(directory / f'field_{z}.txt.gz', nx, ny, frames)
                     if policy == 'gzip' else
                     NativeField(directory / f'field_{z}.txt.tmk', nx, ny, frames, codec_library))
            fields.append(field)
            selector.register(reader, selectors.EVENT_READ, field)
        process = subprocess.Popen(argv, cwd=directory, close_fds=True,
                                   preexec_fn=child_limits)
        while selector.get_map():
            if time.monotonic() - started > watchdog:
                raise TimeoutError('solver plus compression watchdog exceeded')
            if process.poll() is not None and anchors:
                for fd in anchors:
                    os.close(fd)
                anchors.clear()
            for key, _ in selector.select(.05):
                try:
                    data = os.read(key.fd, CHUNK)
                except BlockingIOError:
                    continue
                if data:
                    key.data.feed(data)
                else:
                    selector.unregister(key.fd)
            # Include staged inputs and stdout/stderr; FIFO sizes are zero.
            occupied = sum(p.stat().st_size for p in directory.rglob('*') if p.is_file())
            # Reserve compressor buffers, gzip trailers and receipt space.
            if occupied + layers * CHUNK + 1024**2 > max_bytes:
                raise ValueError('point output budget exceeded')
        if process.wait() != 0:
            raise ValueError('stock producer failed')
        records = [field.finish() for field in fields]
        status = 'PASS'
    except BaseException as exc:
        error = f'{type(exc).__name__}: {exc}'
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        raise
    finally:
        for fd in anchors + readers:
            os.close(fd)
        selector.close()
        for field in fields:
            field.close_failed()
        receipt = {'status': status, 'error': error,
                   'output_policy': policy,
                   'raw_full_field_retained': status == 'PASS',
                   'retention': ('byte-lossless native stream: '+policy) if status == 'PASS' else 'failed prefixes retained',
                   'gzip_crc_verification': ('PENDING_POSTRUN_STREAMING_REPLAY'
                                             if policy == 'gzip' else 'NOT_APPLICABLE'),
                   'decoded_hash_verification': 'PENDING_POSTRUN_STREAMING_REPLAY',
                   'wall_seconds': time.monotonic() - started,
                   'producer_exit_code': process.returncode if process else None,
                   'fields': records or [field.record() for field in fields]}
        with (directory / 'stream_receipt.json').open('x') as stream:
            json.dump(receipt, stream, indent=2, allow_nan=False)
        if status == 'PASS':
            for pipe in pipes:
                pipe.unlink()
    return receipt


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--backend', required=True)
    p.add_argument('--stack', required=True)
    for flag in ('layers', 'frames', 'nx', 'ny'):
        p.add_argument('--' + flag, type=int, required=True)
    p.add_argument('--watchdog', type=float, default=600)
    p.add_argument('--policy', choices=('gzip','lossless_EQ3TMK1'), default='gzip')
    p.add_argument('--codec-library')
    a = p.parse_args()
    # A low soft ceiling for this bridge must not propagate to the solver.
    hard = resource.getrlimit(resource.RLIMIT_AS)[1]
    if hard != resource.RLIM_INFINITY and hard < 12 * 1024**3:
        raise ValueError('outer hard AS limit cannot accommodate approved solver ceiling')
    resource.setrlimit(resource.RLIMIT_AS, (512 * 1024**2, 12 * 1024**3))
    os.sched_setaffinity(0, {min(os.sched_getaffinity(0))})
    backend = Path(a.backend)
    if not backend.is_absolute():
        backend = Path(os.environ.get('EQ3_ARTIFACT_ROOT', str(Path.cwd()))) / backend
    codec_library = Path(a.codec_library) if a.codec_library else None
    if codec_library is not None and not codec_library.is_absolute():
        codec_library = Path(os.environ.get('EQ3_ARTIFACT_ROOT', str(Path.cwd()))) / codec_library
    run_stream([str(backend.resolve()), a.stack], Path.cwd(),
               a.layers, a.frames, a.nx, a.ny, a.watchdog,
               policy=a.policy, codec_library=codec_library)


if __name__ == '__main__':
    main()
