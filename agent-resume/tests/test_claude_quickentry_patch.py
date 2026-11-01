import json
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

TOOLS = Path(__file__).resolve().parent.parent / "tools"
sys.path.insert(0, str(TOOLS))

import claude_quickentry_patch as cqp  # noqa: E402

CHUNK = ('var a=1;' + cqp.ANCHOR + ';var b=2;')
INJECT_FILE = TOOLS / "reprise_qe_inject.js"


def build_asar_bytes(chunk=CHUNK):
    tree = {"files": {".vite": {"files": {"build": {"files": {
        "index.chunk-m4oMImJ-.js": {"size": len(chunk), "offset": "0"}},
        "other.js": {"size": 3, "offset": "0"}}}}}}
    # offsets corrects
    cqp._assign_offsets(tree, 0)
    header_json = json.dumps(tree, sort_keys=True).encode()
    off = {}
    for rel, entry in cqp.iter_files(tree):
        off[rel] = int(entry["offset"])
    blobs = [chunk.encode()[0:0]]  # placeholder
    data = bytearray()
    for rel, entry in cqp.iter_files(tree):
        size = int(entry["size"])
        if rel == cqp.CHUNK_PATH:
            data += chunk.encode()
        else:
            data += b"abc"
    head = struct.pack("<4I", 4, len(header_json) + 8, len(header_json) + 4,
                       len(header_json))
    return head + header_json + bytes(data), header_json


def make_app(tmp, chunk=CHUNK):
    asar_bytes, header_json = build_asar_bytes(chunk)
    app = Path(tmp) / "Claude-Reprise.app"
    res = app / "Contents" / "Resources"
    res.mkdir(parents=True)
    (asar_path := res / "app.asar").write_bytes(asar_bytes)
    with open(app / "Contents" / "Info.plist", "wb") as f:
        import plistlib
        plistlib.dump({"ElectronAsarIntegrity": {
            "Resources/app.asar": {"hash": cqp.asar_hash(asar_bytes),
                                   "algorithm": "SHA256"}}}, f)
    return app, asar_path


class TestQuickEntryPatch(unittest.TestCase):
    def test_anchor_found_and_inject_once(self):
        new_chunk = cqp.inject_after_anchor(CHUNK, cqp.load_payload(INJECT_FILE))
        self.assertEqual(new_chunk.count(cqp.PAYLOAD_MARKER), 1)
        self.assertEqual(new_chunk.count(cqp.ANCHOR), 1)
        self.assertEqual(new_chunk.count(cqp.SOCKET_SENTINEL), 1)
        i = new_chunk.index(cqp.ANCHOR) + len(cqp.ANCHOR)
        self.assertTrue(new_chunk[i:].startswith(";"))
        self.assertIn(cqp.PAYLOAD_MARKER, new_chunk[i:i + 40])

    def test_refuse_missing_anchor(self):
        with self.assertRaises(cqp.PatchError):
            cqp.inject_after_anchor("no anchor here", "x")

    def test_refuse_double_anchor(self):
        with self.assertRaises(cqp.PatchError):
            cqp.inject_after_anchor(cqp.ANCHOR + cqp.ANCHOR, "x")

    def test_dry_run_writes_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            app, asar = make_app(tmp)
            before = asar.read_bytes()
            rc = cqp.main([str(app)])
            self.assertEqual(rc, 0)
            self.assertEqual(asar.read_bytes(), before)
            self.assertFalse((asar.parent / "app.asar.orig").exists())

    def test_hash_uses_w3(self):
        asar_bytes, _ = build_asar_bytes()
        w = struct.unpack("<4I", asar_bytes[:16])
        import hashlib
        self.assertEqual(cqp.asar_hash(asar_bytes),
                         hashlib.sha256(asar_bytes[16:16 + w[3]]).hexdigest())
        self.assertNotEqual(cqp.asar_hash(asar_bytes),
                            hashlib.sha256(asar_bytes[16:16 + w[0]]).hexdigest()
                            if w[0] != w[3] else "")

    def test_apply_roundtrip_and_check(self):
        with tempfile.TemporaryDirectory() as tmp:
            app, asar = make_app(tmp)
            self.assertEqual(cqp.main([str(app), "--apply"]), 0)
            orig = (asar.parent / "app.asar.orig").read_bytes()
            self.assertEqual(orig, build_asar_bytes()[0])  # backup = original
            # re-parse : le chunk contient le payload
            data, header_json = cqp.read_asar(asar)
            chunk = cqp.chunk_bytes(data, header_json).decode()
            self.assertIn(cqp.PAYLOAD_MARKER, chunk)
            self.assertEqual(chunk.count(cqp.PAYLOAD_MARKER), 1)
            self.assertIn(cqp.SOCKET_SENTINEL, chunk)
            # hash Info.plist coherent
            self.assertEqual(cqp.read_plist_hash(app), cqp.asar_hash(data))
            # --check passe
            self.assertEqual(cqp.main([str(app), "--check"]), 0)
            # re-patch refuse
            self.assertEqual(cqp.main([str(app), "--apply"]), 2)

    def test_codesign_mocked(self):
        with tempfile.TemporaryDirectory() as tmp:
            app, asar = make_app(tmp)
            calls = []

            def fake_run(cmd, capture_output, text):
                calls.append(cmd[0])
                r = subprocess.CompletedProcess(cmd, 0, "", "")
                return r

            orig = cqp.subprocess.run
            cqp.subprocess.run = fake_run
            cqp.shutil.which = lambda n: "/usr/bin/codesign"
            try:
                self.assertEqual(cqp.main([str(app), "--apply"]), 0)
            finally:
                cqp.subprocess.run = orig
                cqp.shutil.which = shutil.which
            self.assertEqual(calls.count("/usr/bin/codesign"), 2)


if __name__ == "__main__":
    unittest.main()
