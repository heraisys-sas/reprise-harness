"""Deterministic clock and scripted Codex client for delegation timing tests."""
from collections import deque
from pathlib import Path


class FakeClock:
    """Monotonic clock advanced explicitly by a test or by an idle scripted client."""

    def __init__(self, start=0.0):
        self.value = float(start)

    def now(self):
        return self.value

    __call__ = now

    def advance(self, seconds):
        self.value += float(seconds)
        return self.value


class ScriptedClient:
    """Client substitute: scripted RPC replies, queued notifications and a timed idle receive.

    `receive` advances the injected clock when no notification is queued, so a wait budget
    expires deterministically instead of sleeping.
    """

    def __init__(self, clock, root=None, thread_id=None, replies=None, notifications=None,
                 turn_notifications=None, idle_step=None, turns=None, thread_status='idle'):
        self.clock = clock
        self.root = None if root is None else str(root)
        self.thread_id = thread_id
        self.replies = dict(replies or {})
        self.notifications = deque(notifications or [])
        self.turn_notifications = list(turn_notifications or [])
        self.turns = turns
        self.thread_status = thread_status
        self.idle_step = idle_step
        self.calls = []
        self.sent = []
        self.closed = False

    def send(self, message):
        self.sent.append(message)

    def close(self):
        self.closed = True

    def receive(self, timeout=None):
        step = 1 if timeout is None else timeout
        if self.idle_step is not None:
            step = min(step, self.idle_step)
        self.clock.advance(step)
        raise TimeoutError('scripted idle client')

    def call(self, method, params=None):
        params = params or {}
        self.calls.append((method, params))
        if method == 'turn/start':
            self.notifications.extend(self.turn_notifications)
        reply = self.replies.get(method)
        if callable(reply):
            return reply(params)
        if reply is not None:
            return reply
        if method in ('initialize', 'initialized', 'config/read'):
            return {} if method != 'config/read' else {'config': {'mcp_servers': {}}}
        return {}

    def methods(self):
        return [method for method, _ in self.calls]


class ScriptedFactory:
    """Patch replacement for `Client`; keeps created instances reachable by the test."""

    def __init__(self, clock, **kwargs):
        self.clock, self.kwargs, self.instances = clock, kwargs, []

    def __call__(self, *args, **kwargs):
        merged = dict(self.kwargs)
        merged.update(kwargs)
        client = ScriptedClient(self.clock, **merged)
        self.instances.append(client)
        return client

    @property
    def last(self):
        return self.instances[-1]


def thread_reply(root, thread_id, status='idle', turns=None, ephemeral=False, provider='openai',
                 cwd=None):
    thread = {'id': thread_id, 'cwd': str(cwd or root), 'modelProvider': provider,
              'path': str(Path(root) / 'rollout.jsonl'), 'ephemeral': ephemeral,
              'status': {'type': status}}
    if turns is not None:
        thread['turns'] = turns
    return {'thread': thread}


def review_replies(root, thread_id, turn_id='own', thread_status='idle', turns=None):
    """RPC replies accepted by `codex_expert.review` preflight and turn start."""
    return {
        'account/read': {'account': {'type': 'chatgpt'}},
        'account/rateLimits/read': {'ordinaryUsageAllowed': True,
                                    'rateLimits': {'primary': {'usedPercent': 30}}},
        'config/read': {'config': {'mcp_servers': {}}},
        'mcpServerStatus/list': {'data': []},
        'thread/start': thread_reply(root, thread_id),
        'thread/read': thread_reply(root, thread_id, status=thread_status, turns=turns),
        'thread/resume': thread_reply(root, thread_id, status='idle', turns=turns),
        'turn/start': {'turn': {'id': turn_id}},
    }


def turn_payload(turn_id, status='inProgress', items=None):
    turn = {'id': turn_id, 'status': status}
    if items is not None:
        turn['items'] = items
    return turn
