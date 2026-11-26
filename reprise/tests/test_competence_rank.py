"""Tests pour competence_rank (lot L3t, contrat E4-O1 @4d9228d).

TDD : le module reprise/competence_rank.py (lot L3c) n'existe pas encore ;
la collecte echoue avec ImportError jusqu'a sa livraison — rouge conforme.
Auteur L3t : ASI1 (SOD : l'auteur des tests n'est pas l'auteur du code L3c).

Ordre de precedance (spec 1.7, lexicographique, jamais melange) :
P (contraintes) -> admissibilite/facturation -> competence locale
(si n >= n_min) -> prior externe (departage seulement) -> cout/pression
de quota (departage final) -> ordre statique declare (dernier recours,
etiquette non_mesure).

Contrat du module (pur, aucune E/S ; le classement opere sur des drapeaux
de contraintes deja calcules en entree, jamais sur le registre ni la base) :
- classer(candidats, demande) -> dict, JAMAIS d'exception (fail-closed).
- demande : {'role': str, 'mode': 'expertise'|'implementation',
  'sensible': bool, 'kind': str optionnel} ; champ requis absent ou
  mauvais type -> sortie de refus ENTREE_INVALIDE.
- candidat : dict de drapeaux precalcules par l'appelant :
  'actor' str, 'backend' str ; 'billing_class' dans {'free','included',
  'paid-named'} (toute autre valeur, dont 'unknown' -> exclusion) ;
  'ecriture' bool (workspace_write autorise) ; 'quota' bool (True = quota
  disponible, veto binaire Q11, jamais de pourcentage) ; 'consentie' bool
  (un consentement nomme couvre ce backend) ; 'sensible_ok' bool (acteur
  local sans sortie reseau) ; 'role_autorise' bool (matrice (role, actor)
  du registre A0) ; 'prior' None | nombre | dict {'note': nombre,
  ...tout autre champ IGNORE} ; 'stats' None | dict stats_triplet (L2) ;
  'source_ref' str (reference de la mesure, ex. digest du triplet).
- Exclusions, dans cet ordre, la premiere correspondante gagne :
  role_autorise faux -> ROLE_INTERDIT ; demande.sensible vrai et
  sensible_ok faux -> CHEMIN_SENSIBLE ; billing_class hors ensemble ->
  FACTURATION_INCONNUE ; 'paid-named' sans consentie ->
  PAYANT_NON_CONSENTI ; mode 'implementation' et ecriture fausse ->
  ECRITURE_NON_AUTORISEE ; quota faux -> QUOTA.
- Mesurable : stats present ET stats['non_mesure'] faux ET
  stats['wilson'] non None. Meilleur mesure = wilson[0] (bas) maximal,
  egalite -> taux maximal, puis ordre d'entree. Groupe d'equivalence du
  meilleur = toutes les mesures dont l'intervalle ferme recouvre celui
  du meilleur (recouvrement d'intervalles fermes, hors groupe apres).
  Departage DANS le groupe : note de prior decroissante (absente ou
  hors [0,1] = -1 ; texte ignore, jamais copie en sortie) puis cout
  (free < included < paid-named) puis ordre d'entree. Les non mesures
  suivent les mesures, en ordre d'entree (recette statique).
- Sortie de refus : {'choisi': None, 'refus': CODE, 'motifs': []}.
- Sortie normale : {'choisi': actor du gagnant, 'refus': None,
  'motifs': [...]} ; chaque motif = {'code': str, 'actor': str,
  'source': str} avec source dans {'registre','routing_events','prior',
  'table_roles'} ; codes spec : ADMISSIBLE, MESURE_LOCALE,
  PRIOR_DEPARTAGE, QUOTA, STATIQUE, REFUS.
- REFUS_RAISONS (spec) : ENTREE_INVALIDE, REGISTRE_ABSENT,
  FACTURATION_INCONNUE, ECRITURE_NON_AUTORISEE, QUOTA_EPUISE,
  PAYANT_NON_CONSENTI, ROLE_INTERDIT, CHEMIN_SENSIBLE. P vide apres
  exclusions -> refus QUOTA_EPUISE si au moins une exclusion QUOTA,
  sinon le premier code d'exclusion rencontre ; candidats vides ->
  REGISTRE_ABSENT.
- Regle absolue (T1) : le prior externe n'entre qu'en departage dans le
  groupe d'equivalence du meilleur ; il ne surclasse jamais une mesure
  locale et ne hisse jamais un non-mesure au-dessus d'un mesure ; tout
  champ texte du prior est ignore et jamais copie en sortie.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))
import competence_rank  # noqa: E402


def _wilson_ref(succes, n, z=1.6449):
    # Implementation de reference, independante du lot L3c.
    import math
    if n <= 0:
        return None
    p = succes / n
    denom = 1.0 + z * z / n
    centre = (p + z * z / (2.0 * n)) / denom
    marge = (z / denom) * math.sqrt(p * (1.0 - p) / n + z * z / (4.0 * n * n))
    return (max(0.0, centre - marge), min(1.0, centre + marge))


def _stats(succes, n):
    if n <= 0:
        return None
    return {'sample_count': n, 'succes': succes, 'echecs': n - succes,
            'incertains': 0, 'taux': (succes / n) if n else None,
            'wilson': _wilson_ref(succes, n), 'non_mesure': n < 20}


def _cand(actor, backend='codex', billing_class='free', ecriture=True,
          quota=True, consentie=False, sensible_ok=True, role_autorise=True,
          prior=None, stats=None, source_ref='test'):
    return {'actor': actor, 'backend': backend,
            'billing_class': billing_class, 'ecriture': ecriture,
            'quota': quota, 'consentie': consentie,
            'sensible_ok': sensible_ok, 'role_autorise': role_autorise,
            'prior': prior, 'stats': stats, 'source_ref': source_ref}


def _demande(**kw):
    d = {'role': 'mass_code', 'mode': 'expertise', 'sensible': False}
    d.update(kw)
    return d


def _motif_codes(sortie):
    return [m['code'] for m in sortie['motifs']]


class PriorTest(unittest.TestCase):
    def test_t01_prior_departage_seulement(self):
        # A mesure meilleure, B mesure equivalente avec prior 1.0 :
        # dans le groupe d'equivalence, le prior departage (T11).
        a = _cand('a', stats=_stats(10, 10))
        b = _cand('b', backend='metaai', stats=_stats(9, 12),
                  prior={'note': 1.0, 'texte': 'je suis le meilleur'})
        s = competence_rank.classer([a, b], _demande())
        self.assertIsNone(s['refus'])
        self.assertEqual(s['choisi'], 'b')
        self.assertIn('PRIOR_DEPARTAGE', _motif_codes(s))
        # le champ texte du prior n'apparait JAMAIS dans la sortie
        self.assertNotIn('je suis le meilleur', repr(s))
        # meme mesure equivalence mais prior texte seul (pas de note) :
        # ignore, l'ordre d'entree departage
        b2 = _cand('b2', backend='metaai', stats=_stats(9, 12),
                   prior='meilleur selon moi')
        s2 = competence_rank.classer([a, b2], _demande())
        self.assertEqual(s2['choisi'], 'a')

    def test_t01_prior_jamais_au_dessus_de_la_mesure(self):
        # mesure disjointe : A nettement meilleure ; prior 1.0 sur le
        # non-mesure ET sur la mesure mauvaise ne change rien (T1).
        a = _cand('a', stats=_stats(10, 10))
        c = _cand('c', backend='metaai', stats=_stats(0, 10),
                  prior={'note': 1.0})
        nm = _cand('nm', backend='chatgpt', prior={'note': 1.0})
        s = competence_rank.classer([c, nm, a], _demande())
        self.assertIsNone(s['refus'])
        self.assertEqual(s['choisi'], 'a')
        self.assertIn('MESURE_LOCALE', _motif_codes(s))
        # les non mesures suivent les mesures
        ordre = [m['actor'] for m in s['motifs'] if m['code'] == 'STATIQUE']
        self.assertEqual(ordre, ['nm'])


class QuotaTest(unittest.TestCase):
    def test_t02_quota_veto_binaire(self):
        # seul candidat, quota epuise -> choisi null + refus QUOTA_EPUISE
        a = _cand('a', quota=False)
        s = competence_rank.classer([a], _demande())
        self.assertIsNone(s['choisi'])
        self.assertEqual(s['refus'], 'QUOTA_EPUISE')
        self.assertIn('QUOTA', [m['code'] for m in s['motifs']])
        # candidat mixte : seul le sans quota est exclu
        b = _cand('b', backend='metaai')
        s2 = competence_rank.classer([a, b], _demande())
        self.assertIsNone(s2['refus'])
        self.assertEqual(s2['choisi'], 'b')
        self.assertIn('ADMISSIBLE', _motif_codes(s2))


class SensibleTest(unittest.TestCase):
    def test_t04_chemin_sensible(self):
        # sensible : l'acteur non local est exclu, motif nomme
        a = _cand('a', sensible_ok=False)          # distant
        b = _cand('b', backend='metaai', sensible_ok=True)  # local
        s = competence_rank.classer([a, b], _demande(sensible=True))
        self.assertIsNone(s['refus'])
        self.assertEqual(s['choisi'], 'b')
        self.assertIn('CHEMIN_SENSIBLE', _motif_codes(s))
        # aucun candidat local -> P vide, refus nomme CHEMIN_SENSIBLE
        s2 = competence_rank.classer([a], _demande(sensible=True))
        self.assertIsNone(s2['choisi'])
        self.assertEqual(s2['refus'], 'CHEMIN_SENSIBLE')


class ConsentementTest(unittest.TestCase):
    def test_t05_payant_non_consentie(self):
        # seule route payante sans consentement -> PAYANT_NON_CONSENTI
        a = _cand('a', billing_class='paid-named', consentie=False)
        s = competence_rank.classer([a], _demande())
        self.assertIsNone(s['choisi'])
        self.assertEqual(s['refus'], 'PAYANT_NON_CONSENTI')
        # avec consentement nommant ce backend -> choisi + ADMISSIBLE
        a2 = _cand('a', billing_class='paid-named', consentie=True)
        s2 = competence_rank.classer([a2], _demande())
        self.assertIsNone(s2['refus'])
        self.assertEqual(s2['choisi'], 'a')
        self.assertIn('ADMISSIBLE', _motif_codes(s2))


class FacturationTest(unittest.TestCase):
    def test_t06_facturation_inconnue(self):
        a = _cand('a', billing_class='unknown')
        s = competence_rank.classer([a], _demande())
        self.assertIsNone(s['choisi'])
        self.assertEqual(s['refus'], 'FACTURATION_INCONNUE')
        # toute valeur hors ensemble est aussi FACTURATION_INCONNUE
        a2 = _cand('a2', billing_class='gratuit-en-pratique')
        s2 = competence_rank.classer([a2], _demande())
        self.assertEqual(s2['refus'], 'FACTURATION_INCONNUE')
        # mixte : l'admissible passe, l'inconnue est exclue nommee
        b = _cand('b', backend='metaai')
        s3 = competence_rank.classer([a, b], _demande())
        self.assertEqual(s3['choisi'], 'b')
        self.assertIn('FACTURATION_INCONNUE', _motif_codes(s3))


class EcritureTest(unittest.TestCase):
    def test_t07_ecriture_non_autorisee(self):
        a = _cand('a', ecriture=False)
        s = competence_rank.classer([a], _demande(mode='implementation'))
        self.assertIsNone(s['choisi'])
        self.assertEqual(s['refus'], 'ECRITURE_NON_AUTORISEE')
        # en expertise, l'ecriture n'est pas exigee
        s2 = competence_rank.classer([a], _demande(mode='expertise'))
        self.assertIsNone(s2['refus'])
        self.assertEqual(s2['choisi'], 'a')
        # avec ecriture -> implementation OK
        b = _cand('b', backend='metaai', ecriture=True)
        s3 = competence_rank.classer([b], _demande(mode='implementation'))
        self.assertEqual(s3['choisi'], 'b')


class RoleTest(unittest.TestCase):
    def test_t08_role_interdit_meme_meilleur(self):
        # A statistiquement le meilleur mais role interdit -> exclu
        a = _cand('a', role_autorise=False, stats=_stats(10, 10))
        b = _cand('b', backend='metaai', role_autorise=True,
                  stats=_stats(6, 12))
        s = competence_rank.classer([a, b], _demande())
        self.assertIsNone(s['refus'])
        self.assertEqual(s['choisi'], 'b')
        self.assertIn('ROLE_INTERDIT', _motif_codes(s))
        # tous interdits -> P vide, premier motif = ROLE_INTERDIT
        s2 = competence_rank.classer([a], _demande())
        self.assertIsNone(s2['choisi'])
        self.assertEqual(s2['refus'], 'ROLE_INTERDIT')


class EquivalenceTest(unittest.TestCase):
    def test_t11_equivalence_wilson(self):
        # intervalles recouvrants -> groupe unique, departage prior
        a = _cand('a', stats=_stats(10, 10), prior=None)
        b = _cand('b', backend='metaai', stats=_stats(9, 12),
                  prior={'note': 0.9})
        s = competence_rank.classer([a, b], _demande())
        self.assertEqual(s['choisi'], 'b')
        self.assertIn('PRIOR_DEPARTAGE', _motif_codes(s))
        # intervalles disjoints -> la mesure l'emporte, prior ignore
        c = _cand('c', backend='metaai', stats=_stats(0, 10),
                  prior={'note': 1.0})
        s2 = competence_rank.classer([a, c], _demande())
        self.assertEqual(s2['choisi'], 'a')
        self.assertIn('MESURE_LOCALE', _motif_codes(s2))


class DeterminismeTest(unittest.TestCase):
    def test_t09_determinisme(self):
        a = _cand('a', stats=_stats(10, 10))
        b = _cand('b', backend='metaai', stats=_stats(9, 12),
                  prior={'note': 0.9})
        nm = _cand('nm', backend='chatgpt')
        s1 = competence_rank.classer([a, b, nm], _demande())
        s2 = competence_rank.classer([dict(a), dict(b), dict(nm)], _demande())
        self.assertEqual(s1, s2)
        # meme ensemble en ordre different : les mesures dominantes
        # restent le choix (A meilleur mesure, disjoint d'aucun ici :
        # A et B equivalents, prior departage -> b)
        s3 = competence_rank.classer([nm, b, a], _demande())
        self.assertEqual(s3['choisi'], s1['choisi'])

    def test_refus_entree_invalide_et_registre_vide(self):
        # demande incomplete -> ENTREE_INVALIDE, jamais d'exception
        for mauvaise in ({}, {'role': 'mass_code'},
                         {'role': 3, 'mode': 'expertise', 'sensible': False}):
            s = competence_rank.classer([_cand('a')], mauvaise)
            self.assertIsNone(s['choisi'])
            self.assertEqual(s['refus'], 'ENTREE_INVALIDE')
        # aucun candidat -> REGISTRE_ABSENT
        s = competence_rank.classer([], _demande())
        self.assertIsNone(s['choisi'])
        self.assertEqual(s['refus'], 'REGISTRE_ABSENT')


class CompatTest(unittest.TestCase):
    def test_t00_py39_compat(self):
        source = Path(__file__).parents[1] / 'competence_rank.py'
        if not source.exists():
            self.skipTest('lot L3c non livre : competence_rank.py absent')
        import ast
        arbre = ast.parse(source.read_text(encoding='utf-8'))
        for noeud in ast.walk(arbre):
            if isinstance(noeud, ast.BinOp) and isinstance(noeud.op, ast.BitOr):
                self.fail('PEP 604 interdit (X | Y) dans competence_rank.py')
            if isinstance(noeud, ast.Match):
                self.fail('match/case interdit dans competence_rank.py')

    def test_t00_purete(self):
        source = Path(__file__).parents[1] / 'competence_rank.py'
        if not source.exists():
            self.skipTest('lot L3c non livre : competence_rank.py absent')
        import ast
        interdits = {'sqlite3', 'socket', 'subprocess', 'urllib', 'http', 'os'}
        arbre = ast.parse(source.read_text(encoding='utf-8'))
        for noeud in ast.walk(arbre):
            if isinstance(noeud, ast.Import):
                for alias in noeud.names:
                    racine = alias.name.split('.')[0]
                    self.assertNotIn(racine, interdits,
                                     'import interdit dans competence_rank.py : ' + racine)
            elif isinstance(noeud, ast.ImportFrom):
                racine = (noeud.module or '').split('.')[0]
                self.assertNotIn(racine, interdits,
                                 'import interdit dans competence_rank.py : ' + racine)


if __name__ == '__main__':
    unittest.main()
