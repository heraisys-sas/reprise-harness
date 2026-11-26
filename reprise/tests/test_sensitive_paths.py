"""Tests pour sensitive_paths (lot L4t, contrat E4-O1 @4d9228d).

TDD : le module reprise/sensitive_paths.py (lot L4c) et le registre
contracts/sensitive-paths.v1.json (livres avec L4c) n'existent pas
encore ; la collecte echoue avec ImportError jusqu'a leur livraison —
rouge conforme.
Auteur L4t : ASI1 (SOD : l'auteur des tests n'est pas l'auteur du code L4c).

Spec : spec-e3-o1-competence 1.7, T4 (detection des chemins sensibles) ;
contrat (d) : registre contracts/sensitive-paths.v1.json versionne,
proprietaire Olivier, format {"schema":"sensitive-paths@1","version":1,
"owner":"Olivier","semantique":{"non_liste":"non_sensible","doute":
"sensible"},"regles":[{"id":"<kebab unique>","classe":"<str>","glob":
"<*, **, ?>"}]} — additionalProperties=false, pas de regex, un
appariement suffit. Normalisation : realpath, expansion de ~, NFC,
casse insensible, relatif a la racine du depot. Fail-closed : chemin
non resolvable (symlink casse) => sensible. Faux positifs assumes
(Q13 CONFIRME) : reprise/vendor/yaml/tokens.py,
tools/probes/mimo/token-plan-ams.patch.yml => acteur local seulement.
Les paires positif/negatif par regle vivent ICI (dans les tests),
jamais dans le registre.

Contrat du module (aucune E/S reseau ; le systeme de fichiers n'est
touche que par des fixturs temporaires ; la normalisation est
injectable) :
- valide_registre(registre) -> list[str] : liste d'erreurs (vide =
  valide). Cles exactes {schema, version, owner, semantique, regles} ;
  schema == 'sensitive-paths@1' ; version == 1 ; owner chaine non vide ;
  semantique exactement {'non_liste': 'non_sensible', 'doute':
  'sensible'} ; regles liste non vide de dicts a cles exactes {id,
  classe, glob} ; id kebab ([a-z0-9]+(-[a-z0-9]+)*) unique dans le
  registre ; classe chaine non vide ; glob chaine non vide SANS meta-
  caracteres regex (aucun de { } ( ) [ ] | + ^ $).
- charger_registre(chemin) -> dict : lit le JSON utf-8, valide via
  valide_registre, ValueError si JSON invalide ou erreurs non vides.
- est_sensible(chemin, registre, normaliser=None, home=None) -> bool :
  normalisateur par defaut = realpath(expanduser(home)) puis NFC puis
  casse insensible ; symlink non resolvable (cible absente) => True ;
  toute exception de resolution => True (fail-closed) ; semantique des
  globs : '**' traverse '/', '*' ne traverse JAMAIS '/' (un seul
  segment), '?' exactement un caractere, motif prefixe '~/' resolu
  contre home, chemin NFC et insensible a la casse ; UN appariement
  suffit (n'importe quelle regle => True).
"""
import json
import os
import sys
import tempfile
import unicodedata
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))
import sensitive_paths  # noqa: E402

RACINE = Path(__file__).parents[2]
REGISTRE = RACINE / 'contracts' / 'sensitive-paths.v1.json'


def _registre(*globs):
    """Registre minimal valide pour les tests d'appariement."""
    return {'schema': 'sensitive-paths@1', 'version': 1, 'owner': 'Olivier',
            'semantique': {'non_liste': 'non_sensible', 'doute': 'sensible'},
            'regles': [{'id': 'regle-%d' % n, 'classe': 'essai', 'glob': g}
                       for n, g in enumerate(globs)]}


class RegleTest(unittest.TestCase):
    """Validation du format du registre (contrat (d))."""

    def test_valide_registre_accepte_format_contrat(self):
        self.assertEqual(sensitive_paths.valide_registre(_registre('**/.env')), [])

    def test_registre_erreurs_detectees(self):
        cas = []
        dup = _registre('**/.env', '**/*.pem')
        dup['regles'][1]['id'] = dup['regles'][0]['id']
        cas.append(('id duplique', dup))
        extra = _registre('**/.env')
        extra['supplement'] = 1
        cas.append(('cle supplementaire', extra))
        cas.append(('meta-caracteres regex', _registre('**(a|b)')))
        faux_schema = _registre('**/.env')
        faux_schema['schema'] = 'sensitive-paths@2'
        cas.append(('schema faux', faux_schema))
        faux_sem = _registre('**/.env')
        faux_sem['semantique'] = {'non_liste': 'non_sensible',
                                  'doute': 'non_sensible'}
        cas.append(('semantique modifiee', faux_sem))
        faux_id = _registre('**/.env')
        faux_id['regles'][0]['id'] = 'Cle Ssh'
        cas.append(('id non kebab', faux_id))
        cas.append(('regles vide', _registre()))
        cas.append(('glob vide', _registre('')))
        for nom, registre in cas:
            self.assertNotEqual(sensitive_paths.valide_registre(registre), [],
                                'erreur non detectee : %s' % nom)

    def test_charger_registre_json_invalide_valueerror(self):
        with tempfile.TemporaryDirectory() as tmp:
            chemin = os.path.join(tmp, 'registre.json')
            with open(chemin, 'w', encoding='utf-8') as po:
                po.write('{oops')
            self.assertRaises(ValueError,
                              sensitive_paths.charger_registre, chemin)

    def test_charger_registre_registre_invalide_valueerror(self):
        invalide = _registre('**/.env')
        invalide['regles'][0]['id'] = 'ID MAJUSCULE'
        with tempfile.TemporaryDirectory() as tmp:
            chemin = os.path.join(tmp, 'registre.json')
            with open(chemin, 'w', encoding='utf-8') as po:
                json.dump(invalide, po)
            self.assertRaises(ValueError,
                              sensitive_paths.charger_registre, chemin)

    def test_charger_registre_retourne_le_registre(self):
        valide = _registre('**/.env')
        with tempfile.TemporaryDirectory() as tmp:
            chemin = os.path.join(tmp, 'registre.json')
            with open(chemin, 'w', encoding='utf-8') as po:
                json.dump(valide, po)
            charge = sensitive_paths.charger_registre(chemin)
        self.assertEqual(charge['schema'], 'sensitive-paths@1')
        self.assertEqual(len(charge['regles']), 1)


class CouvertureRegistreTest(unittest.TestCase):
    """Une paire positif/negatif par regle du registre livre (contrat (d))."""

    PAIRES = {
        '~/.ssh/**': ('.ssh/id_ed25519', 'documents/rapport.md'),
        '**/*.pem': ('serveur/cert.pem', 'serveur/cert.pem.asc'),
        '**/*.key': ('serveur/cle.key', 'serveur/cle.keyring'),
        '**/.env': ('projet/.env', 'projet/env'),
        '**/.env.*': ('projet/.env.local', 'projet/env.local'),
        '**/*keychain*': ('bibliotheque/keychain-db', 'bibliotheque/keys-db'),
        '~/Library/Keychains/**': ('Library/Keychains/login.keychain-db',
                                   'Library/Preferences/com.apple.plist'),
        '**/*token*': ('projet/token-store.json', 'projet/memo.json'),
        '**/*cookie*': ('cache/cookies.sqlite', 'cache/session.sqlite'),
    }

    @classmethod
    def setUpClass(cls):
        cls.registre = sensitive_paths.charger_registre(str(REGISTRE))

    def test_paire_par_regle(self):
        vus = set()
        for regle in self.registre['regles']:
            self.assertIn(regle['glob'], self.PAIRES,
                          'regle sans paire testee : %s' % regle['glob'])
            vus.add(regle['glob'])
        self.assertEqual(vus, set(self.PAIRES),
                         'glob attendu manquant au registre')
        with tempfile.TemporaryDirectory() as tmp:
            for glob, (positif, negatif) in self.PAIRES.items():
                self.assertTrue(
                    sensitive_paths.est_sensible(
                        str(Path(tmp) / positif), self.registre, home=tmp),
                    'positif attendu pour %s' % glob)
                self.assertFalse(
                    sensitive_paths.est_sensible(
                        str(Path(tmp) / negatif), self.registre, home=tmp),
                    'negatif attendu pour %s' % glob)


class T04DetectionTest(unittest.TestCase):
    """T4 : .env ; symlink non resolvable => sensible ; casse ; NFC ;
    non liste => non sensible."""

    @classmethod
    def setUpClass(cls):
        cls.registre = sensitive_paths.charger_registre(str(REGISTRE))

    def test_t04_env_detection(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            self.assertTrue(sensitive_paths.est_sensible(
                str(base / 'projet' / '.env'), self.registre, home=tmp))
            self.assertTrue(sensitive_paths.est_sensible(
                str(base / 'projet' / 'sous' / '.env.local'),
                self.registre, home=tmp))
            self.assertFalse(sensitive_paths.est_sensible(
                str(base / 'projet' / 'env'), self.registre, home=tmp))
            self.assertFalse(sensitive_paths.est_sensible(
                str(base / 'projet' / '.envelope'), self.registre, home=tmp))

    def test_t04_casse_et_nfc(self):
        glob_nfc = unicodedata.normalize('NFC', '**/clé.pem')
        regle = _registre('**/.env', glob_nfc)
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            self.assertTrue(sensitive_paths.est_sensible(
                str(base / 'NOTES' / '.ENV'), regle, home=tmp))
            nfd = unicodedata.normalize('NFD', 'clé.pem')
            self.assertTrue(sensitive_paths.est_sensible(
                str(base / nfd), regle, home=tmp))
            self.assertFalse(sensitive_paths.est_sensible(
                str(base / 'cle.pem'), regle, home=tmp))

    def test_t04_symlink_non_resoluble_sensible(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            lien = base / 'lien'
            os.symlink(base / 'cible-absente.txt', lien)
            self.assertTrue(sensitive_paths.est_sensible(
                str(lien), self.registre, home=tmp))
            self.assertTrue(sensitive_paths.est_sensible(
                str(lien), _registre('**/rien.txt'), home=tmp))

    def test_t04_non_liste_non_sensible(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            self.assertFalse(sensitive_paths.est_sensible(
                str(base / 'rapport.md'), self.registre, home=tmp))
            self.assertFalse(sensitive_paths.est_sensible(
                str(base / 'documents' / 'budget.ods'), self.registre,
                home=tmp))

    def test_t04_glob_semantique(self):
        regle = _registre('documents/*', 'notes/?.txt')
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            self.assertTrue(sensitive_paths.est_sensible(
                str(base / 'documents' / 'secret.txt'), regle, home=tmp))
            self.assertFalse(sensitive_paths.est_sensible(
                str(base / 'documents' / 'archives' / 'secret.txt'),
                regle, home=tmp))
            self.assertTrue(sensitive_paths.est_sensible(
                str(base / 'notes' / 'a.txt'), regle, home=tmp))
            self.assertFalse(sensitive_paths.est_sensible(
                str(base / 'notes' / 'ab.txt'), regle, home=tmp))
            profond = _registre('**/secret.txt')
            self.assertTrue(sensitive_paths.est_sensible(
                str(base / 'a' / 'b' / 'c' / 'secret.txt'), profond,
                home=tmp))


if __name__ == '__main__':
    unittest.main()
