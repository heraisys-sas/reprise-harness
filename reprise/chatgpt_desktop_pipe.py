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


def dedicated_binary():
    """Binary launched by the ReChatGPT button: the clone when present."""
    return resolve_binary(None, 'dedicated')


def dedicated_profile(profile_root=DEFAULT_PROFILE_ROOT):
    """Named isolated profile reserved for the dedicated clone."""
    return profile_path(profile_root, 'dedicated')


PROFILE_NAME = re.compile(r'[A-Za-z0-9_-]{1,64}\Z')
MAIN_URL = 'app://-/index.html'
# Fixed, read-only probe. Never capture conversation text or authorization tokens.
DIAG_PROBE = r"""(() => {
  const candidates = [];
  const controls = [...document.querySelectorAll(
    'button, [role="tab"], [role="radiogroup"], [role="radio"], ' +
    '[role="button"], [role="group"], [role="segmentedcontrol"]')];
  for (const e of controls) {
    const r = e.getBoundingClientRect();
    if (r.top < 0 || r.top > 400 || r.bottom < 0) continue;
    const style = getComputedStyle(e);
    if (style.display === 'none' || style.visibility === 'hidden' ||
        r.width <= 0 || r.height <= 0) continue;
    const text = (e.innerText || e.getAttribute('aria-label') || '')
      .replace(/\s+/g, ' ').trim();
    const role = e.getAttribute('role');
    if (!/chatgpt|codex/i.test(text) &&
        !['tab', 'radiogroup', 'radio', 'group', 'segmentedcontrol'].includes(role))
      continue;
    const attrs = {};
    for (const a of [...e.attributes]) {
      if (a.name.startsWith('data-'))
        attrs[a.name.slice(0, 80)] = a.value.slice(0, 120);
    }
    candidates.push({
      tag: e.tagName.toLowerCase(), text: text.slice(0, 80),
      role, aria_selected: e.getAttribute('aria-selected'),
      aria_pressed: e.getAttribute('aria-pressed'),
      aria_checked: e.getAttribute('aria-checked'),
      aria_expanded: e.getAttribute('aria-expanded'),
      data: attrs, class: String(e.className || '').slice(0, 120),
      rect: {x: Math.round(r.x), y: Math.round(r.y),
             w: Math.round(r.width), h: Math.round(r.height)}
    });
    if (candidates.length >= 200) break;
  }
  return {href: location.href, title: document.title, candidates};
})()"""

DIAG_MAX_BYTES = 8 * 1024


def _bounded_diag(result):
    """Return a small JSON-safe renderer excerpt, trimming candidates first."""
    if not isinstance(result, dict):
        raise ValueError('invalid_diag')
    diag = {'href': str(result.get('href', ''))[:512],
            'title': str(result.get('title', ''))[:256],
            'candidates': result.get('candidates')
            if isinstance(result.get('candidates'), list) else [],
            'truncated': False}
    while len(json.dumps(diag, ensure_ascii=False,
                         separators=(',', ':')).encode('utf-8')) > DIAG_MAX_BYTES:
        if diag['candidates']:
            diag['candidates'].pop()
            diag['truncated'] = True
        else:
            diag['href'] = diag['href'][:128]
            diag['title'] = diag['title'][:64]
            diag['truncated'] = True
            if len(json.dumps(diag, ensure_ascii=False,
                              separators=(',', ':')).encode('utf-8')) <= DIAG_MAX_BYTES:
                break
            raise ValueError('diag_too_large')
    return diag


def _holder_diag(runtime):
    """Read a bounded, conversation-free excerpt from the unique main page."""
    transport = runtime.get('transport')
    try:
        state = census(transport)
    except (TimeoutError, EOFError, RuntimeError, OSError, ValueError):
        return {'status': 'pipe_unavailable', 'diag': None}
    if not isinstance(state, dict) or state.get('status') != 'ready':
        reason = state.get('status') if isinstance(state, dict) else None
        return {'status': reason or 'holder_not_ready', 'diag': None}
    try:
        result = transport.evaluate(DIAG_PROBE)
        diag = _bounded_diag(result)
        return {'status': 'ok', 'diag': diag}
    except (TimeoutError, EOFError, RuntimeError, OSError, ValueError):
        return {'status': 'diag_failed', 'diag': None}


RENDERER_PROBE = """(() => ({
    href: location.href,
    ready: document.readyState,
    composer_count: [...document.querySelectorAll('textarea, [contenteditable="true"], [role="textbox"]')]
      .filter(e => { const r=e.getBoundingClientRect();
                    return r.width >= 80 && r.height >= 12 && !e.disabled && !e.readOnly; }).length
}))()"""

# Read-only probe of the top-left mode selector trigger (Radix menu trigger,
# per live diag evidence: text "Codex", aria-expanded, data-state). Never a
# toggle: switching requires trigger click + menu item click + re-read.
MODE_PROBE = r"""(() => { /* __reprise_mode_probe */
  const candidates = [];
  const controls = [...document.querySelectorAll(
    'button, [role="button"], [role="tab"], [role="radio"]')];
  for (const e of controls) {
    const r = e.getBoundingClientRect();
    if (r.width <= 0 || r.height <= 0) continue;
    const style = getComputedStyle(e);
    if (style.display === 'none' || style.visibility === 'hidden') continue;
    const text = (e.innerText || e.getAttribute('aria-label') || '')
      .replace(/\s+/g, ' ').trim();
    const label = text.toLowerCase();
    if (label !== 'chatgpt' && label !== 'codex') continue;
    candidates.push({
      tag: e.tagName.toLowerCase(), text: text.slice(0, 80),
      aria_expanded: e.getAttribute('aria-expanded'),
      data_state: e.getAttribute('data-state'),
      rect: {x: Math.round(r.x), y: Math.round(r.y),
             w: Math.round(r.width), h: Math.round(r.height)}
    });
    if (candidates.length >= 20) break;
  }
  return {state: candidates.length ? candidates[0].text : null, candidates};
})()"""

# Realistic pointer sequence for the mode clicks: the Radix menu opens on
# pointerdown, and a plain el.click() dispatches no pointer events — the
# trigger reported "clicked" yet the menu stayed closed (live receipt #248,
# dispatch chatgpt-mode-248-live-a2). Coordinates sit at the element center,
# with an el.click() fallback when PointerEvent is unavailable.
_POINTER_FIRE = r"""  const r = target.getBoundingClientRect();
  const base = {bubbles: true, cancelable: true, composed: true,
                clientX: r.x + r.width / 2, clientY: r.y + r.height / 2,
                button: 0};
  if (typeof PointerEvent === 'function') {
    const pointer = {...base, pointerId: 1, isPrimary: true,
                     pointerType: 'mouse'};
    target.dispatchEvent(new PointerEvent('pointerdown', pointer));
    target.dispatchEvent(new MouseEvent('mousedown', base));
    target.dispatchEvent(new PointerEvent('pointerup', pointer));
    target.dispatchEvent(new MouseEvent('mouseup', base));
    target.dispatchEvent(new MouseEvent('click', base));
  } else {
    target.click();
  }
"""

# Bounded click sequence for the mode switch, evaluated (never CDP input
# methods): click the trigger, wait (bounded poll) for the Radix menu to
# open, then pick the menu item containing ChatGPT. Page-wide surfaces
# (probe, trigger) match the EXACT mode label: a sidebar chip like "Trouver
# une skill UX/UI pour Codex" (live receipt, #248) must never count as a
# candidate or a click target. The menu item stays on the substring match:
# it is role-scoped (menuitem/option) and the mandatory re-read verifies
# the outcome either way.
MODE_TRIGGER_CLICK = (r"""(() => { /* __reprise_mode_trigger */
  const triggers = [...document.querySelectorAll('button, [role="button"]')]
    .filter(e => {
      const text = (e.innerText || e.getAttribute('aria-label') || '')
        .replace(/\s+/g, ' ').trim().toLowerCase();
      return text === 'codex';
    });
  if (triggers.length !== 1) return {clicked: false, reason: 'trigger_not_unique'};
  const target = triggers[0];
""" + _POINTER_FIRE + r"""  return {clicked: true};
})()""")

MODE_ITEM_CLICK = (r"""(() => { /* __reprise_mode_item */
  const items = [...document.querySelectorAll('[role="menuitem"], [role="option"]')]
    .filter(e => /chatgpt/i.test((e.innerText || '').replace(/\s+/g, ' ').trim()));
  if (items.length !== 1) return {clicked: false, reason: 'item_not_unique'};
  const target = items[0];
""" + _POINTER_FIRE + r"""  return {clicked: true};
})()""")

# Bounded poll after the trigger click: the Radix menu renders in a popper
# wrapper and its items may mount a tick after pointerdown. open=false with
# a bounded popper sample feeds the menu_not_opened refusal diag.
MODE_MENU_PROBE = r"""(() => { /* __reprise_mode_menu */
  const items = [...document.querySelectorAll(
    '[role="menuitem"], [role="option"], '
    + '[data-radix-popper-content-wrapper] [role="menuitem"], '
    + '[data-radix-popper-content-wrapper] [role="option"]')];
  const wrapper = document.querySelector('[data-radix-popper-content-wrapper]');
  const sample = {
    items: items.length,
    popper_present: !!wrapper,
    popper_text: wrapper
      ? wrapper.textContent.replace(/\s+/g, ' ').trim().slice(0, 300) : null
  };
  return {open: items.length > 0, sample};
})()"""

MENU_OPEN_TIMEOUT_S = 0.45
MENU_POLL_INTERVAL_S = 0.1


def ensure_chatgpt_mode(transport):
    """Verify (or switch to) pure ChatGPT mode before any irreversible send.

    Reuses the diag conventions: read the trigger state, click through the
    Radix menu when Codex is active (bounded poll for the menu to open),
    then RE-READ; only a positive re-read authorizes the send. Any doubt is
    a refusal carrying bounded candidates.
    """
    def read():
        result = transport.evaluate(MODE_PROBE)
        if not isinstance(result, dict):
            raise ValueError('invalid_mode_probe')
        return result

    def refusal(reason, extra=None):
        try:
            diag = _bounded_diag(read())
            if extra:
                diag.update(extra)
        except (TimeoutError, EOFError, RuntimeError, OSError, ValueError):
            diag = None
        return {'verified': False, 'reason': reason, 'diag': diag}

    def menu_poll():
        """Wait (bounded) for the Radix menu to open after the trigger click."""
        deadline = time.monotonic() + MENU_OPEN_TIMEOUT_S
        while True:
            result = transport.evaluate(MODE_MENU_PROBE)
            if isinstance(result, dict) and result.get('open'):
                return result
            if time.monotonic() >= deadline:
                return result if isinstance(result, dict) else None
            time.sleep(MENU_POLL_INTERVAL_S)

    def classify(result):
        """Trust the probe's already-filtered candidate list; fail closed."""
        candidates = result.get('candidates')
        if not isinstance(candidates, list) or not candidates:
            return None, 'selector_missing', None
        # Defense in depth (#248 live evidence): decide only on candidates
        # whose text IS the mode label, so a substring mention ("Trouver une
        # skill UX/UI pour Codex") can never create a false ambiguity. With
        # no exact label, keep the raw list so the fail-closed reasons below
        # (missing/ambiguous/unknown) stay meaningful.
        exact = [entry for entry in candidates
                 if isinstance(entry, dict) and
                 str(entry.get('text', '')).strip().lower()
                 in ('chatgpt', 'codex')]
        if exact:
            candidates = exact
        if len(candidates) > 1:
            return None, 'selector_ambiguous', None
        entry = candidates[0]
        text = str(entry.get('text', '')).strip() if isinstance(entry, dict) else ''
        label = text.lower()
        if label == 'chatgpt':
            return 'ChatGPT', None, text
        if label == 'codex':
            return 'Codex', None, text
        return None, 'unknown_state', text

    try:
        first = read()
        mode_name, problem, _text = classify(first)
        if problem:
            return refusal(problem)
        if mode_name == 'ChatGPT':
            return {'verified': True, 'mode': 'ChatGPT'}
        trigger = transport.evaluate(MODE_TRIGGER_CLICK)
        if not isinstance(trigger, dict) or not trigger.get('clicked'):
            return refusal('click_no_change')
        # Live dispatch lesson (#248, chatgpt-mode-248-live-a2): a synthetic
        # click does not open the Radix menu; after the pointer-sequence
        # trigger click, poll briefly for the menu items before picking one.
        menu = menu_poll()
        if not isinstance(menu, dict) or not menu.get('open'):
            return refusal('menu_not_opened',
                           {'menu': menu.get('sample')
                            if isinstance(menu, dict) else None})
        item = transport.evaluate(MODE_ITEM_CLICK)
        if not isinstance(item, dict) or not item.get('clicked'):
            return refusal('click_no_change')
        second = read()
        mode_name, problem, _text = classify(second)
        if mode_name == 'ChatGPT':
            return {'verified': True, 'mode': 'ChatGPT'}
        if problem in ('selector_missing', 'unknown_state'):
            return refusal('click_no_change')
        return refusal(problem or 'click_no_change')
    except (TimeoutError, EOFError, RuntimeError, OSError, ValueError) as exc:
        try:
            diag = _bounded_diag(read())
        except (TimeoutError, EOFError, RuntimeError, OSError, ValueError):
            diag = None
        return {'verified': False, 'reason': 'cdp_error',
                'detail': type(exc).__name__, 'diag': diag}


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


def inspect(binary=None, profile_root=DEFAULT_PROFILE_ROOT, name='default'):
    profile = profile_path(profile_root, name)
    binary = resolve_binary(binary, name)
    lock = profile.parent / ('.' + name + '.cdp-pipe.lock')
    return {'status': 'inspect_only', 'transport': 'cdp-pipe',
            'binary_present': binary.is_file() and os.access(binary, os.X_OK),
            'profile_present': profile.is_dir() and not profile.is_symlink(),
            'profile_lock_present': lock.exists() or lock.is_symlink(),
            'launch_attempted': False, 'operator_app_touched': False}


def probe(binary=None, profile_root=DEFAULT_PROFILE_ROOT, name='default'):
    """Launch and stop our own child, never adopt or signal the operator app.

    A second-instance event in Electron could still affect the operator UI;
    this cannot be established from the process launch alone. Coexistence must
    be validated on a real Mac before claiming operator isolation.
    """
    profile = profile_path(profile_root, name)
    binary = resolve_binary(binary, name)
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
const bodyProbe = () => {
  const chromeLabel = /^(?:afficher plus|afficher la réponse|afficher le raisonnement|réflexion en cours|réfléchit|reasoning|thinking)[\s.:…]*$|^durée d'exécution\s*:\s*\d+(?:[.,]\d+)?\s*(?:ms|s|sec|min)\.?$/i;
  const controls = document.querySelectorAll('button, [role="button"], [role="status"], [role="progressbar"]');
  const chrome = new Set([...controls].filter(element =>
    chromeLabel.test((element.innerText || element.getAttribute('aria-label') || '').trim())));
  const read = node => {
    if (node.nodeType === 3) return node.nodeValue;
    if (node.nodeType !== 1 || chrome.has(node) ||
        /^(SCRIPT|STYLE|NOSCRIPT)$/.test(node.tagName)) return '';
    const style = getComputedStyle(node);
    if (style.display === 'none' || style.visibility === 'hidden') return '';
    if (node.tagName === 'BR') return '\n';
    const text = [...node.childNodes].map(read).join('');
    return /^(block|flex|grid|list-item|table)/.test(style.display) ? '\n' + text + '\n' : text;
  };
  // Issue #228: anchored read of the assistant message containers themselves.
  // Only the LAST assistant container is read: concatenating every assistant
  // message welded earlier replies (each ending with the sentinel) into the
  // capture, and the Python cut at the FIRST sentinel then returned a stale
  // reply as confirmed (proven on live dispatchs c2/c3, 20/11/2026).
  const assistantProbe = () => {
    const assistants = [...document.querySelectorAll(
      '[data-message-author-role="assistant"]')];
    const last = assistants.length ? assistants[assistants.length - 1] : null;
    if (last) {
      const text = read(last);
      if (text) return {text: text.slice(-60000), selector:
        '[data-message-author-role="assistant"] (last)'};
    }
    // Fallback: last conversation turn that carries an assistant message.
    const turns = [...document.querySelectorAll(
      '[data-testid^="conversation-turn"]')]
      .filter(turn => turn.querySelector(
        '[data-message-author-role="assistant"]'));
    const turn = turns.length ? turns[turns.length - 1] : null;
    const text = turn ? read(turn) : '';
    return {text: text.slice(-60000), selector: text ?
      '[data-testid^="conversation-turn"] (assistant, last)' : null};
  };
  const assistant = assistantProbe();
  return {url: location.href, ready: document.readyState,
    text: read(document.body).slice(-60000), chrome_seen: chrome.size > 0,
    assistant_text: assistant.text, assistant_selector: assistant.selector,
    stream_len: window.__reprise_stream || 0,
    quiet_ms: window.__reprise_last_mutation ? (Date.now() - window.__reprise_last_mutation) : null};
};
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
# Issue #228 (replaces the fixed 6 s settle): after the ws turn-complete
# event the renderer may still be painting the reply. Keep re-reading the
# DOM ONLY (never re-submit) within this total budget; each re-read is
# spaced by CAPTURE_REREAD_INTERVAL_SECONDS. Stop early once two successive
# reads are identical AND the read passes _usable_reply; a budget exhausted
# without that yields captured_incomplete with the cause preserved.
CAPTURE_REREAD_BUDGET_SECONDS = 8.0
CAPTURE_REREAD_INTERVAL_SECONDS = 0.5
RESPONSE_EXCERPT_CHARS = 1500
MAX_PROMPT_CHARS = 20000
MAX_CAPTURE_CHARS = 58000
# JavaScript slices UTF-16 code units; reserve the full prompt and DOM overhead.
MAX_PROBE_CODE_UNITS = 2 * (MAX_PROMPT_CHARS + MAX_CAPTURE_CHARS + 4096)
SEND_HELPERS = SEND_HELPERS.replace('slice(-60000)',
                                    f'slice(-{MAX_PROBE_CODE_UNITS})')
REPLY_SENTINEL = 'REPRISE-CAPTURE-FIN'
REPLY_LABELS = ('chatgpt a dit', 'chatgpt said')
# Issue #228: rendered chrome lines cut from the captured reply. Matching is
# FULL-LINE ONLY (a line whose content, stripped then lowercased, equals one
# of these labels) — never a substring search, so a reply legitimately
# containing «Auto» (or any label inside a sentence) is never corrupted.
# French labels only, by design of the dedicated fr instance; extend here.
REPLY_CHROME_LINES = (
    'effort de réflexion',
    'auto',
    'réponse terminée',
)
# Footer markers include the «ChatGPT» prefix so the cut drops the whole
# closing sentence, not just its tail.
REPLY_FOOTERS = ('chatgpt peut faire des erreurs', 'chatgpt can make mistakes',
                 'peut faire des erreurs', 'can make mistakes')
# A receipt is only confirmed for a capture that is neither chrome nor a
# fragment. Threshold (issue #228, criterion 2): 80 non-blank chars — the
# known chrome labels together stay below ~70 chars, while typical delegate
# answers exceed 80. Short real replies (< 80 chars) without a sentinel are
# degraded to captured_incomplete: include the sentinel in the prompt to get
# a strict completion proof for a short expected answer.
MIN_USEFUL_REPLY_CHARS = 80
TURN_COMPLETE_MARKER = 'conversation-turn-complete'
# JSON control characters can expand to six ASCII bytes, even with ensure_ascii=False.
MAX_REQUEST_BYTES = 6 * MAX_PROMPT_CHARS + 1024
MAX_RESPONSE_BYTES = 6 * (MAX_CAPTURE_CHARS + RESPONSE_EXCERPT_CHARS) + 16384
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


def _cut_chrome_lines(rest):
    """Cut the reply at the first FULL chrome line (issue #228).

    A chrome label only matches when it forms a complete line of the rendered
    text («Auto» never matches as a substring). Returns (rest, cut_applied).
    """
    lines = rest.split('\n')
    for index, line in enumerate(lines):
        if line.strip().lower() in REPLY_CHROME_LINES:
            return '\n'.join(lines[:index]), True
    return rest, False


def _strip_leading_chrome_lines(rest):
    """Drop the LEADING full chrome lines of a multi-turn segment (issue #228).

    Between the previous sentinel and the last one lies the previous turn's
    chrome block followed by the current echo and reply; the first-chrome
    cut would otherwise keep only the empty prefix before that stale chrome
    block. Blank separator lines around the chrome block are skipped too.
    Returns (rest, stripped).
    """
    lines = rest.split('\n')
    index = 0
    while index < len(lines) and (not lines[index].strip()
                                  or lines[index].strip().lower() in REPLY_CHROME_LINES):
        index += 1
    return '\n'.join(lines[index:]), index > 0


def extract_reply(text, prompt, assistant_text=None):
    """Bounded extraction of the assistant reply from rendered conversation text.

    Anchored on the last occurrence of the prompt echo; cut by the explicit
    sentinel when present, else by the conversation footer. Returns
    (reply or None, anchor); never raises, never returns unbounded text.

    Issue #228: when the probe exposes non-empty assistant_text (the
    assistant message containers, anchored by DOM construction rather than
    by the prompt echo), it is preferred over the raw body text and keeps
    the same cut order (prompt echo when present, sentinel, footers); a
    missing prompt echo is then expected, not an error. Both paths end with
    the full-line chrome cut.

    Issue #236: the third element reports the detected total length of the
    reply before the defensive MAX_CAPTURE_CHARS ceiling, so a truncated
    capture can declare its incompleteness on the receipt instead of
    silently delivering a clipped answer as a complete one.
    """
    raw = assistant_text if assistant_text else text
    dom_anchored = bool(assistant_text)
    # Issue #228: anchor on the LAST sentinel BEFORE the chrome cut. In a
    # multi-turn aggregate the chrome labels of earlier turns precede the
    # current reply, so a chrome cut on the raw text would keep only an
    # earlier turn; between the previous sentinel and the last one lies the
    # current, chronologically last reply (proven on live dispatchs c2/c3,
    # 20/11/2026).
    last = (raw or '').rfind(REPLY_SENTINEL)
    sentinel_seen = last >= 0
    if sentinel_seen:
        previous = raw.rfind(REPLY_SENTINEL, 0, last)
        # Issue #246: a single sentinel is the tail of the prompt echo — the
        # reply has not been rendered yet. The echo keeps the whole user
        # preamble and the sentinel anchor bypasses the usable-length gate,
        # so the capture would confirm the prompt as the reply; the loop
        # must keep polling instead. Only prompts that themselves carry the
        # sentinel qualify: otherwise the single sentinel belongs to the
        # rendered reply, not to the echo.
        marker_probe = _normalize(prompt)
        if (previous < 0 and REPLY_SENTINEL in (prompt or '')
                and marker_probe and marker_probe in _normalize(raw)):
            return None, 'prompt_echo_only', 0
        start = previous + len(REPLY_SENTINEL) if previous >= 0 else 0
        raw = raw[start:last]
        raw, _ = _strip_leading_chrome_lines(raw or '')
        # Issue #246: in a multi-turn aggregate the previous sentinel is the
        # prior turn's reply, so when the current reply has not rendered yet
        # the cut segment is the bare prompt echo (live probe c9): the user
        # chrome label, no assistant label, and NOTHING after the echoed
        # prompt. A rendered reply leaves text after the echo (probes c6/c8;
        # #228 DOM aggregates keep it even without the assistant label), so
        # an empty tail beyond the echo proves the tail sentinel belongs to
        # the echo. Unlike the single-sentinel guard above, the prompt needs
        # no sentinel of its own; its trailing sentinel is excluded from the
        # probe because the segment cut stops right before it.
        segment_probe = _normalize(raw).lower()
        echo_probe = _normalize(prompt).lower().strip()
        sentinel_tail = REPLY_SENTINEL.lower()
        if echo_probe.endswith(sentinel_tail):
            echo_probe = echo_probe[:-len(sentinel_tail)].strip()
        # Issue #246 v3: when the prompt itself carries the sentinel, the
        # rendered echo contains that internal sentinel, so the LAST sentinel
        # sits inside the echoed prompt and the cut segment ends mid-prompt
        # (live probe c11, 20/11/2026): the full-prompt rfind of the guard
        # below can never match because the segment was truncated before the
        # prompt's end. The segment then ends with the prompt prefix before
        # the sentinel — proof the tail sentinel belongs to the echo, not to
        # a rendered reply (a rendered reply leaves ". Question: …" after the
        # internal sentinel, as proven by probe c10).
        # Issue #246 v4 (live probe c14, 20/11/2026): the decisive signature is
        # the mid-prompt cut WITHOUT an assistant label — the user chrome
        # (« vous avez dit ») is NOT required, the c14 double-wrap echo carries
        # none. A rendered reply leaves text after the prompt head before its
        # sentinel, so a segment ending exactly at the head proves the tail
        # sentinel belongs to the echo (live legit reply c10 keeps its anchor).
        prompt_head = (echo_probe[:echo_probe.find(sentinel_tail)].strip()
                       if sentinel_tail in echo_probe else '')
        if (prompt_head
                and not any(label in segment_probe for label in REPLY_LABELS)
                and segment_probe.endswith(prompt_head)):
            return None, 'prompt_echo_only', 0
        echo_position = segment_probe.rfind(echo_probe) if echo_probe else -1
        echo_tail = (segment_probe[echo_position + len(echo_probe):].strip()
                     if echo_position >= 0 else None)
        if ('vous avez dit' in segment_probe
                and not any(label in segment_probe for label in REPLY_LABELS)
                and echo_tail == ''):
            return None, 'prompt_echo_only', 0
    # Issue #228: the full-line chrome cut runs on the RAW rendered text —
    # _normalize flattens newlines, which would weld every chrome label into
    # the reply's own line and make the full-line match impossible.
    raw, chrome_cut = _cut_chrome_lines(raw or '')
    source = _normalize(raw)
    marker = _normalize(prompt)
    position = source.rfind(marker)
    if position < 0:
        if dom_anchored:
            rest = source
            anchor = 'assistant_container'
        else:
            return None, 'prompt_echo_missing', 0
    else:
        rest = source[position + len(marker):]
        anchor = 'end'
    if sentinel_seen:
        anchor = 'sentinel'
    cut = len(rest)
    lower = rest.lower()
    for footer in REPLY_FOOTERS:
        found = lower.find(footer)
        if 0 <= found < cut:
            cut = found
            # A footer inside the kept segment means the current turn never
            # reached its sentinel: the capture is incomplete, not complete.
            anchor = 'cut_before_sentinel' if sentinel_seen else 'footer'
    rest = rest[:cut]
    if chrome_cut and anchor in ('end', 'assistant_container'):
        anchor = 'chrome_line'
    for label in REPLY_LABELS:
        head = rest.lower().find(label)
        if 0 <= head <= 40:
            rest = rest[head + len(label):]
            break
    reply = rest.strip(' :\u00a0-\n\u00a0')
    if not reply:
        return None, anchor, len(rest)
    total = len(reply)
    if len(reply) > MAX_CAPTURE_CHARS:
        reply = reply[:MAX_CAPTURE_CHARS]
    return reply, anchor, total


def _usable_reply(reply, anchor=None):
    if not reply:
        return False, 'no_reply_text'
    if anchor == 'cut_before_sentinel':
        return False, 'capture_cut_before_sentinel'
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
        # the prompt echo, or on the assistant message containers when the
        # probe exposes them (issue #228). Bounded; never a fatal error.
        best = None
        previous = None
        stable_streak = 0
        completed = quiet = sentinel_seen = dom_marker = False
        chrome_seen = False
        anchor_lost = False
        deadline = time.monotonic() + COMPLETION_TIMEOUT_SECONDS
        completed_at = None
        reread_exhausted = False
        capture_reread_passes = 0
        polls = 0
        last = best = None
        last_source = best_source = last_selector = best_selector = None
        while True:
            if drain_turn_completion(transport):
                completed = True
                if completed_at is None:
                    completed_at = time.monotonic()
            probe = transport.evaluate(
                '(() => {\n' + SEND_HELPERS + '\nreturn bodyProbe();\n})()')
            assistant_text = (probe.get('assistant_text')
                              if isinstance(probe, dict) else None)
            if last is not None:
                # Anchoring must survive across polls. Two sources: the
                # assistant containers (issue #228, DOM-anchored) or the
                # body prompt echo (legacy path). Losing both is a real
                # anchor loss; the assistant path merely falling back to
                # the body path is not.
                body_text = probe.get('text') if isinstance(probe, dict) else None
                body_anchored = (isinstance(body_text, str) and
                                 _normalize(prompt) in _normalize(body_text))
                if not (isinstance(assistant_text, str) and assistant_text) \
                        and not body_anchored:
                    anchor_lost = True
                    break
            if isinstance(assistant_text, str) and assistant_text:
                text = assistant_text
                read_source = 'assistant_text'
                read_selector = (probe.get('assistant_selector')
                                 if isinstance(probe, dict) else None)
            else:
                text = probe.get('text') if isinstance(probe, dict) else None
                read_source = 'body_text'
                read_selector = None
            polls += 1
            if isinstance(text, str) and text:
                chrome_seen = chrome_seen or probe.get('chrome_seen') is True
                reply, anchor, reply_total = extract_reply(
                    None, prompt, assistant_text=text) if isinstance(text, str) else (None, 'prompt_echo_missing', 0)
                if reply is not None:
                    # The conversation is append-only: the LAST extraction is
                    # the current final state; the longest seen is the fallback
                    # (junk like the thinking indicator can outsize a short
                    # true reply, so longest is never preferred over last).
                    last = (reply, anchor, reply_total)
                    last_source, last_selector = read_source, read_selector
                if reply is not None and (best is None or len(reply) > len(best[0])):
                    best = (reply, anchor, reply_total)
                    best_source, best_selector = read_source, read_selector
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
            if completed and last is not None:
                # Issue #228: bounded DOM re-read instead of a blind 6 s
                # settle. Re-read ONLY (never re-submit) within
                # CAPTURE_REREAD_BUDGET_SECONDS, spaced by
                # CAPTURE_REREAD_INTERVAL_SECONDS; stop once two successive
                # reads are identical AND the read passes _usable_reply.
                if _usable_reply(last[0], last[1])[0] and stable_streak >= 2:
                    capture_reread_passes = max(1, stable_streak - 1)
                    break
                if time.monotonic() - completed_at >= CAPTURE_REREAD_BUDGET_SECONDS:
                    reread_exhausted = True
                    break
            if time.monotonic() >= deadline:
                break
            time.sleep(CAPTURE_REREAD_INTERVAL_SECONDS if completed
                       else COMPLETION_POLL_INTERVAL_SECONDS)
        if drain_turn_completion(transport):
            completed = True
        status = _evaluate_send(transport, 'composerStatus', None)
        receipt['composer_emptied'] = isinstance(status, dict) and status.get('status') == 'ready'
        chosen = last if last is not None else best
        chosen_source = last_source if last is not None else best_source
        chosen_selector = last_selector if last is not None else best_selector
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
            capture_truncated = anchor_lost or reply_total > len(reply)
            if capture_truncated:
                # A truncated capture is never a complete reply, whatever the
                # completion evidence: the delegated result must be reviewed,
                # not consumed as the answer.
                usable = False
                unusable_reason = 'capture_anchor_lost' if anchor_lost else 'capture_truncated'
            elif reread_exhausted and usable:
                # Issue #228: the re-read budget ended without two identical
                # usable reads; a usable-but-unstable capture never confirms.
                # Secondary to the truncation diagnosis above and to any
                # specific reason returned by _usable_reply.
                usable = False
                unusable_reason = 'capture_reread_budget_exhausted'
            # Issue #228 criterion 2/5: chrome or a too-short capture is never
            # a complete reply, and turn_completed: false (a1: no ws event, no
            # sentinel, no DOM marker, DOM silent) never yields
            # response_complete: true even when some text was read.
            completion_evidence = bool(completed or quiet or sentinel_seen
                                       or dom_marker)
            receipt['response_complete'] = bool(completion_evidence and usable)
            if reread_exhausted:
                # Issue #228: bounded re-read ended without a stable usable
                # read; keep the diagnosis on the receipt (status is handled
                # below through the unusable/complete gates, never confirmed).
                receipt['capture_reread_exhausted'] = True
                receipt['capture_reread_budget_seconds'] = CAPTURE_REREAD_BUDGET_SECONDS
            # Issue #228: which DOM source produced the retained capture —
            # the assistant containers when the probe exposed them, else the
            # body text anchored on the prompt echo. Budget exhaustion is
            # signaled by capture_reread_exhausted, not by a source label.
            receipt['capture_source'] = chosen_source
            if chosen_selector:
                receipt['assistant_selector'] = chosen_selector
            receipt['capture_reread_passes'] = capture_reread_passes
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
                if anchor_lost:
                    receipt['capture_anchor_lost'] = True
                else:
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
            # truncated capture lands here too (usable was flipped to False),
            # as does a re-read budget exhausted without a stable usable read
            # (the gate above keeps the more specific cause when one exists).
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


def ensure_main_target(transport, *, timeout):
    """Census loop, then create the main app:// page when it never appears.

    Returns the last census outcome with a 'verified' flag. Success requires
    exactly one MAIN_URL page observed by census; an ambiguous page set is a
    failure, never a success.
    """
    deadline = time.monotonic() + timeout
    outcome = {'status': 'target_missing', 'ready': False}
    while True:
        outcome = census(transport)
        if outcome['status'] != 'target_missing':
            break
        if time.monotonic() >= deadline:
            break
        time.sleep(0.25)
    if outcome['status'] == 'ready':
        return outcome | {'verified': True}
    if outcome['status'] != 'target_missing':
        return outcome | {'verified': False}
    # The dedicated Electron renderer may register its app:// page later;
    # create it explicitly (same fallback as the Claude bridge, proven live).
    try:
        created = transport.call('Target.createTarget', {'url': MAIN_URL})
    except (EOFError, RuntimeError, OSError, ValueError) as exc:
        return outcome | {'verified': False, 'detail': 'createTarget_failed:' + type(exc).__name__}
    if not isinstance(created, dict) or not isinstance(created.get('targetId'), str) or not created['targetId']:
        return outcome | {'verified': False, 'detail': 'createTarget_unidentified'}
    while time.monotonic() < deadline:
        time.sleep(0.5)
        outcome = census(transport)
        if outcome['status'] == 'ready':
            return outcome | {'verified': True}
        if outcome['status'] != 'target_missing':
            break
    return outcome | {'verified': False}


def _holder_ensure_result(response):
    """Map an 'ensure' reply to the launch_rechatgpt outcome contract."""
    if not isinstance(response, dict):
        return {'status': 'error', 'verified': False, 'pid': None,
                'detail': 'holder_unavailable'}
    if response.get('status') == 'ready':
        return {'status': 'already_active', 'verified': True, 'pid': None,
                'detail': 'holder_listening'}
    if response.get('status') == 'holder_not_ready':
        return {'status': 'error', 'verified': False, 'pid': None,
                'detail': 'holder_chrome_unrecoverable'}
    return {'status': 'error', 'verified': False, 'pid': None,
            'detail': 'holder_unavailable'}


def launch_rechatgpt(*, timeout=30.0, profile_root=DEFAULT_PROFILE_ROOT):
    """Launch the ReChatGPT clone on the ChatGPT screen, or report the holder.

    Holder liveness is checked by connect() only — an orphaned socket file is
    a stopped holder. A listening holder is then asked 'ensure': the holder
    may have outlived its Chrome, and it recovers (bounded relaunch) or
    admits the loss before we report already_active. Under the profile lock,
    a second probe rules out the holder racing us, then the resolved clone
    binary is launched with its dedicated profile over
    --remote-debugging-pipe (never TCP). Our local transport fds are closed
    without terminating the child: the launched app stays alive by design,
    exactly like the holder's own serve() contract.

    'launched' never means the ChatGPT screen is shown; only a census with
    exactly one MAIN_URL page sets verified=True.
    """
    profile = dedicated_profile(profile_root)
    socket_path = _holder_socket_path(profile.parent)
    holder = _holder_connect(socket_path, timeout=2.0)
    if holder is not None:
        # _holder_exchange raises the socket timeout to
        # HOLDER_EXCHANGE_TIMEOUT_SECONDS itself and closes the socket in
        # every path, so the 2.0 connect timeout never bounds the exchange.
        try:
            response = _holder_exchange(holder, {'cmd': 'ensure'})
        finally:
            try:
                holder.close()
            except OSError:
                pass
        return _holder_ensure_result(response)
    binary = dedicated_binary()
    if binary.is_symlink() or not binary.is_file() or not os.access(binary, os.X_OK):
        return {'status': 'error', 'verified': False, 'pid': None,
                'detail': 'binary_unavailable:' + str(binary)}
    _private_directory(profile.parent)
    _private_directory(profile)
    lock = fd = transport = proc = None
    try:
        try:
            lock, fd = _claim_lock(profile.parent, 'dedicated')
        except FileExistsError:
            return {'status': 'error', 'verified': False, 'pid': None,
                    'detail': 'lock_busy'}
        holder = _holder_connect(socket_path, timeout=1.0)
        if holder is not None:
            try:
                response = _holder_exchange(holder, {'cmd': 'ensure'})
            finally:
                try:
                    holder.close()
                except OSError:
                    pass
            return _holder_ensure_result(response)
        try:
            transport, proc = launch_cdp_process(binary, build_args(profile), label='ReChatGPT')
        except (OSError, RuntimeError) as exc:
            return {'status': 'error', 'verified': False, 'pid': None,
                    'detail': 'launch_failed:' + type(exc).__name__}
        os.write(fd, str(proc.pid).encode('ascii'))
        os.fsync(fd)
        if proc.poll() is not None:
            return {'status': 'error', 'verified': False, 'pid': proc.pid,
                    'detail': f'exited_immediately_code_{proc.returncode}'}
        try:
            outcome = ensure_main_target(transport, timeout=timeout)
        except (TimeoutError, EOFError, RuntimeError, OSError, ValueError) as exc:
            return {'status': 'error', 'verified': False, 'pid': proc.pid,
                    'detail': 'pipe_unavailable:' + type(exc).__name__}
        status = 'launched' if outcome.get('verified') else 'launched_screen_unverified'
        return {'status': status, 'verified': outcome.get('verified') is True,
                'pid': proc.pid, 'census_status': outcome.get('status'),
                'detail': outcome.get('detail', 'ok')}
    finally:
        # Keep the child running: close our pipe ends without terminate_chrome.
        if transport is not None:
            transport.close()
        if lock is not None:
            _release_lock(lock, fd)


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


def _encode_holder_frame(payload):
    return (json.dumps(payload, sort_keys=True, ensure_ascii=False) + '\n').encode('utf-8')


def _read_holder_frame(sock, limit):
    frame = bytearray()
    while True:
        chunk = sock.recv(min(65536, limit + 1 - len(frame)))
        if not chunk:
            raise EOFError('Incomplete holder frame')
        frame.extend(chunk)
        if len(frame) > limit:
            raise ValueError('Holder frame exceeds byte limit')
        if b'\n' in frame:
            return json.loads(frame.partition(b'\n')[0])


def _holder_exchange(sock, payload):
    try:
        frame = _encode_holder_frame(payload)
        if len(frame) > MAX_REQUEST_BYTES:
            return None
        sock.settimeout(HOLDER_EXCHANGE_TIMEOUT_SECONDS)
        sock.sendall(frame)
        return _read_holder_frame(sock, MAX_RESPONSE_BYTES)
    except (OSError, EOFError, ValueError):
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


def _holder_ensure(runtime, binary, profile):
    """Answer the 'ensure' command: is the owned Chrome actually alive?

    A holder can outlive its Chrome (window closed, pipe dead), so a listening
    socket proves nothing. One bounded relaunch of the OWN child mirrors
    _holder_dispatch's recovery before 'holder_not_ready' is answered.
    """
    transport = runtime.get('transport')
    try:
        state = census(transport)
    except (TimeoutError, EOFError, RuntimeError, OSError, ValueError):
        state = None
    if isinstance(state, dict) and state.get('status') == 'ready':
        return {'status': 'ready', 'verified': True}
    try:
        transport = _holder_relaunch(runtime, binary, profile)
    except (OSError, RuntimeError):
        return {'status': 'holder_not_ready', 'verified': False}
    for _attempt in range(60):
        if runtime['proc'].poll() is not None:
            break
        try:
            state = census(transport)
        except (TimeoutError, EOFError, RuntimeError, OSError, ValueError):
            state = None
        if isinstance(state, dict) and state.get('status') == 'ready':
            return {'status': 'ready', 'verified': True}
        time.sleep(0.5)
    return {'status': 'holder_not_ready', 'verified': False}


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
    # Issue #248 LOT 2: pure ChatGPT mode is verified (or switched to) before
    # any irreversible send; without a positive re-read, never dispatch.
    receipt = _send_receipt()
    mode = ensure_chatgpt_mode(transport)
    if not mode.get('verified'):
        receipt.update(reason=mode.get('reason') or 'unknown_state',
                       mode_diag=mode.get('diag'))
        return receipt
    receipt['mode'] = mode['mode']
    receipt['mode_verified'] = True
    return _dispatch(transport, prompt, profile, request_key, receipt)


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
        payload = _read_holder_frame(conn, MAX_REQUEST_BYTES)
    except (OSError, EOFError):
        return
    except ValueError:
        return
    if isinstance(payload, dict) and payload.get('cmd') == 'ensure' \
            and 'prompt' not in payload:
        receipt = _holder_ensure(runtime, binary, profile)
    elif isinstance(payload, dict) and payload.get('cmd') == 'diag' \
            and 'prompt' not in payload:
        receipt = _holder_diag(runtime)
    else:
        receipt = _holder_dispatch(runtime, binary, profile, payload)
    try:
        frame = _encode_holder_frame(receipt)
        if len(frame) > MAX_RESPONSE_BYTES:
            return
        conn.sendall(frame)
    except (OSError, ValueError):
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
        # Resolved at usage time (resolve_binary): None keeps the clone
        # preference alive for the dedicated profile (#254).
        obj.add_argument('--binary', type=Path, default=None)
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
