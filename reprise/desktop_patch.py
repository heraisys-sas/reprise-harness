"""Patch de l'installation Claude Desktop : socket local de déclenchement (prototype #124).

Autorisation opérateur explicite (08/11). Le patch injecte un listener socket Unix
dans le main process de l'app — via le pipeline natif Quick Entry (`QTi`) — pour que
Reprise déclenche l'exécuteur sans intervention.

Rails : backup pristine par version + SHA256 d'origine, restauration vérifiée,
gardes d'ancrage (version incompatible = abort propre), idempotent (marqueur
REPRISE-TRIGGER), Desktop quitté requis, intégrité asar recalculée (par fichier +
ElectronAsarIntegrity dans Info.plist — définition du hash déterminée empiriquement :
sha256 des octets JSON d'en-tête). Le patch est réappliqué après chaque mise à jour
Desktop (Squirrel écrase l'asar).

REX 08/11 : le patch de l'app ORIGINALE est une impasse prouvée sur macOS Sequoia —
le fuse ElectronAsarIntegrity exige une signature valide couvrant plist+asar, et
toute re-signature (ad-hoc) est tuée à l'exec par les launch constraints épinglées
sur l'identité Anthropic d'origine (SIGKILL silencieux). L'outil cible donc une
COPIE de l'app (nouvelle identité de bundle, premier lancement épinglé sur sa
propre signature) via le paramètre app.
"""
import hashlib
import json
import os
import plistlib
import re as re_module
import shutil
import socket as socket_module
import struct
import subprocess
import time
from pathlib import Path

APP = Path('/Applications/Claude.app')
ASAR = APP / 'Contents' / 'Resources' / 'app.asar'
PLIST = APP / 'Contents' / 'Info.plist'
PATCH_DIR = Path.home() / 'Library' / 'Application Support' / 'Reprise' / 'patches' / 'claude'
BACKUP = PATCH_DIR / 'app.asar.reprise-backup'
PLIST_BACKUP = PATCH_DIR / 'Info.plist.reprise-backup'
SIDECAR = PATCH_DIR / 'patch.json'
MARKER = '/* REPRISE-TRIGGER v1 (#124) */'
CHUNK = '.vite/build/index.chunk-BBUaTQ8d.js'
ANCHORS = ('requestQuickWindowDismissWithPayload', 'dispatchOnQuickEntrySubmit',
           'stampSubmission', 'activateQuickEntryWindow')
SOCKET = Path.home() / '.claude-desktop' / 'quick-entry-reprise.sock'
TRIGGER_MIN_CHARS = 3  # garde QTi : text.trim().length > 2 (strict)
BLOCK_SIZE = 4194304
FRAMEWORK = APP / 'Contents' / 'Frameworks' / 'Electron Framework.framework' / 'Versions' / 'A' / 'Electron Framework'
DICT_DIGEST_SENTINEL = b'AGbevlPCksUGKNL8TSn7wGmJEuJsXb2A'  # 32 octets
SLOT_USED_OFFSET = 32
SLOT_VERSION_OFFSET = 33
SLOT_DIGEST_OFFSET = 34
SLOT_DIGEST_SIZE = 32

INJECTION = """
;
;(function(){
  try {
    var __net = require('net'), __fs = require('fs'), __os = require('os');
    var __p = __os.homedir() + '/.claude-desktop/quick-entry-reprise.sock';
    try { __fs.unlinkSync(__p); } catch (e) {}
    var __seen = {};
    function __handle(obj, c) {
      var isRpc = obj && typeof obj === 'object' && typeof obj.method === 'string';
      if (isRpc) {
        var id = obj.id === undefined ? null : obj.id;
        function reply(result) { try { c.write(JSON.stringify({jsonrpc:'2.0', id:id, result:result}) + '\\n'); } catch (e2) {} }
        function fail(code, message) { try { c.write(JSON.stringify({jsonrpc:'2.0', id:id, error:{code:code, message:message}}) + '\\n'); } catch (e2) {} }
        if (obj.method === 'initialize') { reply({serverInfo:{name:'reprise-dispatch', version:'1'}}); return; }
        if (obj.method === 'quick_entry/capabilities') { reply({dispatch_supported:true, resume_validation_supported:true, protocol:'reprise-dispatch-1'}); return; }
        if (obj.method === 'quick_entry/submit') {
          var pr = obj.params || {};
          var rk = typeof pr.request_key === 'string' ? pr.request_key : null;
          var text = typeof pr.prompt === 'string' ? pr.prompt : '';
          if (!rk) { fail(-32602, 'request_key requis'); return; }
          if (__seen[rk]) { reply({dispatch_attempted:true, admission_only:false, request_key:rk, duplicate:true}); return; }
          if (text.trim().length < 3) { fail(-32602, 'prompt trop court (garde QTi : > 2 caractères après trim)'); return; }
          __seen[rk] = true;
          try { c.write(JSON.stringify({jsonrpc:'2.0', id:id, result:{dispatch_attempted:true, admission_only:false, request_key:rk}}) + '\\n'); } catch (we) {}
          setImmediate(function() {
            try { QTi({text:text, images:[], chatId: pr.chatId ? String(pr.chatId) : undefined}); }
            catch (qe) { console.error('[reprise-dispatch] QTi error:', qe && qe.message); }
          });
          return;
        }
        fail(-32601, 'méthode inconnue: ' + obj.method);
        return;
      }
      var text = obj && typeof obj.prompt === 'string' ? obj.prompt : '';
      if (text.trim().length < 3) { try { c.write(JSON.stringify({ok:false, error:'prompt trop court (garde QTi)'}) + '\\n'); c.end(); } catch (e2) {} return; }
      try { c.write(JSON.stringify({ok:true, dispatched:'queued'}) + '\\n'); c.end(); } catch (we) {}
      setImmediate(function() {
        try { QTi({text:text, images:[], chatId: obj && obj.chatId ? String(obj.chatId) : undefined}); }
        catch (qe) { console.error('[reprise-dispatch] QTi error:', qe && qe.message); }
      });
    }
    var __srv = __net.createServer(function(c) {
      try { c.write(JSON.stringify({hello:true}) + '\\n'); } catch (e0) {}
      var b = '';
      c.on('data', function(d) {
        b += d.toString('utf8');
        if (b.length > 8388608) { try { c.end(); } catch (e2) {} return; }
        var i;
        while ((i = b.indexOf('\\n')) >= 0) {
          var line = b.slice(0, i); b = b.slice(i + 1);
          if (!line.trim()) continue;
          try { __handle(JSON.parse(line), c); } catch (e) { try { c.write(JSON.stringify({ok:false, error:String(e && e.message || e)}) + '\\n'); } catch (e2) {} }
        }
      });
      c.on('error', function() {});
    });
    __srv.listen(__p, function() { try { __fs.chmodSync(__p, 384); } catch (e) {} });
  } catch (e) {}
})();
"""


class PatchError(ValueError):
    pass


def read_asar_raw(data):
    """(raw, tree, header_size, json_length) — miroir du lecteur du recensement."""
    if len(data) < 16:
        raise PatchError('Archive asar tronquée')
    size_pickle, header_size, _inner, json_length = struct.unpack('<IIII', data[:16])
    if size_pickle != 4 or json_length > header_size or header_size > 64 * 1024 * 1024:
        raise PatchError('En-tête asar inattendu')
    tree = json.loads(data[16:16 + json_length])
    return data, tree, header_size, json_length


def file_integrity(data):
    """Miroir du bloc par fichier observé dans l'en-tête installé."""
    blocks = [hashlib.sha256(data[i:i + BLOCK_SIZE]).hexdigest()
              for i in range(0, len(data), BLOCK_SIZE)] or [hashlib.sha256(b'').hexdigest()]
    return {'algorithm': 'SHA256', 'hash': hashlib.sha256(data).hexdigest(),
            'blockSize': BLOCK_SIZE, 'blocks': blocks}


def write_asar(tree, blobs, path):
    """Reconstruit l'archive : offsets/tailles/intégrité recalculés, données concaténées."""
    ordered = []
    def collect(node, prefix=''):
        for name, entry in sorted((node.get('files') or {}).items()):
            full = prefix + name
            if 'files' in entry:
                collect(entry, full + '/')
            else:
                ordered.append((full, entry))
    collect(tree)
    root = {'files': {}}
    data_parts, offset = [], 0
    seen = {}
    for full, entry in ordered:
        new_entry = dict(entry)
        if not (entry.get('unpacked') or 'offset' not in entry):
            data = blobs[full]
            key = hashlib.sha256(data).hexdigest()
            if full == CHUNK:
                key += '|patched'
            if key in seen:
                new_entry['size'] = len(data)
                new_entry['offset'] = seen[key]
                if 'integrity' in entry:
                    new_entry['integrity'] = file_integrity(data)
            else:
                new_entry['size'] = len(data)
                new_entry['offset'] = str(offset)
                if 'integrity' in entry:
                    new_entry['integrity'] = file_integrity(data)
                seen[key] = new_entry['offset']
                offset += len(data)
                data_parts.append(data)
        node = root['files']
        parts = full.split('/')
        for part in parts[:-1]:
            node = node.setdefault(part, {'files': {}})['files']
        node[parts[-1]] = new_entry
    json_bytes = json.dumps(root, separators=(',', ':')).encode()
    padded = json_bytes + b'\0' * ((-len(json_bytes)) % 4)
    inner_size = 4 + len(padded)
    header_size = 4 + inner_size
    prefix = struct.pack('<IIII', 4, header_size, inner_size, len(json_bytes))
    with open(path, 'wb') as handle:
        handle.write(prefix + padded)
        for part in data_parts:
            handle.write(part)
    return json_bytes


def _chunk_entry(tree):
    entry = tree
    for part in CHUNK.split('/'):
        entry = (entry.get('files') or {}).get(part)
        if entry is None:
            raise PatchError('Chunk Quick Entry introuvable dans l archive asar.')
    return entry


def _chunk_text(raw, header_size, entry):
    start = 8 + header_size + int(entry['offset'])
    return raw[start:start + entry['size']].decode('utf-8', 'replace')


def desktop_running(app=APP):
    """L'app CIBLE tourne-t-elle ? (matching par chemin de binaire, pas par nom)."""
    probe = subprocess.run(['pgrep', '-f', str(Path(app) / 'Contents' / 'MacOS')], capture_output=True)
    return probe.returncode == 0


def verify_anchors(chunk_text):
    missing = [anchor for anchor in ANCHORS if anchor not in chunk_text]
    if missing:
        raise PatchError('Ancrages introuvables dans le chunk installé (' + ', '.join(missing) +
                         ') — version Desktop incompatible ; patch refusé.')
    if MARKER in chunk_text:
        raise PatchError('Le patch REPRISE-TRIGGER est déjà appliqué.')


def _plist_asar_hash(plist_path, json_bytes):
    with open(plist_path, 'rb') as handle:
        plist = plistlib.load(handle)
    integrity = plist.get('ElectronAsarIntegrity') or {}
    entry = integrity.get('Resources/app.asar') or {}
    if entry.get('algorithm') != 'SHA256' or not entry.get('hash'):
        raise PatchError('ElectronAsarIntegrity absent du Info.plist — format inattendu.')
    entry['hash'] = hashlib.sha256(json_bytes).hexdigest()
    plist['ElectronAsarIntegrity'] = integrity
    return plist, entry['hash']


def _paths(app):
    """Chemins de backup/sidecar : constantes pour l'app d'origine, répertoire dérivé pour une copie."""
    app = Path(app)
    if app == APP:
        return {'backup': BACKUP, 'plist_backup': PLIST_BACKUP, 'sidecar': SIDECAR}
    tag = re_module.sub(r'[^A-Za-z0-9_.-]+', '_', str(app))[-60:]
    d = PATCH_DIR / 'copies' / tag
    return {'backup': d / 'app.asar.reprise-backup',
            'plist_backup': d / 'Info.plist.reprise-backup',
            'sidecar': d / 'patch.json'}


def integrity_dict_digest(integrity_dict):
    """sha256 du dictionnaire ElectronAsarIntegrity : clés triées, key+algorithm+hash concaténés.

    Réplique exacte de IsIntegrityDictionaryValid (Electron 44, integrity_digest.mm).
    """
    hasher = hashlib.sha256()
    for key in sorted(integrity_dict.keys()):
        hasher.update(key.encode())
        hasher.update(integrity_dict[key]['algorithm'].encode())
        hasher.update(integrity_dict[key]['hash'].encode())
    return hasher.hexdigest()


def _framework_path(app):
    app = Path(app)
    if app == APP:
        return FRAMEWORK
    return app / 'Contents' / 'Frameworks' / 'Electron Framework.framework' / 'Versions' / 'A' / 'Electron Framework'


def patch_integrity_slot(app, digest_hex):
    """Met à jour le digest du dictionnaire dans le slot __asar_integrity du Framework.

    Retourne (nombre de slots actifs patchés, offsets). Lève si aucun slot trouvé.
    """
    framework = _framework_path(app)
    try:
        data = bytearray(framework.read_bytes())
    except OSError as error:
        raise PatchError(f'Framework illisible: {error}') from error
    new_digest = bytes.fromhex(digest_hex)
    patched = 0
    offsets = []
    start = 0
    while True:
        idx = data.find(DICT_DIGEST_SENTINEL, start)
        if idx < 0:
            break
        used, version = data[idx + SLOT_USED_OFFSET], data[idx + SLOT_VERSION_OFFSET]
        if used in (0, 1) and version == 1:
            offsets.append(idx)
            if used == 1:
                data[idx + SLOT_DIGEST_OFFSET:idx + SLOT_DIGEST_OFFSET + SLOT_DIGEST_SIZE] = new_digest
                patched += 1
        start = idx + 1
    if not offsets:
        raise PatchError('Slot __asar_integrity introuvable dans le Framework.')
    framework.write_bytes(bytes(data))
    return patched, offsets


def resign(app):
    """Re-signature ad-hoc PROFONDE : helpers + framework + bundle externe.

    Les helpers d'origine (signature Anthropic, hardened runtime) refusent sinon
    de charger le Framework re-signé (library validation) — crash dyld au spawn.
    """
    import subprocess as sp
    result = sp.run(['codesign', '--force', '--deep', '--sign', '-', str(Path(app))],
                    capture_output=True, text=True)
    if result.returncode != 0:
        raise PatchError('codesign --deep a échoué: ' + result.stderr.strip()[:200])


def _version():
    try:
        with open(APP / 'Contents' / 'Info.plist', 'rb') as handle:
            return plistlib.load(handle).get('CFBundleShortVersionString')
    except (OSError, KeyError):
        return None


def patch(app=APP):
    asar = Path(app) / 'Contents' / 'Resources' / 'app.asar'
    plist_path = Path(app) / 'Contents' / 'Info.plist'
    paths = _paths(app)
    backup, plist_backup, sidecar_path = paths['backup'], paths['plist_backup'], paths['sidecar']
    if desktop_running(app):
        raise PatchError('Claude Desktop est lancé ; le quitter avant le patch.')
    raw, tree, header_size, json_length = read_asar_raw(asar.read_bytes())
    entry = _chunk_entry(tree)
    chunk = _chunk_text(raw, header_size, entry)
    if MARKER in chunk:
        return {'status': 'already_patched'}
    verify_anchors(chunk)
    pristine = asar.read_bytes()
    original_sha = hashlib.sha256(pristine).hexdigest()
    backup.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(asar, backup)
    plist_backup.write_bytes(plist_path.read_bytes())
    blobs = {full: raw[8 + header_size + int(item['offset']):
                       8 + header_size + int(item['offset']) + item['size']]
             for full, item in _iter_packed(tree)}
    blobs[CHUNK] = (chunk + '\n' + MARKER + '\n' + INJECTION).encode()
    patched_tree = json.loads(json.dumps(tree))
    _bump_entry(patched_tree, CHUNK, len(blobs[CHUNK]), file_integrity(blobs[CHUNK]))
    tmp = asar.parent / ('app.asar.reprise-new-' + str(os.getpid()))
    json_bytes = write_asar(patched_tree, blobs, tmp)
    plist, plist_hash = _plist_asar_hash(plist_path, json_bytes)
    os.replace(tmp, asar)
    with open(plist_path, 'wb') as handle:
        plistlib.dump(plist, handle, fmt=plistlib.FMT_BINARY)
    plist2 = plistlib.load(open(plist_path, 'rb'))
    digest_hex = integrity_dict_digest(plist2['ElectronAsarIntegrity'])
    slot_patched, slot_offsets = patch_integrity_slot(app, digest_hex)
    verify_patch(app)
    sidecar = {'patched_at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
               'version': _version(), 'original_sha256': original_sha,
               'patched_sha256': hashlib.sha256(asar.read_bytes()).hexdigest(),
               'plist_hash': plist_hash, 'chunk': CHUNK,
               'integrity_dict_digest': digest_hex,
               'slot_offsets': slot_offsets, 'slot_patched': slot_patched}
    sidecar_path.write_text(json.dumps(sidecar, indent=1))
    return {'status': 'patched', 'backup': str(backup), 'socket': str(SOCKET),
            'version': sidecar['version']}


def _iter_packed(tree):
    def walk(node, prefix=''):
        for name, entry in (node.get('files') or {}).items():
            full = prefix + name
            if 'files' in entry:
                yield from walk(entry, full + '/')
            elif entry.get('unpacked') or 'offset' not in entry:
                continue
            else:
                yield full, entry
    yield from walk(tree)


def _bump_entry(tree, path, size, integrity):
    node = tree
    parts = path.split('/')
    for part in parts[:-1]:
        node = (node.get('files') or {}).setdefault(part, {'files': {}})
    target = (node.get('files') or {}).get(parts[-1])
    if target is not None:
        target['size'] = size
        target['integrity'] = integrity


def verify_patch(app=APP):
    asar = Path(app) / 'Contents' / 'Resources' / 'app.asar'
    raw, tree, header_size, json_length = read_asar_raw(asar.read_bytes())
    entry = _chunk_entry(tree)
    patched = _chunk_text(raw, header_size, entry)
    if MARKER not in patched:
        raise PatchError('Marqueur absent après patch.')
    if entry.get('integrity') != file_integrity(patched.encode()):
        raise PatchError('Intégrité par fichier incohérente après patch.')
    json_bytes = raw[16:16 + json_length]
    plist = plistlib.load(open(Path(app) / 'Contents' / 'Info.plist', 'rb'))
    recorded = (plist.get('ElectronAsarIntegrity') or {}).get('Resources/app.asar', {}).get('hash')
    if recorded != hashlib.sha256(json_bytes).hexdigest():
        raise PatchError('Hash ElectronAsarIntegrity incohérent après patch.')
    integrity_dict = plist.get('ElectronAsarIntegrity') or {}
    expected = integrity_dict_digest(integrity_dict)
    try:
        data = _framework_path(app).read_bytes()
    except OSError as error:
        raise PatchError(f'Framework illisible: {error}') from error
    slots = 0
    start = 0
    while True:
        idx = data.find(DICT_DIGEST_SENTINEL, start)
        if idx < 0:
            break
        if data[idx + SLOT_USED_OFFSET] == 1 and data[idx + SLOT_VERSION_OFFSET] == 1:
            if data[idx + SLOT_DIGEST_OFFSET:idx + SLOT_DIGEST_OFFSET + SLOT_DIGEST_SIZE].hex() != expected:
                raise PatchError('Digest du dictionnaire incohérent dans le slot __asar_integrity.')
            slots += 1
        start = idx + 1
    if slots == 0:
        raise PatchError('Aucun slot __asar_integrity actif après patch.')
    return True


def restore(app=APP):
    asar = Path(app) / 'Contents' / 'Resources' / 'app.asar'
    plist_path = Path(app) / 'Contents' / 'Info.plist'
    paths = _paths(app)
    backup, plist_backup, sidecar_path = paths['backup'], paths['plist_backup'], paths['sidecar']
    if desktop_running(app):
        raise PatchError('Claude Desktop est lancé ; le quitter avant la restauration.')
    if not backup.exists():
        raise PatchError('Aucun backup pristine à restaurer.')
    pristine = backup.read_bytes()
    raw, tree, header_size, json_length = read_asar_raw(pristine)
    plist = plistlib.load(open(plist_path, 'rb'))
    plist['ElectronAsarIntegrity']['Resources/app.asar']['hash'] = hashlib.sha256(raw[16:16 + json_length]).hexdigest()
    with open(plist_path, 'wb') as handle:
        plistlib.dump(plist, handle, fmt=plistlib.FMT_BINARY)
    os.replace(backup, asar)
    # le hash du plist doit repasser à la valeur du backup (le plist courant a été swappé)
    plist_new = plistlib.load(open(plist_path, 'rb'))
    plist_new['ElectronAsarIntegrity']['Resources/app.asar']['hash'] = hashlib.sha256(raw[16:16 + json_length]).hexdigest()
    with open(plist_path, 'wb') as handle:
        plistlib.dump(plist_new, handle, fmt=plistlib.FMT_BINARY)
    try:
        patch_integrity_slot(app, integrity_dict_digest(plist_new['ElectronAsarIntegrity']))
    except PatchError:
        pass
    for stale in (plist_backup, sidecar_path, SOCKET):
        try:
            stale.unlink()
        except OSError:
            pass
    return {'status': 'restored', 'asar_sha256': hashlib.sha256(asar.read_bytes()).hexdigest()}


def status(app=APP):
    asar = Path(app) / 'Contents' / 'Resources' / 'app.asar'
    raw, tree, header_size, json_length = read_asar_raw(asar.read_bytes())
    entry = _chunk_entry(tree)
    chunk = _chunk_text(raw, header_size, entry)
    return {'version': _version() if Path(app) == APP else None, 'patched': MARKER in chunk,
            'backup': _paths(app)['backup'].exists(), 'socket': SOCKET.exists(),
            'desktop_running': desktop_running(app)}


def _readline(sock, timeout=30):
    buffer = b''
    sock.settimeout(timeout)
    while b'\n' not in buffer:
        chunk = sock.recv(65536)
        if not chunk:
            raise PatchError('Connexion dispatch fermée avant réponse.')
        buffer += chunk
    return buffer.split(b'\n', 1)[0].decode()


def send_trigger(text, chat_id=None):
    """Client du socket de dispatch : greeting, une ligne {'prompt', 'chatId'}, la réponse.

    Compatible avec le mode one-ligne de claude_desktop_rpc.dispatch_turn.
    """
    if len(text.strip()) < TRIGGER_MIN_CHARS:
        raise PatchError(f'Le déclencheur doit faire au moins {TRIGGER_MIN_CHARS} caractères après trim (garde QTi).')
    client = socket_module.socket(socket_module.AF_UNIX, socket_module.SOCK_STREAM)
    client.settimeout(30)
    client.connect(str(SOCKET))
    greeting = json.loads(_readline(client))
    if not (isinstance(greeting, dict) and greeting.get('hello') is True):
        client.close()
        raise PatchError('Greeting dispatch inattendu.')
    payload = {'prompt': text}
    if chat_id:
        payload['chatId'] = str(chat_id)
    client.sendall(json.dumps(payload).encode() + b'\n')
    reply = json.loads(_readline(client))
    client.close()
    return reply


def send_rpc(method, params=None):
    """Client JSON-RPC one-shot (initialize / quick_entry/capabilities / quick_entry/submit)."""
    client = socket_module.socket(socket_module.AF_UNIX, socket_module.SOCK_STREAM)
    client.settimeout(30)
    client.connect(str(SOCKET))
    greeting = json.loads(_readline(client))
    if not (isinstance(greeting, dict) and greeting.get('hello') is True):
        client.close()
        raise PatchError('Greeting dispatch inattendu.')
    client.sendall(json.dumps({'jsonrpc': '2.0', 'id': 1, 'method': method, 'params': params or {}}).encode() + b'\n')
    reply = json.loads(_readline(client))
    client.close()
    if 'error' in reply:
        raise PatchError('Erreur RPC: ' + str(reply['error']))
    return reply.get('result') or {}
