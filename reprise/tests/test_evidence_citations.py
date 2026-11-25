"""Tests pour evidence_citations, fichiers temporaires uniquement."""
import hashlib
import os
import sys
from pathlib import Path
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).parents[1]))
from evidence_citations import CitationError, verify_citation, require_cited_evidence


class TestEvidenceCitations(unittest.TestCase):
    # Contenu de reference : trois lignes simples.
    CONTENU = "ligne un\nligne deux\nligne trois\n"
    BASE_SHA = "a" * 40

    def _creer_fichier(self, dossier):
        # Cree un fichier reel dans le dossier temporaire.
        chemin = os.path.join(dossier, "doc.txt")
        with open(chemin, "w", encoding="utf-8") as f:
            f.write(self.CONTENU)
        return chemin

    def test_plage_hors_fichier_refusee(self):
        # Plage 9999-10001 refusee.
        with tempfile.TemporaryDirectory() as dossier:
            self._creer_fichier(dossier)
            resultat = verify_citation("doc.txt", "9999-10001", "rien", dossier)
            self.assertFalse(resultat["ok"])
            self.assertIn("plage_hors_fichier", resultat["reasons"])

    def test_extrait_fabrique_refuse(self):
        # Extrait invente refuse.
        with tempfile.TemporaryDirectory() as dossier:
            self._creer_fichier(dossier)
            resultat = verify_citation("doc.txt", "1-2", "extrait invente", dossier)
            self.assertFalse(resultat["ok"])
            self.assertIn("extrait_ne_correspond_pas", resultat["reasons"])

    def test_citation_valide_acceptee(self):
        # Plage exacte et extrait exact acceptes.
        with tempfile.TemporaryDirectory() as dossier:
            self._creer_fichier(dossier)
            resultat = verify_citation("doc.txt", "1-2", "ligne un\nligne deux", dossier)
            self.assertTrue(resultat["ok"])
            self.assertEqual(resultat["reasons"], [])

    def test_chemin_refuse(self):
        # Chemin avec .. et chemin absolu refuses.
        with tempfile.TemporaryDirectory() as dossier:
            self._creer_fichier(dossier)
            resultat1 = verify_citation("../doc.txt", "1", "ligne un", dossier)
            self.assertFalse(resultat1["ok"])
            self.assertIn("chemin_refuse", resultat1["reasons"])
            resultat2 = verify_citation("/tmp/doc.txt", "1", "ligne un", dossier)
            self.assertFalse(resultat2["ok"])
            self.assertIn("chemin_refuse", resultat2["reasons"])

    def test_sha_divergent_refuse(self):
        # Sha attendu faux refuse.
        with tempfile.TemporaryDirectory() as dossier:
            self._creer_fichier(dossier)
            faux_sha = "0" * 64
            resultat = verify_citation("doc.txt", "1", "ligne un", dossier, faux_sha)
            self.assertFalse(resultat["ok"])
            self.assertIn("sha_diverge", resultat["reasons"])

    def test_base_sha_exige(self):
        # base_sha absent ou invalide leve CitationError.
        with tempfile.TemporaryDirectory() as dossier:
            self._creer_fichier(dossier)
            sortie = {"citations": [{"path": "doc.txt", "lines": "1", "excerpt": "ligne un"}]}
            with self.assertRaises(CitationError):
                require_cited_evidence(sortie, dossier)
            sortie["base_sha"] = "xyz"
            with self.assertRaises(CitationError):
                require_cited_evidence(sortie, dossier)
            sortie["base_sha"] = self.BASE_SHA
            self.assertIsNone(require_cited_evidence(sortie, dossier))

    def test_citations_vides_exigees(self):
        # Citations vides ou absentes levent CitationError.
        with tempfile.TemporaryDirectory() as dossier:
            self._creer_fichier(dossier)
            with self.assertRaises(CitationError):
                require_cited_evidence({"citations": [], "base_sha": self.BASE_SHA}, dossier)
            with self.assertRaises(CitationError):
                require_cited_evidence({"base_sha": self.BASE_SHA}, dossier)

    def test_ligne_simple(self):
        # Lines egal a N seul, ligne 2.
        with tempfile.TemporaryDirectory() as dossier:
            self._creer_fichier(dossier)
            resultat = verify_citation("doc.txt", "2", "ligne deux", dossier)
            self.assertTrue(resultat["ok"])
            self.assertEqual(resultat["reasons"], [])


if __name__ == "__main__":
    unittest.main()
