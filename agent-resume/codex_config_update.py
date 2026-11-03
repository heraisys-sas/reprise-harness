"""Explicit Codex MCP release update using the native app-server config CAS.

This module does not reload clients or dispatch inference. It only accepts a
verified, already staged Reprise release and existing recognized entries.
"""
import hashlib
import json
import os
from pathlib import Path
import re
import selectors
import subprocess
import time
import tomllib
import uuid

import harness_integration as integration

class NativeClient:
    """Dedicated stdio app-server for config operations only; no reload or turns."""
    def __init__(self, program, codex_home, timeout=15):
        home = _safe(codex_home)
        program = _safe(program)
        if not program.is_file() or not os.access(program, os.X_OK):
            raise ValueError('Exécutable Codex absent ou non exécutable.')
        env = {key: val for key, val in os.environ.items()
               if not key.startswith(('PYTHON', 'DYLD_'))}
        env['CODEX_HOME'] = str(home)
        self.process = subprocess.Popen([str(program), 'app-server'], stdin=subprocess.PIPE,
                                        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, env=env)
        self.selector = selectors.DefaultSelector()
        self.selector.register(self.process.stdout, selectors.EVENT_READ)
        self.buffer = bytearray()
        self.counter, self.timeout = 0, timeout
        try:
            self.call('initialize', {'clientInfo': {'name': 'reprise-config-update', 'version': '1'},
                                     'capabilities': {}})
            self._send({'method': 'initialized'})
        except BaseException:
            self.close()
            raise

    def _send(self, value):
        self.process.stdin.write(_json(value) + b'\n')
        self.process.stdin.flush()

    def call(self, method, params):
        if method not in ('initialize', 'config/read', 'config/batchWrite'):
            raise ValueError('Méthode native hors périmètre.')
        self.counter += 1
        ident = self.counter
        self._send({'id': ident, 'method': method, 'params': params})
        deadline = time.monotonic() + self.timeout
        while time.monotonic() < deadline:
            while b'\n' in self.buffer:
                line, _, remainder = self.buffer.partition(b'\n')
                self.buffer = bytearray(remainder)
                message = json.loads(line)
                if not isinstance(message, dict):
                    raise ValueError('Réponse native invalide.')
                if message.get('id') != ident or 'method' in message:
                    continue
                if 'error' in message:
                    error = message['error']
                    if not isinstance(error, dict) or not isinstance(error.get('data') or {}, dict):
                        raise ValueError('Erreur native invalide.')
                    code = (error.get('data') or {}).get('config_write_error_code', 'native_error')
                    raise ValueError('Écriture native refusée : ' + str(code))
                result = message.get('result')
                if not isinstance(result, dict):
                    raise ValueError('Résultat natif invalide.')
                return result
            ready = self.selector.select(max(0, deadline - time.monotonic()))
            if not ready:
                break
            chunk = os.read(self.process.stdout.fileno(), 65536)
            if not chunk:
                raise EOFError('App-server fermé avant réponse.')
            self.buffer.extend(chunk)
            if len(self.buffer) > 4 * 1024 * 1024:
                raise ValueError('Réponse native trop volumineuse.')
        raise TimeoutError('Réponse native non confirmée.')

    def close(self):
        self.selector.close()
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=3)
        for pipe in (self.process.stdin, self.process.stdout):
            if pipe:
                pipe.close()


ENTRIES = {'reprise_codex': 'codex_expert_mcp.py', 'reprise-harness': 'claude_mcp.py'}


def _digest(value):
    return hashlib.sha256(value).hexdigest()


def _json(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()


def _safe(path):
    path = Path(path).expanduser()
    if not path.is_absolute() or '..' in path.parts:
        raise ValueError('Chemin absolu sans parent relatif requis.')
    for candidate in (path, *path.parents):
        if candidate.is_symlink():
            raise ValueError('Chemin symbolique refusé.')
    return path


def _read(path):
    path = _safe(path)
    if not path.is_file():
        raise ValueError('Configuration absente ou non régulière.')
    raw = path.read_bytes()
    return raw, tomllib.loads(raw.decode('utf-8'))


def _release(path, release_base):
    path, base = _safe(path), _safe(release_base)
    if path.parent != base or not re.fullmatch(r'[a-f0-9]{64}', path.name):
        raise ValueError('Release hors du registre autorisé.')
    integration.release_check(path)
    helpers = path / 'Contents/Resources/helpers'
    if integration.bundle_info(helpers)[2] != path.name:
        raise ValueError('Identité de release invalide.')
    return path


def _rewrite(entry, script, release_base, target):
    if not isinstance(entry, dict) or not isinstance(entry.get('command'), str) or not isinstance(entry.get('args'), list):
        raise ValueError('Entrée MCP non reconnue.')
    args = entry['args']
    if len(args) < 4 or args[:3] != ['-E', '-s', '-B'] or not all(isinstance(x, str) for x in args):
        raise ValueError('Arguments MCP non reconnus.')
    command, helper = _safe(entry['command']), _safe(args[3])
    suffix = Path('Contents/Resources/helpers') / script
    if not str(helper).endswith('/' + str(suffix)):
        raise ValueError('Helper MCP étranger.')
    old = helper.parents[3]
    _release(old, release_base)
    if command != old / 'Contents/Resources/runtime/bin/python3':
        raise ValueError('Runtime MCP étranger.')
    # The helper flags and their values, including scopes, remain byte-for-byte
    # equal as JSON values. Reject a second script/executable or malformed flags.
    options = {
        'codex_expert_mcp.py': ({'--project-root', '--socket', '--state', '--tools', '--daily-limit',
                                  '--claude-program', '--desktop-socket', '--ironclaw-config'},
                                 {'--claude-included-quota-confirmed', '--codex-workspace-write'},
                                 {'--project-root', '--state'}),
        'claude_mcp.py': ({'--project-root', '--state', '--expert-state', '--socket', '--max-wait',
                            '--progress-interval'}, {'--enable-await'},
                           {'--project-root', '--state', '--expert-state'}),
    }
    valued, switches, required = options[script]
    seen = set()
    remainder = iter(args[4:])
    for option in remainder:
        if option in seen or option not in valued | switches:
            raise ValueError('Options MCP ambiguës ou non reconnues.')
        seen.add(option)
        if option in valued:
            value = next(remainder, None)
            if not value or value.startswith('--'):
                raise ValueError('Valeur MCP absente ou ambiguë.')
    if not required <= seen:
        raise ValueError('Périmètre MCP insuffisamment explicite.')
    updated = dict(entry)
    updated['command'] = str(target / 'Contents/Resources/runtime/bin/python3')
    updated['args'] = args[:3] + [str(target / suffix)] + args[4:]
    return updated


def _layer(client, config):
    result = client.call('config/read', {'includeLayers': True})
    candidates = [x for x in result.get('layers') or [] if x.get('name', {}).get('type') == 'user'
                  and not x['name'].get('profile') and Path(x['name'].get('file', '')).resolve() == config.resolve()]
    if len(candidates) != 1 or not isinstance(candidates[0].get('version'), str):
        raise ValueError('Version native de la configuration utilisateur non confirmée.')
    return candidates[0]


def plan(client, config, release_base, target, names, *, source=None, ident=None):
    config, base = _safe(config), _safe(release_base)
    target = _safe(target)
    if source is None:
        target = _release(target, base)
    else:
        source = _safe(source)
        helpers, _, identity = integration.bundle_info(source)
        if helpers != source or target != base / identity:
            raise ValueError('Identité du bundle incompatible avec la cible.')
    if not names or len(set(names)) != len(names) or set(names) - ENTRIES.keys():
        raise ValueError('Sélection explicite et unique des entrées Reprise requise.')
    raw, data = _read(config)
    layer = _layer(client, config)
    if layer.get('config') != data:
        raise ValueError('La configuration native diffère du fichier lu.')
    servers = data.get('mcp_servers', {})
    if not isinstance(servers, dict):
        raise ValueError('Serveurs MCP invalides.')
    before, after = {}, {}
    for name in names:
        if name not in servers:
            raise ValueError('Entrée MCP sélectionnée absente.')
        before[name] = servers[name]
        after[name] = _rewrite(servers[name], ENTRIES[name], base, target)
    value = {'format': 1, 'id': str(uuid.UUID(ident)) if ident else str(uuid.uuid4()), 'config': str(config), 'release_base': str(base),
             'target': str(target), 'source': str(source) if source is not None else None, 'names': list(names), 'version': layer['version'],
             'before_sha256': _digest(raw), 'before': before, 'after': after}
    value['fingerprint'] = _digest(_json(value))
    return value


def _write_new(path, data):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
    finally:
        pass


def _mkdir_durable(path):
    path = _safe(path)
    if path.parent != path:
        _mkdir_durable(path.parent)
    if path.exists():
        if not path.is_dir():
            raise ValueError('Répertoire de sauvegarde invalide.')
    else:
        path.mkdir(mode=0o700)
    # Existing ancestors must also be synchronized: a prior attempt might have
    # created a directory but failed before its entry reached durable storage.
    _sync_dir(path)
    if path.parent != path:
        _sync_dir(path.parent)


def _sync_dir(path):
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _sync_release(release):
    # Persist file contents and directory entries before exposing paths in config.
    for path in release.rglob('*'):
        if path.is_symlink() or not path.is_file():
            continue
        fd = os.open(path, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    directories = [p for p in release.rglob('*') if p.is_dir() and not p.is_symlink()]
    for directory in sorted(directories, key=lambda p: len(p.parts), reverse=True):
        _sync_dir(directory)
    _sync_dir(release)
    _sync_dir(release.parent)


def apply(client, value, operations):
    value = dict(value)
    fingerprint = value.pop('fingerprint', None)
    if fingerprint != _digest(_json(value)) or value.get('format') != 1:
        raise ValueError('Plan modifié ou invalide.')
    ident = str(uuid.UUID(value['id']))
    if ident != value['id']:
        raise ValueError('Identifiant de plan invalide.')
    config, base = _safe(value['config']), _safe(value['release_base'])
    target = _safe(value['target'])
    source = value.get('source')
    if source is None:
        _release(target, base)
    else:
        source = _safe(source)
        helpers, _, identity = integration.bundle_info(source)
        if helpers != source or target != base / identity:
            raise ValueError('Bundle modifié depuis le plan.')
    raw, data = _read(config)
    layer = _layer(client, config)
    if _digest(raw) != value['before_sha256'] or layer['version'] != value['version'] or layer.get('config') != data:
        raise ValueError('Configuration modifiée depuis le plan.')
    names = value['names']
    if not names or len(set(names)) != len(names) or set(names) - ENTRIES.keys():
        raise ValueError('Sélection invalide.')
    servers = data.get('mcp_servers', {})
    before = {name: servers[name] for name in names}
    after = {name: _rewrite(before[name], ENTRIES[name], base, target) for name in names}
    if before != value['before'] or after != value['after']:
        raise ValueError('Plan non conforme aux entrées existantes.')
    operations = _safe(operations)
    _mkdir_durable(operations)
    folder = operations / ident
    folder.mkdir(mode=0o700)  # Existing operation is never replayed automatically.
    _sync_dir(operations)
    backup = folder / 'config-before.toml'
    _write_new(backup, raw)
    if _digest(backup.read_bytes()) != value['before_sha256']:
        raise ValueError('Sauvegarde non vérifiée ; aucune écriture native.')
    _write_new(folder / 'plan.json', _json(dict(value, fingerprint=fingerprint)))
    _write_new(folder / 'dispatch-intent.json', _json({'status': 'indeterminate', 'expectedVersion': value['version']}))
    _sync_dir(folder)
    if source is not None:
        helpers, runtime, identity = integration.bundle_info(source)
        if helpers != source or target != base / identity:
            raise ValueError('Bundle modifié avant staging.')
        _mkdir_durable(base)
        integration.stage_release(helpers, runtime, target)
        _release(target, base)
        _sync_release(target)
        _release(target, base)
    edits = []
    for name in names:
        # Only the two paths are edited, preserving all other values.
        for key in ('command', 'args'):
            edits.append({'keyPath': f'mcp_servers.{name}.{key}', 'value': after[name][key], 'mergeStrategy': 'replace'})
    result = client.call('config/batchWrite', {'filePath': str(config), 'expectedVersion': value['version'],
                                              'reloadUserConfig': False, 'edits': edits})
    if not isinstance(result, dict) or result.get('status') != 'ok' or Path(result.get('filePath', '')).resolve() != config.resolve():
        raise ValueError('Résultat natif non confirmé ; état indéterminé.')
    receipt = {'status': 'written', 'version': result['version'], 'plan_fingerprint': fingerprint,
               'backup_sha256': value['before_sha256'], 'client_loaded': 'unknown'}
    _write_new(folder / 'receipt.json', _json(receipt))
    _sync_dir(folder)
    return dict(receipt, operation=str(folder))


def verify(client, value):
    config = _safe(value['config'])
    _, data = _read(config)
    layer = _layer(client, config)
    servers = data.get('mcp_servers', {})
    ok = layer.get('config') == data and all(servers.get(n) == value['after'][n] for n in value['names'])
    _release(value['target'], value['release_base'])
    return {'ok': ok, 'configuration_written': ok, 'client_loaded': 'unknown'}
