const fs = require('node:fs');
const zlib = require('node:zlib');
const file = process.argv[2];
const LIMIT = 64 * 1024 * 1024;
if (fs.statSync(file).size > 32 * 1024 * 1024) throw Error('Session too large');
const raw = fs.readFileSync(file);
let bytes = raw;
if (file.endsWith('.zstd')) {
  // Harness appends independent frames. Node stops after the first frame;
  // bytesWritten is the decoder's consumed input, not a magic-byte search.
  let offset = 0, size = 0;
  const parts = [];
  while (offset < raw.length) {
    if (size >= LIMIT) throw Error('Session too large');
    const decoded = zlib.zstdDecompressSync(raw.subarray(offset), {
      maxOutputLength: LIMIT - size, info: true,
    });
    const consumed = decoded.engine.bytesWritten;
    if (!Number.isSafeInteger(consumed) || consumed <= 0 || consumed > raw.length - offset)
      throw Error('Invalid compressed frame');
    parts.push(decoded.buffer);
    size += decoded.buffer.length;
    offset += consumed;
  }
  bytes = Buffer.concat(parts, size);
}
if (bytes.length > LIMIT) throw Error('Session too large');
let last = null;
for (const line of bytes.toString('utf8').split('\n')) {
  if (!line) continue;
  const event = JSON.parse(line);
  const identity = {seq: event.seq ?? null, time: event.time ?? null,
                    turn: event.data?.turn ?? null};
  if (event.type === 'turn/start') last = {kind: 'open', ...identity};
  if (event.type === 'user/message' && last?.kind === 'max-tokens')
    last = {kind: 'pending_input', ...identity};
  if (event.type === 'turn/end') {
    const reason = event.data?.reason;
    last = {kind: reason?.kind || 'unknown', code: reason?.error?.code || null, ...identity};
  }
}
process.stdout.write(JSON.stringify(last));
