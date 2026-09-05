"""Owned-process route shared memory, with no inference runtime import.

Only the supplied capturer module binding changes. Original-class handles and
descriptor identities establish ownership, not an attempted name. This is not
an atomic identity-checked unlink or a hostile-namespace isolation mechanism.
"""
from __future__ import annotations

import os
from pathlib import Path
import re
import stat

ROOT = Path(__file__).resolve().parents[2]
_NAME = re.compile(r'vllm_routed_experts_buffer_[1-9][0-9]{0,24}_0')


def _fd_identity(handle):
    info = os.fstat(handle._fd)
    if not stat.S_ISREG(info.st_mode):
        raise ValueError('owned shared memory descriptor is not regular')
    return info.st_dev, info.st_ino, info.st_size


def _namespace_identity(name):
    if type(name) is not str or _NAME.fullmatch(name) is None:
        raise ValueError('invalid owned shared memory name')
    try:
        info = os.stat(Path('/dev/shm') / name, follow_symlinks=False)
    except FileNotFoundError:
        return None
    if not stat.S_ISREG(info.st_mode):
        raise ValueError('owned shared memory namespace entry is not regular')
    return info.st_dev, info.st_ino, info.st_size


class _ModuleProxy:
    def __init__(self, scope):
        self.scope = scope
        self.SharedMemory = scope._open

    def __getattr__(self, name):
        return getattr(self.scope.original, name)


class OwnedRouteMemory:
    """One exclusive creator and one reader in a fresh private worker.

    Enter before LLM construction. After observation, reconcile the actual
    instance/rank. Finish reader/capturer/client independently in the worker's
    finally block and require a successful report before publishing success.
    Context exit also covers failures before singleton assignment. Reports are
    lifecycle diagnostics and never establish capture origin or scientific gold.
    """
    def __init__(self, module, private_tmp, *, max_bytes=16 << 20):
        path = Path(os.path.abspath(private_tmp))
        if path.resolve() != path or not path.is_relative_to(ROOT) or path == ROOT or not path.is_dir():
            raise ValueError('route lock directory must be a private project directory')
        if type(max_bytes) is not int or not 0 < max_bytes <= 64 << 20:
            raise ValueError('invalid route shared memory size cap')
        if module._TMP_DIR != str(path) or module._LOCK_FILE_PREFIX != str(path/'vllm_routed_experts'):
            raise ValueError('capturer imported before private temporary environment was installed')
        if isinstance(module.shared_memory, _ModuleProxy):
            raise ValueError('route shared memory scope already installed')
        self.module, self.original = module, module.shared_memory
        self.cls = self.original.SharedMemory
        self.max_bytes = max_bytes
        self.proxy = _ModuleProxy(self)
        self.entered = self.finished = self.creator_attempted = self.reader_attempted = False
        self.records = []
        self.creator = None
        self.report = None

    def __enter__(self):
        if self.entered or self.finished or self.module.shared_memory is not self.original:
            raise RuntimeError('conflicting or reused route shared memory scope')
        self.module.shared_memory = self.proxy
        self.entered = True
        return self

    def __exit__(self, error_type, *_):
        already_finished = self.finished
        report = self.finish()
        if not already_finished and error_type is None and report['status'] != 'OWNED_MEMORY_CLOSED':
            raise RuntimeError('owned route memory cleanup failed')

    def _open(self, name=None, create=False, size=0):
        if not self.entered or self.finished or self.module.shared_memory is not self.proxy:
            raise RuntimeError('route memory binding is inactive or changed')
        if type(name) is not str or _NAME.fullmatch(name) is None or type(create) is not bool or type(size) is not int:
            raise ValueError('invalid route shared memory constructor')
        if create:
            if self.creator_attempted or not 0 < size <= self.max_bytes:
                raise ValueError('only one bounded exclusive route buffer creation is permitted')
            self.creator_attempted = True
        else:
            if self.creator is None or not self.creator['complete'] or self.reader_attempted or \
               name != self.creator['name'] or size != 0:
                raise ValueError('reader must attach once to this scope creator using the native default size')
            if _namespace_identity(name) != self.creator['identity']:
                raise ValueError('route shared memory namespace changed before reader attachment')
            self.reader_attempted = True
        # Retain the object even when initialization fails after an exclusive
        # allocation. An attempted name without a live FD never grants ownership.
        handle = self.cls.__new__(self.cls)
        record = dict(handle=handle, name=name, create=create, identity=None, complete=False)
        self.records.append(record)
        try:
            self.cls.__init__(handle, name=name, create=create, size=size)
        except BaseException as error:
            if getattr(handle, '_fd', -1) >= 0:
                record['identity'] = _fd_identity(handle)
                if create: self.creator = record
            if create and isinstance(error, FileExistsError):
                raise RuntimeError('route shared memory name already exists; attachment refused') from error
            raise
        record['identity'] = _fd_identity(handle)
        if create: self.creator = record
        if type(handle) is not self.cls or handle.name != name or handle.size != record['identity'][2] or \
           (create and handle.size != size) or \
           (not create and record['identity'] != self.creator['identity']):
            raise ValueError('route shared memory returned descriptor identity/size mismatch')
        record['complete'] = True
        return handle

    def reconcile(self, instance_id, dp_rank):
        if type(instance_id) is not str or type(dp_rank) is not int or dp_rank != 0 or \
           self.creator is None or not self.creator['complete'] or \
           self.creator['name'] != f'vllm_routed_experts_buffer_{instance_id}_{dp_rank}':
            raise ValueError('route buffer differs from constructed runtime instance/rank')

    def finish(self, capturer=None, reader=None, client=None):
        if self.finished:
            return self.report
        self.finished = True
        errors = []
        cleanup_completed = False

        def attempt(label, operation):
            try:
                operation()
            except BaseException as error:
                errors.append(label + ': ' + type(error).__name__ + ': ' + str(error))

        try:
            for label, owner, creator in (('reader', reader, False), ('capturer', capturer, True)):
                if owner is None: continue
                handle = getattr(owner, '_shm', None)
                if handle is None: continue
                matching = [record for record in self.records
                            if record['handle'] is handle and record['create'] is creator]
                if len(matching) != 1 or matching[0]['identity'] is None:
                    errors.append(label + ': singleton handle is not owned by this scope')
                    continue
                record = matching[0]
                if creator:
                    try:
                        identity = _namespace_identity(record['name'])
                    except BaseException as error:
                        errors.append('namespace: ' + type(error).__name__ + ': ' + str(error)); identity = False
                    if identity != record['identity']:
                        # Prevent a later owned singleton destructor unlinking a
                        # replaced name. Closing our saved FD remains safe.
                        owner._shm = None
                        if identity is not None: errors.append('capturer: namespace identity changed')
                        continue
                attempt(label, lambda: owner.cleanup())
                if getattr(handle, '_fd', -1) != -1 or getattr(handle, '_buf', None) is not None or \
                   getattr(handle, '_mmap', None) is not None:
                    errors.append(label + ': native cleanup did not close saved handle')
                # Native cleanup normally clears this field even on failure.
                # Retain our handle independently for bounded fallback below.
                if getattr(owner, '_shm', None) is handle: owner._shm = None
            if client is not None:
                attempt('engine', lambda: client.shutdown())
            for record in reversed(self.records):
                handle = record['handle']
                if record['identity'] is None and getattr(handle, '_fd', -1) >= 0:
                    errors.append('live descriptor identity was not established; name is not owned')
                if getattr(handle, '_fd', -1) >= 0 or getattr(handle, '_mmap', None) is not None or \
                   getattr(handle, '_buf', None) is not None:
                    attempt('close recorded handle', handle.close)
            if self.creator is not None and self.creator['identity'] is not None:
                def unlink_owned():
                    record = self.creator
                    identity = _namespace_identity(record['name'])
                    if identity is None: return
                    if identity != record['identity']:
                        raise ValueError('namespace replacement; unlink refused')
                    record['handle'].unlink()
                attempt('unlink owned creator', unlink_owned)
                def check_absent():
                    if _namespace_identity(self.creator['name']) is not None:
                        raise ValueError('exact owned name remains or was replaced')
                attempt('verify namespace absence', check_absent)
            for record in self.records:
                handle = record['handle']
                if getattr(handle, '_fd', -1) != -1 or getattr(handle, '_buf', None) is not None or \
                   getattr(handle, '_mmap', None) is not None:
                    errors.append('saved route handle remains open')
            cleanup_completed = True
        finally:
            if not cleanup_completed:
                errors.append('cleanup interrupted before all saved handles were verified')
            if self.entered:
                if self.module.shared_memory is self.proxy:
                    self.module.shared_memory = self.original
                else:
                    errors.append('route module binding changed before restoration')
            self.report = dict(schema_version=1,
                status='FAILED_MEMORY_CLEANUP' if errors else 'OWNED_MEMORY_CLOSED',
                errors=errors, scientific_validation_passed=False,
                records=[dict(name=r['name'], create=r['create'], complete=r['complete'],
                              descriptor_identity=r['identity']) for r in self.records],
                boundary='Process-local owned handle cleanup; name replacement and resource-tracker unlink are not atomic.')
        return self.report
