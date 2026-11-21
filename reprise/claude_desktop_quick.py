#!/usr/bin/env python3
"""Local Quick Entry supervision server (start-at-login, in the Codex server spirit).

Accepts a validated Quick Apply payload and hands it to Claude Desktop's Quick
Apply submission contract. It binds a private UNIX socket, speaks newline
delimited JSON-RPC. Orchestrated dispatch launches Chrome over CDP pipes owned
by this daemon, attached to the admitted claude.ai chat: one prepare, one
fingerprint check, one click — never a retry after a lost response, never a
response read. This server owns payload validation, target routing and the
bounded admission contract so the harness can treat it as an external-expert
channel.
"""
from app_paths import state_location
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import signal
import socket
import sqlite3
import weakref
import stat
import threading
import time
import uuid

import claude_desktop
import claude_desktop_apply
import cdp_targets
from claude_desktop import build_payload, validate_chat_id
from delegation_store import Store, file_evidence

SERVER_NAME = 'reprise-claude-desktop-quick'
VERSION = Path(__file__).with_name('VERSION').read_text().strip()
RELAY_PROTOCOL = 'reprise-relay-1'


def task_pointer(request_key):
    """Composeur text handed to the chat: get_task is the single source of the full prompt.

    The full prompt stays in the TaskStore (claim/attempt_id traceability);
    pushing it in the composer too made the chat receive the instruction twice.
    """
    return ('A Reprise task is waiting in the configured project. Persist a claim_key, then call '
            'get_task with request_key=' + json.dumps(request_key) +
            '. Follow your session instructions, perform that scoped task, and call submit_result '
            'with the returned attempt_id. If already completed, retrieve the existing result instead.')


class RelayHub:
    """Connexion relais Quick Apply (injection ReClaude) tenue ouverte.

    Le daemon est l'unique détenteur du socket ; le relais se connecte, s'annonce
    (greeting role=relay) et reçoit les payloads admis sur cette connexion.
    Dernier connecté gagne ; aucune reconnexion côté daemon (le relais revient).
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._conn = None
        self._stream = None
        self._push_id = 0
        self.last_result = None

    def register(self, conn, stream):
        with self._lock:
            previous = self._conn
            self._conn, self._stream = conn, stream
        if previous is not None and previous is not conn:
            try:
                previous.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass

    def unregister(self, conn):
        with self._lock:
            if self._conn is conn:
                self._conn = self._stream = None

    def connected(self):
        with self._lock:
            return self._conn is not None

    def push(self, params):
        """Pousse un payload admis sur la connexion relais ; None si absente."""
        with self._lock:
            if self._stream is None:
                return None
            self._push_id += 1
            ident = self._push_id
            try:
                self._stream.write((json.dumps({'jsonrpc': '2.0', 'id': ident,
                                                'method': 'relay/dispatch',
                                                'params': params}, ensure_ascii=False) + '\n').encode())
                self._stream.flush()
            except (OSError, ValueError):
                self._conn = self._stream = None
                return None
            return ident

    def record_result(self, message):
        with self._lock:
            self.last_result = message


def _cdp_pipe():
    import claude_cdp_pipe as mod
    return mod


class Endpoint:
    """Bounded admission and payload hand-off for Quick Entry submissions."""

    def __init__(self, state, daily_limit=8, project_root=None):
        state = Path(state)
        state.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.state = state
        self.db = sqlite3.connect(state/'quick.sqlite', timeout=2)
        weakref.finalize(self, type(self)._safe_close, self.db)
        self.db.execute('CREATE TABLE IF NOT EXISTS quick_submissions(scope TEXT,id TEXT,payload TEXT,day INTEGER,PRIMARY KEY(scope,id))')
        self.db.execute('CREATE TABLE IF NOT EXISTS quick_calls(scope TEXT,day INTEGER,count INTEGER,PRIMARY KEY(scope,day))')
        self.db.commit()
        self.db.execute('CREATE TABLE IF NOT EXISTS quick_identity(scope TEXT NOT NULL)')
        identities = {row[0] for row in self.db.execute('SELECT scope FROM quick_identity')}
        identities.update(row[0] for row in self.db.execute('SELECT DISTINCT scope FROM quick_submissions'))
        if self.db.execute("SELECT 1 FROM sqlite_master WHERE name='delegation_requests'").fetchone():
            identities.update(row[0] for row in self.db.execute('SELECT DISTINCT scope FROM delegation_requests')
                              if not row[0].startswith('quick-project-v1:'))
        if len(identities) > 1:
            self.db.close()
            raise ValueError('Identités Quick Apply multiples : réconciliation explicite requise.')
        self.scope = next(iter(identities), str(state.resolve()))
        if not self.db.execute('SELECT 1 FROM quick_identity').fetchone():
            self.db.execute('INSERT INTO quick_identity VALUES (?)', (self.scope,))
        self.db.commit()
        request_scope = self.scope
        if project_root is not None:
            project = str(Path(project_root).resolve(strict=True))
            request_scope = 'quick-project-v1:' + hashlib.sha256((self.scope + '\0' + project).encode()).hexdigest()
        self.requests = Store(self.db, request_scope)
        self.relay = RelayHub()
        self.tasks = None
        if project_root is not None:
            from desktop_task_store import TaskStore
            self.tasks = TaskStore(state, project_root)

    @staticmethod
    def _safe_close(connection):
        try:
            connection.close()
        except sqlite3.Error:
            pass  # finalized from another thread than the one that created it

    def close(self):
        if self.tasks:
            self.tasks.close()
        self.db.close()

    def submit(self, args):
        """Validate, bound and record a submission; returns the routed payload."""
        if not isinstance(args, dict):
            raise ValueError('Objet de soumission requis')
        allowed = {'prompt', 'images', 'filePaths', 'chatId', 'request_key', 'parent_request_key', 'purpose', 'orchestrated'}
        if args.keys() - allowed:
            raise ValueError('Champs de soumission non pris en charge')
        orchestrated = bool(args.get('orchestrated'))
        purpose = args.get('purpose', 'delegation')
        if purpose not in ('delegation', 'continuation'):
            raise ValueError('Invalid submission purpose')
        payload = build_payload(args.get('prompt'), images=args.get('images'),
                                file_paths=args.get('filePaths'), chat_id=args.get('chatId'))
        if self.tasks:
            if payload['images'] or payload['filePaths']:
                raise ValueError('Scoped Desktop inbox supports text snapshots only; attachments require an explicit adapter')
            from desktop_task_store import bounded
            bounded(payload['text'], 'prompt', 12000)
        identity = dict(payload, file_evidence=file_evidence(self.state, file_paths=payload['filePaths']))
        if purpose == 'continuation':
            identity['purpose'] = purpose
        existing = self.requests.begin(args.get('request_key'), identity, args.get('parent_request_key'))
        if existing:
            return existing
        delivery = payload
        if self.tasks and purpose == 'delegation' and not orchestrated:
            delivery = dict(payload, text=task_pointer(args['request_key']))
        day = int(time.time() // 86400)
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            ident = str(uuid.uuid4())
            self.db.execute('INSERT INTO quick_submissions VALUES (?,?,?,?)',
                            (self.scope, ident, json.dumps(delivery, ensure_ascii=False), day))
        if self.tasks and purpose == 'delegation':
            self.tasks.admit(args['request_key'], payload['text'], payload['chatId'])
        receipt = self.requests.finish(args['request_key'], {'status': 'finished', 'submission_id': ident, 'chatId': payload['chatId'],
                'images': len(payload['images']), 'files': len(payload['filePaths']),
                'dispatch': 'dispatchOnQuickEntrySubmit',
                'reason': 'Payload validé et routé ; la soumission réelle dépend du pilote Desktop autorisé.',
                'automatic_fallback': False, 'admission_only': True, 'dispatch_attempted': False,
                'task_pre_registered': bool(orchestrated and self.tasks),
                'task_available': self.tasks is not None and purpose == 'delegation', 'purpose': purpose})
        if self.relay.connected():
            # Canal relais additionnel : la réception reste durable (admission),
            # la preuve d'envoi reste la chaîne native Quick Apply. Same pointer
            # rule as the composer (#155): the full prompt travels via get_task.
            pushed = self.relay.push({'request_key': args.get('request_key'),
                                      'prompt': delivery['text'], 'chatId': payload['chatId']})
            receipt['relay_pushed'] = pushed is not None
        return receipt

    def _dispatch(self, request_key):
        """Dispatch once over CDP pipes after durable admission and reservation.

        Unknown request_key never dispatches. The failure is logged
        (dispatch_attempted + ok/error/stage) and returned as-is; no retry, no
        fallback, and admission-only remains the recorded outcome. The composer
        receives the task pointer only: the full prompt is delivered by
        get_task (#155).
        """
        row = self.tasks.db.execute(
            'SELECT prompt,chat_id FROM desktop_tasks WHERE scope=? AND request_key=?',
            (self.tasks.scope, request_key)).fetchone()
        if not row:
            raise ValueError('Unknown request_key; no dispatch')
        chat_id = row[1]
        result = {'request_key': request_key, 'dispatch_attempted': True,
                  'dispatch_channel': 'cdp-pipe', 'admission_only': True}
        reservation = self.tasks.reserve_socket_dispatch(request_key)
        if reservation['state'] == 'recorded':
            return reservation['receipt']
        if reservation['state'] != 'reserved':
            return result | {'dispatch_ok': False, 'stage': 'incertain',
                             'dispatch_error': 'Dispatch antérieur ou historique incertain ; aucune relance.'}
        outcome = self._dispatch_reserved(result, task_pointer(request_key), chat_id)
        self.tasks.record_socket_dispatch(request_key, outcome)
        return outcome

    def _dispatch_reserved(self, result, prompt, chat_id):
        """Dispatch once over the daemon-owned Chrome CDP pipes, no retry.

        One prepare, one fingerprint check, one click. Chrome is terminated
        only on a certain failure that predates the click; a possible click
        (envoyé without click_observed, incertain) preserves the Chrome
        process so the outcome can be observed.
        """
        if not chat_id or not validate_chat_id(chat_id):
            return result | {'dispatch_ok': False, 'dispatch_error': 'chat_id invalide ou absent',
                             'stage': 'cible'}
        cdp_mod = _cdp_pipe()
        profile = cdp_mod._profile_dir()
        lock_path, _ = cdp_mod._hostname_pid_lock(profile)
        busy = cdp_mod.check_profile_busy(lock_path)
        if busy is not None:
            return result | {'dispatch_attempted': False, 'dispatch_ok': False,
                             'stage': 'profile_busy',
                             'dispatch_error': 'Profil Chrome occupé par un autre propriétaire ; aucune tentative.'}
        if not Path(cdp_mod._chrome_bin()).exists():
            return result | {'dispatch_ok': False, 'stage': 'incertain',
                             'dispatch_error': 'Binaire Chrome introuvable'}
        try:
            transport, proc = cdp_mod.launch_chrome(profile)
        except RuntimeError as exc:
            return result | {'dispatch_ok': False, 'stage': 'incertain',
                             'dispatch_error': str(exc) or 'launch_chrome échoué'}
        path = '/chat/' + chat_id
        try:
            try:
                transport.pick_and_attach(chat_id)
            except RuntimeError:
                # Cold start: launch_chrome opens only https://claude.ai; the
                # admitted chat page may not exist yet. Create the target
                # explicitly, then pick once again (still exactly one attach).
                transport.call('Target.createTarget',
                               {'url': 'https://claude.ai' + path})
                # The new target takes a moment to register and load; poll
                # briefly instead of picking immediately (cold-start race).
                attached = None
                for _ in range(20):
                    try:
                        attached = transport.pick_and_attach(chat_id)
                        break
                    except RuntimeError:
                        time.sleep(0.5)
                if attached is None:
                    # Diagnostic: report the actual open targets so a redirect
                    # or a silent createTarget failure is visible in the receipt.
                    try:
                        infos = transport.call('Target.getTargets', {}).get('targetInfos', [])
                        seen = [str(i.get('url', '')) for i in infos if i.get('type') == 'page']
                    except Exception as diag_error:
                        seen = [f'<getTargets échoué: {diag_error}>']
                    raise RuntimeError(
                        f'Page claude.ai/chat/{chat_id} introuvable après createTarget ; cibles: {seen}')
            if not claude_desktop_apply.wait_composer(transport):
                cdp_mod.terminate_chrome(proc)
                return result | {'dispatch_ok': False, 'stage': 'composeur',
                                 'dispatch_error': 'Composeur indisponible'}
            info = claude_desktop_apply.state(transport)
            check = claude_desktop_apply._check(info, path)
            if check:
                cdp_mod.terminate_chrome(proc)
                return result | {'dispatch_ok': False, 'stage': check,
                                 'dispatch_error': check}
            if not claude_desktop_apply._prepare(transport, path):
                cdp_mod.terminate_chrome(proc)
                return result | {'dispatch_ok': False, 'stage': 'composeur',
                                 'dispatch_error': 'Préparation du composeur échouée'}
            # Fingerprint over the shared canonical form (imported from
            # claude_desktop_apply): the same representation is hashed here,
            # in apply() and in the in-page insertion checks, so multi-line
            # prompts (blank lines, CRLF, NBSP) no longer fail the empreinte.
            fingerprint = hashlib.sha256(
                claude_desktop_apply.canonical_text(prompt.strip()).encode()).hexdigest()
            # Event-driven insertion (execCommand + bubbling input), not a
            # plain innerText assignment: React must observe the input.
            inserted = claude_desktop_apply._insert(transport, prompt.strip())
            if not isinstance(inserted, dict) or inserted.get('inserted') is not True:
                cdp_mod.terminate_chrome(proc)
                return result | {'dispatch_ok': False, 'stage': 'composeur',
                                 'dispatch_error': 'Insertion dans le composeur non confirmée'}
            try:
                outcome = claude_desktop_apply._dispatch(transport, path, fingerprint)
            except (OSError, ValueError, claude_desktop_apply.WSError, TimeoutError):
                # A lost response may conceal a click: preserve Chrome, never retry.
                return result | {'dispatch_ok': False, 'stage': 'incertain',
                                 'dispatch_error': 'Réponse CDP perdue au clic'}
            if outcome.get('stage') == 'envoyé' and outcome.get('click_observed') is True:
                return result | {'dispatch_ok': True, 'stage': 'envoyé',
                                 'click_observed': True}
            if outcome.get('stage') == 'incertain' or outcome.get('click_observed') is not False:
                return result | {'dispatch_ok': False, 'stage': 'incertain',
                                 'dispatch_error': 'Résultat de clic incertain ; Chrome préservé'}
            stage = outcome.get('stage') or 'incertain'
            # Certain failure before the click: terminate the daemon-owned Chrome.
            cdp_mod.terminate_chrome(proc)
            return result | {'dispatch_ok': False, 'stage': stage,
                             'dispatch_error': outcome.get('dispatch_error') or stage}
        except Exception as exc:  # a daemon crash must never replace the receipt
            return result | {'dispatch_ok': False, 'stage': 'incertain',
                             'dispatch_error': str(exc) or type(exc).__name__}

    def call(self, name, args):
        if not isinstance(args, dict):
            raise ValueError('Objet JSON-RPC requis')
        if name == 'initialize':
            return {'protocolVersion': '2024-11-05', 'capabilities': {'tools': {}},
                    'serverInfo': {'name': SERVER_NAME, 'version': VERSION}}
        if name == 'quick_entry/capabilities':
            if args:
                raise ValueError('No capability arguments expected')
            # Dispatch is daemon-owned CDP over pipes; capability is not proof
            # of a connected browser or a successful click.
            chrome_present = Path(_cdp_pipe()._chrome_bin()).is_file()
            relay_connected = self.relay.connected()
            supported = self.tasks is not None and chrome_present
            if self.tasks is None:
                reason = 'Project-scoped task store required for dispatch.'
            elif not chrome_present:
                reason = 'Chrome binary absent.'
            elif not relay_connected:
                reason = 'Relais Quick Apply indisponible ; admission seule.'
            else:
                reason = 'Chrome launches at dispatch; session and target are not verified.'
            return {'version': VERSION, 'admission_supported': True,
                    'dispatch_supported': supported,
                    'dispatch_channel': 'cdp-pipe',
                    'resume_validation_supported': relay_connected,
                    'task_return_supported': self.tasks is not None,
                    'cdp_targets': cdp_targets.enabled_names(),
                    'project_root': self.tasks.scope if self.tasks else None,
                    'state_sha256': hashlib.sha256(str(self.state.resolve()).encode()).hexdigest(),
                    'reason': reason,
                    'cdp_resume_supported': chrome_present}
        if name == 'quick_entry/dispatch':
            if not self.tasks:
                raise ValueError('Project-scoped dispatch required; unknown request_key gets no dispatch')
            if args.keys() - {'request_key'} or not isinstance(args.get('request_key'), str):
                raise ValueError('request_key only; images and filePaths are refused on this channel')
            return self._dispatch(args['request_key'])
        if name == 'quick_entry/get_result':
            if not self.tasks or args.keys() - {'request_key', 'acknowledge', 'view', 'offset', 'max_chars', 'result_sha256'} or 'request_key' not in args:
                raise ValueError('Project-scoped result lookup required')
            return self.tasks.result(**args)
        if name == 'quick_entry/list_tasks':
            if not self.tasks or args.keys() - {'limit', 'cursor'}:
                raise ValueError('Project-scoped task listing required')
            return self.tasks.list_tasks(**args)
        if name == 'ping':
            return {}
        if name == 'quick_entry/requests':
            return self.requests.lookup(args)
        if name == 'quick_entry/submit':
            return self.submit(args)
        if name == 'cdp/status':
            return self._cdp_status()
        if name == 'cdp/resume':
            return self._cdp_resume(args)
        if name == 'shutdown':
            return self._shutdown(args)
        raise ValueError('Méthode Quick Apply non prise en charge')

    # -- CDP methods ---------------------------------------------------------

    def _cdp_status(self):
        """Passive CDP status: returns running:false if no Chrome process."""
        cdp = _cdp_pipe()
        profile = cdp._profile_dir()
        lock_path, _ = cdp._hostname_pid_lock(profile)
        busy = cdp.check_profile_busy(lock_path)
        targets = {}
        for name in sorted(cdp_targets._registry()):
            config = cdp_targets.load(name)
            if config is None:
                continue
            ready = busy is None and Path(cdp._chrome_bin()).exists()
            targets[name] = {'enabled': config['enabled'], 'ready': ready}
        if busy is not None:
            return {'ok': False, 'status': 'profile_busy', 'pid': busy['pid'],
                    'lock_mtime': busy['lock_mtime'], 'targets': targets}
        if not Path(cdp._chrome_bin()).exists():
            return {'ok': False, 'status': 'chrome_missing', 'targets': targets}
        return {'ok': True, 'status': 'idle',
                'cdp_resume_supported': Path(_cdp_pipe()._chrome_bin()).exists(),
                'targets': targets}

    def _cdp_resume(self, args):
        """Resume via CDP pipe: spawn lazy, target unique page, deposit+click."""
        if not isinstance(args, dict):
            raise ValueError('Objet de soumission CDP requis')
        chat_id = args.get('chat_id') or args.get('chatId')
        prompt = args.get('prompt')
        target = args.get('target')
        target_name = target if isinstance(target, str) and target else 'claude-web'
        try:
            chat_uuid = validate_chat_id(chat_id)
        except ValueError:
            chat_uuid = None
        if not chat_id or not chat_uuid:
            return {'ok': False, 'status': 'target_missing', 'reason': 'chat_id invalide ou absent'}
        if not prompt or len(prompt.strip()) < 2:
            return {'ok': False, 'status': 'paramètres', 'reason': 'prompt requis (≥2 caractères)'}
        config = cdp_targets.load(target_name)
        if config is None or not config['enabled']:
            # Route gate BEFORE any transport touch: all targets ship disabled.
            return {'ok': False, 'status': 'route_disabled',
                    'reason': f"cible '{target_name}' absente ou désactivée"}
        max_prompt_chars = config['max_prompt_chars']
        if len(prompt) > max_prompt_chars:
            return {'ok': False, 'status': 'paramètres',
                    'reason': f'prompt trop long (> {max_prompt_chars})'}

        cdp_mod = _cdp_pipe()
        profile_name = config['profile']
        # Default profile: arg-less call keeps the historical path and lock name.
        profile = cdp_mod._profile_dir(None if profile_name in (None, '', 'chrome') else profile_name)
        lock_path, _ = cdp_mod._hostname_pid_lock(profile)

        busy = cdp_mod.check_profile_busy(lock_path)
        if busy is not None:
            return {'ok': False, 'status': 'profile_busy', 'pid': busy['pid'],
                    'lock_mtime': busy['lock_mtime']}

        if not Path(cdp_mod._chrome_bin()).exists():
            return {'ok': False, 'status': 'chrome_missing'}

        try:
            transport, proc = cdp_mod.launch_chrome(profile, origin=config['origin'])
        except RuntimeError as exc:
            return {'ok': False, 'status': 'session', 'reason': str(exc)}

        origin = config['origin']
        chat_path = config['chat_url_pattern'].replace('{chat_id}', chat_uuid)
        try:
            try:
                # Single pick-and-attach: getTargets → select /chat/<uuid> → attachToTarget flatten
                transport.pick_and_attach(chat_uuid)
            except RuntimeError:
                # Cold start: launch_chrome opens only the target origin; create
                # the chat target explicitly, then poll briefly before picking
                # (the new target takes a moment to register and load).
                transport.call('Target.createTarget', {'url': origin + chat_path})
                attached = None
                for _ in range(20):
                    try:
                        attached = transport.pick_and_attach(chat_uuid)
                        break
                    except RuntimeError:
                        time.sleep(0.5)
                if attached is None:
                    try:
                        infos = transport.call('Target.getTargets', {}).get('targetInfos', [])
                        seen = [str(i.get('url', '')) for i in infos if i.get('type') == 'page']
                    except Exception as diag_error:
                        seen = [f'<getTargets échoué: {diag_error}>']
                    raise RuntimeError(
                        f'Page {chat_path} introuvable après createTarget ; cibles: {seen}')
        except Exception as exc:
            cdp_mod.terminate_chrome(proc)
            return {'ok': False, 'status': 'session', 'reason': str(exc)}

        if not claude_desktop_apply.wait_composer(transport):
            cdp_mod.terminate_chrome(proc)
            return {'ok': False, 'status': 'composeur', 'stage': 'composeur',
                    'detail': 'Composeur indisponible'}

        # Garde anti-redirection : la page attachée doit être exactement le
        # chat visé (une redirection /chat/<uuid> inconnu vers une page sans
        # identité ne laisse aucune trace dans le bon chat).
        json_path = json.dumps(chat_path)
        try:
            on_target = transport.evaluate(
                "(function(){return location.origin===" + json.dumps(origin)
                + " && location.pathname===" + json_path
                + " && !location.search && !location.hash})()")
        except Exception as exc:
            cdp_mod.terminate_chrome(proc)
            return {'ok': False, 'status': 'cible', 'stage': 'cible',
                    'detail': f'URL de la page non vérifiable: {exc}'}
        if on_target is not True:
            try:
                url_now = transport.evaluate('location.href')
            except Exception:
                url_now = None
            cdp_mod.terminate_chrome(proc)
            return {'ok': False, 'status': 'cible', 'stage': 'cible',
                    'detail': 'Page attachée ≠ /chat/' + chat_uuid,
                    'actual_url': url_now}

        composer_selector = json.dumps(config['selectors']['composer'])
        try:
            compose_check = transport.evaluate(
                "(function(){const el=document.querySelector(" + composer_selector
                + ");return el?{hasText:!!el.innerText,textLength:el.innerText.length}:null})()")
        except Exception as exc:
            cdp_mod.terminate_chrome(proc)
            return {'ok': False, 'status': 'send_failed',
                    'reason': f'compose_check: {exc}', 'exc_type': type(exc).__name__}
        try:
            if compose_check and compose_check.get("textLength", 0) > 0:
                stage = 'brouillon'
                cdp_mod.terminate_chrome(proc)
                return {'ok': False, 'status': 'brouillon', 'stage': 'brouillon',
                        'detail': 'Contenu préexistant dans le composeur'}

            # Event-driven insertion: focus + execCommand('insertText') fires
            # beforeinput/input through the browser input pipeline so the
            # claude.ai React state actually observes the text.
            selectors = config['selectors']
            inserted = claude_desktop_apply._insert(transport, prompt, selectors=selectors)
        except Exception as exc:
            cdp_mod.terminate_chrome(proc)
            return {'ok': False, 'status': 'send_failed',
                    'reason': f'insert: {exc}', 'exc_type': type(exc).__name__}
        if not isinstance(inserted, dict) or inserted.get('inserted') is not True:
            cdp_mod.terminate_chrome(proc)
            return {'ok': False, 'status': 'composeur', 'stage': 'composeur',
                    'detail': 'Insertion dans le composeur non confirmée',
                    'insert_result': inserted}
        stage = 'envoyé'

        try:
            # Pre-click gate: the send button must be enabled; one Enter
            # keydown fallback, else honest incertain without a click.
            armed = claude_desktop_apply._arm(transport, selectors=selectors)
            # Baseline du compteur user-message mesurée AVANT le clic (une
            # baseline mesurée après serait déjà incrémentée par l'envoi).
            try:
                before_state = transport.evaluate(
                    "(function(){return {n: document.querySelectorAll("
                    + json.dumps(selectors['user_message']) + ").length,"
                    " url: location.pathname}})()")
            except Exception:
                before_state = {}
            if not isinstance(armed, dict) or armed.get('ready') is not True:
                # No Enter fallback: a still-disabled button is a distinct,
                # certain pre-click failure (Chrome is terminated below).
                stage = 'readiness_timeout' if (
                    isinstance(armed, dict) and armed.get('stage') == 'readiness_timeout') else 'incertain'
            else:
                try:
                    # Clic événementiel complet : React/claude.ai peut
                    # brancher l'envoi sur pointerdown plutôt que click.
                    click_result = transport.evaluate(
                        "(function(){"
                        "const btn=document.querySelector(" + json.dumps(selectors['send']) + ");"
                        "if(!btn)return {clicked:false, reason:'bouton absent'};"
                        "if(btn.disabled)return {clicked:false, reason:'disabled'};"
                        "const o={bubbles:true,cancelable:true,composed:true,view:window,button:0};"
                        "btn.dispatchEvent(new PointerEvent('pointerdown',Object.assign({pointerId:1,isPrimary:true,pointerType:'mouse'},o)));"
                        "btn.dispatchEvent(new MouseEvent('mousedown',o));"
                        "btn.dispatchEvent(new PointerEvent('pointerup',Object.assign({pointerId:1,isPrimary:true,pointerType:'mouse'},o)));"
                        "btn.dispatchEvent(new MouseEvent('mouseup',o));"
                        "btn.dispatchEvent(new MouseEvent('click',o));"
                        "return {clicked:true}})()")
                    if not click_result or not click_result.get("clicked"):
                        stage = 'incertain'
                except Exception:
                    stage = 'incertain'
                try:
                    # Preuve d'envoi stricte : composeur vidé ET un
                    # user-message de plus que la baseline ET URL toujours
                    # /chat/<uuid>. « Composeur vidé » seul confirme aussi
                    # une redirection vers un nouveau chat — jamais suffisant.
                    if not claude_desktop_apply._confirm(transport, before_state, selectors=selectors):
                        stage = 'incertain'
                except Exception:
                    stage = 'incertain'
        except Exception:
            stage = 'incertain'
        finally:
            cdp_mod.terminate_chrome(proc)

        confirm_detail = getattr(transport, 'last_confirm', None)
        receipt = {
            'ok': True, 'status': 'submitted', 'stage': stage,
            'chatId': chat_uuid, 'submitted': 1,
            'transport': 'cdp-pipe',
            'detail': 'Envoyé' if stage == 'envoyé' else stage}
        if isinstance(confirm_detail, dict):
            receipt['confirm'] = confirm_detail
        return receipt

    def _shutdown(self, args):
        """Authenticate shutdown without terminating an unowned Chrome process."""
        if not isinstance(args, dict):
            raise ValueError('Objet de soumission requis')
        token = args.get('token', '')
        if not isinstance(token, str) or len(token) != 64:
            return {'ok': False, 'status': 'unauthenticated'}

        shutdown_token_path = self.state / 'quick-apply-shutdown.token'
        try:
            stored = shutdown_token_path.read_text().strip()
        except OSError:
            return {'ok': False, 'status': 'unauthenticated'}

        if hashlib.sha256(token.encode()).hexdigest() != hashlib.sha256(stored.encode()).hexdigest():
            return {'ok': False, 'status': 'unauthenticated'}

        try:
            shutdown_token_path.unlink(missing_ok=True)
        except OSError:
            pass

        return {'ok': True}


def _relay_greeting(raw):
    """Retourne True si la première ligne annonce un relais compatible."""
    try:
        greeting = json.loads(raw)
    except ValueError:
        return False
    if not isinstance(greeting, dict) or greeting.get('role') != 'relay':
        return False
    return greeting.get('protocol') == RELAY_PROTOCOL


def _serve(conn, endpoint):
    stream = conn.makefile('rwb')
    stream.write((json.dumps({'hello': True}) + '\n').encode())
    stream.flush()
    relay = False
    for raw in stream:
        if len(raw) > 1024*1024:
            break
        if not relay and _relay_greeting(raw):
            # Enregistrement relais : connexion tenue ouverte, dernier gagne.
            relay = True
            endpoint.relay.register(conn, stream)
            continue
        ident = None
        try:
            message = json.loads(raw)
            if not isinstance(message, dict):
                raise ValueError('Objet requis')
            ident = message.get('id')
            if 'id' not in message:
                continue
            method = message.get('method')
            params = message.get('params', {})
            if method == 'ping':
                # Battement du relais : réponse sur le même flux que les pushs.
                stream.write((json.dumps({'jsonrpc': '2.0', 'id': ident, 'result': {}}) + '\n').encode())
                stream.flush()
                continue
            if method == 'quick_entry/list_tasks' and not isinstance(params, dict):
                raise ValueError('Project-scoped task listing requires object params')
            result = endpoint.call(method, params or {})
            reply = {'jsonrpc': '2.0', 'id': ident, 'result': result}
            stream.write((json.dumps(reply, ensure_ascii=False)+'\n').encode())
            stream.flush()
            # After a successful shutdown response, exit the loop to close socket and endpoint
            if isinstance(result, dict) and result.get('ok') and message.get('method') == 'shutdown':
                raise SystemExit(0)
        except SystemExit:
            raise
        except (ValueError, TypeError, KeyError, OSError, sqlite3.Error) as error:
            reply = {'jsonrpc': '2.0', 'id': ident,
                     'error': {'code': -32602, 'message': str(error) or 'Requête refusée ; aucune relance automatique.'}}
            stream.write((json.dumps(reply, ensure_ascii=False)+'\n').encode())
            stream.flush()
        except Exception as error:
            # Unexpected endpoint failure (CDP RuntimeError, id mismatch...):
            # reply as internal error instead of killing the single-threaded loop.
            reply = {'jsonrpc': '2.0', 'id': ident,
                     'error': {'code': -32603, 'message': str(error) or 'Erreur interne ; aucune relance automatique.'}}
            stream.write((json.dumps(reply, ensure_ascii=False)+'\n').encode())
            stream.flush()
    if relay:
        endpoint.relay.unregister(conn)
    stream.close()


def serve(path, endpoint):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with Path(str(path) + '.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise SystemExit('Socket already owned by another Quick Apply daemon')
        return _serve_owned(path, endpoint)


def _serve_owned(path, endpoint):
    path = Path(path)
    if path.exists():
        if not stat.S_ISSOCK(path.lstat().st_mode):
            raise SystemExit('Le chemin de socket existe déjà sans être un socket.')
        # A refused connection does not prove ownership or that the listener is
        # absent (the owner may still be binding). Startup never replaces a socket.
        raise SystemExit('Socket déjà présent ; remplacement interdit sans réconciliation explicite.')
    try:
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    except OSError as error:
        raise SystemExit(f'Impossible de créer le dossier de socket {path.parent} : {error}')
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    os.umask(0o077)
    sock.bind(str(path))
    os.chmod(path, 0o600)
    sock.listen(8)
    try:
        while True:
            conn, _ = sock.accept()
            try:
                conn.settimeout(5)
                _serve(conn, endpoint)
            except (OSError, TimeoutError):
                pass
            finally:
                conn.close()
    finally:
        sock.close()
        endpoint.close()


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--socket', default=str(claude_desktop.DEFAULT_SOCKET))
    parser.add_argument('--state', default=str(state_location(Path(__file__).parent, 'quick-state')))
    parser.add_argument('--project-root', help='Bind the inbox/result channel to this project; legacy admission remains unbound')
    parser.add_argument('--daily-limit', type=int, help='Deprecated compatibility option; ignored')
    args = parser.parse_args()
    endpoint = Endpoint(args.state, args.daily_limit, args.project_root)
    def terminate(signum, frame):
        # A profile lock does not establish ownership of a Chrome process.
        raise SystemExit(128 + signum)
    previous = signal.signal(signal.SIGTERM, terminate)
    try:
        serve(args.socket, endpoint)
    finally:
        signal.signal(signal.SIGTERM, previous)


if __name__ == '__main__':
    main()
