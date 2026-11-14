#!/usr/bin/env node
// chatgpt_cdp.mjs — pont CDP vers le renderer chat de ChatGPT Desktop (com.openai.codex)
// Transport validé 14/11/2026 : --remote-debugging-port honore le fork Electron « owl »,
// cible page unique app://-/index.html. Quota ChatGPT (UI), moteur codex non sollicité.
//
// Modes :
//   node chatgpt_cdp.mjs targets            — liste brute des cibles CDP
//   node chatgpt_cdp.mjs discover           — cibles + sondes DOM (composer, boutons)
//   node chatgpt_cdp.mjs newchat            — clique « nouvelle discussion » si trouvé
//   node chatgpt_cdp.mjs send "<prompt>"    — dispatch + capture de la réponse
//
// Port : --port N | env CHATGPT_CDP_PORT | 9333 par défaut.

const argv = process.argv.slice(2);
const pi = argv.indexOf('--port');
if (pi >= 0 && pi + 1 >= argv.length) throw new Error('--port nécessite un nombre');
const portValue = pi >= 0 ? argv[pi + 1] : (process.env.CHATGPT_CDP_PORT || '9333');
if (!/^[0-9]+$/.test(String(portValue))) throw new Error('port CDP invalide');
const PORT = Number(portValue);
if (!Number.isSafeInteger(PORT) || PORT < 1 || PORT > 65535) throw new Error('port CDP hors plage');
const positional = pi >= 0 ? argv.filter((_, i) => i !== pi && i !== pi + 1) : argv;
const mode = positional[0] || 'discover';
const promptText = positional.slice(1).join(' ').trim();
const BASE = 'http://127.0.0.1:' + PORT;
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
const REQUEST_TIMEOUT_MS = 10000;

async function waitPort(ms = 10000) {
  const end = Date.now() + ms;
  while (Date.now() < end) {
    try {
      const reply = await fetch(BASE + '/json/version', { signal: AbortSignal.timeout(1200) });
      if (!reply.ok) throw new Error('CDP HTTP ' + reply.status);
      const version = await reply.json();
      if (!version || typeof version.Browser !== 'string') throw new Error('réponse DevTools invalide');
      return version;
    } catch {}
    await sleep(300);
  }
  throw new Error('port ' + PORT + ' injoignable ou non-CDP');
}

class Cdp {
  constructor(ws) {
    this.ws = ws;
    this.id = 0;
    this.pending = new Map();
    this.closed = false;
  }
  failAll(error) {
    if (this.closed) return;
    this.closed = true;
    for (const [id, item] of this.pending) {
      clearTimeout(item.timer);
      item.reject(error);
      this.pending.delete(id);
    }
  }
  static async connect(url) {
    const ws = new WebSocket(url);
    const c = new Cdp(ws);
    await new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        c.failAll(new Error('CDP WebSocket connection timeout'));
        try { ws.close(); } catch {}
        reject(new Error('CDP WebSocket connection timeout'));
      }, REQUEST_TIMEOUT_MS);
      ws.onopen = () => { clearTimeout(timer); resolve(); };
      ws.onerror = () => { clearTimeout(timer); reject(new Error('CDP WebSocket connection failed')); };
    });
    ws.onclose = () => c.failAll(new Error('CDP WebSocket closed'));
    ws.onerror = () => c.failAll(new Error('CDP WebSocket error'));
    ws.onmessage = (event) => {
      let message;
      try { message = JSON.parse(event.data); }
      catch { c.close(); return; }
      if (message.id === undefined || !c.pending.has(message.id)) return;
      const item = c.pending.get(message.id);
      c.pending.delete(message.id);
      clearTimeout(item.timer);
      message.error ? item.reject(new Error(message.error.message || 'CDP error')) : item.resolve(message.result);
    };
    return c;
  }
  send(method, params = {}) {
    if (this.closed || this.ws.readyState !== 1) return Promise.reject(new Error('CDP WebSocket unavailable'));
    return new Promise((resolve, reject) => {
      const id = ++this.id;
      const timer = setTimeout(() => {
        this.failAll(new Error('CDP command timeout: ' + method));
        try { this.ws.close(); } catch {}
      }, REQUEST_TIMEOUT_MS);
      this.pending.set(id, { resolve, reject, timer });
      try { this.ws.send(JSON.stringify({ id, method, params })); }
      catch (error) {
        clearTimeout(timer);
        this.pending.delete(id);
        reject(error);
      }
    });
  }
  close() {
    this.failAll(new Error('CDP connection closed'));
    try { this.ws.close(); } catch {}
  }
}

async function pageTargets() {
  const list = await (await fetch(`${BASE}/json/list`)).json();
  return list.filter((t) => t.type === 'page');
}

const DISCOVERY = `(() => {
  const boxes = [...document.querySelectorAll('textarea, [contenteditable="true"], [role="textbox"]')]
    .map((e) => ({
      tag: e.tagName, id: e.id || '', cls: String(e.className).slice(0, 100),
      ce: e.isContentEditable, visible: e.offsetParent !== null || e.isContentEditable,
      ph: (e.getAttribute('placeholder') || e.getAttribute('data-placeholder') || e.getAttribute('aria-label') || '').slice(0, 60),
      text: (e.value || e.textContent || '').slice(0, 40),
    }));
  const btns = [...document.querySelectorAll('button, [role="button"], a')]
    .map((e) => (e.getAttribute('aria-label') || e.innerText || '').trim().slice(0, 50))
    .filter(Boolean).slice(0, 80);
  return JSON.stringify({
    url: location.href, title: document.title, ready: document.readyState,
    bodyChars: document.body ? document.body.innerText.length : 0, boxes, btns,
  });
})()`;

function isMainChatTarget(target) {
  if (!target || target.type !== 'page') return false;
  try {
    const url = new URL(target.url);
    return url.protocol === 'app:' && url.hostname === '-' && url.pathname === '/index.html';
  } catch { return false; }
}

function verifiedDebuggerUrl(raw) {
  const url = new URL(raw);
  const loopback = ['127.0.0.1', 'localhost', '[::1]'];
  if (url.protocol !== 'ws:' || !loopback.includes(url.hostname) ||
      Number(url.port) !== PORT || !url.pathname.startsWith('/devtools/page/') ||
      url.username || url.password) {
    throw new Error('endpoint CDP étranger au port local sélectionné');
  }
  return raw;
}

async function attach() {
  const matches = (await pageTargets()).filter(isMainChatTarget);
  if (matches.length !== 1) throw new Error('target_ambiguous: cible principale app://-/index.html non unique ou absente');
  const target = matches[0];
  const c = await Cdp.connect(verifiedDebuggerUrl(target.webSocketDebuggerUrl));
  try {
    await c.send('Runtime.enable');
    const r = await c.send('Runtime.evaluate', { expression: DISCOVERY, returnByValue: true });
    if (r.exceptionDetails || !r.result || typeof r.result.value !== 'string')
      throw new Error('selectors_stale: sonde DOM invalide');
    const discovery = JSON.parse(r.result.value);
    if (!isMainChatTarget({ type: 'page', url: discovery.url }))
      throw new Error('target_changed: navigation depuis la cible ChatGPT');
    return { c, d: discovery, url: target.url };
  } catch (error) {
    c.close();
    throw error;
  }
}

async function evaluate(c, fn, ...args) {
  const expression = '(' + fn.toString() + ')(' + args.map((arg) => JSON.stringify(arg)).join(',') + ')';
  const result = await c.send('Runtime.evaluate', { expression, returnByValue: true });
  if (result.exceptionDetails || !result.result || !Object.hasOwn(result.result, 'value'))
    throw new Error('CDP Runtime.evaluate failed');
  return result.result.value;
}

function composerStatus(expected) {
  if (location.protocol !== 'app:' || location.hostname !== '-' || location.pathname !== '/index.html')
    return { status: 'target_changed' };
  const visible = (e) => {
    const box = e.getBoundingClientRect();
    const style = getComputedStyle(e);
    return box.width >= 80 && box.height >= 12 && style.visibility !== 'hidden' &&
           style.display !== 'none' && !e.disabled && !e.readOnly;
  };
  const editors = [...document.querySelectorAll('textarea, [contenteditable="true"], [role="textbox"]')]
    .filter(visible);
  if (editors.length !== 1) return { status: 'selectors_stale', composer_count: editors.length };
  const editor = editors[0];
  const text = editor.tagName === 'TEXTAREA' ? editor.value : editor.innerText;
  const sendButtons = [...document.querySelectorAll('button')]
    .filter(visible).filter((e) => /^(send|envoyer)(\s+(message|le message))?$/i
      .test((e.getAttribute('aria-label') || e.getAttribute('title') || '').trim()));
  if (sendButtons.length !== 1)
    return { status: 'selectors_stale', send_button_count: sendButtons.length };
  if (expected === null && text.length) return { status: 'draft_conflict' };
  if (expected !== null && text !== expected) return { status: 'draft_mismatch' };
  const userTurns = [...document.querySelectorAll('[data-message-author-role="user"], [data-testid="user-turn"]')];
  const assistantTurns = [...document.querySelectorAll('[data-message-author-role="assistant"], [data-testid="assistant-turn"]')];
  return { status: 'ready', users: userTurns.length, assistants: assistantTurns.length };
}

function focusUniqueComposer() {
  const editors = [...document.querySelectorAll('textarea, [contenteditable="true"], [role="textbox"]')]
    .filter((e) => {
      const r = e.getBoundingClientRect();
      return r.width >= 80 && r.height >= 12 && !e.disabled && !e.readOnly;
    });
  if (editors.length !== 1) return false;
  editors[0].focus();
  return document.activeElement === editors[0];
}

function clickUniqueSend(expected) {
  const status = composerStatus(expected);
  if (status.status !== 'ready') return { clicked: false, status: status.status };
  const buttons = [...document.querySelectorAll('button')]
    .filter((e) => {
      const r = e.getBoundingClientRect();
      return r.width > 0 && r.height > 0 && !e.disabled && !e.readOnly &&
        /^(send|envoyer)(\s+(message|le message))?$/i
          .test((e.getAttribute('aria-label') || e.getAttribute('title') || '').trim());
    });
  if (buttons.length !== 1) return { clicked: false, status: 'selectors_stale' };
  buttons[0].click();
  return { clicked: true, status: 'clicked' };
}

function observeTurns(expected, usersBefore, assistantsBefore) {
  if (location.protocol !== 'app:' || location.hostname !== '-' || location.pathname !== '/index.html')
    return { status: 'target_changed' };
  const users = [...document.querySelectorAll('[data-message-author-role="user"], [data-testid="user-turn"]')];
  const assistants = [...document.querySelectorAll('[data-message-author-role="assistant"], [data-testid="assistant-turn"]')];
  const latest = users.at(-1);
  const userConfirmed = users.length > usersBefore && latest && latest.innerText.trim() === expected.trim();
  const newAssistant = assistants.length > assistantsBefore ? assistants.at(-1) : null;
  return { status: 'observed', user_confirmed: !!userConfirmed,
           assistant_observed: !!(userConfirmed && newAssistant && newAssistant.innerText.trim()),
           assistant_text: userConfirmed && newAssistant ? newAssistant.innerText.slice(-1500) : '' };
}

async function cmdTargets() {
  await waitPort();
  const list = await (await fetch(`${BASE}/json/list`)).json();
  console.log(JSON.stringify(list.map((t) => ({ type: t.type, title: t.title, url: t.url })), null, 2));
}

async function cmdDiscover() {
  await waitPort();
  const pages = await pageTargets();
  console.log('targets:', pages.map((p) => `${p.title} | ${p.url}`).join(' ; ') || '(aucune)');
  const { c, d } = await attach();
  console.log(JSON.stringify(d, null, 2));
  c.close();
}

async function cmdNewChat() {
  await waitPort();
  const { c } = await attach();
  const expr = `(() => {
    const el = [...document.querySelectorAll('button, [role="button"], a')]
      .find((e) => /nouvelle discussion|nouveau chat|new chat|nouvelle conversation/i.test(e.getAttribute('aria-label') || e.innerText || ''));
    if (!el) return false;
    el.click(); return true;
  })()`;
  const r = await c.send('Runtime.evaluate', { expression: expr, returnByValue: true });
  console.log(r.result.value ? 'nouvelle discussion cliquée' : 'bouton « nouvelle discussion » introuvable');
  c.close();
}

async function cmdSend(prompt) {
  // A click is a dispatch attempt, not business success. Never auto-retry.
  const receipt = { status: 'refused', dispatch_attempted: false, dispatch_confirmed: false,
                    response_observed: false, response_complete: false };
  let c;
  try {
    if (prompt.length > 20000) { receipt.status = 'prompt_too_large'; return 2; }
    await waitPort();
    ({ c } = await attach());
    const before = await evaluate(c, composerStatus, null);
    if (before.status !== 'ready') { receipt.status = before.status; return 2; }
    if (!await evaluate(c, focusUniqueComposer)) { receipt.status = 'focus_failed'; return 2; }
    await c.send('Input.insertText', { text: prompt });
    const staged = await evaluate(c, composerStatus, prompt);
    if (staged.status !== 'ready') { receipt.status = staged.status; return 2; }
    // From here, transport loss can leave the dispatch outcome uncertain.
    receipt.dispatch_attempted = true;
    const click = await evaluate(c, clickUniqueSend, prompt);
    if (!click.clicked) {
      receipt.dispatch_attempted = false;
      receipt.status = click.status;
      return 2;
    }
    receipt.status = 'uncertain';
    for (let attempt = 0; attempt < 20; attempt++) {
      await sleep(1000);
      const turn = await evaluate(c, observeTurns, prompt, before.users, before.assistants);
      if (turn.status === 'target_changed') break;
      if (turn.user_confirmed) {
        receipt.dispatch_confirmed = true;
        receipt.status = 'sent_confirmed';
        if (turn.assistant_observed) {
          receipt.response_observed = true;
          receipt.assistant_excerpt = turn.assistant_text;
          // A visible assistant message is not proof that generation ended.
          receipt.response_complete = false;
          break;
        }
      }
    }
    return receipt.dispatch_confirmed ? 0 : 2;
  } catch (error) {
    receipt.status = receipt.dispatch_attempted ? 'uncertain' : 'refused';
    receipt.reason = String(error.message).slice(0, 200);
    return 2;
  } finally {
    if (c) c.close();
    console.log(JSON.stringify(receipt));
  }
}

(async () => {
  try {
    if (mode === 'targets') await cmdTargets();
    else if (mode === 'discover') await cmdDiscover();
    else if (mode === 'newchat') await cmdNewChat();
    else if (mode === 'send') {
      if (!promptText) { console.error('usage: chatgpt_cdp.mjs send "<prompt>"'); process.exitCode = 64; }
      else process.exitCode = await cmdSend(promptText);
    } else { console.error('modes: targets | discover | newchat | send "<prompt>"'); process.exitCode = 64; }
  } catch (error) {
    console.error('ERREUR:', error.message);
    process.exitCode = 1;
  }
})();
