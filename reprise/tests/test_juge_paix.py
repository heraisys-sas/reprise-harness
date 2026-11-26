# -*- coding: utf-8 -*-
"""Tests du juge de paix (contrat SERVICES-JUGE-DEPOT v1.2 §11, critères J1-J8).

Hermetiques : state dir jetable (REPRISE_JUGE_ETAT), arbres Git jetables avec
un ci_test fictif, aucune operation reseau, aucun arbre reel touche.
Auteur des tests : ASI1 (session Services 26-27/11/2026, SOD de session).
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import juge_paix  # noqa: E402


def _git(cwd, *args):
    proc = subprocess.run(
        ['git', '-C', cwd, '-c', 'user.email=test@reprise.local', '-c', 'user.name=Tests'] + list(args),
        capture_output=True, text=True, timeout=60,
    )
    if proc.returncode != 0:
        raise AssertionError('git %s a échoué : %s' % (' '.join(args), proc.stderr.strip()))
    return proc.stdout.strip()


CI_TEST_FICTIF = """# -*- coding: utf-8 -*-
import os, sys, time
DORMIR = float(sys.argv[sys.argv.index('--dormir') + 1]) if '--dormir' in sys.argv else 0.0
CODE = int(sys.argv[sys.argv.index('--code') + 1]) if '--code' in sys.argv else 0
LOG = '--sans-log' not in sys.argv
DOSSIER = os.path.join(os.path.dirname(os.path.abspath(__file__)), '.runtime-py')
LOG_CHEMIN = os.path.join(DOSSIER, 'test-parallel.log')
if LOG:
    os.makedirs(DOSSIER, exist_ok=True)
    with open(LOG_CHEMIN, 'a', encoding='utf-8') as h:
        h.write('debut\\n')
time.sleep(DORMIR)
if LOG:
    with open(LOG_CHEMIN, 'a', encoding='utf-8') as h:
        h.write('===== Shard 1/1 exit=%d =====\\n' % CODE)
        h.write('fin\\n')
sys.exit(CODE)
"""


class BaseJuge(unittest.TestCase):
    """Socle : state dir jetable, arbre Git jetable avec ci_test fictif."""

    def setUp(self):
        self.base = tempfile.mkdtemp(prefix='juge-etat-')
        self.racine_tmp = tempfile.mkdtemp(prefix='juge-arbres-')
        self.ancien_env = os.environ.get('REPRISE_JUGE_ETAT')
        os.environ['REPRISE_JUGE_ETAT'] = self.base
        self.politique = juge_paix.politique_defaut()
        juge_paix.ecrire_politique(self.politique)

    def tearDown(self):
        if self.ancien_env is None:
            os.environ.pop('REPRISE_JUGE_ETAT', None)
        else:
            os.environ['REPRISE_JUGE_ETAT'] = self.ancien_env
        shutil.rmtree(self.base, ignore_errors=True)
        shutil.rmtree(self.racine_tmp, ignore_errors=True)

    def arbre(self, code=0, dormir=0.0, log=True):
        chemin = tempfile.mkdtemp(dir=self.racine_tmp)
        _git(chemin, 'init', '-q', '-b', 'main')
        os.makedirs(os.path.join(chemin, 'reprise'), exist_ok=True)
        with open(os.path.join(chemin, 'reprise', 'ci_test.py'), 'w', encoding='utf-8') as h:
            h.write(CI_TEST_FICTIF)
        with open(os.path.join(chemin, 'LISEZMOI.txt'), 'w', encoding='utf-8') as h:
            h.write('arbre de test\n')
        _git(chemin, 'add', '.')
        _git(chemin, 'commit', '-q', '-m', 'init')
        # Le drapeau du ci_test fictif est encodé dans le fichier même.
        with open(os.path.join(chemin, 'reprise', 'ci_test.py'), 'a', encoding='utf-8') as h:
            h.write('')
        # Paramètres : on réécrit le fichier avec ses drapeaux.
        drapeaux = ' '.join(['--code', str(code), '--dormir', str(dormir)] + ([] if log else ['--sans-log']))
        with open(os.path.join(chemin, 'reprise', 'ci_test.py'), 'w', encoding='utf-8') as h:
            h.write(CI_TEST_FICTIF + "\nsys.argv += %r\n" % drapeaux.split())
        _git(chemin, 'add', '.')
        _git(chemin, 'commit', '-q', '-m', 'ci fictif')
        return chemin

    def audit_lignes(self):
        chemin = juge_paix.audit_path(self.base)
        if not os.path.isfile(chemin):
            return []
        with open(chemin, 'r', encoding='utf-8') as h:
            return [json.loads(l) for l in h if l.strip()]


class TestGardesEntree(BaseJuge):
    """J2 + invalides + environnement : refus nommés, fail-closed."""

    def test_j2_acteur_inconnu_refuse(self):
        arbre = self.arbre()
        with self.assertRaises(juge_paix.JugeError) as ctx:
            juge_paix.run('inconnu', arbre, base=self.base)
        self.assertEqual(ctx.exception.code, 'JUGE_ACTEUR_INCONNU')
        refus = [l for l in self.audit_lignes() if l.get('refus') == 'JUGE_ACTEUR_INCONNU']
        self.assertEqual(len(refus), 1)

    def test_j2_politique_absente_fail_closed(self):
        os.unlink(juge_paix.policy_path(self.base))
        arbre = self.arbre()
        with self.assertRaises(juge_paix.JugeError) as ctx:
            juge_paix.run('autoclaw', arbre, base=self.base)
        self.assertEqual(ctx.exception.code, 'JUGE_POLITIQUE_ABSENTE')

    def test_juge_desactive(self):
        self.politique['actif'] = False
        juge_paix.ecrire_politique(self.politique)
        arbre = self.arbre()
        with self.assertRaises(juge_paix.JugeError) as ctx:
            juge_paix.run('autoclaw', arbre, base=self.base)
        self.assertEqual(ctx.exception.code, 'JUGE_DESACTIVE')

    def test_arbre_invalide(self):
        vide = tempfile.mkdtemp(dir=self.racine_tmp)
        with self.assertRaises(juge_paix.JugeError) as ctx:
            juge_paix.run('autoclaw', vide, base=self.base)
        self.assertEqual(ctx.exception.code, 'JUGE_ARBRE_INVALIDE')
        arbre_sans_suite = self.arbre()
        os.unlink(os.path.join(arbre_sans_suite, 'reprise', 'ci_test.py'))
        with self.assertRaises(juge_paix.JugeError) as ctx2:
            juge_paix.run('autoclaw', arbre_sans_suite, base=self.base)
        self.assertEqual(ctx2.exception.code, 'JUGE_ARBRE_INVALIDE')

    def test_env_herite_refuse(self):
        arbre = self.arbre()
        os.environ['REPRISE_TEST_PYTHON'] = '/faux/python'
        try:
            with self.assertRaises(juge_paix.JugeError) as ctx:
                juge_paix.run('autoclaw', arbre, base=self.base)
            self.assertEqual(ctx.exception.code, 'JUGE_ENV_REFUSE')
        finally:
            os.environ.pop('REPRISE_TEST_PYTHON', None)


class TestRunFait(BaseJuge):
    """J7, J3, J4 : reçus factuels sur arbres propres, sales, logs absents, budgets dépassés."""

    def test_j7_arbre_sale_recu_non_eligible_meme_code_zero(self):
        arbre = self.arbre(code=0)
        with open(os.path.join(arbre, 'brouillon.txt'), 'w', encoding='utf-8') as h:
            h.write('modif non committée\n')
        recu = juge_paix.run('autoclaw', arbre, base=self.base)
        self.assertEqual(recu['etat'], 'fini')
        self.assertEqual(recu['code'], 0)
        self.assertTrue(recu['arbre_sale'])
        self.assertFalse(juge_paix.eligible_e6(recu))

    def test_j3_log_absent_etat_incertain(self):
        arbre = self.arbre(code=0, log=False)
        recu = juge_paix.run('autoclaw', arbre, base=self.base)
        self.assertEqual(recu['etat'], 'incertain')
        self.assertIsNone(recu['code'])
        self.assertFalse(juge_paix.eligible_e6(recu))
        expire = [l for l in self.audit_lignes() if l.get('refus') == 'JUGE_LOG_ABSENT']
        self.assertEqual(len(expire), 1)

    def test_j4_budget_depasse_expire_et_tue_le_groupe(self):
        self.politique['delai_secondes'] = 1
        juge_paix.ecrire_politique(self.politique)
        arbre = self.arbre(code=0, dormir=4.0)
        debut = time.time()
        recu = juge_paix.run('autoclaw', arbre, base=self.base)
        duree = time.time() - debut
        self.assertEqual(recu['etat'], 'expire')
        self.assertFalse(juge_paix.eligible_e6(recu))
        self.assertLess(duree, 3.5, 'le run doit être interrompu près du budget, pas attendre la fin du ci_test')
        log_archive = os.path.join(juge_paix.run_dir(recu['run_id'], self.base), 'test-parallel.log')
        with open(log_archive, 'r', encoding='utf-8') as h:
            contenu = h.read()
        self.assertNotIn('fin', contenu, 'le groupe de processus doit être tué avant la fin du ci_test')
        depasse = [l for l in self.audit_lignes() if l.get('refus') == 'JUGE_DELAI_DEPASSE']
        self.assertEqual(len(depasse), 1)

    def test_run_fini_recu_digeste_et_eligible(self):
        arbre = self.arbre(code=0)
        recu = juge_paix.run('autoclaw', arbre, base=self.base, objet='L1c')
        self.assertEqual(recu['etat'], 'fini')
        self.assertEqual(recu['code'], 0)
        self.assertFalse(recu['arbre_sale'])
        self.assertTrue(recu['digest_log'])
        self.assertTrue(juge_paix.verifier_recu(recu))
        self.assertTrue(juge_paix.eligible_e6(recu))
        # J8 : le reçu cite digest_suite — toute modification de la suite est détectable.
        avant = recu['digest_suite']
        avec = open(os.path.join(arbre, 'reprise', 'ci_test.py'), 'a', encoding='utf-8')
        avec.write('\n# retouche après coup\n')
        avec.close()
        self.assertNotEqual(juge_paix.digest_suite(arbre), avant)


class TestConcurrence(BaseJuge):
    """J1 + file bornée + équité : jamais deux runs simultanés sur le même arbre."""

    def test_j1_deux_demandes_meme_arbre_jamais_simultanées(self):
        arbre = self.arbre(code=0, dormir=0.9)
        resultats = {}

        def worker(cle, acteur, parallel):
            resultats[cle] = juge_paix.run(acteur, arbre, parallel=parallel, base=self.base)

        premier = threading.Thread(target=worker, args=('A', 'autoclaw', 4))
        premier.start()
        time.sleep(0.35)
        second = threading.Thread(target=worker, args=('B', 'codex', 2))
        second.start()
        premier.join(60)
        second.join(60)
        a, b = resultats['A'], resultats['B']
        self.assertEqual(a['etat'], 'fini')
        self.assertEqual(b['etat'], 'fini')
        # Les fenêtres de vérification ne se chevauchent pas : le second attend (attente) puis court.
        self.assertGreaterEqual(b['debut_ts'], a['fin_ts'] - 0.05,
                                'deux exécutions simultanées sur le même arbre = violation J1')
        self.assertGreater(b['attente_s'], 0.0, 'la seconde demande doit être passée par la file (attente)')

    def test_file_pleine_refuse(self):
        self.politique['concurrence_machine'] = 1
        self.politique['file_max'] = 1
        juge_paix.ecrire_politique(self.politique)
        arbre = self.arbre()
        dossier_machine = os.path.join(self.base, 'juge', 'machine')
        os.makedirs(dossier_machine, exist_ok=True)
        with open(os.path.join(dossier_machine, 'slot-0.lock'), 'w', encoding='utf-8') as h:
            json.dump({'run_id': 'autre', 'acteur': 'metaai', 'pid': os.getpid()}, h)
        dossier_file = os.path.join(self.base, 'juge', 'file')
        os.makedirs(dossier_file, exist_ok=True)
        with open(os.path.join(dossier_file, 'en-attente.json'), 'w', encoding='utf-8') as h:
            json.dump({'run_id': 'en-attente', 'acteur': 'codex', 'depuis': time.time()}, h)
        with self.assertRaises(juge_paix.JugeError) as ctx:
            juge_paix.run('autoclaw', arbre, base=self.base)
        self.assertEqual(ctx.exception.code, 'JUGE_FILE_PLEINE')

    def test_delai_attente_ttl_epuise(self):
        self.politique['concurrence_machine'] = 1
        self.politique['delai_file_secondes'] = 1
        juge_paix.ecrire_politique(self.politique)
        arbre = self.arbre()
        dossier_machine = os.path.join(self.base, 'juge', 'machine')
        os.makedirs(dossier_machine, exist_ok=True)
        with open(os.path.join(dossier_machine, 'slot-0.lock'), 'w', encoding='utf-8') as h:
            json.dump({'run_id': 'autre', 'acteur': 'metaai', 'pid': os.getpid()}, h)
        with self.assertRaises(juge_paix.JugeError) as ctx:
            juge_paix.run('autoclaw', arbre, base=self.base)
        self.assertEqual(ctx.exception.code, 'JUGE_DELAI_ATTENTE')
        dossier_file = os.path.join(self.base, 'juge', 'file')
        self.assertEqual([n for n in os.listdir(dossier_file) if n.endswith('.json')], [],
                         'l\'entrée de file doit être retirée après un refus d\'attente')


class TestIdempotence(BaseJuge):
    """J5 : demande identique à un run fini => reçu existant, rejeu tracé, aucune relance."""

    def test_j5_rejeu_idempotent_trace(self):
        arbre = self.arbre(code=0, dormir=0.4)
        premier = juge_paix.run('autoclaw', arbre, base=self.base)
        log_recu = os.path.join(juge_paix.run_dir(premier['run_id'], self.base), 'test-parallel.log')
        mtime = os.path.getmtime(log_recu)
        time.sleep(0.05)
        second = juge_paix.run('autoclaw', arbre, base=self.base)
        self.assertEqual(premier['digest_recu'], second['digest_recu'])
        self.assertEqual(os.path.getmtime(log_recu), mtime, 'le run fini ne doit pas être relancé')
        rejeux = [l for l in self.audit_lignes() if l.get('type') == 'rejeu']
        self.assertEqual(len(rejeux), 1, 'l\'audit doit tracer le rejeu')

    def test_relance_fraiche_apres_incertain(self):
        arbre = self.arbre(code=0, log=False)
        premier = juge_paix.run('autoclaw', arbre, base=self.base)
        self.assertEqual(premier['etat'], 'incertain')
        # La suite ne change pas, mais le ci_test fictif produit maintenant un log.
        ci = os.path.join(arbre, 'reprise', 'ci_test.py')
        with open(ci, 'r', encoding='utf-8') as h:
            contenu = h.read()
        with open(ci, 'w', encoding='utf-8') as h:
            h.write(contenu.replace("'--sans-log' not in sys.argv", "True or '--sans-log' not in sys.argv"))
        _git(arbre, 'add', '.')
        _git(arbre, 'commit', '-q', '-m', 'log rétabli')
        second = juge_paix.run('autoclaw', arbre, base=self.base)
        self.assertEqual(second['etat'], 'fini')
        self.assertNotEqual(premier['run_id'], second['run_id'], 'un autre état d\'arbre = un autre run_id')


class TestSodEtPolitique(BaseJuge):
    """J6 : croisement SOD ; init : amorçage sans auto-élévation ; rétention."""

    def test_j6_croisement_sod_demandeur_egale_auteur(self):
        recus = [
            {'run_id': 'r1', 'acteur': 'autoclaw', 'objet': 'L3c', 'etat': 'fini', 'code': 0,
             'arbre_sale': False, 'digest_log': 'x'},
            {'run_id': 'r2', 'acteur': 'metaai', 'objet': 'L3c', 'etat': 'fini', 'code': 0,
             'arbre_sale': False, 'digest_log': 'x'},
            {'run_id': 'r3', 'acteur': 'autoclaw', 'objet': 'L4t', 'etat': 'incertain', 'code': None,
             'arbre_sale': False, 'digest_log': 'x'},
        ]
        violations = juge_paix.sod_violations(recus, {'L3c': 'autoclaw', 'L4t': 'autoclaw'})
        self.assertEqual(len(violations), 1)
        self.assertEqual(violations[0]['run_id'], 'r1')

    def test_init_amorcage_et_refus_double(self):
        os.unlink(juge_paix.policy_path(self.base))
        politique = juge_paix.init_politique(self.base)
        self.assertTrue(politique['actif'])
        self.assertEqual(sorted(politique['acteurs']), sorted(juge_paix.ACTEURS_DEFAUT))
        self.assertEqual(politique['retention_jours'], 10)
        self.assertEqual(politique['concurrence_machine'], 2)
        self.assertEqual(politique['delai_secondes'], 1800)
        with self.assertRaises(juge_paix.JugeError):
            juge_paix.init_politique(self.base)

    def test_retention_preuves_supprimee_audits_intacts(self):
        vieux = os.path.join(juge_paix.runs_dir(self.base), '0000vieux')
        os.makedirs(vieux, exist_ok=True)
        with open(os.path.join(vieux, 'receipt.json'), 'w', encoding='utf-8') as h:
            json.dump({'run_id': '0000vieux'}, h)
        passe = time.time() - 11 * 86400
        os.utime(vieux, (passe, passe))
        audit_chemin = juge_paix.audit_path(self.base)
        with open(audit_chemin, 'a', encoding='utf-8') as h:
            h.write(json.dumps({'type': 'run', 'memo': 'à conserver'}) + '\n')
        arbre = self.arbre(code=0)
        juge_paix.run('autoclaw', arbre, base=self.base)
        self.assertFalse(os.path.exists(vieux), 'preuve au-delà de retention_jours (10) doit être supprimée')
        with open(audit_chemin, 'r', encoding='utf-8') as h:
            lignes = [l for l in h if 'à conserver' in l]
        self.assertEqual(len(lignes), 1, 'les audits restent intacts')


class TestActivite(BaseJuge):
    """Flux activity.json (§8.4) : publié atomiquement, lu par l'UI — jamais une copie d'écran."""

    def test_activity_json_publie_avec_snapshot_juge(self):
        arbre = self.arbre(code=2)
        recu = juge_paix.run('autoclaw', arbre, base=self.base)
        chemin = juge_paix.activity_path(self.base)
        self.assertTrue(os.path.isfile(chemin))
        with open(chemin, 'r', encoding='utf-8') as h:
            flux = json.load(h)
        self.assertIn('ts', flux)
        self.assertIn('juge', flux)
        juge_flux = flux['juge']
        self.assertIn('actifs', juge_flux)
        self.assertIn('file', juge_flux)
        self.assertTrue(any(d['run_id'] == recu['run_id'] for d in juge_flux['derniers']),
                        'les derniers reçus apparaissent dans le flux')
        self.assertTrue(juge_flux['erreurs'], 'les erreurs d\'un run en échec sont visibles dans le flux')


if __name__ == '__main__':
    unittest.main()
