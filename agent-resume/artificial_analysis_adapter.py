"""Adaptateur source Artificial Analysis -> external_rankings.

Endpoint reel valide le 29/10/2026 : GET /api/v2/data/llms/models (tier free,
x-api-key requise - la cle est stockee hors depot, permissions 600 ; la route
/language/models exige Pro, a ne pas utiliser). Structure verifiee : dict a
racine liste de 716 modeles, name/slug, pricing.price_1m_input_tokens et
price_1m_output_tokens (USD/Mtokens, pas de conversion),
median_time_to_first_token_seconds, evaluations.artificial_analysis_
intelligence_index.

Correspondance declarative uniquement : ingest() revalide tout.
"""

import json
import os
import re
import urllib.request

MODELS_URL = 'https://artificialanalysis.ai/api/v2/data/llms/models'
_SLUG_RE = re.compile(r'^[A-Za-z0-9._-]{1,128}$')


def key_path():
    root = os.environ.get('REPRISE_SUPPORT') or os.path.expanduser(
        '~/Library/Application Support/Reprise')
    return os.path.join(root, 'credentials', 'artificial-analysis.key')


def load_key(path=None):
    """Cle depuis le stockage hors depot ; ValueError si absente (jamais de
    cle par defaut, jamais de cle dans le depot)."""
    path = path or key_path()
    try:
        with open(path, encoding='utf-8') as handle:
            key = handle.read().strip()
    except OSError as exc:
        raise ValueError(f'Cle Artificial Analysis introuvable : {exc}') from exc
    if not key:
        raise ValueError('Cle Artificial Analysis vide')
    return key


def fetch(timeout=30):
    """Telecharge la liste des modeles avec authentification ; retourne la
    liste brute. La separation reseau/SQLite reste integrale : rien n'est
    ecrit ici - ingest() revalide tout."""
    request = urllib.request.Request(
        MODELS_URL, headers={'x-api-key': load_key(), 'Accept': 'application/json'})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        content_type = response.headers.get('Content-Type', '')
        if 'json' not in content_type:
            raise ValueError(f'Content-Type inattendu : {content_type!r}')
        payload = json.loads(response.read().decode('utf-8', errors='replace'))
    if isinstance(payload, dict) and isinstance(payload.get('data'), list):
        payload = payload['data']
    if not isinstance(payload, list):
        raise ValueError('Structure inattendue : liste attendue')
    return payload


def _model_id(entry):
    """slug -> identifiant conforme au fetcher (^[A-Za-z0-9._-]{1,128}$) ;
    None si le slug est absent ou non normalisable."""
    slug = str(entry.get('slug') or (entry.get('name') or '').replace(' ', '_'))
    return slug if _SLUG_RE.match(slug) else None


def transform(payload, fetched_at=None):
    """Entrees AA -> enregistrements conformes a ingest(). Les valeurs
    absentes sont ignorees (on n'invente jamais)."""
    records = []
    for entry in payload:
        model = _model_id(entry)
        if not model:
            continue
        stamp = {'fetched_at': fetched_at} if fetched_at else {}
        try:
            blended = (entry.get('pricing') or {}).get('price_1m_blended_3_to_1')
            if blended not in (None, ''):
                records.append({'model': model, 'metric': 'price_per_mtok',
                                'value': float(blended), **stamp})
            ttft = entry.get('median_time_to_first_token_seconds')
            if ttft not in (None, ''):
                records.append({'model': model, 'metric': 'latency_ms',
                                'value': float(ttft) * 1000.0, **stamp})
            index = ((entry.get('evaluations') or {})
                     .get('artificial_analysis_intelligence_index'))
            if index not in (None, ''):
                records.append({'model': model, 'metric': 'quality_index',
                                'value': float(index), **stamp})
        except (KeyError, TypeError, ValueError):
            continue
    return records
