#!/usr/bin/env node
/*
 * Pilotage par Chrome DevTools Protocol.
 *
 * Deux cibles possibles :
 *   - Claude Desktop (port 9222) — REFUSÉ par l'application, voir README ;
 *   - claude.ai dans un Chrome qu'on lance nous-mêmes (port 9333) — voie retenue.
 *
 * Aucune frappe synthétique, aucune permission macOS : on parle au moteur.
 *
 *   node cdp.mjs status
 *   node cdp.mjs inspect
 *   node cdp.mjs navigate --url https://claude.ai/chat/<uuid>
 *   node cdp.mjs send --text-b64 <base64> [--submit]
 *   node cdp.mjs retry
 *   node cdp.mjs selftest
 */

const PORT = Number(argOf("port") || process.env.CDP_PORT || 9333);
const BASE = `http://127.0.0.1:${PORT}`;

function argOf(name, fallback = null) {
  const index = process.argv.indexOf(`--${name}`);
  return index === -1 ? fallback : process.argv[index + 1];
}

function flag(name) {
  return process.argv.includes(`--${name}`);
}

async function targets() {
  const response = await fetch(`${BASE}/json/list`, { signal: AbortSignal.timeout(3000) });
  if (!response.ok) throw new Error(`HTTP ${response.status} sur /json/list`);
  return response.json();
}

function pickPage(list) {
  const pages = list.filter((t) => t.type === "page");
  const claude = pages.filter((t) => (t.url || "").includes("claude.ai"));
  return claude[0] || pages[0] || null;
}

class Session {
  constructor(wsUrl) {
    this.wsUrl = wsUrl;
    this.id = 0;
    this.pending = new Map();
  }

  open() {
    return new Promise((resolve, reject) => {
      this.ws = new WebSocket(this.wsUrl);
      this.ws.addEventListener("open", () => resolve());
      this.ws.addEventListener("error", (e) => reject(new Error(`WebSocket: ${e.message || "échec"}`)));
      this.ws.addEventListener("message", (event) => {
        let payload;
        try {
          payload = JSON.parse(event.data);
        } catch {
          return;
        }
        const waiter = this.pending.get(payload.id);
        if (!waiter) return;
        this.pending.delete(payload.id);
        if (payload.error) waiter.reject(new Error(JSON.stringify(payload.error)));
        else waiter.resolve(payload.result);
      });
    });
  }

  call(method, params = {}) {
    const id = ++this.id;
    return new Promise((resolve, reject) => {
      this.pending.set(id, { resolve, reject });
      this.ws.send(JSON.stringify({ id, method, params }));
      setTimeout(() => {
        if (this.pending.delete(id)) reject(new Error(`délai dépassé: ${method}`));
      }, 8000);
    });
  }

  async evaluate(expression) {
    const result = await this.call("Runtime.evaluate", {
      expression,
      returnByValue: true,
      awaitPromise: true,
    });
    if (result.exceptionDetails) throw new Error(result.exceptionDetails.text || "exception page");
    return result.result?.value;
  }

  async clickComposer() {
    const box = await this.evaluate(`(() => {
      const el = document.querySelector('div[contenteditable="true"], textarea');
      if (!el) return null;
      const r = el.getBoundingClientRect();
      if (!r.width || !r.height) return null;
      return { x: r.x + r.width / 2, y: r.y + r.height / 2 };
    })()`);
    if (!box) return false;
    for (const type of ["mousePressed", "mouseReleased"]) {
      await this.call("Input.dispatchMouseEvent", {
        type, x: box.x, y: box.y, button: "left", clickCount: 1,
      });
    }
    await sleep(80);
    return true;
  }

  async insertText(text) {
    await this.clickComposer();
    await this.evaluate(JS_FOCUS_SELECT);
    await this.call("Input.insertText", { text });
    await sleep(120);
    return this.evaluate(`(() => {
      const el = document.querySelector('div[contenteditable="true"], textarea');
      if (!el) return { ok: false, reason: 'composeur introuvable' };
      const value = el.innerText ?? el.value ?? '';
      return { ok: true, after: value, matched: value.trim() === ${JSON.stringify(text)}.trim() };
    })()`);
  }

  close() {
    try {
      this.ws.close();
    } catch {
      /* ignore */
    }
  }
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

// --- expressions ---------------------------------------------------------

const JS_STATE = `(() => {
  const composer = document.querySelector('div[contenteditable="true"], textarea');
  const buttons = [...document.querySelectorAll('button')]
    .map((b) => ({
      label: b.getAttribute('aria-label'),
      testid: b.getAttribute('data-testid'),
      text: (b.innerText || '').trim().slice(0, 24),
      disabled: b.disabled,
    }))
    .filter((b) => b.label || b.testid);
  return {
    url: location.href,
    title: document.title,
    hasComposer: Boolean(composer),
    composerText: composer ? (composer.innerText ?? composer.value ?? null) : null,
    buttons: buttons.slice(0, 40),
  };
})()`;

const JS_TYPE = (textB64) => `(() => {
  const text = atob("${textB64}");
  const el = document.querySelector('div[contenteditable="true"], textarea');
  if (!el) return { ok: false, reason: 'composeur introuvable' };
  el.focus();
  if (el.tagName === 'TEXTAREA') {
    const setter = Object.getOwnPropertyDescriptor(window.HTMLTextAreaElement.prototype, 'value').set;
    setter.call(el, text);
    el.dispatchEvent(new Event('input', { bubbles: true }));
  } else {
    const selection = window.getSelection();
    const range = document.createRange();
    range.selectNodeContents(el);
    selection.removeAllRanges();
    selection.addRange(range);
    document.execCommand('insertText', false, text);
  }
  const value = el.innerText ?? el.value ?? '';
  return { ok: true, after: value, matched: value.trim() === text.trim() };
})()`;

const JS_FOCUS_SELECT = `(() => {
  const el = document.querySelector('div[contenteditable="true"], textarea');
  if (!el) return { ok: false, reason: 'composeur introuvable' };
  el.focus();
  if (el.tagName !== 'TEXTAREA') {
    const range = document.createRange();
    range.selectNodeContents(el);
    const selection = window.getSelection();
    selection.removeAllRanges();
    selection.addRange(range);
  }
  return { ok: true };
})()`;

const JS_SEND = `(() => {
  const buttons = [...document.querySelectorAll('button')];
  const send = buttons.find((b) => {
    const label = (b.getAttribute('aria-label') || '').toLowerCase();
    const testid = (b.getAttribute('data-testid') || '').toLowerCase();
    return label.includes('send') || testid.includes('send') || label.includes('envoyer');
  });
  if (!send) return { ok: false, reason: "bouton d'envoi introuvable" };
  if (send.disabled) return { ok: false, reason: 'bouton désactivé' };
  send.click();
  return { ok: true, clicked: send.getAttribute('aria-label') || send.getAttribute('data-testid') };
})()`;

const JS_QUOTA = `(() => {
  const NL = String.fromCharCode(10);
  const send = document.querySelector('[data-testid="chat-input-send"]');
  const input = document.querySelector('div[contenteditable="true"], textarea');
  const hasText = Boolean(((input ? (input.innerText ?? input.value) : '') || '').trim());
  const dialogs = [...document.querySelectorAll('[role="dialog"], [data-testid*="modal"]')]
    .map((d) => (d.innerText || '').split(NL).join(' ').trim())
    .filter(Boolean);
  const text = dialogs.join(' ');
  const lower = text.toLowerCase();
  const blocked = lower.includes('limite') || lower.includes('limit');
  const time = text.match(new RegExp("([0-9]{1,2}:[0-9]{2})"));
  return {
    sendDisabled: send ? send.disabled : null,
    hasText,
    blocked,
    resetAt: time ? time[1] : null,
    dialogs,
  };
})()`;

const JS_RETRY = `(() => {
  // Deux actions distinctes portent le libellé « Réessayer » :
  //   action-bar-retry    → régénère la réponse de l'assistant
  //   user-message-retry  → renvoie le message de l'utilisateur
  const pick = (selector) => {
    const nodes = [...document.querySelectorAll(selector)];
    return nodes.length ? nodes[nodes.length - 1] : null;
  };
  const regenerate = pick('[data-testid="action-bar-retry"]');
  const resend = pick('[data-testid="user-message-retry"]');
  const target = regenerate || resend;
  if (!target) {
    return { ok: false, reason: 'action Retry introuvable',
             retryLike: [...document.querySelectorAll('button')]
               .map((b) => b.getAttribute('data-testid'))
               .filter((t) => t && t.includes('retry')) };
  }
  target.click();
  return {
    ok: true,
    action: regenerate ? 'regenerate' : 'resend',
    testid: target.getAttribute('data-testid'),
    label: target.getAttribute('aria-label'),
  };
})()`;

// --- commandes -----------------------------------------------------------

async function connect() {
  let list;
  try {
    list = await targets();
  } catch (error) {
    console.log(JSON.stringify({
      ok: false,
      stage: "connexion",
      detail: `${error.message} — un Chrome tourne-t-il avec --remote-debugging-port=${PORT} ?`,
    }, null, 2));
    process.exit(1);
  }
  const page = pickPage(list);
  if (!page) {
    console.log(JSON.stringify({ ok: false, stage: "cible", detail: "aucune page" }, null, 2));
    process.exit(1);
  }
  const session = new Session(page.webSocketDebuggerUrl);
  await session.open();
  await session.call("Runtime.enable");
  await session.call("Page.enable");
  return session;
}

async function waitComposer(session, timeoutMs = 10000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    const ready = await session.evaluate(`(() => {
      const el = document.querySelector('div[contenteditable="true"], textarea');
      if (!el) return false;
      const r = el.getBoundingClientRect();
      return r.width > 100 && r.height > 10;
    })()`);
    if (ready) return true;
    await sleep(250);
  }
  return false;
}

async function selftest() {
  const session = await connect();
  const html = `<div id="c" contenteditable="true" style="border:1px solid #000;min-height:40px"></div>
<button aria-label="Send message" onclick="document.title='SENT:'+document.getElementById('c').innerText">Envoyer</button>`;
  const url = "data:text/html," + encodeURIComponent(html);
  await session.call("Page.navigate", { url });
  await sleep(400);

  const text = "preuve-cdp-" + Date.now();
  const typed = await session.evaluate(JS_TYPE(Buffer.from(text).toString("base64")));
  const sent = await session.evaluate(JS_SEND);
  await sleep(200);
  const title = await session.evaluate("document.title");
  session.close();

  const ok = Boolean(typed?.ok && sent?.ok && title === `SENT:${text}`);
  console.log(JSON.stringify({ ok, typed, sent, title }, null, 2));
  process.exit(ok ? 0 : 1);
}

async function main() {
  const action = process.argv[2] || "status";

  if (action === "status") {
    try {
      const list = await targets();
      console.log(JSON.stringify({
        ok: true,
        port: PORT,
        pages: list.filter((t) => t.type === "page").map((t) => ({ title: t.title, url: t.url })),
      }, null, 2));
    } catch (error) {
      console.log(JSON.stringify({
        ok: false,
        stage: "connexion",
        detail: `${error.message} — un Chrome tourne-t-il avec --remote-debugging-port=${PORT} ?`,
      }, null, 2));
      process.exit(1);
    }
    return;
  }

  if (action === "selftest") {
    await selftest();
    return;
  }

  const session = await connect();
  try {
    if (action === "eval") {
      const encoded = argOf("expr-b64");
      if (!encoded) throw new Error("--expr-b64 requis");
      const expression = Buffer.from(encoded, "base64").toString("utf8");
      console.log(JSON.stringify(await session.evaluate(expression), null, 2));
      return;
    }

    if (action === "inspect" || action === "read") {
      console.log(JSON.stringify(await session.evaluate(JS_STATE), null, 2));
      return;
    }

    if (action === "navigate") {
      const url = argOf("url");
      if (!url) throw new Error("--url requis");
      await session.call("Page.navigate", { url });
      await sleep(Number(argOf("wait", "3500")));
      console.log(JSON.stringify(await session.evaluate(JS_STATE), null, 2));
      return;
    }

    if (action === "quota") {
      console.log(JSON.stringify(await session.evaluate(JS_QUOTA), null, 2));
      return;
    }

    if (action === "apply") {
      const encoded = argOf("text-b64");
      if (!encoded) throw new Error("--text-b64 requis");
      const chatId = argOf("chat");
      const target = chatId ? `https://claude.ai/chat/${chatId}` : "https://claude.ai/new";
      await session.call("Page.navigate", { url: target });
      await sleep(Number(argOf("wait", "3500")));

      if (!(await waitComposer(session))) {
        const current = await session.evaluate(JS_STATE);
        const url = (current && current.url) || target;
        const stage = url.includes("/login") ? "session" : "composeur";
        console.log(JSON.stringify({ ok: false, stage, target, url,
          detail: stage === "session" ? "connexion claude.ai requise"
                                      : "composeur absent ou invisible" }, null, 2));
        process.exit(stage === "session" ? 4 : 1);
      }

      const payload = Buffer.from(encoded, "base64").toString("utf8");
      let typed = await session.insertText(payload);
      if (!typed?.matched) {
        // La page peut être hydratée tardivement : une seconde tentative suffit
        // dans les cas observés (SPA qui monte l'éditeur après le premier passage).
        await sleep(600);
        typed = await session.insertText(payload);
      }
      if (!typed?.matched) {
        console.log(JSON.stringify({ ok: false, stage: "frappe", typed }, null, 2));
        process.exit(1);
      }
      await sleep(300);

      // Un bouton désactivé n'a de sens qu'une fois du texte présent :
      // champ vide et quota épuisé se ressemblent sinon.
      const quota = await session.evaluate(JS_QUOTA);
      if (quota?.sendDisabled) {
        // Le texte reste dans le composeur : un simple ⏎ après réouverture suffit.
        console.log(JSON.stringify({
          ok: false,
          stage: "quota",
          staged: true,
          reason: "bouton d'envoi désactivé avec du texte présent",
          target,
          ...quota,
        }, null, 2));
        process.exit(3);
      }
      const sent = await session.evaluate(JS_SEND);
      console.log(JSON.stringify({
        ok: Boolean(sent?.ok), stage: sent?.ok ? "envoyé" : "envoi", target,
        matched: typed.matched, ...sent,
      }, null, 2));
      if (!sent?.ok) process.exit(1);
      return;
    }

    if (action === "send-staged") {
      const composer = await session.evaluate(`(() => {
        const el = document.querySelector('div[contenteditable="true"], textarea');
        return el ? (el.innerText ?? el.value ?? '') : '';
      })()`);
      if (!String(composer || "").trim()) {
        console.log(JSON.stringify({ ok: false, stage: "vide",
          detail: "aucun texte en attente dans le composeur" }, null, 2));
        process.exit(2);
      }
      const state = await session.evaluate(JS_QUOTA);
      if (state?.sendDisabled) {
        console.log(JSON.stringify({ ok: false, stage: "quota", ...state }, null, 2));
        process.exit(3);
      }
      const sent = await session.evaluate(JS_SEND);
      console.log(JSON.stringify({ ok: Boolean(sent?.ok), stage: sent?.ok ? "envoyé" : "envoi", ...sent }, null, 2));
      if (!sent?.ok) process.exit(1);
      return;
    }

    if (action === "retry") {
      console.log(JSON.stringify(await session.evaluate(JS_RETRY), null, 2));
      return;
    }

    if (action === "send") {
      const encoded = argOf("text-b64");
      if (!encoded) throw new Error("--text-b64 requis");
      const typed = await session.evaluate(JS_TYPE(encoded));
      if (!typed?.ok) {
        console.log(JSON.stringify({ ok: false, stage: "frappe", ...typed }, null, 2));
        process.exit(1);
      }
      if (!flag("submit")) {
        console.log(JSON.stringify({ ok: true, stage: "pré-rempli", ...typed }, null, 2));
        return;
      }
      await sleep(350);
      const sent = await session.evaluate(JS_SEND);
      console.log(JSON.stringify({
        ok: Boolean(sent?.ok), stage: sent?.ok ? "envoyé" : "envoi", matched: typed.matched, ...sent,
      }, null, 2));
      if (!sent?.ok) process.exit(1);
      return;
    }

    console.log(JSON.stringify({ ok: false, detail: `action inconnue: ${action}` }, null, 2));
    process.exit(2);
  } finally {
    session.close();
  }
}

main().catch((error) => {
  console.log(JSON.stringify({ ok: false, stage: "exception", detail: String(error.message || error) }, null, 2));
  process.exit(1);
});
