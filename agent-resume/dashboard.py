import http.server
import json
from pathlib import Path
import secrets
import threading
import webbrowser


def serve(database, root):
    token = secrets.token_urlsafe(32)

    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def reply(self, status, body, content='application/json'):
            self.send_response(status)
            self.send_header('Content-Type', content + '; charset=utf-8')
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Content-Security-Policy', "default-src 'self'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; frame-ancestors 'none'; connect-src 'self'")
            self.end_headers()
            self.wfile.write(body.encode())

        def allowed(self):
            return self.headers.get('Host') == f'127.0.0.1:{self.server.server_port}'

        def do_GET(self):
            if not self.allowed():
                self.reply(403, '{}'); return
            if self.path == '/':
                self.reply(200, (root / 'dashboard.html').read_text(), 'text/html'); return
            if self.path == '/tasks' and self.headers.get('X-Resume-Token') == token:
                with database() as db:
                    rows = [dict(r) for r in db.execute('SELECT id,cwd,due,status,attempts FROM tasks ORDER BY due')]
                self.reply(200, json.dumps(rows)); return
            self.reply(404, '{}')

        def do_POST(self):
            if not self.allowed() or self.headers.get('X-Resume-Token') != token or self.path != '/task':
                self.reply(403, '{}'); return
            try:
                size = int(self.headers.get('Content-Length', '0'))
                if not 0 < size <= 2048:
                    raise ValueError()
                data = json.loads(self.rfile.read(size))
                if data['action'] not in ('pause', 'enable'):
                    raise ValueError()
                with database() as db:
                    row = db.execute('SELECT * FROM tasks WHERE id=?', (data['id'],)).fetchone()
                    if not row or row['status'] == 'running':
                        self.reply(409, json.dumps({'error': 'Cette tâche est déjà en cours ou indisponible.'})); return
                    if data['action'] == 'enable' and not Path(row['cwd']).is_dir():
                        self.reply(409, json.dumps({'error': 'Le dossier du projet doit être reconnecté.'})); return
                    db.execute('UPDATE tasks SET status=? WHERE id=?', ('ready' if data['action'] == 'enable' else 'paused', data['id']))
                self.reply(200, '{}')
            except (ValueError, KeyError, TypeError):
                self.reply(400, '{}')

    server = http.server.HTTPServer(('127.0.0.1', 0), Handler)
    url = f'http://127.0.0.1:{server.server_port}/#{token}'
    print(url, flush=True)
    threading.Timer(0.3, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
