"""Statistiques de competence, module pur."""
import math
from datetime import datetime, timedelta
N_MIN = 20
FENETRE_JOURS = 30
FENETRE_OBS = 50
WILSON_Z = 1.6449
def _parse_iso(texte):
    # Parse une chaine ISO 8601, None si echec.
    if not isinstance(texte, str) or not texte:
        return None
    try:
        txt = texte
        if txt.endswith("Z"):
            txt = txt[:-1] + "+00:00"
        return datetime.fromisoformat(txt)
    except Exception:
        return None
def fenetre(obs, maintenant):
    # Garde les obs dans la fenetre, tri descendant par ts.
    if not isinstance(obs, list):
        return []
    mnt = _parse_iso(maintenant)
    if mnt is None:
        return []
    debut = mnt - timedelta(days=FENETRE_JOURS)
    gardees = []
    for o in obs:
        if not isinstance(o, dict):
            continue
        ts = o.get("ts")
        dt = _parse_iso(ts)
        if dt is None:
            continue
        if debut <= dt <= mnt:
            gardees.append((dt, o))
    gardees.sort(key=lambda paire: paire[0], reverse=True)
    resultat = [o for dt, o in gardees]
    return resultat[:FENETRE_OBS]
def wilson(succes, n_mesure):
    # Intervalle de Wilson a 90 pour cent.
    if not isinstance(n_mesure, (int, float)) or isinstance(n_mesure, bool):
        return None
    if n_mesure <= 0:
        return None
    if not isinstance(succes, (int, float)) or isinstance(succes, bool):
        return None
    z = WILSON_Z
    n = float(n_mesure)
    p = float(succes) / n
    denom = 1.0 + z * z / n
    centre = (p + z * z / (2.0 * n)) / denom
    marge = (z / denom) * math.sqrt(p * (1.0 - p) / n + z * z / (4.0 * n * n))
    bas = centre - marge
    haut = centre + marge
    if bas < 0.0:
        bas = 0.0
    if haut > 1.0:
        haut = 1.0
    return (bas, haut)
def equivalentes(iv_a, iv_b):
    # True si intervalles fermes se recouvrent.
    if iv_a is None or iv_b is None:
        return False
    try:
        a, b = iv_a
        c, d = iv_b
    except Exception:
        return False
    return a <= d and c <= b
def stats_triplet(obs):
    # Calcule le triplet sur les obs terminales.
    if not isinstance(obs, list):
        obs = []
    succes = 0
    echecs = 0
    incertains = 0
    for o in obs:
        if not isinstance(o, dict):
            continue
        issue = o.get("issue")
        if issue == "succes_verifie":
            succes += 1
        elif issue == "echec" or issue == "refuse":
            echecs += 1
        elif issue == "incertain":
            incertains += 1
    sample_count = len(obs)
    n_mesure = succes + echecs
    if n_mesure == 0:
        taux = None
        iv = None
    else:
        taux = succes / n_mesure
        iv = wilson(succes, n_mesure)
    non_mesure = sample_count < N_MIN
    return {"sample_count": sample_count, "succes": succes, "echecs": echecs, "incertains": incertains, "taux": taux, "wilson": iv, "non_mesure": non_mesure}
def derive_role_kind(tool, level, table):
    # Projette tool level via la table, sinon None None.
    if table is None or not isinstance(table, dict):
        return (None, None)
    cle = (tool, level)
    try:
        valeur = table.get(cle)
    except Exception:
        return (None, None)
    if valeur is None:
        return (None, None)
    try:
        role, kind = valeur
    except Exception:
        return (None, None)
    return (role, kind)
def groupe_par_triplet(obs, table):
    # Groupe par backend role kind, ignore non projetables.
    groupes = {}
    ignores = []
    if not isinstance(obs, list):
        return (groupes, ignores)
    for o in obs:
        if not isinstance(o, dict) or "backend" not in o:
            ignores.append(o)
            continue
        role, kind = derive_role_kind(o.get("tool"), o.get("level"), table)
        if role is None or kind is None:
            ignores.append(o)
            continue
        cle = (o.get("backend"), role, kind)
        if cle not in groupes:
            groupes[cle] = []
        groupes[cle].append(o)
    return (groupes, ignores)
