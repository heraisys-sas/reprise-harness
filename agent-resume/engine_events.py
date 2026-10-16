"""Pure evidence parser for installed Codex and Harness CLI event streams.

No action is executed or retried. A final text or exit code alone is not proof
of completion. Reasons contain no provider messages, prompts, or credentials.
Harness headless does not expose an interactive approvals protocol.
"""


REASONS = {
    'finished': 'Fin du tour confirmée ; résultat métier non vérifié.',
    'needs_review': 'Aucune fin de tour fiable confirmée ; examen manuel requis.',
    'failed': 'Le moteur a signalé un échec ; consulter son journal local.',
    'timed_out': 'Délai maximal atteint ; aucune nouvelle tentative automatique.',
    'quota_blocked': 'Quota ou solde refusé par le fournisseur.',
    'blocked_auth': 'Authentification du fournisseur requise dans le harness.',
    'blocked_owner': 'Cette session est déjà détenue par un autre processus.',
    'blocked_session': 'Identité de session différente de celle demandée.',
    'blocked_permission': 'Une autorisation nécessaire est indisponible ou refusée.',
    'blocked_trust': 'Le moteur refuse la confiance du dossier.',
    'needs_input': 'Une réponse humaine est nécessaire ; aucune réponse automatique.',
    'stopped': 'Tour interrompu ou arrêté ; aucune nouvelle tentative automatique.',
}


def _mapping(value):
    return value if isinstance(value, dict) else {}


def _failure(value):
    """Read only a protocol error envelope, never assistant/tool output text."""
    error = _mapping(value)
    code = error.get('code')
    info = error.get('codex_error_info')
    if isinstance(info, str):
        code = info
    elif isinstance(info, dict) and 'usage_limit_exceeded' in info:
        code = 'usage_limit_exceeded'
    if code in ('QUOTA', 'ACCOUNT_QUOTA', 'usage_limit_exceeded'):
        return 'quota_blocked'
    if code in ('MISSING_CREDENTIAL', 'INVALID_CREDENTIAL', 'ACCOUNT_SIGN_IN_REQUIRED',
                'ACCOUNT_TOKEN_INVALID', 'UNAUTHORIZED', 'AUTHENTICATION_REQUIRED'):
        return 'blocked_auth'
    if code == 'NO_PROVIDER':
        return 'needs_input'
    message = error.get('message', value if isinstance(value, str) else '')
    message = message.lower() if isinstance(message, str) else ''
    if any(part in message for part in ('already owned by an active write handle', 'is live in this process', 'already has an active writer', 'sessionalreadyowned')):
        return 'blocked_owner'
    if 'not inside a trusted directory' in message:
        return 'blocked_trust'
    if 'usage_limit_exceeded' in message or 'hit your usage limit' in message:
        return 'quota_blocked'
    if any(fragment in message for fragment in ('not logged in', 'authentication required',
                                               'unauthorized', 'no api key for provider route')):
        return 'blocked_auth'
    if 'no user-questions answerer accepted' in message:
        return 'needs_input'
    return 'failed'


class ExecutionEvidence:
    """Evidence for one requested session and invocation; unknown engines fail closed."""

    def __init__(self, engine, session):
        self.engine = engine
        self.session = session
        self.family = ('codex' if engine in ('codex', 'codex-cli', 'codex-server') else
                       'harness' if engine in ('harness', 'harness-cli', 'deepseek-harness') else None)
        self.identity_seen = False
        self.started = False
        self.completed = False
        self.turn = None
        self.problem = None

    def _block(self, state):
        # Identity violations dominate every other outcome; other failures stick.
        if self.problem is None or state == 'blocked_session':
            self.problem = state
        self.completed = False
        return self.problem

    def _identity(self, actual):
        if actual != self.session or not isinstance(actual, str) or not actual:
            return self._block('blocked_session')
        self.identity_seen = True
        return None

    def feed(self, event):
        if not isinstance(event, dict) or self.family is None:
            return None
        kind = event.get('type')
        if self.family == 'codex':
            if kind == 'thread.started':
                return self._identity(event.get('thread_id'))
            if kind in ('error', 'turn.failed'):
                return self._block(_failure(event.get('error', event)))
            if self.problem:
                return None
            if kind == 'turn.started':
                self.started, self.completed = True, False
                return 'running'
            if kind == 'turn.completed' and self.started:
                self.completed = True
            return None

        if kind == 'session':
            return self._identity(event.get('sessionId'))
        if kind == 'error':
            return self._block(_failure(event))
        if self.problem:
            return None
        # The installed headless projection announces the exact session first.
        if not self.identity_seen:
            return None
        if kind == 'tool_call' and event.get('tool') == 'ask_user_question':
            return self._block('needs_input')
        if kind != 'status':
            return None
        phase = event.get('phase')
        if phase == 'turn_start':
            self.started, self.completed = True, False
            self.turn = event.get('turn')
            return 'running'
        if phase != 'turn_end' or not self.started:
            return None
        if event.get('turn') != self.turn:
            return self._block('needs_review')
        reason = _mapping(event.get('reason'))
        outcome = reason.get('kind')
        if outcome == 'completed':
            self.completed = True
        elif outcome == 'error':
            return self._block(_failure(reason.get('error')))
        elif outcome in ('stopped', 'interrupted', 'aborted', 'cancelled'):
            return self._block('stopped')
        else:
            return self._block('needs_review')
        return None

    def finish(self, exitcode, timedout=False):
        if self.problem:
            state = self.problem
        elif timedout:
            state = 'timed_out'
        elif self.family and exitcode == 0 and self.started and self.completed:
            state = 'finished'
        elif exitcode not in (0, None):
            state = 'failed'
        else:
            state = 'needs_review'
        return state, REASONS[state]


def failure(value):
    state = _failure(value)
    return state, REASONS[state]
