"""Garde de perimetre du diff, pre-garde machine avant E5."""
import fnmatch
import hashlib
import json
import os
import re
import subprocess
import unicodedata
GARDE = 'PERIMETRE_DIFF'
INTERDITS_IMPLICITES = ['.git/**', 'VERSION', 'CHANGELOG*', '.github/**', '.gitlab-ci.yml', 'ci_test.py']
def normaliser_chemin(chemin):
    # Normalise un chemin relatif, leve ValueError si invalide.
    if not isinstance(chemin, str):
        raise ValueError("chemin invalide : chaine attendue")
    if not chemin or not chemin.strip():
        raise ValueError("chemin invalide : vide")
    if os.path.isabs(chemin) or chemin.startswith("/") or chemin.startswith("\\"):
        raise ValueError("chemin invalide : absolu refuse")
    if re.match(r"^[a-zA-Z]:", chemin):
        raise ValueError("chemin invalide : absolu refuse")
    texte = chemin.replace("\\", "/")
    texte = unicodedata.normalize("NFC", texte).casefold()
    nettoyees = []
    for partie in texte.split("/"):
        if partie == "" or partie == ".":
            continue
        if partie == "..":
            raise ValueError("chemin invalide : .. refuse")
        nettoyees.append(partie)
    if not nettoyees:
        raise ValueError("chemin invalide : vide apres nettoyage")
    return "/".join(nettoyees)
def _normaliser_motif(motif):
    # Normalise un motif glob, sans lever d erreur.
    try:
        return normaliser_chemin(motif)
    except ValueError:
        texte = motif.replace("\\", "/")
        texte = unicodedata.normalize("NFC", texte).casefold()
        while texte.startswith("./"):
            texte = texte[2:]
        return texte
def _apparie(chemin_norm, motifs):
    # True si le chemin apparie un motif, **/ optionnel en tete.
    for motif in motifs:
        if not isinstance(motif, str) or not motif:
            continue
        motif_norm = _normaliser_motif(motif)
        if fnmatch.fnmatchcase(chemin_norm, motif_norm):
            return True
        if "**/" in motif_norm and fnmatch.fnmatchcase(chemin_norm, motif_norm.replace("**/", "")):
            return True
    return False
def charger_regles_sensibles(chemin_fichier):
    # Charge une regle par ligne, ignore vide et commentaires.
    regles = []
    with open(chemin_fichier, "r", encoding="utf-8") as f:
        for ligne in f:
            texte = ligne.strip()
            if not texte or texte.startswith("#"):
                continue
            if "\t" in texte:
                texte = texte.split("\t")[-1].strip()
                if not texte:
                    continue
            regles.append(texte)
    return regles
def chemin_est_sensible(chemin, regles):
    # True si le chemin apparie une regle sensible.
    if not regles:
        return False
    try:
        chemin_norm = normaliser_chemin(chemin)
    except ValueError:
        return False
    return _apparie(chemin_norm, regles)
def calculer_diff(repo, base_sha):
    # Calcule le diff avec git, parse numstat -z.
    resultat = subprocess.run(["git", "diff", "--no-renames", "--numstat", "-z", base_sha], cwd=str(repo), stdout=subprocess.PIPE, check=True)
    texte_brut = resultat.stdout
    entrees = []
    binaire = []
    if not texte_brut:
        return {"entrees": entrees, "binaire": binaire, "texte_brut": texte_brut}
    for partie in texte_brut.split(b"\x00"):
        if not partie:
            continue
        champs = partie.decode("utf-8", errors="replace").split("\t")
        if len(champs) != 3:
            continue
        ajoutees_txt, supprimees_txt, chemin = champs
        if ajoutees_txt == "-" or supprimees_txt == "-":
            binaire.append(chemin)
            entrees.append({"ajoutees": 0, "supprimees": 0, "chemin": chemin})
        else:
            try:
                ajoutees = int(ajoutees_txt)
                supprimees = int(supprimees_txt)
            except ValueError:
                continue
            entrees.append({"ajoutees": ajoutees, "supprimees": supprimees, "chemin": chemin})
    return {"entrees": entrees, "binaire": binaire, "texte_brut": texte_brut}
def verifier_perimetre_diff(repo, base_sha, allowed_paths, prohibited=None, fichier_regles_sensibles=None, acteur=None, acteurs_locaux=("ironclaw",), max_diff_files=None, max_diff_lines=None):
    # Verifie le diff entier, refus net, aucune troncature.
    diff = calculer_diff(repo, base_sha)
    entrees = diff["entrees"]
    binaire_brut = diff["binaire"]
    texte_brut = diff["texte_brut"]
    hors_perimetre = []
    interdits = []
    sensibles = []
    binaires = []
    symlinks = []
    refuse = None
    if prohibited is None:
        prohibited = []
    if allowed_paths is None:
        allowed_paths = []
    regles_sensibles = []
    if fichier_regles_sensibles is not None:
        regles_sensibles = charger_regles_sensibles(fichier_regles_sensibles)
    for entree in entrees:
        chemin_brut = entree["chemin"]
        try:
            chemin_norm = normaliser_chemin(chemin_brut)
        except ValueError:
            hors_perimetre.append(chemin_brut)
            if refuse is None:
                refuse = "HORS_PERIMETRE"
            continue
        # Regle 1 : dans allowed_paths.
        if not _apparie(chemin_norm, allowed_paths):
            hors_perimetre.append(chemin_norm)
            if refuse is None:
                refuse = "HORS_PERIMETRE"
        # Regle 2 : interdit, prioritaire sur allowed.
        if _apparie(chemin_norm, INTERDITS_IMPLICITES) or _apparie(chemin_norm, prohibited):
            interdits.append(chemin_norm)
            if refuse is None:
                refuse = "CHEMIN_INTERDIT"
        # Regle 3 : binaire.
        if chemin_brut in binaire_brut:
            binaires.append(chemin_norm)
            if refuse is None:
                refuse = "BINAIRE_INTERDIT"
        # Regle 4 : symlink.
        if os.path.islink(os.path.join(str(repo), chemin_brut)):
            symlinks.append(chemin_norm)
            if refuse is None:
                refuse = "SYMLINK_INTERDIT"
        # Regle 5 : sensible.
        if fichier_regles_sensibles is not None and chemin_est_sensible(chemin_norm, regles_sensibles):
            sensibles.append(chemin_norm)
            if acteur is not None and acteur not in acteurs_locaux:
                if refuse is None:
                    refuse = "SENSIBLE_ACTOR_INTERDIT"
    n_fichiers = len(entrees)
    lignes_ajoutees = sum(e["ajoutees"] for e in entrees)
    lignes_supprimees = sum(e["supprimees"] for e in entrees)
    # Regle 6 : budget fichiers.
    if max_diff_files is not None and n_fichiers > max_diff_files:
        if refuse is None:
            refuse = "BUDGET_FICHIERS_DEPASSE"
    # Regle 7 : budget lignes.
    if max_diff_lines is not None and lignes_ajoutees + lignes_supprimees > max_diff_lines:
        if refuse is None:
            refuse = "BUDGET_LIGNES_DEPASSE"
    digest_diff = hashlib.sha256(texte_brut).hexdigest()
    ok = refuse is None
    if refuse is not None:
        message = json.dumps({"garde": "PERIMETRE_DIFF", "etape": "pre-E5", "champ": "diff", "recu": {"refuse": refuse, "hors_perimetre": hors_perimetre[:5], "interdits": interdits[:5], "sensibles": sensibles[:5], "n_fichiers": n_fichiers, "lignes": lignes_ajoutees + lignes_supprimees}, "attendu": "diff inclus dans allowed_paths, hors interdits"}, ensure_ascii=True, sort_keys=True)
    else:
        message = ""
    return {"ok": ok, "refuse": refuse, "hors_perimetre": hors_perimetre, "interdits": interdits, "sensibles": sensibles, "binaires": binaires, "symlinks": symlinks, "n_fichiers": n_fichiers, "lignes_ajoutees": lignes_ajoutees, "lignes_supprimees": lignes_supprimees, "digest_diff": digest_diff, "message": message}
