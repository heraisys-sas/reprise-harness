"""Private SQLite registry for named projects and explicit harness workspaces.

The registry records existing directories only. It never creates, removes, or
mutates Git repositories/worktrees. Workspaces must share the source
repository's Git common directory. Names are unique without regard to case;
UUIDs remain stable when a project is renamed.

Example::

    with ProjectRegistry(state_directory) as registry:
        project = registry.create('Atlas', project_root, source_repository)
        registry.associate_workspace(project['id'], 'codex', codex_worktree)

The database is ``<state_directory>/projects.sqlite``. The state directory is
made private (mode 0700), and the database is private (mode 0600).
"""
from pathlib import Path
import os
import sqlite3
import subprocess
import uuid
import unicodedata


def _directory(value, label):
    try:
        path = Path(value).expanduser().resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise ValueError(f'{label} must be an existing directory') from exc
    if not path.is_dir():
        raise ValueError(f'{label} must be an existing directory')
    return str(path)


def _git_info(path, label):
    # Ambient Git variables can redirect repository discovery even with -C.
    # Isolate each observation from any caller-selected repository context.
    environment = {key: value for key, value in os.environ.items()
                   if not key.startswith('GIT_')}
    try:
        top = subprocess.run(['git', '-C', path, 'rev-parse', '--show-toplevel'],
                             check=True, capture_output=True, text=True, timeout=10,
                             env=environment).stdout.strip()
        common = subprocess.run(['git', '-C', path, 'rev-parse', '--git-common-dir'],
                                check=True, capture_output=True, text=True, timeout=10,
                                env=environment).stdout.strip()
    except (OSError, subprocess.SubprocessError) as exc:
        raise ValueError(f'{label} must be an existing Git worktree') from exc
    common_path = Path(common)
    if not common_path.is_absolute():
        common_path = Path(path) / common_path
    try:
        return str(Path(top).resolve(strict=True)), str(common_path.resolve(strict=True))
    except (OSError, RuntimeError) as exc:
        raise ValueError(f'{label} Git metadata is unavailable') from exc


class ProjectRegistry:
    """Manage named project records and explicit existing workspace paths.

    ``root`` is the user-selected project location and may be a subdirectory
    of the source repository. ``source_repository`` must be its Git worktree
    root. Workspaces are assigned per project and harness, and must themselves
    be Git worktree roots sharing the source's Git common-dir. Assigning the
    source repository itself is supported for harnesses without an isolated
    worktree. Reassigning an occupied harness slot is deliberately refused.
    """

    def __init__(self, state_directory):
        state = Path(state_directory).expanduser()
        if state.is_symlink():
            raise ValueError('State directory must not be a symlink')
        state.mkdir(parents=True, exist_ok=True, mode=0o700)
        if not state.is_dir():
            raise ValueError('State path must be a directory')
        state.chmod(0o700)
        self.path = state / 'projects.sqlite'
        if self.path.is_symlink():
            raise ValueError('Registry database must not be a symlink')
        # Create/open the database with a restrictive umask even on systems
        # whose default umask permits group or other access.
        prior_umask = os.umask(0o177)
        try:
            self.db = sqlite3.connect(self.path, timeout=5, isolation_level=None)
        finally:
            os.umask(prior_umask)
        self.path.chmod(0o600)
        self.db.row_factory = sqlite3.Row
        self.db.execute('PRAGMA foreign_keys=ON')
        self.db.execute('PRAGMA busy_timeout=5000')
        self.db.executescript('''
            CREATE TABLE IF NOT EXISTS projects (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL CHECK(length(trim(name)) BETWEEN 1 AND 200),
                name_key TEXT NOT NULL,
                root_path TEXT NOT NULL,
                source_repository TEXT NOT NULL,
                source_common_dir TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS project_paths (
                canonical_path TEXT PRIMARY KEY,
                project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                kind TEXT NOT NULL CHECK(kind IN ('root','source','workspace'))
            );
            CREATE TABLE IF NOT EXISTS project_workspaces (
                project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                harness TEXT NOT NULL COLLATE NOCASE
                    CHECK(length(trim(harness)) BETWEEN 1 AND 80),
                path TEXT NOT NULL UNIQUE,
                common_dir TEXT NOT NULL,
                PRIMARY KEY(project_id, harness)
            );
        ''')
        # SQLite NOCASE folds ASCII only. Keep a Python Unicode casefold key,
        # and migrate prototype databases transactionally without dropping rows.
        self.db.execute('BEGIN IMMEDIATE')
        try:
            columns = {row['name'] for row in self.db.execute('PRAGMA table_info(projects)')}
            if 'name_key' not in columns:
                self.db.execute('ALTER TABLE projects ADD COLUMN name_key TEXT')
                rows = self.db.execute('SELECT id,name FROM projects ORDER BY id').fetchall()
                keys = {}
                for row in rows:
                    key = self._name_key(row['name'])
                    if key in keys:
                        raise ValueError('Existing project names collide under Unicode casefold; rename one before migration')
                    keys[key] = row['id']
                    self.db.execute('UPDATE projects SET name_key=? WHERE id=?', (key, row['id']))
            self.db.execute('CREATE UNIQUE INDEX IF NOT EXISTS project_name_key_unique ON projects(name_key)')
            self.db.commit()
        except BaseException:
            self.db.rollback()
            raise

    def close(self):
        self.db.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    def _project(self, row):
        if row is None:
            return None
        result = dict(row)
        result['workspaces'] = {
            item['harness']: item['path'] for item in self.db.execute(
                'SELECT harness,path FROM project_workspaces WHERE project_id=? ORDER BY harness',
                (result['id'],))
        }
        return result

    @staticmethod
    def _name_key(value):
        return unicodedata.normalize('NFC', value).casefold()

    @staticmethod
    def _name(value):
        if not isinstance(value, str) or not value.strip() or len(value.strip()) > 200:
            raise ValueError('Project name must contain 1 to 200 characters')
        return value.strip()

    def create(self, name, root, source_repository):
        """Create a project with a UUID; reject duplicate names and paths."""
        name = self._name(name)
        root_path = _directory(root, 'Project root')
        source_path = _directory(source_repository, 'Source repository')
        top, common = _git_info(source_path, 'Source repository')
        if top != source_path:
            raise ValueError('Source repository must be the canonical Git worktree root')
        project_id = str(uuid.uuid4())
        try:
            self.db.execute('BEGIN IMMEDIATE')
            self.db.execute('INSERT INTO projects(id,name,name_key,root_path,source_repository,source_common_dir) VALUES(?,?,?,?,?,?)',
                            (project_id, name, self._name_key(name), root_path, source_path, common))
            # Root and source often name the same directory. Reserve the path
            # once in that case, while still reserving both distinct paths.
            paths = [(root_path, 'root')]
            if source_path != root_path:
                paths.append((source_path, 'source'))
            for path, kind in paths:
                self.db.execute('INSERT INTO project_paths(canonical_path,project_id,kind) VALUES(?,?,?)',
                                (path, project_id, kind))
            self.db.commit()
        except sqlite3.IntegrityError as exc:
            self.db.rollback()
            message = 'Project name is already registered' if 'projects.name' in str(exc) or 'project_name_key_unique' in str(exc) else 'Project path is already registered'
            raise ValueError(message) from exc
        except BaseException:
            self.db.rollback()
            raise
        return self.get(project_id)

    def list(self):
        """Return projects ordered by case-insensitive name and immutable ID."""
        return [self._project(row) for row in self.db.execute(
            'SELECT * FROM projects ORDER BY name_key,id')]

    def get(self, project_id):
        """Return a project by UUID, or ``None`` if it is not registered."""
        return self._project(self.db.execute('SELECT * FROM projects WHERE id=?',
                                             (str(project_id),)).fetchone())

    def rename(self, project_id, name):
        """Rename a project without changing its UUID; names remain unique."""
        name = self._name(name)
        try:
            self.db.execute('BEGIN IMMEDIATE')
            cursor = self.db.execute('UPDATE projects SET name=?,name_key=? WHERE id=?',
                                     (name, self._name_key(name), str(project_id)))
            if cursor.rowcount != 1:
                raise ValueError('Unknown project')
            self.db.commit()
        except sqlite3.IntegrityError as exc:
            self.db.rollback()
            raise ValueError('Project name is already registered') from exc
        except BaseException:
            self.db.rollback()
            raise
        return self.get(project_id)

    def associate_workspace(self, project_id, harness, workspace):
        """Record an existing shared-common-dir worktree for one harness.

        Repeating the identical assignment is idempotent. A different path for
        the same harness slot, or a path already owned by another project, is
        rejected. The project's source path may be selected as the workspace.
        """
        if not isinstance(harness, str) or not harness.strip() or len(harness.strip()) > 80:
            raise ValueError('Harness name must contain 1 to 80 characters')
        harness = harness.strip()
        workspace_path = _directory(workspace, 'Workspace')
        workspace_top, common = _git_info(workspace_path, 'Workspace')
        if workspace_top != workspace_path:
            raise ValueError('Workspace must be the canonical Git worktree root')
        try:
            self.db.execute('BEGIN IMMEDIATE')
            project = self.db.execute('SELECT source_repository,source_common_dir FROM projects WHERE id=?',
                                      (str(project_id),)).fetchone()
            if project is None:
                raise ValueError('Unknown project')
            if common != project['source_common_dir']:
                raise ValueError('Workspace and source repository do not share a Git common-dir')
            existing = self.db.execute('SELECT path,common_dir FROM project_workspaces WHERE project_id=? AND harness=?',
                                        (str(project_id), harness)).fetchone()
            if existing:
                if existing['path'] != workspace_path or existing['common_dir'] != common:
                    raise ValueError('Harness already has a different workspace for this project')
                self.db.commit()
                return self.get(project_id)
            path_owner = self.db.execute('SELECT project_id FROM project_paths WHERE canonical_path=?',
                                         (workspace_path,)).fetchone()
            if path_owner and path_owner['project_id'] != str(project_id):
                raise ValueError('Workspace path is already registered')
            # Source (and, if identical, project root) is a valid workspace
            # for this project; do not create a contradictory second claim.
            if not path_owner:
                self.db.execute('INSERT INTO project_paths(canonical_path,project_id,kind) VALUES(?,?,?)',
                                (workspace_path, str(project_id), 'workspace'))
            self.db.execute('INSERT INTO project_workspaces(project_id,harness,path,common_dir) VALUES(?,?,?,?)',
                            (str(project_id), harness, workspace_path, common))
            self.db.commit()
        except sqlite3.IntegrityError as exc:
            self.db.rollback()
            raise ValueError('Workspace path or harness assignment is already registered') from exc
        except BaseException:
            self.db.rollback()
            raise
        return self.get(project_id)
