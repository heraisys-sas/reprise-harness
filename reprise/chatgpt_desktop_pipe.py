#!/usr/bin/env python3
"""Owned CDP pipe probe and guarded one-way ChatGPT Desktop send.

The official ChatGPT.app binary is launched as an owned child with its own
persistent profile. No Codex CLI, operator app, TCP DevTools port, or
implicit provider fallback. Sending requires a unique target and durable
receipt; it is NOT proof of business completion or an autonomous backend.
"""
import argparse
import hashlib
import uuid
import json
import os
from pathlib import Path
import re
import stat
import sys
import time

from claude_cdp_pipe import launch_cdp_process, terminate_chrome

DEFAULT_BINARY = Path('/Applications/ChatGPT.app/Contents/MacOS/ChatGPT')
DEFAULT_PROFILE_ROOT = Path.home() / 'Library/Application Support/Reprise/chatgpt-cdp-profiles'
PROFILE_NAME = re.compile(r'[A-Za-z0-9_-]{1,64}\Z')
MAIN_URL = 'app://-/index.html'
# Fixed, read-only probe. Never capture conversation text or authorization tokens.
RENDERER_PROBE = """(() => ({
    href: location.href,
    ready: document.readyState,
    composer_count: [...document.querySelectorAll('textarea, [contenteditable="true"], [role="textbox"]')]
      .filter(e => { const r=e.getBoundingClientRect();
                    return r.width >= 80 && r.height >= 12 && !e.disabled && !e.readOnly; }).length
}))()"""


def profile_path(root, name):
    """Resolve a named isolated profile without following existing symlinks."""
    if not isinstance(name, str) or not PROFILE_NAME.fullmatch(name):
        raise ValueError('Nom de profil CDP invalide.')
    root = Path(root).expanduser().absolute()
    if root.is_symlink() or (root / name).is_symlink():
        raise ValueError('Profil ou racine symbolique refusé.')
    return root / name


def _private_directory(path):
    path = Path(path)
    if path.is_symlink():
        raise ValueError('Dossier symbolique refusé.')
    if not path.exists():
        path.mkdir(mode=0o700, parents=True, exist_ok=False)
    info = path.stat(follow_symlinks=False)
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or (stat.S_IMODE(info.st_mode) & 0o077):
        raise ValueError('Dossier de profil non privé ou non détenu par cet utilisateur.')


def _claim_lock(root, name):
    """Never adopt another process or auto-remove a potentially stale lock."""
    lock = root / ('.' + name + '.cdp-pipe.lock')
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, 'O_NOFOLLOW'):
        flags |= os.O_NOFOLLOW
    fd = os.open(lock, flags, 0o600)
    return lock, fd


def _release_lock(lock, fd):
    try:
        owned_inode = os.fstat(fd).st_ino
    except OSError:
        owned_inode = None
    try:
        os.close(fd)
    except OSError:
        pass
    if owned_inode is not None:
        try:
            if lock.lstat().st_ino == owned_inode and not lock.is_symlink():
                lock.unlink()
        except FileNotFoundError:
            pass


def build_args(profile):
    return ['--remote-debugging-pipe', f'--user-data-dir={profile}',
            '--no-first-run']


def select_main(targets):
    """Require exactly one main renderer; do not fall back to arbitrary pages."""
    if not isinstance(targets, (list, tuple)):
        raise ValueError('Inventaire CDP invalide.')
    matches = [item for item in targets if isinstance(item, dict) and
               item.get('type') == 'page' and item.get('url') == MAIN_URL]
    if len(matches) != 1:
        return None, 'target_missing' if not matches else 'target_ambiguous'
    ident = matches[0].get('targetId')
    if not isinstance(ident, str) or not ident:
        return None, 'target_unidentified'
    return ident, 'ready'


def census(transport):
    """Attach to the main app renderer and inspect DOM *without* reading chat."""
    info = transport.call('Target.getTargets', {}).get('targetInfos')
    ident, stage = select_main(info)
    if stage != 'ready':
        return {'status': stage, 'transport': 'cdp-pipe', 'ready': False}
    transport.attach_to(ident)
    result = transport.evaluate(RENDERER_PROBE)
    if not isinstance(result, dict) or result.get('href') != MAIN_URL:
        return {'status': 'target_changed', 'transport': 'cdp-pipe', 'ready': False}
    count = result.get('composer_count')
    ready = type(count) is int and count == 1
    return {'status': 'ready' if ready else 'selectors_stale',
            'transport': 'cdp-pipe', 'ready': ready,
            'composer_count': count if type(count) is int else None,
            'document_ready': result.get('ready')}


def inspect(binary=DEFAULT_BINARY, profile_root=DEFAULT_PROFILE_ROOT, name='default'):
    profile = profile_path(profile_root, name)
    binary = Path(binary).expanduser().absolute()
    lock = profile.parent / ('.' + name + '.cdp-pipe.lock')
    return {'status': 'inspect_only', 'transport': 'cdp-pipe',
            'binary_present': binary.is_file() and os.access(binary, os.X_OK),
            'profile_present': profile.is_dir() and not profile.is_symlink(),
            'profile_lock_present': lock.exists() or lock.is_symlink(),
            'launch_attempted': False, 'operator_app_touched': False}


def probe(binary=DEFAULT_BINARY, profile_root=DEFAULT_PROFILE_ROOT, name='default'):
    """Launch and stop our own child, never adopt or signal the operator app.

    A second-instance event in Electron could still affect the operator UI;
    this cannot be established from the process launch alone. Coexistence must
    be validated on a real Mac before claiming operator isolation.
    """
    profile = profile_path(profile_root, name)
    binary = Path(binary).expanduser().absolute()
    if binary.is_symlink() or not binary.is_file() or not os.access(binary, os.X_OK):
        return {'status': 'binary_unavailable', 'ready': False, 'launch_attempted': False}
    _private_directory(profile.parent)
    _private_directory(profile)
    lock, fd = _claim_lock(profile.parent, name)
    transport = proc = None
    try:
        try:
            transport, proc = launch_cdp_process(binary, build_args(profile), label='ChatGPT Desktop')
        except (OSError, RuntimeError) as exc:
            return {'status': 'launch_failed', 'ready': False,
                    'launch_attempted': True, 'operator_isolation_verified': False,
                    'reason': type(exc).__name__}
        os.write(fd, str(proc.pid).encode('ascii'))
        os.fsync(fd)
        try:
            # The dedicated Electron renderer is not necessarily present at
            # the first Target.getTargets: poll only the missing-target state.
            outcome = {'status': 'target_missing', 'ready': False}
            for attempt in range(20):
                if proc.poll() is not None:
                    outcome = {'status': 'instance_exited', 'ready': False}
                    break
                outcome = census(transport)
                if outcome['status'] != 'target_missing':
                    break
                if attempt != 19:
                    time.sleep(0.25)
            return outcome | {'launch_attempted': True,
                              'operator_isolation_verified': False}
        except (TimeoutError, EOFError, RuntimeError, OSError, ValueError) as exc:
            return {'status': 'pipe_unavailable', 'ready': False,
                    'launch_attempted': True, 'operator_isolation_verified': False,
                    'reason': type(exc).__name__}
    finally:
        if proc is not None:
            # Never enumerate, quit, signal or adopt the operator's app.
            terminate_chrome(proc, timeout=3)
        if transport is not None:
            for handle in (transport._reader, transport._writer):
                try:
                    handle.close()
                except OSError:
                    pass
        _release_lock(lock, fd)



# Send is intentionally an outbound-only dispatch. Completion still requires
# an independently accepted Reprise submit_result, never a renderer observation.
MAX_PROMPT_CHARS = 20000
REQUEST_KEY = re.compile(r'[A-Za-z0-9_.:-]{1,128}\Z')
SEND_HELPERS = r"""
const visible = (element) => {
  const rect = element.getBoundingClientRect();
  const style = getComputedStyle(element);
  return rect.width > 0 && rect.height >= 12 && style.visibility !== 'hidden'
    && style.display !== 'none' && !element.readOnly;
};
const editors = () => [...document.querySelectorAll('textarea, [contenteditable="true"], [role="textbox"]')]
  .filter(e => visible(e) && e.getBoundingClientRect().width >= 80 && !e.disabled);
const sendButtons = () => [...document.querySelectorAll('button')].filter(visible)
  .filter(e => /^(send|envoyer)(\s+(message|le message))?$/i
    .test((e.getAttribute('aria-label') || e.getAttribute('title') || '').trim()));
const turns = (role) => [...document.querySelectorAll(
  '[data-message-author-role="' + role + '"], [data-testid="' + role + '-turn"]')];
function composerStatus(expected) {
  if (location.href !== 'app://-/index.html') return {status: 'target_changed'};
  const found = editors();
  if (found.length !== 1) return {status: 'selectors_stale', composer_count: found.length};
  const buttons = sendButtons();
  if (buttons.length !== 1) return {status: 'selectors_stale', send_button_count: buttons.length};
  const editor = found[0];
  const text = editor.tagName === 'TEXTAREA' ? editor.value : editor.innerText;
  if (expected === null && text.length) return {status: 'draft_conflict'};
  if (expected !== null && text !== expected) return {status: 'draft_mismatch'};
  if (expected !== null && buttons[0].disabled) return {status: 'send_disabled'};
  return {status: 'ready', users: turns('user').length, assistants: turns('assistant').length};
}
function focusUniqueComposer() {
  const found = editors();
  if (found.length !== 1) return false;
  found[0].focus();
  return document.activeElement === found[0];
}
function clickUniqueSend(expected) {
  const status = composerStatus(expected);
  if (status.status !== 'ready') return {clicked: false, status: status.status};
  const buttons = sendButtons().filter(e => !e.disabled);
  if (buttons.length !== 1) return {clicked: false, status: 'selectors_stale'};
  buttons[0].click();
  return {clicked: true, status: 'clicked'};
}
function observeTurns(expected, beforeUsers, beforeAssistants) {
  if (location.href !== 'app://-/index.html') return {status: 'target_changed'};
  const users = turns('user'), assistants = turns('assistant');
  const found = editors();
  const last = users.at(-1);
  const matching = users.length === beforeUsers + 1 && !!last &&
    last.innerText.trim() === expected.trim();
  const newest = assistants.length > beforeAssistants ? assistants.at(-1) : null;
  const newText = newest ? newest.innerText.trim() : '';
  return {status: 'observed', users: users.length, assistants: assistants.length,
    user_confirmed: matching,
    composer_emptied: found.length === 1 &&
      (found[0].tagName === 'TEXTAREA' ? found[0].value : found[0].innerText) === '',
    assistant_observed: !!(matching && newText),
    assistant_text: matching && newText ? newText.slice(-1500) : ''};
}
"""


def _evaluate_send(transport, function, *args):
    # JSON serialization keeps prompt contents out of interpolated JS source.
    values = ','.join(json.dumps(v, ensure_ascii=True) for v in args)
    expression = '(() => {\n' + SEND_HELPERS + '\nreturn ' + function + '(' + values + ');\n})()'
    return transport.evaluate(expression)


def _send_receipt():
    return {
        'status': 'refused', 'reason': None, 'attempted': False,
        'click_observed': False, 'user_message_count_before': None,
        'user_message_count_after': None, 'user_confirmed': False,
        'composer_emptied': False, 'response_observed': False,
        'response_complete': False, 'operator_isolation_verified': False,
        'transport': 'cdp-pipe',
    }


def _receipt_path(profile, request_key):
    return profile / ('.send-' + hashlib.sha256(request_key.encode('utf-8')).hexdigest() + '.json')


def _load_receipt(path, prompt_hash):
    if not path.exists() and not path.is_symlink():
        return None
    flags = os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0)
    fd = os.open(path, flags)
    with os.fdopen(fd, 'rb') as stored:
        info = os.fstat(stored.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size > 32768:
            raise ValueError('Invalid receipt type or size')
        data = json.loads(stored.read(32769))
    if not isinstance(data, dict) or data.get('prompt_sha256') != prompt_hash:
        raise ValueError('Request key belongs to another prompt or malformed receipt')
    return data


def _persist_receipt(path, receipt, *, initial=False):
    payload = json.dumps(receipt, sort_keys=True, ensure_ascii=False).encode('utf-8')
    if initial:
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_NOFOLLOW', 0)
        fd = os.open(path, flags, 0o600)
        target = path
    else:
        target = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_NOFOLLOW', 0)
        fd = os.open(target, flags, 0o600)
    try:
        with os.fdopen(fd, 'wb') as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        if not initial:
            os.replace(target, path)
        # A crash after reservation must not permit a second click after restart.
        directory_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if not initial:
            target.unlink(missing_ok=True)


def _dispatch(transport, prompt, profile, request_key, receipt):
    before = _evaluate_send(transport, 'composerStatus', None)
    if not isinstance(before, dict) or before.get('status') != 'ready':
        receipt['reason'] = before.get('status', 'selectors_stale') if isinstance(before, dict) else 'selectors_stale'
        if isinstance(before, dict):
            for key in ('composer_count', 'send_button_count'):
                if key in before:
                    receipt[key] = before[key]
        return receipt
    users, assistants = before.get('users'), before.get('assistants')
    if type(users) is not int or type(assistants) is not int:
        receipt['reason'] = 'turn_counts_invalid'
        return receipt
    receipt['user_message_count_before'] = users
    receipt['user_message_count_after'] = users
    if _evaluate_send(transport, 'focusUniqueComposer') is not True:
        receipt['reason'] = 'focus_failed'
        return receipt
    transport.insert_text(prompt)
    staged = None
    for attempt in range(15):
        staged = _evaluate_send(transport, 'composerStatus', prompt)
        if isinstance(staged, dict) and staged.get('status') == 'ready':
            break
        if not isinstance(staged, dict) or staged.get('status') not in ('send_disabled', 'draft_mismatch'):
            break
        if attempt != 14:
            time.sleep(0.15)
    if not isinstance(staged, dict) or staged.get('status') != 'ready':
        receipt['reason'] = staged.get('status', 'selectors_stale') if isinstance(staged, dict) else 'selectors_stale'
        return receipt

    # Irreversible boundary: write an fsync'd uncertainty marker BEFORE click.
    # Even a crash between this marker and the CDP reply must never re-dispatch.
    path = _receipt_path(profile, request_key)
    receipt.update(status='incertain', attempted=True,
                   prompt_sha256=hashlib.sha256(prompt.encode('utf-8')).hexdigest())
    _persist_receipt(path, receipt, initial=True)
    try:
        click = _evaluate_send(transport, 'clickUniqueSend', prompt)
        if not isinstance(click, dict) or click.get('clicked') is not True:
            receipt.update(status='refused', attempted=False,
                           reason=click.get('status', 'click_unconfirmed') if isinstance(click, dict) else 'click_unconfirmed')
            return receipt
        receipt['click_observed'] = True
        for attempt in range(20):
            time.sleep(1)
            turn = _evaluate_send(transport, 'observeTurns', prompt, users, assistants)
            if not isinstance(turn, dict) or turn.get('status') != 'observed':
                receipt.update(status='incertain',
                               reason=turn.get('status', 'observation_invalid') if isinstance(turn, dict) else 'observation_invalid')
                break
            count = turn.get('users')
            if type(count) is int:
                receipt['user_message_count_after'] = count
            receipt['user_confirmed'] = turn.get('user_confirmed') is True and count == users + 1
            receipt['composer_emptied'] = turn.get('composer_emptied') is True
            # A later contradictory observation revokes an earlier positive one.
            receipt['status'] = ('sent' if receipt['user_confirmed'] and receipt['composer_emptied']
                                 else 'incertain')
            if receipt['status'] == 'sent':
                if turn.get('assistant_observed') is True and type(turn.get('assistants')) is int and turn['assistants'] > assistants:
                    receipt.update(status='confirmed', response_observed=True,
                                   assistant_excerpt=str(turn.get('assistant_text', ''))[-1500:])
                    break
        if receipt['status'] == 'incertain' and receipt['reason'] is None:
            receipt['reason'] = 'evidence_incomplete'
    except (TimeoutError, EOFError, RuntimeError, OSError, ValueError):
        receipt.update(status='incertain', reason='pipe_unavailable')
    finally:
        try:
            _persist_receipt(path, receipt)
        except (OSError, ValueError):
            # The initial marker is still present, so replay stays blocked.
            receipt.update(status='incertain', reason='receipt_update_failed')
    return receipt


def send(prompt, binary=DEFAULT_BINARY, profile_root=DEFAULT_PROFILE_ROOT,
         name='default', request_key=None):
    """One at-most-once dispatch on an owned pipe; stdout is never the durable guard."""
    receipt = _send_receipt()
    transport = proc = lock = fd = None
    if not isinstance(prompt, str) or not prompt.strip():
        receipt['reason'] = 'prompt_required'
        return receipt
    if len(prompt) > MAX_PROMPT_CHARS:
        receipt['reason'] = 'prompt_too_large'
        return receipt
    try:
        profile = profile_path(profile_root, name)
        binary = Path(binary).expanduser().absolute()
        if request_key is None:
            # Repeating identical prompts is intentionally blocked by default.
            request_key = hashlib.sha256(prompt.encode('utf-8')).hexdigest()
        if not isinstance(request_key, str) or not REQUEST_KEY.fullmatch(request_key):
            receipt['reason'] = 'invalid_request_key'
            return receipt
        prompt_hash = hashlib.sha256(prompt.encode('utf-8')).hexdigest()
        if binary.is_symlink() or not binary.is_file() or not os.access(binary, os.X_OK):
            receipt['reason'] = 'binary_unavailable'
            return receipt
        _private_directory(profile.parent)
        _private_directory(profile)
        try:
            existing = _load_receipt(_receipt_path(profile, request_key), prompt_hash)
        except (OSError, ValueError, json.JSONDecodeError):
            receipt.update(status='incertain', attempted=True,
                       reason='receipt_conflict_or_unreadable')
            return receipt
        if existing is not None:
            existing['replayed'] = True
            return existing
        lock, fd = _claim_lock(profile.parent, name)
        try:
            transport, proc = launch_cdp_process(binary, build_args(profile), label='ChatGPT Desktop')
        except (OSError, RuntimeError):
            receipt['reason'] = 'launch_failed'
            return receipt
        os.write(fd, str(proc.pid).encode('ascii'))
        os.fsync(fd)
        for attempt in range(20):
            if proc.poll() is not None:
                receipt['reason'] = 'instance_exited'
                return receipt
            state = census(transport)
            if state.get('status') != 'target_missing':
                if state.get('status') != 'ready':
                    receipt['reason'] = state.get('status', 'selectors_stale')
                    receipt.update({k: state[k] for k in ('composer_count',) if k in state})
                    return receipt
                break
            if attempt != 19:
                time.sleep(0.25)
        else:
            receipt['reason'] = 'target_missing'
            return receipt
        return _dispatch(transport, prompt, profile, request_key, receipt)
    except FileExistsError:
        receipt['reason'] = 'profile_busy'
    except (TimeoutError, EOFError, RuntimeError, OSError, ValueError) as exc:
        # Before the irreversible marker, transport failure proves no click.
        receipt['reason'] = 'pipe_unavailable' if isinstance(exc, (TimeoutError, EOFError, RuntimeError)) else 'refused'
        if receipt.get('attempted'):
            receipt['status'] = 'incertain'
    finally:
        if proc is not None:
            terminate_chrome(proc, timeout=3)
        if transport is not None:
            transport.close()
        if lock is not None:
            _release_lock(lock, fd)
    return receipt


def _read_prompt(source):
    if source is None:
        return None, 'prompt_required'
    # 20k Unicode code points can occupy at most 80k UTF-8 bytes.
    limit = MAX_PROMPT_CHARS * 4 + 1
    try:
        if source == '-':
            raw = sys.stdin.buffer.read(limit)
        else:
            with open(source, 'rb') as handle:
                raw = handle.read(limit)
        if len(raw) >= limit:
            return None, 'prompt_too_large'
        prompt = raw.decode('utf-8')
    except (OSError, UnicodeError):
        return None, 'prompt_unreadable'
    if not prompt.strip():
        return None, 'prompt_required'
    if len(prompt) > MAX_PROMPT_CHARS:
        return None, 'prompt_too_large'
    return prompt, None

def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='action', required=True)
    for cmd in ('inspect', 'probe', 'send'):
        obj = sub.add_parser(cmd)
        obj.add_argument('--profile', default='default')
        obj.add_argument('--profile-root', type=Path, default=DEFAULT_PROFILE_ROOT)
        obj.add_argument('--binary', type=Path, default=DEFAULT_BINARY)
        if cmd == 'send':
            obj.add_argument('--prompt-file', help="UTF-8 file or '-' for stdin")
            obj.add_argument('--request-key', help='Stable request identity; default hashes the prompt')
    args = parser.parse_args(argv)
    os.umask(0o077)
    try:
        values = dict(binary=args.binary, profile_root=args.profile_root, name=args.profile)
        if args.action == 'send':
            prompt, error = _read_prompt(args.prompt_file)
            if error is not None:
                outcome = _send_receipt() | {'reason': error}
                if error == 'prompt_required':
                    parser.print_usage(sys.stderr)
                exit_code = 64 if error == 'prompt_required' else 2
            else:
                outcome = send(prompt, request_key=args.request_key, **values)
                exit_code = 0 if outcome.get('status') in ('sent', 'confirmed') else 2
        else:
            outcome = inspect(**values) if args.action == 'inspect' else probe(**values)
    except (ValueError, OSError, RuntimeError) as exc:
        if args.action == 'send':
            outcome = _send_receipt() | {'reason': type(exc).__name__}
            exit_code = 2
        else:
            outcome = {'status': 'refused', 'ready': False, 'reason': type(exc).__name__}
    print(json.dumps(outcome, sort_keys=True, ensure_ascii=False))
    return exit_code if args.action == 'send' and 'exit_code' in locals() else (0 if args.action == 'inspect' or outcome.get('status') == 'ready' else 2)


if __name__ == '__main__':
    sys.exit(main())
