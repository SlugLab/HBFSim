"""ctypes adapter for the native byte-lossless EQ3TMK1 codec.

The shared library performs canonical integer parsing/formatting and zlib I/O.
Python intentionally retains SHA-256 and end-to-end byte verification so the
native acceleration does not weaken the campaign evidence contract.
"""
import ctypes
import hashlib
import os
from pathlib import Path


CHUNK = 65536
_ERROR_CAPACITY = 1024


def _library_path(explicit=None):
    if explicit is not None:
        return Path(explicit)
    configured = os.environ.get("EQ3_CAMPAIGN_NATIVECODEC")
    if configured:
        return Path(configured)
    raise RuntimeError(
        "native codec library path required via library= or "
        "EQ3_CAMPAIGN_NATIVECODEC"
    )


def _load(explicit=None):
    library = ctypes.CDLL(str(_library_path(explicit).resolve()))
    byte_pointer = ctypes.POINTER(ctypes.c_ubyte)
    error_pointer = ctypes.POINTER(ctypes.c_char)

    library.eq3tmk_encoder_open.argtypes = [
        ctypes.c_char_p, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_uint32,
        ctypes.c_int, error_pointer, ctypes.c_size_t,
    ]
    library.eq3tmk_encoder_open.restype = ctypes.c_void_p
    library.eq3tmk_encoder_feed.argtypes = [
        ctypes.c_void_p, byte_pointer, ctypes.c_size_t,
        error_pointer, ctypes.c_size_t,
    ]
    library.eq3tmk_encoder_feed.restype = ctypes.c_int
    library.eq3tmk_encoder_finish.argtypes = [
        ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint64),
        ctypes.POINTER(ctypes.c_uint64), error_pointer, ctypes.c_size_t,
    ]
    library.eq3tmk_encoder_finish.restype = ctypes.c_int
    library.eq3tmk_encoder_free.argtypes = [ctypes.c_void_p]
    library.eq3tmk_encoder_free.restype = None

    library.eq3tmk_decoder_open.argtypes = [
        ctypes.c_char_p, ctypes.POINTER(ctypes.c_uint32),
        ctypes.POINTER(ctypes.c_uint32), ctypes.POINTER(ctypes.c_uint32),
        ctypes.POINTER(ctypes.c_size_t), error_pointer, ctypes.c_size_t,
    ]
    library.eq3tmk_decoder_open.restype = ctypes.c_void_p
    library.eq3tmk_decoder_next.argtypes = [
        ctypes.c_void_p, byte_pointer, ctypes.c_size_t,
        ctypes.POINTER(ctypes.c_size_t), error_pointer, ctypes.c_size_t,
    ]
    library.eq3tmk_decoder_next.restype = ctypes.c_int
    library.eq3tmk_decoder_free.argtypes = [ctypes.c_void_p]
    library.eq3tmk_decoder_free.restype = None
    library.eq3tmk_codec_version.argtypes = []
    library.eq3tmk_codec_version.restype = ctypes.c_char_p
    return library


def _error_buffer():
    return ctypes.create_string_buffer(_ERROR_CAPACITY)


def _message(error):
    return error.value.decode("utf-8", errors="replace") or "native codec error"


class Encoder:
    """Streaming native encoder with the same public shape as the Python codec."""

    def __init__(self, path, nx, ny, frames, level=1, *, library=None):
        if (not all(isinstance(value, int) for value in (nx, ny, frames, level)) or
                min(nx, ny, frames) < 1 or nx * ny > 400000 or
                max(nx, ny, frames) > 2**32 - 1 or not 0 <= level <= 9):
            raise ValueError("invalid codec dimensions/level")
        self.path = Path(path)
        self.nx, self.ny, self.frames = nx, ny, frames
        self._library = _load(library)
        self._handle = None
        self._digest = hashlib.sha256()
        self._bytes = 0
        error = _error_buffer()
        handle = self._library.eq3tmk_encoder_open(
            os.fsencode(self.path), nx, ny, frames, level, error, len(error)
        )
        if not handle:
            raise ValueError(_message(error))
        self._handle = handle

    def feed(self, data):
        if self._handle is None:
            raise ValueError("native encoder is closed")
        data = bytes(data)
        self._digest.update(data)
        self._bytes += len(data)
        # ctypes permits a null pointer for a zero-length feed.
        storage = (ctypes.c_ubyte * len(data)).from_buffer_copy(data) if data else None
        error = _error_buffer()
        status = self._library.eq3tmk_encoder_feed(
            self._handle, storage, len(data), error, len(error)
        )
        if status != 0:
            raise ValueError(_message(error))

    def finish(self):
        if self._handle is None:
            raise ValueError("native encoder is closed")
        rows = ctypes.c_uint64()
        native_bytes = ctypes.c_uint64()
        error = _error_buffer()
        status = self._library.eq3tmk_encoder_finish(
            self._handle, ctypes.byref(rows), ctypes.byref(native_bytes),
            error, len(error),
        )
        if status != 0:
            raise ValueError(_message(error))
        if native_bytes.value != self._bytes:
            raise ValueError("native encoder byte count disagrees with Python")
        receipt = {
            "codec": "EQ3TMK1",
            "codec_backend": self._library.eq3tmk_codec_version().decode("ascii"),
            "raw_full_field_retained": True,
            "byte_lossless": True,
            "native_sha256": self._digest.hexdigest(),
            "native_bytes": self._bytes,
            "compressed_bytes": self.path.stat().st_size,
            "frames": self.frames,
            "rows": rows.value,
        }
        self.close()
        return receipt

    def close(self):
        if self._handle is not None:
            self._library.eq3tmk_encoder_free(self._handle)
            self._handle = None

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    def __del__(self):
        try:
            self.close()
        except Exception:
            pass


def decoded_lines(path, *, library=None):
    """Yield decoded native lines and force footer/physical-EOF validation."""
    native = _load(library)
    nx = ctypes.c_uint32()
    ny = ctypes.c_uint32()
    frames = ctypes.c_uint32()
    capacity = ctypes.c_size_t()
    error = _error_buffer()
    handle = native.eq3tmk_decoder_open(
        os.fsencode(Path(path)), ctypes.byref(nx), ctypes.byref(ny),
        ctypes.byref(frames), ctypes.byref(capacity), error, len(error),
    )
    if not handle:
        raise ValueError(_message(error))
    output = (ctypes.c_ubyte * capacity.value)()
    try:
        while True:
            size = ctypes.c_size_t()
            status = native.eq3tmk_decoder_next(
                handle, output, capacity.value, ctypes.byref(size),
                error, len(error),
            )
            if status < 0:
                raise ValueError(_message(error))
            if status == 0:
                break
            # Avoid materializing a Python integer list for every native row.
            yield ctypes.string_at(output, size.value)
    finally:
        native.eq3tmk_decoder_free(handle)


def replay(source, destination, nx, ny, frames, level=1, *, library=None):
    """Encode natively, then independently verify decoded bytes in Python."""
    encoder = Encoder(destination, nx, ny, frames, level, library=library)
    try:
        with Path(source).open("rb") as stream:
            while data := stream.read(CHUNK):
                encoder.feed(data)
        receipt = encoder.finish()
    finally:
        encoder.close()

    digest = hashlib.sha256()
    count = 0
    decoded_rows = 0
    for line in decoded_lines(destination, library=library):
        digest.update(line)
        count += len(line)
        stripped = line.strip()
        if stripped and not stripped.startswith(b"%"):
            decoded_rows += 1
    if digest.hexdigest() != receipt["native_sha256"] or count != receipt["native_bytes"]:
        raise ValueError("byte lossless verification failed")
    if decoded_rows != frames * ny or receipt["rows"] != frames * ny:
        raise ValueError("decoded frame/row validation failed")
    receipt["decoded_sha256_verified"] = True
    receipt["decoded_rows_verified"] = True
    return receipt
