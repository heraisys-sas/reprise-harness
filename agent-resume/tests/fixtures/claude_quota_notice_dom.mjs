import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {pathToFileURL} from 'node:url';
import vm from 'node:vm';

export function runQuotaFixtures(script) {
  const expected = 'https://claude.ai/chat/00000000-0000-0000-0000-000000000001';
  let count = 0;
  function run(options = {}) {
    let queries = 0;
    const message = options.text ?? 'Usage limit reached. Reset at 14:40.';
    const element = {innerText: message,
      getBoundingClientRect() { return {width: options.hidden ? 0 : 200, height: 30}; },
      querySelector(selector) {
        assert.equal(selector, 'time[datetime]');
        return {getAttribute(name) { assert.equal(name, 'datetime'); return '2026-10-29T14:40:00+01:00'; }};
      },
      click() { throw new Error('Observation must not click'); },
    };
    const document = {querySelectorAll(selector) {
      queries++;
      if (selector === '[role="alert"], [role="dialog"]')
        return options.duplicate ? [element, element] : [element];
      assert.equal(selector, '[data-testid="chat-input-send"]');
      return [{disabled: true}];
    }};
    Object.defineProperty(document, 'body', {get() { throw new Error('Conversation read forbidden'); }});
    const result = vm.runInNewContext(script, {document, location: new URL(options.url ?? expected),
      Intl: {DateTimeFormat() { return {resolvedOptions() { return {timeZone: 'Europe/Paris'}; }}; }}}, {timeout: 1000});
    count++;
    return {result: JSON.parse(JSON.stringify(result)), queries};
  }
  const valid = run();
  assert.equal(valid.result.notice, 'Usage limit reached. Reset at 14:40.');
  assert.equal(valid.result.reset_iso, '2026-10-29T14:40:00+01:00');
  assert.equal(valid.result.send_disabled, true);
  assert.equal(run({text: 'Limite de messages atteinte. Réinitialisation : 14:40.'}).result.notice.startsWith('Limite'), true);
  for (const options of [{hidden: true}, {duplicate: true}, {text: 'Unrelated private dialog text'},
    {text: 'Upgrade for higher usage limits. Reset at 14:40.'},
    {text: 'No usage limit reached.'}, {text: 'You have not reached your usage limit.'},
    {text: 'Aucune limite atteinte.'}]) {
    const result = run(options).result;
    assert.equal(result.notice, null);
    assert.equal(result.reset_iso, null);
  }
  for (const url of ['https://evil.test/?claude.ai', 'https://claude.ai/chat/other',
                     expected + '?private=value', expected + '#fragment', 'https://claude.ai/new']) {
    const blocked = run({url});
    assert.equal(blocked.result.url, null);
    assert.equal(blocked.result.notice, null);
    assert.equal(blocked.queries, 0);
  }
  assert.equal(run({text: 'Usage limit reached. ' + 'x'.repeat(2000)}).result.notice.length, 1024);
  return {fixtures: count, status: 'passed', browser_used: false};
}

if (typeof process !== 'undefined' && process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  console.log(JSON.stringify(runQuotaFixtures(JSON.parse(readFileSync(0, 'utf8')))));
}
