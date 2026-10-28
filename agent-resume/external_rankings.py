"""Cache de classements LLM externes (anti-injection par construction).

Les sources externes sont des DONNÉES, jamais des instructions : le fetcher
n'ingère que des enregistrements numériques conformes à une structure fermée ;
tout texte libre est rejeté avant SQLite. Le journal local routing_events
(mesures réelles) domine toujours : l'externe n'est qu'un prior. Le cache
n'élargit jamais l'admissibilité (routing_events.admissible reste la porte) ;
il informe le choix ENTRE routes déjà admissibles.

Métriques : price_per_mtok (USD/Mtokens), latency_ms, quality_index
(classes de compétences — extensible pour un routage thématique de chat à
terme, non implémenté). Mapping modèle externe → canal : à la lecture via
external_model_map, jamais stocké dupliqué.
"""

import hashlib
import json
import re
import sqlite3
import time
import urllib.request

VERSION = 4
DEFAULT_TTL_SECONDS = 86400

SOURCES = ('artificial-analysis', 'openrouter', 'lmarena')
METRICS = ('price_per_mtok', 'latency_ms', 'quality_index')

# Liste fermée de colonnes ; aucun autre champ n'est jamais ingéré.
_FIELDS = frozenset(('source', 'model', 'metric', 'value', 'fetched_at', 'source_url_hash'))
_MODEL_RE = re.compile(r'^[A-Za-z0-9._-]{1,128}$')

SCHEMA = (
    'CREATE TABLE IF NOT EXISTS external_rankings ('
    'source TEXT NOT NULL CHECK(source IN (\'artificial-analysis\',\'openrouter\',\'lmarena\')), '
    'model TEXT NOT NULL, '
    'metric TEXT NOT NULL CHECK(metric IN (\'price_per_mtok\',\'latency_ms\',\'quality_index\')), '
    'value REAL NOT NULL, '
    'fetched_at REAL NOT NULL, '
    'source_url_hash TEXT, '
    'PRIMARY KEY (source, model, metric))',
    'CREATE INDEX IF NOT EXISTS external_rankings_fresh ON external_rankings(metric, fetched_at)',
    'CREATE TABLE IF NOT EXISTS external_model_map ('
    'external_model TEXT PRIMARY KEY, channel TEXT NOT NULL)',
    'CREATE TABLE IF NOT EXISTS external_rankings_meta('
    'key TEXT PRIMARY KEY, value TEXT NOT NULL)',
)


def setup(db):
    version = db.execute('PRAGMA user_version').fetchone()[0]
    if version > VERSION:
        raise ValueError(f'Schéma external_rankings inconnu : user_version={version}')
    with db:
        for statement in SCHEMA:
            db.execute(statement)
        db.execute(f'PRAGMA user_version = {VERSION}')
    return version


def _url_hash(url):
    return hashlib.sha256(url.encode()).hexdigest()[:16]


def ingest(db, source, records, url=''):
    """Ingère des enregistrements déjà parsés ; rejet strict, tout ou rien
    par enregistrement (les conformes sont écrits, les autres ignorés)."""
    if source not in SOURCES:
        raise ValueError(f'Source inconnue : {source}')
    now = time.time()
    accepted = rejected = 0
    for entry in records:
        try:
            if not isinstance(entry, dict) or not _FIELDS.issuperset(entry):
                raise ValueError('champ inattendu')
            model = entry['model']
            metric = entry['metric']
            value = float(entry['value'])
            if not isinstance(model, str) or not _MODEL_RE.match(model):
                raise ValueError('modèle invalide')
            if metric not in METRICS or value != value or value in (float('inf'), float('-inf')):
                raise ValueError('métrique invalide')
        except (KeyError, TypeError, ValueError):
            rejected += 1
            continue
        with db:
            db.execute('INSERT OR REPLACE INTO external_rankings'
                       '(source,model,metric,value,fetched_at,source_url_hash) '
                       'VALUES (?,?,?,?,?,?)',
                       (source, model, metric, value, now, _url_hash(url) if url else None))
        accepted += 1
    return accepted, rejected


def fetch_url(url, timeout=10):
    """Télécharge et parse ; retourne la liste brute (le filtrage est dans ingest).
    Aucune donnée n'est écrite ici — la séparation fetch/ingest garantit que le
    réseau n'a jamais accès à SQLite."""
    with urllib.request.urlopen(url, timeout=timeout) as response:
        payload = json.loads(response.read().decode('utf-8', errors='replace'))
    if not isinstance(payload, list):
        raise ValueError('Structure inattendue : liste attendue')
    return payload


def ttl(db, source):
    row = db.execute('SELECT value FROM external_rankings_meta WHERE key=?',
                     (f'ttl_{source}',)).fetchone()
    return int(row[0]) if row else DEFAULT_TTL_SECONDS


def stale(db, source):
    row = db.execute('SELECT MAX(fetched_at) FROM external_rankings WHERE source=?',
                     (source,)).fetchone()
    return row is None or row[0] is None or time.time() - row[0] > ttl(db, source)


def refresh(db, source, url):
    """Recharge si le TTL est dépassé ; échec réseau → cache inchangé."""
    if not stale(db, source):
        return None
    try:
        records = fetch_url(url)
    except (OSError, ValueError):
        return None
    return ingest(db, source, records, url=url)


def route_metrics(db, channel):
    """Coût/latence priorisés : (1) journal local routing_events (mesures réelles),
    (2) cache externe si ≥2 sources concordantes, (3) None si insuffisant.
    Divergence >50% entre sources externes → conflict=True, l'externe est
    ignoré au profit du seul journal local. Jamais de substitution."""
    result = {'price_per_mtok': None, 'latency_ms': None, 'quality_index': None,
              'origin': None, 'conflict': False}
    external = {}
    for metric in METRICS:
        rows = db.execute(
            'SELECT value FROM external_rankings r JOIN external_model_map m '
            'ON m.external_model=r.model WHERE m.channel=? AND r.metric=?',
            (channel, metric)).fetchall()
        values = [row[0] for row in rows]
        if len(values) >= 2:
            spread = (max(values) - min(values)) / min(abs(v) for v in values if v)
            if spread > 0.5:
                result['conflict'] = True
                continue
        if values:
            external[metric] = min(values)
    if not result['conflict'] and external:
        result.update(external, origin='external-cache')
    return result


def route_choice(db, channels, automatic=True):
    """Choix ENTRE routes déjà admissibles uniquement. Pré-filtre par
    routing_events.admissible ; automatic=True ne peut retourner qu'une route
    free/included. Retourne None si aucune route admissible ou mesurable."""
    import routing_events
    admissible = [ch for ch in channels if routing_events.admissible(db, ch, automatic)[0]]
    measured = [(ch, route_metrics(db, ch)) for ch in admissible]
    scored = [(ch, m) for ch, m in measured if m['price_per_mtok'] is not None]
    if not scored:
        return None
    return min(scored, key=lambda item: item[1]['price_per_mtok'])[0]
