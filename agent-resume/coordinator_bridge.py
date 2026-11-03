"""Thread-owned adapter to Reprise Bridge; no inference on construction."""
from pathlib import Path
import threading

from codex_expert_mcp import Bridge


class LocalBridge:
    """Instantiate separately in each owning worker or status-reader thread."""
    def __init__(self, project_root, state, *, socket=None, codex_workspace_write=False):
        self.owner = threading.get_ident()
        self.root = Path(project_root).resolve(strict=True)
        self.bridge = Bridge(self.root, {'socket': socket} if socket else {}, state,
            codex_workspace_write=codex_workspace_write,
            desktop_state=state,
            allowed_tools=['reprise_backends', 'reprise_requests', 'reprise_delegate'])

    def _owner(self):
        if threading.get_ident() != self.owner:
            raise RuntimeError('Bridge must not be shared across threads')

    def discover(self):
        self._owner()
        return self.bridge.call('reprise_backends', {})

    def submit(self, payload):
        self._owner()
        return self.bridge.call('reprise_delegate', payload)

    def receipt(self, request_key):
        self._owner()
        return self.bridge.call('reprise_requests', {'request_key': request_key, 'view': 'receipt'})

    def close(self):
        self._owner()
        self.bridge.db.close()
        tasks = self.bridge.desktop_tasks
        if tasks is not None:
            # TaskStore owns its own SQLite connection.
            connection = getattr(tasks, 'db', None)
            if connection is not None:
                connection.close()
