"""Reconcile a persisted request with the native turn; never submit a new turn."""
from pathlib import Path

from codex_expert import MAX_RESPONSE_CHARACTERS, STOP_REASONS, stop_turn
from codex_rpc import Client
from engines import Unavailable
from delegation_store import file_evidence

ACTIVE_STATUSES = ('inprogress', 'in_progress', 'active', 'running', 'pending')


def _thread(client, thread_id):
    """Observe without loading first; resume only loads history, it starts no turn."""
    try:
        thread = client.call('thread/read', {'threadId': thread_id, 'includeTurns': True}).get('thread')
    except (OSError, ValueError, TimeoutError, Unavailable):
        thread = None
    if thread:
        return thread
    try:
        client.call('thread/resume', {'threadId': thread_id, 'excludeTurns': False})
        return client.call('thread/read', {'threadId': thread_id, 'includeTurns': True}).get('thread')
    except (OSError, ValueError, TimeoutError, Unavailable):
        return None


def _owned(thread, thread_id, root):
    return bool(thread and thread.get('id') == thread_id and thread.get('modelProvider') == 'openai'
                and thread.get('cwd') and Path(thread['cwd']).resolve() == root)


def _native_status(turn):
    status = turn.get('status')
    if isinstance(status, dict):
        status = status.get('type')
    return str(status or '').lower()


def pending_approval(turn):
    for item in turn.get('items') or []:
        kind = str(item.get('type') or '')
        if 'requestApproval' in kind or 'request_approval' in kind:
            return kind
    for name in ('pendingApproval', 'approval'):
        value = turn.get(name)
        if value:
            return str((value if isinstance(value, dict) else {}).get('method') or name)
    return None


def final_text(turn):
    """Return the captured final agent message, or None when it is not restitutable."""
    parts = []
    for item in turn.get('items') or []:
        if item.get('type') not in ('agentMessage', 'agent_message'):
            continue
        if item.get('phase') not in (None, 'final_answer'):
            continue
        if isinstance(item.get('text'), str):
            parts.append(item['text'])
    return '\n'.join(parts) if parts else None


def _response(store, key, **fields):
    receipt = store.lookup({'request_key': key, 'view': 'receipt'})
    base = {'request_key': key, 'state': receipt.get('state'), 'phase': receipt.get('phase'),
            'work_completed': receipt.get('work_completed'),
            'terminal_evidence': receipt.get('terminal_evidence'),
            'next_action': receipt.get('next_action')}
    base.update(fields)
    return base


def reconcile(store, root, request_key, *, action='observe', reason=None, program=None, values=None,
              client_factory=None):
    """Observe or explicitly stop one persisted native turn.

    No `turn/start`, no retry, no fallback. An unreachable or unreadable native state is reported
    as `owner_unavailable` and keeps the uncertainty instead of inventing a result.
    """
    root = Path(root).resolve(strict=True)
    receipt = store.lookup({'request_key': request_key})
    if receipt.get('status') == 'not_found':
        raise ValueError('Unknown request_key')
    native = store.phase(request_key) or {}
    thread_id, turn_id = native.get('thread_id'), native.get('turn_id')
    if action not in ('observe', 'stop'):
        raise ValueError('Unknown reconciliation action')
    if action == 'stop':
        if reason not in STOP_REASONS:
            raise ValueError('Explicit authorized stop reason required')
        if not (thread_id and turn_id):
            raise ValueError('No native turn is persisted for this request')
        if receipt.get('state') == 'recorded' and (receipt.get('result') or {}).get('status') in ('finished', 'interrupted'):
            raise ValueError('Turn is already terminal')
    if not thread_id:
        return _response(store, request_key, status='unknown', phase='unknown',
                         reason='No native thread was persisted before the client returned.')
    if receipt.get('state') == 'recorded' and (receipt.get('result') or {}).get('status') in ('finished', 'interrupted'):
        result = receipt['result']
        return _response(store, request_key, status=result['status'], thread_id=thread_id,
                         turn_id=turn_id, response=result.get('response', ''),
                         reason='Terminal result already captured; native state was not modified.')
    if not turn_id:
        return _response(store, request_key, status='unknown', phase='unknown', next_action='reconcile',
                         reason='Native turn identity is missing; no other turn may be adopted or replayed.')
    client = None
    try:
        try:
            client = (client_factory or Client)(program, values)
            client.call('initialize', {'clientInfo': {'name': 'reprise-reconcile', 'version': '0.1.0'},
                                       'capabilities': {'experimentalApi': True}})
            client.send({'method': 'initialized'})
        except (OSError, ValueError, TimeoutError, Unavailable):
            store.set_phase(request_key, 'owner_unavailable')
            return _response(store, request_key, status='owner_unavailable', thread_id=thread_id,
                             turn_id=turn_id,
                             reason='Native endpoint unreachable; uncertainty preserved, no new turn.')
        thread = _thread(client, thread_id)
        if not _owned(thread, thread_id, root):
            store.set_phase(request_key, 'owner_unavailable')
            return _response(store, request_key, status='owner_unavailable', thread_id=thread_id,
                             turn_id=turn_id, reason='Native thread unreachable, unloaded or not owned; uncertainty preserved.')
        turns = thread.get('turns')
        if not isinstance(turns, list):
            store.set_phase(request_key, 'owner_unavailable')
            return _response(store, request_key, status='owner_unavailable', thread_id=thread_id,
                             turn_id=turn_id, next_action='collect',
                             reason='Native turn history is not restitutable; retrieve what exists or reconcile again later.')
        turn = next((item for item in turns if item.get('id') == turn_id), None) if turn_id else None
        if turn is None:
            store.set_phase(request_key, 'owner_unavailable')
            return _response(store, request_key, status='owner_unavailable', thread_id=thread_id,
                             turn_id=turn_id, reason='The persisted turn is not present in the native history.')
        status = _native_status(turn)
        if action == 'stop' and status not in ACTIVE_STATUSES:
            # Never interrupt an already terminal turn: report the observed state instead.
            action = 'observe'
        if action == 'stop':
            store.set_phase(request_key, 'unknown', work_completed=False, last_event='stop_requested:' + reason)
            journal = stop_turn(client, thread_id, turn_id, reason)
            result = dict(receipt.get('result') or {})
            result.update(status='interrupted' if journal['confirmed'] else 'needs_review',
                          phase='interrupted' if journal['confirmed'] else 'unknown', work_completed=False,
                          interrupt=journal, thread_id=thread_id, turn_id=turn_id,
                          next_action='collect' if journal['confirmed'] else 'reconcile',
                          reason='Explicit stop requested; native terminal evidence is required.')
            if journal['confirmed']:
                result['terminal_evidence'] = 'native:interrupted'
            store.finish(request_key, result)
            return _response(store, request_key, status=result['status'], interrupt=journal,
                             thread_id=thread_id, turn_id=turn_id, work_completed=False,
                             reason=result['reason'])
        if status in ACTIVE_STATUSES:
            method = (pending_approval(turn) or native.get('pending_method') or
                      (receipt.get('result') or {}).get('pending_request', {}).get('method'))
            phase = 'awaiting_approval' if method else 'active'
            store.set_phase(request_key, phase, pending_method=method, work_completed=False)
            store.finish(request_key, dict(receipt.get('result') or {}, status='awaiting_approval' if method else 'running',
                                           phase=phase, work_completed=False, thread_id=thread_id,
                                           pending_request={'method': method} if method else None,
                                           turn_id=turn_id, next_action='reconcile'))
            return _response(store, request_key,
                             status='awaiting_approval' if method else 'running',
                             pending_request={'method': method} if method else None,
                             thread_id=thread_id, turn_id=turn_id,
                             reason='Native turn is still active; it was not interrupted.')
        if status == 'completed':
            text = final_text(turn)
            if text is None or not text.strip():
                store.set_phase(request_key, 'owner_unavailable', work_completed=False,
                                terminal_evidence='native:completed; final text not retrievable')
                return _response(store, request_key, status='owner_unavailable',
                                 thread_id=thread_id, turn_id=turn_id, next_action='collect',
                                 reason='Turn completed but its final message is not restitutable; no new turn was started.')
            truncated = len(text) > MAX_RESPONSE_CHARACTERS
            result = dict(receipt.get('result') or {})
            context = native.get('context') or {}
            if context.get('mode', 'expertise') != 'implementation' and 'file_evidence' in context:
                expected = context['file_evidence']
                try:
                    changed = expected != file_evidence(root, file_paths=[item['path'] for item in expected])
                except (ValueError, OSError):
                    changed = True
                if changed:
                    result.update(input_changed=True, original_reason=result.get('original_reason', result.get('reason')))
            requires_review = truncated or bool(result.get('input_changed'))
            result.update(status='needs_review' if requires_review else 'finished',
                          phase='finished', work_completed=not requires_review,
                          terminal_evidence='native:completed', response=text[:MAX_RESPONSE_CHARACTERS],
                          output_complete=not truncated, output_truncated=truncated, thread_id=thread_id, turn_id=turn_id,
                          next_action='reconcile' if requires_review else 'collect')
            if result.get('input_changed'):
                result['reason'] = 'Native result captured; changed inputs still require review.'
            if truncated:
                result.update(output_truncated=True, capture_limit_characters=MAX_RESPONSE_CHARACTERS,
                              reason='Late result exceeds capture limit; stored output is incomplete.')
            store.finish(request_key, result)
            return _response(store, request_key, status=result['status'], thread_id=thread_id,
                             turn_id=turn_id, work_completed=result['work_completed'],
                             response=result['response'],
                             reason='Late native result captured after detachment; no second turn was started.')
        if status == 'interrupted':
            result = dict(receipt.get('result') or {})
            result.update(status='interrupted', phase='interrupted', work_completed=False,
                          terminal_evidence='native:interrupted', thread_id=thread_id,
                          turn_id=turn_id, next_action='collect')
            store.finish(request_key, result)
            return _response(store, request_key, status='interrupted', thread_id=thread_id,
                             turn_id=turn_id, work_completed=False,
                             reason='Native turn is confirmed stopped; this is not a completed deliverable.')
        phase = 'finished' if status == 'failed' else 'unknown'
        store.set_phase(request_key, phase, work_completed=False,
                        terminal_evidence='native:' + (status or 'unknown'))
        result = dict(receipt.get('result') or {})
        result.update(status='needs_review', phase=phase, work_completed=False,
                      terminal_evidence='native:' + (status or 'unknown'),
                      thread_id=thread_id, turn_id=turn_id, next_action='reconcile')
        store.finish(request_key, result)
        return _response(store, request_key, status='needs_review', thread_id=thread_id,
                         turn_id=turn_id, reason='Native turn ended without a deliverable.')
    finally:
        if client:
            try:
                client.close()
            except OSError:
                pass
