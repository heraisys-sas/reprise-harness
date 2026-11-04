// Synthetic DOM only: never imports a browser driver or opens a connection.
import assert from 'node:assert/strict';
import {webcrypto} from 'node:crypto';
import {readFileSync} from 'node:fs';
import {pathToFileURL} from 'node:url';
import vm from 'node:vm';

export async function runFixtures(scripts) {
  const path = '/chat/00000000-0000-0000-0000-000000000001';
  let count = 0;
  function placeholderNodes(structure) {
    const br = {nodeType: 1, tagName: 'BR', childNodes: [],
      classList: {length: structure === 'extra-class' ? 2 : 1,
        contains(name) { return structure !== 'wrong-class' && name === 'ProseMirror-trailingBreak'; }}};
    const paragraph = {nodeType: 1, tagName: 'P', childNodes: [br]};
    const attachment = {nodeType: 1, tagName: 'IMG', childNodes: [], textContent: ''};
    if (structure === 'two-breaks') paragraph.childNodes.push({...br});
    if (structure === 'attachment-in-paragraph') paragraph.childNodes.push(attachment);
    if (structure === 'text-node') paragraph.childNodes.push({nodeType: 3, textContent: ' '});
    if (structure === 'wrong-paragraph') paragraph.tagName = 'SPAN';
    if (structure === 'non-element-paragraph') paragraph.nodeType = 3;
    if (structure === 'non-element-break') br.nodeType = 3;
    if (structure === 'two-paragraphs') return [paragraph, {...paragraph}];
    if (structure === 'attachment-sibling') return [paragraph, attachment];
    return [paragraph];
  }
  function fixture(options = {}) {
    const observed = {clicks: 0, selections: 0};
    const input = {
      text: options.text ?? '', tagName: options.tag ?? 'DIV',
      isContentEditable: (options.tag ?? 'DIV') === 'DIV',
      childNodes: options.structure ? placeholderNodes(options.structure) : [],
      get textContent() { return options.textContent ?? this.text; },
      get innerText() { return this.text; }, get value() { return this.text; },
      getBoundingClientRect() { return {width: 200, height: 30}; },
      focus() { if (options.focusMutation) this.text += ' changed'; },
      setSelectionRange(start, end) { observed.selections++; observed.selection = [start, end]; },
    };
    const button = {disabled: options.disabled ?? false, click() { observed.clicks++; }};
    const inputs = options.multipleInputs ? [input, {...input}] : [input];
    const buttons = options.missingButton ? [] : options.multipleButtons ? [button, button] : [button];
    const document = {
      querySelectorAll(selector) { return selector.includes('contenteditable') ? inputs : buttons; },
      createRange() { return {selectNodeContents() { observed.selections++; }}; },
    };
    const context = {
      document, location: new URL(options.url ?? 'https://claude.ai' + path),
      window: {getSelection() { return {removeAllRanges() {}, addRange() {}}; }},
      TextEncoder, Uint8Array,
      crypto: {subtle: {async digest(...args) {
        const value = await webcrypto.subtle.digest(...args);
        if (options.mutation === 'target') context.location.href = 'https://claude.ai/chat/other';
        if (options.mutation === 'origin') context.location.href = 'https://evil.test/chat/other';
        if (options.mutation === 'text') input.text += ' changed';
        if (options.mutation === 'input') inputs[0] = {...input};
        if (options.mutation === 'document') context.document = {...document};
        return value;
      }}},
    };
    return {context, observed, input};
  }
  async function run(source, options = {}) {
    const value = fixture(options);
    value.result = JSON.parse(JSON.stringify(await vm.runInNewContext(source, value.context, {timeout: 1000})));
    count++;
    return value;
  }

  for (const tag of ['DIV', 'TEXTAREA']) {
    const ready = await run(scripts.prepare, {tag});
    assert.equal(ready.result, true);
    assert.equal(ready.observed.selections, 1);
    if (tag === 'TEXTAREA') assert.deepEqual(ready.observed.selection, [0, 0]);
    for (const text of ['old draft', ' \n ']) {
      const draft = await run(scripts.prepare, {tag, text});
      assert.equal(draft.result, false);
      assert.equal(draft.observed.selections, 0);
    }
    const changed = await run(scripts.prepare, {tag, focusMutation: true});
    assert.equal(changed.result, false);
  }
  for (const options of [
    {url: 'https://evil.test/?claude.ai'},
    {url: 'https://claude.ai/chat/other'},
    {url: 'https://claude.ai' + path + '?private=value'},
    {multipleInputs: true}, {missingButton: true}, {multipleButtons: true},
  ]) {
    const ready = await run(scripts.prepare, options);
    assert.equal(ready.result, false);
    assert.equal(ready.observed.clicks, 0);
  }
  const placeholder = {structure: 'exact', text: '\n', textContent: '', disabled: true};
  const emptySnapshot = await run(scripts.state, placeholder);
  assert.equal(emptySnapshot.result.text_length, 0);
  assert.equal(emptySnapshot.result.hasText, false);
  assert.equal(emptySnapshot.result.text_sha256,
    'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855');
  const emptyReady = await run(scripts.prepare, placeholder);
  assert.equal(emptyReady.result, true);
  assert.equal(emptyReady.observed.selections, 1);
  assert.equal(emptyReady.observed.clicks, 0);
  const emptySend = await run(scripts.send, placeholder);
  assert.equal(emptySend.result.clicked, false);
  assert.equal(emptySend.observed.clicks, 0);
  for (const options of [
    {...placeholder, text: ' ', textContent: ' '},
    {...placeholder, text: 'actual draft', textContent: 'actual draft'},
    {...placeholder, text: '\n\n'},
    {...placeholder, tag: 'TEXTAREA'},
    ...['two-paragraphs', 'two-breaks', 'attachment-sibling', 'attachment-in-paragraph',
      'text-node', 'wrong-class', 'extra-class', 'wrong-paragraph',
      'non-element-paragraph', 'non-element-break'].map(structure => ({...placeholder, structure})),
  ]) {
    const retained = await run(scripts.state, options);
    assert.equal(retained.result.text_length > 0, true);
    const refused = await run(scripts.prepare, options);
    assert.equal(refused.result, false);
    assert.equal(refused.observed.selections, 0);
    assert.equal(refused.observed.clicks, 0);
  }
  const text = scripts.text;
  const sent = await run(scripts.send, {text});
  assert.deepEqual(sent.result, {stage: 'envoyé', clicked: true});
  assert.equal(sent.observed.clicks, 1);
  for (const options of [
    {text: text + ' suffix'}, {text: text.slice(0, -1)}, {text: ''},
    {text, disabled: true}, {text, missingButton: true}, {text, multipleButtons: true},
    {text, multipleInputs: true}, {text, url: 'https://evil.test/?claude.ai'},
    ...['target', 'origin', 'text', 'input', 'document'].map(mutation => ({text, mutation})),
  ]) {
    const blocked = await run(scripts.send, options);
    assert.equal(blocked.result.clicked, false);
    assert.equal(blocked.observed.clicks, 0);
  }
  const snapshot = await run(scripts.state, {text: 'synthetic private draft'});
  assert.equal(snapshot.result.text_length, 23);
  assert.equal(snapshot.result.text_sha256.length, 64);
  assert.equal(JSON.stringify(snapshot.result).includes('synthetic private draft'), false);
  assert.equal('text' in snapshot.result, false);
  for (const mutation of ['target', 'text', 'input', 'document']) {
    assert.deepEqual((await run(scripts.state, {text, mutation})).result, {changed: true});
  }
  return {fixtures: count, status: 'passed', browser_used: false};
}

if (typeof process !== 'undefined' && process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  console.log(JSON.stringify(await runFixtures(JSON.parse(readFileSync(0, 'utf8')))));
}
