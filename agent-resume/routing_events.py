"""Journal des décisions de routage et admissibilité (contrat §7.5 révisé §7.6), schéma v3.

Trois types d'événements : routed, explicit, routing_substituted.
Anonymisation P0 : jamais de prompt brut ni de chemin utilisateur ; les
identifiants de session/thread et les clés de compte sont hachés.
INVARIANT (sal partagé) : account_key utilise le sal de quota_identity_meta
(quota_knowledge), pas routing_meta — la jointure journal ↔ observations de
quota est le but du lot ① (annotation D2/D4 a posteriori) ; elle exige la
même fonction de pseudonymisation des deux côtés.
La substitution de route reste gelée : admissible() définit la règle pour un
usage futur, aucun remplacement automatique n'est activé ni appelé ici.
"""
import hashlib
import hmac
import json
import os
import time

VERSION = 4
TYPES = ('routed', 'explicit', 'routing_substituted')
DEFAULT_RETENTION_DAYS = 180
# Classes de facturation : free (local), included (abonnement confirmé),
# paid (clé API / crédits — jamais admissible en remplacement automatique),
# unknown (défaut fail-closed).
BILLING_CLASSES = ('free', 'included', 'paid', 'unknown')
# Valeurs codées pour les routes locales certaines ; tout le reste = unknown
# jusqu'à déclaration opérateur (édition directe ou lot dédié — limite notée).
LOCAL_BILLING = {'foundation-models': 'free', 'ironclaw': 'free'}

SCHEMA_V2 = ('CREATE TABLE IF NOT EXISTS routing_events ('
             'id INTEGER PRIMARY KEY, ts REAL NOT NULL, '
             "type TEXT NOT NULL CHECK(type IN ('routed','explicit','routing_substituted')), "
             'tool TEXT NOT NULL, channel TEXT NOT NULL, '
             'requested_route TEXT CHECK(requested_route IS NULL OR json_valid(requested_route)), '
             'served_route TEXT CHECK(served_route IS NULL OR json_valid(served_route)), '
             'level TEXT, terminal_state TEXT NOT NULL DEFAULT \'dispatched\', '
             'request_key TEXT, thread_hash TEXT, loop_signature TEXT, reason TEXT, '
             'account_key TEXT, '
             "admissibility TEXT DEFAULT 'not_required', "
             "CHECK(type != 'routing_substituted' OR (requested_route IS NOT NULL AND served_route IS NOT NULL)))")
INDEXES = ('CREATE INDEX IF NOT EXISTS routing_events_ts ON routing_events(ts)',
           'CREATE INDEX IF NOT EXISTS routing_events_type ON routing_events(type, ts)')

ADMISSIBILITY_SCHEMA = (
    'CREATE TABLE IF NOT EXISTS route_admissibility ('
    'channel TEXT PRIMARY KEY, account_key TEXT, pool_key TEXT, '
    "billing_class TEXT NOT NULL CHECK(billing_class IN ('free','included','paid','unknown')), "
    "identity_status TEXT NOT NULL CHECK(identity_status IN ('known','unknown')), "
    'updated_at REAL NOT NULL)')


def _meta_value(db, key):
    row = db.execute('SELECT value FROM routing_meta WHERE key=?', (key,)).fetchone()
    return row[0] if row else None


def setup(db):
    """Création ou migration idempotente vers le schéma VERSION.

    0 → 3 : re-création (v1/v2 = journal de test, perte assumée).
    2 → 3 : ALTER TABLE additif (account_key, admissibility) + route_admissibility,
    les données v2 sont préservées.
    """
    version = db.execute('PRAGMA user_version').fetchone()[0]
    if version > VERSION:
        raise ValueError(f'Schéma routing_events inconnu : user_version={version}')
    with db:
        if version < 2:
            db.execute('DROP TABLE IF EXISTS routing_events')
        db.execute('CREATE TABLE IF NOT EXISTS routing_meta(key TEXT PRIMARY KEY, value TEXT NOT NULL)')
        db.execute(SCHEMA_V2)
        if version < VERSION:
            db.execute("INSERT OR IGNORE INTO routing_meta VALUES ('local_salt',?)", (os.urandom(32).hex(),))
            db.execute("INSERT OR IGNORE INTO routing_meta VALUES ('retention_days',?)", (str(DEFAULT_RETENTION_DAYS),))
        if version == 2:
            existing = {row[1] for row in db.execute('PRAGMA table_info(routing_events)')}
            for column in ('account_key TEXT', "admissibility TEXT DEFAULT 'not_required'"):
                name = column.split()[0]
                if name not in existing:
                    db.execute(f'ALTER TABLE routing_events ADD COLUMN {column}')
        for index in INDEXES:
            db.execute(index)
        db.execute(ADMISSIBILITY_SCHEMA)
        db.execute('CREATE INDEX IF NOT EXISTS route_admissibility_class ON route_admissibility(billing_class)')
        db.execute("INSERT OR IGNORE INTO routing_meta VALUES ('local_salt',?)", (os.urandom(32).hex(),))
        db.execute("INSERT OR IGNORE INTO routing_meta VALUES ('retention_days',?)", (str(DEFAULT_RETENTION_DAYS),))
        # v4 : cache externe (tables additives IF NOT EXISTS, données préservées).
        import external_rankings
        external_rankings.setup(db)
        db.execute(f'PRAGMA user_version = {VERSION}')
    return version


def declare(db, channel, billing_class, account_key=None, pool_key=None, identity_status='known'):
    """Déclaration opérateur ou peuplement d'admissibilité pour un canal."""
    if billing_class not in BILLING_CLASSES or identity_status not in ('known', 'unknown'):
        raise ValueError('Invalid billing class or identity status')
    with db:
        db.execute('INSERT OR REPLACE INTO route_admissibility VALUES (?,?,?,?,?,?)',
                   (channel, account_key, pool_key, billing_class, identity_status, time.time()))


def admissible(db, channel, automatic):
    """Règle d'admissibilité (contrat « aucun repli payant implicite »).

    automatic=True (remplacement futur, aujourd'hui gelé) : seules les classes
    free/included passent ; paid jamais ; unknown fail-closed.
    automatic=False (route demandée explicitement par l'appel) : unknown est
    toléré — le consentement opérateur est porté par l'appel lui-même ; paid
    reste explicitement autorisé (l'appelant l'a nommée).
    """
    row = db.execute('SELECT billing_class FROM route_admissibility WHERE channel=?', (channel,)).fetchone()
    billing = row[0] if row else 'unknown'
    if automatic:
        return billing in ('free', 'included'), billing
    return True, billing


def populate_from_quota(db, quota_status):
    """Peuplement codex=included depuis une observation quota_knowledge conforme :
    identity_status known ET ordinary_usage_allowed observé. Sinon unknown."""
    if not isinstance(quota_status, dict):
        return
    if quota_status.get('identity_status') == 'known' and quota_status.get('account_key'):
        pools = quota_status.get('pools') or []
        allowed = quota_status.get('ordinary_usage_allowed_observed')
        pool_key = next((p['quota_pool_key'] for p in pools if p.get('quota_pool_key')), None)
        if allowed is True:
            declare(db, 'codex', 'included', account_key=quota_status['account_key'],
                    pool_key=pool_key, identity_status='known')
        else:
            declare(db, 'codex', 'unknown', account_key=quota_status['account_key'],
                    pool_key=pool_key, identity_status='known')


def seed_local(db):
    """Valeurs codées des routes locales certaines (appelé par le pont à l'init)."""
    for channel, billing in LOCAL_BILLING.items():
        row = db.execute('SELECT 1 FROM route_admissibility WHERE channel=?', (channel,)).fetchone()
        if not row:
            declare(db, channel, billing, identity_status='known')


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


def account_hash(db, account_key):
    """Passe-through du HMAC quota_knowledge (déjà haché, sal partagé) ;
    refuse toute valeur qui ne ressemble pas à un HMAC hexadécimal."""
    if not isinstance(account_key, str) or len(account_key) < 16:
        return None
    return account_key


def _route(value):
    if value is None:
        return None
    return json.dumps({k: value[k] for k in sorted(value)}, sort_keys=True)


def record(db, event_type, tool, channel, requested=None, served=None,
           level=None, request_key=None, thread_id=None, loop_signature=None, reason=None,
           account_key=None, admissibility=None):
    if event_type not in TYPES:
        raise ValueError(f'Unknown routing event type: {event_type}')
    if requested is not None and not isinstance(requested, dict):
        raise ValueError('requested route must be a dict')
    if served is not None and not isinstance(served, dict):
        raise ValueError('served route must be a dict')
    if admissibility is not None and admissibility not in ('granted', 'refused_paid', 'refused_unknown', 'not_required'):
        raise ValueError('Invalid admissibility outcome')
    with db:
        cursor = db.execute(
            'INSERT INTO routing_events(ts,type,tool,channel,requested_route,served_route,'
            'level,terminal_state,request_key,thread_hash,loop_signature,reason,'
            'account_key,admissibility) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
            (time.time(), event_type, tool, channel, _route(requested), _route(served),
             level, 'dispatched', request_key, pseudonymize(db, thread_id), loop_signature, reason,
             account_hash(db, account_key), admissibility))
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
