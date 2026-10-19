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
RE_ERROR = re.compile(
    r'^(?P<ts>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}).*\[(?:error|warn)\] \[QuickEntry\](?P<msg>.*)')

# --- mcp-server-*.log interactions -----------------------------------------
RE_MCP_METHOD = re.compile(
    r'^(?P<ts>\d{4}-\d{2}-\d{2}T[\d:.]+Z) \[(?P<server>[^\]]+)\] \[info\] Message from (?P<who>client|server): (?P<body>.*)')
RE_METHOD_NAME = re.compile(r'method="(?P<method>[a-z/]+)"')
RE_REQUEST_ID = re.compile(r'\bid=(?P<id>\d+)')


def _fields(blob):
    """Parse `key: value` pairs from a log brace block into a dict."""
    out = {}
    for part in blob.split(','):
        if ':' not in part:
            continue
        key, _, value = part.partition(':')
        out[key.strip()] = value.strip().strip("'\"")
    return out


def parse_quick_entry(text):
    """Extract Quick Entry submissions with their routing and terminal state."""
    events = []
    for line in text.splitlines():
        m = RE_SUBMITTED.match(line)
        if m:
            fields = _fields(m.group('fields'))
            events.append({
                'kind': 'submitted', 'ts': m.group('ts'),
                'promptLength': fields.get('promptLength'),
                'hasImage': fields.get('hasImage'),
                'chatId': fields.get('chatId'),
            })
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
    """Group Quick Entry events into submissions keyed by chatId + timestamp."""
    submissions, current = [], None
    for e in events:
        if e['kind'] == 'submitted':
            current = {'ts': e['ts'], 'chatId': e.get('chatId'), 'promptLength': e.get('promptLength'),
                       'hasImage': e.get('hasImage'), 'steps': [e], 'terminal': 'submitted'}
            submissions.append(current)
        elif current is not None:
            current['steps'].append(e)
            if e['kind'] == 'routing':
                current['terminal'] = 'dispatched'
            elif e['kind'] == 'guard':
                current['terminal'] = 'guarded'
            elif e['kind'] == 'error':
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
