import tempfile
import unittest
import unittest.mock
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import desktop_patch
from desktop_patch import PatchError

BODY = ('function QTi(p){}; function XTi(t){QTi({text:t,images:[]});} '
        'requestQuickWindowDismissWithPayload dispatchOnQuickEntrySubmit stampSubmission '
        'activateQuickEntryWindow')


def make_tree():
    """Arbre minimal : un chunk Quick Entry, un second fichier, un dédupliqué, un unpacked."""
    blobs = {
        '.vite/build/index.chunk-BBUaTQ8d.js': BODY.encode(),
        '.vite/build/other.js': b'console.log("other")',
        'compile-cache/dup.bin': b'SAME-CONTENT',
        'compile-cache/dup2.bin': b'SAME-CONTENT',
        'unpacked-dir/native.bin': b'\x00\x01\x02',
    }
    files = {}
    for full, data in blobs.items():
        parts = full.split('/')
        node = files
        for part in parts[:-1]:
            node = node.setdefault(part, {'files': {}})['files']
        entry = {'size': len(data), 'offset': '0', 'integrity': {'algorithm': 'SHA256'}}
        if full.endswith('native.bin'):
            node[parts[-1]] = {'size': len(data), 'unpacked': True}
        else:
            node[parts[-1]] = entry
    return {'files': files}, blobs


class AsarRoundTripTests(unittest.TestCase):
    def test_round_trip_preserves_every_file_and_dedup(self):
        tree, blobs = make_tree()
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / 'app.asar'
            desktop_patch.write_asar(tree, blobs, target)
            raw, read_tree, header_size, _ = desktop_patch.read_asar_raw(target.read_bytes())
            data_start = 8 + header_size
            packed = list(desktop_patch._iter_packed(read_tree))
            self.assertEqual(len(packed), 4)
            for full, entry in packed:
                start = data_start + int(entry['offset'])
                self.assertEqual(raw[start:start + entry['size']], blobs[full], full)
            # les doublons partagent un offset, les données sont stockées une fois
            offsets = {entry['offset'] for _, entry in packed}
            by_content = {}
            for full, entry in packed:
                by_content.setdefault(blobs[full], set()).add(entry['offset'])
            self.assertEqual(len(offsets), 3)
            self.assertEqual({len(v) for v in by_content.values()}, {1})
            # l'entrée unpacked reste hors archive
            self.assertNotIn('unpacked-dir/native.bin', [full for full, _ in packed])

    def test_patched_chunk_gets_fresh_storage(self):
        tree, blobs = make_tree()
        blobs[desktop_patch.CHUNK] = (BODY + desktop_patch.MARKER).encode()
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / 'app.asar'
            desktop_patch.write_asar(tree, blobs, target)
            raw, read_tree, header_size, _ = desktop_patch.read_asar_raw(target.read_bytes())
            other = {full: entry for full, entry in desktop_patch._iter_packed(read_tree)
                     if full != desktop_patch.CHUNK}
            chunk = dict(desktop_patch._iter_packed(read_tree))[desktop_patch.CHUNK] \
                if False else [e for f, e in desktop_patch._iter_packed(read_tree) if f == desktop_patch.CHUNK][0]
            self.assertNotEqual(chunk['offset'], other['.vite/build/other.js']['offset'])


class PatchRestoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.app = Path(self.tmp.name) / 'Claude.app'
        (self.app / 'Contents' / 'Resources').mkdir(parents=True)
        tree, blobs = make_tree()
        desktop_patch.write_asar(tree, blobs, self.app / 'Contents' / 'Resources' / 'app.asar')
        # rediriger les chemins réels du module vers le répertoire temporaire
        patch_dir = Path(self.tmp.name) / 'patches'
        self.mocks = [
            unittest.mock.patch.object(desktop_patch, 'PATCH_DIR', patch_dir),
            unittest.mock.patch.object(desktop_patch, 'BACKUP', patch_dir / 'app.asar.reprise-backup'),
            unittest.mock.patch.object(desktop_patch, 'PLIST_BACKUP', patch_dir / 'Info.plist.reprise-backup'),
            unittest.mock.patch.object(desktop_patch, 'SIDECAR', patch_dir / 'patch.json'),
        ]
        for m in self.mocks:
            m.start()
        self.addCleanup(self.tmp.cleanup)
        for m in self.mocks:
            self.addCleanup(m.stop)

    def _write_plist(self):
        import plistlib
        plist = {'CFBundleShortVersionString': '9.9.9',
                 'ElectronAsarIntegrity': {'Resources/app.asar': {
                     'algorithm': 'SHA256', 'hash': 'x'}}}
        with open(self.app / 'Contents' / 'Info.plist', 'wb') as handle:
            plistlib.dump(plist, handle)

    def test_patch_then_restore(self):
        self._write_plist()
        with unittest.mock.patch.object(desktop_patch, 'desktop_running', return_value=False):
            receipt = desktop_patch.patch(app=self.app)
            self.assertEqual(receipt['status'], 'patched')
        self.assertEqual(receipt['status'], 'patched')
        asar = self.app / 'Contents' / 'Resources' / 'app.asar'
        raw, tree, header_size, _ = desktop_patch.read_asar_raw(asar.read_bytes())
        entry = [e for f, e in desktop_patch._iter_packed(tree) if f == desktop_patch.CHUNK][0]
        chunk = desktop_patch._chunk_text(raw, header_size, entry)
        self.assertIn(desktop_patch.MARKER, chunk)
        self.assertIn('require(\'net\')', chunk)
        desktop_patch.verify_patch(app=self.app)
        with unittest.mock.patch.object(desktop_patch, 'desktop_running', return_value=False):
            desktop_patch.restore(app=self.app)
        raw, tree, header_size, _ = desktop_patch.read_asar_raw(asar.read_bytes())
        entry = [e for f, e in desktop_patch._iter_packed(tree) if f == desktop_patch.CHUNK][0]
        self.assertNotIn(desktop_patch.MARKER, desktop_patch._chunk_text(raw, header_size, entry))
        self.assertFalse(desktop_patch.BACKUP.exists())

    def test_patch_refuses_when_anchors_missing(self):
        self._write_plist()
        tree, blobs = make_tree()
        blobs[desktop_patch.CHUNK] = b'function unrelated(){}'
        asar = self.app / 'Contents' / 'Resources' / 'app.asar'
        with tempfile.TemporaryDirectory() as tmp:
            desktop_patch.write_asar(tree, blobs, Path(tmp) / 'x.asar')
            asar.write_bytes((Path(tmp) / 'x.asar').read_bytes())
        with unittest.mock.patch.object(desktop_patch, 'desktop_running', return_value=False):
            with self.assertRaises(PatchError) as caught:
                desktop_patch.patch(app=self.app)
        self.assertIn('Ancrages introuvables', str(caught.exception))

    def test_patch_refuses_while_desktop_runs(self):
        self._write_plist()
        with unittest.mock.patch.object(desktop_patch, 'desktop_running', return_value=True):
            with self.assertRaises(PatchError) as caught:
                desktop_patch.patch(app=self.app)
        self.assertIn('quitter', str(caught.exception))


if __name__ == '__main__':
    unittest.main()
