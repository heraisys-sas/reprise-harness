"""Types et gardes pour la competence O1, lot L1c."""
import hashlib
import json
SPEC_ID = "spec-e3-o1-competence-20261126@1.1"
REFUS_RAISONS = ("ENTREE_INVALIDE", "REGISTRE_ABSENT", "FACTURATION_INCONNUE", "ECRITURE_NON_AUTORISEE", "QUOTA_EPUISE", "PAYANT_NON_CONSENTI", "ROLE_INTERDIT", "CHEMIN_SENSIBLE")
MOTIF_CODES = ("ADMISSIBLE", "MESURE_LOCALE", "PRIOR_DEPARTAGE", "QUOTA", "STATIQUE", "REFUS")
_CHAMPS_INTERDITS = frozenset(["request_key", "retry_of", "e2_ref", "context_paths", "allowed_paths", "prohibited", "acceptance", "output_form", "depends_on", "budget", "consent_ref", "quota_ceiling"])
_HEX = set("0123456789abcdefABCDEF")
def digest_entree(entree):
    # Digest sha256 du JSON canonique.
    canonique = json.dumps(entree, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonique.encode("utf-8")).hexdigest()
def rejete_champs_interdits(sortie):
    # True si une cle de premier niveau est interdite.
    if not isinstance(sortie, dict):
        return False
    for cle in sortie:
        if cle in _CHAMPS_INTERDITS:
            return True
    return False
def exclut_confiance(valeur):
    # True si la cle confiance apparait n importe ou.
    pile = [valeur]
    vus = set()
    while pile:
        courant = pile.pop()
        if isinstance(courant, dict):
            if id(courant) in vus:
                continue
            vus.add(id(courant))
            if "confiance" in courant:
                return True
            for v in courant.values():
                pile.append(v)
        elif isinstance(courant, (list, tuple)):
            if id(courant) in vus:
                continue
            vus.add(id(courant))
            for v in courant:
                pile.append(v)
    return False
def _est_hex(valeur, longueur):
    # True si chaine hex de la longueur attendue.
    if not isinstance(valeur, str):
        return False
    if len(valeur) != longueur:
        return False
    for c in valeur:
        if c not in _HEX:
            return False
    return True
def _choix_valide(choix):
    # True si un choix actor backend billing est valide.
    if not isinstance(choix, dict):
        return False
    if set(choix.keys()) != set(["actor", "backend", "billing_class"]):
        return False
    if not isinstance(choix["actor"], str) or not choix["actor"]:
        return False
    if not isinstance(choix["backend"], str) or not choix["backend"]:
        return False
    if choix["billing_class"] not in ("free", "included", "paid-named"):
        return False
    return True
def valide_sortie(sortie):
    # Valide une sortie O1, jamais d exception.
    try:
        return _valide_sortie_interne(sortie)
    except Exception:
        return (False, ["validation-erreur"])
def _valide_sortie_interne(sortie):
    raisons = []
    if not isinstance(sortie, dict):
        return (False, ["sortie-invalide"])
    if set(sortie.keys()) != set(["spec", "base_sha", "demande", "decision", "tracabilite", "refus"]):
        raisons.append("cles-top-invalides")
    if "spec" in sortie and sortie["spec"] != SPEC_ID:
        raisons.append("spec-faux")
    if "base_sha" in sortie and not _est_hex(sortie["base_sha"], 40):
        raisons.append("base-sha-invalide")
    if exclut_confiance(sortie):
        raisons.append("confiance-presente")
    demande = sortie.get("demande")
    if not isinstance(demande, dict):
        raisons.append("demande-invalide")
    else:
        if set(demande.keys()) != set(["role", "mode", "kind", "sensible"]):
            raisons.append("demande-cles-invalides")
        if "mode" in demande and demande["mode"] not in ("expertise", "implementation"):
            raisons.append("demande-mode-invalide")
        if "sensible" in demande and not isinstance(demande["sensible"], bool):
            raisons.append("demande-sensible-invalide")
        if "role" in demande and (not isinstance(demande["role"], str) or not demande["role"]):
            raisons.append("demande-role-invalide")
        if "kind" in demande and (not isinstance(demande["kind"], str) or not demande["kind"]):
            raisons.append("demande-kind-invalide")
    decision = sortie.get("decision")
    if not isinstance(decision, dict):
        raisons.append("decision-invalide")
    else:
        if set(decision.keys()) != set(["choisi", "secondaires", "motifs"]):
            raisons.append("decision-cles-invalides")
        if "choisi" in decision:
            choisi = decision["choisi"]
            if choisi is None:
                if sortie.get("refus") is None:
                    raisons.append("choisi-sans-refus")
            elif not _choix_valide(choisi):
                raisons.append("choisi-invalide")
        if "secondaires" in decision:
            secondaires = decision["secondaires"]
            if not isinstance(secondaires, list):
                raisons.append("secondaires-invalides")
            elif len(secondaires) > 3:
                raisons.append("secondaires-trop-nombreux")
            else:
                for elem in secondaires:
                    if not _choix_valide(elem):
                        raisons.append("secondaires-element-invalide")
                        break
        if "motifs" in decision:
            motifs = decision["motifs"]
            if not isinstance(motifs, list):
                raisons.append("motifs-invalides")
            else:
                for motif in motifs:
                    if not isinstance(motif, dict):
                        raisons.append("motif-invalide")
                        continue
                    if set(motif.keys()) != set(["code", "valeur", "source"]):
                        raisons.append("motif-cles-invalides")
                        continue
                    if motif["code"] not in MOTIF_CODES:
                        raisons.append("motif-code-inconnu")
                    valeur = motif["valeur"]
                    if isinstance(valeur, str) or not isinstance(valeur, (int, float, bool)):
                        raisons.append("motif-valeur-invalide")
                    source = motif["source"]
                    if not isinstance(source, dict):
                        raisons.append("motif-source-invalide")
                    else:
                        if set(source.keys()) != set(["type", "ref"]):
                            raisons.append("motif-source-invalide")
                        else:
                            if source["type"] not in ("registre", "routing_events", "prior", "table_roles"):
                                raisons.append("motif-source-type-inconnu")
                            if not isinstance(source["ref"], str) or not source["ref"]:
                                raisons.append("motif-source-ref-invalide")
    tracabilite = sortie.get("tracabilite")
    if not isinstance(tracabilite, dict):
        raisons.append("tracabilite-invalide")
    else:
        if set(tracabilite.keys()) != set(["sample_count", "fenetre", "non_mesure", "incertains", "digest_entree"]):
            raisons.append("tracabilite-cles-invalides")
        if "non_mesure" in tracabilite and not isinstance(tracabilite["non_mesure"], bool):
            raisons.append("tracabilite-non-mesure-invalide")
        if "digest_entree" in tracabilite and not _est_hex(tracabilite["digest_entree"], 64):
            raisons.append("tracabilite-digest-invalide")
        if "sample_count" in tracabilite and (not isinstance(tracabilite["sample_count"], int) or isinstance(tracabilite["sample_count"], bool)):
            raisons.append("tracabilite-sample-invalide")
        if "incertains" in tracabilite and (not isinstance(tracabilite["incertains"], int) or isinstance(tracabilite["incertains"], bool)):
            raisons.append("tracabilite-incertains-invalide")
        if "fenetre" in tracabilite and not isinstance(tracabilite["fenetre"], dict):
            raisons.append("tracabilite-fenetre-invalide")
    if "refus" in sortie:
        refus = sortie["refus"]
        if refus is None:
            pass
        elif isinstance(refus, dict):
            if set(refus.keys()) != set(["raison", "route_rejetees"]):
                raisons.append("refus-cles-invalides")
            else:
                if refus["raison"] not in REFUS_RAISONS:
                    raisons.append("refus-raison-inconnue")
                if not isinstance(refus["route_rejetees"], list):
                    raisons.append("refus-routes-invalides")
        else:
            raisons.append("refus-invalide")
    return (len(raisons) == 0, raisons)
