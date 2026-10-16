"""Explicit CLI scope policy. This is not an MCP client authenticator."""
import hashlib
import json
import os
from pathlib import Path
import stat

LIMIT = 2 * 1024 * 1024


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()


def absolute(raw):
    path = Path(raw)
    if '..' in path.parts:
        raise ValueError('Parent traversal is not allowed')
    return Path(os.path.abspath(path))


def open_path(path, lineage=None):
    """Walk from / using directory descriptors; never follow a symbolic link."""
    fd = os.open('/', os.O_RDONLY | os.O_DIRECTORY)
    try:
        if lineage is not None:
            info = os.fstat(fd)
            lineage.append((info.st_dev, info.st_ino))
        for i, part in enumerate(path.parts[1:]):
            flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
            if i < len(path.parts)-2:
                flags |= os.O_DIRECTORY
            child = os.open(part, flags, dir_fd=fd)
            os.close(fd)
            fd = child
            if lineage is not None:
                info = os.fstat(fd)
                lineage.append((info.st_dev, info.st_ino))
        return fd
    except BaseException:
        os.close(fd)
        raise


def read_fd(fd):
    before = os.fstat(fd)
    if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or before.st_size > LIMIT:
        raise ValueError('Only single-link regular text files <= 2 MiB are allowed')
    data = bytearray()
    while len(data) <= LIMIT:
        chunk = os.read(fd, min(65536, LIMIT+1-len(data)))
        if not chunk:
            break
        data.extend(chunk)
    after = os.fstat(fd)
    if len(data) > LIMIT or (before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (after.st_size, after.st_mtime_ns, after.st_ctime_ns):
        raise ValueError('File changed or exceeded size limit during read')
    return data.decode('utf-8')


class ProjectPolicy:
    def __init__(self, path, project):
        fd = open_path(absolute(path))
        try:
            info = os.fstat(fd)
            if info.st_uid != os.getuid() or info.st_mode & 0o022:
                raise ValueError('Policy must be owned by the caller and not group/world writable')
            policy = json.loads(read_fd(fd))
        finally:
            os.close(fd)
        if policy.get('version') != 1 or not isinstance(policy.get('projects'), dict):
            raise ValueError('Expected version 1 project policy')
        if project not in policy['projects']:
            raise ValueError('Project is not authorized by this policy')
        all_roots = []
        self.roots = []
        for name, config in policy['projects'].items():
            if not isinstance(config.get('roots'), list) or not config['roots']:
                raise ValueError('Every project needs explicit roots')
            for raw in config['roots']:
                if not isinstance(raw, str) or not Path(raw).is_absolute():
                    raise ValueError('Policy roots must be absolute canonical directories')
                root = absolute(raw)
                if str(root) != raw or root == Path('/'):
                    raise ValueError('Policy root must be canonical and narrower than /')
                for other, _, _ in all_roots:
                    if root.is_relative_to(other) or other.is_relative_to(root):
                        raise ValueError('Project roots must not overlap')
                lineage = []
                fd = open_path(root, lineage)
                try:
                    info = os.fstat(fd)
                    if not stat.S_ISDIR(info.st_mode):
                        raise ValueError('Project root must be a directory')
                    identity = (info.st_dev, info.st_ino)
                    if any(identity in ancestors or other_identity in lineage
                           for _, other_identity, ancestors in all_roots):
                        raise ValueError('Physical project roots must not overlap (including filesystem aliases)')
                    if name == project:
                        self.roots.append((root, info.st_dev, info.st_ino))
                finally:
                    os.close(fd)
                all_roots.append((root, identity, lineage))
        self.project = project
        self.namespace = fingerprint({'policy': policy, 'project': project,
                                      'roots': [(str(p), dev, ino) for p, dev, ino in self.roots]})
        configured = policy['projects'][project].get('instructions')
        if not isinstance(configured, list) or not configured or any(not isinstance(p, str) or not Path(p).is_absolute() for p in configured):
            raise ValueError('Project policy must pin applicable instruction files')
        self.instructions = configured

    def read(self, raw):
        path = absolute(raw)
        for root, device, inode in self.roots:
            if not path.is_relative_to(root) or path == root:
                continue
            fd = open_path(root)
            try:
                info = os.fstat(fd)
                if (info.st_dev, info.st_ino) != (device, inode):
                    raise ValueError('Authorized root was replaced')
                parts = path.relative_to(root).parts
                for i, part in enumerate(parts):
                    flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
                    if i < len(parts)-1:
                        flags |= os.O_DIRECTORY
                    child = os.open(part, flags, dir_fd=fd)
                    os.close(fd)
                    fd = child
                    if os.fstat(fd).st_dev != device:
                        raise ValueError('Cross-device project traversal is not authorized')
                return str(path), read_fd(fd)
            finally:
                os.close(fd)
        raise ValueError('Source is outside authorized project roots')
