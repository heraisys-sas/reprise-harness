"""Routeur de repli de délégation : substitution automatique entre backends.

Motifs empruntés au routeur LiteLLM (chaînes de repli ordonnées, cooldown
par cible comme PRÉFÉRENCE d'ordre — jamais comme assertion d'indisponibilité) :
https://docs.litellm.ai/docs/router/learn/reliability
https://docs.litellm.ai/docs/routing

Anti-patterns expressément NON adoptés (LiteLLM les documente, nous les
refusons) :
- réessayer la même cible après un refus de quota (quota refusé = travail
  conservé, continuation native sur la route d'origine, cf.
  docs/contracts/QUOTA-CONTINUATION-CONTRACT.md) ;
- repli vers une route payante (aucune facturation implicite).

Déclencheurs (opérateur, arbitrés) : les trois statuts `quota_blocked`,
`billing_unverified`, `engine_unavailable`. EXCEPTION NON NÉGOCIABLE : les
trois portes manifeste (backend désactivé, déclaré sans handler, inconnu du
manifeste) restent des refus définitifs — jamais substitués.

Zéro dépendance : stdlib uniquement.
"""
TRIGGER_STATUSES = ('quota_blocked', 'billing_unverified', 'engine_unavailable')
# Exclusion contractuelle : ironclaw = payant/never auto, même si
# routing_events.LOCAL_BILLING le marque 'free'.
NEVER_AUTO_BACKENDS = ('ironclaw',)


def _manifest_gate_refusal(result):
    """True si le résultat est l'un des trois refus manifeste définitifs
    (#258 lot 2) : ces refus ne sont JAMAIS substitués."""
    reason = result.get('reason')
    if not isinstance(reason, str):
        return False
    return ('désactivé dans experts-manifest.json' in reason
            or 'sans handler de dispatch (#258 lot 2)' in reason
            or 'inconnu du manifeste experts' in reason)


def handled_backends():
    import expert_mcp
    return expert_mcp.EXPERTS_HANDLED_BACKENDS


def foundation_models_ready():
    import apple_expert
    return apple_expert.configured()


def _configured_backends(bridge):
    """Backends avec handler ET configurés sur CE pont (les mêmes conditions
    que les portes de connexion de _call), dans l'ordre du manifeste. Un
    backend non configuré n'est pas un candidat : la substitution évite les
    portes de connexion prévisibles."""
    configured = []
    for backend in handled_backends():
        if backend == 'codex' and bridge.program:
            configured.append(backend)
        elif backend == 'claude-code' and bridge.claude_program:
            configured.append(backend)
        elif backend == 'claude-desktop' and bridge.desktop_tasks is not None:
            configured.append(backend)
        elif backend == 'chatgpt' and bridge.chatgpt_socket and bridge.chatgpt_tasks is not None:
            configured.append(backend)
        elif backend == 'foundation-models' and foundation_models_ready():
            configured.append(backend)
        elif backend == 'metaai' and bridge.metaai_values \
                and bridge.metaai_values.get('enabled', True) is not False \
                and bridge.metaai_tasks is not None:
            configured.append(backend)
    return configured


def _manifest_admits(bridge, backend):
    entry = (bridge.experts or {}).get(backend)
    return entry is None or entry.get('enabled', True)


def _exclude(bridge, requested_backend, configured, exclude=None):
    """Filtre les candidats : cible défaillante, ironclaw (contractuel),
    backend désactivé dans le manifeste courant."""
    candidates = []
    for backend in configured:
        if backend in (requested_backend, exclude) or backend in NEVER_AUTO_BACKENDS:
            continue
        if not _manifest_admits(bridge, backend):
            continue
        candidates.append(backend)
    return candidates


def substitute_for_target(bridge, requested_backend, exclude=None, task_class=None):
    """Choisit le meilleur remplaçant pour une cible défaillante : candidats
    configurés et admissibles (free/included), ordonnés par
    external_rankings.route_choice (prix corroboré le plus bas) ; None si
    aucun candidat mesurable (refus d'origine inchangé). task_class (lot ①)
    est passée telle quelle à route_choice ; branchement automatique hors
    périmètre de ce lot."""
    import external_rankings
    candidates = _exclude(bridge, requested_backend, _configured_backends(bridge), exclude)
    if not candidates:
        return None
    return external_rankings.route_choice(bridge.db, candidates, automatic=True,
                                          task_class=task_class)


def chain_candidates(bridge, chain, exclude=None):
    """Résout la default_chain au temps T : premier backend admissible
    (admissible(automatic=True)) ET configuré ET non exclu ET sans veto
    (refus-recent / quota-perime — même filtre que route_choice, lot 3 #258),
    dans l'ordre déclaré. Retourne None si aucun."""
    import routing_events
    configured = set(_configured_backends(bridge))
    for backend in chain or []:
        if backend in NEVER_AUTO_BACKENDS or backend == exclude:
            continue
        if backend not in configured or not _manifest_admits(bridge, backend):
            continue
        ok, _ = routing_events.admissible(bridge.db, backend, True)
        if not ok:
            continue
        if routing_events.vetos(bridge.db, backend):
            continue  # refus-recent / quota-perime : même filtre que route_choice
        return backend
    return None


def is_trigger(result):
    """True si le résultat est un refus déclencheur (3 statuts arbitrés),
    hors portes manifeste définitives."""
    if not isinstance(result, dict):
        return False
    if _manifest_gate_refusal(result):
        return False
    return result.get('status') in TRIGGER_STATUSES


def trace_substitution(bridge, tool, requested_backend, served_backend, level=None,
                       request_key=None, thread_id=None, reason=None):
    """Journalise une substitution effective : event routing_substituted avec
    requested_route ET served_route non nuls (CHECK schema routing_events v4 ;
    les routes journalisées sont des dicts, ex. {'backend': 'codex'})."""
    import routing_events
    return routing_events.record(bridge.db, 'routing_substituted', tool, served_backend,
                                 requested={'backend': requested_backend},
                                 served={'backend': served_backend},
                                 level=level, request_key=request_key,
                                 thread_id=thread_id,
                                 account_key=bridge._account_key(served_backend),
                                 admissibility='granted', reason=reason)


def annotate(result, requested_backend, served_backend):
    """Marque un résultat substitué : jamais silencieux."""
    return dict(result, automatic_fallback=True,
                requested_route=requested_backend, served_route=served_backend)
