import json
import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from package_helpers import ASSETS, package


SOURCE = Path(__file__).resolve().parents[1]


def git(path, *args):
    return subprocess.run(['git', '-C', str(path), *args], check=True,
                          capture_output=True, text=True).stdout.strip()


class ProjectCommandsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name).resolve()
        self.home = self.base / 'home'
        self.state = self.base / 'explicit-state'
        self.resume = SOURCE / 'resume'
        self.env = dict(os.environ, HOME=str(self.home), AGENT_RESUME_STATE=str(self.state))
        self.env.pop('PYTHONPATH', None)
        self.repo = self._repo(self.base / 'source', commit=True)

    def tearDown(self):
        self.temp.cleanup()

    def _repo(self, path, *, commit=False):
        path.mkdir(parents=True)
        git(path, 'init', '-q', '-b', 'main')
        if commit:
            git(path, 'config', 'user.email', 'test@example.invalid')
            git(path, 'config', 'user.name', 'CLI Test')
            (path / 'file.txt').write_text('test\n')
            git(path, 'add', 'file.txt')
            git(path, 'commit', '-qm', 'initial')
        return path

    def command(self, *args, resume=None, env=None):
        result = subprocess.run([sys.executable, str(resume or self.resume), *args],
                                cwd=self.base, env=env or self.env,
                                capture_output=True, text=True, timeout=30)
        try:
            body = json.loads(result.stdout)
        except json.JSONDecodeError as error:
            self.fail(f'CLI returned non-JSON output ({result.returncode}): {result.stdout!r} {result.stderr!r}: {error}')
        return result, body

    def test_cli_create_list_rename_and_source_workspace_association(self):
        result, initial = self.command('project-list')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(initial, {'status': 'ok', 'projects': []})
        self.assertTrue((self.state / 'projects.sqlite').is_file())
        self.assertFalse(self.home.exists())

        result, created = self.command('project-create', '--name', 'Atlas',
                                       '--root', str(self.repo), '--source', str(self.repo))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(created['status'], 'ok')
        project = created['project']
        project_id = project['id']
        self.assertEqual(project['name'], 'Atlas')

        result, listed = self.command('project-list')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(listed['projects'], [project])

        result, associated = self.command('project-associate', '--project-id', project_id,
                                          '--harness', 'claude', '--workspace', str(self.repo))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(associated['project']['workspaces'], {'claude': str(self.repo)})

        result, renamed = self.command('project-rename', '--project-id', project_id,
                                       '--name', 'Atlas Local')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(renamed['project']['id'], project_id)
        self.assertEqual(renamed['project']['name'], 'Atlas Local')
        self.assertEqual(renamed['project']['workspaces'], {'claude': str(self.repo)})

    def test_cli_associates_a_temporary_linked_worktree(self):
        result, created = self.command('project-create', '--name', 'Atlas',
                                       '--root', str(self.repo), '--source', str(self.repo))
        project_id = created['project']['id']
        workspace = self.base / 'codex-worktree'
        git(self.repo, 'worktree', 'add', '-qb', 'codex-task', str(workspace))
        result, body = self.command('project-associate', '--project-id', project_id,
                                    '--harness', 'codex', '--workspace', str(workspace))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(body['status'], 'ok')
        self.assertEqual(body['project']['workspaces'], {'codex': str(workspace)})

    def test_cli_refuses_duplicate_name_and_reports_json(self):
        result, first = self.command('project-create', '--name', 'Atlas',
                                     '--root', str(self.repo), '--source', str(self.repo))
        self.assertEqual(result.returncode, 0, result.stderr)
        other = self._repo(self.base / 'other')
        result, refused = self.command('project-create', '--name', 'atlas',
                                       '--root', str(other), '--source', str(other))
        self.assertEqual(result.returncode, 1)
        self.assertEqual(refused['status'], 'refused')
        self.assertIn('name is already registered', refused['message'])
        result, listed = self.command('project-list')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual([item['id'] for item in listed['projects']], [first['project']['id']])

    def test_cli_refuses_unrelated_repository_and_conflicting_harness_workspace(self):
        result, created = self.command('project-create', '--name', 'Atlas',
                                       '--root', str(self.repo), '--source', str(self.repo))
        project_id = created['project']['id']
        unrelated = self._repo(self.base / 'unrelated')
        result, refused = self.command('project-associate', '--project-id', project_id,
                                       '--harness', 'codex', '--workspace', str(unrelated))
        self.assertEqual(result.returncode, 1)
        self.assertEqual(refused['status'], 'refused')
        self.assertIn('common-dir', refused['message'])

        first = self.base / 'first-worktree'
        second = self.base / 'second-worktree'
        git(self.repo, 'worktree', 'add', '-qb', 'first-work', str(first))
        git(self.repo, 'worktree', 'add', '-qb', 'second-work', str(second))
        result, success = self.command('project-associate', '--project-id', project_id,
                                       '--harness', 'codex', '--workspace', str(first))
        self.assertEqual(result.returncode, 0, result.stderr)
        result, refused = self.command('project-associate', '--project-id', project_id,
                                       '--harness', 'codex', '--workspace', str(second))
        self.assertEqual(result.returncode, 1)
        self.assertEqual(refused['status'], 'refused')
        self.assertIn('different workspace', refused['message'])
        result, listed = self.command('project-list')
        self.assertEqual(listed['projects'][0]['workspaces'], success['project']['workspaces'])

    def test_cli_refuses_unknown_project_and_bad_harness_with_json(self):
        result, missing = self.command('project-rename', '--project-id', 'missing', '--name', 'Renamed')
        self.assertEqual(result.returncode, 1)
        self.assertEqual(missing['status'], 'refused')
        self.assertIn('Unknown project', missing['message'])
        result, invalid = self.command('project-associate', '--project-id', 'missing',
                                       '--harness', 'codex', '--workspace', str(self.repo))
        self.assertEqual(result.returncode, 1)
        self.assertEqual(invalid['status'], 'refused')

    def test_moved_packaged_payload_contains_and_runs_registry_helpers(self):
        app = self.base / 'build/Reprise.app'
        package(SOURCE, app)
        moved = self.base / 'distribution/Reprise.app'
        moved.parent.mkdir()
        shutil.move(app, moved)
        moved_helpers = moved / 'Contents/Resources/helpers'
        manifest = json.loads((moved_helpers / 'payload-manifest.json').read_text())
        for name in ('project_registry.py', 'project_commands.py'):
            self.assertIn(name, ASSETS)
            self.assertTrue((moved_helpers / name).is_file())
            self.assertIn(name, manifest['files'])
        for name in ('project_registry.py', 'project_commands.py'):
            self.assertEqual(hashlib.sha256((moved_helpers / name).read_bytes()).hexdigest(),
                             manifest['files'][name])

        packaged_env = dict(self.env)
        packaged_env['AGENT_RESUME_STATE'] = str(self.base / 'packaged-state')
        packaged_resume = moved_helpers / 'resume'
        result, listed = self.command('project-list', resume=packaged_resume, env=packaged_env)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(listed, {'status': 'ok', 'projects': []})
        repo = self.base / 'packaged-source'
        self._repo(repo, commit=True)
        result, created = self.command('project-create', '--name', 'Packaged', '--root', str(repo),
                                       '--source', str(repo), resume=packaged_resume, env=packaged_env)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(created['project']['name'], 'Packaged')
        self.assertTrue((self.base / 'packaged-state/projects.sqlite').is_file())
        self.assertFalse((moved_helpers / 'state').exists())


if __name__ == '__main__':
    unittest.main()
