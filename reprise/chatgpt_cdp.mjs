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

const PORT = (() => {
  const i = process.argv.indexOf('--port');
  return i >= 0 ? Number(process.argv[i + 1]) : Number(process.env.CHATGPT_CDP_PORT || 9333);
})();
const BASE = `http://127.0.0.1:${PORT}`;
const mode = process.argv[2] || 'discover';
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function waitPort(ms = 30000) {
  const t0 = Date.now();
  while (Date.now() - t0 < ms) {
    try { return await (await fetch(`${BASE}/json/version`)).json(); } catch {}
    await sleep(500);
  }
  throw new Error(`port ${PORT} injoignable après ${ms} ms`);
}

class Cdp {
  constructor(ws) { this.ws = ws; this.id = 0; this.pending = new Map(); }
  static async connect(url) {
    const ws = new WebSocket(url);
    const c = new Cdp(ws);
    await new Promise((res, rej) => { ws.onopen = res; ws.onerror = () => rej(new Error('ws error')); });
    ws.onmessage = (ev) => {
      const m = JSON.parse(ev.data);
      if (m.id && c.pending.has(m.id)) {
        const p = c.pending.get(m.id); c.pending.delete(m.id);
        m.error ? p.rej(new Error(m.error.message)) : p.res(m.result);
      }
    };
    return c;
  }
  send(method, params = {}) {
    return new Promise((res, rej) => {
      const i = ++this.id;
      this.pending.set(i, { res, rej });
      this.ws.send(JSON.stringify({ id: i, method, params }));
    });
  }
  close() { try { this.ws.close(); } catch {} }
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

async function attach() {
  let pages = await pageTargets();
  if (!pages.length) throw new Error('aucune cible page (app lancée avec le flag ?)');
  // fenêtre principale d'abord : app://-/index.html sans route secondaire (avatar-overlay, detached-window)
  pages.sort((a, b) => Number(b.url.endsWith('/index.html')) - Number(a.url.endsWith('/index.html')));
  let first = null;
  for (const p of pages) {
    let c;
    try { c = await Cdp.connect(p.webSocketDebuggerUrl); } catch { continue; }
    try {
      await c.send('Runtime.enable');
      const r = await c.send('Runtime.evaluate', { expression: DISCOVERY, returnByValue: true });
      const d = JSON.parse(r.result.value);
      if (!first) first = { c, d, url: p.url };
      if (d.boxes.some((b) => b.visible)) return { c, d, url: p.url };
    } catch { /* cible suivante */ }
  }
  if (first) return first;
  throw new Error('aucune cible app:// joignable');
}

async function findComposer(c) {
  const expr = `(() => [...document.querySelectorAll('textarea, [contenteditable="true"], [role="textbox"]')]
    .find((e) => e.offsetParent !== null || e.isContentEditable) || null)()`;
  const r = await c.send('Runtime.evaluate', { expression: `!!(${expr})`, returnByValue: true });
  return r.result.value;
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
  await waitPort();
  const { c, d } = await attach();
  if (!d.boxes.length) {
    console.error('composer introuvable, état :', JSON.stringify(d, null, 2));
    c.close(); process.exit(2);
  }
  const before = d.bodyChars;
  await c.send('Runtime.evaluate', {
    expression: `(() => { const el = [...document.querySelectorAll('textarea, [contenteditable="true"], [role="textbox"]')]
      .find((e) => e.offsetParent !== null || e.isContentEditable); if (!el) return false; el.focus(); return true; })()`,
    returnByValue: true,
  });
  // Cmd+A puis remplacement par le prompt (le typage remplace la sélection)
  const key = (type, k, vk) => c.send('Input.dispatchKeyEvent', {
    type, modifiers: 4, key: k, code: 'KeyA', windowsVirtualKeyCode: vk, nativeVirtualKeyCode: vk,
  });
  await key('keyDown', 'a', 65); await key('keyUp', 'a', 65);
  await c.send('Input.insertText', { text: prompt });
  await c.send('Input.dispatchKeyEvent', { type: 'keyDown', key: 'Enter', code: 'Enter', windowsVirtualKeyCode: 13, nativeVirtualKeyCode: 13, text: '\r' });
  await c.send('Input.dispatchKeyEvent', { type: 'keyUp', key: 'Enter', code: 'Enter', windowsVirtualKeyCode: 13, nativeVirtualKeyCode: 13 });
  console.log('dispatch ok — capture de la réponse…');

  // filet de sécurité : clic sur un bouton d'envoi si rien ne part
  await sleep(2500);
  let len = (await c.send('Runtime.evaluate', { expression: 'document.body.innerText.length', returnByValue: true })).result.value;
  if (!(len > before)) {
    await c.send('Runtime.evaluate', {
      expression: `(() => { const el = [...document.querySelectorAll('button, [role="button"]')]
        .find((e) => /envoyer|send/i.test(e.getAttribute('aria-label') || '')); if (!el) return false; el.click(); return true; })()`,
      returnByValue: true,
    });
  }

  let stable = 0, last = -1;
  for (let i = 0; i < 30; i++) {
    await sleep(2000);
    len = (await c.send('Runtime.evaluate', { expression: 'document.body.innerText.length', returnByValue: true })).result.value;
    if (len === last && len > before) { stable++; if (stable >= 2) break; } else stable = 0;
    last = len;
  }
  const tail = (await c.send('Runtime.evaluate', { expression: 'document.body.innerText.slice(-1500)', returnByValue: true })).result.value;
  console.log('--- fin de conversation (1500 derniers caractères) ---');
  console.log(tail);
  c.close();
}

(async () => {
  try {
    if (mode === 'targets') await cmdTargets();
    else if (mode === 'discover') await cmdDiscover();
    else if (mode === 'newchat') await cmdNewChat();
    else if (mode === 'send') {
      const args = process.argv.slice(3);
      const pi = args.indexOf('--port');
      let prompt = args;
      if (pi >= 0) prompt = args.slice(0, pi).concat(args.slice(pi + 2));
      prompt = prompt.join(' ').trim();
      if (!prompt) { console.error('usage: chatgpt_cdp.mjs send "<prompt>"'); process.exit(64); }
      await cmdSend(prompt);
    } else { console.error('modes: targets | discover | newchat | send "<prompt>"'); process.exit(64); }
  } catch (e) {
    console.error('ERREUR:', e.message);
    process.exit(1);
  }
})();
