"""Tests pour competence_types (lot L1t, contrat E4-O1 @4d9228d).

TDD : le module reprise/competence_types.py (lot L1c) n'existe pas encore ;
la collecte echoue avec ImportError jusqu'a sa livraison — rouge conforme.
"""
import hashlib
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))
import competence_types  # noqa: E402


SPEC = 'spec-e3-o1-competence-20261126@1.1'
BASE_SHA = 'a' * 40
DIGEST = 'b' * 64


def _motif(code='STATIQUE', valeur=0, ref='reprise/x.py#L1@' + BASE_SHA):
    return {'code': code, 'valeur': valeur,
            'source': {'type': 'table_roles', 'ref': ref}}


def _sortie_valide(**surcharge):
    sortie = {
        'spec': SPEC, 'base_sha': BASE_SHA,
        'demande': {'role': 'mass_code', 'mode': 'implementation', 'kind': 'lot', 'sensible': False},
        'decision': {
            'choisi': {'actor': 'codex', 'backend': 'codex-cli', 'billing_class': 'included'},
            'secondaires': [], 'motifs': [_motif()],
        },
        'tracabilite': {'sample_count': 20, 'fenetre': {'jours': 30, 'obs_max': 50,
                        'debut': '2026-10-28T00:00:00+00:00', 'fin': '2026-11-27T00:00:00+00:00'},
                        'non_mesure': True, 'incertains': 0, 'digest_entree': DIGEST},
        'refus': None,
    }
    sortie.update(surcharge)
    return sortie


class DigestEntreeTest(unittest.TestCase):
    def test_t09_determinisme(self):
        entree = {'a': 1, 'b': [2, 3]}
        self.assertEqual(competence_types.digest_entree(entree),
                         competence_types.digest_entree(dict(reversed(list(entree.items())))))
        self.assertEqual(competence_types.digest_entree(entree), competence_types.digest_entree(entree))
        # format canonique : JSON trie, separateurs compacts, sha256 hex 64
        canonique = json.dumps(entree, sort_keys=True, separators=(',', ':'), ensure_ascii=False)
        self.assertEqual(competence_types.digest_entree(entree),
                         hashlib.sha256(canonique.encode('utf-8')).hexdigest())

    def test_digest_hex64(self):
        digest = competence_types.digest_entree({'x': 'y'})
        self.assertIsInstance(digest, str)
        self.assertEqual(len(digest), 64)
        int(digest, 16)  # hex


class ValideSortieTest(unittest.TestCase):
    def test_t12_entree_corrompue(self):
        ok, raisons = competence_types.valide_sortie({})
        self.assertFalse(ok)
        self.assertTrue(raisons)
        ok, _ = competence_types.valide_sortie(_sortie_valide(spec='autre@1.0'))
        self.assertFalse(ok)
        ok, _ = competence_types.valide_sortie(_sortie_valide(base_sha='court'))
        self.assertFalse(ok)
        ok, _ = competence_types.valide_sortie(_sortie_valide(tracabilite={'digest_entree': 'zz'}))
        self.assertFalse(ok)

    def test_sortie_valide_acceptee(self):
        ok, raisons = competence_types.valide_sortie(_sortie_valide())
        self.assertTrue(ok, raisons)

    def test_choisi_null_exige_refus(self):
        sortie = _sortie_valide()
        sortie['decision'] = {'choisi': None, 'secondaires': [], 'motifs': []}
        sortie['refus'] = None
        ok, _ = competence_types.valide_sortie(sortie)
        self.assertFalse(ok)  # fail-closed
        sortie['refus'] = {'raison': 'QUOTA_EPUISE', 'route_rejetees': []}
        ok, raisons = competence_types.valide_sortie(sortie)
        self.assertTrue(ok, raisons)

    def test_motifs_sources_obligatoires(self):
        sortie = _sortie_valide()
        sortie['decision']['motifs'] = [{'code': 'STATIQUE', 'valeur': 0}]  # sans source
        ok, _ = competence_types.valide_sortie(sortie)
        self.assertFalse(ok)
        sortie['decision']['motifs'] = [_motif(code='INCONNU')]
        ok, _ = competence_types.valide_sortie(sortie)
        self.assertFalse(ok)
        sortie['decision']['motifs'] = [_motif(valeur='texte libre')]
        ok, _ = competence_types.valide_sortie(sortie)
        self.assertFalse(ok)


class GardesTest(unittest.TestCase):
    def test_champs_e4_interdits(self):
        self.assertTrue(competence_types.rejete_champs_interdits({'request_key': 'x'}))
        self.assertTrue(competence_types.rejete_champs_interdits({'context_paths': []}))
        self.assertFalse(competence_types.rejete_champs_interdits({'decision': {}}))

    def test_t00_confiance_exclue(self):
        self.assertTrue(competence_types.exclut_confiance({'confiance': 1.0}))
        self.assertFalse(competence_types.exclut_confiance({'taux': 0.9}))

    def test_codes_refus_figes(self):
        attendus = {'ENTREE_INVALIDE', 'REGISTRE_ABSENT', 'FACTURATION_INCONNUE',
                    'ECRITURE_NON_AUTORISEE', 'QUOTA_EPUISE', 'PAYANT_NON_CONSENTI',
                    'ROLE_INTERDIT', 'CHEMIN_SENSIBLE'}
        self.assertTrue(attendus.issubset(set(competence_types.REFUS_RAISONS)))

    def test_motif_codes_figes(self):
        self.assertEqual({'ADMISSIBLE', 'MESURE_LOCALE', 'PRIOR_DEPARTAGE', 'QUOTA',
                          'STATIQUE', 'REFUS'}, set(competence_types.MOTIF_CODES))


class CompatPy39Test(unittest.TestCase):
    def test_t00_py39_compat(self):
        source = Path(__file__).parents[1] / 'competence_types.py'
        if not source.exists():
            self.skipTest('lot L1c non livre : competence_types.py absent')
        import ast
        arbre = ast.parse(source.read_text(encoding='utf-8'))
        for noeud in ast.walk(arbre):
            if isinstance(noeud, ast.BinOp) and isinstance(noeud.op, ast.BitOr):
                self.fail('PEP 604 interdit (X | Y) dans competence_types.py')
            if isinstance(noeud, ast.Match):
                self.fail('match/case interdit dans competence_types.py')


if __name__ == '__main__':
    unittest.main()
