"""Adaptateur source OpenRouter → external_rankings.

Correspondance déclarative uniquement : unwrap de l'enveloppe {"data":[...]},
normalisation des IDs « vendor/model » → « vendor_model » (le fetcher refuse
les slashs par conception), conversion prix USD/token → USD/Mtokens (×1e6),
mapping intelligence_index → quality_index. Aucune règle anti-injection ici :
le fetcher/ingest reste la seule porte — l'adaptateur ne fait que transformer,
et ingest revalide tout.

Source : test réel du 29/10/2026 (openrouter.ai/api/v1/models, 457 modèles,
prix en chaînes USD/token, benchmarks.artificial_analysis.intelligence_index
présent pour une partie des modèles seulement).
"""

import json
import re
import urllib.request

MODELS_URL = 'https://openrouter.ai/api/v1/models'
_SLASH_RE = re.compile(r'[^A-Za-z0-9._-]')


def unwrap(payload):
    """{"data":[...]} → [...]. Lève ValueError si la forme est inattendue."""
    if isinstance(payload, dict) and isinstance(payload.get('data'), list):
        return payload['data']
    if isinstance(payload, list):
        return payload
    raise ValueError('Enveloppe OpenRouter inattendue')


def normalize_model(model):
    """vendor/model → vendor_model (fetcher refuse les slashs)."""
    if not isinstance(model, str):
        raise ValueError('modèle non textuel')
    return _SLASH_RE.sub('_', model)[:128]


def _price_per_mtok(pricing):
    value = float(pricing['prompt']) * 1e6
    return value


def fetch(timeout=20):
    """Télécharge et déballe l'enveloppe {"data":[...]}. Le réseau n'écrit
    jamais : ingest() revalide tout. fetch_url() de external_rankings exige une
    liste racine et ne peut donc pas servir ici (l'API OpenRouter renvoie un
    objet) — c'est l'adaptateur qui assume le déballage."""
    request = urllib.request.Request(MODELS_URL, headers={'Accept': 'application/json'})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        content_type = response.headers.get('Content-Type', '')
        if 'json' not in content_type:
            raise ValueError(f'Content-Type inattendu : {content_type!r}')
        payload = json.loads(response.read().decode('utf-8', errors='replace'))
    return unwrap(payload)


def transform(payload, fetched_at=None):
    """Enveloppe OpenRouter → enregistrements conformes à ingest().
    Les modèles sans prix pertinent sont ignorés (pas d'invention de valeur)."""
    records = []
    for entry in unwrap(payload):
        try:
            model = normalize_model(entry.get('id'))
            pricing = entry.get('pricing') or {}
            if pricing.get('prompt') not in (None, ''):
                records.append({'model': model, 'metric': 'price_per_mtok',
                                'value': _price_per_mtok(pricing),
                                **({'fetched_at': fetched_at} if fetched_at else {})})
            index = ((entry.get('benchmarks') or {})
                     .get('artificial_analysis') or {}).get('intelligence_index')
            if index not in (None, ''):
                records.append({'model': model, 'metric': 'quality_index',
                                'value': float(index),
                                **({'fetched_at': fetched_at} if fetched_at else {})})
        except (KeyError, TypeError, ValueError):
            continue  # entrée non exploitable : ignorée, ingest revalide le reste
    return records
