import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).parents[1]))
import siri_intents_probe as probe


class SiriProbeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.toolchain = self.root / 'toolchain'
        self.bin = self.toolchain / 'usr' / 'bin'
        self.bin.mkdir(parents=True)
        protocols = self.toolchain / 'usr/share/swift/SwiftConstantValues/AppIntents.json'
        protocols.parent.mkdir(parents=True)
        protocols.write_text(json.dumps({'constValueProtocols': ['AppIntent', 'AppShortcutsProvider']}))
        self.commands = []

    def command(self, args):
        self.commands.append(args)
        if args == ['xcrun', '--find', 'swiftc']:
            return str(self.bin / 'swiftc')
        if args == ['xcrun', '--find', 'appintentsmetadataprocessor']:
            return str(self.bin / 'appintentsmetadataprocessor')
        if args == ['xcrun', '--sdk', 'macosx', '--show-sdk-path']:
            return str(self.root / 'sdk')
        if args == ['xcodebuild', '-version']:
            return 'Xcode 27.1\nBuild version 27A9275'
        if Path(args[0]).name == 'appintentsmetadataprocessor':
            destination = Path(args[args.index('--output') + 1]) / 'Metadata.appintents'
            destination.mkdir()
            action = {'isDiscoverable': True, 'openAppWhenRun': False}
            if args[args.index('--deployment-target') + 1] == '27.0':
                action['allowedTargets'] = [{'extensionPointIdentifier': 'com.apple.appintents-extension', 'type': 3}]
            (destination / 'extract.actionsdata').write_text(json.dumps({
                'actions': {'RepriseStatusIntent': action},
                'autoShortcuts': [{'actionIdentifier': 'RepriseStatusIntent',
                                   'phraseTemplates': [{'key': 'Quel est l’état de ${applicationName}'}]}],
                'generator': {'name': 'xcode-tools'}}))
        return ''

    def test_build_produces_verified_uninstalled_bundle(self):
        result = probe.build(self.root / 'new', command=self.command, machine='arm64')
        self.assertTrue(Path(result['metadata']).is_file())
        self.assertEqual(result['generator']['name'], 'xcode-tools')
        self.assertFalse(result['installed'])
        self.assertFalse(result['launched'])
        self.assertFalse(result['siri_verified'])
        self.assertTrue(any('--swift-const-vals-list' in c for c in self.commands))
        self.assertFalse(any('--force-metadata-output' in c for c in self.commands))

    def test_extension_emits_restricted_target_without_installing(self):
        result = probe.build(self.root / 'extension', command=self.command, machine='arm64', extension=True)
        self.assertTrue(result['extension'])
        self.assertTrue(result['bundle'].endswith('.appex'))
        self.assertFalse(result['installed'])
        self.assertFalse(result['launched'])
        self.assertFalse(result['siri_verified'])
        source = (self.root / 'extension' / 'Intent.swift').read_text()
        self.assertIn('.appIntentsExtension', source)
        self.assertIn('AppIntentsExtension', source)
        processor = next(c for c in self.commands if '--deployment-target' in c)
        self.assertEqual(processor[processor.index('--deployment-target') + 1], '27.0')
        metadata = Path(result['metadata'])
        data = json.loads(metadata.read_text())
        data['actions']['RepriseStatusIntent']['allowedTargets'] = []
        metadata.write_text(json.dumps(data))
        with self.assertRaises(ValueError):
            probe.verify_metadata(metadata, extension=True)

    def test_existing_destination_or_symlink_is_never_touched(self):
        existing = self.root / 'existing'
        existing.mkdir()
        (existing / 'keep').write_text('safe')
        for destination in (existing, self.root / 'link'):
            if destination.name == 'link':
                destination.symlink_to(existing, target_is_directory=True)
            with self.assertRaises(FileExistsError):
                probe.build(destination, command=self.command, machine='arm64')
        self.assertEqual((existing / 'keep').read_text(), 'safe')
        self.assertEqual(self.commands, [])

    def test_relative_or_missing_parent_rejected(self):
        for destination in ('relative', str(self.root / 'missing' / 'output')):
            with self.subTest(destination=destination), self.assertRaises(ValueError):
                probe.build(destination, command=self.command, machine='arm64')
        self.assertEqual(self.commands, [])

    def test_missing_or_invalid_metadata_is_error(self):
        metadata = self.root / 'metadata'
        metadata.write_text(json.dumps({'actions': {}, 'autoShortcuts': []}))
        with self.assertRaises(ValueError):
            probe.verify_metadata(metadata)
        metadata.write_text('{')
        with self.assertRaises(json.JSONDecodeError):
            probe.verify_metadata(metadata)

    def test_command_failure_propagates(self):
        def fail(args):
            raise RuntimeError('tool failed')
        with self.assertRaisesRegex(RuntimeError, 'tool failed'):
            probe.build(self.root / 'failed', command=fail, machine='arm64')
        self.assertTrue((self.root / 'failed').is_dir())


if __name__ == '__main__':
    unittest.main()
