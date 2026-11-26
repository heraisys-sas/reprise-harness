"""Tests pour competence_stats (lot L2t, contrat E4-O1 @4d9228d).

TDD : le module reprise/competence_stats.py (lot L2c) n'existe pas encore ;
la collecte echoue avec ImportError jusqu'a sa livraison — rouge conforme.
Auteur L2t : ASI1 (SOD : l'auteur des tests n'est pas l'auteur du code L2c).

Contrat du module (pur, aucune E/S) :
- N_MIN = 20, FENETRE_JOURS = 30, FENETRE_OBS = 50, WILSON_Z = 1.6449.
- fenetre(obs, maintenant) : garde ts dans [maintenant - 30 j, maintenant]
  (bornes incluses) puis au plus les 50 plus recentes ; tri descendant par
  ts, egalite -> ordre d'entree conserve.
- wilson(succes, n_mesure) -> (bas, haut) a 90 % (z = 1.6449), None si n <= 0,
  None aussi si succes hors [0, n] (fail-closed, jamais de sqrt negatif).
- equivalentes(iv_a, iv_b) -> True si intervalles fermes recouvrent.
- stats_triplet(obs) -> {'sample_count','succes','echecs','incertains',
  'taux','wilson','non_mesure'} ; issue dans {'succes_verifie','echec',
  'incertain','refuse'} ; echecs = echec + refuse ; incertain ni succes ni
  echec ; taux et wilson sur (succes, succes+echecs), None si n_mesure 0 ;
  non_mesure = sample_count < N_MIN.
- derive_role_kind(tool, level, table) -> (role, kind) ou (None, None).
- groupe_par_triplet(obs, table) -> (groupes, ignores).
"""
import math
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))
import competence_stats  # noqa: E402


BASE = datetime(2026, 11, 27, 12, 0, tzinfo=timezone.utc)


def _ts(jours, minutes=0):
    return (BASE - timedelta(days=jours, minutes=minutes)).isoformat()


def _obs(jours, issue, minutes=0, backend='metaai', tool='expert_delegate',
         level='standard'):
    return {'ts': _ts(jours, minutes), 'issue': issue, 'backend': backend,
            'tool': tool, 'level': level, 'actor': backend}


def _wilson_ref(succes, n, z=1.6449):
    # Implementation de reference, independante du lot L2c.
    if n <= 0:
        return None
    p = succes / n
    denom = 1.0 + z * z / n
    centre = (p + z * z / (2.0 * n)) / denom
    marge = (z / denom) * math.sqrt(p * (1.0 - p) / n + z * z / (4.0 * n * n))
    return (max(0.0, centre - marge), min(1.0, centre + marge))


class FenetreTest(unittest.TestCase):
    def test_fenetre_30_jours_et_50_obs(self):
        obs = [_obs(j, 'succes_verifie') for j in range(60)]
        gardees = competence_stats.fenetre(obs, _ts(0))
        # la fenetre 30 jours sort les obs de plus de 30 jours
        self.assertEqual(len(gardees), 31)  # jours 0..30 inclus
        self.assertTrue(all(o['ts'] >= _ts(30) for o in gardees))
        # la cap 50 observations garde les 50 plus recentes
        obs_jour = [_obs(0, 'succes_verifie', minutes=i) for i in range(60)]
        gardees2 = competence_stats.fenetre(obs_jour, _ts(0))
        self.assertEqual(len(gardees2), 50)
        attendus = {_ts(0, i) for i in range(50)}  # les 50 plus recentes
        self.assertEqual({o['ts'] for o in gardees2}, attendus)

    def test_fenetre_bords_et_determinisme(self):
        obs = [_obs(31, 'succes_verifie'), _obs(30, 'echec'),  # 31 dehors, 30 borne incluse
               _obs(-1, 'succes_verifie'),                     # futur exclu
               _obs(0, 'incertain'), _obs(0, 'echec')]         # egalite de ts
        gardees = competence_stats.fenetre(obs, _ts(0))
        issues = [o['issue'] for o in gardees]
        self.assertEqual(issues, ['incertain', 'echec', 'echec'])
        # tri descendant : la premiere est la plus recente
        self.assertTrue(gardees[0]['ts'] >= gardees[-1]['ts'])
        # meme entree -> meme sortie
        self.assertEqual(competence_stats.fenetre(obs, _ts(0)), gardees)

    def test_fenetre_entrees_degenerees(self):
        # P1(e) c6 §5 : non-list -> [] ; maintenant non parseable -> []
        # (fail-closed) ; obs a ts non parseable ignoree.
        self.assertEqual(competence_stats.fenetre('x', _ts(0)), [])
        self.assertEqual(competence_stats.fenetre(None, _ts(0)), [])
        self.assertEqual(competence_stats.fenetre([_obs(0, 'echec')], 'pas-une-date'), [])
        corrompue = _obs(0, 'echec')
        corrompue['ts'] = 'x'
        self.assertEqual(competence_stats.fenetre([corrompue], _ts(0)), [])


class WilsonTest(unittest.TestCase):
    def test_wilson_formule_reference(self):
        for succes, n in [(9, 12), (0, 10), (10, 10), (3, 20), (1, 1)]:
            iv = competence_stats.wilson(succes, n)
            ref = _wilson_ref(succes, n)
            self.assertIsNotNone(iv)
            for a, b in zip(iv, ref):
                self.assertAlmostEqual(a, b, places=9)
        self.assertAlmostEqual(competence_stats.wilson(0, 10)[0], 0.0, places=9)
        self.assertAlmostEqual(competence_stats.wilson(10, 10)[1], 1.0, places=9)

    def test_wilson_cas_degrades(self):
        self.assertIsNone(competence_stats.wilson(5, 0))
        self.assertIsNone(competence_stats.wilson(5, -1))

    def test_e701_comptages_entiers_stricts(self):
        # E7-01 : un nombre d'observations est un entier strict ; flottants
        # (10.0, 10.5), NaN/inf et booleens refuses des deux cotes.
        for args in [(10.0, 10), (10, 10.0), (10.5, 10), (10, 10.5),
                     (True, 10), (10, False),
                     (float('nan'), 10), (10, float('nan')),
                     (float('inf'), 10), (10, float('-inf'))]:
            self.assertIsNone(competence_stats.wilson(*args), args)
        # entiers valides aux bornes restent mesures.
        self.assertEqual(competence_stats.wilson(10, 10), _wilson_ref(10, 10))
        self.assertEqual(competence_stats.wilson(0, 5), _wilson_ref(0, 5))


class EquivalenceTest(unittest.TestCase):
    def test_t11_equivalence(self):
        # 9/12 et 6/12 : intervalles de Wilson qui se recouvrent
        self.assertTrue(competence_stats.equivalentes(
            competence_stats.wilson(9, 12), competence_stats.wilson(6, 12)))
        # 10/10 et 0/10 : intervalles disjoints
        self.assertFalse(competence_stats.equivalentes(
            competence_stats.wilson(10, 10), competence_stats.wilson(0, 10)))
        # symetrie
        a, b = competence_stats.wilson(9, 12), competence_stats.wilson(6, 12)
        self.assertEqual(competence_stats.equivalentes(a, b),
                         competence_stats.equivalentes(b, a))


class StatsTripletTest(unittest.TestCase):
    def test_t03_echantillon_insuffisant(self):
        obs19 = [_obs(0, 'succes_verifie', minutes=i) for i in range(19)]
        stats = competence_stats.stats_triplet(obs19)
        self.assertTrue(stats['non_mesure'])
        obs20 = obs19 + [_obs(0, 'echec', minutes=19)]
        self.assertFalse(competence_stats.stats_triplet(obs20)['non_mesure'])

    def test_t10_incertains_a_part(self):
        obs = ([_obs(0, 'succes_verifie', minutes=i) for i in range(9)]
               + [_obs(0, 'echec', minutes=10 + i) for i in range(3)]
               + [_obs(0, 'incertain', minutes=13 + i) for i in range(8)])
        stats = competence_stats.stats_triplet(obs)
        self.assertEqual(stats['sample_count'], 20)
        self.assertEqual(stats['succes'], 9)
        self.assertEqual(stats['echecs'], 3)
        self.assertEqual(stats['incertains'], 8)
        self.assertFalse(stats['non_mesure'])
        # incertain n'est ni succes ni echec : taux sur 12, pas sur 20
        self.assertAlmostEqual(stats['taux'], 9.0 / 12.0, places=9)
        ref = _wilson_ref(9, 12)
        self.assertAlmostEqual(stats['wilson'][0], ref[0], places=9)
        self.assertAlmostEqual(stats['wilson'][1], ref[1], places=9)

    def test_refuse_compte_echec_et_cles_exactes(self):
        obs = [_obs(0, 'succes_verifie'), _obs(0, 'echec', minutes=1),
               _obs(0, 'refuse', minutes=2), _obs(0, 'incertain', minutes=3)]
        stats = competence_stats.stats_triplet(obs)
        self.assertEqual(stats['succes'], 1)
        self.assertEqual(stats['echecs'], 2)  # echec + refuse
        self.assertEqual(stats['incertains'], 1)
        self.assertEqual(stats['sample_count'], 4)
        self.assertAlmostEqual(stats['taux'], 1.0 / 3.0, places=9)
        self.assertEqual(set(stats.keys()), {'sample_count', 'succes', 'echecs',
                                             'incertains', 'taux', 'wilson',
                                             'non_mesure'})

    def test_taux_nul_si_aucune_mesure(self):
        obs = [_obs(0, 'incertain', minutes=i) for i in range(20)]
        stats = competence_stats.stats_triplet(obs)
        self.assertIsNone(stats['taux'])
        self.assertIsNone(stats['wilson'])
        self.assertFalse(stats['non_mesure'])  # 20 observations terminales

    def test_t09_determinisme(self):
        obs = ([_obs(0, 'succes_verifie', minutes=i) for i in range(9)]
               + [_obs(0, 'echec', minutes=10 + i) for i in range(11)])
        a = competence_stats.stats_triplet(obs)
        b = competence_stats.stats_triplet(list(obs))
        self.assertEqual(a, b)


class MappingTest(unittest.TestCase):
    def test_derive_role_kind(self):
        table = {('expert_delegate', 'standard'): ('mass_code', 'lot')}
        self.assertEqual(competence_stats.derive_role_kind('expert_delegate', 'standard', table),
                         ('mass_code', 'lot'))
        self.assertEqual(competence_stats.derive_role_kind('autre', 'standard', table),
                         (None, None))
        self.assertEqual(competence_stats.derive_role_kind('expert_delegate', 'standard', None),
                         (None, None))

    def test_groupe_par_triplet_ignore_non_projetables(self):
        table = {('expert_delegate', 'standard'): ('mass_code', 'lot')}
        obs = [_obs(0, 'succes_verifie', minutes=i) for i in range(3)]
        obs.append(_obs(0, 'echec', minutes=3, tool='autre_outil'))
        obs.append(_obs(0, 'echec', minutes=4))
        del obs[-1]['backend']
        groupes, ignores = competence_stats.groupe_par_triplet(obs, table)
        self.assertEqual(set(groupes.keys()), {('metaai', 'mass_code', 'lot')})
        self.assertEqual(len(groupes[('metaai', 'mass_code', 'lot')]), 3)
        self.assertEqual(len(ignores), 2)


class FailClosedTest(unittest.TestCase):
    def test_t12_wilson_succes_hors_borne_none(self):
        # succes > n ou < 0 : fail-closed (avant correctif : ValueError sqrt).
        self.assertIsNone(competence_stats.wilson(15, 10))
        self.assertIsNone(competence_stats.wilson(-1, 10))
        self.assertIsNone(competence_stats.wilson(10.5, 10))
        # bornes incluses restent mesurables.
        self.assertEqual(competence_stats.wilson(10, 10), _wilson_ref(10, 10))
        self.assertEqual(competence_stats.wilson(0, 5), _wilson_ref(0, 5))

    def test_t13_sample_count_ne_compte_que_les_terminales(self):
        # Les obs non terminales (issue inconnue, non-dict) n'inflechissent ni
        # sample_count ni la porte N_MIN (spec §1.4 : 20 observations terminales).
        valides = [_obs(0, 'succes_verifie', minutes=i) for i in range(19)]
        valides.append(_obs(0, 'echec', minutes=19))
        corrompus = [_obs(0, 'issue-inconnue', minutes=20),
                     {'ts': _ts(0), 'issue': None, 'backend': 'metaai'}]
        s = competence_stats.stats_triplet(valides + corrompus)
        self.assertEqual(s['sample_count'], 20)
        self.assertFalse(s['non_mesure'])
        s19 = competence_stats.stats_triplet(valides[:19] + corrompus)
        self.assertEqual(s19['sample_count'], 19)
        self.assertTrue(s19['non_mesure'])


    def test_e704_tableau_n_min_terminales(self):
        # E7-04 : N_MIN porte sur les observations TERMINALES (spec §1.4, Q2
        # CONFIRMED). Tableau c6 : 19+1/20 mesurable ; 20+0/20 mesurable ;
        # 0+20/20 mesurable au sens N_MIN mais wilson None (voie statique au
        # rang) ; 19+0/19 non mesurable.
        obs = [_obs(0, 'succes_verifie', minutes=i) for i in range(19)]
        obs.append(_obs(0, 'incertain', minutes=19))
        s = competence_stats.stats_triplet(obs)
        self.assertFalse(s['non_mesure'])
        self.assertEqual((s['succes'], s['echecs'], s['incertains']), (19, 0, 1))
        self.assertIsNotNone(s['wilson'])
        obs_e = [_obs(0, 'echec', minutes=i) for i in range(20)]
        se = competence_stats.stats_triplet(obs_e)
        self.assertFalse(se['non_mesure'])
        self.assertEqual(se['wilson'], competence_stats.wilson(0, 20))
        obs0 = [_obs(0, 'incertain', minutes=i) for i in range(20)]
        s0 = competence_stats.stats_triplet(obs0)
        self.assertFalse(s0['non_mesure'])
        self.assertIsNone(s0['wilson'])
        self.assertIsNone(s0['taux'])
        obs19 = [_obs(0, 'incertain', minutes=i) for i in range(19)]
        s19 = competence_stats.stats_triplet(obs19)
        self.assertTrue(s19['non_mesure'])


class CompatTest(unittest.TestCase):
    def test_t00_py39_compat(self):
        source = Path(__file__).parents[1] / 'competence_stats.py'
        if not source.exists():
            self.skipTest('lot L2c non livre : competence_stats.py absent')
        import ast
        arbre = ast.parse(source.read_text(encoding='utf-8'))
        for noeud in ast.walk(arbre):
            if isinstance(noeud, ast.BinOp) and isinstance(noeud.op, ast.BitOr):
                self.fail('PEP 604 interdit (X | Y) dans competence_stats.py')
            if isinstance(noeud, ast.Match):
                self.fail('match/case interdit dans competence_stats.py')

    def test_t00_purete(self):
        source = Path(__file__).parents[1] / 'competence_stats.py'
        if not source.exists():
            self.skipTest('lot L2c non livre : competence_stats.py absent')
        import ast
        interdits = {'sqlite3', 'socket', 'subprocess', 'urllib', 'http', 'os'}
        arbre = ast.parse(source.read_text(encoding='utf-8'))
        for noeud in ast.walk(arbre):
            if isinstance(noeud, ast.Import):
                for alias in noeud.names:
                    racine = alias.name.split('.')[0]
                    self.assertNotIn(racine, interdits,
                                     'import interdit dans competence_stats.py : ' + racine)
            elif isinstance(noeud, ast.ImportFrom):
                racine = (noeud.module or '').split('.')[0]
                self.assertNotIn(racine, interdits,
                                 'import interdit dans competence_stats.py : ' + racine)


if __name__ == '__main__':
    unittest.main()
