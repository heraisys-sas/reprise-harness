"""Cache de classements LLM externes (anti-injection par construction).

Les sources externes sont des DONNÉES, jamais des instructions : le fetcher
n'ingère que des enregistrements numériques conformes à une structure fermée ;
tout texte libre est rejeté avant SQLite. Le journal local routing_events
(mesures réelles) est censé dominer : l'externe n'est qu'un prior — priorité
NON implémentée à ce jour, voir route_metrics. Le cache
n'élargit jamais l'admissibilité (routing_events.admissible reste la porte) ;
il informe le choix ENTRE routes déjà admissibles.

Métriques : price_per_mtok (USD/Mtokens), latency_ms, ttft_ms, quality_index
(classes de compétences — extensible pour un routage thématique de chat à
terme, non implémenté). latency_ms n'accueille qu'une latence PERÇUE (temps
de réponse mesuré) ; ttft_ms accueille le time-to-first-token publié par une
source externe — un TTFT d'API n'est pas la latence perçue de la route.
Mapping modèle externe → canal : à la lecture via external_model_map, jamais
stocké dupliqué.
"""

import hashlib
import json
import re
import sqlite3
import time
import urllib.request

VERSION = 6
DEFAULT_TTL_SECONDS = 86400

SOURCES = ('artificial-analysis', 'openrouter', 'lmarena')
METRICS = ('price_per_mtok', 'latency_ms', 'ttft_ms', 'quality_index')

# Liste fermée de colonnes ; aucun autre champ n'est jamais ingéré.
_FIELDS = frozenset(('source', 'model', 'metric', 'value', 'fetched_at', 'source_url_hash'))
_MODEL_RE = re.compile(r'^[A-Za-z0-9._-]{1,128}$')

# Les listes fermées des CHECK sont générées depuis SOURCES/METRICS : la
# contrainte SQL et le filtre d'ingest ne peuvent pas diverger.
_RANKINGS_TABLE = (
    'CREATE TABLE IF NOT EXISTS external_rankings ('
    'source TEXT NOT NULL CHECK(source IN ('
    + ','.join(f"'{source}'" for source in SOURCES) + ')), '
    'model TEXT NOT NULL, '
    'metric TEXT NOT NULL CHECK(metric IN ('
    + ','.join(f"'{metric}'" for metric in METRICS) + ')), '
    'value REAL NOT NULL, '
    'fetched_at REAL NOT NULL, '
    'source_url_hash TEXT, '
    'PRIMARY KEY (source, model, metric))')

SCHEMA = (
    _RANKINGS_TABLE,
    'CREATE INDEX IF NOT EXISTS external_rankings_fresh ON external_rankings(metric, fetched_at)',
    'CREATE TABLE IF NOT EXISTS external_model_map ('
    'external_model TEXT NOT NULL, channel TEXT NOT NULL, '
    'PRIMARY KEY (external_model, channel))',
    'CREATE TABLE IF NOT EXISTS external_rankings_meta('
    'key TEXT PRIMARY KEY, value TEXT NOT NULL)',
)


def _recreate_rankings(db):
    """v5 → v6 : ajouter ttft_ms au CHECK(metric) impose de recréer la table.

    Les lignes latency_ms de la source 'artificial-analysis' deviennent ttft_ms :
    cet adaptateur y écrivait median_time_to_first_token_seconds, jamais une
    latence perçue. Les latency_ms des autres sources restent telles quelles :
    elles peuvent publier une vraie latence mesurée, et requalifier une valeur
    non identifiable au hasard serait une perte d'information silencieuse.
    """
    # Pas de DROP préalable de external_rankings_v5 : un résidu ferait échouer le
    # RENAME (visible) plutôt que d'écraser des données existantes en silence.
    db.execute('ALTER TABLE external_rankings RENAME TO external_rankings_v5')
    db.execute(_RANKINGS_TABLE)
    db.execute(
        'INSERT OR REPLACE INTO external_rankings'
        '(source,model,metric,value,fetched_at,source_url_hash) '
        'SELECT source, model, '
        "CASE WHEN metric='latency_ms' AND source='artificial-analysis' "
        "THEN 'ttft_ms' ELSE metric END, "
        'value, fetched_at, source_url_hash FROM external_rankings_v5')
    db.execute('DROP TABLE external_rankings_v5')


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
        # Détection sur le SQL réel de la table, pas sur user_version seul : une
        # table au CHECK périmé est recréée même si la version avait déjà bougé.
        row = db.execute("SELECT sql FROM sqlite_master WHERE type='table' "
                         "AND name='external_rankings'").fetchone()
        if row and 'ttft_ms' not in (row[0] or ''):
            _recreate_rankings(db)
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
    except (OSError, ValueError) as exc:
        # Classe seule (jamais le message : urllib y met l'URL) ; lue par
        # refresh_due pour tracer la cause sans exposer de contenu.
        _meta_set(db, f'refresh_error_class_{source}', type(exc).__name__)
        return None
    return ingest(db, source, records)


# Sources réellement rafraîchissables (lmarena figure dans SOURCES mais n'a
# pas d'adaptateur : refresh_by_source lève ValueError, refresh_due l'ignore).
REFRESH_SOURCES = ('artificial-analysis', 'openrouter')
# Backoff : 1×, 2×, 4× l'intervalle de base (ttl de la source), puis plafond 4×.
BACKOFF_CAP_FACTOR = 4
# Bail anti-double écriture : plus long que les timeouts réseau (10–30 s).
REFRESH_LEASE_SECONDS = 120


def _meta(db, key, default=None):
    row = db.execute('SELECT value FROM external_rankings_meta WHERE key=?', (key,)).fetchone()
    return row[0] if row else default


def _meta_set(db, key, value):
    with db:
        db.execute('INSERT OR REPLACE INTO external_rankings_meta VALUES (?,?)', (key, str(value)))


def _failures(db, source):
    try:
        return max(0, int(_meta(db, f'refresh_failures_{source}', '0')))
    except (TypeError, ValueError):
        return 0


def backoff_delay(base, failures):
    """Délai minimal entre deux tentatives après `failures` échecs consécutifs.
    0 sans échec : le TTL reste la seule condition de déclenchement."""
    if not failures:
        return 0
    return int(base) * min(2 ** (failures - 1), BACKOFF_CAP_FACTOR)


def _wait_seconds(db, source, now):
    """Temps restant avant nouvelle tentative, 0 si le backoff est écoulé.
    Un last_attempt ancien (machine en veille, timer arrêté) donne 0 : la
    reprise est immédiate dès que stale() est vrai."""
    failures = _failures(db, source)
    if not failures:
        return 0
    try:
        last = float(_meta(db, f'refresh_attempt_{source}', '0') or 0)
    except (TypeError, ValueError):
        return 0
    return max(0.0, last + backoff_delay(ttl(db, source), failures) - now)


def _claim(db, source, now):
    """Bail atomique : True si ce tick possède le rafraîchissement.

    INSERT OR IGNORE puis UPDATE conditionnel dans une même transaction
    d'écriture ; SQLite sérialise, donc un second tick concurrent voit un bail
    futur et repart en 'skipped' sans doubler le réseau ni les écritures."""
    key = f'refresh_lease_{source}'
    with db:
        db.execute('INSERT OR IGNORE INTO external_rankings_meta VALUES (?,?)', (key, '0'))
        cursor = db.execute(
            'UPDATE external_rankings_meta SET value=? WHERE key=? AND CAST(value AS REAL)<=?',
            (str(now + REFRESH_LEASE_SECONDS), key, now))
        return cursor.rowcount == 1


def _record_failure(db, source, now, code):
    """Trace d'échec anonymisée P0 : type/classe seulement, jamais le message
    (urllib y inclut l'URL) ni un contenu de réponse."""
    failures = _failures(db, source) + 1
    with db:
        db.execute('INSERT OR REPLACE INTO external_rankings_meta VALUES (?,?)',
                   (f'refresh_failures_{source}', str(failures)))
        db.execute('INSERT OR REPLACE INTO external_rankings_meta VALUES (?,?)',
                   (f'refresh_attempt_{source}', str(now)))
        db.execute('INSERT OR REPLACE INTO external_rankings_meta VALUES (?,?)',
                   (f'last_error_{source}', str(code)[:64]))
    return failures


def _record_success(db, source, now):
    with db:
        db.execute('INSERT OR REPLACE INTO external_rankings_meta VALUES (?,?)',
                   (f'last_refresh_{source}', str(now)))
        db.execute('INSERT OR REPLACE INTO external_rankings_meta VALUES (?,?)',
                   (f'refresh_attempt_{source}', str(now)))
        db.execute('INSERT OR REPLACE INTO external_rankings_meta VALUES (?,?)',
                   (f'refresh_failures_{source}', '0'))
        db.execute('DELETE FROM external_rankings_meta WHERE key=?',
                   (f'refresh_error_class_{source}',))
        db.execute('DELETE FROM external_rankings_meta WHERE key=?',
                   (f'last_error_{source}',))


def refresh_due(db, sources=None, now=None):
    """Rafraîchit les sources dues, avec tolérance de panne intégrale.

    INVARIANT : aucun échec (réseau, 403, clé absente, timeout, valeur,
    exception inattendue) ne remonte à l'appelant et aucun ne vide le cache —
    le cache périmé reste servi, l'échec est seulement tracé. Le TTL (`stale`)
    est la seule condition de déclenchement ; le backoff ne fait que différer
    une tentative. Retour : {source: {'status': 'ok'|'skipped'|'failed',
    'accepted': int, 'rejected': int, 'failures': int, 'reason': str|None}}.

    Branchement timer (2 lignes, non câblé ici) : appeler refresh_due(db) dans
    la branche `tick` de `resume`, à côté de run_due(db, executable) ; échec et
    lenteur sont déjà contenus, l'appel est idempotent et borné par le TTL.
    """
    now = time.time() if now is None else float(now)
    report = {}
    for source in (REFRESH_SOURCES if sources is None else sources):
        entry = {'status': 'skipped', 'accepted': 0, 'rejected': 0,
                 'failures': _failures(db, source), 'reason': None}
        report[source] = entry
        if source not in REFRESH_SOURCES:
            entry['reason'] = 'unknown-source'
            continue
        try:
            if not stale(db, source):
                entry['reason'] = 'fresh'
                continue
            if _wait_seconds(db, source, now) > 0:
                entry['reason'] = 'backoff'
                continue
            if not _claim(db, source, now):
                entry['reason'] = 'concurrent'
                continue
            try:
                result = refresh_by_source(db, source)
            finally:
                _meta_set(db, f'refresh_lease_{source}', 0)
            if result is None:
                # refresh_by_source a déjà absorbé l'exception : on récupère la
                # classe qu'il a tracée (sans message) pour un diagnostic utile.
                reason = _meta(db, f'refresh_error_class_{source}', 'refresh-unavailable')
                entry.update(status='failed', reason=reason)
                entry['failures'] = _record_failure(db, source, now, reason)
                continue
            accepted, rejected = result
            _record_success(db, source, now)
            entry.update(status='ok', accepted=accepted, rejected=rejected, failures=0)
        except Exception as exc:  # tout échec est contenu et tracé
            entry.update(status='failed', reason=type(exc).__name__)
            try:
                entry['failures'] = _record_failure(db, source, now, type(exc).__name__)
            except Exception:
                pass
    return report


def refresh_meta(db, source):
    """Vue consultable de la trace de rafraîchissement d'une source (test/UI)."""
    return {'last_refresh': _meta(db, f'last_refresh_{source}'),
            'last_attempt': _meta(db, f'refresh_attempt_{source}'),
            'last_error': _meta(db, f'last_error_{source}'),
            'failures': _failures(db, source)}


def route_metrics(db, channel):
    """Métriques par canal : cache externe quand une même métrique est
    corroborée par ≥2 sources pour un même modèle, None si insuffisant.

    ÉTAT RÉEL : la priorité au journal local routing_events annoncée en tête de
    module N'EST PAS implémentée ici. Cette fonction ne lit que
    external_rankings ; routing_events n'expose d'ailleurs aucune colonne de
    durée ou de TTFT à consommer. Toute valeur non nulle retournée vient donc de
    l'externe. Le journal local garde son rôle de porte d'admissibilité via
    admissible(), pas de source de métriques.

    La concordance se juge PAR MODÈLE entre sources : comparer les valeurs de
    modèles différents n'a pas de sens (AA publie un prix blended 3:1,
    OpenRouter un prix d'entrée — l'écart est normal, pas un conflit).
    Divergence >50 % sur un même modèle → conflict=True et toutes les valeurs
    externes sont abandonnées (None partout) : jamais de moyenne, jamais de
    substitution.

    Une clé par entrée de METRICS est toujours présente (dont ttft_ms)."""
    result = dict.fromkeys(METRICS)
    result.update(origin=None, conflict=False)
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
