'use strict';

const PORT = 8481;
const UPSTREAM_HOST = 'inference.asicloud.cudos.org';
const UPSTREAM_PORT = 443;
const ALLOWED_PATH = '/v1/chat/completions';
const PLANNER_HEADER = 'x-asi-planner';
const REASONING_EFFORT = 'low';
const TEMPERATURE = 0.25;

const http = require('http');
const https = require('https');
const fs = require('fs');
const os = require('os');
const path = require('path');
const crypto = require('crypto');

const LOG_PATH = path.join(
  os.homedir(), 'Library', 'Logs', 'asi1-planner-proxy', 'traffic.jsonl'
);

const HOP_HEADERS = [
  'connection', 'keep-alive', 'proxy-connection', 'proxy-authenticate',
  'proxy-authorization', 'te', 'trailer', 'transfer-encoding', 'upgrade'
];

function cleanHeaders(headers) {
  const result = { ...headers };
  const connection = String(headers.connection || '');
  for (const name of [...HOP_HEADERS, ...connection.split(',')]) {
    delete result[name.trim().toLowerCase()];
  }
  return result;
}

function createProxy() {
  const target = new URL(
    process.env.ASI1_PLANNER_UPSTREAM ||
    `https://${UPSTREAM_HOST}:${UPSTREAM_PORT}`
  );
  if (
    !['http:', 'https:'].includes(target.protocol) ||
    target.username || target.password ||
    target.pathname !== '/' || target.search || target.hash
  ) {
    throw new Error('ASI1_PLANNER_UPSTREAM doit être une origine HTTP(S).');
  }

  const logPath = process.env.ASI1_PLANNER_LOG_PATH || LOG_PATH;
  fs.mkdirSync(path.dirname(logPath), { recursive: true, mode: 0o700 });
  const fd = fs.openSync(logPath, 'a', 0o600);
  fs.closeSync(fd);

  function log(entry) {
    try {
      fs.appendFileSync(logPath, JSON.stringify(entry) + '\n', {
        encoding: 'utf8', mode: 0o600
      });
    } catch {
      process.stderr.write('asi1-planner-proxy: LOG_WRITE_FAILED\n');
    }
  }

  const server = http.createServer((req, res) => {
    const started = process.hrtime.bigint();
    const elapsed = () => Number(process.hrtime.bigint() - started) / 1e6;
    const entry = {
      timestamp: new Date().toISOString(),
      correlationId: null,
      sessionIdHash: null,
      status: null,
      headersMs: null,
      totalMs: null,
      requestBytes: 0,
      bytesReceived: 0,
      bytesSent: 0,
      errorCode: null
    };

    let upstream;
    let upstreamResponse;
    let connectionTimer;
    let headersTimer;
    let totalTimer;
    let completed = false;
    let failing = false;
    let upstreamEnded = false;

    function clearTimers() {
      clearTimeout(connectionTimer);
      clearTimeout(headersTimer);
      clearTimeout(totalTimer);
    }

    function complete() {
      if (completed) return;
      completed = true;
      clearTimers();
      entry.totalMs = Math.round(elapsed());
      log(entry);
    }

    function stopUpstream() {
      upstreamResponse?.destroy();
      upstream?.destroy();
    }

    function fail(status, code) {
      if (completed || failing) return;
      failing = true;
      entry.errorCode = code;
      clearTimers();
      stopUpstream();

      if (!res.headersSent && !res.destroyed) {
        try {
          entry.status = status;
          const body = Buffer.from(JSON.stringify({ error: code }));
          res.writeHead(status, {
            'content-type': 'application/json',
            'content-length': body.length,
            connection: 'close'
          });
          res.end(body);
          return;
        } catch {
          entry.errorCode = 'RESPONSE_WRITE_FAILED';
        }
      }

      res.destroy();
      complete();
    }

    res.on('finish', complete);
    res.on('close', () => {
      if (!res.writableFinished) {
        entry.errorCode ||= 'CLIENT_DISCONNECTED';
        stopUpstream();
        complete();
      }
    });
    res.on('error', () => fail(502, 'CLIENT_RESPONSE_ERROR'));
    req.on('aborted', () => fail(502, 'CLIENT_DISCONNECTED'));
    req.on('error', () => fail(502, 'CLIENT_REQUEST_ERROR'));

    try {
      entry.correlationId = crypto.randomBytes(16).toString('hex');
      totalTimer = setTimeout(() => fail(504, 'TOTAL_TIMEOUT'), 600_000);

      if (req.headers[PLANNER_HEADER] !== '1' || req.url !== ALLOWED_PATH) {
        req.resume();
        fail(404, 'NOT_FOUND');
        return;
      }

      const chunks = [];
      req.on('data', chunk => {
        entry.requestBytes += chunk.length;
        chunks.push(chunk);
      });

      req.on('end', () => {
        if (completed || failing) return;

        let body;
        try {
          body = JSON.parse(Buffer.concat(chunks).toString('utf8'));
        } catch {
          fail(400, 'INVALID_JSON');
          return;
        }

        if (!body || typeof body !== 'object' || Array.isArray(body)) {
          fail(400, 'INVALID_BODY');
          return;
        }

        const sessionId = body.prompt_cache_key;
        if (
          typeof sessionId !== 'string' ||
          sessionId.trim().length === 0 ||
          Array.from(sessionId).length > 64
        ) {
          fail(400, 'INVALID_PROMPT_CACHE_KEY');
          return;
        }

        try {
          entry.sessionIdHash = crypto.createHash('sha256')
            .update(sessionId).digest('hex').slice(0, 24);

          body.planner_mode = true;
          body['x-session-id'] = sessionId;
          delete body.prompt_cache_key;
          delete body.stream_options;
          body.reasoning_effort = REASONING_EFFORT;
          body.temperature = TEMPERATURE;

          const payload = Buffer.from(JSON.stringify(body));
          const headers = cleanHeaders(req.headers);
          delete headers[PLANNER_HEADER];
          delete headers['accept-encoding'];
          delete headers['x-session-id'];
          delete headers.expect;
          headers.host = target.host;
          headers['content-type'] = 'application/json';
          headers['content-length'] = String(payload.length);

          if (req.headers.authorization !== undefined) {
            headers.authorization = req.headers.authorization;
          }

          const transport = target.protocol === 'https:' ? https : http;
          upstream = transport.request({
            protocol: target.protocol,
            hostname: target.hostname,
            port: target.port || (target.protocol === 'https:' ? 443 : 80),
            method: req.method,
            path: ALLOWED_PATH,
            headers,
            agent: false
          });

          connectionTimer = setTimeout(
            () => fail(504, 'CONNECT_TIMEOUT'), 2_000
          );

          upstream.on('socket', socket => {
            socket.once(
              target.protocol === 'https:' ? 'secureConnect' : 'connect',
              () => {
                if (completed || failing) return;
                clearTimeout(connectionTimer);
                headersTimer = setTimeout(
                  () => fail(504, 'HEADERS_TIMEOUT'), 10_000
                );
              }
            );
          });

          upstream.setTimeout(120_000, () => fail(504, 'IDLE_TIMEOUT'));
          upstream.on('error', () => fail(502, 'UPSTREAM_REQUEST_ERROR'));
          upstream.on('response', response => {
            upstreamResponse = response;
            if (completed || failing) {
              response.destroy();
              return;
            }

            clearTimeout(connectionTimer);
            clearTimeout(headersTimer);
            entry.headersMs = Math.round(elapsed());

            response.on('error', () => fail(502, 'UPSTREAM_RESPONSE_ERROR'));
            response.on('aborted', () => fail(502, 'UPSTREAM_ABORTED'));
            response.on('end', () => { upstreamEnded = true; });
            response.on('close', () => {
              if (!upstreamEnded) fail(502, 'UPSTREAM_PREMATURE_CLOSE');
            });

            const encoding = response.headers['content-encoding'];
            if (encoding && String(encoding).toLowerCase() !== 'identity') {
              fail(502, 'UPSTREAM_COMPRESSION_REJECTED');
              return;
            }

            try {
              const responseHeaders = cleanHeaders(response.headers);
              delete responseHeaders.authorization;
              delete responseHeaders['content-encoding'];

              entry.status = response.statusCode;
              res.writeHead(response.statusCode, responseHeaders);
              res.flushHeaders();

              response.on('data', chunk => {
                entry.bytesReceived += chunk.length;
                if (!res.destroyed) entry.bytesSent += chunk.length;
              });
              response.pipe(res);
            } catch {
              fail(502, 'UPSTREAM_RESPONSE_ERROR');
            }
          });

          upstream.end(payload);
        } catch {
          fail(502, 'INTERNAL_ERROR');
        }
      });
    } catch {
      fail(502, 'INTERNAL_ERROR');
    }
  });

  server.requestTimeout = 0;
  server.headersTimeout = 10_000;
  server.on('clientError', (_error, socket) => {
    if (socket.writable) {
      socket.end('HTTP/1.1 400 Bad Request\r\nConnection: close\r\n\r\n');
    } else {
      socket.destroy();
    }
  });

  server.readTrafficLog = () => fs.readFileSync(logPath, 'utf8');
  return server;
}

module.exports = { createProxy };

if (require.main === module) {
  try {
    const server = createProxy();
    server.on('error', error => {
      process.stderr.write(
        error.code === 'EADDRINUSE'
          ? `asi1-planner-proxy: port ${PORT} déjà occupé sur 127.0.0.1.\n`
          : 'asi1-planner-proxy: échec du socket HTTP local.\n'
      );
      process.exitCode = 1;
    });
    server.listen(PORT, '127.0.0.1');
  } catch {
    process.stderr.write(
      'asi1-planner-proxy: configuration ou journal inaccessible.\n'
    );
    process.exitCode = 1;
  }
}
