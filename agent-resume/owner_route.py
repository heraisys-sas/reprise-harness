"""Explicit delegation to a verified parent through Codex's native queue."""
import json
import os
from pathlib import Path
import subprocess
import time
import uuid

from codex_rpc import Client
from engines import Unavailable
import run_status


def read_thread(client, ident):
    thread = client.call('thread/read', {'threadId': ident, 'includeTurns': False}).get('thread') or {}
    if thread.get('id') != ident:
        raise Unavailable('blocked_session', 'Le serveur ne confirme pas l’identité de la session.')
    return thread


def parent_thread(client, task, parent):
    try:
        if str(uuid.UUID(parent)) != parent or parent == task['session']:
            raise ValueError()
    except (ValueError, TypeError, AttributeError):
        raise Unavailable('blocked_session', 'Identifiant explicite de session parente requis.') from None
    child = read_thread(client, task['session'])
    owner = read_thread(client, parent)
    if child.get('parentThreadId') != parent:
        raise Unavailable('blocked_session', 'Le serveur ne confirme pas la relation parent–enfant.')
    cwd = Path(task['cwd']).resolve()
    if any(not thread.get('cwd') or Path(thread['cwd']).resolve() != cwd for thread in (child, owner)):
        raise Unavailable('blocked_session', 'La délégation exige le même dossier confirmé pour le parent et l’enfant.')
    if (owner.get('status') or {}).get('type') != 'idle':
        raise Unavailable('blocked_owner', 'La session parente n’est pas au repos ; aucune consigne envoyée.')
    return owner


def queue_once(client, parent, ident, text):
    cursor, seen = None, set()
    for _ in range(20):
        page = client.call('thread/queue/list', {'threadId': parent, 'cursor': cursor, 'limit': 100})
        if any(item.get('clientUserMessageId') == ident for item in page.get('data', [])):
            raise Unavailable('queued_parent', 'Cette demande figure déjà dans la file du parent ; aucun doublon envoyé.')
        cursor = page.get('nextCursor')
        if cursor is None:
            break
        if cursor in seen:
            raise Unavailable('needs_review', 'La file du parent n’a pas pu être vérifiée intégralement.')
        seen.add(cursor)
    else:
        raise Unavailable('needs_review', 'File du parent trop longue pour confirmer l’absence de doublon.')
    result = client.call('thread/queue/add', {'threadId': parent, 'clientUserMessageId': ident,
                         'input': [{'type': 'text', 'text': text, 'text_elements': []}]})
    queued = result.get('queuedSubmission') or {}
    if not queued.get('id') or queued.get('clientUserMessageId') != ident:
        raise Unavailable('needs_review', 'Réception de la demande non confirmée ; ne pas la renvoyer automatiquement.')
    return queued['id']


def execute(db, task, program, values, log, automatic=False, timeout=15):
    ident, parent = task['id'], values.get('parent')
    client, receipt = None, {}
    state, reason = 'needs_review', 'Délégation non confirmée.'
    run_status.save(db, ident, 'launching', 'Vérification du propriétaire et de la session parente', started_at=time.time())
    try:
        client = Client(program, values, timeout=timeout)
        client.call('initialize', {'clientInfo': {'name': 'reprise', 'version': '0.1.0'},
                                  'capabilities': {'experimentalApi': True}})
        client.send({'method': 'initialized'})
        owner = parent_thread(client, task, parent)
        if automatic:
            account = client.call('account/read', {'refreshToken': False}).get('account') or {}
            limits = client.call('account/rateLimits/read')
            if account.get('type') != 'chatgpt' or owner.get('modelProvider') != 'openai':
                raise Unavailable('blocked_auth', 'La délégation automatique exige le compte ChatGPT et le fournisseur OpenAI existants.')
            if limits.get('ordinaryUsageAllowed') is not True:
                raise Unavailable('quota_blocked', 'Le serveur ne confirme pas la disponibilité de l’usage inclus.')
        text = (f'Reprise request {ident}. Resume only your existing child agent {task["session"]} '
                'using your native agent controls if it is interrupted and still needs work. '
                'Do not create a replacement agent, repeat completed work, change permissions, or bypass a quota. '
                'If it is already running, finished, or requires human input, report that state and do nothing. '
                'Do not perform the child task yourself. Authorized continuation instruction:\n' + task['prompt'])
        queued = queue_once(client, parent, ident, text)
        receipt = {'parent': parent, 'child': task['session'], 'queued_submission_id': queued}
        state, reason = 'queued_parent', 'Demande reçue dans la file du parent ; reprise de l’enfant non confirmée.'
        # Recheck after enqueue: queue/start must never deliberately steer a busy parent.
        parent_thread(client, task, parent)
        result = client.call('thread/queue/start', {'threadId': parent, 'queuedSubmissionId': queued})
        turn = (result.get('turn') or {}).get('id')
        if not turn:
            raise Unavailable('queued_parent', 'Demande déposée ; démarrage du parent non confirmé, ne pas renvoyer.')
        receipt['parent_turn_id'] = turn
        state, reason = 'delegated_parent', 'Tour du parent confirmé ; son action et la reprise de l’enfant restent à vérifier.'
    except Unavailable as error:
        state, reason = ('queued_parent', 'Demande déposée ; action du parent à vérifier, aucune relance automatique.') if receipt else (error.state, error.reason)
    except (TimeoutError, OSError, ValueError, subprocess.SubprocessError):
        state, reason = ('queued_parent', 'Transmission au parent incertaine ; aucune seconde tentative automatique.') if receipt else ('needs_review', 'Connexion interrompue ; réception incertaine, aucune seconde tentative automatique.')
    finally:
        if client:
            try:
                client.close()
            except (OSError, subprocess.SubprocessError):
                pass
        if receipt:
            with Path(log).open('w') as output:
                os.chmod(log, 0o600)
                output.write(json.dumps(receipt) + '\n')
    run_status.save(db, ident, state, reason, ended_at=time.time())
    return 'needs_review'
