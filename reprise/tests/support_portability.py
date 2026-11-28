"""Sauts par capacité (#276) : un test qui exige un outil, un chemin ou une API absente
de la machine est sauté avec la raison, jamais compté en échec."""
from pathlib import Path
import shutil
import subprocess
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def requires_tools(*names):
    missing = [name for name in names if shutil.which(name) is None]
    return unittest.skipIf(missing, 'outil absent : ' + ', '.join(missing))


def _apple_sdk_available():
    """swiftc seul ne suffit pas : Swift pour Linux n'a ni Foundation Apple ni AppKit."""
    xcrun = shutil.which('xcrun')
    return bool(xcrun) and subprocess.run([xcrun, '--sdk', 'macosx', '--show-sdk-path'],
                                          capture_output=True).returncode == 0


requires_apple_sdk = unittest.skipUnless(_apple_sdk_available(), 'SDK Apple (xcrun macosx) indisponible')


def requires_directory(path):
    return unittest.skipUnless(Path(path).is_dir(), f'répertoire absent : {path}')


def _acl_available():
    import siri_inbox_acl
    try:
        siri_inbox_acl._library()
    except siri_inbox_acl.ACLUnavailable:
        return False
    return True


requires_acl = unittest.skipUnless(_acl_available(), 'inspection ACL indisponible')
