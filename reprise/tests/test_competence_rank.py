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
  PAYANT_NON_CONSENTI, ROLE_INTERDIT, CHEMIN_SENSIBLE, DEFAUT_INTERNE.
  P vide apres exclusions -> QUOTA_EPUISE seulement si TOUS les candidats
  sont exclus pour QUOTA (E7-03) ; sinon le code le plus severe selon la
  precedence fixe ENTREE_INVALIDE > ROLE_INTERDIT > CHEMIN_SENSIBLE >
  FACTURATION_INCONNUE > PAYANT_NON_CONSENTI > ECRITURE_NON_AUTORISEE >
  QUOTA (independant de l'ordre d'entree) ; candidats vides ->
  REGISTRE_ABSENT ; exception interne inattendue -> DEFAUT_INTERNE
  (E7-02 : diagnostic type sur stderr, aucun candidat, jamais une
  validation d'entree confondue avec un defaut de programmation).
- Regle absolue (T1) : le prior externe n'entre qu'en departage dans le
  groupe d'equivalence du meilleur ; il ne surclasse jamais une mesure
  locale et ne hisse jamais un non-mesure au-dessus d'un mesure ; tout
  champ texte du prior est ignore et jamais copie en sortie.
"""
import contextlib
import io
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
    # Stats FABRIQUEES et MESUREES (non_mesure=False) pour les tests de
    # classement ; la voie non mesure est couverte par stats=None et par
    # NonMesureFlagTest (drapeau non_mesure force a True).
    if n <= 0:
        return None
    return {'sample_count': n, 'succes': succes, 'echecs': n - succes,
            'incertains': 0, 'taux': (succes / n) if n else None,
            'wilson': _wilson_ref(succes, n), 'non_mesure': False}


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


class NonMesureFlagTest(unittest.TestCase):
    def test_non_mesure_flag_reste_non_mesure(self):
        # Un drapeau non_mesure=True fait foi : la route reste hors mesure
        # locale (gate N_MIN), meme si un intervalle wilson est present.
        stats_nm = _stats(10, 10)
        stats_nm['non_mesure'] = True
        nm_flag = _cand('nm_flag', stats=stats_nm, prior={'note': 1.0})
        mesure = _cand('mesure', backend='metaai', stats=_stats(6, 12))
        s = competence_rank.classer([nm_flag, mesure], _demande())
        self.assertIsNone(s['refus'])
        self.assertEqual(s['choisi'], 'mesure')
        codes = [(m['code'], m['actor']) for m in s['motifs']]
        self.assertIn(('STATIQUE', 'nm_flag'), codes)
        self.assertIn('MESURE_LOCALE', _motif_codes(s))


class EnumsSourceUniqueTest(unittest.TestCase):
    def test_t12_enums_reexportes_depuis_types(self):
        # Deduplication : rank n'a plus de tuples propres, il re-exporte ceux
        # de competence_types (source unique des enums spec).
        import competence_types
        self.assertIs(competence_rank.REFUS_RAISONS, competence_types.REFUS_RAISONS)
        self.assertIs(competence_rank.MOTIF_CODES, competence_types.MOTIF_CODES)


class RefusPolicyTest(unittest.TestCase):
    # E7-03 : precedence fixe des refus globaux, independante de l'ordre
    # d'entree ; QUOTA_EPUISE seulement si TOUS les candidats sont QUOTA.
    def test_tous_quota_epuise(self):
        s = competence_rank.classer([_cand('a', quota=False), _cand('b', quota=False)],
                                    _demande())
        self.assertEqual(s['refus'], 'QUOTA_EPUISE')
        self.assertIsNone(s['choisi'])

    def test_tous_role_interdit(self):
        s = competence_rank.classer([_cand('a', role_autorise=False),
                                     _cand('b', role_autorise=False)], _demande())
        self.assertEqual(s['refus'], 'ROLE_INTERDIT')

    def test_role_plus_severe_que_quota(self):
        cands = [_cand('a', role_autorise=False), _cand('b', quota=False)]
        s = competence_rank.classer(cands, _demande())
        self.assertEqual(s['refus'], 'ROLE_INTERDIT')
        # les motifs embarquent toutes les exclusions, pas seulement le code retenu.
        self.assertEqual(sorted(_motif_codes(s)), ['QUOTA', 'ROLE_INTERDIT'])

    def test_precedence_independante_de_l_ordre_d_entree(self):
        cands1 = [_cand('a', role_autorise=False), _cand('b', quota=False)]
        cands2 = [_cand('b', quota=False), _cand('a', role_autorise=False)]
        self.assertEqual(competence_rank.classer(cands1, _demande())['refus'],
                         competence_rank.classer(cands2, _demande())['refus'])

    def test_chemin_sensible_plus_severe_que_quota(self):
        cands = [_cand('a', sensible_ok=False, quota=False), _cand('b', quota=False)]
        s = competence_rank.classer(cands, _demande(sensible=True))
        self.assertEqual(s['refus'], 'CHEMIN_SENSIBLE')

    def test_payant_non_consentie_plus_severe_que_quota(self):
        cands = [_cand('a', billing_class='paid-named', consentie=False, quota=False),
                 _cand('b', quota=False)]
        s = competence_rank.classer(cands, _demande())
        self.assertEqual(s['refus'], 'PAYANT_NON_CONSENTI')

    def test_ecriture_plus_severe_que_quota(self):
        cands = [_cand('a', ecriture=False, quota=False), _cand('b', quota=False)]
        s = competence_rank.classer(cands, _demande(mode='implementation'))
        self.assertEqual(s['refus'], 'ECRITURE_NON_AUTORISEE')

    def test_facturation_inconnue_plus_severe_que_quota(self):
        cands = [_cand('a', billing_class='unknown', quota=False), _cand('b', quota=False)]
        s = competence_rank.classer(cands, _demande())
        self.assertEqual(s['refus'], 'FACTURATION_INCONNUE')

    def test_entree_invalide_plus_severe_que_quota(self):
        cands = [{'pas': 'dict'}, _cand('b', quota=False)]
        s = competence_rank.classer(cands, _demande())
        self.assertEqual(s['refus'], 'ENTREE_INVALIDE')


class InternalErrorTest(unittest.TestCase):
    # E7-02 : une exception interne inattendue doit rester identifiable
    # (DEFAUT_INTERNE), sans detail sensible, sans candidat selectionne ;
    # elle ne doit jamais etre confondue avec une validation d'entree.
    def _classe_avec_panne(self, attribut, cand):
        original = getattr(competence_rank, attribut)

        def panne(_valeur):
            raise RuntimeError('SECRET_PANNE_XYZ')

        setattr(competence_rank, attribut, panne)
        cap = io.StringIO()
        try:
            with contextlib.redirect_stderr(cap):
                s = competence_rank.classer([cand], _demande())
        finally:
            setattr(competence_rank, attribut, original)
        return s, cap.getvalue()

    def test_panne_dans_mesure_info_defaut_interne(self):
        s, diag = self._classe_avec_panne('_mesure_info', _cand('a', stats=_stats(10, 10)))
        self.assertIsNone(s['choisi'])
        self.assertEqual(s['refus'], 'DEFAUT_INTERNE')
        self.assertEqual(s['motifs'], [])
        self.assertIn('RuntimeError', diag)
        self.assertNotIn('SECRET_PANNE_XYZ', diag)

    def test_panne_dans_note_prior_defaut_interne(self):
        s, diag = self._classe_avec_panne('_note_prior',
                                          _cand('a', stats=_stats(10, 10),
                                                prior={'note': 0.5}))
        self.assertEqual(s['refus'], 'DEFAUT_INTERNE')
        self.assertIn('RuntimeError', diag)
        self.assertNotIn('SECRET_PANNE_XYZ', diag)

    def test_validation_entree_reste_entree_invalide(self):
        s = competence_rank.classer([_cand('a')], None)
        self.assertEqual(s['refus'], 'ENTREE_INVALIDE')


class PieceInterneTest(unittest.TestCase):
    # P1(e) c6 §5 : certification des pieces internes de classement.
    def test_mesure_info_cas_limites(self):
        M = competence_rank._mesure_info
        self.assertEqual(M(None), (None, -1.0, False))
        self.assertEqual(M('x'), (None, -1.0, False))
        self.assertEqual(M({'non_mesure': True, 'wilson': (0.1, 0.9)}), (None, -1.0, False))
        self.assertEqual(M({'non_mesure': False, 'wilson': None}), (None, -1.0, False))
        self.assertEqual(M({'non_mesure': False, 'wilson': 'x'}), (None, -1.0, False))
        self.assertEqual(M({'non_mesure': False, 'wilson': (0.1,)}), (None, -1.0, False))
        self.assertEqual(M({'non_mesure': False, 'wilson': (True, 0.9)}), (None, -1.0, False))
        self.assertEqual(M({'non_mesure': False, 'wilson': ('a', 0.9)}), (None, -1.0, False))
        iv, taux, mesurable = M({'non_mesure': False, 'wilson': (0.1, 0.9), 'taux': 0.5})
        self.assertEqual((iv, taux, mesurable), ((0.1, 0.9), 0.5, True))

    def test_note_prior_cas_limites(self):
        N = competence_rank._note_prior
        self.assertEqual(N(None), -1.0)
        self.assertEqual(N(True), -1.0)
        self.assertEqual(N('x'), -1.0)
        self.assertEqual(N(0.5), 0.5)
        self.assertEqual(N(1), 1.0)
        self.assertEqual(N(1.5), -1.0)
        self.assertEqual(N(-0.1), -1.0)
        self.assertEqual(N({'note': 0.7}), 0.7)
        self.assertEqual(N({'note': True}), -1.0)
        self.assertEqual(N({'note': 'x'}), -1.0)
        self.assertEqual(N({'note': 2.0}), -1.0)
        self.assertEqual(N({}), -1.0)

    def test_cout_et_motif(self):
        self.assertEqual(competence_rank._COUT,
                         {'free': 0, 'included': 1, 'paid-named': 2})
        self.assertEqual(competence_rank._motif('QUOTA', 'codex', 'registre'),
                         {'code': 'QUOTA', 'actor': 'codex', 'source': 'registre'})


class StaticPathTest(unittest.TestCase):
    # E7-04 : 0 mesure + 20 incertains / 20 terminales -> non mesurable
    # statistiquement (wilson None) -> voie statique, pas un refus.
    def test_zero_mesure_sur_20_terminales_voie_statique(self):
        stats0 = {'sample_count': 20, 'succes': 0, 'echecs': 0, 'incertains': 20,
                  'taux': None, 'wilson': None, 'non_mesure': False}
        s = competence_rank.classer([_cand('a', stats=stats0)], _demande())
        self.assertEqual(s['choisi'], 'a')
        self.assertIsNone(s['refus'])
        self.assertIn('ADMISSIBLE', _motif_codes(s))
        self.assertIn('STATIQUE', _motif_codes(s))


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
