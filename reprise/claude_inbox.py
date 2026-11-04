"""Read and acknowledge results from an already-running, project-bound Quick Apply socket.

This module never starts Claude Desktop or a daemon. Callers own the client lifetime
and must only call ``acknowledge_result`` after presenting a fully collected result.
"""
import argparse
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re


MAX_PAGE_CHARS = 6000
MAX_ANSWER_CHARS = 2_000_000
# json.dumps may expand each control character to a six-character ``\\u00XX`` escape.
# Include the canonical JSON object's fixed keys, quotes, braces, and separators.
MAX_RESULT_CHARS = 6 * MAX_ANSWER_CHARS + 32
MAX_TASKS = 10_000
CONNECTOR_NAME = 'reprise-harness'


class InboxError(Exception):
    """Expected collection failure with a safe, user-displayable message."""

    def __init__(self, code, message):
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class CollectedResult:
    request_key: str
    answer: str
    canonical_result: str
    result_sha256: str
    total_length: int
    acknowledged: bool


def _canonical_directory(value, label):
    try:
        path = Path(value).expanduser().resolve(strict=True)
        if not path.is_dir():
            raise ValueError
    except (OSError, TypeError, ValueError, RuntimeError) as error:
        raise InboxError('invalid_scope', f'{label} introuvable ou invalide.') from error
    return str(path)


def read_managed_manifest(path):
    """Read the managed connector manifest; no configuration is changed."""
    try:
        value = json.loads(Path(path).read_text(encoding='utf-8'))
    except (OSError, UnicodeError, json.JSONDecodeError, TypeError) as error:
        raise InboxError('manifest_unavailable', 'Manifeste géré du connecteur indisponible ou invalide.') from error
    if not isinstance(value, dict):
        raise InboxError('manifest_invalid', 'Manifeste géré du connecteur invalide.')
    return value


def verify_binding(project, manifest, capabilities):
    """Require an installed manifest and matching live project and state identity."""
    if not isinstance(manifest, dict) or manifest.get('format') != 1 or \
            manifest.get('status') != 'installed' or manifest.get('connector_name') != CONNECTOR_NAME:
        raise InboxError('manifest_mismatch', 'Le connecteur géré ne confirme pas une installation active.')
    selected = _canonical_directory(project, 'Projet sélectionné')
    managed_project = _canonical_directory(manifest.get('project'), 'Projet du manifeste')
    if selected != managed_project:
        raise InboxError('project_mismatch', 'Le projet choisi ne correspond pas au projet autorisé du connecteur.')
    quick_state = _canonical_directory(manifest.get('quick_state'), 'État Quick Apply du manifeste')
    if not isinstance(capabilities, dict) or capabilities.get('task_return_supported') is not True:
        raise InboxError('unsupported', 'Le socket existant ne déclare pas la collecte de tâches prise en charge.')
    observed_project = capabilities.get('project_root')
    if not isinstance(observed_project, str) or _canonical_directory(observed_project, 'Projet annoncé par le socket') != selected:
        raise InboxError('project_mismatch', 'Le socket existant est lié à un autre projet.')
    expected_state_sha256 = hashlib.sha256(quick_state.encode('utf-8')).hexdigest()
    if capabilities.get('state_sha256') != expected_state_sha256:
        raise InboxError('state_mismatch', 'L’empreinte d’état du socket ne correspond pas au manifeste géré.')
    return {'project': selected, 'quick_state': quick_state,
            'state_sha256': expected_state_sha256}


def connect_existing(socket_path, client_factory=None, timeout=2):
    """Join only the supplied socket. The factory is injectable for tests."""
    if client_factory is None:
        from claude_desktop_rpc import Client
        client_factory = Client
    try:
        return client_factory(None, {'socket': str(socket_path)}, timeout=timeout)
    except Exception as error:
        raise InboxError('unavailable', 'Socket Quick Apply indisponible ; aucun service n’a été démarré.') from error


def _call(client, method, arguments):
    try:
        result = client.call(method, arguments)
    except Exception as error:
        raise InboxError('unavailable', f'Le socket existant a refusé ou interrompu la requête {method}.') from error
    if not isinstance(result, dict):
        raise InboxError('invalid_response', f'Réponse invalide pour {method}.')
    return result


def _bound_client(client, project, manifest):
    capabilities = _call(client, 'quick_entry/capabilities', {})
    return verify_binding(project, manifest, capabilities)


def list_tasks(client, project, manifest, page_size=50, max_tasks=MAX_TASKS):
    """Return a sanitized, fully paginated task list for the verified project."""
    _bound_client(client, project, manifest)
    if isinstance(page_size, bool) or not isinstance(page_size, int) or not 1 <= page_size <= 100:
        raise InboxError('invalid_limit', 'La taille de page doit être comprise entre 1 et 100.')
    if isinstance(max_tasks, bool) or not isinstance(max_tasks, int) or not 1 <= max_tasks <= MAX_TASKS:
        raise InboxError('size_limit', 'La limite locale de tâches est invalide.')
    cursor = None
    seen_cursors = set()
    items = []
    high_water = None
    while True:
        args = {'limit': page_size}
        if cursor is not None:
            args['cursor'] = cursor
        page = _call(client, 'quick_entry/list_tasks', args)
        tasks = page.get('tasks')
        if not isinstance(tasks, list):
            raise InboxError('invalid_response', 'La liste de tâches est mal formée.')
        page_high_water = page.get('high_water')
        if isinstance(page_high_water, bool) or not isinstance(page_high_water, int) or page_high_water < 0:
            raise InboxError('invalid_response', 'Le repère de pagination est invalide.')
        if high_water is None:
            high_water = page_high_water
        elif page_high_water != high_water:
            raise InboxError('result_changed', 'La liste a changé pendant sa pagination ; actualisez-la.')
        for task in tasks:
            if not isinstance(task, dict):
                raise InboxError('invalid_response', 'Une entrée de tâche est mal formée.')
            key = task.get('request_key')
            state = task.get('state')
            available = task.get('result_available')
            acknowledged = task.get('acknowledged')
            if not isinstance(key, str) or not key or len(key) > 128 or \
                    not isinstance(state, str) or type(available) is not bool or type(acknowledged) is not bool:
                raise InboxError('invalid_response', 'Les métadonnées d’une tâche sont invalides.')
            # Whitelist only UI-safe fields; never pass through claim_key, prompt, or arbitrary server data.
            items.append({'request_key': key, 'state': state,
                          'result_available': available, 'acknowledged': acknowledged})
            if len(items) > max_tasks:
                raise InboxError('size_limit', 'La liste dépasse la limite locale de collecte.')
        next_cursor = page.get('next_cursor')
        if next_cursor is None:
            break
        if not tasks:
            raise InboxError('invalid_response', 'Une page vide ne peut pas annoncer un curseur suivant.')
        if not isinstance(next_cursor, str) or not next_cursor or next_cursor in seen_cursors:
            raise InboxError('invalid_response', 'Le curseur de pagination est invalide ou répété.')
        seen_cursors.add(next_cursor)
        cursor = next_cursor
    return {'project': _canonical_directory(project, 'Projet sélectionné'),
            'high_water': high_water, 'tasks': items}


def _validate_receipt(receipt, request_key):
    if receipt.get('status') == 'result_changed':
        raise InboxError('result_changed', 'Le résultat a changé ; recommencez sa collecte.')
    if receipt.get('request_key') != request_key or receipt.get('available') is not True:
        raise InboxError('not_ready', 'Aucun résultat complet n’est disponible pour cette tâche.')
    total = receipt.get('total_length')
    sha = receipt.get('result_sha256')
    if isinstance(total, bool) or not isinstance(total, int) or total < 0:
        raise InboxError('invalid_response', 'La longueur annoncée du résultat est invalide.')
    if total > MAX_RESULT_CHARS:
        raise InboxError('size_limit', 'Le résultat dépasse la limite locale de collecte.')
    if not isinstance(sha, str) or re.fullmatch(r'[0-9a-fA-F]{64}', sha) is None:
        raise InboxError('invalid_response', 'L’empreinte SHA-256 du résultat est invalide.')
    return total, sha.lower()


def get_result(client, project, manifest, request_key, page_chars=MAX_PAGE_CHARS):
    """Collect every page of one result and verify its SHA, offsets, and length."""
    _bound_client(client, project, manifest)
    if not isinstance(request_key, str) or not request_key or len(request_key) > 128:
        raise InboxError('invalid_request', 'Identifiant de tâche invalide.')
    if isinstance(page_chars, bool) or not isinstance(page_chars, int) or not 1 <= page_chars <= MAX_PAGE_CHARS:
        raise InboxError('invalid_limit', 'La taille de page de résultat est invalide.')
    receipt = _call(client, 'quick_entry/get_result', {'request_key': request_key, 'view': 'receipt'})
    total, sha = _validate_receipt(receipt, request_key)
    offset = 0
    fragments = []
    while offset < total:
        page = _call(client, 'quick_entry/get_result', {
            'request_key': request_key, 'view': 'page', 'offset': offset,
            'max_chars': page_chars, 'result_sha256': sha})
        if page.get('status') in ('result_changed', 'invalid_offset'):
            raise InboxError('result_changed', 'Le résultat a changé ou sa pagination est invalide ; recommencez.')
        page_total, page_sha = _validate_receipt(page, request_key)
        page_offset = page.get('offset')
        if page_total != total or page_sha != sha or isinstance(page_offset, bool) or \
                not isinstance(page_offset, int) or page_offset != offset:
            raise InboxError('result_changed', 'Les pages ne correspondent pas au reçu initial.')
        fragment = page.get('text')
        length = page.get('returned_length')
        if not isinstance(fragment, str) or isinstance(length, bool) or not isinstance(length, int) or length != len(fragment):
            raise InboxError('invalid_response', 'La longueur d’une page ne correspond pas à son contenu.')
        next_offset = offset + length
        has_more = page.get('has_more')
        expected_more = next_offset < total
        page_next_offset = page.get('next_offset')
        next_offset_valid = (isinstance(page_next_offset, int) and not isinstance(page_next_offset, bool)
                             and page_next_offset == next_offset) if expected_more else page_next_offset is None
        if type(has_more) is not bool or has_more != expected_more or not next_offset_valid or \
                (expected_more and length == 0):
            raise InboxError('invalid_response', 'La pagination du résultat est incohérente.')
        fragments.append(fragment)
        offset = next_offset
    canonical_result = ''.join(fragments)
    if len(canonical_result) != total or hashlib.sha256(canonical_result.encode('utf-8')).hexdigest() != sha:
        raise InboxError('result_changed', 'L’empreinte ou la longueur du résultat collecté ne correspond pas au reçu.')
    try:
        decoded = json.loads(canonical_result)
    except (json.JSONDecodeError, UnicodeError) as error:
        raise InboxError('invalid_response', 'Le résultat collecté n’est pas un JSON valide.') from error
    if not isinstance(decoded, dict) or set(decoded) != {'answer'} or not isinstance(decoded.get('answer'), str):
        raise InboxError('invalid_response', 'Le résultat ne contient pas une réponse affichable valide.')
    answer = decoded['answer']
    canonical_check = json.dumps({'answer': answer}, sort_keys=True, separators=(',', ':'), ensure_ascii=False)
    if canonical_check != canonical_result or len(answer) > MAX_ANSWER_CHARS:
        raise InboxError('invalid_response', 'La réponse dépasse le contrat ou sa représentation canonique est invalide.')
    return CollectedResult(request_key, answer, canonical_result, sha, total,
                           bool(receipt.get('acknowledged', False)))


def acknowledge_result(client, project, manifest, collected, explicit=False):
    """Acknowledge only an explicitly requested, fully validated result."""
    if not isinstance(collected, CollectedResult):
        raise InboxError('invalid_result', 'Un résultat collecté et vérifié est requis avant acquittement.')
    if len(collected.canonical_result) != collected.total_length or \
            hashlib.sha256(collected.canonical_result.encode('utf-8')).hexdigest() != collected.result_sha256:
        raise InboxError('invalid_result', 'Le résultat fourni ne correspond plus à son empreinte validée.')
    try:
        decoded = json.loads(collected.canonical_result)
    except (json.JSONDecodeError, UnicodeError) as error:
        raise InboxError('invalid_result', 'La représentation canonique du résultat est invalide.') from error
    if not isinstance(decoded, dict) or set(decoded) != {'answer'} or decoded.get('answer') != collected.answer:
        raise InboxError('invalid_result', 'La réponse affichable ne correspond pas au résultat vérifié.')
    return acknowledge_identity(client, project, manifest, collected.request_key,
                                collected.result_sha256, collected.total_length, explicit)


def acknowledge_identity(client, project, manifest, request_key, result_sha256,
                         total_length, explicit=False):
    """Recheck the displayed result identity, then acknowledge on explicit request."""
    _bound_client(client, project, manifest)
    if explicit is not True:
        raise InboxError('confirmation_required', 'Acquittement explicite requis après consultation du résultat.')
    if not isinstance(request_key, str) or not request_key or len(request_key) > 128 or \
            not isinstance(result_sha256, str) or re.fullmatch(r'[0-9a-fA-F]{64}', result_sha256) is None or \
            isinstance(total_length, bool) or not isinstance(total_length, int) or total_length < 0:
        raise InboxError('invalid_result', 'Identité SHA et longueur valides requises pour l’acquittement.')
    current = _call(client, 'quick_entry/get_result', {'request_key': request_key, 'view': 'receipt'})
    total, sha = _validate_receipt(current, request_key)
    if total != total_length or sha != result_sha256.lower():
        raise InboxError('result_changed', 'Le résultat actuel diffère de celui consulté ; rien n’a été acquitté.')
    # The backend keeps submitted results immutable. This explicit call is the only write in this module.
    acknowledged = _call(client, 'quick_entry/get_result', {
        'request_key': request_key, 'view': 'receipt', 'acknowledge': True})
    ack_total, ack_sha = _validate_receipt(acknowledged, request_key)
    if ack_total != total or ack_sha != sha or acknowledged.get('acknowledged') is not True:
        raise InboxError('result_changed', 'La réponse d’acquittement ne confirme pas le résultat consulté.')
    return {'request_key': request_key, 'result_sha256': sha,
            'total_length': total, 'acknowledged': True}


def run_action(action, project, request_key=None, expected_sha256=None,
               expected_length=None, manifest_path=None, socket_path=None,
               client_factory=None, explicit=False):
    """Run one CLI-facing operation, joining only the already configured socket."""
    if action not in ('list', 'read', 'ack'):
        raise InboxError('invalid_request', 'Opération de collecte inconnue.')
    if manifest_path is None:
        manifest_path = Path.home() / 'Library/Application Support/Reprise/claude-integration/managed.json'
    if socket_path is None:
        from claude_desktop import DEFAULT_SOCKET
        socket_path = DEFAULT_SOCKET
    manifest = read_managed_manifest(manifest_path)
    client = connect_existing(socket_path, client_factory=client_factory, timeout=10)
    try:
        if action == 'list':
            return {'status': 'ok', **list_tasks(client, project, manifest)}
        if not isinstance(request_key, str) or not request_key:
            raise InboxError('invalid_request', 'Identifiant de tâche requis.')
        if action == 'read':
            collected = get_result(client, project, manifest, request_key)
            return {'status': 'ok', 'project': _canonical_directory(project, 'Projet sélectionné'),
                    'request_key': collected.request_key, 'answer': collected.answer,
                    'result_sha256': collected.result_sha256, 'total_length': collected.total_length,
                    'acknowledged': collected.acknowledged}
        if not isinstance(expected_sha256, str) or re.fullmatch(r'[0-9a-fA-F]{64}', expected_sha256) is None or \
                isinstance(expected_length, bool) or not isinstance(expected_length, int) or expected_length < 0:
            raise InboxError('invalid_request', 'Identité SHA et longueur lues requises avant acquittement.')
        return {'status': 'ok', **acknowledge_identity(client, project, manifest, request_key,
                                                        expected_sha256, expected_length, explicit=explicit)}
    finally:
        client.close()


def main(root=None, argv=None):
    parser = argparse.ArgumentParser(description='Collecte explicite des résultats Quick Apply existants.')
    parser.add_argument('operation', choices=('list', 'read', 'ack'))
    parser.add_argument('--project', required=True)
    parser.add_argument('--request-key')
    parser.add_argument('--result-sha256')
    parser.add_argument('--total-length', type=int)
    parser.add_argument('--confirm-viewed', action='store_true',
                        help='Confirmer que le résultat complet a été affiché et consulté.')
    args = parser.parse_args(argv)
    try:
        result = run_action(args.operation, args.project, args.request_key,
                            args.result_sha256, args.total_length,
                            explicit=args.confirm_viewed)
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except InboxError as error:
        print(json.dumps({'status': error.code, 'message': error.message}, ensure_ascii=False))
        return 1
