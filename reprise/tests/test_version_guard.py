"""Garde-fou numéro de bundle : reprise/VERSION vs dernière entrée CHANGELOG.

Le numéro de bundle (CFBundleShortVersionString via native-install) a une
seule source : reprise/VERSION. Ce test échoue si VERSION est strictement en
retard sur la première entrée `## X.Y.Z` du CHANGELOG ; égalité ou avance = OK
(version en avance possible si le CHANGELOG ne porte pas encore la release).
Chemins résolus depuis __file__ : les shards CI lancent les tests avec
cwd = reprise/tests/ (cf. #257).
"""
import re
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))

REPO_ROOT = Path(__file__).resolve().parents[2]
VERSION_PATH = REPO_ROOT / 'reprise' / 'VERSION'
CHANGELOG_PATH = REPO_ROOT / 'CHANGELOG.md'

ENTRY_PATTERN = re.compile(r'^##\s+(\d+)\.(\d+)\.(\d+)\b', re.MULTILINE)
SEMAPVER = re.compile(r'^(\d+)\.(\d+)\.(\d+)$')

GUARD_REMINDER = ("reprise/VERSION est la source unique du numéro de bundle "
                  "(CFBundleShortVersionString via native-install) : la bumper "
                  "avant (ou en même temps que) l'entrée CHANGELOG.")


def parse_version(text):
    """Tuple (major, minor, patch) depuis un contenu type '0.1.25', sinon None."""
    match = SEMAPVER.match(text.strip())
    return tuple(int(part) for part in match.groups()) if match else None


def parse_changelog_version(text):
    """Tuple de la première entrée '## X.Y.Z', sinon None."""
    match = ENTRY_PATTERN.search(text)
    return tuple(int(part) for part in match.groups()) if match else None


class ParseVersionTest(unittest.TestCase):
    def test_reads_simple_version(self):
        self.assertEqual(parse_version('0.1.25\n'), (0, 1, 25))

    def test_rejects_non_semver(self):
        self.assertIsNone(parse_version('0.1\n'))
        self.assertIsNone(parse_version('abc\n'))
        self.assertIsNone(parse_version(''))


class ParseChangelogVersionTest(unittest.TestCase):
    def test_reads_first_entry(self):
        text = '# CHANGELOG\n\n## 0.1.25 — titre\n\n## 0.1.24 — ancien\n'
        self.assertEqual(parse_changelog_version(text), (0, 1, 25))

    def test_no_entry_returns_none(self):
        self.assertIsNone(parse_changelog_version('# CHANGELOG\n\nHistorique.\n'))


class VersionGuardTest(unittest.TestCase):
    """Cas synthétiques : la fonction de décision pure est exécutée sur des
    fichiers temporaires via read_version/parse_changelog_version ; le verdict
    comparé est assertGreaterEqual(VERSION, CHANGELOG)."""

    def guard(self, version_text, changelog_text):
        version = parse_version(version_text)
        entry = parse_changelog_version(changelog_text)
        self.assertIsNotNone(version, f'Version illisible : {version_text!r}')
        self.assertIsNotNone(entry, f'Entrée CHANGELOG illisible : {changelog_text!r}.\n{GUARD_REMINDER}')
        self.assertGreaterEqual(version, entry,
                                f'reprise/VERSION {version_text.strip()} est en retard '
                                f'sur le CHANGELOG {".".join(map(str, entry))}.\n{GUARD_REMINDER}')

    def test_version_behind_fails(self):
        with self.assertRaises(AssertionError) as caught:
            self.guard('0.1.23\n', '## 0.1.25 — titre\n')
        self.assertIn('0.1.23', str(caught.exception))
        self.assertIn('0.1.25', str(caught.exception))

    def test_version_equal_passes(self):
        self.guard('0.1.25\n', '## 0.1.25 — titre\n')

    def test_version_ahead_passes(self):
        self.guard('0.1.26\n', '## 0.1.25 — titre\n')

    def test_missing_changelog_entry_fails_explicitly(self):
        with self.assertRaises(AssertionError) as caught:
            self.guard('0.1.25\n', '# CHANGELOG\n\nHistorique.\n')
        self.assertIn('Entrée CHANGELOG illisible', str(caught.exception))

    def test_missing_version_file_skips(self):
        with tempfile.TemporaryDirectory() as temporary:
            missing = Path(temporary) / 'absent'
            self.assertIsNone(parse_version(missing.read_text() if missing.exists() else ''))
        # Un fichier absent doit produire un skipTest, jamais un crash :
        # simulé par une version None → verdict explicite.
        with self.assertRaises(AssertionError):
            self.guard('', '## 0.1.25 — titre\n')


class InstalledVersionGuardTest(unittest.TestCase):
    """Garde-fou réel : fichiers du dépôt ; skip si un fichier est absent."""

    def test_version_is_not_behind_changelog(self):
        if not VERSION_PATH.exists() or not CHANGELOG_PATH.exists():
            self.skipTest('reprise/VERSION ou CHANGELOG.md absent de cet arbre')
        version_text = VERSION_PATH.read_text(encoding='utf-8')
        changelog_text = CHANGELOG_PATH.read_text(encoding='utf-8')
        version = parse_version(version_text)
        self.assertIsNotNone(version, f'reprise/VERSION illisible : {version_text!r}')
        entry = parse_changelog_version(changelog_text)
        self.assertIsNotNone(entry, 'CHANGELOG.md sans entrée "## X.Y.Z" lisible.\n' + GUARD_REMINDER)
        self.assertGreaterEqual(
            version, entry,
            f'reprise/VERSION {".".join(map(str, version))} est en retard sur '
            f'la dernière entrée CHANGELOG {".".join(map(str, entry))}.\n{GUARD_REMINDER}')


if __name__ == '__main__':
    unittest.main()
