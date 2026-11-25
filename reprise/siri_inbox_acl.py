"""Conservative Darwin ACL inspection on existing descriptors; never changes ACLs."""
import ctypes
import errno
import os
import sys

ACL_TYPE_EXTENDED = 0x100
ACL_FIRST_ENTRY = 0


class ACLPresent(ValueError):
    pass


class ACLUnavailable(ValueError):
    pass


def _library():
    if sys.platform != 'darwin':
        raise ACLUnavailable('ACL inspection unsupported')
    try:
        library = ctypes.CDLL('/usr/lib/libSystem.B.dylib', use_errno=True)
        pointer = ctypes.c_void_p
        declarations = {
            'acl_get_fd_np': ([ctypes.c_int, ctypes.c_int], pointer),
            'acl_valid': ([pointer], ctypes.c_int),
            'acl_get_entry': ([pointer, ctypes.c_int, ctypes.POINTER(pointer)], ctypes.c_int),
            'acl_free': ([pointer], ctypes.c_int),
        }
        for name, (arguments, result) in declarations.items():
            function = getattr(library, name)
            function.argtypes, function.restype = arguments, result
        return library
    except (OSError, AttributeError) as exc:
        raise ACLUnavailable('ACL inspection unavailable') from exc


def require_no_extended_acl(fd):
    """ENOENT denotes no Darwin ACL only after validating the descriptor itself.

    A nonempty ACL is refused even when its entries only deny access. Unknown
    results fail closed. This is a point-in-time check, not a filesystem lock.
    """
    try:
        os.fstat(fd)
    except OSError as exc:
        raise ACLUnavailable('ACL descriptor unavailable') from exc
    library = _library()
    ctypes.set_errno(0)
    acl = library.acl_get_fd_np(fd, ACL_TYPE_EXTENDED)
    number = ctypes.get_errno()
    if not acl:
        if number == errno.ENOENT:
            # Detect a descriptor closed during inspection. This does not protect
            # against concurrent reuse of its numeric value; the caller owns it.
            try:
                os.fstat(fd)
            except OSError as exc:
                raise ACLUnavailable('ACL descriptor unavailable') from exc
            return
        raise ACLUnavailable('ACL retrieval failed')
    failure = None
    try:
        if library.acl_valid(acl) != 0:
            raise ACLUnavailable('ACL validation failed')
        entry = ctypes.c_void_p()
        ctypes.set_errno(0)
        result = library.acl_get_entry(acl, ACL_FIRST_ENTRY, ctypes.byref(entry))
        if result == 0:
            raise ACLPresent('Extended ACL refused')
        # On the tested Darwin ABI, absence is NULL/ENOENT above; EINVAL here
        # cannot independently distinguish an empty object from invalid input.
        raise ACLUnavailable('ACL entry inspection failed')
    except BaseException as exc:
        failure = exc
        raise
    finally:
        result = library.acl_free(acl)
        if result != 0 and failure is None:
            raise ACLUnavailable('ACL release failed')
