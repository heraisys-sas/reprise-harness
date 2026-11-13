"""One-way initialization guard for encrypted registries.

A marker cannot detect deletion of both files or replay of old ciphertext.
"""
import os


class RegistryMissing(ValueError):
    """The encrypted snapshot of a marked registry is missing."""


class LegacyRegistry(ValueError):
    """A pre-marker registry requires explicit adoption before modification."""


def status(folder, *, snapshot_exists, check_file):
    """Check the initialization marker under the caller's interprocess lock."""
    try:
        fd = os.open('initialized', os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                     dir_fd=folder)
    except FileNotFoundError:
        return 'legacy' if snapshot_exists else 'new'
    try:
        if check_file(fd).st_size != 0:
            raise ValueError('Invalid registry initialization marker')
    finally:
        os.close(fd)
    if not snapshot_exists:
        raise RegistryMissing('Initialized encrypted registry is missing')
    return 'marked'


def mark(folder, *, check_file):
    """Persist the marker before the very first ciphertext is published."""
    fd = os.open('initialized', os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                 0o600, dir_fd=folder)
    try:
        if check_file(fd).st_size != 0:
            raise ValueError('Invalid registry initialization marker')
        os.fsync(fd)
    finally:
        os.close(fd)
    os.fsync(folder)
