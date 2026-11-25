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
# Independent of PRAGMA user_version: external_rankings owns later versions.
LEGACY_IDENTITY_SCRUB_KEY = 'legacy_opaque_identity_scrub_v1'
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

# Lot ① : durées de veto explicites (pas des préférences d'ordre). TTL du
# quota included sans observation ; cooldown après un refus observé
# (synthèse v3 : 30 min, un refus de disponibilité est en général transitoire).
DEFAULT_VETO_TTL_S = 86400
REFUSAL_COOLDOWN_S = 1800


def _meta_value(db, key):
    row = db.execute('SELECT value FROM routing_meta WHERE key=?', (key,)).fetchone()
    return row[0] if row else None


def _invalid_opaque_sql(column):
    """Static SQL predicate for malformed legacy HMAC-shaped values."""
    if column not in ('account_key', 'pool_key'):
        raise ValueError('Unexpected routing identity column')
    return (f"{column} IS NOT NULL AND (typeof({column}) != 'text' "
            f"OR length({column}) != 64 OR {column} GLOB '*[^0-9a-f]*')")


def _scrub_legacy_identities(db):
    """One-time, transactional redaction. Report counts, never raw identities.

    Must run inside setup's write transaction after both routing tables exist.
    This only removes logically visible values, not copies in WAL/backups.
    """
    if _meta_value(db, LEGACY_IDENTITY_SCRUB_KEY) is not None:
        return
    redacted = db.execute(
        'UPDATE routing_events SET account_key=NULL WHERE '
        + _invalid_opaque_sql('account_key')).rowcount
    unsafe = ('(' + _invalid_opaque_sql('account_key') + ') OR ('
              + _invalid_opaque_sql('pool_key') + ')')
    revoked = db.execute(
        "UPDATE route_admissibility SET account_key=NULL, pool_key=NULL, "
        "billing_class='unknown', identity_status='unknown' WHERE "
        + unsafe).rowcount
    counts = json.dumps({'events_redacted': redacted, 'admissibility_revoked': revoked},
                        sort_keys=True, separators=(',', ':'))
    # Course au premier lancement (#195) : deux setups peuvent tous deux passer
    # le check _meta_value puis tenter l'INSERT. OR IGNORE rend le marqueur
    # idempotent et préserve le reçu du premier écrivain, comme les autres
    # écritures routing_meta de setup. Pas de capture IntegrityError : elle
    # masquerait d'autres conflits et compliquerait le rollback du trigger de
    # test.
    db.execute('INSERT OR IGNORE INTO routing_meta(key,value) VALUES (?,?)',
               (LEGACY_IDENTITY_SCRUB_KEY, counts))


def setup(db):
    """Création ou migration idempotente vers le schéma VERSION.

    0 → 3 : re-création (v1/v2 = journal de test, perte assumée).
    2 → 3 : ALTER TABLE additif (account_key, admissibility) + route_admissibility,
    les données v2 sont préservées.
    """
    import external_rankings
    version = db.execute('PRAGMA user_version').fetchone()[0]
    # Le cache externe vit dans la même base et peut être en avance sur le
    # journal : la version supportée est le maximum des deux schémas.
    supported = max(VERSION, external_rankings.VERSION)
    if version > supported:
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
        # Registre des refus (lot ①) : table additive, aucun bump user_version.
        db.execute('CREATE TABLE IF NOT EXISTS route_refusals ('
                   'channel TEXT NOT NULL, reason TEXT NOT NULL, ts REAL NOT NULL)')
        db.execute('CREATE INDEX IF NOT EXISTS route_refusals_channel_ts '
                   'ON route_refusals(channel, ts)')
        db.execute('CREATE INDEX IF NOT EXISTS route_admissibility_class ON route_admissibility(billing_class)')
        db.execute("INSERT OR IGNORE INTO routing_meta VALUES ('local_salt',?)", (os.urandom(32).hex(),))
        db.execute("INSERT OR IGNORE INTO routing_meta VALUES ('retention_days',?)", (str(DEFAULT_RETENTION_DAYS),))
        # v4 : cache externe (tables additives IF NOT EXISTS, données préservées).
        external_rankings.setup(db)
        # A version current on entry does not prove historical identity redaction.
        # Updates + audit marker commit as one unit; failure retains original rows.
        _scrub_legacy_identities(db)
        # user_version reflète le schéma le plus récent (le cache peut être en
        # avance sur le journal) : la valeur la plus élevée fait foi.
        db.execute(f'PRAGMA user_version = {max(VERSION, external_rankings.VERSION)}')
    return version


def _opaque_sha256(value):
    """Format from quota_knowledge.Registry.opaque; does not authenticate origin."""
    return (isinstance(value, str) and len(value) == 64
            and all(char in '0123456789abcdef' for char in value))


def declare(db, channel, billing_class, account_key=None, pool_key=None, identity_status='known'):
    """Déclaration opérateur ou peuplement d'admissibilité pour un canal."""
    if billing_class not in BILLING_CLASSES or identity_status not in ('known', 'unknown'):
        raise ValueError('Invalid billing class or identity status')
    if (account_key is not None and not _opaque_sha256(account_key)
            or pool_key is not None and not _opaque_sha256(pool_key)):
        raise ValueError('Only opaque SHA-256 account/pool keys may be stored')
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


def record_refusal(db, channel, reason):
    """Enregistre un refus de route observé (lot ①). reason : chaîne non vide
    (trim), sinon ValueError ; alimente le veto refus-recent. Aucun
    branchement automatique dans expert_mcp ce lot."""
    if not isinstance(reason, str) or not reason.strip():
        raise ValueError('Raison de refus requise (chaine non vide)')
    with db:
        db.execute('INSERT INTO route_refusals(channel, reason, ts) VALUES (?,?,?)',
                   (channel, reason.strip(), time.time()))


def vetos(db, channel, ttl_s=None):
    """Raisons de veto d'un canal (lot ①) ; liste vide = pas de veto. Jamais
    un passe-droit : un veto ne fait que renforcer le fail-closed.
    - 'paid' / 'unknown' : cohérent avec admissible(automatic=True) ;
    - 'quota-perime' : included sans observation depuis ttl_s (défaut
      DEFAULT_VETO_TTL_S) ; la donnée périmée vaut unknown, jamais extrapolée ;
    - 'refus-recent' : refus enregistré il y a moins de REFUSAL_COOLDOWN_S."""
    reasons = []
    row = db.execute('SELECT billing_class, updated_at FROM route_admissibility WHERE channel=?',
                     (channel,)).fetchone()
    billing = row[0] if row else 'unknown'
    if billing in ('paid', 'unknown'):
        reasons.append(billing)
    if billing == 'included':
        if ttl_s is None:
            ttl_s = DEFAULT_VETO_TTL_S
        updated_at = row[1]
        if updated_at is None or (time.time() - updated_at) > ttl_s:
            reasons.append('quota-perime')
    recent = db.execute(
        'SELECT 1 FROM route_refusals WHERE channel=? AND ts > ? LIMIT 1',
        (channel, time.time() - REFUSAL_COOLDOWN_S)).fetchone()
    if recent:
        reasons.append('refus-recent')
    return reasons


def populate_from_quota(db, quota_status):
    """Admit included only for a known opaque account with observed usage.

    A newer unknown identity must replace stale included evidence; it cannot
    retain previous account/pool identifiers or silently inherit eligibility.
    """
    status = quota_status if isinstance(quota_status, dict) else {}
    account = status.get('account_key')
    if status.get('identity_status') != 'known' or not _opaque_sha256(account):
        declare(db, 'codex', 'unknown', identity_status='unknown')
        return
    pools = status.get('pools')
    pools = pools if isinstance(pools, list) else []
    pool_key = next((pool['quota_pool_key'] for pool in pools
                     if isinstance(pool, dict) and _opaque_sha256(pool.get('quota_pool_key'))), None)
    billing = 'included' if status.get('ordinary_usage_allowed_observed') is True else 'unknown'
    declare(db, 'codex', billing, account_key=account,
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
    """Pass through only a quota_knowledge-style HMAC-SHA-256 hex digest.

    Reject possible raw account identifiers; shape alone cannot prove HMAC
    provenance, so callers must continue to use quota_knowledge.
    """
    return account_key if _opaque_sha256(account_key) else None


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
