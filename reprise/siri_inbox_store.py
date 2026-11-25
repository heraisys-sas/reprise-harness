"""Private durable inbox for explicit Siri requests; no dispatch or model execution."""
import contextlib
import fcntl
import json
import os
from pathlib import Path
import stat
import time
import uuid

import siri_inbox_model as model
from siri_inbox_acl import require_no_extended_acl
import vault

PURPOSE = 'siri-inbox-v1'
MAX_STORE = 4 * 1024 * 1024


class WriteUncertain(OSError):
    """Replacement may be visible; retry only using the same request identity."""


class MissingRegistry(ValueError):
    """Initialization evidence and encrypted registry disagree; do not reset."""


def _directory(fd):
    info = os.fstat(fd)
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise ValueError('Private owned directory required')
    require_no_extended_acl(fd)


def _file(fd):
    info = os.fstat(fd)
    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_nlink != 1 or info.st_mode & 0o077:
        raise ValueError('Private owned file required')
    require_no_extended_acl(fd)
    return info


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf-8')


class Inbox:
    def __init__(self, state_directory, *, crypt=None):
        self.state = Path(state_directory).expanduser().absolute()
        self.crypt = crypt or vault.crypt
        root = os.open(self.state, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            _directory(root)
            try:
                os.mkdir('siri-inbox.noindex', 0o700, dir_fd=root)
            except FileExistsError:
                pass
            folder = os.open('siri-inbox.noindex', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=root)
            try:
                _directory(folder)
                os.fsync(root)
            finally:
                os.close(folder)
        finally:
            os.close(root)

    @contextlib.contextmanager
    def _locked(self):
        root = os.open(self.state, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        folder = lock = None
        try:
            _directory(root)
            folder = os.open('siri-inbox.noindex', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=root)
            _directory(folder)
            try:
                lock = os.open('inbox.lock', os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=folder)
            except FileExistsError:
                lock = os.open('inbox.lock', os.O_RDWR | os.O_NOFOLLOW, dir_fd=folder)
            _file(lock)
            deadline = time.monotonic() + 5
            while True:
                try:
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if time.monotonic() >= deadline:
                        raise TimeoutError('Inbox lock busy')
                    time.sleep(.02)
            yield folder
        finally:
            if lock is not None: os.close(lock)
            if folder is not None: os.close(folder)
            os.close(root)

    def _read(self, folder):
        try:
            marker = os.open('initialized', os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=folder)
        except FileNotFoundError:
            initialized = False
        else:
            try:
                initialized = _file(marker).st_size == 0
                if not initialized:
                    raise ValueError('Invalid inbox marker')
            finally:
                os.close(marker)
        try:
            fd = os.open('inbox.enc', os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=folder)
        except FileNotFoundError:
            if initialized:
                raise MissingRegistry('Initialized inbox registry is missing')
            return model.empty(), False
        try:
            if not initialized:
                raise MissingRegistry('Inbox initialization marker is missing')
            if _file(fd).st_size > MAX_STORE:
                raise ValueError('Inbox too large')
            payload = bytearray()
            while len(payload) <= MAX_STORE:
                chunk = os.read(fd, min(65536, MAX_STORE + 1 - len(payload)))
                if not chunk:
                    break
                payload.extend(chunk)
            if len(payload) > MAX_STORE:
                raise ValueError('Inbox too large')
        finally:
            os.close(fd)
        plain = self.crypt(bytes(payload), PURPOSE, 'open')
        if not isinstance(plain, bytes) or len(plain) > MAX_STORE:
            raise ValueError('Invalid inbox')
        return model.validate(json.loads(plain)), True

    def _change(self, change):
        with self._locked() as folder:
            before, exists = self._read(folder)
            after, result = change(before)
            model.validate(after)
            if after == before:
                if exists:
                    try:
                        os.fsync(folder)
                    except OSError as exc:
                        raise WriteUncertain('Inbox durability remains uncertain') from exc
                return result
            plain = _json(after)
            if len(plain) > MAX_STORE:
                raise ValueError('Inbox too large')
            encrypted = self.crypt(plain, PURPOSE, 'seal')
            if not isinstance(encrypted, bytes) or not encrypted or len(encrypted) > MAX_STORE:
                raise ValueError('Invalid ciphertext')
            name = '.inbox-' + str(uuid.uuid4())
            fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=folder)
            failure = None
            try:
                with os.fdopen(fd, 'wb') as stream:
                    _file(stream.fileno())
                    stream.write(encrypted)
                    stream.flush()
                    os.fsync(stream.fileno())
                if not exists:
                    marker = os.open('initialized', os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=folder)
                    try:
                        _file(marker)
                        os.fsync(marker)
                    finally:
                        os.close(marker)
                    os.fsync(folder)
                os.replace(name, 'inbox.enc', src_dir_fd=folder, dst_dir_fd=folder)
                try:
                    os.fsync(folder)
                except OSError as exc:
                    raise WriteUncertain('Inbox replacement durability uncertain') from exc
            except BaseException as exc:
                failure = exc
                raise
            finally:
                try:
                    os.unlink(name, dir_fd=folder)
                except FileNotFoundError:
                    pass
                except OSError:
                    # Cleanup cannot turn an uncertain published write into a
                    # generic failure (nor hide any earlier storage failure).
                    if failure is None:
                        raise
            return result

    def put(self, ident, text, *, now):
        return self._change(lambda state: model.put(state, ident, text, now=now))

    def dismiss(self, ident, *, now):
        return self._change(lambda state: model.dismiss(state, ident, now=now))

    def list(self):
        with self._locked() as folder:
            state, _ = self._read(folder)
            return model.list_entries(state)

    def read(self, ident):
        with self._locked() as folder:
            state, _ = self._read(folder)
            return model.read(state, ident)
