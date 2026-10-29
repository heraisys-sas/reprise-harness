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

VERSION = 5
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
    'external_model TEXT NOT NULL, channel TEXT NOT NULL, '
    'PRIMARY KEY (external_model, channel))',
    'CREATE TABLE IF NOT EXISTS external_rankings_meta('
    'key TEXT PRIMARY KEY, value TEXT NOT NULL)',
)


def setup(db):
    version = db.execute('PRAGMA user_version').fetchone()[0]
    if version > VERSION:
        raise ValueError(f'Schéma external_rankings inconnu : user_version={version}')
    with db:
        # v5 : external_model_map passe en clé composite (un modèle peut servir
        # plusieurs canaux). Table de correspondance reconstructible → re-création
        # sans perte (les mappings sont déclaratifs, pas mesurés).
        if version == 4:
            existing = {row[1] for row in db.execute('PRAGMA table_info(external_model_map)')}
            if 'channel' in existing:
                db.execute('DROP TABLE IF EXISTS external_model_map')
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


MAX_RESPONSE_BYTES = 5 * 1024 * 1024


def fetch_url(url, timeout=10):
    """Télécharge et parse ; retourne la liste brute (le filtrage est dans ingest).
    Aucune donnée n'est écrite ici — la séparation fetch/ingest garantit que le
    réseau n'a jamais accès à SQLite. Réponse bornée en taille ; content-type
    JSON exigé."""
    with urllib.request.urlopen(url, timeout=timeout) as response:
        content_type = response.headers.get('Content-Type', '')
        if 'json' not in content_type:
            raise ValueError(f'Content-Type inattendu : {content_type!r}')
        payload = json.loads(response.read(MAX_RESPONSE_BYTES + 1).decode('utf-8', errors='replace'))
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


# Mappings initiaux canal → IDs externes, relevés sur les flux réels le
# 29/10/2026 (AA : 716 modèles, OpenRouter : 457). Les IDs OpenRouter sont
# déjà normalisés (vendor/model → vendor_model) : c'est la forme que
# l'adaptateur écrit dans le cache. À affiner avec le journal local.
CHANNEL_MODEL_MAP = {
    'codex': ('openai_gpt-6.1-sol-pro', 'gpt-6-1-sol',
              'openai_gpt-6.1-terra', 'gpt-6-1-terra'),
    'claude-code': ('anthropic_claude-opus-5', 'claude-opus-5',
                    'anthropic_claude-opus-4.8'),
    'claude-desktop': ('anthropic_claude-opus-5', 'claude-opus-5'),
    'foundation-models': (),  # local : aucune donnée externe pertinente
    'ironclaw': (),
    'super-decider': (),      # classifieur interne : pas de classement public
    'asi1': (),               # pas de classement public fiable
    'asi1-mini': (),
}


def populate_model_map(db):
    """Insère les mappings initiaux canal → modèle externe, idempotent.
    Jointure sur le nom de modèle seul : route_metrics agrège déjà toutes
    les sources, et les flux AA/OpenRouter partagent les mêmes identifiants."""
    inserted = 0
    for channel, externals in CHANNEL_MODEL_MAP.items():
        for model in externals:
            row = db.execute('SELECT 1 FROM external_model_map '
                             'WHERE external_model=? AND channel=?',
                             (model, channel)).fetchone()
            if not row:
                db.execute('INSERT INTO external_model_map VALUES (?,?)',
                           (model, channel))
                inserted += 1
    db.commit()
    return inserted


def refresh_by_source(db, source):
    """Dispatch par source : stale → fetch (adaptateur) → transform → ingest.
    Le réseau n'écrit jamais directement — ingest revalide tout. Retour :
    (accepted, rejected), ou None si pas stale, ou None en échec réseau."""
    if source not in ('openrouter', 'artificial-analysis'):
        raise ValueError(f'Source inconnue : {source}')
    if not stale(db, source):
        return None
    try:
        if source == 'openrouter':
            import openrouter_adapter
            payload = openrouter_adapter.fetch()
            records = openrouter_adapter.transform(payload)
        else:
            import artificial_analysis_adapter
            payload = artificial_analysis_adapter.fetch()
            records = artificial_analysis_adapter.transform(payload)
    except (OSError, ValueError):
        return None
    return ingest(db, source, records)


def route_metrics(db, channel):
    """Coût/latence priorisés : (1) journal local routing_events (mesures
    réelles), (2) cache externe quand une même métrique est corroborée par
    ≥2 sources pour un même modèle, (3) None si insuffisant.

    La concordance se juge PAR MODÈLE entre sources : comparer les valeurs de
    modèles différents n'a pas de sens (AA publie un prix blended 3:1,
    OpenRouter un prix d'entrée — l'écart est normal, pas un conflit).
    Divergence >50 % sur un même modèle → conflict=True et l'externe est
    ignoré au profit du seul journal local. Jamais de substitution."""
    result = {'price_per_mtok': None, 'latency_ms': None, 'quality_index': None,
              'origin': None, 'conflict': False}
    external = {}
    for metric in METRICS:
        rows = db.execute(
            'SELECT r.source, r.model, r.value FROM external_rankings r '
            'JOIN external_model_map m ON m.external_model=r.model '
            'WHERE m.channel=? AND r.metric=?', (channel, metric)).fetchall()
        per_model = {}
        for source, model, value in rows:
            per_model.setdefault(model, {})[source] = value
        values = []
        for model, by_source in per_model.items():
            corroborated = [v for v in by_source.values() if v]
            if len(by_source) >= 2 and corroborated:
                spread = ((max(corroborated) - min(corroborated))
                          / min(abs(v) for v in corroborated))
                if spread > 0.5:
                    result['conflict'] = True
                    break
            values.extend(by_source.values())
        if result['conflict']:
            external.clear()
            break
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
