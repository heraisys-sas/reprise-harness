import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from project_registry import ProjectRegistry


def git(path, *args):
    return subprocess.run(['git', '-C', str(path), *args], check=True,
                          capture_output=True, text=True).stdout.strip()


class ProjectRegistryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.state = self.base / 'state'
        self.repo = self.base / 'source'
        self.repo.mkdir()
        git(self.repo, 'init', '-q', '-b', 'main')
        git(self.repo, 'config', 'user.email', 'test@example.invalid')
        git(self.repo, 'config', 'user.name', 'Registry Test')
        (self.repo / 'file.txt').write_text('source\n')
        git(self.repo, 'add', 'file.txt')
        git(self.repo, 'commit', '-qm', 'initial')
        self.registry = ProjectRegistry(self.state)

    def tearDown(self):
        self.registry.close()
        self.temp.cleanup()

    def test_private_state_database_and_persistent_uuid(self):
        project = self.registry.create('Atlas', self.repo, self.repo)
        self.assertEqual(os.stat(self.state).st_mode & 0o777, 0o700)
        self.assertEqual(os.stat(self.registry.path).st_mode & 0o777, 0o600)
        project_id = project['id']
        self.registry.close()
        self.registry = ProjectRegistry(self.state)
        self.assertEqual(self.registry.get(project_id)['name'], 'Atlas')

    def test_create_get_list_rename_and_root_subdirectory(self):
        subdir = self.repo / 'module'
        subdir.mkdir()
        project = self.registry.create('  Atlas  ', subdir, self.repo)
        self.assertEqual(project['name'], 'Atlas')
        self.assertEqual(project['root_path'], str(subdir.resolve()))
        self.assertEqual(project['source_repository'], str(self.repo.resolve()))
        self.assertEqual(project['workspaces'], {})
        project_id = project['id']
        renamed = self.registry.rename(project_id, 'Atlas v2')
        self.assertEqual(renamed['id'], project_id)
        self.assertEqual(self.registry.get(project_id), renamed)
        self.assertEqual(self.registry.list(), [renamed])
        self.assertIsNone(self.registry.get('unknown'))

    def test_names_are_unambiguous_case_insensitively(self):
        first = self.registry.create('Atlas', self.repo, self.repo)
        second_repo = self.base / 'second'
        second_repo.mkdir()
        git(second_repo, 'init', '-q')
        with self.assertRaisesRegex(ValueError, 'name is already registered'):
            self.registry.create('atlas', second_repo, second_repo)
        second = self.registry.create('Beta', second_repo, second_repo)
        with self.assertRaisesRegex(ValueError, 'name is already registered'):
            self.registry.rename(second['id'], 'ATLAS')
        self.assertEqual(self.registry.get(first['id'])['name'], 'Atlas')
        self.assertEqual(self.registry.get(second['id'])['name'], 'Beta')

    def test_legacy_registry_gets_unicode_key_migration(self):
        legacy_state = self.base / 'legacy-state'
        legacy_state.mkdir(mode=0o700)
        legacy_db = sqlite3.connect(legacy_state / 'projects.sqlite')
        legacy_db.execute("""CREATE TABLE projects (
            id TEXT PRIMARY KEY, name TEXT NOT NULL COLLATE NOCASE UNIQUE,
            root_path TEXT NOT NULL, source_repository TEXT NOT NULL,
            source_common_dir TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )""")
        legacy_db.execute('INSERT INTO projects(id,name,root_path,source_repository,source_common_dir) VALUES(?,?,?,?,?)',
                          ('legacy-id', 'École', str(self.repo), str(self.repo), git(self.repo, 'rev-parse', '--git-common-dir')))
        legacy_db.commit()
        legacy_db.close()
        migrated = ProjectRegistry(legacy_state)
        try:
            self.assertEqual(migrated.get('legacy-id')['name'], 'École')
            with self.assertRaisesRegex(ValueError, 'name is already registered'):
                migrated.create('école', self.base / 'legacy-state', self.repo)
        finally:
            migrated.close()

    def test_names_use_unicode_casefold_and_canonical_normalization(self):
        self.registry.create('École', self.repo, self.repo)
        alternate_repo = self.base / 'unicode'
        alternate_repo.mkdir()
        git(alternate_repo, 'init', '-q')
        with self.assertRaisesRegex(ValueError, 'name is already registered'):
            self.registry.create('éCOLE', alternate_repo, alternate_repo)
        with self.assertRaisesRegex(ValueError, 'name is already registered'):
            self.registry.create('E\u0301cole', alternate_repo, alternate_repo)

    def test_create_validates_name_paths_and_git_source_root(self):
        with self.assertRaises(ValueError):
            self.registry.create(' ', self.repo, self.repo)
        with self.assertRaises(ValueError):
            self.registry.create('Bad', self.base / 'missing', self.repo)
        with self.assertRaises(ValueError):
            self.registry.create('Bad', self.repo, self.base)
        nested = self.repo / 'nested'
        nested.mkdir()
        with self.assertRaisesRegex(ValueError, 'worktree root'):
            self.registry.create('Bad', nested, nested)
        with self.assertRaises(ValueError):
            self.registry.rename('missing', 'Name')

    def test_canonical_path_collisions_and_failed_create_rollback(self):
        self.registry.create('First', self.repo, self.repo)
        alias = self.base / 'repo-alias'
        alias.symlink_to(self.repo, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, 'path is already registered'):
            self.registry.create('Second', alias, self.repo)
        second_repo = self.base / 'second'
        second_repo.mkdir()
        git(second_repo, 'init', '-q')
        with self.assertRaisesRegex(ValueError, 'path is already registered'):
            self.registry.create('Second', self.repo, second_repo)
        self.assertEqual(len(self.registry.list()), 1)
        # Rollback did not reserve second_repo or its name.
        self.assertEqual(self.registry.create('Second', second_repo, second_repo)['name'], 'Second')

    def test_source_repository_is_allowed_as_workspace_for_nonisolated_harness(self):
        project = self.registry.create('Atlas', self.repo, self.repo)
        result = self.registry.associate_workspace(project['id'], 'claude', self.repo)
        self.assertEqual(result['workspaces'], {'claude': str(self.repo.resolve())})
        self.assertEqual(self.registry.associate_workspace(project['id'], 'claude', self.repo), result)

    def test_linked_worktree_assignment_checks_common_dir_and_is_idempotent(self):
        project = self.registry.create('Atlas', self.repo, self.repo)
        workspace = self.base / 'codex-worktree'
        git(self.repo, 'worktree', 'add', '-qb', 'codex-work', str(workspace))
        result = self.registry.associate_workspace(project['id'], 'codex', workspace)
        self.assertEqual(result['workspaces'], {'codex': str(workspace.resolve())})
        self.assertEqual(self.registry.associate_workspace(project['id'], 'codex', workspace), result)

    def test_workspace_rejects_non_git_unrelated_git_and_nested_path(self):
        project = self.registry.create('Atlas', self.repo, self.repo)
        with self.assertRaisesRegex(ValueError, 'existing Git worktree'):
            self.registry.associate_workspace(project['id'], 'codex', self.base)
        unrelated = self.base / 'unrelated'
        unrelated.mkdir()
        git(unrelated, 'init', '-q')
        with self.assertRaisesRegex(ValueError, 'common-dir'):
            self.registry.associate_workspace(project['id'], 'codex', unrelated)
        nested = self.repo / 'nested'
        nested.mkdir()
        with self.assertRaisesRegex(ValueError, 'worktree root'):
            self.registry.associate_workspace(project['id'], 'codex', nested)

    def test_foreign_git_environment_cannot_make_unrelated_repo_look_linked(self):
        project = self.registry.create('Atlas', self.repo, self.repo)
        unrelated = self.base / 'foreign'
        unrelated.mkdir()
        git(unrelated, 'init', '-q')
        for variable in ('GIT_DIR', 'GIT_COMMON_DIR'):
            with self.subTest(variable=variable), patch.dict(os.environ, {variable: str(self.repo / '.git')}):
                with self.assertRaisesRegex(ValueError, 'common-dir'):
                    self.registry.associate_workspace(project['id'], 'codex', unrelated)

    def test_workspace_path_and_harness_conflicts_are_refused(self):
        project = self.registry.create('Atlas', self.repo, self.repo)
        first = self.base / 'first-worktree'
        second = self.base / 'second-worktree'
        git(self.repo, 'worktree', 'add', '-qb', 'first-work', str(first))
        git(self.repo, 'worktree', 'add', '-qb', 'second-work', str(second))
        self.registry.associate_workspace(project['id'], 'codex', first)
        with self.assertRaisesRegex(ValueError, 'different workspace'):
            self.registry.associate_workspace(project['id'], 'codex', second)
        with self.assertRaisesRegex(ValueError, 'already registered'):
            self.registry.associate_workspace(project['id'], 'claude', first)

    def test_workspace_cannot_be_claimed_by_another_project(self):
        project = self.registry.create('Atlas', self.repo, self.repo)
        workspace = self.base / 'worktree'
        git(self.repo, 'worktree', 'add', '-qb', 'agent-work', str(workspace))
        self.registry.associate_workspace(project['id'], 'codex', workspace)
        # A distinct source repository cannot claim the existing workspace
        # because common-dir validation rejects it before path reservation.
        other_repo = self.base / 'other'
        other_repo.mkdir()
        git(other_repo, 'init', '-q')
        other = self.registry.create('Other', other_repo, other_repo)
        with self.assertRaisesRegex(ValueError, 'common-dir'):
            self.registry.associate_workspace(other['id'], 'codex', workspace)

    def test_unknown_project_and_invalid_harness_are_refused(self):
        with self.assertRaisesRegex(ValueError, 'Unknown project'):
            self.registry.associate_workspace('missing', 'codex', self.repo)
        project = self.registry.create('Atlas', self.repo, self.repo)
        with self.assertRaises(ValueError):
            self.registry.associate_workspace(project['id'], '', self.repo)


if __name__ == '__main__':
    unittest.main()
