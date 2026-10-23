"""Complete live CUDA-storage byte identity, before timing registration.

Copies use framework byte tensors; no model payload is written to disk. Both
normal comparison arms use this path within model loading. These are actual
storage bytes (offset zero through nbytes), not tensor-only or synthetic bytes.
"""
from __future__ import annotations

import hashlib
from typing import Any


def digest_chunks(chunks, expected_length: int) -> str:
    if type(expected_length) is not int or expected_length <= 0:
        raise ValueError("invalid storage identity length")
    digest = hashlib.sha256()
    amount = 0
    for chunk in chunks:
        if not isinstance(chunk, bytes) or not chunk:
            raise ValueError("invalid storage identity chunk")
        amount += len(chunk)
        if amount > expected_length:
            raise ValueError("storage identity overrun")
        digest.update(chunk)
    if amount != expected_length:
        raise ValueError("storage identity incomplete")
    return digest.hexdigest()


def collect_storage_identities(registered, model: Any, *, chunk_bytes=8 << 20):
    import torch

    if type(chunk_bytes) is not int or chunk_bytes <= 0:
        raise ValueError("invalid copy chunk length")
    backing = {}
    for _, parameter in model.named_parameters(recurse=True):
        storage = parameter.untyped_storage()
        index = parameter.device.index
        if index is None:
            index = torch.cuda.current_device()
        key = (int(index), int(storage.data_ptr()), int(storage.nbytes()))
        backing.setdefault(key, storage)
    identities, rows = {}, []
    for storage, amount in registered:
        if amount != storage.size:
            raise ValueError("byte identity requires complete selected storage")
        key = (storage.device, storage.address, storage.size)
        actual = backing[key]
        if (int(actual.data_ptr()), int(actual.nbytes())) != (storage.address, storage.size):
            raise ValueError("live backing identity changed")
        view = torch.empty(0, dtype=torch.uint8,
                           device=torch.device("cuda", storage.device)).set_(
                               actual, 0, (storage.size,), (1,))

        def chunks():
            for start in range(0, storage.size, chunk_bytes):
                host = view[start:min(start + chunk_bytes, storage.size)].cpu()
                yield host.numpy().tobytes(order="C")

        digest = digest_chunks(chunks(), storage.size)
        aliases = tuple(sorted(storage.aliases))
        if aliases in identities:
            raise ValueError("duplicate selected storage aliases")
        row = {"aliases": list(aliases), "device": storage.device,
               "registered_address": storage.address, "length": storage.size,
               "storage_offset_bytes": 0, "sha256": digest}
        identities[aliases] = row
        rows.append(row)
    return identities, {"schema": "hbfsim.live_model_storage_bytes.v1",
                        "content_identity": "LIVE_MODEL_STORAGE_BYTES",
                        "copy_chunk_bytes": chunk_bytes,
                        "total_bytes": sum(row["length"] for row in rows),
                        "storages": rows,
                        "limit": "Complete storage bytes before registration; no MQSim returned-payload claim."}
