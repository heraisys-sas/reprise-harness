"""Synthetic persistence checks; no system Keychain or real inbox is used."""
import os
from pathlib import Path
import sys
import tempfile
import unittest
import uuid
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parents[1]))
import siri_inbox_store as store


def crypt(data, purpose, operation):
    assert purpose == 'siri-inbox-v1'
    key = b'fixture-key'
    if operation == 'seal':
        return b'ENC' + bytes(b ^ key[i % len(key)] for i, b in enumerate(data))
    if operation == 'open' and data.startswith(b'ENC'):
        return bytes(b ^ key[i % len(key)] for i, b in enumerate(data[3:]))
    raise ValueError('invalid ciphertext')


class InboxStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        os.chmod(self.tmp.name, 0o700)
        self.box = store.Inbox(self.tmp.name, crypt=crypt)
        self.ident = str(uuid.uuid4())

    def test_persist_list_read_dismiss_and_no_plaintext_on_disk(self):
        self.box.put(self.ident, 'demande confidentielle', now=1)
        self.assertEqual(self.box.read(self.ident)['text'], 'demande confidentielle')
        self.assertNotIn('demande confidentielle', repr(self.box.list()))
        for path in Path(self.tmp.name).rglob('*'):
            if path.is_file():
                self.assertNotIn(b'demande confidentielle', path.read_bytes())
        self.box.dismiss(self.ident, now=2)
        self.assertEqual(self.box.put(self.ident, 'demande confidentielle', now=3)['status'], 'dismissed')
        with self.assertRaises(ValueError):
            self.box.read(self.ident)

    def test_uncertain_replace_is_recovered_with_same_identity(self):
        original = os.fsync
        def fail_directory(fd):
            if os.path.isdir('/dev/fd/' + str(fd)) and (Path(self.tmp.name) / 'siri-inbox.noindex' / 'inbox.enc').exists():
                raise OSError('simulated crash window')
            return original(fd)
        with patch.object(store.os, 'fsync', side_effect=fail_directory):
            with self.assertRaises(store.WriteUncertain):
                self.box.put(self.ident, 'recover me', now=1)
        result = self.box.put(self.ident, 'recover me', now=2)
        self.assertEqual(result['status'], 'pending')
        self.assertEqual(len(self.box.list()), 1)

    def test_corrupt_and_symlinked_store_are_refused(self):
        self.box.put(self.ident, 'hello', now=1)
        path = Path(self.tmp.name) / 'siri-inbox.noindex' / 'inbox.enc'
        path.write_bytes(b'corrupt')
        with self.assertRaises(ValueError):
            self.box.list()
        path.unlink()
        path.symlink_to(Path(self.tmp.name) / 'elsewhere')
        with self.assertRaises(OSError):
            self.box.list()


    def test_cleanup_failure_preserves_uncertain_publication_and_retry_identity(self):
        original_sync = os.fsync
        def fail_published_directory(fd):
            if os.path.isdir('/dev/fd/' + str(fd)) and (Path(self.tmp.name) / 'siri-inbox.noindex' / 'inbox.enc').exists():
                raise OSError('synthetic publication sync failure')
            return original_sync(fd)
        with patch.object(store.os, 'fsync', side_effect=fail_published_directory), \
             patch.object(store.os, 'unlink', side_effect=PermissionError('synthetic cleanup failure')):
            with self.assertRaises(store.WriteUncertain) as raised:
                self.box.put(self.ident, 'same identity', now=1)
        self.assertEqual(str(raised.exception.__cause__), 'synthetic publication sync failure')
        self.assertEqual(self.box.put(self.ident, 'same identity', now=2)['status'], 'pending')
        self.assertEqual(len(self.box.list()), 1)

    def test_cleanup_failure_does_not_hide_an_earlier_storage_failure(self):
        with patch.object(store.os, 'replace', side_effect=OSError('synthetic replace failure')), \
             patch.object(store.os, 'unlink', side_effect=PermissionError('synthetic cleanup failure')):
            with self.assertRaisesRegex(OSError, 'synthetic replace failure'):
                self.box.put(self.ident, 'synthetic', now=1)


def concurrent_put(directory, ident, start, results):
    try:
        box = store.Inbox(directory, crypt=crypt)
        start.wait(10)
        results.put(box.put(ident, 'same request', now=1)['status'])
    except Exception as exc:
        results.put(type(exc).__name__)


class InboxConcurrencyTests(unittest.TestCase):
    def test_multiple_processes_preserve_single_request_identity(self):
        import multiprocessing
        context = multiprocessing.get_context('spawn')
        with tempfile.TemporaryDirectory() as folder:
            os.chmod(folder, 0o700)
            ident = str(uuid.uuid4())
            start = context.Event()
            results = context.Queue()
            workers = [context.Process(target=concurrent_put, args=(folder, ident, start, results)) for _ in range(6)]
            for worker in workers:
                worker.start()
            start.set()
            statuses = [results.get(timeout=20) for _ in workers]
            for worker in workers:
                worker.join(20)
                self.assertEqual(worker.exitcode, 0)
            self.assertEqual(statuses, ['pending'] * len(workers))
            self.assertEqual(len(store.Inbox(folder, crypt=crypt).list()), 1)

class InboxLossTests(unittest.TestCase):
    def test_deleted_registry_is_not_treated_as_empty(self):
        with tempfile.TemporaryDirectory() as folder:
            os.chmod(folder, 0o700)
            box = store.Inbox(folder, crypt=crypt)
            ident = str(uuid.uuid4())
            box.put(ident, 'request', now=1)
            box.dismiss(ident, now=2)
            (Path(folder) / 'siri-inbox.noindex' / 'inbox.enc').unlink()
            with self.assertRaises(store.MissingRegistry):
                box.put(ident, 'request', now=3)

    def test_missing_initialization_marker_fails_closed(self):
        with tempfile.TemporaryDirectory() as folder:
            os.chmod(folder, 0o700)
            box = store.Inbox(folder, crypt=crypt)
            box.put(str(uuid.uuid4()), 'request', now=1)
            (Path(folder) / 'siri-inbox.noindex' / 'initialized').unlink()
            with self.assertRaises(store.MissingRegistry):
                box.list()

    def test_nonprivate_registry_is_refused(self):
        with tempfile.TemporaryDirectory() as folder:
            os.chmod(folder, 0o700)
            box = store.Inbox(folder, crypt=crypt)
            box.put(str(uuid.uuid4()), 'request', now=1)
            os.chmod(Path(folder) / 'siri-inbox.noindex' / 'inbox.enc', 0o644)
            with self.assertRaisesRegex(ValueError, 'Private owned file'):
                box.list()

    def test_interruption_after_marker_prevents_silent_reinitialization(self):
        with tempfile.TemporaryDirectory() as folder:
            os.chmod(folder, 0o700)
            box = store.Inbox(folder, crypt=crypt)
            ident = str(uuid.uuid4())
            with patch.object(store.os, 'replace', side_effect=OSError('simulated failure')):
                with self.assertRaises(OSError):
                    box.put(ident, 'request', now=1)
            with self.assertRaises(store.MissingRegistry):
                box.put(ident, 'request', now=2)
