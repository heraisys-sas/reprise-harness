"""Tests pour perimetre_guard, depot git temporaire."""
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock
sys.path.insert(0, str(Path(__file__).parents[1]))
import perimetre_guard
from perimetre_guard import verifier_perimetre_diff


class TestPerimetreGuard(unittest.TestCase):
    # Helpers pour un depot git temporaire.
    def _git(self, dossier, *args):
        # Lance git dans le dossier.
        subprocess.run(["git", "-C", dossier, *args], check=True, capture_output=True, text=True)

    def _init(self, dossier, fichiers=None):
        # Init + commit initial, retourne base_sha.
        self._git(dossier, "init")
        self._git(dossier, "config", "user.email", "test@test.test")
        self._git(dossier, "config", "user.name", "Test")
        if fichiers is None:
            fichiers = {"init.txt": "init\n"}
        for nom, contenu in fichiers.items():
            self._ecrire(dossier, nom, contenu)
        self._git(dossier, "add", ".")
        self._git(dossier, "commit", "-m", "init")
        base = subprocess.run(["git", "-C", dossier, "rev-parse", "HEAD"], check=True, capture_output=True, text=True).stdout.strip()
        return base

    def _ecrire(self, dossier, nom, contenu):
        # Ecrit un fichier, texte ou octets, cree les dossiers.
        chemin = os.path.join(dossier, nom)
        parent = os.path.dirname(chemin)
        if parent:
            os.makedirs(parent, exist_ok=True)
        mode = "wb" if isinstance(contenu, bytes) else "w"
        with open(chemin, mode) as f:
            f.write(contenu)

    def test_diff_conforme_accepte(self):
        # 2 fichiers modifies dans allowed, budgets larges, ok vrai.
        with tempfile.TemporaryDirectory() as dossier:
            base = self._init(dossier)
            self._ecrire(dossier, "src/a.py", "a = 1\n")
            self._ecrire(dossier, "src/b.py", "b = 2\n")
            self._git(dossier, "add", ".")
            resultat = verifier_perimetre_diff(dossier, base, ["src/**"], max_diff_files=10, max_diff_lines=1000)
            self.assertTrue(resultat["ok"])
            self.assertIsNone(resultat["refuse"])

    def test_hors_perimetre_refuse(self):
        # Fichier hors allowed, digest non vide.
        with tempfile.TemporaryDirectory() as dossier:
            base = self._init(dossier)
            self._ecrire(dossier, "hors.txt", "hors\n")
            self._git(dossier, "add", ".")
            resultat = verifier_perimetre_diff(dossier, base, ["src/**"])
            self.assertFalse(resultat["ok"])
            self.assertEqual(resultat["refuse"], "HORS_PERIMETRE")
            self.assertTrue(len(resultat["hors_perimetre"]) > 0)
            self.assertTrue(len(resultat["digest_diff"]) > 0)

    def test_allowed_paths_absent_ou_vide_refusent_tout(self):
        # allowed_paths absent (None) ou vide : chaque entree refusee (fail-closed), aucun repli sur tout le depot.
        with tempfile.TemporaryDirectory() as dossier:
            base = self._init(dossier)
            self._ecrire(dossier, "src/a.py", "a = 1\n")
            self._git(dossier, "add", ".")
            for autorises in (None, []):
                resultat = verifier_perimetre_diff(dossier, base, autorises, max_diff_files=10, max_diff_lines=1000)
                self.assertFalse(resultat["ok"])
                self.assertEqual(resultat["refuse"], "HORS_PERIMETRE")
                self.assertTrue(len(resultat["hors_perimetre"]) > 0)

    def test_interdit_implicite_VERSION(self):
        # VERSION modifiee meme si dans allowed.
        with tempfile.TemporaryDirectory() as dossier:
            base = self._init(dossier)
            self._ecrire(dossier, "VERSION", "1.0.0\n")
            self._git(dossier, "add", ".")
            resultat = verifier_perimetre_diff(dossier, base, ["VERSION"])
            self.assertFalse(resultat["ok"])
            self.assertEqual(resultat["refuse"], "CHEMIN_INTERDIT")

    def test_renommage_partiel_refuse(self):
        # git mv, ancien chemin hors allowed.
        with tempfile.TemporaryDirectory() as dossier:
            base = self._init(dossier, {"ancien.txt": "ancien\n"})
            self._git(dossier, "mv", "ancien.txt", "nouveau.txt")
            resultat = verifier_perimetre_diff(dossier, base, ["nouveau.txt"])
            self.assertFalse(resultat["ok"])
            self.assertEqual(resultat["refuse"], "HORS_PERIMETRE")
            self.assertIn("ancien.txt", resultat["hors_perimetre"])

    def test_fichier_non_suivi_hors_perimetre_refuse_issue_293(self):
        with tempfile.TemporaryDirectory() as dossier:
            base = self._init(dossier)
            self._ecrire(dossier, "src/a.py", "a = 1\n")
            self._git(dossier, "add", ".")
            self._ecrire(dossier, "hors/nouveau.py", "x = 1\ny = 2")
            resultat = verifier_perimetre_diff(dossier, base, ["src/**"], max_diff_lines=1000)
            self.assertFalse(resultat["ok"])
            self.assertEqual(resultat["refuse"], "HORS_PERIMETRE")
            self.assertEqual(resultat["hors_perimetre"], ["hors/nouveau.py"])
            self.assertEqual(resultat["lignes_ajoutees"], 3)

    def test_fichier_non_suivi_compte_et_change_le_digest(self):
        with tempfile.TemporaryDirectory() as dossier:
            base = self._init(dossier)
            self._ecrire(dossier, "src/n.py", "n = 1\n")
            premier = verifier_perimetre_diff(dossier, base, ["src/**"], max_diff_files=0)
            self.assertEqual(premier["refuse"], "BUDGET_FICHIERS_DEPASSE")
            self._ecrire(dossier, "src/n.py", "n = 2\n")
            second = verifier_perimetre_diff(dossier, base, ["src/**"])
            self.assertTrue(second["ok"])
            self.assertNotEqual(premier["digest_diff"], second["digest_diff"])

    def test_non_suivi_binaire_et_ignore(self):
        with tempfile.TemporaryDirectory() as dossier:
            base = self._init(dossier, {"init.txt": "init\n", ".gitignore": "build/\n"})
            self._ecrire(dossier, "build/out.o", b"\x00\x01")
            self.assertTrue(verifier_perimetre_diff(dossier, base, ["src/**"])["ok"])
            self._ecrire(dossier, "src/blob.dat", b"\x00\x01")
            self.assertEqual(verifier_perimetre_diff(dossier, base, ["src/**"])["refuse"], "BINAIRE_INTERDIT")

    def test_symlink_refuse(self):
        # Symlink ajoute.
        with tempfile.TemporaryDirectory() as dossier:
            base = self._init(dossier)
            lien = os.path.join(dossier, "lien.txt")
            os.symlink("init.txt", lien)
            self._git(dossier, "add", ".")
            resultat = verifier_perimetre_diff(dossier, base, ["lien.txt"])
            self.assertFalse(resultat["ok"])
            self.assertEqual(resultat["refuse"], "SYMLINK_INTERDIT")

    def test_binaire_refuse(self):
        # Fichier avec octets nuls.
        with tempfile.TemporaryDirectory() as dossier:
            base = self._init(dossier)
            self._ecrire(dossier, "bin.dat", b"\x00\x01\x02\x03")
            self._git(dossier, "add", ".")
            self._git(dossier, "commit", "-m", "binaire")
            resultat = verifier_perimetre_diff(dossier, base, ["bin.dat"])
            self.assertFalse(resultat["ok"])
            self.assertEqual(resultat["refuse"], "BINAIRE_INTERDIT")

    def test_budget_fichiers_depasse(self):
        # 3 fichiers, max 2.
        with tempfile.TemporaryDirectory() as dossier:
            base = self._init(dossier)
            for nom in ["f1.txt", "f2.txt", "f3.txt"]:
                self._ecrire(dossier, nom, "x\n")
            self._git(dossier, "add", ".")
            resultat = verifier_perimetre_diff(dossier, base, ["*.txt"], max_diff_files=2)
            self.assertFalse(resultat["ok"])
            self.assertEqual(resultat["refuse"], "BUDGET_FICHIERS_DEPASSE")

    def test_budget_lignes_depasse(self):
        # Ajouts superieurs au plafond.
        with tempfile.TemporaryDirectory() as dossier:
            base = self._init(dossier)
            self._ecrire(dossier, "gros.txt", "".join("ligne %d\n" % i for i in range(20)))
            self._git(dossier, "add", ".")
            resultat = verifier_perimetre_diff(dossier, base, ["gros.txt"], max_diff_lines=5)
            self.assertFalse(resultat["ok"])
            self.assertEqual(resultat["refuse"], "BUDGET_LIGNES_DEPASSE")

    def test_sensible_acteur_interdit(self):
        # Regle sensible + acteur metaai refuse, ironclaw accepte.
        with tempfile.TemporaryDirectory() as dossier:
            base = self._init(dossier)
            self._ecrire(dossier, "cle.pem", "secret\n")
            self._git(dossier, "add", ".")
            regles = os.path.join(tempfile.gettempdir(), "regles-sensibles-test.txt")
            with open(regles, "w", encoding="utf-8") as f:
                f.write("*.pem\n")
            try:
                resultat = verifier_perimetre_diff(dossier, base, ["*.pem"], fichier_regles_sensibles=regles, acteur="metaai")
                self.assertFalse(resultat["ok"])
                self.assertEqual(resultat["refuse"], "SENSIBLE_ACTOR_INTERDIT")
                resultat2 = verifier_perimetre_diff(dossier, base, ["*.pem"], fichier_regles_sensibles=regles, acteur="ironclaw")
                self.assertTrue(resultat2["ok"])
                self.assertIsNone(resultat2["refuse"])
            finally:
                if os.path.exists(regles):
                    os.remove(regles)


    def test_etoile_simple_ne_traverse_pas_les_dossiers(self):
        # allowed src/*.py ne couvre pas src/sub/x.py (fail-open fnmatch corrige).
        with tempfile.TemporaryDirectory() as dossier:
            base = self._init(dossier)
            self._ecrire(dossier, "src/sub/x.py", "x = 1\n")
            self._git(dossier, "add", ".")
            resultat = verifier_perimetre_diff(dossier, base, ["src/*.py"])
            self.assertFalse(resultat["ok"])
            self.assertEqual(resultat["refuse"], "HORS_PERIMETRE")

    def test_double_etoile_traverse_les_dossiers(self):
        # src/**/*.py couvre les sous-dossiers.
        with tempfile.TemporaryDirectory() as dossier:
            base = self._init(dossier)
            self._ecrire(dossier, "src/sub/x.py", "x = 1\n")
            self._git(dossier, "add", ".")
            resultat = verifier_perimetre_diff(dossier, base, ["src/**/*.py"])
            self.assertTrue(resultat["ok"])
            self.assertIsNone(resultat["refuse"])

    def test_interdit_sans_slash_toute_profondeur(self):
        # CHANGELOG* attrape un CHANGELOG dans un sous-dossier.
        with tempfile.TemporaryDirectory() as dossier:
            base = self._init(dossier)
            self._ecrire(dossier, "docs/CHANGELOG.md", "x\n")
            self._git(dossier, "add", ".")
            resultat = verifier_perimetre_diff(dossier, base, ["docs/**"])
            self.assertFalse(resultat["ok"])
            self.assertEqual(resultat["refuse"], "CHEMIN_INTERDIT")

    def test_sensible_sans_acteur_refuse(self):
        # Regle sensible + acteur absent : refuse (fail-open acteur=None corrige).
        with tempfile.TemporaryDirectory() as dossier:
            base = self._init(dossier)
            self._ecrire(dossier, "cle.pem", "secret\n")
            self._git(dossier, "add", ".")
            regles = os.path.join(tempfile.gettempdir(), "regles-sensibles-test-absent.txt")
            with open(regles, "w", encoding="utf-8") as f:
                f.write("*.pem\n")
            try:
                resultat = verifier_perimetre_diff(dossier, base, ["*.pem"], fichier_regles_sensibles=regles)
                self.assertFalse(resultat["ok"])
                self.assertEqual(resultat["refuse"], "ACTEUR_ABSENT")
            finally:
                if os.path.exists(regles):
                    os.remove(regles)

    def test_parse_numstat_entree_corrompue_comptee(self):
        # Une entree numstat non parseable est comptee, jamais jetee.
        parse = perimetre_guard._parse_numstat(b"10\t2\tok.txt\x00corrompu-sans-tab\x00")
        self.assertEqual(len(parse["entrees"]), 1)
        self.assertEqual(parse["entrees_ignorees"], ["corrompu-sans-tab"])

    def test_numstat_illisible_refuse(self):
        # Entree illisible dans le diff : refuse NUMSTAT_ILLISIBLE, digest garde.
        with tempfile.TemporaryDirectory() as dossier:
            base = self._init(dossier)
            faux = {"entrees": [], "binaire": [], "entrees_ignorees": ["entree-corrompue"], "texte_brut": b"corrompu"}
            with mock.patch.object(perimetre_guard, "calculer_diff", return_value=faux):
                resultat = verifier_perimetre_diff(dossier, base, ["**"])
            self.assertFalse(resultat["ok"])
            self.assertEqual(resultat["refuse"], "NUMSTAT_ILLISIBLE")
            self.assertEqual(resultat["entrees_ignorees"], ["entree-corrompue"])
            self.assertTrue(len(resultat["digest_diff"]) > 0)

if __name__ == "__main__":
    unittest.main()
