# -*- coding: utf-8 -*-
"""Juge de paix Reprise : verification mecanique centralisee (contrat SERVICES-JUGE-DEPOT v1.2, §2).

Role (contrat §2) : executer `reprise/ci_test.py` sur un arbre de travail et
produire un RECEU FACTUEL — le juge produit des faits, jamais des verdicts.
Un recu digeste sha256, conserve `retention_jours` (10 par defaut, decision
operateur 26/11/2026), porte : sha de l'arbre, proprete, digest de la suite,
code de sortie reel, digest du log archive. L'interpretation reste un travail
E6 ; le juge n'edulcore jamais un resultat (flake `test_apple_pilot_ui`
rappelle seulement en `notes`).

Garanties encodées ici (critères falsifiables J1-J8) :
- verrou PAR ARBRE : jamais deux exécutions simultanees sur le meme arbre (J1) ;
- politique fail-closed : absente => JUGE_POLITIQUE_ABSENTE, acteur inconnu =>
  JUGE_ACTEUR_INCONNU, service inactif => JUGE_DESACTIVE (J2) ;
- log absent a l'archivage => etat `incertain`, jamais eligible E6 (J3) ;
- budget depasse => etat `expire`, interruption du groupe de processus,
  recu conserve, non eligible, aucune relance automatique (J4) ;
- demande identique a un run `fini` => rejeu idempotent du recu existant,
  trace a l'audit (J5) ;
- croisement SOD a posteriori : demandeur d'une preuve != auteur du lot (J6) ;
- arbre sale => recu `arbre_sale=true` non eligible meme si code 0 (J7) ;
- `digest_suite` rend toute modification de la suite detectable a posteriori (J8).

Environnement : le juge fixe lui-meme REPRISE_TEST_PYTHON (interpreteur 3.11+)
et un HOME jetable ; un heritage entrant de REPRISE_TEST_* est refuse
(JUGE_ENV_REFUSE — la preuve doit etre reproductible, §2.4).

State dir (jamais commité) : `~/Library/Application Support/Reprise/` —
`juge-policy.json`, `juge-audit.jsonl`, `juge/runs/<run_id>/receipt.json`,
`juge/runs/<run_id>/test-parallel.log`, `activity.json` (flux lu par l'UI §8.4).
Test local : REPRISE_JUGE_ETAT remplace la base (tests hermetiques, HOME jetable).

Convention du depot : compatible Python 3.9 (pas de PEP 604), zero dependance.
"""

import argparse
import hashlib
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from typing import Any, Dict, Iterable, List, Optional

DEFAULT_STATE_DIR = os.path.join(
    os.path.expanduser('~'), 'Library', 'Application Support', 'Reprise'
)

JUGE_DESACTIVE = 'JUGE_DESACTIVE'
JUGE_ACTEUR_INCONNU = 'JUGE_ACTEUR_INCONNU'
JUGE_POLITIQUE_ABSENTE = 'JUGE_POLITIQUE_ABSENTE'
JUGE_ARBRE_INVALIDE = 'JUGE_ARBRE_INVALIDE'
JUGE_FILE_PLEINE = 'JUGE_FILE_PLEINE'
JUGE_DELAI_ATTENTE = 'JUGE_DELAI_ATTENTE'
JUGE_DELAI_DEPASSE = 'JUGE_DELAI_DEPASSE'
JUGE_ENV_REFUSE = 'JUGE_ENV_REFUSE'
JUGE_LOG_ABSENT = 'JUGE_LOG_ABSENT'

ACTEURS_DEFAUT = ('autoclaw', 'codex', 'claude', 'chatgpt', 'metaai', 'harness')

NOTE_FLAKE = (
    'Flake documente : test_apple_pilot_ui hors CI (sandbox swiftc) — USAGE.md. '
    'Le juge ne l\'edulcore pas : le recu porte le code de sortie reel et le log complet ; '
    'l\'interpretation reste un travail E6.'
)

BUDGET_PLAFOND_S = 3600
CLÉS_ENV_REFUSEES = ('REPRISE_TEST_PYTHON', 'REPRISE_TEST_RUNTIME_ARCHIVE')


class JugeError(Exception):
    """Erreur attendue du juge : refus nommé (code JUGE_*) ou demande invalide."""

    def __init__(self, code, message):
        super(JugeError, self).__init__(message)
        self.code = code
        self.message = message


# ---------------------------------------------------------------------------
# Chemins d'état
# ---------------------------------------------------------------------------

def state_dir(base=None):
    """Base d'état : argument > REPRISE_JUGE_ETAT > défaut produit."""
    candidat = base or os.environ.get('REPRISE_JUGE_ETAT') or DEFAULT_STATE_DIR
    return os.path.abspath(os.path.expanduser(candidat))


def policy_path(base=None):
    return os.path.join(state_dir(base), 'juge-policy.json')


def audit_path(base=None):
    return os.path.join(state_dir(base), 'juge-audit.jsonl')


def runs_dir(base=None):
    return os.path.join(state_dir(base), 'juge', 'runs')


def activity_path(base=None):
    return os.path.join(state_dir(base), 'activity.json')


# ---------------------------------------------------------------------------
# Politique (§2.5) — fail-closed
# ---------------------------------------------------------------------------

def politique_defaut():
    """Politique initiale exacte du contrat §2.5 (+ TTL de file, défaut 600 s)."""
    return {
        'actif': True,
        'acteurs': {nom: {} for nom in ACTEURS_DEFAUT},
        'concurrence_machine': 2,
        'file_max': 8,
        'delai_secondes': 1800,
        'delai_file_secondes': 600,
        'retention_jours': 10,
    }


def charger_politique(chemin=None):
    """Charge la politique ; absente => JUGE_POLITIQUE_ABSENTE (pas de repli permissif)."""
    chemin = chemin or policy_path()
    if not os.path.exists(chemin):
        raise JugeError(
            JUGE_POLITIQUE_ABSENTE,
            'Politique du juge absente (%s) : amorçage par écriture directe du '
            'fichier ou `init` — jamais de politique implicite permissive' % chemin,
        )
    with open(chemin, 'r', encoding='utf-8') as handle:
        brut = json.load(handle)
    if not isinstance(brut, dict):
        raise JugeError(JUGE_POLITIQUE_ABSENTE, 'Politique invalide (%s) : objet JSON attendu' % chemin)
    politique = {
        'actif': bool(brut.get('actif', False)),
        'acteurs': dict(brut.get('acteurs') or {}),
        'concurrence_machine': int(brut.get('concurrence_machine', 2)),
        'file_max': int(brut.get('file_max', 8)),
        'delai_secondes': int(brut.get('delai_secondes', 1800)),
        'delai_file_secondes': int(brut.get('delai_file_secondes', 600)),
        'retention_jours': int(brut.get('retention_jours', 10)),
    }
    return politique


def ecrire_politique(politique, chemin=None):
    """Écriture atomique (tmp + os.replace) — convention reprise_gh."""
    chemin = chemin or policy_path()
    os.makedirs(os.path.dirname(chemin), exist_ok=True)
    tmp = chemin + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as handle:
        json.dump(politique, handle, ensure_ascii=False, indent=2)
        handle.write('\n')
    os.replace(tmp, chemin)


def init_politique(base=None, acteurs=None):
    """Amorçage : écrit la politique par défaut si absente, refuse sinon.

    Sans auto-élévation : `init` ne modifie jamais une politique existante
    (l'ajustement est une écriture directe de juge-policy.json par l'opérateur).
    """
    chemin = policy_path(base)
    audit_chemin = audit_path(base)
    if os.path.exists(chemin):
        _audit(audit_chemin, {'type': 'init', 'refus': 'INIT_REFUSE', 'chemin': chemin})
        raise JugeError(
            'INIT_REFUSE',
            'Politique deja presente (%s) : l\'ajustement est une ecriture directe '
            'par l\'operateur — init sans auto-elevation' % chemin,
        )
    politique = politique_defaut()
    if acteurs:
        liste = [x.strip() for x in acteurs if x and x.strip()]
        politique['acteurs'] = {nom: {} for nom in liste}
    ecrire_politique(politique, chemin)
    _audit(audit_chemin, {'type': 'init', 'chemin': chemin, 'acteurs': sorted(politique['acteurs'])})
    return politique


# ---------------------------------------------------------------------------
# Audit JSONL (§2.6) — sans secret, sans dump d'environnement
# ---------------------------------------------------------------------------

def _audit(chemin, entree):
    ligne = {'ts': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}
    ligne.update(entree)
    try:
        os.makedirs(os.path.dirname(chemin) or '.', exist_ok=True)
        with open(chemin, 'a', encoding='utf-8') as handle:
            handle.write(json.dumps(ligne, ensure_ascii=False) + '\n')
    except OSError:
        pass  # l'audit ne fait jamais echouer une operation mecanique


# ---------------------------------------------------------------------------
# Digests — sha_arbre, modifs, suite, recu
# ---------------------------------------------------------------------------

def _sha256_texte(texte):
    return hashlib.sha256(texte.encode('utf-8')).hexdigest()


def _sha256_fichier(chemin):
    digest = hashlib.sha256()
    with open(chemin, 'rb') as handle:
        for bloc in iter(lambda: handle.read(65536), b''):
            digest.update(bloc)
    return digest.hexdigest()


def _canonical(objet):
    return json.dumps(objet, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def infos_arbre(worktree):
    """Faits Git de l'arbre : sha HEAD, proprete, digest des modifs. Refus si invalide (§2.5)."""
    if not worktree or not os.path.isdir(worktree):
        raise JugeError(JUGE_ARBRE_INVALIDE, 'Arbre introuvable : %s' % worktree)
    if not os.path.isfile(os.path.join(worktree, 'reprise', 'ci_test.py')):
        raise JugeError(
            JUGE_ARBRE_INVALIDE,
            'Arbre sans reprise/ci_test.py (pas un arbre verifiable) : %s' % worktree,
        )
    probe = subprocess.run(
        ['git', '-C', worktree, 'rev-parse', '--is-inside-work-tree'],
        capture_output=True, text=True, timeout=30,
    )
    if probe.returncode != 0 or probe.stdout.strip() != 'true':
        raise JugeError(JUGE_ARBRE_INVALIDE, 'Pas un arbre de travail Git : %s' % worktree)
    head = subprocess.run(
        ['git', '-C', worktree, 'rev-parse', 'HEAD'],
        capture_output=True, text=True, timeout=30,
    )
    if head.returncode != 0:
        raise JugeError(JUGE_ARBRE_INVALIDE, 'Arbre sans commit HEAD : %s' % worktree)
    statut = subprocess.run(
        ['git', '-C', worktree, 'status', '--porcelain', '-uall'],
        capture_output=True, text=True, timeout=30,
    )
    # Contrat §2.5 : digest hors `.runtime-py/` (journal d'exécution, écrasé à
    # chaque run) et hors fichiers ignorés (porcelain ne liste jamais les ignorés).
    lignes = [
        ligne for ligne in (statut.stdout or '').splitlines()
        if '/.runtime-py/' not in '/' + ligne[3:]
    ]
    texte = '\n'.join(lignes) + ('\n' if lignes else '')
    return {
        'sha_arbre': head.stdout.strip(),
        'arbre_sale': bool(lignes),
        'digest_modifs': _sha256_texte(texte),
    }


def digest_suite(worktree):
    """Digest de la suite de vérification : ci_test.py + tous les tests (J8)."""
    racine = os.path.join(worktree, 'reprise')
    fichiers = []
    cible = os.path.join(racine, 'ci_test.py')
    if os.path.isfile(cible):
        fichiers.append(('reprise/ci_test.py', cible))
    tests = os.path.join(racine, 'tests')
    if os.path.isdir(tests):
        for dossier_actuel, _sous, noms in os.walk(tests):
            for nom in sorted(noms):
                if nom.endswith('.py'):
                    chemin = os.path.join(dossier_actuel, nom)
                    relatif = os.path.relpath(chemin, racine)
                    fichiers.append(('reprise/' + relatif.replace(os.sep, '/'), chemin))
    fichiers.sort()
    empreinte = hashlib.sha256()
    for relatif, chemin in fichiers:
        empreinte.update(('%s:%s\n' % (relatif, _sha256_fichier(chemin))).encode('utf-8'))
    return empreinte.hexdigest()


def calculer_run_id(sha_arbre, digest_modifs, digest_suite_valeur, parallel):
    """run_id = digest déterministe de (sha_arbre, digest_modifs, digest_suite, parallel)."""
    return _sha256_texte(_canonical([sha_arbre, digest_modifs, digest_suite_valeur, int(parallel)]))[:16]


def eligible_e6(recu):
    """Éligibilité E6 (§2.2) : fini && code 0 && arbre propre && log digesté."""
    return bool(
        recu
        and recu.get('etat') == 'fini'
        and recu.get('code') == 0
        and not recu.get('arbre_sale')
        and recu.get('digest_log')
    )


def verifier_recu(recu):
    """Vérifie le digest sha256 d'un reçu (integrité a posteriori)."""
    if not isinstance(recu, dict) or 'digest_recu' not in recu:
        return False
    attendu = recu['digest_recu']
    copie = dict(recu)
    copie.pop('digest_recu', None)
    return _sha256_texte(_canonical(copie)) == attendu


def sod_violations(recus, auteurs_lots):
    """Croisement SOD (J6) : preuve d'acceptation (recu eligible) d'un lot demandée par l'auteur du lot.

    recus : reçus (dicts, champ `objet` facultatif = lot) ; auteurs_lots : {lot: auteur_code}.
    Retour : liste de violations {run_id, objet, demandeur, auteur}.
    """
    violations = []
    for recu in recus or []:
        if not isinstance(recu, dict):
            continue
        objet = recu.get('objet') or ''
        if objet not in (auteurs_lots or {}):
            continue
        if eligible_e6(recu) and recu.get('acteur') == auteurs_lots[objet]:
            violations.append({
                'run_id': recu.get('run_id'),
                'objet': objet,
                'demandeur': recu.get('acteur'),
                'auteur': auteurs_lots[objet],
            })
    return violations


# ---------------------------------------------------------------------------
# Verrous et file (§2.3/§2.4) — coordination par fichiers dans le state dir
# ---------------------------------------------------------------------------

def _pid_vivant(pid):
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False


def _lock_prendre(chemin, contenu):
    """Prend un verrou fichier O_EXCL ; reprend un verrou périmé (pid mort)."""
    for _ in range(3):
        try:
            fd = os.open(chemin, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            with os.fdopen(fd, 'w', encoding='utf-8') as handle:
                handle.write(json.dumps(contenu, ensure_ascii=False))
            return True
        except FileExistsError:
            try:
                with open(chemin, 'r', encoding='utf-8') as handle:
                    ancien = json.load(handle)
                pid = ancien.get('pid')
                if isinstance(pid, int) and pid != os.getpid() and not _pid_vivant(pid):
                    os.unlink(chemin)  # verrou périmé : reprise après crash
                    continue
            except (OSError, ValueError):
                pass
            return False
    return False


def _lock_liberer(chemin):
    try:
        os.unlink(chemin)
    except FileNotFoundError:
        pass
    except OSError:
        pass


def _slots_actifs(base):
    """Reçus des verrous machine : [{slot, run_id, acteur, debut}]."""
    dossier = os.path.join(state_dir(base), 'juge', 'machine')
    actifs = []
    if not os.path.isdir(dossier):
        return actifs
    for nom in sorted(os.listdir(dossier)):
        if not nom.startswith('slot-') or not nom.endswith('.lock'):
            continue
        try:
            with open(os.path.join(dossier, nom), 'r', encoding='utf-8') as handle:
                contenu = json.load(handle)
            contenu['slot'] = nom
            actifs.append(contenu)
        except (OSError, ValueError):
            continue
    return actifs


def _file_entries(base):
    dossier = os.path.join(state_dir(base), 'juge', 'file')
    if not os.path.isdir(dossier):
        return []
    return [nom for nom in sorted(os.listdir(dossier)) if nom.endswith('.json')]


def _preparer_dossiers(base):
    for relatif in (('juge', 'runs'), ('juge', 'machine'), ('juge', 'verrous'), ('juge', 'file')):
        os.makedirs(os.path.join(state_dir(base), *relatif), exist_ok=True)


# ---------------------------------------------------------------------------
# Flux d'activité (§8.4) — activity.json atomique, lu par l'UI
# ---------------------------------------------------------------------------

def _snapshot_juge(base):
    actifs = [
        {'run_id': a.get('run_id'), 'acteur': a.get('acteur'), 'worktree': a.get('worktree'), 'debut': a.get('debut')}
        for a in _slots_actifs(base)
    ]
    file = [
        {'run_id': nom[:-5], 'depuis': None}
        for nom in _file_entries(base)
    ]
    derniers = []
    erreurs = []
    dossier_runs = runs_dir(base)
    if os.path.isdir(dossier_runs):
        runs = sorted(os.listdir(dossier_runs))[-8:]
        for nom in reversed(runs):
            recu = charger_recu(os.path.join(dossier_runs, nom))
            if not recu:
                continue
            derniers.append({
                'run_id': recu.get('run_id'), 'acteur': recu.get('acteur'),
                'etat': recu.get('etat'), 'code': recu.get('code'),
                'sha_arbre': (recu.get('sha_arbre') or '')[:8],
                'fin': recu.get('fin'),
            })
            if recu.get('etat') == 'fini' and recu.get('code') not in (0, None):
                log = os.path.join(dossier_runs, nom, 'test-parallel.log')
                avant = len(erreurs)
                if os.path.isfile(log):
                    try:
                        with open(log, 'r', encoding='utf-8', errors='replace') as handle:
                            for ligne in handle:
                                if 'FAIL' in ligne or 'Error' in ligne:
                                    erreurs.append(ligne.rstrip()[:200])
                                    if len(erreurs) >= 3:
                                        break
                    except OSError:
                        pass
                if len(erreurs) == avant:
                    # sortie non nulle sans ligne FAIL/Error dans le log : fait brut du reçu
                    erreurs.append('run %s : code de sortie %s' % (recu.get('run_id'), recu.get('code')))
            if len(erreurs) >= 3:
                break
    return {'actifs': actifs, 'file': file, 'derniers': derniers[:5], 'erreurs': erreurs}


def publier_activite(base=None, **sections):
    """Écrit `activity.json` (atomique) : état des services pour l'UI (jamais une copie d'écran)."""
    chemin = activity_path(base)
    try:
        flux = {}
        if os.path.isfile(chemin):
            with open(chemin, 'r', encoding='utf-8') as handle:
                flux = json.load(handle)
            if not isinstance(flux, dict):
                flux = {}
        flux['ts'] = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
        flux.update(sections)
        tmp = chemin + '.tmp'
        os.makedirs(os.path.dirname(chemin), exist_ok=True)
        with open(tmp, 'w', encoding='utf-8') as handle:
            json.dump(flux, handle, ensure_ascii=False, indent=1)
        os.replace(tmp, chemin)
    except (OSError, ValueError):
        pass  # le flux d'activité ne fait jamais echouer un service


# ---------------------------------------------------------------------------
# Interpréteur épinglé (§2.2 : runtime 3.13 embarqué)
# ---------------------------------------------------------------------------

def resoudre_interpreteur():
    """Interpreteur de vérification : celui du juge si >= 3.11, sinon runtime embarqué Reprise."""
    if sys.version_info >= (3, 11) and sys.executable:
        return sys.executable
    candidats = [
        '/Applications/Reprise.app/Contents/Resources/runtime/bin/python3',
        os.path.join(os.path.expanduser('~'), '.reprise', 'runtime', 'bin', 'python3'),
    ]
    for candidat in candidats:
        if os.path.isfile(candidat) and os.access(candidat, os.X_OK):
            return candidat
    raise JugeError(
        JUGE_ENV_REFUSE,
        'Aucun interpreteur 3.11+ disponible pour la verification (juge en 3.9 sans runtime embarque)',
    )


# ---------------------------------------------------------------------------
# Reçus (§2.2)
# ---------------------------------------------------------------------------

def run_dir(run_id, base=None):
    return os.path.join(runs_dir(base), run_id)


def charger_recu(dossier):
    """Lit receipt.json d'un dossier de run ; None si absent/illisible."""
    chemin = os.path.join(dossier, 'receipt.json')
    if not os.path.isfile(chemin):
        return None
    try:
        with open(chemin, 'r', encoding='utf-8') as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return None


def _nettoyer_retention(base, retention_jours):
    """Rétenue des preuves : runs plus vieux que retention_jours supprimés, audits intacts (§2.6)."""
    if retention_jours <= 0:
        return
    dossier = runs_dir(base)
    if not os.path.isdir(dossier):
        return
    limite = time.time() - retention_jours * 86400
    try:
        for nom in os.listdir(dossier):
            chemin = os.path.join(dossier, nom)
            try:
                if os.path.getmtime(chemin) < limite:
                    shutil.rmtree(chemin, ignore_errors=True)
            except OSError:
                continue
    except OSError:
        pass


# ---------------------------------------------------------------------------
# Exécution d'un run (cœur du service)
# ---------------------------------------------------------------------------

def run(
    acteur,
    worktree,
    parallel=4,
    objet=None,
    base=None,
    budget=None,
    poll_s=0.2,
):
    """Vérifie un arbre et produit un reçu factual. Retour : dict du reçu.

    Refus avant tout lancement (JUGE_*) => JugeError ; runs lancés => reçu
    `fini` | `expire` | `incertain` (jamais de verdict, jamais d'édulcoration).
    """
    if not acteur or not str(acteur).strip():
        raise JugeError(JUGE_ACTEUR_INCONNU, 'Acteur requis (--acteur) : appel sans identité refusé')
    acteur = str(acteur).strip()
    parallel = int(parallel)
    if parallel < 1:
        parallel = 1
    _preparer_dossiers(base)
    audit_chemin = audit_path(base)

    politique = charger_politique(policy_path(base))
    def refus(code, message):
        _audit(audit_chemin, {'type': 'refus', 'acteur': acteur, 'refus': code, 'worktree': worktree, 'message': message[:200]})
        raise JugeError(code, message)

    if not politique['actif']:
        refus(JUGE_DESACTIVE, 'Juge desactive par la politique (juge-policy.json : actif=false)')
    if acteur not in politique['acteurs']:
        refus(JUGE_ACTEUR_INCONNU, 'Acteur « %s » absent de la politique du juge' % acteur)
    herite = [cle for cle in CLÉS_ENV_REFUSEES if cle in os.environ]
    if herite:
        refus(
            JUGE_ENV_REFUSE,
            'Environnement entrant refuse (%s) : le juge fixe lui-meme REPRISE_TEST_* '
            '— la preuve doit etre reproductible' % ', '.join(herite),
        )
    infos = infos_arbre(worktree)
    suite = digest_suite(worktree)
    run_id = calculer_run_id(infos['sha_arbre'], infos['digest_modifs'], suite, parallel)
    dossier = run_dir(run_id, base)

    # Idempotence (J5) : demande identique a un run `fini` => rejeu du recu existant.
    precedent = charger_recu(dossier)
    if precedent and precedent.get('etat') == 'fini':
        _audit(audit_chemin, {
            'type': 'rejeu', 'acteur': acteur, 'run_id': run_id, 'worktree': worktree,
            'sha_arbre': infos['sha_arbre'], 'arbre_sale': infos['arbre_sale'],
            'digest_recu': precedent.get('digest_recu'),
        })
        publier_activite(base, juge=_snapshot_juge(base))
        return precedent
    if precedent:
        # Relance toujours fraiche : aucun --reuse-log, l'ancien essai est conservé en precedent.json.
        try:
            os.replace(os.path.join(dossier, 'receipt.json'), os.path.join(dossier, 'precedent.json'))
        except OSError:
            pass

    concurrence = max(1, int(politique['concurrence_machine']))
    file_max = max(0, int(politique['file_max']))
    ttl_file = max(1, int(politique['delai_file_secondes']))
    if budget is None:
        budget = max(1, min(int(politique['delai_secondes']), BUDGET_PLAFOND_S))
    else:
        budget = max(1, min(int(budget), BUDGET_PLAFOND_S))

    actifs = _slots_actifs(base)
    if len(actifs) + len(_file_entries(base)) >= max(1, file_max):
        refus(JUGE_FILE_PLEINE, 'File du juge pleine (%d actifs + %d en attente >= file_max=%d) — retenter plus tard'
              % (len(actifs), len(_file_entries(base)), file_max))

    interpreteur = resoudre_interpreteur()
    os.makedirs(dossier, exist_ok=True)

    # File + verrous : machine (concurrence, équité 1 actif/acteur) puis arbre (J1).
    debut_file = time.time()
    entree_file = os.path.join(state_dir(base), 'juge', 'file', run_id + '.json')
    verrou_arbre = os.path.join(
        state_dir(base), 'juge', 'verrous',
        hashlib.sha1(os.path.realpath(worktree).encode('utf-8')).hexdigest() + '.lock',
    )
    slot_pris = None
    attente = 0.0
    while True:
        if slot_pris is None:
            actifs = _slots_actifs(base)
            pris_par_moi = [a for a in actifs if a.get('acteur') == acteur]
            if not pris_par_moi:  # équité : max 1 exécution active par acteur (§2.4)
                for i in range(concurrence):
                    chemin_slot = os.path.join(state_dir(base), 'juge', 'machine', 'slot-%d.lock' % i)
                    contenu = {
                        'run_id': run_id, 'acteur': acteur, 'pid': os.getpid(),
                        'worktree': os.path.realpath(worktree),
                        'debut': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
                    }
                    if _lock_prendre(chemin_slot, contenu):
                        slot_pris = chemin_slot
                        break
        if slot_pris is not None and _lock_prendre(verrou_arbre, {'run_id': run_id, 'acteur': acteur, 'pid': os.getpid()}):
            break
        if not os.path.exists(entree_file):
            try:
                with open(entree_file, 'w', encoding='utf-8') as handle:
                    json.dump({'run_id': run_id, 'acteur': acteur, 'depuis': time.time()}, handle)
            except OSError:
                pass
        attente = time.time() - debut_file
        publier_activite(base, juge=_snapshot_juge(base))
        if attente > ttl_file:
            if slot_pris:
                _lock_liberer(slot_pris)
            _lock_liberer(entree_file)
            refus(JUGE_DELAI_ATTENTE,
                  'TTL de file epuise (%.0f s > %d s) pour %s — retenter plus tard' % (attente, ttl_file, run_id))
        time.sleep(poll_s)

    try:
        _lock_liberer(entree_file)
        return _executer_run(
            base, audit_chemin, politique, acteur, worktree, run_id, dossier,
            infos, suite, parallel, objet, interpreteur, budget, attente,
        )
    finally:
        _lock_liberer(verrou_arbre)
        if slot_pris:
            _lock_liberer(slot_pris)
        publier_activite(base, juge=_snapshot_juge(base))


def _executer_run(
    base, audit_chemin, politique, acteur, worktree, run_id, dossier,
    infos, suite, parallel, objet, interpreteur, budget, attente,
):
    """Lance ci_test sous budget, archive le log AVANT libération, écrit le reçu + l'audit."""
    debut_ts = time.time()
    log_canonique = os.path.join(worktree, 'reprise', '.runtime-py', 'test-parallel.log')
    capture = os.path.join(dossier, 'stdout-capture.log')
    os.makedirs(os.path.dirname(log_canonique), exist_ok=True)

    home_jetable = tempfile.mkdtemp(prefix='juge-home-')
    env = {}
    for cle in ('PATH', 'LANG', 'LC_ALL', 'TMPDIR'):
        if cle in os.environ:
            env[cle] = os.environ[cle]
    env['HOME'] = home_jetable
    env['REPRISE_TEST_PYTHON'] = interpreteur  # override explicite de ci_test (§2.4)

    etat = 'fini'
    code = None
    refus_nom = None
    with open(capture, 'wb') as cap:
        processus = subprocess.Popen(
            [interpreteur, 'reprise/ci_test.py', '--parallel', str(parallel)],
            cwd=worktree, stdout=cap, stderr=subprocess.STDOUT,
            env=env, start_new_session=True,
        )
        publier_activite(base, juge=_snapshot_juge(base))
        try:
            code = processus.wait(timeout=budget)
        except subprocess.TimeoutExpired:
            etat = 'expire'
            refus_nom = JUGE_DELAI_DEPASSE
            try:
                os.killpg(os.getpgid(processus.pid), signal.SIGTERM)
            except (ProcessLookupError, PermissionError, OSError):
                pass
            try:
                processus.wait(timeout=5)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(os.getpgid(processus.pid), signal.SIGKILL)
                except (ProcessLookupError, PermissionError, OSError):
                    pass
                processus.wait()
            code = None

    # Archivage du log AVANT toute libération de verrou (§2.2).
    digest_log = None
    if os.path.isfile(log_canonique):
        try:
            shutil.copy2(log_canonique, os.path.join(dossier, 'test-parallel.log'))
            digest_log = _sha256_fichier(os.path.join(dossier, 'test-parallel.log'))
        except OSError:
            digest_log = None
    if digest_log is None:
        etat = 'incertain'
        refus_nom = JUGE_LOG_ABSENT  # J3 : log introuvable => jamais `fini`, jamais eligible E6
        code = None  # sans log archivé, aucun code de sortie n'est attestable

    fin_ts = time.time()
    recu = {
        'run_id': run_id,
        'acteur': acteur,
        'objet': objet or '',
        'worktree': os.path.realpath(worktree),
        'sha_arbre': infos['sha_arbre'],
        'arbre_sale': infos['arbre_sale'],
        'digest_modifs': infos['digest_modifs'],
        'digest_suite': suite,
        'parallel': parallel,
        'interpreteur': interpreteur,
        'etat': etat,
        'code': code,
        'debut': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(debut_ts)),
        'fin': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(fin_ts)),
        'debut_ts': round(debut_ts, 3),
        'fin_ts': round(fin_ts, 3),
        'duree_s': round(fin_ts - debut_ts, 2),
        'attente_s': round(attente, 2),
        'digest_log': digest_log,
        'notes': NOTE_FLAKE,
    }
    recu['digest_recu'] = _sha256_texte(_canonical(recu))
    tmp = os.path.join(dossier, 'receipt.json.tmp')
    with open(tmp, 'w', encoding='utf-8') as handle:
        json.dump(recu, handle, ensure_ascii=False, indent=2)
        handle.write('\n')
    os.replace(tmp, os.path.join(dossier, 'receipt.json'))

    entree_audit = {
        'type': 'run' if etat == 'fini' else 'incertain' if etat == 'incertain' else 'expire',
        'acteur': acteur, 'run_id': run_id, 'worktree': recu['worktree'],
        'sha_arbre': recu['sha_arbre'], 'arbre_sale': recu['arbre_sale'],
        'code': recu['code'], 'digest_log': digest_log,
        'duree_s': recu['duree_s'],
    }
    if refus_nom:
        entree_audit['refus'] = refus_nom
    _audit(audit_chemin, entree_audit)
    _nettoyer_retention(base, int(politique.get('retention_jours', 10)))
    try:
        shutil.rmtree(home_jetable, ignore_errors=True)
    except OSError:
        pass
    return recu


# ---------------------------------------------------------------------------
# Consultation : status / list
# ---------------------------------------------------------------------------

def recu_par_run(run_id, base=None):
    recu = charger_recu(run_dir(run_id, base))
    if recu is None:
        raise JugeError('RUN_INCONNU', 'Recu introuvable pour le run %s' % run_id)
    return recu


def recus_recents(limite=10, base=None):
    dossier = runs_dir(base)
    recus = []
    if os.path.isdir(dossier):
        entrees = []
        for nom in os.listdir(dossier):
            recu = charger_recu(os.path.join(dossier, nom))
            if recu:
                entrees.append((recu.get('fin_ts') or 0, recu))
        entrees.sort(key=lambda paire: paire[0], reverse=True)
        recus = [recu for _tri, recu in entrees[:max(1, limite)]]
    return recus


# ---------------------------------------------------------------------------
# CLI — python3 reprise/juge_paix.py (et shim bin/reprise-juge, lot S4)
# ---------------------------------------------------------------------------

def _cmd_run(args):
    recu = run(args.acteur, args.worktree, parallel=args.parallel, objet=args.objet)
    print(json.dumps(recu, ensure_ascii=False, indent=2))
    return 0


def _cmd_status(args):
    if args.run:
        print(json.dumps(recu_par_run(args.run), ensure_ascii=False, indent=2))
        return 0
    recus = recus_recents(limite=args.recent)
    print(json.dumps(recus, ensure_ascii=False, indent=2))
    return 0


def _cmd_list(args):
    recus = recus_recents(limite=args.recent)
    for recu in recus:
        eligible = 'eligible-E6' if eligible_e6(recu) else 'non-eligible'
        print('%s  %s  %-9s code=%-4s %s  %s  %s' % (
            recu.get('fin', '?'), recu.get('acteur', '?'), recu.get('etat', '?'),
            recu.get('code', '-'), str(recu.get('duree_s', '?')) + 's',
            (recu.get('sha_arbre') or '')[:8], eligible,
        ))
    return 0


def _cmd_init(_args):
    politique = init_politique()
    print('Politique du juge écrite : %s' % policy_path())
    print('Acteurs : %s' % ', '.join(sorted(politique['acteurs'])))
    return 0


def build_parser():
    parser = argparse.ArgumentParser(description='Juge de paix Reprise (contrat SERVICES-JUGE-DEPOT §2)')
    sous = parser.add_subparsers(dest='cmd', required=True)
    p_run = sous.add_parser('run', help='vérifier un arbre (reçu factual)')
    p_run.add_argument('--acteur', required=True, help='harness demandeur (audit + politique)')
    p_run.add_argument('--worktree', required=True)
    p_run.add_argument('--parallel', type=int, default=4)
    p_run.add_argument('--objet', default='', help='lot ou objet vérifié (tracé au reçu, croisement SOD)')
    p_run.set_defaults(func=_cmd_run)
    p_status = sous.add_parser('status', help='reçu par run_id ou N derniers reçus')
    p_status.add_argument('--run', default='')
    p_status.add_argument('--recent', type=int, default=10)
    p_status.set_defaults(func=_cmd_status)
    p_list = sous.add_parser('list', help='liste lisible des derniers reçus')
    p_list.add_argument('--recent', type=int, default=10)
    p_list.set_defaults(func=_cmd_list)
    p_init = sous.add_parser('init', help='amorce la politique par défaut (sans auto-élévation)')
    p_init.set_defaults(func=_cmd_init)
    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except JugeError as exc:
        print('Refus %s : %s' % (exc.code, exc.message), file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
