"""Runtime archive validation uses local, tiny tar fixtures only."""
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import package_runtime


class PackageRuntimeTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.archive = self.root / 'runtime.tar.gz'
        self.destination = self.root / 'extracted'
        self.outside = self.root / 'outside'
        self.outside.mkdir()
        (self.outside / 'sentinel').write_text('preserve')

    def fixture(self, entries):
        with tarfile.open(self.archive, 'w:gz') as archive:
            for name, kind, payload in entries:
                member = tarfile.TarInfo(name)
                member.type = kind
                member.mode = 0o755 if kind == tarfile.DIRTYPE else 0o644
                if kind == tarfile.REGTYPE:
                    data = payload.encode()
                    member.size = len(data)
                    archive.addfile(member, io.BytesIO(data))
                else:
                    if kind in (tarfile.SYMTYPE, tarfile.LNKTYPE):
                        member.linkname = payload
                    archive.addfile(member)

    def assert_refused(self, entries):
        self.fixture(entries)
        with self.assertRaises(ValueError):
            package_runtime.safe_extract(self.archive, self.destination)
        self.assertEqual((self.outside / 'sentinel').read_text(), 'preserve')
        self.assertEqual(sorted(p.name for p in self.outside.iterdir()), ['sentinel'])

    def test_extracts_files_and_internal_relative_symlinks(self):
        self.fixture([
            ('python/', tarfile.DIRTYPE, ''),
            ('python/bin/', tarfile.DIRTYPE, ''),
            ('python/lib/', tarfile.DIRTYPE, ''),
            ('python/lib/runtime', tarfile.REGTYPE, 'fixture runtime'),
            ('python/bin/python3', tarfile.SYMTYPE, '../lib/runtime'),
            ('python/bin/python', tarfile.SYMTYPE, 'python3'),
        ])
        package_runtime.safe_extract(self.archive, self.destination)
        self.assertEqual((self.destination / 'python/bin/python').read_text(), 'fixture runtime')
        self.assertTrue((self.destination / 'python/bin/python3').is_symlink())

    def test_rejects_member_paths_outside_python_root(self):
        names = ['../outside/created', 'python/../../outside/created',
                 str(self.outside / 'created'), 'other/file', 'python-other/file']
        for name in names:
            with self.subTest(name=name):
                self.assert_refused([(name, tarfile.REGTYPE, 'bad')])

    def test_rejects_absolute_and_escaping_symlinks(self):
        for target in [str(self.outside), '../../outside', '../..']:
            with self.subTest(target=target):
                self.assert_refused([
                    ('python/link', tarfile.SYMTYPE, target),
                    ('python/link/created', tarfile.REGTYPE, 'bad'),
                ])

    def test_rejects_hardlinks_even_to_internal_file(self):
        self.assert_refused([
            ('python/original', tarfile.REGTYPE, 'fixture'),
            ('python/copy', tarfile.LNKTYPE, 'python/original'),
        ])

    def test_rejects_special_members(self):
        for kind in [tarfile.CHRTYPE, tarfile.BLKTYPE, tarfile.FIFOTYPE]:
            with self.subTest(kind=kind):
                self.assert_refused([('python/special', kind, '')])

    def test_existing_destination_symlink_cannot_redirect_writes(self):
        (self.destination / 'python').mkdir(parents=True)
        (self.destination / 'python/link').symlink_to(self.outside, target_is_directory=True)
        self.assert_refused([('python/link/created', tarfile.REGTYPE, 'bad')])

    def test_archive_cannot_replace_existing_link_to_external_file(self):
        (self.destination / 'python').mkdir(parents=True)
        (self.destination / 'python/file').symlink_to(self.outside / 'sentinel')
        self.assert_refused([('python/file', tarfile.REGTYPE, 'overwritten')])

    def test_digest_matches_archive_bytes(self):
        self.fixture([('python/file', tarfile.REGTYPE, 'fixture')])
        expected = hashlib.sha256(self.archive.read_bytes()).hexdigest()
        self.assertIsNone(package_runtime.verify_archive(self.archive, expected))

    def test_modified_archive_fails_digest_check(self):
        self.fixture([('python/file', tarfile.REGTYPE, 'fixture')])
        expected = hashlib.sha256(self.archive.read_bytes()).hexdigest()
        with self.archive.open('ab') as stream:
            stream.write(b'tampered')
        with self.assertRaises(ValueError):
            package_runtime.verify_archive(self.archive, expected)

    def test_invalid_expected_digest_is_rejected(self):
        self.fixture([('python/file', tarfile.REGTYPE, 'fixture')])
        for expected in ['', 'not-a-digest', '0' * 63, 'g' * 64]:
            with self.subTest(expected=expected):
                with self.assertRaises(ValueError):
                    package_runtime.verify_archive(self.archive, expected)

    def test_install_checks_digest_before_extraction_or_execution(self):
        self.fixture([('python/file', tarfile.REGTYPE, 'fixture')])
        with patch.object(package_runtime.platform, 'system', return_value='Darwin'), \
                patch.object(package_runtime.platform, 'machine', return_value='arm64'), \
                patch.object(package_runtime, 'safe_extract') as extract, \
                patch.object(package_runtime.subprocess, 'run') as execute:
            with self.assertRaises(ValueError):
                package_runtime.install(self.archive, self.root / 'Reprise.app')
            extract.assert_not_called()
            execute.assert_not_called()
        self.assertFalse((self.root / 'Reprise.app').exists())

    def test_install_rejects_unsupported_architecture_before_reading_archive(self):
        with patch.object(package_runtime.platform, 'system', return_value='Darwin'), \
                patch.object(package_runtime.platform, 'machine', return_value='unsupported-fixture'), \
                patch.object(package_runtime, 'verify_archive') as verify, \
                patch.object(package_runtime, 'safe_extract') as extract:
            with self.assertRaises(ValueError):
                package_runtime.install(self.archive, self.root / 'Reprise.app')
            verify.assert_not_called()
            extract.assert_not_called()

    @unittest.skipUnless(os.environ.get('REPRISE_TEST_RUNTIME_ARCHIVE'),
                         'Set REPRISE_TEST_RUNTIME_ARCHIVE to a local pinned runtime archive')
    def test_real_runtime_relocates_and_initializes_only_temporary_home(self):
        from package_helpers import package
        archive = Path(os.environ['REPRISE_TEST_RUNTIME_ARCHIVE']).expanduser().resolve()
        app = self.root / 'Original.app'
        package_runtime.install(archive, app)
        package(ROOT, app)
        relocated = self.root / 'Folder with spaces' / 'Reprise relocated.app'
        relocated.parent.mkdir()
        shutil.move(str(app), relocated)
        runtime = relocated / 'Contents/Resources/runtime'
        executable = runtime / 'bin/python3'
        resume = relocated / 'Contents/Resources/helpers/resume'
        before = package_runtime.metadata(relocated)
        home = self.root / 'Isolated Home'
        home.mkdir()
        environment = package_runtime.clean_environment()
        environment.update(HOME=str(home), PATH='/usr/bin:/bin')
        environment.pop('AGENT_RESUME_STATE', None)
        environment.pop('REPRISE_ALLOW_NEW_STATE', None)

        def run(*arguments):
            return subprocess.run([str(executable), '-E', '-s', '-B', *arguments],
                                  env=environment, cwd=self.root, capture_output=True,
                                  text=True, check=True, timeout=60)

        probe = run('-I', '-c', 'import sys,json,sqlite3,ssl,ctypes,hashlib,zlib,bz2,lzma,fcntl,socket; '
                    'assert sqlite3.connect(":memory:").execute("select 1").fetchone()==(1,); '
                    'print(json.dumps({"prefix":sys.prefix,"executable":sys.executable}))')
        observed = json.loads(probe.stdout)
        self.assertEqual(Path(observed['prefix']).resolve(), runtime.resolve())
        self.assertEqual(Path(observed['executable']).resolve(), executable.resolve())
        state = home / 'Library/Application Support/Reprise/state'
        run(str(resume), 'state-init', '--target', str(state))
        run(str(resume), 'scheduler-status')
        self.assertTrue((state / '.reprise-state.json').is_file())
        self.assertFalse(state.is_relative_to(relocated))
        self.assertFalse((relocated / 'state').exists())
        self.assertEqual(package_runtime.metadata(relocated), before)
        manifest_path = runtime / 'runtime-manifest.json'
        original_manifest = manifest_path.read_text()
        for key in ['provider', 'release', 'target', 'source_url', 'archive_sha256']:
            with self.subTest(manifest_field=key):
                modified = json.loads(original_manifest)
                modified[key] = 'tampered-fixture'
                manifest_path.write_text(json.dumps(modified))
                with self.assertRaises(ValueError):
                    package_runtime.metadata(relocated)
                manifest_path.write_text(original_manifest)
        executable_mode = executable.stat().st_mode
        executable.chmod(executable_mode & ~0o111)
        try:
            with self.assertRaises(ValueError):
                package_runtime.metadata(relocated)
        finally:
            executable.chmod(executable_mode)
        marker = relocated / 'Contents/Resources/runtime-installing.json'
        marker.write_text('{"format":1}')
        try:
            with self.assertRaises(ValueError):
                package_runtime.metadata(relocated)
        finally:
            marker.unlink()
        self.assertEqual(package_runtime.metadata(relocated), before)
        rename = Path.rename
        def fail_runtime_publication(path, target):
            if path.name == 'python' and Path(target).resolve() == runtime.resolve():
                raise OSError('fixture publication failure')
            return rename(path, target)
        with patch.object(Path, 'rename', fail_runtime_publication):
            with self.assertRaises(OSError):
                package_runtime.install(archive, relocated)
        self.assertTrue(executable.exists())
        self.assertTrue(marker.exists())
        with self.assertRaises(ValueError):
            package_runtime.metadata(relocated)
        package_runtime.install(archive, relocated)
        self.assertFalse(marker.exists())
        self.assertEqual(package_runtime.metadata(relocated), before)
        with (runtime / 'THIRD-PARTY-NOTICES.txt').open('a') as stream:
            stream.write('\nfixture tamper\n')
        with self.assertRaises(ValueError):
            package_runtime.metadata(relocated)


if __name__ == '__main__':
    unittest.main()
