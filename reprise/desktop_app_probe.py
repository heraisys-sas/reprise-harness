#!/usr/bin/env python3
"""Bounded MCP Apps experiment; capability-only mode never exposes tools."""
import argparse
import json
import os
from pathlib import Path
from desktop_job_probe import Probe, TOOLS, serve

EXTENSION = 'io.modelcontextprotocol/ui'
MIME = 'text/html;profile=mcp-app'
URI = 'ui://reprise-test/panel.html'
EMPTY = {'type': 'object', 'properties': {}, 'additionalProperties': False}
APP_TOOLS = [
    {'name': 'open_test_panel', 'description': 'Display the Reprise single-job test button. Do not get or solve the task until the user clicks it.',
     'inputSchema': EMPTY, '_meta': {'ui': {'resourceUri': URI}}},
    {'name': 'reserve_dispatch', 'description': 'Reserve the only test message before sending it. Never retry after uncertainty.',
     'inputSchema': EMPTY, '_meta': {'ui': {'visibility': ['app']}}},
    {'name': 'record_ui_event', 'description': 'Record a bounded widget protocol event; never triggers a model.',
     'inputSchema': {'type': 'object', 'properties': {'event': {'type': 'string', 'enum': ['ready','message_ack','message_error']}},
                     'required': ['event'], 'additionalProperties': False},
     '_meta': {'ui': {'visibility': ['app']}}},
]


class AppProbe(Probe):
    def __init__(self, state, capabilities_only=False):
        super().__init__(state)
        self.state = Path(state)
        self.capabilities_only = capabilities_only
        self.ui_supported = False
        with self.db:
            self.db.execute('CREATE TABLE IF NOT EXISTS dispatch(id INTEGER PRIMARY KEY CHECK(id=1),reserved INTEGER NOT NULL)')
            self.db.execute('INSERT OR IGNORE INTO dispatch VALUES (1,0)')
            self.db.execute('CREATE TABLE IF NOT EXISTS ui_events(event TEXT PRIMARY KEY)')

    def call(self, name, args):
        if self.capabilities_only or not self.ui_supported:
            raise ValueError('UI mode not negotiated')
        if name == 'open_test_panel':
            if args != {}:
                raise ValueError('No arguments expected')
            return {'status': 'panel_ready', 'instruction': 'Wait for the user to click the panel button. Do not solve or claim a job now.'}
        if name == 'reserve_dispatch':
            if args != {}:
                raise ValueError('No arguments expected')
            with self.db:
                self.db.execute('BEGIN IMMEDIATE')
                if (self.db.execute('SELECT reserved FROM dispatch').fetchone()[0]
                        or self.db.execute('SELECT status FROM job').fetchone()[0] != 'pending'):
                    raise ValueError('Already reserved or assigned; do not resend')
                self.db.execute('UPDATE dispatch SET reserved=1')
            return {'status': 'reserved'}
        if name == 'record_ui_event':
            if not isinstance(args, dict) or args.keys() != {'event'} or args['event'] not in ('ready','message_ack','message_error'):
                raise ValueError('Invalid event')
            with self.db:
                self.db.execute('INSERT OR IGNORE INTO ui_events VALUES (?)', (args['event'],))
            return {'status': 'recorded'}
        return super().call(name, args)

    def dispatch(self, message):
        method, params = message.get('method'), message.get('params') or {}
        if method == 'initialize':
            caps = params.get('capabilities') or {}
            extensions = caps.get('extensions') or {}
            ui = extensions.get(EXTENSION)
            # Keep only the selected public protocol capability, not arbitrary input.
            if len(json.dumps(ui)) > 4096:
                raise ValueError('Unexpected capability size')
            self.ui_supported = isinstance(ui, dict) and MIME in ui.get('mimeTypes', [])
            evidence = {'client': params.get('clientInfo'), 'protocolVersion': params.get('protocolVersion'),
                        'extension_names': sorted(extensions), 'ui_capability': ui,
                        'ui_supported': self.ui_supported, 'capabilities_only': self.capabilities_only}
            path = self.state/'ui-capabilities.json'
            path.write_text(json.dumps(evidence, indent=2)+'\n')
            path.chmod(0o600)
            result = super().dispatch(message)
            if self.capabilities_only:
                result['capabilities'] = {}
            elif self.ui_supported:
                result['capabilities'] |= {'resources': {}, 'extensions': {EXTENSION: {'mimeTypes':[MIME]}}}
            return result
        if method == 'tools/list':
            return {'tools': [] if self.capabilities_only or not self.ui_supported else TOOLS + APP_TOOLS}
        if method == 'resources/list':
            return {'resources': [] if self.capabilities_only or not self.ui_supported else [
                {'uri': URI, 'name': 'Reprise single-job test', 'mimeType': MIME}]}
        if method == 'resources/read':
            if (self.capabilities_only or not self.ui_supported or params.get('uri') != URI
                    or params.keys() - {'uri', '_meta'}):
                raise ValueError('Exact negotiated UI resource required')
            return {'contents': [{'uri': URI, 'mimeType': MIME,
                                 'text': Path(__file__).with_name('desktop_app_probe.html').read_text(),
                                 '_meta': {'ui': {'prefersBorder': True, 'csp': {'connectDomains': [], 'resourceDomains': []}}}}]}
        return super().dispatch(message)


if __name__ == '__main__':
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--state', required=True)
    parser.add_argument('--capabilities-only', action='store_true')
    args = parser.parse_args()
    serve(AppProbe(args.state, args.capabilities_only))
