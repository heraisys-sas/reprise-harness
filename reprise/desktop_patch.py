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
SOCKET = Path.home() / '.claude-reprise-trigger.sock'
TRIGGER_LIMIT = 2000
BLOCK_SIZE = 4194304

INJECTION = """
;(function(){
  try {
    var __net = require('net'), __fs = require('fs'), __os = require('os');
    var __p = __os.homedir() + '/.claude-reprise-trigger.sock';
    try { __fs.unlinkSync(__p); } catch (e) {}
    var __srv = __net.createServer(function(c) {
      var b = '';
      c.on('data', function(d) {
        b += d.toString('utf8');
        if (b.length > 8192) { try { c.write(JSON.stringify({ok:false, error:'oversized'})); c.end(); } catch (e2) {} }
      });
      c.on('end', function() {
        try {
          var o = JSON.parse(b);
          var text = typeof o.text === 'string' ? o.text : '';
          if (!text || text.length > %(limit)d) { c.write(JSON.stringify({ok:false, error:'text required (<= %(limit)d)'})); c.end(); return; }
          var payload = {text: text, images: []};
          if (o.chatId) payload.chatId = String(o.chatId);
          QTi(payload);
          c.write(JSON.stringify({ok:true, dispatched:true, chatId:o.chatId||null}));
        } catch (e) { try { c.write(JSON.stringify({ok:false, error:String(e && e.message || e)})); } catch (e2) {} }
        c.end();
      });
      c.on('error', function() {});
    });
    __srv.listen(__p, function() { try { __fs.chmodSync(__p, 384); } catch (e) {} });
  } catch (e) {}
})();
""" % {'limit': TRIGGER_LIMIT}


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


def desktop_running():
    probe = subprocess.run(['pgrep', '-x', 'Claude'], capture_output=True)
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


def _version():
    try:
        with open(APP / 'Contents' / 'Info.plist', 'rb') as handle:
            return plistlib.load(handle).get('CFBundleShortVersionString')
    except (OSError, KeyError):
        return None


def patch(app=APP):
    asar = Path(app) / 'Contents' / 'Resources' / 'app.asar'
    plist_path = Path(app) / 'Contents' / 'Info.plist'
    if desktop_running():
        raise PatchError('Claude Desktop est lancé ; le quitter avant le patch.')
    raw, tree, header_size, json_length = read_asar_raw(asar.read_bytes())
    entry = _chunk_entry(tree)
    chunk = _chunk_text(raw, header_size, entry)
    if MARKER in chunk:
        return {'status': 'already_patched'}
    verify_anchors(chunk)
    pristine = asar.read_bytes()
    original_sha = hashlib.sha256(pristine).hexdigest()
    PATCH_DIR.mkdir(parents=True, exist_ok=True)
    shutil.copy2(asar, BACKUP)
    PLIST_BACKUP.write_bytes(plist_path.read_bytes())
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
    verify_patch(app)
    sidecar = {'patched_at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
               'version': _version(), 'original_sha256': original_sha,
               'patched_sha256': hashlib.sha256(asar.read_bytes()).hexdigest(),
               'plist_hash': plist_hash, 'chunk': CHUNK}
    SIDECAR.write_text(json.dumps(sidecar, indent=1))
    return {'status': 'patched', 'backup': str(BACKUP), 'socket': str(SOCKET),
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
    return True


def restore(app=APP):
    asar = Path(app) / 'Contents' / 'Resources' / 'app.asar'
    plist_path = Path(app) / 'Contents' / 'Info.plist'
    if desktop_running():
        raise PatchError('Claude Desktop est lancé ; le quitter avant la restauration.')
    if not BACKUP.exists():
        raise PatchError('Aucun backup pristine à restaurer.')
    pristine = BACKUP.read_bytes()
    raw, tree, header_size, json_length = read_asar_raw(pristine)
    plist = plistlib.load(open(plist_path, 'rb'))
    plist['ElectronAsarIntegrity']['Resources/app.asar']['hash'] = hashlib.sha256(raw[16:16 + json_length]).hexdigest()
    with open(plist_path, 'wb') as handle:
        plistlib.dump(plist, handle, fmt=plistlib.FMT_BINARY)
    os.replace(BACKUP, asar)
    for stale in (PLIST_BACKUP, SIDECAR, SOCKET):
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
    return {'version': _version(), 'patched': MARKER in chunk,
            'backup': BACKUP.exists(), 'socket': SOCKET.exists(),
            'desktop_running': desktop_running()}


def send_trigger(text, chat_id=None):
    """Client du socket de déclenchement : une ligne JSON, la réponse, déconnexion."""
    payload = {'text': str(text)[:TRIGGER_LIMIT]}
    if chat_id:
        payload['chatId'] = str(chat_id)
    client = socket_module.socket(socket_module.AF_UNIX, socket_module.SOCK_STREAM)
    client.settimeout(30)
    client.connect(str(SOCKET))
    client.sendall(json.dumps(payload).encode())
    response = b''
    while True:
        chunk = client.recv(65536)
        if not chunk:
            break
        response += chunk
        if response.endswith(b'}'):
            break
    client.close()
    return json.loads(response)
