"""Journal des décisions de routage (contrat §7.5 révisé §7.6), schéma v2.

Trois types d'événements : routed, explicit, routing_substituted.
Anonymisation P0 : jamais de prompt brut ni de chemin utilisateur ; les
identifiants de session/thread sont hachés par HMAC salé persistant
(routing_meta, même modèle que quota_identity_meta).
La substitution de route reste gelée : le type routing_substituted est
journalisable, mais aucun code de production ne le produit aujourd'hui.
Migration idempotente via PRAGMA user_version (0 → 2 sur expert.sqlite) ;
la table v1 (~0 lignes de journal de test) est recréée, pas préservée.
"""
import hashlib
import hmac
import json
import os
import time

VERSION = 2
TYPES = ('routed', 'explicit', 'routing_substituted')
DEFAULT_RETENTION_DAYS = 180

SCHEMA_V2 = ('CREATE TABLE IF NOT EXISTS routing_events ('
             'id INTEGER PRIMARY KEY, ts REAL NOT NULL, '
             "type TEXT NOT NULL CHECK(type IN ('routed','explicit','routing_substituted')), "
             'tool TEXT NOT NULL, channel TEXT NOT NULL, '
             'requested_route TEXT CHECK(requested_route IS NULL OR json_valid(requested_route)), '
             'served_route TEXT CHECK(served_route IS NULL OR json_valid(served_route)), '
             'level TEXT, terminal_state TEXT NOT NULL DEFAULT \'dispatched\', '
             'request_key TEXT, thread_hash TEXT, loop_signature TEXT, reason TEXT, '
             "CHECK(type != 'routing_substituted' OR (requested_route IS NOT NULL AND served_route IS NOT NULL)))")
INDEXES = ('CREATE INDEX IF NOT EXISTS routing_events_ts ON routing_events(ts)',
           'CREATE INDEX IF NOT EXISTS routing_events_type ON routing_events(type, ts)')


def _meta_value(db, key):
    row = db.execute('SELECT value FROM routing_meta WHERE key=?', (key,)).fetchone()
    return row[0] if row else None


def setup(db):
    """Création ou migration idempotente vers le schéma VERSION."""
    version = db.execute('PRAGMA user_version').fetchone()[0]
    if version > VERSION:
        raise ValueError(f'Schéma routing_events inconnu : user_version={version}')
    with db:
        if version < VERSION:
            db.execute('DROP TABLE IF EXISTS routing_events')
            db.execute('CREATE TABLE IF NOT EXISTS routing_meta(key TEXT PRIMARY KEY, value TEXT NOT NULL)')
            db.execute(SCHEMA_V2)
            db.execute("INSERT OR IGNORE INTO routing_meta VALUES ('local_salt',?)", (os.urandom(32).hex(),))
            db.execute("INSERT OR IGNORE INTO routing_meta VALUES ('retention_days',?)", (str(DEFAULT_RETENTION_DAYS),))
            db.execute('PRAGMA user_version = %d' % VERSION)
        db.execute('CREATE TABLE IF NOT EXISTS routing_meta(key TEXT PRIMARY KEY, value TEXT NOT NULL)')
        db.execute(SCHEMA_V2)
        for index in INDEXES:
            db.execute(index)
        db.execute("INSERT OR IGNORE INTO routing_meta VALUES ('local_salt',?)", (os.urandom(32).hex(),))
        db.execute("INSERT OR IGNORE INTO routing_meta VALUES ('retention_days',?)", (str(DEFAULT_RETENTION_DAYS),))
    return version


def retention_days(db):
    value = _meta_value(db, 'retention_days')
    try:
        return int(value)
    except (TypeError, ValueError):
        return DEFAULT_RETENTION_DAYS


def purge(db, before):
    """Suppression explicite des événements antérieurs au timestamp donné."""
    with db:
        cursor = db.execute('DELETE FROM routing_events WHERE ts < ?', (before,))
    return cursor.rowcount


def pseudonymize(db, ident):
    """HMAC salé persistant d'un identifiant de session/thread (P0)."""
    if not isinstance(ident, str) or not ident:
        return None
    salt = bytes.fromhex(_meta_value(db, 'local_salt'))
    return hmac.new(salt, ident.encode(), hashlib.sha256).hexdigest()[:16]


def _route(value):
    if value is None:
        return None
    return json.dumps({k: value[k] for k in sorted(value)}, sort_keys=True)


def record(db, event_type, tool, channel, requested=None, served=None,
           level=None, request_key=None, thread_id=None, loop_signature=None, reason=None):
    if event_type not in TYPES:
        raise ValueError(f'Unknown routing event type: {event_type}')
    if requested is not None and not isinstance(requested, dict):
        raise ValueError('requested route must be a dict')
    if served is not None and not isinstance(served, dict):
        raise ValueError('served route must be a dict')
    with db:
        cursor = db.execute(
            'INSERT INTO routing_events(ts,type,tool,channel,requested_route,served_route,'
            'level,terminal_state,request_key,thread_hash,loop_signature,reason) '
            'VALUES (?,?,?,?,?,?,?,?,?,?,?,?)',
            (time.time(), event_type, tool, channel, _route(requested), _route(served),
             level, 'dispatched', request_key, pseudonymize(db, thread_id), loop_signature, reason))
    return cursor.lastrowid


def finalize(db, event_id, terminal_state):
    """Attache l'état terminal du tour (finished/blocked/…) à la décision."""
    if not isinstance(terminal_state, str) or not terminal_state:
        raise ValueError('terminal_state required')
    with db:
        db.execute('UPDATE routing_events SET terminal_state=? WHERE id=?', (terminal_state, event_id))


def recent(db, limit=100):
    cursor = db.execute('SELECT * FROM routing_events ORDER BY id DESC LIMIT ?', (limit,))
    columns = [c[0] for c in cursor.description]
    return [dict(zip(columns, row)) for row in cursor.fetchall()]
