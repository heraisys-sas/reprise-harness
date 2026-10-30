"""Tests unitaires pour claude_cdp_pipe.py.

Tous les tests utilisent des mocks et le helper test_bidirectional().
Aucun Chrome réel n'est lancé.
"""
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock


class TestPipeBidirectional(unittest.TestCase):
    """Vérifier que le framing NUL fonctionne avec un enfant simulé."""

    def test_bidirectional_echo(self):
        """test_bidirectional() doit renvoyer True avec cat."""
        from claude_cdp_pipe import test_bidirectional
        self.assertTrue(test_bidirectional())

    def test_bidirectional_json_roundtrip(self):
        """Test JSON+NUL en boucle via cat."""
        rA, wA = os.pipe()
        rB, wB = os.pipe()

        proc = subprocess.Popen(
            ['cat'],
            stdin=rA, stdout=wB, stderr=subprocess.DEVNULL,
            start_new_session=True,
            close_fds=True,
        )

        os.close(rA)
        os.close(wB)

        # Envoyer plusieurs frames
        messages = [
            {"id": 1, "method": "Test.get", "params": {}},
            {"id": 2, "method": "Runtime.evaluate", "params": {"expression": "1+1"}},
        ]
        for msg in messages:
            payload = json.dumps(msg).encode() + b'\0'
            os.write(wA, payload)

            raw = bytearray()
            deadline = time.monotonic() + 2
            while b'\0' not in raw and time.monotonic() < deadline:
                try:
                    chunk = os.read(rB, 4096)
                    if not chunk:
                        break
                    raw.extend(chunk)
                except BlockingIOError:
                    time.sleep(0.05)

            frame = raw.rstrip(b'\0')
            parsed = json.loads(frame.decode('utf-8'))
            self.assertEqual(parsed, msg)

        os.close(wA)
        os.close(rB)
        proc.wait(timeout=5)


class TestTerminateChrome(unittest.TestCase):
    """Vérifier terminate_chrome sur des processus fictifs."""

    def test_terminate_none(self):
        """terminate_chrome(None) ne doit pas planter."""
        from claude_cdp_pipe import terminate_chrome
        result = terminate_chrome(None)
        self.assertIsNone(result)

    def test_terminate_sigterm_then_sigkill(self):
        """terminate_chrome envoie SIGTERM, attend borné, puis SIGKILL."""
        from claude_cdp_pipe import terminate_chrome
        mock_proc = mock.MagicMock()
        mock_proc.wait.side_effect = subprocess.TimeoutExpired(['fake'], 1)
        terminate_chrome(mock_proc, timeout=0.1)
        mock_proc.terminate.assert_called_once()
        mock_proc.kill.assert_called_once()


class TestProfileHelpers(unittest.TestCase):
    """Vérifier les helpers de profil/verrouillage."""

    def test_profile_dir_default(self):
        """_profile_dir retourne le chemin par défaut."""
        from claude_cdp_pipe import _profile_dir
        path = _profile_dir()
        self.assertIsInstance(path, Path)
        # macOS: ~/.local/share/claude-cdp/chrome
        self.assertIn('claude-cdp', path.parts)
        self.assertEqual(path.name, 'chrome')

    def test_profile_dir_env_override(self):
        """L'environnement CLAUDE_CDP_PROFILE override le chemin."""
        with mock.patch.dict(os.environ, {'CLAUDE_CDP_PROFILE': '/tmp/test-profile'}):
            from claude_cdp_pipe import _profile_dir
            path = _profile_dir()
            self.assertEqual(str(path), '/tmp/test-profile')

    def test_chrome_bin_default(self):
        """_chrome_bin retourne le chemin Google Chrome par défaut."""
        from claude_cdp_pipe import _chrome_bin
        self.assertIn('Google Chrome', _chrome_bin())

    def test_chrome_bin_env_override(self):
        """L'environnement CLAUDE_CDP_CHROME override le chemin."""
        with mock.patch.dict(os.environ, {'CLAUDE_CDP_CHROME': '/usr/bin/chromium'}):
            from claude_cdp_pipe import _chrome_bin
            self.assertEqual(_chrome_bin(), '/usr/bin/chromium')

    def test_sweep_dead_lock_no_file(self):
        """sweep_dead_lock sans fichier retourne False (rien à balayer)."""
        from claude_cdp_pipe import sweep_dead_lock
        with tempfile.TemporaryDirectory() as td:
            lock_path = Path(td) / 'test-pid'
            result = sweep_dead_lock(lock_path)
            self.assertFalse(result)

    def test_sweep_dead_lock_dead_process(self):
        """sweep_dead_lock avec PID mort supprime le fichier."""
        from claude_cdp_pipe import sweep_dead_lock
        with tempfile.TemporaryDirectory() as td:
            lock_path = Path(td) / 'test-pid'
            lock_path.write_text('99999999')  # PID inexistant
            result = sweep_dead_lock(lock_path)
            self.assertTrue(result)
            self.assertFalse(lock_path.exists())

    def test_check_profile_busy_alive(self):
        """check_profile_busy avec PID vivant retourne dict."""
        # Utiliser notre propre PID (toujours vivant)
        from claude_cdp_pipe import check_profile_busy
        with tempfile.TemporaryDirectory() as td:
            lock_path = Path(td) / 'test-pid'
            lock_path.write_text(str(os.getpid()))
            result = check_profile_busy(lock_path)
            self.assertIsNotNone(result)
            self.assertFalse(result['ok'])
            self.assertEqual(result['status'], 'profile_busy')

    def test_check_profile_busy_dead(self):
        """check_profile_busy avec PID mort retourne None."""
        from claude_cdp_pipe import check_profile_busy
        with tempfile.TemporaryDirectory() as td:
            lock_path = Path(td) / 'test-pid'
            lock_path.write_text('99999999')
            result = check_profile_busy(lock_path)
            self.assertIsNone(result)

    def test_write_lock(self):
        """write_lock écrit le PID dans le fichier."""
        from claude_cdp_pipe import write_lock
        with tempfile.TemporaryDirectory() as td:
            lock_path = Path(td) / 'test-pid'
            write_lock(lock_path, 12345)
            self.assertEqual(lock_path.read_text().strip(), '12345')


class TestLaunchChromeBlocked(unittest.TestCase):
    """Vérifier que launch_chrome échoue gracieusement sans Chrome."""

    def test_launch_chrome_missing_binary(self):
        """launch_chrome avec un bininaire inexistant lève RuntimeError."""
        from claude_cdp_pipe import launch_chrome
        with tempfile.TemporaryDirectory() as td:
            profile = Path(td) / 'profile'
            with mock.patch('claude_cdp_pipe._chrome_bin', return_value='/nonexistent/chrome'):
                with self.assertRaises(RuntimeError):
                    launch_chrome(profile)


class TestBuildChromeArgs(unittest.TestCase):
    """Vérifier les arguments Chrome générés."""

    def test_build_chrome_args(self):
        from claude_cdp_pipe import build_chrome_args
        with tempfile.TemporaryDirectory() as td:
            profile = Path(td) / 'profile'
            args = build_chrome_args(profile)
            self.assertIn('--remote-debugging-pipe', args)
            self.assertIn(f'--user-data-dir={profile}', args)
            self.assertIn('--app=https://claude.ai', args)
            self.assertIn('--disable-blink-features=AutomationControlled', args)
            self.assertIn('--no-first-run', args)
            self.assertIn('--no-default-browser-check', args)


class TestPipeTimeout(unittest.TestCase):
    """Vérifier le timeout CDP et l'état mort du transport après expiration."""

    @staticmethod
    def _close_quiet(handle):
        try:
            handle.close()
        except OSError:
            pass

    def test_call_times_out_then_transport_is_dead(self):
        """Aucune réponse : TimeoutError borné, puis RuntimeError sur réutilisation."""
        from claude_cdp_pipe import PipeTransport
        r_read, w_keep = os.pipe()   # le transport lit r_read ; w_keep reste ouvert
        r_keep, w_write = os.pipe()  # le transport écrit w_write ; r_keep reste ouvert
        self.addCleanup(os.close, w_keep)
        self.addCleanup(os.close, r_keep)
        transport = PipeTransport(mock.MagicMock(), r_read, w_write, timeout=0.5)
        self.addCleanup(self._close_quiet, transport._writer)
        self.addCleanup(self._close_quiet, transport._reader)

        start = time.monotonic()
        with self.assertRaises(TimeoutError):
            transport.call('Target.getTargets', {})
        self.assertLess(time.monotonic() - start, 5, 'timeout non appliqué')

        with self.assertRaisesRegex(RuntimeError, 'dead'):
            transport.call('Target.getTargets', {})


if __name__ == '__main__':
    unittest.main()
