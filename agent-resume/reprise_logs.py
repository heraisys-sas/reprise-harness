"""Read-only Reprise log reader.

Parses Claude Desktop local logs to reconstruct (1) interrupted tasks that can
be resumed and (2) an explainable history of recent harness work and its
interactions through Reprise. Two evidence sources, both local and permission-
free:

- main.log: Quick Entry submission lifecycle and guards (Quick entry submitted,
  [QuickEntry] …, dropping payload, quick_entry_inactive_account).
- mcp-server-*.log: harness <-> Reprise MCP interactions (tools/call, results).

Nothing here automates the UI or reads the screen; it only reads log files.
Parsing is deterministic: no heuristics that could invent an event.
"""
from pathlib import Path
import re

DEFAULT_LOG_DIR = Path.home() / 'Library/Logs/Claude'

# --- main.log Quick Entry events -------------------------------------------
RE_SUBMITTED = re.compile(
    r'^(?P<ts>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}).*Quick entry submitted \{(?P<fields>[^}]*)\}')
RE_PROCESSING = re.compile(
    r'^(?P<ts>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}).*\[QuickEntry\] Processing quick entry payload')
RE_SENDING = re.compile(
    r'^(?P<ts>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}).*\[QuickEntry\] Sending to specific chat: (?P<chat>[0-9a-f-]+)')
RE_SUBMITTING = re.compile(
    r'^(?P<ts>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}).*\[QuickEntry\] Submitting payload to (?P<target>.+)')
RE_DROPPED = re.compile(
    r'^(?P<ts>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}).*\[QuickEntry\].*(?:dropping payload|inactive_account|Creating new chat)')
RE_SUBMITTED_TRUNCATED = re.compile(
    r'^(?P<ts>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}).*Quick entry submitted \{(?P<fields>[^}]*)$')
RE_ERROR = re.compile(
    r'^(?P<ts>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}).*\[(?:error|warn)\] \[QuickEntry\](?P<msg>.*)')

# --- mcp-server-*.log interactions -----------------------------------------
RE_MCP_METHOD = re.compile(
    r'^(?P<ts>\d{4}-\d{2}-\d{2}T[\d:.]+Z) \[(?P<server>[^\]]+)\] \[info\] Message from (?P<who>client|server): (?P<body>.*)')
RE_METHOD_NAME = re.compile(r'method="(?P<method>[a-z/]+)"')
RE_REQUEST_ID = re.compile(r'\bid=(?P<id>\d+)')


def _fields(blob):
    """Parse `key: value` pairs from a log brace block into a dict.

    R3 (review): a malformed block must not raise mid-parse nor be treated as
    valid; it is normalised to ValueError so callers fail safely per record.
    """
    if not isinstance(blob, str):
        raise ValueError('Log field block must be a string')
    out = {}
    for part in blob.split(','):
        if not part.strip():
            continue
        if ':' not in part:
            raise ValueError('Malformed log field token without separator')
        key, _, value = part.partition(':')
        key = key.strip()
        if not key:
            raise ValueError('Empty key in log field block')
        if key in out:
            raise ValueError('Duplicate key in log field block')
        value = value.strip()
        if (value.startswith("'") and not value.endswith("'")) or (value.startswith('"') and not value.endswith('"')):
            raise ValueError('Unbalanced quoting in log field block')
        out[key] = value.strip("'\"")
    return out


def parse_quick_entry(text):
    """Extract Quick Entry submissions with their routing and terminal state."""
    events = []
    for line in text.splitlines():
        m = RE_SUBMITTED.match(line)
        if m:
            try:
                fields = _fields(m.group('fields'))
            except ValueError:
                events.append({'kind': 'incomplete', 'ts': m.group('ts'),
                               'detail': 'malformed submission record'})
                continue
            # R1: a submission record is conclusive only when its identity and
            # size fields are all present; otherwise mark it incomplete so no
            # downstream logic treats a truncated record as authoritative.
            # R1 strict: complete requires all fields present AND consistent
            # (non-empty numeric length, boolean-ish image flag, non-empty chat id).
            try:
                plen = int(fields.get('promptLength', ''))
            except (TypeError, ValueError):
                plen = -1
            complete = (plen >= 0
                        and fields.get('hasImage') in ('true', 'false')
                        and bool((fields.get('chatId') or '').strip()))
            events.append({
                'kind': 'submitted', 'ts': m.group('ts'),
                'promptLength': fields.get('promptLength'),
                'hasImage': fields.get('hasImage'),
                'chatId': fields.get('chatId'),
                'complete': complete,
            })
            continue
        m = RE_SUBMITTED_TRUNCATED.match(line)
        if m:
            # R1: truncated tail record — decoded gap, never a valid submission.
            events.append({'kind': 'incomplete', 'ts': m.group('ts'),
                           'detail': 'truncated submission record'})
            continue
        m = RE_SENDING.match(line)
        if m:
            events.append({'kind': 'routing', 'ts': m.group('ts'), 'chatId': m.group('chat'),
                           'target': 'specific_chat'})
            continue
        m = RE_SUBMITTING.match(line)
        if m:
            events.append({'kind': 'routing', 'ts': m.group('ts'), 'target': m.group('target').strip()})
            continue
        m = RE_PROCESSING.match(line)
        if m:
            events.append({'kind': 'processing', 'ts': m.group('ts')})
            continue
        m = RE_DROPPED.match(line)
        if m:
            events.append({'kind': 'guard', 'ts': m.group('ts'), 'detail': m.group(0).split('[QuickEntry]')[-1].strip()})
            continue
        m = RE_ERROR.match(line)
        if m:
            events.append({'kind': 'error', 'ts': m.group('ts'), 'detail': m.group('msg').strip()})
    return events


def parse_interactions(text):
    """Extract harness <-> Reprise MCP interactions (tools/call and results)."""
    events = []
    for line in text.splitlines():
        m = RE_MCP_METHOD.match(line)
        if not m:
            continue
        body = m.group('body')
        method = RE_METHOD_NAME.search(body)
        request_id = RE_REQUEST_ID.search(body)
        events.append({
            'kind': 'mcp', 'ts': m.group('ts'), 'server': m.group('server'),
            'who': m.group('who'), 'method': method.group('method') if method else None,
            'request_id': request_id.group('id') if request_id else None,
        })
    return events


def read_main(log_dir=DEFAULT_LOG_DIR):
    path = Path(log_dir) / 'main.log'
    return parse_quick_entry(path.read_text(errors='replace')) if path.is_file() else []


def read_interactions(log_dir=DEFAULT_LOG_DIR):
    events = []
    for path in sorted(Path(log_dir).glob('mcp-server-*.log')):
        events.extend(parse_interactions(path.read_text(errors='replace')))
    return events


# --- Reprise: reconstruct resumable work ------------------------------------
def _group_submissions(events):
    """Group Quick Entry events into submissions, correlated by chat identity.

    R2 (review): each step is attached only to the submission it belongs to.
    A routing step confirms dispatch only when it targets that submission's
    chat; an unrelated guard/error must not flip an already-dispatched
    submission back to a retryable state.
    """
    submissions, current = [], None
    for e in events:
        if e['kind'] == 'submitted':
            chat = (e.get('chatId') or '').strip()
            current = {'ts': e['ts'], 'chatId': e.get('chatId'), 'promptLength': e.get('promptLength'),
                       'hasImage': e.get('hasImage'), 'steps': [e], 'terminal': 'submitted',
                       'complete': bool(e.get('complete')),
                       'identified': bool(chat), 'chat': chat}
            submissions.append(current)
            continue
        if current is None:
            continue
        chat = current.get('chat')
        ev_chat = (e.get('chatId') or '').strip()
        # A step explicitly bound to a different chat is unrelated: ignore it.
        if ev_chat and chat and ev_chat != chat:
            continue
        current['steps'].append(e)
        if e['kind'] == 'routing':
            target = (e.get('target') or '')
            if target == 'specific_chat' and (not ev_chat or ev_chat == chat):
                current['terminal'] = 'dispatched'
        elif e['kind'] == 'guard' and current['terminal'] != 'dispatched':
            current['terminal'] = 'guarded'
        elif e['kind'] == 'error' and current['terminal'] != 'dispatched':
            current['terminal'] = 'error'
    return submissions


def resumable(log_dir=DEFAULT_LOG_DIR):
    """Return submissions that look interrupted and can be retried.

    A submission is resumable when it reached a terminal 'submitted' state but
    shows no successful routing step (dispatched), or hit a guard/error. This
    is evidence-based: no assumption that a turn actually ran.
    """
    out = []
    for s in _group_submissions(read_main(log_dir)):
        # R2: only a complete, identified submission may be called resumable;
        # a truncated or unidentified record yields 'uncertain', never a retry.
        if not s.get('complete') or not s.get('identified'):
            out.append({'ts': s['ts'], 'chatId': s['chatId'], 'state': 'uncertain',
                        'reason': 'Incomplete or unidentified submission record; not safe to resume'})
            continue
        if s['terminal'] in ('guarded', 'error') or (s['terminal'] == 'submitted' and
                                                     not any(st['kind'] == 'routing' and st.get('target', '').startswith('specific') for st in s['steps'])):
            out.append({'ts': s['ts'], 'chatId': s['chatId'], 'promptLength': s['promptLength'],
                        'hasImage': s['hasImage'], 'state': s['terminal'],
                        'reason': 'No confirmed dispatch to a target chat' if s['terminal'] == 'submitted'
                                  else f"Terminal state: {s['terminal']}"})
    return out


# --- History: explain recent work and interactions -------------------------
def history(log_dir=DEFAULT_LOG_DIR, limit=20):
    """Explain recent harness work: Quick Entry submissions + MCP interactions."""
    submissions = [{'ts': s['ts'], 'chatId': s['chatId'], 'state': s['terminal'],
                    'promptLength': s['promptLength']} for s in _group_submissions(read_main(log_dir))]
    interactions = [{'ts': e['ts'], 'server': e['server'], 'who': e['who'],
                     'method': e['method'], 'request_id': e['request_id']}
                    for e in read_interactions(log_dir) if e['method']]
    timeline = sorted(submissions + interactions, key=lambda x: x['ts'])
    return {'submissions': submissions[-limit:], 'interactions': interactions[-limit:],
            'timeline': timeline[-limit:],
            'note': 'Log-derived; a submission state is not proof a model turn completed.'}


# --- Active conversations (pense-bête multi-sujets) -------------------------
def active_conversations(log_dir=DEFAULT_LOG_DIR, limit=10):
    """Aggregate distinct active conversations across rotated main*.log files.

    Returns one entry per chatId with its first/last seen timestamps and an
    activity count, so the local synthesis can act as a multi-subject recap.
    Evidence-based: only conversations actually present in the logs.
    """
    from collections import OrderedDict
    import glob
    per_chat = {}
    for path in sorted(glob.glob(str(Path(log_dir) / 'main*.log'))):
        try:
            text = Path(path).read_text(errors='replace')
        except OSError:
            continue
        for e in parse_quick_entry(text):
            chat = (e.get('chatId') or '').strip()
            if e['kind'] == 'submitted' and chat:
                rec = per_chat.setdefault(chat, {'chatId': chat, 'first': e['ts'], 'last': e['ts'],
                                                 'submissions': 0, 'states': []})
                rec['last'] = max(rec['last'], e['ts'])
                rec['first'] = min(rec['first'], e['ts'])
                rec['submissions'] += 1
                rec['states'].append(e.get('complete'))
    ordered = sorted(per_chat.values(), key=lambda r: r['last'], reverse=True)
    return ordered[:limit]
