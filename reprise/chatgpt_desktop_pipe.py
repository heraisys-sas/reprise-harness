#!/usr/bin/env python3
"""Owned CDP pipe probe, guarded ChatGPT Desktop dispatch and reply capture.

The official ChatGPT.app binary is launched as an owned child with its own
persistent profile. No Codex CLI, operator app, TCP DevTools port, or
implicit provider fallback. Dispatch requires a unique target and a durable
receipt; the send alone is NOT proof of business completion — the receipt's
captured reply (anchored rendered read + completion evidence) is.
"""
import argparse
import hashlib
import uuid
import json
import os
from pathlib import Path
import re
import signal
import time
import socket as socket_module
import stat
import subprocess
import sys
import threading
import time

# This module doubles as a detached holder entrypoint (`python3 <script>
# serve`); the bare import below only resolves when the module directory is
# importable, which is not the case for a spawned child. Fix sys.path first.
_HERE = str(Path(__file__).resolve().parent)
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

from claude_cdp_pipe import launch_cdp_process, terminate_chrome

DEFAULT_BINARY = Path('/Applications/ChatGPT.app/Contents/MacOS/ChatGPT')
# Clone gelé à identité propre (contrat APP-CLONES) : préféré pour le profil
# dédié dès qu'il existe.
RECHATGPT_BINARY = Path.home() / 'Applications/ReChatGPT.app/Contents/MacOS/ChatGPT'
DEFAULT_PROFILE_ROOT = Path.home() / 'Library/Application Support/Reprise/chatgpt-cdp-profiles'

def resolve_binary(binary, name):
    """None → le clone ReChatGPT pour le profil dédié, sinon l'app officielle."""
    if binary is not None:
        return Path(binary).expanduser().absolute()
    if name == 'dedicated' and RECHATGPT_BINARY.is_file():
        return RECHATGPT_BINARY
    return DEFAULT_BINARY
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
// Issue #226 (live evidence, 17 nov. 2026): the owl Electron client exposes
// neither turn selector family, and the conversation token stream is invisible
// to CDP (no EventSource, renderer JS issues no conversation fetch). Completion
// arrives as a Network.webSocketFrameReceived lifecycle event on the shared
// topics socket; the reply text is read from the rendered conversation via
// bodyProbe below.
const bodyProbe = () => ({url: location.href, ready: document.readyState,
  text: (document.body.innerText || '').slice(-60000),
  stream_len: window.__reprise_stream || 0,
  quiet_ms: window.__reprise_last_mutation ? (Date.now() - window.__reprise_last_mutation) : null});
function composerStatus(expected) {
  if (location.href !== 'app://-/index.html') return {status: 'target_changed'};
  const found = editors();
  if (found.length !== 1) return {status: 'selectors_stale', composer_count: found.length};
  const buttons = sendButtons();
  if (buttons.length !== 1) return {status: 'selectors_stale', send_button_count: buttons.length};
  const editor = found[0];
  const text = editor.tagName === 'TEXTAREA' ? editor.value : editor.innerText;
  if (expected === null && text.trim().length) return {status: 'draft_conflict', draft_text: text.slice(0, 200)};
  if (expected !== null) {
    const norm = (value) => value.replace(/\s+/g, ' ').trim();
    if (norm(text) !== norm(expected)) {
      return {status: 'draft_mismatch', staged_text: text.slice(0, 120),
              staged_chars: text.length};
    }
  }
  if (expected !== null && buttons[0].disabled) return {status: 'send_disabled'};
  return {status: 'ready'};
}
function focusUniqueComposer() {
  const found = editors();
  if (found.length !== 1) return false;
  found[0].focus();
  return document.activeElement === found[0];
}
// Select the whole composer content so the following CDP Input.insertText
// REPLACES any leftover draft (PoC #161 recipe: Cmd+A then insertText).
function selectComposerContent() {
  const found = editors();
  if (found.length !== 1) return false;
  const editor = found[0];
  editor.focus();
  if (editor.tagName === 'TEXTAREA') {
    editor.select();
    return editor.selectionStart === 0 && editor.selectionEnd === editor.value.length;
  }
  const selection = document.getSelection();
  selection.removeAllRanges();
  const range = document.createRange();
  range.selectNodeContents(editor);
  selection.addRange(range);
  return document.getSelection().rangeCount === 1;
}
function clickUniqueSend(expected) {
  const status = composerStatus(expected);
  if (status.status !== 'ready') return {clicked: false, status: status.status};
  const buttons = sendButtons().filter(e => !e.disabled);
  if (buttons.length !== 1) return {clicked: false, status: 'selectors_stale'};
  buttons[0].click();
  return {clicked: true, status: 'clicked'};
}
"""


def _evaluate_send(transport, function, *args):
    # JSON serialization keeps prompt contents out of interpolated JS source.
    values = ','.join(json.dumps(v, ensure_ascii=True) for v in args)
    expression = '(() => {\n' + SEND_HELPERS + '\nreturn ' + function + '(' + values + ');\n})()'
    return transport.evaluate(expression)


# Issue #226 (live evidence, 17 nov. 2026): the owl client streams no CDP
# EventSource and its renderer JS issues no conversation fetch; the token
# stream lives in the main process and is not observable over CDP. What the
# Network domain does deliver on the shared ws.chatgpt.com topics socket is
# the lifecycle: conversation-created and conversation-turn-complete. The
# reply TEXT is read from the rendered conversation (bodyProbe), anchored on
# the prompt echo, cut by an explicit sentinel when the model complied and
# else by the conversation footer; the retained extraction is the LAST
# anchored read (append-only conversation — longest seen is only a fallback).
COMPLETION_POLL_INTERVAL_SECONDS = 1.0
# Deep-reasoning turns ('Effort de réflexion : Élevé') can think for 60-90 s;
# the ws turn-complete event ends the wait, not the clock.
COMPLETION_TIMEOUT_SECONDS = 180
# After the ws turn-complete event the DOM needs a moment to render the final
# reply; read at most this long after the event (footer anchor ends it early).
COMPLETION_SETTLE_SECONDS = 6.0
RESPONSE_EXCERPT_CHARS = 1500
MAX_PROMPT_CHARS = 20000
# Issue #236: the capture ceiling is now declared on the receipt instead of a
# silent truncation. It must stay below the bodyProbe render window
# (MAX_PROBE_CHARS = 60000 minus the prompt echo) so a reply cut here is a
# real ceiling, and the sentinel-capped capture can carry an honest
# output_complete proof for the pagination layer.
MAX_CAPTURE_CHARS = 58000
REPLY_SENTINEL = 'REPRISE-CAPTURE-FIN'
REPLY_LABELS = ('chatgpt a dit', 'chatgpt said')
# Footer markers include the «ChatGPT» prefix so the cut drops the whole
# closing sentence, not just its tail.
REPLY_FOOTERS = ('chatgpt peut faire des erreurs', 'chatgpt can make mistakes',
                 'peut faire des erreurs', 'can make mistakes')
# Issue #228 (2/2 confirmed): rendered conversation text can be pure UI chrome
# — «Afficher plus», «Durée d'exécution : 0s», «Réflexion en cours» and
# neighbours. Such text is NOT a reply: it is cut at the first label (like a
# footer) and can never validate a receipt.
REPLY_UI_CHROME = ('afficher plus', 'afficher la réponse', 'afficher le raisonnement',
                   'durée d\'exécution', 'réflexion en cours', 'réfléchit',
                   'reasoning', 'thinking')
# A receipt is only confirmed for a capture that is neither chrome nor a
# fragment. Threshold (issue #228, criterion 2): 80 non-blank chars — the
# known chrome labels together stay below ~70 chars, while typical delegate
# answers exceed 80. Short real replies (< 80 chars) without a sentinel are
# degraded to captured_incomplete: include the sentinel in the prompt to get
# a strict completion proof for a short expected answer.
MIN_USEFUL_REPLY_CHARS = 80
TURN_COMPLETE_MARKER = 'conversation-turn-complete'
# Distinct window title for the dedicated instance (visible in Mission Control
# and window switchers); re-applied at dispatch start — the client resets it.
WINDOW_TITLE = 'ReChatGPT'
# Rendered completion marker (measured 17/11): the owl client prints it when
# the turn is done. DOM marker + ws event are the primary end signals.
COMPLETION_MARKERS = ('réponse terminée',)
# Targeted DOM change detection (issue #226): a MutationObserver injected on
# the dedicated renderer records that conversation rendering is active and
# when it goes quiet. The stream is a change signal only: the reply text is
# read from the rendered conversation (bodyProbe), never reconstructed from
# deltas (owl obfuscation makes delta reconstruction fragile). Intersection
# tracking adds nothing in the compact dedicated window and is left out.
# Quiet is a degraded fallback: deep thinking can hold the DOM still for a
# long time, so the threshold stays high (20 s).
STREAM_QUIET_MS = 20000
OBSERVER_SCRIPT = """
(() => {
  if (window.__reprise_observer) return 'already';
  window.__reprise_stream = 0;
  window.__reprise_last_mutation = 0;
  const observer = new MutationObserver((mutations) => {
    for (const m of mutations) {
      if (m.type === 'characterData' || m.type === 'childList') {
        window.__reprise_stream += 1;
        window.__reprise_last_mutation = Date.now();
      }
    }
  });
  observer.observe(document.body, {subtree: true, childList: true, characterData: true});
  window.__reprise_observer = observer;
  return 'armed';
})()
"""


def _net_enable(transport):
    """Enable the Network domain on the attached renderer; best effort."""
    try:
        transport.call('Network.enable', {'maxTotalBufferSize': 10000000,
                                          'maxResourceBufferSize': 5000000})
        return True
    except (TimeoutError, EOFError, RuntimeError, OSError, ValueError):
        return False


def drain_turn_completion(transport):
    """Scan buffered CDP notifications for the turn lifecycle event.

    Returns True once a conversation-turn-complete frame is observed. The
    shared topics socket may predate the Network.enable call: frames keep
    flowing to a session that enables the domain mid-flight (measured).
    """
    for frame in transport.drain_notifications():
        if not isinstance(frame, dict):
            continue
        response = (frame.get('params') or {}).get('response') or {}
        payload = response.get('payloadData') if isinstance(response, dict) else None
        if isinstance(payload, str) and TURN_COMPLETE_MARKER in payload:
            return True
    return False


def apply_window_title(transport):
    """Mark the dedicated window so the operator never confuses the two apps."""
    try:
        return transport.evaluate('document.title = ' + json.dumps(WINDOW_TITLE)) == WINDOW_TITLE
    except (TimeoutError, EOFError, RuntimeError, OSError, ValueError):
        return False


def install_stream_observer(transport):
    """Arm the targeted change detector on the attached renderer; best effort."""
    try:
        return transport.evaluate(OBSERVER_SCRIPT) in ('armed', 'already')
    except (TimeoutError, EOFError, RuntimeError, OSError, ValueError):
        return False


def _normalize(text):
    return ' '.join((text or '').split())


def extract_reply(text, prompt):
    """Bounded extraction of the assistant reply from rendered conversation text.

    Anchored on the last occurrence of the prompt echo; cut by the explicit
    sentinel when present, else by the conversation footer. Returns
    (reply or None, anchor); never raises, never returns unbounded text.

    Issue #236: the third element reports the detected total length of the
    reply before the defensive MAX_CAPTURE_CHARS ceiling, so a truncated
    capture can declare its incompleteness on the receipt instead of
    silently delivering a clipped answer as a complete one.
    """
    joined = _normalize(text)
    marker = _normalize(prompt)
    position = joined.rfind(marker)
    if position < 0:
        return None, 'prompt_echo_missing', 0
    rest = joined[position + len(marker):]
    anchor = 'end'
    cut = len(rest)
    sentinel = rest.find(REPLY_SENTINEL)
    if sentinel >= 0:
        cut = sentinel
        anchor = 'sentinel'
    lower = rest.lower()
    for footer in REPLY_FOOTERS + REPLY_UI_CHROME:
        found = lower.find(footer)
        if 0 <= found < cut:
            cut = found
            if anchor != 'sentinel':
                anchor = 'footer'
    rest = rest[:cut]
    for label in REPLY_LABELS:
        head = rest.lower().find(label)
        if 0 <= head <= 40:
            rest = rest[head + len(label):]
            break
    reply = rest.strip(' :\u00a0-')
    if not reply:
        return None, anchor, len(rest)
    total = len(reply)
    if len(reply) > MAX_CAPTURE_CHARS:
        reply = reply[:MAX_CAPTURE_CHARS]
    return reply, anchor, total


def _usable_reply(reply, anchor=None):
    """Issue #228 gate: None or (True, None) for real content, else (False, reason).

    UI chrome («Afficher plus», «Durée d'exécution», «Réflexion en cours»…)
    never validates a receipt; a capture below MIN_USEFUL_REPLY_CHARS
    non-blank chars is a fragment, not a complete answer.

    Issue #236: a sentinel-anchored capture is exempt from the length
    threshold — REPRISE-CAPTURE-FIN is the strict completion proof the
    delegate was instructed to emit, so a short real reply anchored on it
    is a complete answer, not a fragment (the gate comment in #228
    promised this exemption but never received the anchor).
    """
    if not reply:
        return False, 'no_reply_text'
    lowered = reply.lower()
    if any(label in lowered for label in REPLY_UI_CHROME):
        return False, 'ui_chrome_capture'
    if anchor != 'sentinel' and sum(not ch.isspace() for ch in reply) < MIN_USEFUL_REPLY_CHARS:
        return False, 'capture_too_short'
    return True, None


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
    # The durable receipt keeps bounded evidence only: never the full reply
    # text (response_text is an in-memory field for the synchronous caller).
    durable = {k: v for k, v in receipt.items() if k != 'response_text'}
    payload = json.dumps(durable, sort_keys=True, ensure_ascii=False).encode('utf-8')
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


def _bundle_path(binary):
    """The .app bundle owning this binary, for path-accurate activation."""
    for parent in Path(binary).resolve().parents:
        if parent.suffix == '.app':
            return parent
    return None


def _dispatch_enter(transport):
    """Submit the staged prompt with the PoC-validated Enter (keyCode 13).

    Measured live (17/11): the button click can hit a node replaced by the
    thinking-spinner re-render and silently no-op; the Enter key event on the
    focused composer dispatches the whole staged draft.
    """
    transport.call('Input.dispatchKeyEvent',
                   {'type': 'rawKeyDown', 'windowsVirtualKeyCode': 13, 'code': 'Enter',
                    'key': 'Enter', 'text': '\r', 'unmodifiedText': '\r',
                    'nativeVirtualKeyCode': 13})
    transport.call('Input.dispatchKeyEvent',
                   {'type': 'keyUp', 'windowsVirtualKeyCode': 13, 'code': 'Enter',
                    'key': 'Enter'})
    return True


def _dispatch(transport, prompt, profile, request_key, receipt):
    apply_window_title(transport)
    before = _evaluate_send(transport, 'composerStatus', None)
    if not isinstance(before, dict) or before.get('status') != 'ready':
        receipt['reason'] = before.get('status', 'selectors_stale') if isinstance(before, dict) else 'selectors_stale'
        if isinstance(before, dict):
            for key in ('composer_count', 'send_button_count', 'draft_text'):
                if key in before:
                    receipt[key] = before[key]
        return receipt
    # Refusals above stay CDP-quiet: nothing is enabled or injected before a
    # viable dispatch. From here on: Network domain (lifecycle frames flow even
    # if the topics socket predates the enable), then the targeted change
    # detector, then staging and the guarded click.
    _net_enable(transport)
    install_stream_observer(transport)
    if _evaluate_send(transport, 'focusUniqueComposer') is not True:
        receipt['reason'] = 'focus_failed'
        return receipt
    # Select-all first: a leftover draft (crashed prior dispatch) must be
    # REPLACED, not appended to. The owned dedicated profile makes this safe.
    if _evaluate_send(transport, 'selectComposerContent') is not True:
        receipt['reason'] = 'select_failed'
        return receipt
    transport.insert_text(prompt)
    staged = None
    ready_streak = 0
    for attempt in range(15):
        staged = _evaluate_send(transport, 'composerStatus', prompt)
        if isinstance(staged, dict) and staged.get('status') == 'ready':
            # ProseMirror commits the multi-paragraph insert asynchronously:
            # require the staged state twice before the irreversible click.
            ready_streak += 1
            if ready_streak >= 2:
                break
            if attempt != 14:
                time.sleep(0.2)
            continue
        if not isinstance(staged, dict) or staged.get('status') not in ('send_disabled', 'draft_mismatch'):
            break
        ready_streak = 0
        if attempt != 14:
            time.sleep(0.15)
    if not isinstance(staged, dict) or staged.get('status') != 'ready':
        receipt['reason'] = staged.get('status', 'selectors_stale') if isinstance(staged, dict) else 'selectors_stale'
        if isinstance(staged, dict) and 'staged_text' in staged:
            receipt['staged_text'] = staged['staged_text']
        return receipt
    time.sleep(0.5)

    # Irreversible boundary: write an fsync'd uncertainty marker BEFORE click.
    # Even a crash between this marker and the CDP reply must never re-dispatch.
    path = _receipt_path(profile, request_key)
    receipt.update(status='incertain', attempted=True,
                   prompt_sha256=hashlib.sha256(prompt.encode('utf-8')).hexdigest())
    _persist_receipt(path, receipt, initial=True)
    try:
        # Irreversible boundary already marked. Primary dispatch = the PoC-
        # validated Enter on the focused composer; the send button click is
        # the fallback (its node can be replaced mid-render, measured live).
        receipt['dispatch_action'] = 'enter'
        try:
            _dispatch_enter(transport)
            receipt['click_observed'] = True
        except (TimeoutError, EOFError, RuntimeError, OSError, ValueError):
            click = _evaluate_send(transport, 'clickUniqueSend', prompt)
            if not isinstance(click, dict) or click.get('clicked') is not True:
                receipt.update(status='refused', attempted=False,
                               reason=click.get('status', 'click_unconfirmed') if isinstance(click, dict) else 'click_unconfirmed')
                return receipt
            receipt['click_observed'] = True
            receipt['dispatch_action'] = 'button'
        # Post-send observation (issue #226): completion evidence from two
        # identical rendered polls, the ws.chatgpt.com lifecycle event, and
        # the injected MutationObserver quiet window; reply text anchored on
        # the prompt echo. Bounded; never a fatal error.
        best = None
        previous = None
        stable_streak = 0
        completed = quiet = sentinel_seen = dom_marker = False
        chrome_seen = False
        deadline = time.monotonic() + COMPLETION_TIMEOUT_SECONDS
        completed_at = None
        polls = 0
        last = best = None
        while True:
            if drain_turn_completion(transport):
                completed = True
                if completed_at is None:
                    completed_at = time.monotonic()
            probe = transport.evaluate(
                '(() => {\n' + SEND_HELPERS + '\nreturn bodyProbe();\n})()')
            text = probe.get('text') if isinstance(probe, dict) else None
            polls += 1
            if isinstance(text, str) and text:
                lowered_text = text.lower()
                if any(label in lowered_text for label in REPLY_UI_CHROME):
                    chrome_seen = True
                reply, anchor, reply_total = extract_reply(text, prompt) if isinstance(text, str) else (None, 'prompt_echo_missing', 0)
                if reply is not None:
                    # The conversation is append-only: the LAST extraction is
                    # the current final state; the longest seen is the fallback
                    # (junk like the thinking indicator can outsize a short
                    # true reply, so longest is never preferred over last).
                    last = (reply, anchor, reply_total)
                if reply is not None and (best is None or len(reply) > len(best[0])):
                    best = (reply, anchor, reply_total)
                evidence = reply if reply is not None else text
                digest = hashlib.sha256(_normalize(evidence).encode('utf-8')).hexdigest()
                stable_streak = stable_streak + 1 if previous == digest else 0
                previous = digest
                quiet_ms = probe.get('quiet_ms') if isinstance(probe, dict) else None
                if type(quiet_ms) in (int, float) and quiet_ms >= STREAM_QUIET_MS:
                    quiet = True
            if last is not None and any(marker in text.lower() for marker in COMPLETION_MARKERS):
                dom_marker = True
                break
            if quiet and last is not None:
                break
            if completed and last is not None and (
                    (last[1] == 'footer' or last[1] == 'sentinel')
                    or time.monotonic() - completed_at >= COMPLETION_SETTLE_SECONDS):
                # The ws event ends the turn; let the DOM render the final
                # reply (footer anchor) before reading it (measured 17/11:
                # reading immediately captures the thinking indicator).
                break
            if time.monotonic() >= deadline:
                break
            time.sleep(COMPLETION_POLL_INTERVAL_SECONDS)
        if drain_turn_completion(transport):
            completed = True
        status = _evaluate_send(transport, 'composerStatus', None)
        receipt['composer_emptied'] = isinstance(status, dict) and status.get('status') == 'ready'
        chosen = last if last is not None else best
        usable, unusable_reason = _usable_reply(chosen[0] if chosen else None,
                                                chosen[1] if chosen else None)
        chrome_only = chosen is None and chrome_seen
        if chosen is not None:
            reply, anchor, reply_total = chosen
            # The prompt echo in the rendered conversation is the delivery
            # evidence: the message is visibly part of the client conversation.
            # The sentinel anchor (the reply was cut at REPRISE-CAPTURE-FIN),
            # the ws lifecycle event, the rendered completion marker and the
            # mutation-quiet window are the only completeness evidence.
            sentinel_seen = anchor == 'sentinel'
            receipt['user_confirmed'] = True
            receipt['response_observed'] = True
            # Issue #236: declare capture completeness on the receipt instead
            # of silently clipping. A reply whose detected length exceeded the
            # defensive ceiling (probe window bound) is an amputated capture:
            # it never validates a receipt and is flagged for reconciliation.
            capture_truncated = reply_total > len(reply)
            if capture_truncated:
                # A truncated capture is never a complete reply, whatever the
                # completion evidence: the delegated result must be reviewed,
                # not consumed as the answer.
                usable = False
                unusable_reason = 'capture_truncated'
            # Issue #228 criterion 2/5: chrome or a too-short capture is never
            # a complete reply, and turn_completed: false (a1: no ws event, no
            # sentinel, no DOM marker, DOM silent) never yields
            # response_complete: true even when some text was read.
            completion_evidence = bool(completed or quiet or sentinel_seen
                                       or dom_marker)
            receipt['response_complete'] = bool(completion_evidence and usable)
            receipt['response_source'] = 'rendered-conversation'
            receipt['response_anchor'] = anchor
            receipt['completion'] = ('sentinel' if sentinel_seen else
                                     'turn-complete' if completed else
                                     'dom-marker' if dom_marker else
                                     'quiet' if quiet else 'timeout')
            receipt['turn_completed'] = completed
            if capture_truncated:
                receipt['output_truncated'] = True
                receipt['output_complete'] = False
                receipt['response_full_length'] = reply_total
                receipt['capture_limit_characters'] = MAX_CAPTURE_CHARS
            elif sentinel_seen:
                # Producer-attested completeness: the delegate emitted the
                # exact end marker, so the captured content is the whole
                # answer regardless of its length.
                receipt['output_complete'] = True
            # Raw excerpt/hash are kept unchanged for diagnosis (criterion 3),
            # even when the capture is chrome and the receipt stays incomplete.
            receipt['response_sha256'] = hashlib.sha256(reply.encode('utf-8')).hexdigest()
            receipt['response_chars'] = len(reply)
            receipt['response_excerpt'] = reply[:RESPONSE_EXCERPT_CHARS]
            receipt['response_text'] = reply
        else:
            receipt['completion'] = ('turn-complete' if completed else
                                     'quiet' if quiet else 'timeout')
        if chosen is not None and not usable:
            # Issue #228 criterion 3/4: new durable status. The raw excerpt is
            # kept for diagnosis and the receipt is persisted, so replay
            # returns it as-is: captured_incomplete NEVER re-sends the prompt
            # (at-most-once contract, single initial marker). Issue #236: a
            # truncated capture lands here too (usable was flipped to False).
            receipt['status'] = 'captured_incomplete'
            if receipt['reason'] is None:
                receipt['reason'] = unusable_reason
        elif chrome_only:
            # Issue #228 case a2: only UI chrome was rendered (thinking
            # indicator, «Afficher plus»…); nothing usable was extracted but
            # the observation itself is evidence, not silence.
            receipt['status'] = 'captured_incomplete'
            if receipt['reason'] is None:
                receipt['reason'] = 'ui_chrome_capture'
        else:
            receipt['status'] = ('sent' if receipt['click_observed'] and
                                 (receipt['composer_emptied'] or completed or
                                  bool(chosen and 'réflexion en cours' in chosen[0].lower()))
                                 else 'incertain')
            if receipt['status'] == 'sent' and receipt['response_observed'] \
                    and receipt['response_complete']:
                receipt['status'] = 'confirmed'
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


def _holder_socket_path(profile_root):
    return Path(profile_root) / 'dedicated.pipe.sock'


# Default holder socket, under the same private folder as the profile lock.
HOLDER_SOCKET_PATH = _holder_socket_path(DEFAULT_PROFILE_ROOT)

_HOLDER_START_ATTEMPTS = 30
# One dispatch is bounded (launch ≤ 30 s + staging + completion ≤ 180 s + margin);
# the reply may take the whole budget, so the client recv must outlive it.
HOLDER_EXCHANGE_TIMEOUT_SECONDS = 240


def _holder_connect(path, timeout=2.0):
    """Return a connected unix socket, or None when nobody listens."""
    sock = socket_module.socket(socket_module.AF_UNIX, socket_module.SOCK_STREAM)
    sock.settimeout(timeout)
    try:
        sock.connect(str(path))
    except OSError:
        try:
            sock.close()
        except OSError:
            pass
        return None
    return sock


def _holder_exchange(sock, payload):
    """One newline-framed JSON request; None on any transport/parse failure.

    The holder answers only after its full bounded dispatch, so the read
    timeout must cover the whole budget instead of the 2 s connect timeout.
    """
    try:
        sock.settimeout(HOLDER_EXCHANGE_TIMEOUT_SECONDS)
        sock.sendall((json.dumps(payload, ensure_ascii=False) + '\n').encode('utf-8'))
        frame = bytearray()
        while b'\n' not in frame:
            chunk = sock.recv(65536)
            if not chunk:
                return None
            frame.extend(chunk)
            if len(frame) > MAX_PROMPT_CHARS * 4 + 65536:
                return None
        line = frame.partition(b'\n')[0]
        return json.loads(line)
    except (OSError, ValueError, UnicodeDecodeError):
        return None
    finally:
        try:
            sock.close()
        except OSError:
            pass


def _client_replay(profile, prompt, request_key):
    """Read-only durable replay when no holder lives: the receipt files are
    the single source of truth and outlive the holder process. None when no
    receipt exists yet (caller may then start a holder)."""
    prompt_hash = hashlib.sha256(prompt.encode('utf-8')).hexdigest()
    try:
        existing = _load_receipt(_receipt_path(profile, request_key), prompt_hash)
    except (OSError, ValueError, json.JSONDecodeError):
        return _send_receipt() | {'status': 'incertain', 'attempted': True,
                                  'reason': 'receipt_conflict_or_unreadable'}
    if existing is None:
        return None
    return existing | {'replayed': True}


def _start_holder_daemon(binary, profile_root, name):
    """Spawn a detached holder process; it owns Chrome and the pipe for good.

    The binary/profile choices travel with the spawn: a renamed bundle copy
    (ReChatGPT) must survive client-triggered starts, not fall back to the
    default app.
    """
    script = Path(__file__).resolve()
    try:
        return subprocess.Popen(
            [sys.executable, str(script), 'serve', '--binary', str(binary),
             '--profile', name, '--profile-root', str(profile_root)],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, start_new_session=True)
    except OSError:
        return None


def _holder_sigterm(_signum, _frame):
    # Close the socket and keep Chrome alive: only the holder owns the pipe,
    # and the finally block of serve() performs the bounded cleanup.
    raise SystemExit(0)


def _holder_receipt(receipt, **update):
    receipt.update(update)
    return receipt


def _holder_relaunch(runtime, binary, profile):
    """Bounded recovery: replace the owned Chrome + transport when the pipe died.

    A long-lived pipe dies on owl renderer reload churn (measured 17/11: one
    CDP timeout marks the transport dead forever). Only the holder's OWN child
    is touched — never the operator app — and the lock pid is refreshed so
    diagnostics keep pointing at the live instance.
    """
    old_transport = runtime.get('transport')
    old_proc = runtime.get('proc')
    if old_transport is not None:
        for handle in (old_transport._reader, old_transport._writer):
            try:
                handle.close()
            except OSError:
                pass
    if old_proc is not None:
        terminate_chrome(old_proc, timeout=3)
    transport, proc = launch_cdp_process(binary, build_args(profile),
                                         label='ChatGPT Desktop')
    runtime['transport'] = transport
    runtime['proc'] = proc
    fd = runtime.get('lock_fd')
    if fd is not None:
        try:
            os.pwrite(fd, str(proc.pid).encode('ascii'), 0)
            os.fsync(fd)
        except OSError:
            pass
    return transport


def _holder_dispatch(runtime, binary, profile, payload):
    """Validate, replay or dispatch one request on the holder's LIVE transport.

    A dead pipe or a lagging renderer triggers ONE bounded relaunch of the
    owned Chrome plus a fresh census before the request is refused.
    """
    if not isinstance(payload, dict):
        return _send_receipt() | {'reason': 'invalid_request'}
    prompt = payload.get('prompt')
    request_key = payload.get('request_key')
    if not isinstance(prompt, str) or not prompt.strip():
        return _send_receipt() | {'reason': 'prompt_required'}
    if len(prompt) > MAX_PROMPT_CHARS:
        return _send_receipt() | {'reason': 'prompt_too_large'}
    if request_key is None:
        request_key = hashlib.sha256(prompt.encode('utf-8')).hexdigest()
    if not isinstance(request_key, str) or not REQUEST_KEY.fullmatch(request_key):
        return _send_receipt() | {'reason': 'invalid_request_key'}
    prompt_hash = hashlib.sha256(prompt.encode('utf-8')).hexdigest()
    path = _receipt_path(profile, request_key)
    try:
        existing = _load_receipt(path, prompt_hash)
    except (OSError, ValueError, json.JSONDecodeError):
        return _send_receipt() | {'status': 'incertain', 'attempted': True,
                                  'reason': 'receipt_conflict_or_unreadable'}
    if existing is not None:
        existing['replayed'] = True
        return existing
    # Quick census on the shared transport; one bounded relaunch on trouble.
    transport = runtime.get('transport')
    try:
        state = census(transport)
    except (TimeoutError, EOFError, RuntimeError, OSError, ValueError):
        state = None
    if not isinstance(state, dict) or state.get('status') != 'ready':
        try:
            transport = _holder_relaunch(runtime, binary, profile)
        except (OSError, RuntimeError):
            return _send_receipt() | {'reason': 'holder_not_ready'}
        ready = False
        for _attempt in range(60):
            if runtime['proc'].poll() is not None:
                break
            try:
                state = census(transport)
            except (TimeoutError, EOFError, RuntimeError, OSError, ValueError):
                state = None
            if isinstance(state, dict) and state.get('status') == 'ready':
                ready = True
                break
            time.sleep(0.5)
        if not ready:
            return _send_receipt() | {'reason': 'holder_not_ready'}
    return _dispatch(transport, prompt, profile, request_key, _send_receipt())


def _close_secondary_pages(transport):
    """One-window policy for the dedicated instance.

    owl restores the companion overlay (avatar) window next to the main chat
    window; close every page target that is not the main surface so the
    operator sees exactly one ReChatGPT window.
    """
    closed = 0
    try:
        infos = transport.call('Target.getTargets', {}).get('targetInfos') or []
        for info in infos:
            if info.get('type') == 'page' and info.get('url') != MAIN_URL:
                transport.call('Target.closeTarget', {'targetId': info.get('targetId')})
                closed += 1
    except (TimeoutError, EOFError, RuntimeError, OSError, ValueError):
        return closed
    return closed


def _serve_connection(conn, runtime, binary, profile):
    try:
        frame = bytearray()
        while b'\n' not in frame:
            chunk = conn.recv(65536)
            if not chunk:
                return
            frame.extend(chunk)
            if len(frame) > MAX_PROMPT_CHARS * 4 + 65536:
                break
    except OSError:
        return
    try:
        payload = json.loads(frame.partition(b'\n')[0])
    except (ValueError, UnicodeDecodeError):
        payload = None
    receipt = _holder_dispatch(runtime, binary, profile, payload)
    try:
        conn.sendall((json.dumps(receipt, sort_keys=True, ensure_ascii=False) + '\n')
                     .encode('utf-8'))
    except OSError:
        pass


def serve(binary=None, profile_root=DEFAULT_PROFILE_ROOT,
          name='dedicated', stop=None):
    """Long-lived holder: owns Chrome, the CDP pipe, and serves send requests.

    Chrome is owned for the holder's lifetime and is replaced (bounded relaunch)
    only when its CDP pipe died or the renderer never became ready; the
    operator app is never touched. The pipe constraint (only the launcher may
    use it) is what motivates this process. SIGTERM closes the socket and
    leaves Chrome running.
    """
    profile = profile_path(profile_root, name)
    binary = resolve_binary(binary, name)
    if binary.is_symlink() or not binary.is_file() or not os.access(binary, os.X_OK):
        return {'status': 'refused', 'reason': 'binary_unavailable'}
    _private_directory(profile.parent)
    _private_directory(profile)
    socket_path = _holder_socket_path(profile.parent)
    lock, fd = _claim_lock(profile.parent, name)
    transport = proc = server = None
    try:
        try:
            transport, proc = launch_cdp_process(binary, build_args(profile),
                                                 label='ChatGPT Desktop')
        except (OSError, RuntimeError):
            return {'status': 'refused', 'reason': 'launch_failed'}
        os.write(fd, str(proc.pid).encode('ascii'))
        os.fsync(fd)
        runtime = {'transport': transport, 'proc': proc, 'lock_fd': fd}
        # No `open`/activation here: the owned child registers its app:// page
        # by itself (measured across every 17/11 run), and `open <bundle>`
        # launches or fronts the OPERATOR instance when the bundle id is
        # shared — an operator-visible side effect that never helped census.
        ready = False
        for attempt in range(60):
            try:
                state = census(transport)
            except (TimeoutError, EOFError, RuntimeError):
                state = None
            if isinstance(state, dict) and state.get('status') == 'ready':
                ready = True
                _close_secondary_pages(transport)
                apply_window_title(transport)
                break
            if proc.poll() is not None:
                return {'status': 'refused', 'reason': 'instance_exited'}
            time.sleep(0.5)
        if not ready:
            return {'status': 'refused', 'reason': 'holder_not_ready'}
        if socket_path.exists() or socket_path.is_symlink():
            probe = _holder_connect(socket_path, timeout=0.5)
            if probe is not None:
                probe.close()
                return {'status': 'refused', 'reason': 'holder_already_running'}
            # Nobody answers: orphaned socket file, no live holder holds it.
            socket_path.unlink()
        server = socket_module.socket(socket_module.AF_UNIX, socket_module.SOCK_STREAM)
        server.bind(str(socket_path))
        os.chmod(socket_path, 0o600)
        server.listen(8)
        server.settimeout(0.5)
        if threading.current_thread() is threading.main_thread():
            signal.signal(signal.SIGTERM, _holder_sigterm)
        last_maintenance = 0.0
        while stop is None or not stop.is_set():
            try:
                conn, _ = server.accept()
            except socket_module.timeout:
                # Idle maintenance: owl re-opens the companion overlay after
                # startup and resets document.title on re-render; enforce the
                # one-window ReChatGPT identity while nothing is dispatched.
                if time.monotonic() - last_maintenance >= 10.0:
                    last_maintenance = time.monotonic()
                    _close_secondary_pages(transport)
                    apply_window_title(transport)
                continue
            except OSError:
                break
            with conn:
                _serve_connection(conn, runtime, binary, profile)
        return {'status': 'holder_stopped'}
    finally:
        if server is not None:
            try:
                server.close()
            except OSError:
                pass
        try:
            socket_path.unlink()
        except OSError:
            pass
        if lock is not None:
            _release_lock(lock, fd)
        # Chrome and the transport stay alive by design (holder contract).


def send(prompt, binary=None, profile_root=DEFAULT_PROFILE_ROOT,
         name='default', request_key=None):
    """At-most-once dispatch delegated to the profile's persistent holder.

    send owns no transport: it validates locally, then talks to the holder
    socket (starting the detached holder when absent). Durable receipts are
    the holder's business (same profile folder, single source of truth).
    """
    receipt = _send_receipt()
    binary = resolve_binary(binary, name)
    if not isinstance(prompt, str) or not prompt.strip():
        receipt['reason'] = 'prompt_required'
        return receipt
    if len(prompt) > MAX_PROMPT_CHARS:
        receipt['reason'] = 'prompt_too_large'
        return receipt
    if request_key is None:
        # Repeating identical prompts is intentionally blocked by default.
        request_key = hashlib.sha256(prompt.encode('utf-8')).hexdigest()
    if not isinstance(request_key, str) or not REQUEST_KEY.fullmatch(request_key):
        receipt['reason'] = 'invalid_request_key'
        return receipt
    try:
        profile = profile_path(profile_root, name)
    except ValueError:
        receipt['reason'] = 'invalid_profile'
        return receipt
    socket_path = _holder_socket_path(profile.parent)
    sock = _holder_connect(socket_path, timeout=2.0)
    if sock is None:
        replay = _client_replay(profile, prompt, request_key)
        if replay is not None:
            return replay
        holder = _start_holder_daemon(binary, profile_root, name)
        if holder is None:
            receipt['reason'] = 'holder_spawn_failed'
            return receipt
        for attempt in range(_HOLDER_START_ATTEMPTS):
            sock = _holder_connect(socket_path, timeout=0.5)
            if sock is not None:
                break
            if attempt != _HOLDER_START_ATTEMPTS - 1:
                time.sleep(0.5)
        if sock is None:
            receipt['reason'] = 'holder_unavailable'
            return receipt
    response = _holder_exchange(sock, {'prompt': prompt, 'request_key': request_key})
    if not isinstance(response, dict):
        receipt['reason'] = 'holder_unavailable'
        return receipt
    return response


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
    for cmd in ('inspect', 'probe', 'send', 'serve'):
        obj = sub.add_parser(cmd)
        obj.add_argument('--profile', default='default' if cmd != 'serve' else 'dedicated')
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
            outcome = (inspect(**values) if args.action == 'inspect'
                       else probe(**values) if args.action == 'probe'
                       else serve(binary=args.binary, profile_root=args.profile_root,
                                  name=args.profile))
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
