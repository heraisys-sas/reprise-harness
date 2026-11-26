"""Classement de competence, module pur, sans effet de bord."""
REFUS_RAISONS = ("ENTREE_INVALIDE", "REGISTRE_ABSENT", "FACTURATION_INCONNUE", "ECRITURE_NON_AUTORISEE", "QUOTA_EPUISE", "PAYANT_NON_CONSENTI", "ROLE_INTERDIT", "CHEMIN_SENSIBLE")
MOTIF_CODES = ("ADMISSIBLE", "MESURE_LOCALE", "PRIOR_DEPARTAGE", "QUOTA", "STATIQUE", "REFUS")
_COUT = {"free": 0, "included": 1, "paid-named": 2}
def _note_prior(prior):
    # Extrait la note de prior, -1 si absente ou hors 0 1.
    if prior is None:
        return -1.0
    if isinstance(prior, bool):
        return -1.0
    if isinstance(prior, (int, float)):
        try:
            v = float(prior)
        except Exception:
            return -1.0
        if v < 0.0 or v > 1.0:
            return -1.0
        return v
    if isinstance(prior, dict):
        note = prior.get("note")
        if isinstance(note, bool):
            return -1.0
        if not isinstance(note, (int, float)):
            return -1.0
        try:
            v = float(note)
        except Exception:
            return -1.0
        if v < 0.0 or v > 1.0:
            return -1.0
        return v
    return -1.0
def _mesure_info(stats):
    # Mesurable si stats present, non_mesure faux, wilson present.
    if not isinstance(stats, dict):
        return (None, -1.0, False)
    if stats.get("non_mesure"):
        return (None, -1.0, False)
    iv = stats.get("wilson")
    if iv is None:
        return (None, -1.0, False)
    if not isinstance(iv, (list, tuple)):
        return (None, -1.0, False)
    if len(iv) != 2:
        return (None, -1.0, False)
    bas, haut = iv
    if isinstance(bas, bool) or isinstance(haut, bool):
        return (None, -1.0, False)
    if not isinstance(bas, (int, float)) or not isinstance(haut, (int, float)):
        return (None, -1.0, False)
    taux = stats.get("taux")
    if isinstance(taux, bool) or not isinstance(taux, (int, float)):
        taux_v = -1.0
    else:
        try:
            taux_v = float(taux)
        except Exception:
            taux_v = -1.0
    return ((float(bas), float(haut)), taux_v, True)
def _recouvre(iv_a, iv_b):
    # True si intervalles fermes se recouvrent.
    a, b = iv_a
    c, d = iv_b
    return a <= d and c <= b
def _motif(code, actor, source):
    # Construit un motif de sortie.
    return {"code": code, "actor": actor, "source": source}
def _refus(code, motifs):
    # Construit une sortie de refus.
    return {"choisi": None, "refus": code, "motifs": motifs}
def classer(candidats, demande):
    # Classe les candidats, jamais d exception.
    try:
        return _classer_interne(candidats, demande)
    except Exception:
        return _refus("ENTREE_INVALIDE", [])
def _classer_interne(candidats, demande):
    if not isinstance(demande, dict):
        return _refus("ENTREE_INVALIDE", [])
    role = demande.get("role")
    mode = demande.get("mode")
    sensible = demande.get("sensible")
    if not isinstance(role, str) or not role:
        return _refus("ENTREE_INVALIDE", [])
    if mode not in ("expertise", "implementation"):
        return _refus("ENTREE_INVALIDE", [])
    if not isinstance(sensible, bool):
        return _refus("ENTREE_INVALIDE", [])
    if "kind" in demande and not isinstance(demande["kind"], str):
        return _refus("ENTREE_INVALIDE", [])
    if not isinstance(candidats, list):
        return _refus("ENTREE_INVALIDE", [])
    if len(candidats) == 0:
        return _refus("REGISTRE_ABSENT", [])
    exclusions = []
    admissibles = []
    for idx in range(len(candidats)):
        cand = candidats[idx]
        if not isinstance(cand, dict):
            exclusions.append((idx, "", "ENTREE_INVALIDE"))
            continue
        actor = cand.get("actor")
        if not isinstance(actor, str) or not actor:
            actor_txt = actor if isinstance(actor, str) else ""
            exclusions.append((idx, actor_txt, "ENTREE_INVALIDE"))
            continue
        backend = cand.get("backend")
        if not isinstance(backend, str) or not backend:
            exclusions.append((idx, actor, "ENTREE_INVALIDE"))
            continue
        role_ok = cand.get("role_autorise")
        if role_ok is not True:
            exclusions.append((idx, actor, "ROLE_INTERDIT"))
            continue
        sensible_ok = cand.get("sensible_ok")
        if sensible is True and sensible_ok is not True:
            exclusions.append((idx, actor, "CHEMIN_SENSIBLE"))
            continue
        billing = cand.get("billing_class")
        if billing not in ("free", "included", "paid-named"):
            exclusions.append((idx, actor, "FACTURATION_INCONNUE"))
            continue
        consentie = cand.get("consentie")
        if billing == "paid-named" and consentie is not True:
            exclusions.append((idx, actor, "PAYANT_NON_CONSENTI"))
            continue
        ecriture = cand.get("ecriture")
        if mode == "implementation" and ecriture is not True:
            exclusions.append((idx, actor, "ECRITURE_NON_AUTORISEE"))
            continue
        quota = cand.get("quota")
        if quota is not True:
            exclusions.append((idx, actor, "QUOTA"))
            continue
        iv, taux_v, mesurable = _mesure_info(cand.get("stats"))
        note = _note_prior(cand.get("prior"))
        cout = _COUT.get(billing, 2)
        admissibles.append({"idx": idx, "actor": actor, "billing": billing, "cout": cout, "note": note, "iv": iv, "taux": taux_v, "mesurable": mesurable})
    if len(admissibles) == 0:
        motifs = []
        for idx, actor, code in exclusions:
            motifs.append(_motif(code, actor, "registre"))
        a_quota = False
        for idx, actor, code in exclusions:
            if code == "QUOTA":
                a_quota = True
                break
        if a_quota:
            return _refus("QUOTA_EPUISE", motifs)
        premier = exclusions[0][2]
        return _refus(premier, motifs)
    motifs = []
    for idx, actor, code in exclusions:
        motifs.append(_motif(code, actor, "registre"))
    mesures = []
    non_mesures = []
    for adm in admissibles:
        if adm["mesurable"]:
            mesures.append(adm)
        else:
            non_mesures.append(adm)
    non_mesures.sort(key=lambda x: x["idx"])
    if len(mesures) > 0:
        meilleur = mesures[0]
        for m in mesures[1:]:
            if m["iv"][0] > meilleur["iv"][0]:
                meilleur = m
            elif m["iv"][0] == meilleur["iv"][0] and m["taux"] > meilleur["taux"]:
                meilleur = m
            elif m["iv"][0] == meilleur["iv"][0] and m["taux"] == meilleur["taux"] and m["idx"] < meilleur["idx"]:
                meilleur = m
        groupe = []
        for m in mesures:
            if _recouvre(m["iv"], meilleur["iv"]):
                groupe.append(m)
        groupe.sort(key=lambda x: (-x["note"], x["cout"], x["idx"]))
        gagnant = groupe[0]
        motifs.append(_motif("ADMISSIBLE", gagnant["actor"], "registre"))
        motifs.append(_motif("MESURE_LOCALE", gagnant["actor"], "routing_events"))
        prior_decisif = False
        if len(groupe) > 1:
            max_autre = None
            for m in groupe[1:]:
                if max_autre is None or m["note"] > max_autre:
                    max_autre = m["note"]
            if max_autre is not None and gagnant["note"] > max_autre:
                prior_decisif = True
        if prior_decisif:
            motifs.append(_motif("PRIOR_DEPARTAGE", gagnant["actor"], "prior"))
        for nm in non_mesures:
            motifs.append(_motif("STATIQUE", nm["actor"], "table_roles"))
        return {"choisi": gagnant["actor"], "refus": None, "motifs": motifs}
    gagnant = non_mesures[0]
    motifs.append(_motif("ADMISSIBLE", gagnant["actor"], "registre"))
    for nm in non_mesures:
        motifs.append(_motif("STATIQUE", nm["actor"], "table_roles"))
    return {"choisi": gagnant["actor"], "refus": None, "motifs": motifs}
