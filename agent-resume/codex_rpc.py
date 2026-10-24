"""Join an existing Codex owner through its private proxy; never create a daemon."""
from collections import deque
import base64
import hashlib
import json
import struct
import os
from pathlib import Path
import selectors
import subprocess
import time
from engines import Unavailable, socket_path
from runtime import codex_environment, terminate

class Closed(Unavailable):
    """Le serveur propriétaire a fermé la connexion ; la fin du tour n'est pas prouvée.

    Sous-classe d'`Unavailable` : pour tout appelant qui ignore si un tour a été envoyé, une
    fermeture reste une indisponibilité. Seul `execute()` sait la distinguer d'une incertitude.
    """

    def __init__(self, reason):
        super().__init__('needs_review', reason)


class Client:
    """Bounded RFC 6455 transport over the official local proxy."""
    LIMIT = 1024 * 1024

    def __init__(self, program, values, timeout=15):
        self.process = subprocess.Popen([program, 'app-server', 'proxy', '--sock', str(socket_path(values))],
                                        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                        env=codex_environment(), start_new_session=True)
        self.selector = selectors.DefaultSelector()
        self.selector.register(self.process.stdout, selectors.EVENT_READ)
        self.buffer, self.notifications = bytearray(), deque()
        self.ident, self.timeout = 0, timeout
        try:
            key = base64.b64encode(os.urandom(16)).decode()
            self._write(('GET /rpc HTTP/1.1\r\nHost: localhost\r\nUpgrade: websocket\r\n'
                         'Connection: Upgrade\r\nSec-WebSocket-Version: 13\r\n'
                         f'Sec-WebSocket-Key: {key}\r\n\r\n').encode())
            deadline = time.monotonic() + timeout
            while b'\r\n\r\n' not in self.buffer:
                self._read(deadline)
                if len(self.buffer) > 16384:
                    raise ValueError('Oversized WebSocket handshake')
            header, remaining = self.buffer.split(b'\r\n\r\n', 1)
            self.buffer = bytearray(remaining)
            lines = header.decode('ascii').split('\r\n')
            headers = {name.lower(): value.strip() for name, value in (line.split(':', 1) for line in lines[1:])}
            expected = base64.b64encode(hashlib.sha1((key + '258EAFA5-E914-47DA-95CA-C5AB0DC85B11').encode()).digest()).decode()
            if (lines[0].split()[1] != '101' or headers.get('sec-websocket-accept') != expected
                    or headers.get('upgrade', '').lower() != 'websocket'
                    or 'upgrade' not in headers.get('connection', '').lower().split(', ')):
                raise ValueError('Invalid WebSocket handshake')
        except BaseException:
            self.close()
            raise

    def close(self):
        self.selector.close()
        terminate(self.process, grace=1)

    def _write(self, data):
        self.process.stdin.write(data)
        self.process.stdin.flush()

    def _read(self, deadline):
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError('Réponse du serveur Codex non reçue')
            ready = self.selector.select(min(.2, remaining))
            if ready:
                chunk = os.read(ready[0][0].fileobj.fileno(), 65536)
                if not chunk:
                    raise Closed('Le serveur propriétaire a fermé la connexion.')
                self.buffer.extend(chunk)
                return

    def _take(self, count, deadline):
        while len(self.buffer) < count:
            self._read(deadline)
        value = bytes(self.buffer[:count])
        del self.buffer[:count]
        return value

    def _frame(self, payload, opcode=1):
        if len(payload) > self.LIMIT:
            raise ValueError('Oversized WebSocket request')
        mask = os.urandom(4)
        size = len(payload)
        header = bytes([128 | opcode, 128 | size]) if size < 126 else bytes([128 | opcode, 254]) + struct.pack('!H', size) if size < 65536 else bytes([128 | opcode, 255]) + struct.pack('!Q', size)
        self._write(header + mask + bytes(value ^ mask[index % 4] for index, value in enumerate(payload)))

    def send(self, message):
        self._frame(json.dumps(message).encode())

    def receive(self, timeout=None):
        deadline = time.monotonic() + (self.timeout if timeout is None else timeout)
        payload, fragmented = bytearray(), False
        while True:
            first, second = self._take(2, deadline)
            opcode, final = first & 15, bool(first & 128)
            size = second & 127
            if first & 112 or second & 128:
                raise ValueError('Unsupported WebSocket frame')
            if size == 126:
                size = struct.unpack('!H', self._take(2, deadline))[0]
            elif size == 127:
                size = struct.unpack('!Q', self._take(8, deadline))[0]
            if opcode >= 8 and (not final or size > 125):
                raise ValueError('Invalid WebSocket control frame')
            if size > self.LIMIT or len(payload) + size > self.LIMIT:
                raise ValueError('Oversized WebSocket response')
            part = self._take(size, deadline)
            if opcode == 8:
                raise Closed('Le serveur propriétaire a fermé la connexion.')
            if opcode == 9:
                self._frame(part, 10)
                continue
            if opcode == 10:
                continue
            if opcode not in (0, 1) or (opcode == 0) != fragmented:
                raise ValueError('Invalid WebSocket message')
            payload.extend(part)
            fragmented = not final
            if final:
                result = json.loads(payload)
                if not isinstance(result, dict):
                    raise ValueError('Invalid JSON-RPC response')
                return result

    def call(self, method, params=None):
        self.ident += 1
        ident = self.ident
        self.send({'id': ident, 'method': method, 'params': params or {}})
        deadline = time.monotonic() + self.timeout
        while time.monotonic() < deadline:
            message = self.receive(max(.01, deadline-time.monotonic()))
            if message.get('id') == ident and 'method' not in message:
                if 'error' in message:
                    from engine_events import failure
                    state, reason = failure(message['error'])
                    raise Unavailable(state, reason)
                return message.get('result') or {}
            self.notifications.append(message)
            if len(self.notifications) > 200:
                raise Unavailable('needs_review', 'Trop de notifications avant confirmation ; aucun nouveau tour demandé.')
        raise TimeoutError('Réponse du serveur Codex non reçue')


# States a thread may have BEFORE thread/resume: idle, not loaded by the server, or left in
# error by an interrupted turn (e.g. quota). thread/resume only attaches the session; the
# state is re-checked and must be idle before any input is sent.
PRE_RESUME_STATES = ('idle', 'notLoaded', 'systemError')


def checked_thread(client, task, resume=False, before_resume=False):
    method = 'thread/resume' if resume else 'thread/read'
    params = {'threadId': task['session'], 'excludeTurns': True} if resume else {'threadId': task['session'], 'includeTurns': False}
    thread = client.call(method, params).get('thread', {})
    if thread.get('id') != task['session'] or not thread.get('cwd') or Path(thread['cwd']).resolve() != Path(task['cwd']).resolve():
        raise Unavailable('blocked_session', 'Identité ou dossier de la session non confirmé par le serveur.')
    state = (thread.get('status') or {}).get('type')
    allowed = PRE_RESUME_STATES if before_resume and not resume else ('idle',)
    if state not in allowed:
        raise Unavailable('blocked_owner', f'État propriétaire : {state!r} ; session non disponible, aucune entrée envoyée.')
    return thread


def execute(db, task, program, values, log, automatic=False, timeout=7200):
    import run_status
    ident = task['id']
    client, turn_id, terminal = None, None, False
    dispatch_attempted = False
    run_status.save(db, ident, 'launching', 'Connexion au serveur propriétaire', started_at=time.time())
    try:
        client = Client(program, values)
        client.call('initialize', {'clientInfo': {'name': 'reprise', 'version': '0.1.0'}, 'capabilities': None})
        client.send({'method': 'initialized'})
        thread = checked_thread(client, task, before_resume=True)
        if automatic:
            account = client.call('account/read', {'refreshToken': False}).get('account') or {}
            limits = client.call('account/rateLimits/read')
            if account.get('type') != 'chatgpt' or thread.get('modelProvider') != 'openai':
                raise Unavailable('blocked_auth', 'La reprise automatique exige le compte ChatGPT et le fournisseur OpenAI existants.')
            if limits.get('ordinaryUsageAllowed') is not True:
                raise Unavailable('quota_blocked', 'Le serveur propriétaire ne confirme pas la disponibilité de l’usage inclus.')
        checked_thread(client, task, resume=True)
        checked_thread(client, task)
        # Do not change model, permissions, instructions or provider of the loaded session.
        # From this point an absent ACK cannot prove that no input was delivered.
        dispatch_attempted = True
        result = client.call('turn/start', {'threadId': task['session'], 'clientUserMessageId': ident,
                             'input': [{'type': 'text', 'text': task['prompt'], 'text_elements': []}]})
        turn_id = (result.get('turn') or {}).get('id')
        if not turn_id:
            raise Unavailable('needs_review', 'Le serveur n’a pas confirmé l’identifiant du tour ; résultat incertain.')
        run_status.save(db, ident, 'running', 'Tour confirmé par le serveur propriétaire', last_event_at=time.time())
        deadline = time.monotonic() + timeout
        with Path(log).open('w') as output:
            os.chmod(log, 0o600)
            output.write(json.dumps({'type': 'turn.started', 'turn_id': turn_id}) + '\n'); output.flush()
            while time.monotonic() < deadline:
                try:
                    message = client.notifications.popleft() if client.notifications else client.receive(max(.01, deadline-time.monotonic()))
                except TimeoutError:
                    continue
                params = message.get('params') or {}
                method = message.get('method', '')
                if params.get('threadId') != task['session']:
                    continue
                event_turn = params.get('turnId') or (params.get('turn') or {}).get('id')
                if event_turn and event_turn != turn_id:
                    continue
                if 'id' in message and method:
                    raise Unavailable('blocked_permission', 'Le moteur attend une approbation ou une réponse humaine ; reprise interrompue sans accord automatique.')
                if event_turn != turn_id:
                    continue
                if method == 'turn/completed':
                    terminal = True
                    status = (params.get('turn') or {}).get('status')
                    state = 'finished' if status == 'completed' else 'stopped' if status == 'interrupted' else 'failed'
                    reason = 'Fin de tour confirmée par le serveur ; tâche métier non validée.' if state == 'finished' else 'Le serveur signale un tour interrompu ou en échec.'
                    output.write(json.dumps({'type': 'turn.completed', 'status': status}) + '\n')
                    break
            else:
                raise TimeoutError('Délai maximal du tour atteint')
    except Closed:
        if dispatch_attempted:
            state, reason = 'needs_review', 'Connexion fermée après envoi du tour ; résultat inconnu, aucune relance automatique.'
        else:
            state, reason = 'engine_unavailable', 'Connexion fermée avant tout envoi ; aucun tour n’a été lancé.'
    except Unavailable as error:
        state, reason = error.state, error.reason
    except TimeoutError:
        state, reason = 'timed_out', 'Délai du serveur dépassé ; résultat à vérifier, aucune tentative de remplacement.'
    except (OSError, ValueError, subprocess.SubprocessError):
        state, reason = 'engine_unavailable', 'Connexion au serveur impossible ; aucun nouveau moteur lancé.'
    finally:
        if client:
            if turn_id and not terminal:
                try:
                    client.call('turn/interrupt', {'threadId': task['session'], 'turnId': turn_id})
                except (OSError, ValueError, TimeoutError, subprocess.SubprocessError):
                    pass
            try:
                client.close()
            except (OSError, ValueError, subprocess.SubprocessError):
                pass
    run_status.save(db, ident, state, reason, ended_at=time.time())
    if automatic and not dispatch_attempted and state in ('blocked_owner', 'quota_blocked', 'engine_unavailable', 'timed_out'):
        return 'ready'
    return 'finished' if state == 'finished' else 'needs_review'
