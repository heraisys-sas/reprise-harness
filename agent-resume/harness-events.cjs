const fs = require('node:fs');
const zlib = require('node:zlib');
const file = process.argv[2];
if (fs.statSync(file).size > 32 * 1024 * 1024) throw Error('Session too large');
const raw = fs.readFileSync(file);
const bytes = file.endsWith('.zstd') ? zlib.zstdDecompressSync(raw, {maxOutputLength: 64 * 1024 * 1024}) : raw;
let last = null;
for (const line of bytes.toString('utf8').split('\n')) {
  if (!line) continue;
  const event = JSON.parse(line);
  if (event.type === 'turn/start') last = {kind: 'open'};
  if (event.type === 'turn/end') {
    const reason = event.data?.reason;
    last = {kind: reason?.kind || 'unknown', code: reason?.error?.code || null};
  }
}
process.stdout.write(JSON.stringify(last));
