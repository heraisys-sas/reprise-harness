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
        # framework factice avec un slot __asar_integrity actif (digest muet)
        framework = self.app / 'Contents' / 'Frameworks' / 'Electron Framework.framework' / 'Versions' / 'A' / 'Electron Framework'
        framework.parent.mkdir(parents=True, exist_ok=True)
        framework.write_bytes(desktop_patch.DICT_DIGEST_SENTINEL
                              + bytes([1, 1]) + bytes(32))
        # rediriger les chemins réels du module vers le répertoire temporaire
        patch_dir = Path(self.tmp.name) / 'patches'
        self.mocks = [
            unittest.mock.patch.object(desktop_patch, 'PATCH_DIR', patch_dir),
            unittest.mock.patch.object(desktop_patch, 'BACKUP', patch_dir / 'app.asar.reprise-backup'),
            unittest.mock.patch.object(desktop_patch, 'PLIST_BACKUP', patch_dir / 'Info.plist.reprise-backup'),
            unittest.mock.patch.object(desktop_patch, 'SIDECAR', patch_dir / 'patch.json'),
            # codesign réel impossible sur un bundle factice : resign est vérifié hors sandbox
            unittest.mock.patch.object(desktop_patch, 'resign', lambda app: None),
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

    def test_status_orphan_socket_is_stopped(self):
        self._write_plist()
        orphan = Path(self.tmp.name) / 'orphan.sock'
        orphan.write_bytes(b'')
        with unittest.mock.patch.object(desktop_patch, 'SOCKET', orphan):
            with unittest.mock.patch.object(desktop_patch, 'desktop_running', return_value=False):
                report = desktop_patch.status(app=self.app)
        self.assertEqual(report['socket'], 'stopped')
        self.assertEqual(report['socket_path'], str(orphan))


class QtiAndInjectionTests(unittest.TestCase):
    def test_two_candidates_resolved_by_caller_signature(self):
        # QTi2 qualifie (ancre dans sa fenêtre) mais n'est jamais appelé avec ({ ;
        # QTi3 l'est → le filtre d'appel tranche en faveur de QTi3.
        body = ('function QTi2(p){requestQuickWindowDismissWithPayload(p);}; '
                'function QTi3(p){requestQuickWindowDismissWithPayload(p);}; '
                'QTi3({text:t,images:[]}); QTi2(legacy);')
        self.assertEqual(desktop_patch.resolve_qti_identifier(body), 'QTi3')

    def test_two_callers_remain_ambiguous(self):
        body = ('function A(p){requestQuickWindowDismissWithPayload(p);}; '
                'function B(p){requestQuickWindowDismissWithPayload(p);}; A({x:1}); B({y:2});')
        with self.assertRaises(PatchError):
            desktop_patch.resolve_qti_identifier(body)

    def test_zero_candidate_raises(self):
        with self.assertRaises(PatchError):
            desktop_patch.resolve_qti_identifier('function unrelated(){}')

    def test_env_override_short_circuits(self):
        with unittest.mock.patch.dict('os.environ', {'REPRISE_QTI_ID': 'CustomFn'}):
            self.assertEqual(desktop_patch.resolve_qti_identifier('function unrelated(){}'), 'CustomFn')

    def test_injection_contains_safety_markers(self):
        for marker in ("setEncoding('utf8')", 'process.umask(0o077)'):
            self.assertIn(marker, desktop_patch.INJECTION)
        # Lot #130 : le relais n'est plus un « service » ; tout passe par le daemon.
        for absent in ('createServer', 'unlinkSync', '__seen', 'reprise-dispatch-1',
                       'net.createServer'):
            self.assertNotIn(absent, desktop_patch.INJECTION)
        for present in ("role:'relay'", 'reprise-relay-1', 'relay/dispatch',
                        'setTimeout(__connect'):
            self.assertIn(present, desktop_patch.INJECTION)

    def test_injection_keeps_length_guard(self):
        # Garde QTi conservée : text.trim().length < 3 (strict, > 2 après trim).
        self.assertIn('trim().length < __MIN', desktop_patch.INJECTION)
        self.assertIn('var __MIN = 3', desktop_patch.INJECTION)


class StateAndClientTests(unittest.TestCase):
    def test_send_trigger_closes_socket_on_server_error(self):
        opened = []
        real_socket = __import__('socket').socket
        class FakeSocket(real_socket):
            def __init__(self, *a, **k):
                super().__init__(*a, **k); opened.append(self)
            def connect(self, address):
                raise OSError('connection refused')
        with unittest.mock.patch.object(desktop_patch.socket_module, 'socket', FakeSocket), \
             unittest.mock.patch.object(desktop_patch, 'SOCKET', Path('/tmp/none.sock')):
            with self.assertRaises(OSError):
                desktop_patch.send_trigger('x' * 64)
        self.assertTrue(all(s.fileno() == -1 for s in opened), 'socket non fermé')


class DistinctBundleIdTests(unittest.TestCase):
    def test_refuses_original_app(self):
        with self.assertRaises(PatchError) as caught:
            desktop_patch._apply_distinct_bundle_id(desktop_patch.APP)
        self.assertIn('origine', str(caught.exception))


if __name__ == '__main__':
    unittest.main()
