import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from context_pack import scoped_prepare
from project_policy import ProjectPolicy


class ProjectPolicyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()
        self.a, self.b = self.base/'project', self.base/'project-copy'
        for root in (self.a, self.b):
            root.mkdir()
            (root/'AGENTS.md').write_text('Never send secrets. Preserve sources.\n')
            (root/'code.py').write_text('migration parser evidence\n')
        self.config = {'version': 1, 'projects': {
            name: {'roots': [str(root)], 'instructions': [str(root/'AGENTS.md')]}
            for name, root in [('a', self.a), ('b', self.b)]}}
        self.path = self.base/'policy.json'
        self.write_policy()

    def write_policy(self):
        self.path.write_text(json.dumps(self.config))
        self.path.chmod(0o600)

    def scope(self, project='a'):
        return ProjectPolicy(self.path, project)

    def test_authorized_and_unknown_project(self):
        pack = scoped_prepare(self.scope(), 'migration', [self.a/'code.py'])
        self.assertEqual(pack['excerpts'][0]['text'], 'migration parser evidence\n')
        self.assertEqual(pack['instructions'][0]['text'], (self.a/'AGENTS.md').read_text())
        with self.assertRaises(ValueError):
            self.scope('made-up')

    def test_similar_names_and_outside_paths(self):
        scope = self.scope()
        for path in (self.b/'code.py', self.path, self.a/'..'/'project-copy'/'code.py'):
            with self.subTest(path=path), self.assertRaises(ValueError):
                scope.read(path)

    def test_file_and_directory_symlink_escape(self):
        (self.a/'link.py').symlink_to(self.b/'code.py')
        (self.a/'linked').symlink_to(self.b, target_is_directory=True)
        for path in (self.a/'link.py', self.a/'linked'/'code.py'):
            with self.subTest(path=path), self.assertRaises(OSError):
                self.scope().read(path)
        (self.a/'internal.py').symlink_to(self.a/'code.py')
        with self.assertRaises(OSError):
            self.scope().read(self.a/'internal.py')

    def test_policy_and_root_symlinks_rejected(self):
        link = self.base/'policy-link.json'
        link.symlink_to(self.path)
        with self.assertRaises(OSError):
            ProjectPolicy(link, 'a')
        root_link = self.base/'root-link'
        root_link.symlink_to(self.a, target_is_directory=True)
        self.config['projects']['a']['roots'] = [str(root_link)]
        self.write_policy()
        with self.assertRaises(OSError):
            self.scope()

    def test_hardlink_and_special_file_rejected(self):
        os.link(self.b/'code.py', self.a/'hardlink')
        os.mkfifo(self.a/'fifo')
        for path in (self.a/'hardlink', self.a/'fifo'):
            with self.subTest(path=path), self.assertRaises(ValueError):
                self.scope().read(path)

    def test_root_replacement_and_overlap_rejected(self):
        scope = self.scope()
        self.a.rename(self.base/'old')
        self.a.mkdir()
        (self.a/'code.py').write_text('new')
        with self.assertRaises(ValueError):
            scope.read(self.a/'code.py')
        self.config['projects']['b']['roots'] = [str(self.base)]
        self.write_policy()
        with self.assertRaises(ValueError):
            self.scope()

    def test_policy_permissions_and_pinned_instructions(self):
        self.path.chmod(0o666)
        with self.assertRaises(ValueError):
            self.scope()
        self.write_policy()
        self.config['projects']['a']['instructions'] = [str(self.b/'AGENTS.md')]
        self.write_policy()
        with self.assertRaises(ValueError):
            scoped_prepare(self.scope(), 'migration', [self.a/'code.py'])

    def test_partition_versions_and_omitted_sources(self):
        a = scoped_prepare(self.scope(), 'migration', [self.a/'code.py'])
        b = scoped_prepare(self.scope('b'), 'migration', [self.b/'code.py'])
        self.assertNotEqual(a['project_scope']['namespace'], b['project_scope']['namespace'])
        self.assertNotEqual(a['cache_key'], b['cache_key'])
        self.assertEqual(a['cache_key'], scoped_prepare(self.scope(), 'migration', [self.a/'code.py'])['cache_key'])
        (self.a/'unrelated').write_text('unrelated text')
        before = scoped_prepare(self.scope(), 'migration', [self.a/'code.py', self.a/'unrelated'])
        (self.a/'unrelated').write_text('changed but still unrelated')
        after = scoped_prepare(self.scope(), 'migration', [self.a/'code.py', self.a/'unrelated'])
        self.assertNotEqual(before['cache_key'], after['cache_key'])
        self.assertGreater(after['omitted_blocks'], 0)

    def test_reprise_unknown_provenance_never_opened(self):
        state = self.base/'global-state'
        state.mkdir()
        (state/'analysis.enc').write_bytes(b'opaque global secrets')
        scope = self.scope()
        with patch('context_pack.reprise_sources', wraps=__import__('context_pack').reprise_sources) as reader:
            pack = scoped_prepare(scope, 'migration', [self.a/'code.py'], reprise_state=state)
        self.assertEqual(reader.call_count, 1)
        self.assertEqual(pack['reprise']['reason'], 'project-provenance-unavailable')
        self.assertFalse(pack['reprise']['decrypted'])
        self.assertNotIn('opaque', json.dumps(pack))
        self.assertEqual((state/'analysis.enc').read_bytes(), b'opaque global secrets')
        self.assertEqual(list(state.iterdir()), [state/'analysis.enc'])

    @unittest.skipUnless(shutil.which('git'), 'git unavailable')
    def test_worktree_needs_explicit_root(self):
        def git(*args):
            subprocess.run(['git', '-C', str(self.a), *args], check=True, capture_output=True)
        git('init', '-q')
        git('add', '.')
        git('-c', 'user.name=Scope Test', '-c', 'user.email=scope@example.invalid', 'commit', '-qm', 'fixture')
        worktree = self.base/'worktree'
        git('worktree', 'add', '-qb', 'isolated', str(worktree))
        with self.assertRaises(ValueError):
            self.scope().read(worktree/'code.py')
        self.config['projects']['w'] = {'roots': [str(worktree)], 'instructions': [str(worktree/'AGENTS.md')]}
        self.write_policy()
        w = scoped_prepare(self.scope('w'), 'migration', [worktree/'code.py'])
        a = scoped_prepare(self.scope(), 'migration', [self.a/'code.py'])
        self.assertNotEqual(w['cache_key'], a['cache_key'])
        with self.assertRaises(ValueError):
            self.scope('w').read(self.a/'code.py')

    def test_physical_alias_overlap(self):
        from project_policy import open_path
        real_identity = (self.a.stat().st_dev, self.a.stat().st_ino)
        def aliased(path, lineage=None):
            fd = open_path(path, lineage)
            if path == self.b and lineage is not None:
                lineage.append(real_identity)
            return fd
        with patch('project_policy.open_path', side_effect=aliased), self.assertRaises(ValueError):
            self.scope()

    def test_macos_firmlink_alias(self):
        alias = Path('/System/Volumes/Data') / str(self.a).lstrip('/')
        if not alias.exists() or not alias.samefile(self.a):
            self.skipTest('macOS data-volume alias unavailable')
        self.config['projects']['b']['roots'] = [str(alias)]
        self.write_policy()
        with self.assertRaises(ValueError):
            self.scope()

    def test_cli_authorized_and_refused_without_partial_output(self):
        import sys
        command = [sys.executable, str(Path(__file__).resolve().parents[1]/'context_pack.py'),
                   '--policy', str(self.path), '--project', 'a', '--query', 'migration', '--source']
        allowed = subprocess.run(command+[str(self.a/'code.py')], capture_output=True, text=True)
        self.assertEqual(allowed.returncode, 0, allowed.stderr)
        self.assertEqual(json.loads(allowed.stdout)['project_scope']['project'], 'a')
        denied = subprocess.run(command+[str(self.b/'code.py')], capture_output=True, text=True)
        self.assertNotEqual(denied.returncode, 0)
        self.assertEqual(denied.stdout, '')
        self.assertNotIn('migration parser evidence', denied.stderr)

    def test_reprise_exclusion_does_not_touch_filesystem(self):
        from context_pack import reprise_sources
        scope = self.scope()
        with patch('os.open', side_effect=AssertionError('must not open global state')):
            documents, status = reprise_sources(self.base/'unread-state', scope)
        self.assertEqual(documents, [])
        self.assertIsNone(status['excluded_entries'])
