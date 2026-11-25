"""Verificateur de citations pour preuves citees."""
import hashlib
import os
import re

class CitationError(Exception):
    """Erreur levee quand une preuve citee est invalide."""
    pass


def _est_hex_40(valeur):
    # Controle : chaine de 40 caracteres hexadecimaux.
    if not isinstance(valeur, str):
        return False
    if len(valeur) != 40:
        return False
    for car in valeur:
        if car not in "0123456789abcdefABCDEF":
            return False
    return True


def _parse_plage(lines):
    # Convertit N ou N-M en tuple (debut, fin), None si invalide.
    if isinstance(lines, bool):
        return None
    if isinstance(lines, int):
        if lines < 1:
            return None
        return (lines, lines)
    if not isinstance(lines, str):
        return None
    texte = lines.strip()
    if not texte:
        return None
    parties = texte.split("-")
    if len(parties) not in (1, 2):
        return None
    valeurs = []
    for partie in parties:
        partie = partie.strip()
        if not partie.isdigit():
            return None
        valeurs.append(int(partie))
    debut = valeurs[0]
    fin = valeurs[1] if len(valeurs) == 2 else debut
    if debut < 1 or fin < debut:
        return None
    return (debut, fin)


def verify_citation(path, lines, excerpt, base_dir, expected_sha256=None):
    # Verifie une citation, retourne dict avec ok et reasons.
    reasons = []
    # Controle du chemin : absolu ou contenant .. refuse.
    if not isinstance(path, str) or not path:
        reasons.append("chemin_refuse")
        return {"ok": False, "reasons": reasons}
    if os.path.isabs(path) or ".." in path:
        reasons.append("chemin_refuse")
        return {"ok": False, "reasons": reasons}
    if re.match(r"^[a-zA-Z]:", path):
        reasons.append("chemin_refuse")
        return {"ok": False, "reasons": reasons}
    # Resolution sous base_dir, sans sortie du dossier.
    try:
        base_abs = os.path.abspath(str(base_dir))
    except Exception:
        reasons.append("fichier_absent")
        return {"ok": False, "reasons": reasons}
    full = os.path.abspath(os.path.join(base_abs, path))
    if full != base_abs and not full.startswith(base_abs + os.sep):
        reasons.append("chemin_refuse")
        return {"ok": False, "reasons": reasons}
    # Existence du fichier.
    if not os.path.isfile(full):
        reasons.append("fichier_absent")
        return {"ok": False, "reasons": reasons}
    # Controle de la plage.
    plage = _parse_plage(lines)
    if plage is None:
        reasons.append("plage_invalide")
        return {"ok": False, "reasons": reasons}
    debut, fin = plage
    # Lecture du fichier en utf-8 avec remplacement.
    try:
        with open(full, "r", encoding="utf-8", errors="replace") as f:
            contenu = f.read()
    except OSError:
        reasons.append("fichier_absent")
        return {"ok": False, "reasons": reasons}
    lignes_fichier = contenu.splitlines()
    total = len(lignes_fichier)
    # Controle de la plage par rapport au fichier reel.
    hors_fichier = False
    if debut > total or fin > total:
        reasons.append("plage_hors_fichier")
        hors_fichier = True
    # Controle de l extrait, seulement si la plage est dans le fichier.
    if not hors_fichier and excerpt is not None:
        reel = "\n".join(lignes_fichier[debut - 1:fin]).strip()
        if str(excerpt).strip() != reel:
            reasons.append("extrait_ne_correspond_pas")
    # Controle du sha256, si attendu.
    if expected_sha256 is not None:
        try:
            with open(full, "rb") as f:
                reel_sha = hashlib.sha256(f.read()).hexdigest()
        except OSError:
            reel_sha = None
        if reel_sha is None or str(expected_sha256).lower() != reel_sha:
            reasons.append("sha_diverge")
    return {"ok": len(reasons) == 0, "reasons": reasons}


def require_cited_evidence(e2_output, base_dir):
    # Exige des citations valides, sinon leve CitationError.
    if not isinstance(e2_output, dict):
        raise CitationError("sortie e2 invalide : dictionnaire attendu")
    citations = e2_output.get("citations")
    if not isinstance(citations, list) or len(citations) == 0:
        raise CitationError("citations absent ou vide")
    base_sha = e2_output.get("base_sha")
    if not _est_hex_40(base_sha):
        raise CitationError("base_sha absent ou invalide : attendu 40 caracteres hexadecimaux")
    for citation in citations:
        if not isinstance(citation, dict):
            raise CitationError("citation invalide : dictionnaire attendu")
        path = citation.get("path")
        lines = citation.get("lines")
        excerpt = citation.get("excerpt")
        expected = citation.get("expected_sha256")
        resultat = verify_citation(path, lines, excerpt, base_dir, expected)
        if not resultat.get("ok"):
            raise CitationError("citation echouee : path=%s raisons=%s" % (path, ",".join(resultat.get("reasons", []))))
    return None
