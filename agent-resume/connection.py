"""Read-only Codex discovery and explicit transport configuration."""
import json
from pathlib import Path
import engines


def status(db, state):
    row = db.execute("SELECT options FROM engine_settings WHERE engine='codex'").fetchone()
    configured = json.loads(row[0]).get('socket') if row else None
    paths = [(str(engines.SOCKET), 'Serveur Codex local'),
             (str(Path(state)/'codex-expert.sock'), 'Serveur d’expertise dédié')]
    if configured and configured not in {p for p, _ in paths}:
        paths.insert(0, (configured, 'Chemin enregistré'))
    candidates = []
    for path, label in paths:
        try:
            engines.socket_path({'socket': path})
            detected, reason = True, 'Socket détecté · connexion à tester'
        except engines.Unavailable as error:
            detected, reason = False, error.reason
        candidates.append({'path': path, 'label': label, 'detected': detected, 'reason': reason})
    from shared_mode import state as shared_state
    return {'configured_socket': configured, 'mode': 'server' if configured else 'auto', 'candidates': candidates,
            'shared': shared_state(db)}


def probe(program, path, session=None):
    from codex_rpc import Client
    client = None
    try:
        values = engines.options('codex-server', {'socket': path})
        if not program:
            raise engines.Unavailable('engine_unavailable', 'Exécutable Codex introuvable.')
        client = Client(program, values, timeout=5)
    except engines.Unavailable as error:
        return {'connected': False, 'model_called': False, 'socket_state': 'unreachable', 'reason': error.reason}
    except (OSError, ValueError, TimeoutError):
        # La construction du Client couvre le lancement du proxy et le handshake :
        # un échec ici signifie qu'aucune requête n'a pu partir → injoignable.
        return {'connected': False, 'model_called': False, 'socket_state': 'unreachable',
                'reason': 'Serveur Codex injoignable ; aucun tour déclenché.'}
    try:
        client.call('initialize', {'clientInfo': {'name': 'reprise-connection', 'version': '0.1.0'}})
        client.send({'method': 'initialized'})
        result = {'connected': True, 'model_called': False, 'socket_state': 'responsive',
                  'reason': 'Connexion confirmée. La propriété des sessions Desktop reste à vérifier.'}
        if session:
            session = engines.validate_session('codex-cli', session)
            thread = client.call('thread/read', {'threadId': session, 'includeTurns': False}).get('thread') or {}
            state = (thread.get('status') or {}).get('type')
            result.update(thread_id=session, thread_status=state,
                          thread_loaded=thread.get('id') == session and state in ('idle', 'active'))
            result['reason'] = ('Session chargée dans ce serveur ; aucun tour déclenché.' if result['thread_loaded'] else
                                'Le serveur ne confirme pas que cette session est chargée chez lui.')
        return result
    except engines.Unavailable as error:
        return {'connected': False, 'model_called': False, 'socket_state': 'unreachable', 'reason': error.reason}
    except (OSError, ValueError, TimeoutError):
        # La connexion a réussi mais initialize/thread/read n'aboutit pas :
        # socket vivant, protocole muet ou incompatible (#52).
        return {'connected': False, 'model_called': False, 'socket_state': 'protocol_muted',
                'reason': 'Le serveur ne répond pas au protocole Codex attendu.'}
    finally:
        if client:
            try:
                client.close()
            except OSError:
                pass


def configure(db, program, path=None):
    if path is None:
        result = {'saved': True, 'reason': 'Détection automatique restaurée : socket Codex local ou CLI.'}
    else:
        result = probe(program, path)
        if not result['connected']:
            return result | {'saved': False}
        result |= {'saved': True, 'reason': 'Socket enregistré pour les reprises Codex sans transport explicite. Propriété des sessions non vérifiée.'}
    with db:
        db.execute('INSERT OR REPLACE INTO engine_settings VALUES (?,?)', ('codex', json.dumps({'socket': path} if path else {})))
    return result
