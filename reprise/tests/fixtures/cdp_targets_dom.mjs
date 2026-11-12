// Synthetic DOM only: never imports a browser driver or opens a connection.
// Proves selector genericity: the DOM routes by INJECTED testids (selectors
// passed in the payload), never by the historical 'contenteditable' trick.
import {readFileSync} from 'node:fs';
import {pathToFileURL} from 'node:url';
import vm from 'node:vm';

export async function runFixtures(payload) {
  const {selectors, scripts} = payload;
  let count = 0;

  function makeTarget(kind, sel) {
    const observed = {clicks: 0, focuses: 0};
    const messages = [];
    const composer = {
      tagName: 'DIV', isContentEditable: true, childNodes: [], disabled: false, readOnly: false,
      text: '',
      get innerText() { return this.text; },
      get textContent() { return this.text; },
      set textContent(value) { this.text = value; },
      getBoundingClientRect() { return {width: 200, height: 30}; },
      focus() { observed.focuses++; active = composer; },
      dispatchEvent() {},
    };
    const send = {
      disabled: false, tagName: 'BUTTON',
      click() {
        observed.clicks++;
        if (!send.disabled && composer.text.length > 0) {
          messages.push({text: composer.text});
          composer.text = '';
        }
      },
    };
    let active = null;
    const bySelector = new Map([[sel.composer, composer], [sel.send, send]]);
    const allBySelector = new Map();
    allBySelector.set(sel.send, [send]);
    const document = {
      querySelector(selector) { return bySelector.get(selector) ?? null; },
      querySelectorAll(selector) {
        if (selector === sel.user_message) return [...messages];
        if (selector === sel.assistant_message) return [];
        // The unchanged structural helper composer() looks for editables;
        // expose only THIS target's composer to it.
        if (selector === 'div[contenteditable="true"], textarea') return [composer];
        return allBySelector.get(selector) ?? [];
      },
      execCommand(command, ui, value) {
        if (command !== 'insertText' || !active) return false;
        active.text = value;
        return true;
      },
    };
    return {observed, messages, composer, send, document};
  }

  function context(target, options = {}) {
    let tick = 0;
    return {
      document: target.document,
      location: new URL(options.url ?? 'https://claude.ai/chat/x'),
      Date: {now: () => (tick += 100)},
      setTimeout(resolve) { tick += 100; resolve(); },
      Promise, JSON, Boolean, Array,
    };
  }

  async function run(target, source, options = {}) {
    const value = {observed: target.observed, composer: target.composer, messages: target.messages};
    value.result = JSON.parse(JSON.stringify(await vm.runInNewContext(source, context(target, options), {timeout: 1000})));
    count++;
    return value;
  }

  const claude = makeTarget('claude', selectors.claude);
  const mammouth = makeTarget('mammouth', selectors.mammouth);

  // 1. Joint DOM: each selector set reaches ITS OWN composer (routing by
  //    injected testid, not by the fallback composer() helper).
  const joint = {
    document: {
      querySelector(selector) { return claude.document.querySelector(selector) ?? mammouth.document.querySelector(selector); },
      querySelectorAll(selector) { return [...claude.document.querySelectorAll(selector), ...mammouth.document.querySelectorAll(selector)]; },
      execCommand(...args) { return claude.document.execCommand(...args) || mammouth.document.execCommand(...args); },
    },
  };
  const jointContext = () => ({
    document: joint.document,
    location: new URL('https://claude.ai/chat/x'),
    Date: {now: () => 0},
    setTimeout(resolve) { resolve(); },
    Promise, JSON, Boolean, Array,
  });
  const b = JSON.parse(JSON.stringify(await vm.runInNewContext(scripts.insert_mammouth, jointContext(), {timeout: 1000}))); count++;
  if (!b.inserted) throw new Error('insert mammouth failed: ' + JSON.stringify(b));
  if (mammouth.composer.text !== 'prompt mammouth') throw new Error('mammouth composer wrong: ' + mammouth.composer.text);
  const a = JSON.parse(JSON.stringify(await vm.runInNewContext(scripts.insert_claude, jointContext(), {timeout: 1000}))); count++;
  if (!a.inserted) throw new Error('insert claude failed: ' + JSON.stringify(a));
  if (claude.composer.text !== 'prompt claude') throw new Error('claude composer wrong: ' + claude.composer.text);
  // Same DOM, no injected selectors: the structural fallback must NOT pick a
  // composer (two editables visible) — proves the selector is doing the work.
  const ambiguous = JSON.parse(JSON.stringify(await vm.runInNewContext(
    "(async () => { const el = document.querySelector('div[contenteditable=\"true\"], textarea');" +
    " const inputs = document.querySelectorAll('div[contenteditable=\"true\"], textarea');" +
    " return {single: Boolean(el), n: inputs.length}; })()",
    jointContext(), {timeout: 1000}))); count++;
  if (ambiguous.n !== 2) throw new Error('joint DOM shape wrong: ' + JSON.stringify(ambiguous));

  // 2. Per-target DOM for _arm and _confirm (composer() must be unambiguous).
  const armed = await run(claude, scripts.arm_claude);
  if (armed.result.ready !== true) throw new Error('arm claude failed: ' + JSON.stringify(armed.result));
  const armedMammouth = await run(mammouth, scripts.arm_mammouth);
  if (armedMammouth.result.ready !== true) throw new Error('arm mammouth failed: ' + JSON.stringify(armedMammouth.result));
  claude.composer.text = 'claude draft';
  mammouth.composer.text = 'mammouth draft';
  claude.send.disabled = true;
  const disabledClaude = await run(claude, scripts.arm_claude);
  claude.send.disabled = false;
  if (!(disabledClaude.result.stage === 'readiness_timeout' && disabledClaude.result.ready === false)) {
    throw new Error('disabled button not detected: ' + JSON.stringify(disabledClaude.result));
  }
  claude.send.disabled = false;

  // 3. _confirm counts messages through the INJECTED selector only: the
  //    other target's message shape is never counted.
  for (const [target, script, before] of [
    [claude, scripts.confirm_claude, {n: claude.messages.length, url: '/chat/x'}],
    [mammouth, scripts.confirm_mammouth, {n: mammouth.messages.length, url: '/chat/x'}],
  ]) {
    if (target.composer.text.length === 0) throw new Error('empty draft before send');
    target.send.click();
    const outcome = await run(target, script, {before});
    if (outcome.result.confirmed !== true) throw new Error('confirm failed: ' + JSON.stringify(outcome.result));
    if (outcome.result.user_messages !== before.n + 1) throw new Error('counter mismatch: ' + JSON.stringify(outcome.result));
    if (target.composer.text !== '') throw new Error('composer not emptied');
  }
  // No click → no confirmation (strict evidence).
  claude.composer.text = 'again';
  const noSend = await run(claude, scripts.confirm_claude, {before: {n: claude.messages.length, url: '/chat/x'}});
  if (noSend.result.confirmed !== false) throw new Error('confirmed without send: ' + JSON.stringify(noSend.result));

  // 4. Volume: fresh isolated DOMs per run, varied prompt sizes and url
  //    shapes, both selector sets through the full insert→arm→confirm cycle.
  for (const [name, sel, insertKey, armKey, confirmKey, origin] of [
    ['claude', selectors.claude, 'insert_claude', 'arm_claude', 'confirm_claude', 'https://claude.ai'],
    ['mammouth', selectors.mammouth, 'insert_mammouth', 'arm_mammouth', 'confirm_mammouth', 'https://mammouth.ai'],
  ]) {
    for (let round = 0; round < 11; round++) {
      const target = makeTarget(name, sel);
      const text = `prompt ${name} r${round} ${'x'.repeat(round)}`;
      const insertScript = scripts[insertKey].replace(`prompt ${name}`, text);
      const inserted = await run(target, insertScript, {url: origin + '/chat/r' + round});
      if (!inserted.result.inserted) throw new Error(`volume insert failed ${name}#${round}`);
      if (target.composer.text !== text) throw new Error(`volume text mismatch ${name}#${round}`);
      const armed = await run(target, scripts[armKey], {url: origin + '/chat/r' + round});
      if (armed.result.ready !== true) throw new Error(`volume arm failed ${name}#${round}`);
      target.send.click();
      const confirmed = await run(target, scripts[confirmKey], {url: origin + '/chat/r' + round});
      if (confirmed.result.confirmed !== true || confirmed.result.user_messages !== 1) {
        throw new Error(`volume confirm failed ${name}#${round}: ` + JSON.stringify(confirmed.result));
      }
    }
  }

  return {fixtures: count, status: 'passed', browser_used: false};
}

if (typeof process !== 'undefined' && process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  console.log(JSON.stringify(await runFixtures(JSON.parse(readFileSync(0, 'utf8')))));
}
