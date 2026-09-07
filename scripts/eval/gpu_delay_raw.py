"""Lossless bounded storage for new GPU-delay raw observations.

Compression is opt-in, performed by acquisition callers before sealing. Existing
sealed observations are never migrated. The bound covers the original largest
grid: 188 SMs * 32 active chains/SM * 64 hops = 385024 helper wait records.
256 MiB permits over 600 bytes per wait plus chain/coverage metadata.
"""
from __future__ import annotations
import gzip
import hashlib
import json
import os
from pathlib import Path
import stat

MAX_RAW_BYTES=256*1024*1024
CHUNK_BYTES=1024*1024


def _limit(value):
    if type(value) is not int or not 0<value<=MAX_RAW_BYTES:
        raise ValueError('raw limit must be positive and at most 256 MiB')


def _open(path,limit):
    fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
    try:
        info=os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or not 0<info.st_size<=limit:
            raise ValueError('raw file must be bounded, nonempty, and regular')
        return os.fdopen(fd,'rb')
    except BaseException:
        os.close(fd);raise


def _bytes(path,limit):
    with _open(path,limit) as stream:
        data=stream.read(limit+1)
        if len(data)>limit:raise ValueError('raw file grew past limit')
        return data


def _compressed(directory,max_bytes):
    receipt=json.loads(_bytes(directory/'raw.json.gzip.json',4096))
    required={'schema_version','compression','uncompressed_bytes','uncompressed_sha256',
              'compressed_bytes','compressed_sha256'}
    if (set(receipt)!=required or type(receipt['schema_version']) is not int
            or receipt['schema_version']!=1 or receipt['compression']!='gzip'
            or type(receipt['uncompressed_bytes']) is not int
            or not 0<receipt['uncompressed_bytes']<=max_bytes
            or type(receipt['compressed_bytes']) is not int
            or not 0<receipt['compressed_bytes']<=MAX_RAW_BYTES+4*CHUNK_BYTES):
        raise ValueError('invalid compression sidecar')
    with _open(directory/'raw.json.gz',MAX_RAW_BYTES+4*CHUNK_BYTES) as stream:
        digest=hashlib.sha256();count=0
        while chunk:=stream.read(CHUNK_BYTES):
            digest.update(chunk);count+=len(chunk)
            if count>MAX_RAW_BYTES+4*CHUNK_BYTES:raise ValueError('compressed input grew past limit')
        if count!=receipt['compressed_bytes'] or digest.hexdigest()!=receipt['compressed_sha256']:
            raise ValueError('compressed raw hash/length mismatch')
        stream.seek(0)
        with gzip.GzipFile(fileobj=stream,mode='rb') as decoder:
            data=decoder.read(receipt['uncompressed_bytes']+1)
        if (len(data)!=receipt['uncompressed_bytes']
                or hashlib.sha256(data).hexdigest()!=receipt['uncompressed_sha256']):
            raise ValueError('uncompressed raw hash/length mismatch')
    return data,receipt


def read_raw(directory, *, max_bytes=MAX_RAW_BYTES):
    _limit(max_bytes);directory=Path(directory)
    plain=directory/'raw.json';compressed=directory/'raw.json.gz'
    if os.path.lexists(plain):
        if os.path.lexists(compressed) or os.path.lexists(directory/'raw.json.gzip.json'):
            raise ValueError('ambiguous raw representations')
        return _bytes(plain,max_bytes)
    return _compressed(directory,max_bytes)[0]


def compress_raw(directory, *, max_bytes=MAX_RAW_BYTES):
    """Compress newly produced raw.json, verify losslessly, then remove that input.

    Never overwrites output. Before source removal, failure leaves that source
    intact and cleans only newly created compressed artifacts. If the final
    directory fsync fails after removal, verified gzip and sidecar are retained.
    """
    _limit(max_bytes);directory=Path(directory)
    # Production callers create their attempt and arm directories exclusively.
    # Also reject direct accidental use on an already completed acquisition.
    for parent in (directory,*directory.parents):
        status_path=parent/'status.json'
        if os.path.lexists(status_path):
            status=json.loads(_bytes(status_path,1024*1024))
            if status.get('state') not in {'PLANNED','RUNNING'}:
                raise ValueError('cannot compress completed or inactive acquisition')
            break
    plain=directory/'raw.json';output=directory/'raw.json.gz';sidecar=directory/'raw.json.gzip.json'
    if os.path.lexists(output) or os.path.lexists(sidecar):raise ValueError('compression output already exists')
    created=[]
    try:
        with _open(plain,max_bytes) as source:
            original=os.fstat(source.fileno());digest=hashlib.sha256();count=0
            with output.open('xb') as destination:
                created.append(output)
                with gzip.GzipFile(filename='',fileobj=destination,mode='wb',compresslevel=6,mtime=0) as encoder:
                    while chunk:=source.read(CHUNK_BYTES):
                        count+=len(chunk)
                        if count>max_bytes:raise ValueError('raw grew past limit during compression')
                        digest.update(chunk);encoder.write(chunk)
                destination.flush();os.fsync(destination.fileno())
            after=os.fstat(source.fileno())
            identity=lambda info:(info.st_dev,info.st_ino,info.st_size,info.st_mtime_ns,info.st_ctime_ns)
            if count!=original.st_size or identity(original)!=identity(after):
                raise ValueError('raw source changed during compression')
            compressed_bytes=_bytes(output,MAX_RAW_BYTES+4*CHUNK_BYTES)
            receipt=dict(schema_version=1,compression='gzip',uncompressed_bytes=count,
                         uncompressed_sha256=digest.hexdigest(),compressed_bytes=len(compressed_bytes),
                         compressed_sha256=hashlib.sha256(compressed_bytes).hexdigest())
            with sidecar.open('xb') as stream:
                created.append(sidecar);stream.write(json.dumps(receipt,sort_keys=True).encode()+b'\n')
                stream.flush();os.fsync(stream.fileno())
            _compressed(directory,max_bytes)
            if identity(os.lstat(plain))!=identity(original):raise ValueError('raw path changed before publication')
            # Persist both new directory entries before removing the original.
            # File fsync alone does not durably publish their names.
            fd=os.open(directory,os.O_RDONLY|os.O_DIRECTORY)
            try:os.fsync(fd)
            finally:os.close(fd)
            plain.unlink()
        fd=os.open(directory,os.O_RDONLY|os.O_DIRECTORY)
        try:os.fsync(fd)
        finally:os.close(fd)
        return receipt
    except BaseException:
        # Once source removal succeeded, retain complete compressed observations
        # even if the final directory fsync failed.
        if os.path.lexists(plain):
            for path in reversed(created):path.unlink()
        raise
