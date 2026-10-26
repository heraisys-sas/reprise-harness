'use strict';

// Validation : node --check proxy/asi1-planner-proxy.js
//              node --check proxy/test-proxy.js
//              node proxy/test-proxy.js
// Non couvert : TLS réel, expiration des quatre délais, pannes en cours de flux.

const assert = require('assert');
const process = require('process');
const http = require('http');
const { createProxy } = require('./asi1-planner-proxy');

const SESSION_SECRET = 'fake-session-secret-73';
const AUTH_SECRET = 'Bearer fake-authorization-secret-91';
const BODY_SECRET = 'fake-message-secret-28';
const sse = Buffer.from(
  'data: {"text":"été"}\n\ndata: {"text":"suite"}\n\ndata: [DONE]\n\n'
);
const pieces = [
  sse.subarray(0, 7),
  sse.subarray(7, 20),
  sse.subarray(20)
];

let proxy;
let upstream;
let calls = 0;
let observed;
let failures = 0;

function listen(server) {
  return new Promise((resolve, reject) => {
    server.once('error', reject);
    server.listen(0, '127.0.0.1', () => {
      server.removeListener('error', reject);
      resolve();
    });
  });
}

function close(server) {
  if (!server?.listening) return Promise.resolve();
  return new Promise(resolve => server.close(resolve));
}

function request(body, options = {}) {
  return new Promise((resolve, reject) => {
    const payload = Buffer.from(JSON.stringify(body));
    const req = http.request({
      hostname: '127.0.0.1',
      port: proxy.address().port,
      method: 'POST',
      path: options.path || '/v1/chat/completions',
      agent: false,
      headers: {
        authorization: AUTH_SECRET,
        'content-type': 'application/json',
        'content-length': payload.length,
        'accept-encoding': 'gzip',
        'x-session-id': 'fake-header-session-secret',
        connection: 'close, x-remove-me',
        'x-remove-me': 'remove',
        ...(options.marked === false ? {} : { 'x-asi-planner': '1' })
      }
    }, res => {
      const chunks = [];
      res.on('data', chunk => chunks.push(chunk));
      res.on('error', reject);
      res.on('aborted', () => reject(new Error('Réponse interrompue')));
      res.on('end', () => resolve({
        status: res.statusCode,
        headers: res.headers,
        body: Buffer.concat(chunks)
      }));
    });
    req.on('error', reject);
    req.setTimeout(5_000, () => req.destroy(new Error('Test timeout')));
    req.end(payload);
  });
}

async function check(name, run) {
  try {
    await run();
    process.stdout.write(`ok ${name}\n`);
  } catch (error) {
    failures++;
    process.stdout.write(`ko ${name}: ${error.message}\n`);
  }
}

async function main() {
  upstream = http.createServer((req, res) => {
    calls++;
    const chunks = [];
    req.on('data', chunk => chunks.push(chunk));
    req.on('end', () => {
      const raw = Buffer.concat(chunks);
      observed = {
        body: JSON.parse(raw.toString('utf8')),
        headers: req.headers,
        length: raw.length
      };
      res.writeHead(200, { 'content-type': 'text/event-stream' });
      res.flushHeaders();
      res.write(pieces[0]);
      setTimeout(() => {
        res.write(pieces[1]);
        setTimeout(() => res.end(pieces[2]), 10);
      }, 10);
    });
  });

  await listen(upstream);
  process.env.ASI1_PLANNER_UPSTREAM =
    `http://127.0.0.1:${upstream.address().port}`;
  process.env.ASI1_PLANNER_LOG_PATH =
    `${process.env.TMPDIR || '/tmp'}/asi1-planner-test-${process.pid}.jsonl`;

  proxy = createProxy();
  const logOffset = proxy.readTrafficLog().length;
  await listen(proxy);

  let reply;
  await check('réécriture et en-têtes upstream', async () => {
    reply = await request({
      model: 'test-model',
      messages: [{ role: 'user', content: BODY_SECRET }],
      prompt_cache_key: SESSION_SECRET,
      'x-session-id': 'fake-body-session-secret',
      stream: true,
      stream_options: { include_usage: true },
      planner_mode: false,
      reasoning_effort: 'high',
      temperature: 1,
      extra: { preserved: true }
    });

    assert.strictEqual(reply.status, 200);
    assert.deepStrictEqual(observed.body, {
      model: 'test-model',
      messages: [{ role: 'user', content: BODY_SECRET }],
      stream: true,
      planner_mode: true,
      reasoning_effort: 'low',
      temperature: 0.25,
      extra: { preserved: true }
    });
    assert.strictEqual(observed.headers.authorization, AUTH_SECRET);
    assert.strictEqual(
      observed.headers.host, `127.0.0.1:${upstream.address().port}`
    );
    assert.strictEqual(
      Number(observed.headers['content-length']), observed.length
    );
    for (const name of [
      'accept-encoding', 'x-remove-me', 'x-asi-planner'
    ]) {
      assert.strictEqual(observed.headers[name], undefined);
    }
    assert.strictEqual(observed.headers['x-session-id'], SESSION_SECRET);
  });

  await check('trois fragments SSE relayés octet pour octet', async () => {
    assert.ok(reply, 'Réponse absente');
    assert.deepStrictEqual(reply.body, sse);
  });

  await check('clé absente, vide ou trop longue refusée', async () => {
    const before = calls;
    for (const key of [undefined, '', '   ', 'x'.repeat(65), 123]) {
      const body = {};
      if (key !== undefined) body.prompt_cache_key = key;
      assert.strictEqual((await request(body)).status, 400);
    }
    assert.strictEqual(calls, before);
  });

  await check('marqueur absent et chemin incorrect refusés', async () => {
    const before = calls;
    const body = { prompt_cache_key: SESSION_SECRET };
    assert.strictEqual(
      (await request(body, { marked: false })).status, 404
    );
    assert.strictEqual(
      (await request(body, { path: '/v1/models' })).status, 404
    );
    assert.strictEqual(calls, before);
  });

  await check('journal JSONL sans secrets ni corps', async () => {
    const text = proxy.readTrafficLog().slice(logOffset);
    for (const secret of [
      SESSION_SECRET, AUTH_SECRET, BODY_SECRET,
      'fake-header-session-secret', 'fake-body-session-secret'
    ]) {
      assert.ok(!text.includes(secret), 'Secret présent dans le journal');
    }
    const entries = text.trim().split('\n').map(line => JSON.parse(line));
    assert.strictEqual(entries.length, 8);
    const allowed = [
      'timestamp', 'correlationId', 'sessionIdHash', 'status',
      'headersMs', 'totalMs', 'requestBytes', 'bytesReceived',
      'bytesSent', 'errorCode'
    ].sort();

    for (const entry of entries) {
      assert.deepStrictEqual(Object.keys(entry).sort(), allowed);
      assert.ok(Number.isFinite(Date.parse(entry.timestamp)));
      assert.match(entry.correlationId, /^[a-f0-9]{32}$/);
      assert.ok(entry.totalMs >= 0);
    }

    const success = entries.find(entry => entry.status === 200);
    assert.ok(success);
    assert.match(success.sessionIdHash, /^[a-f0-9]{24}$/);
    assert.ok(success.headersMs >= 0);
    assert.strictEqual(success.bytesReceived, sse.length);
    assert.strictEqual(success.bytesSent, sse.length);
    assert.strictEqual(success.errorCode, null);
  });
}

main()
  .catch(() => {
    failures++;
    process.stdout.write('ko initialisation ou exécution du test\n');
  })
  .finally(async () => {
    await close(proxy);
    await close(upstream);
    process.exitCode = failures ? 1 : 0;
  });
