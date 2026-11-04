import hashlib
import json
import math
from pathlib import Path
import re
import sqlite3
import subprocess
import unicodedata
import time
import contextlib
import fcntl
from vault import load, save
from app_paths import native_binary


def words(text):
    text = ''.join(c for c in unicodedata.normalize('NFD', text.lower()) if not unicodedata.combining(c))
    return set(re.findall(r'[\w./-]+', text))


def cosine(a, b):
    if not a or not b or len(a) != len(b):
        return 0
    norm = math.sqrt(sum(v*v for v in a) * sum(v*v for v in b))
    return sum(x*y for x,y in zip(a,b))/norm if norm else 0


def embeddings(root, texts):
    binary = native_binary(root, 'Embedding')
    try:
        p = subprocess.run([str(binary)], input=json.dumps(texts), text=True, capture_output=True, timeout=20)
        values = json.loads(p.stdout)
        if p.returncode == 0 and len(values) == len(texts):
            return values
    except (OSError, ValueError, subprocess.TimeoutExpired):
        pass
    return [{'language': None, 'vector': None} for _ in texts]


def search(state, root, query):
    from detect import scan_all
    snapshot = scan_all()
    docs = {}
    for item in snapshot['sessions']:
        ident = item['provider'] + ':' + item['id']
        docs[ident] = {'title': item['title'], 'provider': item['provider'], 'text': item['label'], 'updated': item['updated']}
    for key, item in snapshot.get('analysis', {}).get('results', {}).items():
        docs[key] = {'title': item['title'], 'provider': item['provider'], 'text': item['status'] + ' ' + item['reason'] + ' ' + item['evidence'], 'updated': item['updated']}
    docs = {k:v for k,v in docs.items() if v['updated'] >= time.time() - 7*86400}
    folder = state / 'search.noindex'
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    with encrypted_index(state) as db:
        schema(db)
        db.execute('BEGIN IMMEDIATE')
        stored = {r[0]: (r[1], r[2]) for r in db.execute('SELECT id,digest,embedding FROM documents')}
        changed = []
        for key, doc in docs.items():
            doc['full'] = doc['title'] + '\n' + doc['text']
            doc['digest'] = hashlib.sha256(doc['full'].encode()).hexdigest()
            if key not in stored or stored[key][0] != doc['digest'] or not json.loads(stored[key][1]).get('vector'):
                changed.append(key)
        vectors = embeddings(root, [docs[k]['full'] for k in changed]) if changed else []
        for key, vector in zip(changed, vectors):
            doc = docs[key]
            db.execute('INSERT OR REPLACE INTO documents VALUES (?,?,?,?,?,?,?)', (key,doc['digest'],doc['title'],doc['provider'],doc['text'],json.dumps(vector),doc['updated']))
        for key in stored.keys() - docs.keys():
            db.execute('DELETE FROM documents WHERE id=?', (key,))
        query_embedding = embeddings(root, [query])[0]
        tokens = words(query)
        results = []
        for ident, title, provider, text, raw in db.execute('SELECT id,title,provider,text,embedding FROM documents'):
            vector = json.loads(raw)
            lexical = len(tokens & words(title+' '+text))/max(1,len(tokens))
            semantic = max(0,cosine(query_embedding.get('vector'),vector.get('vector'))) if query_embedding['language'] == vector['language'] and query_embedding.get('revision') == vector.get('revision') else 0
            score = lexical * 0.65 + semantic * 0.35
            if lexical > 0 or semantic >= 0.55:
                results.append(dict(id=ident,title=title,provider=provider,text=text[:600],score=round(score,4)))
    return {'results': sorted(results,key=lambda r:r['score'],reverse=True)[:8], 'semantic_available': bool(query_embedding.get('vector')), 'scope': 'États récents et comptes rendus déjà analysés · index privé'}


def schema(db):
    db.execute('PRAGMA secure_delete=ON')
    db.execute('CREATE TABLE IF NOT EXISTS documents (id TEXT PRIMARY KEY, digest TEXT, title TEXT, provider TEXT, text TEXT, embedding TEXT, updated REAL DEFAULT 0)')
    if 'updated' not in {r[1] for r in db.execute('PRAGMA table_info(documents)')}:
        db.execute('ALTER TABLE documents ADD COLUMN updated REAL DEFAULT 0')
    db.execute('CREATE TABLE IF NOT EXISTS maintenance (id INTEGER PRIMARY KEY, cleaned REAL)')
    db.commit()


@contextlib.contextmanager
def encrypted_index(state):
    folder = state / 'search.noindex'
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = folder / 'index.enc'
    legacy = folder / 'index.sqlite'
    with (folder / 'index.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        db = sqlite3.connect(':memory:')
        try:
            if path.exists():
                db.deserialize(load(path, 'search-index-v1'))
            elif legacy.exists():
                with contextlib.closing(sqlite3.connect(legacy.as_uri() + '?mode=ro', uri=True)) as old:
                    old.backup(db)
            schema(db)
            yield db
            db.commit()
            save(path, db.serialize(), 'search-index-v1')
            if legacy.exists():
                legacy.unlink()
                for suffix in ('-wal', '-shm', '-journal'):
                    Path(str(legacy)+suffix).unlink(missing_ok=True)
        finally:
            db.close()


def cleanup(state, clear=False, now=None):
    now = time.time() if now is None else now
    folder = state / 'search.noindex'
    if not any((folder / name).exists() for name in ('index.enc', 'index.sqlite')):
        return 0
    with encrypted_index(state) as db:
        db.execute('BEGIN IMMEDIATE')
        last = db.execute('SELECT cleaned FROM maintenance WHERE id=1').fetchone()
        if not clear and last and now-last[0] < 86400:
            return 0
        removed = db.execute('DELETE FROM documents' if clear else 'DELETE FROM documents WHERE updated<?', () if clear else (now-7*86400,)).rowcount
        db.execute('INSERT OR REPLACE INTO maintenance VALUES (1,?)', (now,))
        db.commit()
        if removed:
            db.execute('VACUUM')
    return removed
