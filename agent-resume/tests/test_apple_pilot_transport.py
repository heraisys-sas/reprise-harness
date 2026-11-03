import json
import errno
import os
from pathlib import Path
import subprocess
import socket
import sys
import tempfile
import threading
import time
import unittest
import uuid
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parents[1]))
import apple_pilot
import apple_pilot_transport as transport

FAKE_SERVER = r'''
import json, os, socket, sys, time
mode, path = sys.argv[1:]
if mode == 'exit': sys.exit(7)
if mode == 'never': time.sleep(20); sys.exit(0)
s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
s.bind(path); s.listen(1)
c, _ = s.accept()
if mode == 'never_read': time.sleep(20); sys.exit(0)
data = b''
while b'\r\n\r\n' not in data: data += c.recv(65536)
head, body = data.split(b'\r\n\r\n', 1)
size = int([x.split(b':',1)[1] for x in head.split(b'\r\n') if x.lower().startswith(b'content-length:')][0])
while len(body) < size: body += c.recv(65536)
if mode == 'stall': time.sleep(20); sys.exit(0)
if mode == 'partial': c.sendall(b'HTTP/1.1 200 OK\r\nContent-Length: 20\r\n\r\nshort'); c.close(); time.sleep(1); sys.exit(0)
if mode == 'malformed': c.sendall(b'HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n'); time.sleep(1); sys.exit(0)
if mode == 'status': c.sendall(b'HTTP/1.1 500 Error\r\nContent-Length: 2\r\n\r\n{}'); time.sleep(1); sys.exit(0)
if mode == 'oversize': c.sendall(b'HTTP/1.1 200 OK\r\nContent-Length: 99999999\r\n\r\n'); time.sleep(1); sys.exit(0)
if mode == 'duplicate': response = b'{"model":"system","model":"pcc","choices":[]}'
else:
 model = 'pcc' if mode == 'wrong_model' else 'system'
 finish = 'tool_calls' if mode == 'bad_finish' else 'stop'
 response = json.dumps({'model':model,'choices':[{'finish_reason':finish,'message':{'role':'assistant','content':'Quatre.'}}]}).encode()
c.sendall(b'HTTP/1.1 200 OK\r\nContent-Length: '+str(len(response)).encode()+b'\r\nConnection: close\r\n\r\n'+response)
time.sleep(1)
'''


class TransportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with tempfile.TemporaryDirectory(prefix='reprise-socket-test-', dir='/private/tmp') as directory:
            sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            try:
                sock.bind(str(Path(directory) / 'probe.sock'))
            except OSError as exc:
                if exc.errno in (errno.EPERM, errno.EACCES, errno.EAFNOSUPPORT, errno.EPROTONOSUPPORT):
                    raise unittest.SkipTest('Unix socket bind unavailable in this environment') from exc
                raise
            finally:
                sock.close()

    def setUp(self):
        self.processes = []
        self.paths = []

    def spawn(self, mode):
        def launch(command, directory):
            self.assertEqual(command[:3], [transport.FM, 'serve', '--socket'])
            self.paths.append((directory, command[3]))
            process = subprocess.Popen([sys.executable, '-c', FAKE_SERVER, mode, command[3]],
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                start_new_session=True)
            self.processes.append(process)
            return process
        return launch

    def backend(self, mode, **options):
        return transport.LocalFM(spawn=self.spawn(mode), **options)

    def request(self, backend, timeout=3, cancelled=lambda: False, messages=None, tools=None):
        return backend.respond(messages or [{'role':'user','content':'secret synthétique'}],
            apple_pilot.TOOLS if tools is None else tools, time.monotonic()+timeout, cancelled)

    def tearDown(self):
        for process in self.processes:
            if process.poll() is None:
                process.kill(); process.wait(timeout=5)
                self.fail('Transport left a child process running')
        for directory, _ in self.paths:
            self.assertFalse(os.path.exists(directory), 'Private socket directory persisted')

    def test_valid_response_peer_pid_and_cleanup(self):
        self.assertEqual(self.request(self.backend('ok'))['content'], 'Quatre.')
        self.assertIsNotNone(self.processes[0].returncode)

    def test_untrusted_peer_pid_fails_before_request(self):
        backend = self.backend('ok', peer_pid=lambda sock: -1)
        with self.assertRaises(transport.TransportError) as caught:
            self.request(backend)
        self.assertEqual(caught.exception.code, 'peer_unverified')

    def test_peer_identity_unavailable_fails_closed(self):
        def unavailable(sock): raise OSError('private error')
        with self.assertRaises(transport.TransportError) as caught:
            self.request(self.backend('ok', peer_pid=unavailable))
        self.assertEqual(str(caught.exception), 'peer_unverified')

    def test_errors_never_include_request_or_raw_response(self):
        expected = {'partial':'incomplete_response', 'malformed':'invalid_response',
                    'status':'http_error', 'oversize':'invalid_response', 'duplicate':'invalid_response',
                    'wrong_model':'invalid_response', 'bad_finish':'invalid_response'}
        for mode, code in expected.items():
            with self.subTest(mode=mode), self.assertRaises(transport.TransportError) as caught:
                self.request(self.backend(mode))
            self.assertEqual(caught.exception.code, code)
            self.assertNotIn('secret synthétique', str(caught.exception))

    def test_stalled_start_and_response_hit_total_deadline_and_cleanup(self):
        for mode in ('never', 'stall'):
            with self.subTest(mode=mode):
                start = time.monotonic()
                with self.assertRaises(transport.TransportError) as caught:
                    self.request(self.backend(mode), timeout=0.25)
                self.assertEqual(caught.exception.code, 'deadline_exceeded')
                self.assertLess(time.monotonic()-start, 3)

    def test_cancellation_during_response_and_before_spawn(self):
        start = time.monotonic()
        with self.assertRaises(transport.TransportError) as caught:
            self.request(self.backend('stall'), timeout=3, cancelled=lambda: time.monotonic()-start > .2)
        self.assertEqual(caught.exception.code, 'cancelled')
        number = len(self.processes)
        with self.assertRaises(transport.TransportError) as caught:
            self.request(self.backend('ok'), cancelled=lambda: True)
        self.assertEqual(caught.exception.code, 'cancelled')
        self.assertEqual(len(self.processes), number)

    def test_pilot_close_interrupts_stalled_transport_and_discards_output(self):
        class Memory:
            def get_checkpoint(self, project_id, checkpoint_id):
                raise AssertionError('no tool request expected')
        backend = self.backend('stall')
        session = apple_pilot.Pilot(backend=backend, memory=Memory(), project_id=str(uuid.uuid4()),
            work_id=str(uuid.uuid4()), checkpoint_id=None, skill='checkpoint-review')
        results = []
        worker = threading.Thread(target=lambda: results.append(session.turn('Question synthétique')))
        worker.start()
        try:
            end = time.monotonic()+3
            while not self.processes and time.monotonic() < end:
                time.sleep(.01)
            self.assertTrue(self.processes)
            session.close()
        finally:
            worker.join(4)
        self.assertFalse(worker.is_alive())
        self.assertEqual(results, [{'status': 'cancelled'}])
        self.assertEqual(session.messages, [])

    def test_process_exit_and_invalid_inputs_are_fail_closed(self):
        with self.assertRaises(transport.TransportError) as caught:
            self.request(self.backend('exit'))
        self.assertEqual(caught.exception.code, 'process_exited')
        with self.assertRaises(ValueError):
            self.request(self.backend('ok'), tools=[])
        self.assertEqual(len(self.processes), 1)
        with self.assertRaises(transport.TransportError) as caught:
            self.request(self.backend('ok'), messages=[{'role':'user','content':'x'*(transport.MAX_REQUEST+1)}])
        self.assertEqual(caught.exception.code, 'request_too_large')
        self.assertEqual(len(self.processes), 1)

    def test_deadlines_reject_nan_infinity_and_excessive_duration(self):
        backend = self.backend('ok')
        for deadline in (float('nan'), float('inf'), time.monotonic() + transport.MAX_LIFETIME_SECONDS + 1, True):
            with self.subTest(deadline=deadline), self.assertRaises(ValueError):
                backend.respond([{'role':'user','content':'x'}], apple_pilot.TOOLS, deadline, lambda: False)
        self.assertFalse(self.processes)

    def test_signal_denial_is_cleanup_unconfirmed(self):
        class Process:
            pid = 12345
            def poll(self): return None
        with patch.object(transport.os, 'killpg', side_effect=PermissionError('sensitive details')):
            with self.assertRaises(transport.TransportError) as caught:
                transport._stop(Process())
        self.assertEqual(str(caught.exception), 'cleanup_unconfirmed')

    def test_already_exited_process_is_not_signalled(self):
        class Process:
            pid = 12345
            def poll(self): return 0
            def wait(self): return 0
        with patch.object(transport.os, 'killpg') as kill:
            transport._stop(Process())
            kill.assert_not_called()

    def test_every_call_uses_new_process_and_socket(self):
        backend = self.backend('ok')
        self.request(backend); self.request(backend)
        self.assertNotEqual(self.processes[0].pid, self.processes[1].pid)
        self.assertNotEqual(self.paths[0][1], self.paths[1][1])


if __name__ == '__main__':
    unittest.main()
