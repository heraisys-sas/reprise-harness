"""Registre unique des backends de délégation (pré-lot A0, décision 25/11/2026).

Une seule source de vérité pour : classe de facturation déclarée, modes
admis, écriture workspace, éligibilité au consentement opérateur et au
snapshot mémoire. Avant ce registre, trois sources divergentes coexistaient :
la garde codée en dur de coordinator_jobs.consent(), le dictionnaire
LOCAL_BILLING de routing_events, et les snapshots de capacité fournis par
l'hôte — d'où le bug latent D2 (consent() acceptait « codex » que
LOCAL_BILLING ne connaît pas).

Règles (revue Meta AI + Claude Desktop, dossier plan 7 lots) :
- fail-closed : un backend absent du registre n'existe pas (facturation
  « unknown », aucun mode, pas de consentement) ;
- « codex » n'est JAMAIS « free » par défaut : sa facturation reste
  « unknown » tant qu'une observation quota (routing_events.populate_from_quota)
  ne l'a pas portée à « included » ; tant qu'elle est unknown, il n'est pas
  éligible au routage automatique, seulement au choix explicite ;
- l'écriture workspace est refusée par défaut (défaut fermé) : le grant
  opérateur #104 sera posé explicitement au lot A, jamais déduit ;
- jalon 1 du pilote Apple : expertise seule (décision Q2) ; l'implémentation
  reste déclarée pour codex mais reste bloquée faute de grant installé.
"""
VERSION = 1

# Mêmes classes que routing_events.BILLING_CLASSES ; tuple local pour éviter
# tout import inverse (routing_events importe ce module).
BILLING_CLASSES = ('free', 'included', 'paid', 'unknown')

BACKENDS = {
    # Modèle local Apple (AFM) : gratuit par construction, expertise seule au
    # jalon 1, seul backend admettant une sélection mémoire (consentement local).
    'foundation-models': {
        'billing': 'free', 'modes': ('expertise',), 'workspace_write': False,
        'consent': True, 'memory_snapshot': True,
    },
    # codex : facturation unknown par défaut (jamais free par défaut) ;
    # implementation reste déclarée car le mode est prévu par le contrat,
    # mais workspace_write reste False tant que le grant #104 n'est pas installé.
    'codex': {
        'billing': 'unknown', 'modes': ('expertise', 'implementation'),
        'workspace_write': False, 'consent': True, 'memory_snapshot': False,
    },
    # Canaux de chat connus du routage local (commentaire du 25/11/2026,
    # Olivier : chatgpt reste ouvert et gratuit même quand la jauge codex est
    # épuisée ; asi1/asi1-mini, route CUDOS, toujours gratuites). Hors
    # périmètre consentement pilote pour l'instant : fail-closed.
    'ironclaw': {
        'billing': 'free', 'modes': ('expertise',), 'workspace_write': False,
        'consent': False, 'memory_snapshot': False,
    },
    'chatgpt': {
        'billing': 'free', 'modes': ('expertise',), 'workspace_write': False,
        'consent': False, 'memory_snapshot': False,
    },
    'asi1': {
        'billing': 'free', 'modes': ('expertise',), 'workspace_write': False,
        'consent': False, 'memory_snapshot': False,
    },
    'asi1-mini': {
        'billing': 'free', 'modes': ('expertise',), 'workspace_write': False,
        'consent': False, 'memory_snapshot': False,
    },
}

# Routes locales certaines : les entrées déclarées « free » du registre.
# Consommé par routing_events (seed_local) — contenu identique à l'ancien
# dictionnaire codé en dur, désormais dérivé d'une seule source.
LOCAL_BILLING = {name: item['billing'] for name, item in BACKENDS.items()
                 if item['billing'] == 'free'}


def entry(backend):
    """Entrée complète d'un backend, ou None si inconnu du registre."""
    if not isinstance(backend, str):
        return None
    return BACKENDS.get(backend)


def billing(backend):
    """Classe de facturation déclarée ; « unknown » pour tout inconnu."""
    item = entry(backend)
    return item['billing'] if item else 'unknown'


def known(backend):
    """True seulement si le backend est déclaré dans le registre."""
    return entry(backend) is not None


def consent_backends():
    """Backends éligibles au consentement opérateur (consent())."""
    return tuple(name for name, item in BACKENDS.items() if item['consent'])


def require_consent_backend(backend):
    """Garde de consentement : même message que la garde historique, source unique."""
    item = entry(backend)
    if item is None or not item['consent']:
        raise ValueError('Explicit supported consent backend required')
    return item


def capability_entry(backend, *, workspace_write_authorized=False):
    """Entrée de snapshot de capacité (forme consommée par coordinator_jobs
    ._authority et coordinator_worker._capability).

    Le registre déclare ce qu'un backend EST ; l'autorisation d'écriture est
    un grant opérateur posé hors registre (#104, lot A) : elle reste False
    sauf paramètre explicite, et refuse tout backend inconnu (fail-closed).
    """
    item = entry(backend)
    if item is None:
        raise ValueError('Unknown backend requested from registry')
    if not isinstance(workspace_write_authorized, bool):
        raise ValueError('Invalid workspace write authorization')
    if workspace_write_authorized and 'implementation' not in item['modes']:
        raise ValueError('Workspace write requires an implementation-capable backend')
    return {'id': backend, 'configured': True, 'modes': list(item['modes']),
            'workspace_write_authorized': bool(workspace_write_authorized)}
