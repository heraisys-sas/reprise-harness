import json
import plistlib
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).parents[1]))
import app_clones
from support_portability import requires_tools


def make_fake_app(root, name='FakeSource', bundle_id='com.fake.source', version='1.0'):
    """Minimal signed Electron-like bundle (no app launch, ad-hoc signature)."""
    app = root / f'{name}.app'
    (app / 'Contents/MacOS').mkdir(parents=True)
    exe = app / 'Contents/MacOS/Dummy'
    exe.write_text('#!/bin/sh\nexit 0\n')
    exe.chmod(0o755)
    (app / 'Contents/Resources').mkdir()
    (app / 'Contents/Resources/app.asar').write_bytes(b'fake asar payload')
    plist = {'CFBundleName': name, 'CFBundleDisplayName': name,
             'CFBundleIdentifier': bundle_id,
             'CFBundleShortVersionString': version,
             'CFBundleExecutable': 'Dummy'}
    with open(app / 'Contents/Info.plist', 'wb') as handle:
        plistlib.dump(plist, handle)
    subprocess.run(['codesign', '--force', '--deep', '--sign', '-', str(app)],
                   check=True, capture_output=True, timeout=120)
    return app


class AppClonesTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.source = make_fake_app(self.base / 'src')
        self.target = self.base / 'home' / 'Applications' / 'FakeClone.app'
        self.home = self.base / 'home'
        self.manifest_path = self.base / 'state' / 'app-clones.json'
        self.specs = {'testclone': {'source': self.source, 'target': self.target,
                                    'bundle_id': 'com.reprise.testclone',
                                    'display_name': 'FakeClone', 'kind': 'clone'}}
        self.written = []
        self.writer = lambda bid, key, value: self.written.append((bid, key, value))
        self.reader = lambda bid: any(k == 'SUEnableAutomaticChecks' and v is False
                                      for b, k, v in self.written if b == bid)

    def invoke(self, action, names=None):
        if action == 'plan':
            return app_clones.plan(self.home, specs=self.specs,
                                   manifest_path=self.manifest_path)
        if action == 'apply':
            return app_clones.apply(self.home, names=names, specs=self.specs,
                                    manifest_path=self.manifest_path,
                                    defaults_writer=self.writer)
        if action == 'inventory':
            return app_clones.inventory(self.home, specs=self.specs,
                                        manifest_path=self.manifest_path,
                                        defaults_state=self.reader)
        return app_clones.verify(self.home, specs=self.specs,
                                 manifest_path=self.manifest_path,
                                 defaults_state=self.reader)

    @requires_tools('codesign')
    def test_plan_missing_clone_lists_full_pipeline(self):
        planned = self.invoke('plan')
        self.assertEqual(planned['testclone'],
                         ['clone', 'identity', 'sign', 'update-off', 'freeze-record'])

    @requires_tools('codesign')
    def test_apply_creates_frozen_reidentified_clone_and_source_stays_pristine(self):
        receipt = self.invoke('apply')
        self.assertEqual(receipt['testclone']['status'], 'applied')
        self.assertTrue(self.target.is_dir())
        with open(self.target / 'Contents/Info.plist', 'rb') as handle:
            plist = plistlib.load(handle)
        self.assertEqual(plist['CFBundleIdentifier'], 'com.reprise.testclone')
        self.assertEqual(plist['CFBundleName'], 'FakeClone')
        with open(self.source / 'Contents/Info.plist', 'rb') as handle:
            source_plist = plistlib.load(handle)
        self.assertEqual(source_plist['CFBundleIdentifier'], 'com.fake.source')
        self.assertIn('freeze-record', receipt['testclone']['steps'])
        manifest = json.loads(self.manifest_path.read_text())
        self.assertEqual(manifest['clones']['testclone']['version'], '1.0')
        self.assertEqual(self.written, [
            ('com.reprise.testclone', 'SUEnableAutomaticChecks', False),
            ('com.reprise.testclone', 'SUAutomaticallyUpdate', False)])

    @requires_tools('codesign')
    def test_inventory_and_verify_green_after_apply(self):
        self.invoke('apply')
        state = self.invoke('inventory')['testclone']
        self.assertTrue(state['present'])
        self.assertTrue(state['bundle_id_ok'])
        self.assertTrue(state['signature_valid'])
        self.assertTrue(state['su_off'])
        self.assertEqual(state['issues'], [])
        verdict = self.invoke('verify')
        self.assertTrue(verdict['ok'], verdict)

    @requires_tools('codesign')
    def test_patched_clone_preserves_electron_helper_bundle_name(self):
        self.specs['testclone']['kind'] = 'patched'

        self.invoke('apply')

        plist = app_clones._read_plist(self.target)
        self.assertEqual(plist['CFBundleName'], 'FakeSource')
        self.assertEqual(plist['CFBundleDisplayName'], 'FakeClone')
        self.assertTrue(self.invoke('verify')['ok'])

    @requires_tools('codesign')
    def test_existing_patched_clone_repairs_internal_name_and_resigns_in_place(self):
        self.invoke('apply')
        self.specs['testclone']['kind'] = 'patched'
        asar = self.target / 'Contents/Resources/app.asar'
        asar.write_bytes(b'already patched')
        app_clones._sign(self.target)
        before = self.invoke('inventory')['testclone']

        self.assertTrue(before['signature_valid'])
        self.assertTrue(before['display_name_ok'])
        self.assertFalse(before['bundle_name_ok'])
        self.assertEqual(before['issues'], ['identity'])
        receipt = self.invoke('apply')['testclone']

        self.assertEqual(receipt['steps'][:2], ['identity', 'sign'])
        self.assertNotIn('clone', receipt['steps'])
        self.assertEqual(asar.read_bytes(), b'already patched')
        self.assertTrue(self.invoke('verify')['ok'])

    @requires_tools('codesign')
    def test_upstream_bump_plans_refresh_and_apply_replaces_clone(self):
        self.invoke('apply')
        with open(self.source / 'Contents/Info.plist', 'rb') as handle:
            plist = plistlib.load(handle)
        plist['CFBundleShortVersionString'] = '2.0'
        with open(self.source / 'Contents/Info.plist', 'wb') as handle:
            plistlib.dump(plist, handle)
        subprocess.run(['codesign', '--force', '--deep', '--sign', '-', str(self.source)],
                       check=True, capture_output=True, timeout=120)
        planned = self.invoke('plan')['testclone']
        self.assertIn('refresh', planned)
        receipt = self.invoke('apply')
        self.assertEqual(receipt['testclone']['status'], 'applied')
        self.assertIn('old_clone_removed', receipt['testclone']['steps'])
        self.assertEqual(receipt['testclone']['target_version'], '2.0')
        manifest = json.loads(self.manifest_path.read_text())
        self.assertEqual(manifest['clones']['testclone']['version'], '2.0')

    @requires_tools('codesign')
    def test_apply_refuses_when_target_is_running_on_refresh(self):
        self.invoke('apply')
        with open(self.source / 'Contents/Info.plist', 'rb') as handle:
            plist = plistlib.load(handle)
        plist['CFBundleShortVersionString'] = '2.0'
        with open(self.source / 'Contents/Info.plist', 'wb') as handle:
            plistlib.dump(plist, handle)
        subprocess.run(['codesign', '--force', '--deep', '--sign', '-', str(self.source)],
                       check=True, capture_output=True, timeout=120)
        with mock.patch.object(app_clones, '_running_pid', return_value=4242):
            receipt = self.invoke('apply')
        self.assertEqual(receipt['testclone']['status'], 'refused')
        self.assertEqual(receipt['testclone']['reason'], 'target_running:4242')


if __name__ == '__main__':
    unittest.main()
