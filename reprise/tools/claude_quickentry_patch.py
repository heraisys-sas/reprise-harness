#!/usr/bin/env python3
"""Patch app.asar d'une COPIE de Claude.app : injecte le serveur socket
quick-entry.sock apres l'ancre KW?.on("quickEntrySubmitted",TYr).

Dry-run par defaut (--apply requis pour ecrire). Python stdlib uniquement.
Usage :
    claude_quickentry_patch.py /tmp/Claude-Reprise.app           # dry-run
    claude_quickentry_patch.py /tmp/Claude-Reprise.app --apply
    claude_quickentry_patch.py /tmp/Claude-Reprise.app --check
"""

import argparse
import hashlib
import plistlib
import shutil
import struct
import subprocess
import sys
from pathlib import Path

CHUNK_PATH = ".vite/build/index.chunk-m4oMImJ-.js"
ANCHOR = 'KW?.on("quickEntrySubmitted",TYr)'
PAYLOAD_MARKER = "/*repriseQE-v1*/"
SOCKET_SENTINEL = "quick-entry.sock"  # fusion 10/11/2026 : un seul socket


class PatchError(Exception):
    pass


def _fail(msg):
    raise PatchError(msg)


# --- asar parsing -----------------------------------------------------------

def read_asar(path):
    data = Path(path).read_bytes()
    if len(data) < 16:
        _fail("asar trop court")
    w = struct.unpack("<4I", data[:16])
    json_len = w[3]
    if w[0] != 4 or 16 + json_len > len(data):
        _fail("entete asar incoherente")
    header_json = data[16:16 + json_len]
    return data, header_json


def iter_files(node, prefix=""):
    for name, entry in node.get("files", {}).items():
        rel = prefix + name
        if "files" in entry:
            yield from iter_files(entry, rel + "/")
        else:
            yield rel, entry


def find_chunk_entry(header_json, chunk_rel=CHUNK_PATH):
    import json
    root = json.loads(header_json)
    for rel, entry in iter_files(root):
        if rel == chunk_rel:
            return int(entry["offset"]), int(entry["size"])
    _fail("chunk %s introuvable dans l'asar" % chunk_rel)


def chunk_bytes(data, header_json, chunk_rel=CHUNK_PATH):
    off, size = find_chunk_entry(header_json, chunk_rel)
    base = 16 + len(header_json)
    return data[base + off:base + off + size]


# --- serialize asar ---------------------------------------------------------

def _assign_offsets(node, cur):
    for name in sorted(node.get("files", {})):
        entry = node["files"][name]
        if "files" in entry:
            cur = _assign_offsets(entry, cur)
        else:
            entry["offset"] = str(cur)
            cur += int(entry["size"])
    return cur


def build_asar(header_tree, data_blobs):
    """header_tree: dict asar ; data_blobs: list[bytes] dans le meme ordre
    deterministe que _assign_offsets (trii alphabetique recursif)."""
    total = _assign_offsets(header_tree, 0)
    import json
    header_json = json.dumps(header_tree, sort_keys=True).encode()
    head = struct.pack("<4I", 4, len(header_json) + 8, len(header_json) + 4,
                       len(header_json))
    return head + header_json + b"".join(data_blobs), len(header_json)


def rebuild_with_chunk(original_data, header_json, chunk_rel, new_chunk):
    import json
    tree = json.loads(header_json)
    old_off, old_size = find_chunk_entry(header_json, chunk_rel)

    def set_chunk(node, parts):
        if not parts:
            entry = dict(node)
            entry["size"] = len(new_chunk)
            return entry
        nxt = dict(node)
        nxt["files"] = dict(node["files"])
        nxt["files"][parts[0]] = set_chunk(node["files"][parts[0]], parts[1:])
        return nxt

    parts = chunk_rel.split("/")
    tree = set_chunk(tree, parts)
    # collecte dans le meme ordre trii que build_asar
    blobs = []

    def walk(node, prefix=""):
        for name in sorted(node.get("files", {})):
            entry = node["files"][name]
            rel = prefix + name
            if "files" in entry:
                walk(entry, rel + "/")
            elif rel == chunk_rel:
                blobs.append(new_chunk)
            else:
                blobs.append(original_data[16 + len(header_json) + int(entry["offset"]):
                                           16 + len(header_json) + int(entry["offset"]) + int(entry["size"])])

    walk(tree)
    return build_asar(tree, blobs)


# --- integrity --------------------------------------------------------------

def asar_hash(asar_bytes):
    """SHA-256 des octets [16:16+w4], w=struct.unpack('<4I',...), w[3]."""
    w = struct.unpack("<4I", asar_bytes[:16])
    return hashlib.sha256(asar_bytes[16:16 + w[3]]).hexdigest()


def read_plist_hash(app_dir):
    with open(Path(app_dir) / "Contents" / "Info.plist", "rb") as f:
        pl = plistlib.load(f)
    ei = pl.get("ElectronAsarIntegrity")
    if not ei or "Resources/app.asar" not in ei:
        _fail("ElectronAsarIntegrity absent de Info.plist")
    return ei["Resources/app.asar"].get("hash")


def write_plist_hash(app_dir, new_hash):
    p = Path(app_dir) / "Contents" / "Info.plist"
    with open(p, "rb") as f:
        pl = plistlib.load(f)
    pl["ElectronAsarIntegrity"]["Resources/app.asar"]["hash"] = new_hash
    with open(p, "wb") as f:
        plistlib.dump(pl, f)


# --- payload ----------------------------------------------------------------

def load_payload(inject_file=None):
    if inject_file is None:
        inject_file = Path(__file__).with_name("reprise_qe_inject.js")
    payload = Path(inject_file).read_text()
    if PAYLOAD_MARKER not in payload or SOCKET_SENTINEL not in payload:
        _fail("payload invalide (marqueur/socket absents) : %s" % inject_file)
    if "\\\n" in payload:
        _fail("payload invalide : backslash-LF interdit dans les literals")
    return payload


def inject_after_anchor(chunk, payload):
    n = chunk.count(ANCHOR)
    if n == 0:
        _fail("ancre %r absente du chunk" % ANCHOR)
    if n > 1:
        _fail("ancre %r presente %d fois (refus)" % (ANCHOR, n))
    if PAYLOAD_MARKER in chunk:
        _fail("payload deja injecte (copie deja patchee ?)")
    i = chunk.index(ANCHOR) + len(ANCHOR)
    return chunk[:i] + ";" + payload + chunk[i:]


# --- main -------------------------------------------------------------------

def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("app", help="COPIE de Claude.app (jamais /Applications)")
    ap.add_argument("--apply", action="store_true", help="ecrire (defaut: dry-run)")
    ap.add_argument("--check", action="store_true",
                    help="verifier une copie deja patchee")
    ap.add_argument("--inject-file", default=None)
    args = ap.parse_args(argv)

    app = Path(args.app)
    asar = app / "Contents" / "Resources" / "app.asar"
    if not asar.is_file():
        _fail("asar introuvable : %s" % asar)
    if str(app).startswith("/Applications/"):
        _fail("refus : /Applications est interdit, patcher une copie")

    data, header_json = read_asar(asar)
    chunk = chunk_bytes(data, header_json).decode("utf-8", "surrogateescape")
    sha_before = hashlib.sha256(data).hexdigest()

    try:
        return _main(args, app, asar, data, header_json, chunk, sha_before)
    except PatchError as e:
        print("ERREUR: %s" % e, file=sys.stderr)
        return 2


def _main(args, app, asar, data, header_json, chunk, sha_before):
    if args.check:
        n_anchor = chunk.count(ANCHOR)
        n_payload = chunk.count(PAYLOAD_MARKER)
        plist_hash = read_plist_hash(app)
        ok = (n_payload == 1 and SOCKET_SENTINEL in chunk
              and plist_hash == asar_hash(data))
        print("ancre: %d, payload: %d, hash plist %s, hash recalcule %s" % (
            n_anchor, n_payload, plist_hash, asar_hash(data)))
        print("CHECK %s" % ("OK" if ok else "ECHEC"))
        return 0 if ok else 1

    payload = load_payload(args.inject_file)
    new_chunk = inject_after_anchor(chunk, payload)
    new_chunk_b = new_chunk.encode("utf-8", "surrogateescape")
    new_data, new_json_len = rebuild_with_chunk(data, header_json, CHUNK_PATH, new_chunk_b)
    new_hash = asar_hash(new_data)
    sha_after = hashlib.sha256(new_data).hexdigest()

    print("asar        : %s" % asar)
    print("SHA-256 avant: %s" % sha_before)
    print("SHA-256 apres: %s" % sha_after)
    print("hash Info.plist (ElectronAsarIntegrity): %s" % new_hash)
    print("taille chunk : %d -> %d" % (len(chunk), len(new_chunk)))

    if not args.apply:
        print("DRY-RUN : aucune ecriture. Utiliser --apply.")
        return 0

    orig = asar.with_name("app.asar.orig")
    if not orig.exists():
        shutil.copy2(asar, orig)
        print("backup       : %s" % orig)
    else:
        print("backup existant conserve : %s" % orig)

    tmp = asar.with_suffix(".asar.tmp")
    tmp.write_bytes(new_data)
    tmp.replace(asar)
    write_plist_hash(app, new_hash)
    print("asar + Info.plist ecrits.")

    cs = shutil.which("codesign")
    if cs:
        r = subprocess.run([cs, "--force", "--deep", "--sign", "-", str(app)],
                           capture_output=True, text=True)
        print(r.stdout + r.stderr)
        if r.returncode != 0:
            _fail("codesign sign a echoue")
        r = subprocess.run([cs, "--verify", "--deep", str(app)],
                           capture_output=True, text=True)
        print(r.stdout + r.stderr)
        if r.returncode != 0:
            _fail("codesign --verify a echoue")
        print("signature ad-hoc OK.")
    else:
        print("codesign absent : signature ignoree (hors macOS).")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except PatchError as e:
        print("ERREUR: %s" % e, file=sys.stderr)
        sys.exit(2)
