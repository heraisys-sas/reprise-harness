import importlib.util
from pathlib import Path
import tempfile
import unittest

spec = importlib.util.spec_from_file_location('expertise_install', Path(__file__).parents[1]/'install-expertise.py')
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)


class InstallationTests(unittest.TestCase):
    def test_existing_profile_backed_up_preserved_and_idempotent(self):
        with tempfile.TemporaryDirectory() as folder:
            home = Path(folder)
            profile = home/'profiles/desktop/cordis.patch.yml'
            profile.parent.mkdir(parents=True)
            original = '- id: existing\n  config:\n    value: preserved\n'
            profile.write_text(original)
            result = installer.install(home, home)
            self.assertEqual((Path(result['backup'])/'profiles/desktop/cordis.patch.yml').read_text(), original)
            self.assertTrue(profile.read_text().startswith(original))
            self.assertEqual(profile.read_text().count(installer.START), 1)
            self.assertFalse(installer.install(home, home)['changed'])
    def test_empty_commented_profile_remains_valid_array(self):
        with tempfile.TemporaryDirectory() as folder:
            home = Path(folder)
            profile = home/'profiles/desktop/cordis.patch.yml'
            profile.parent.mkdir(parents=True)
            profile.write_text('# original comment\n[]\n')
            installer.install(home, home)
            self.assertNotIn('\n[]\n', profile.read_text())
            self.assertTrue(profile.read_text().startswith('# original comment\n'))
