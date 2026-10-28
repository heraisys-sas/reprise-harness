"""Journal additif des décisions de routage (contrat §7.5 révisé §7.6).

Trois types d'événements : routed, explicit, routing_substituted.
Anonymisation P0 par défaut : jamais de prompt brut ni de chemin utilisateur ;
les identifiants de session/thread sont hachés (tronqués à 16 hex).
La substitution de route reste gelée : le type routing_substituted est
journalisable, mais aucun code ne le produit aujourd'hui.
"""
import hashlib
import json
import time

TYPES = ('routed', 'explicit', 'routing_substituted')

SCHEMA = ('CREATE TABLE IF NOT EXISTS routing_events ('
          'id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL NOT NULL, '
          'type TEXT NOT NULL CHECK(type IN (\'routed\',\'explicit\',\'routing_substituted\')), '
          'tool TEXT NOT NULL, channel TEXT NOT NULL, '
          'requested_route TEXT, served_route TEXT, level TEXT, '
          'terminal_state TEXT, request_key TEXT, '
          'thread_hash TEXT, loop_signature TEXT, reason TEXT)')


def setup(db):
    db.execute(SCHEMA)
    db.commit()


def pseudonymize(ident):
    """Hash stable d'un identifiant de session/thread (P0 : pas d'ID complet)."""
    if not isinstance(ident, str) or not ident:
        return None
    return hashlib.sha256(ident.encode()).hexdigest()[:16]


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
             level, 'dispatched', request_key, pseudonymize(thread_id), loop_signature, reason))
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
