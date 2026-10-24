'use strict';
// Native same-agent continuation, never a second inference client.
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const MESSAGE = 'Continue. Ton tour a été interrompu par la limite de sortie du modèle. Vérifie où tu en étais, les résultats déjà obtenus et tes sous-agents avant de poursuivre ; évite les doublons et respecte les autorisations, les budgets et les demandes de précision en attente. Si le travail est terminé, confirme-le sans le recommencer.';
const SOURCE = 'reprise-output-resume';
function apply(ctx, config = {}) {
  if (config.enabled !== true) return;
  const directory = config.stateDirectory;
  const maximum = config.maxConsecutive === undefined ? 2 : config.maxConsecutive;
  if (!path.isAbsolute(directory || '') || !path.isAbsolute(config.projectRoot || '') ||
      !Number.isSafeInteger(maximum) || maximum < 1 || maximum > 10) throw Error('Invalid output-resume configuration');
  const projectRoot = fs.realpathSync(config.projectRoot);
  if (fs.realpathSync(path.dirname(directory)) !== path.resolve(path.dirname(directory))) throw Error('Symlinked output-resume parent');
  fs.mkdirSync(directory, {recursive: true, mode: 0o700});
  if (fs.lstatSync(directory).isSymbolicLink() || !fs.statSync(directory).isDirectory()) throw Error('Invalid output-resume directory');
  const pending = new Map();
  let disposed = false;
  function enabled() {
    let db;
    try {
      const {DatabaseSync} = require('node:sqlite');
      db = new DatabaseSync(path.join(path.dirname(directory), 'tasks.sqlite'), {readOnly: true});
      const row = db.prepare("SELECT value FROM scheduler_meta WHERE key='auto_enabled'").get();
      return row !== undefined && JSON.parse(row.value) === true;
    } catch (_) { return false; }
    finally { if (db) db.close(); }
  }
  function eligible(session, event, agent) {
    if (disposed || !agent || agent.session !== session || agent.status !== 'idle' ||
        agent.session.header?.origin === 'subagent' || !ctx.agents.roots().includes(agent) ||
        ctx.workspaceRegistry.archivedSessionIds.includes(session.id) ||
        agent.inbox.hasPending || agent.inbox.nextStep.length || agent.inbox.nextTurn.length) return null;
    try { if (fs.realpathSync(session.header.cwd) !== projectRoot) return null; } catch (_) { return null; }
    const events = session.snapshotEvents();
    const last = events[events.length - 1];
    if (!last || last.seq !== event.seq || last.type !== 'turn/end' || last.data?.reason?.kind !== 'max-tokens') return null;
    // A pending interactive call must never be turned into implicit permission.
    const calls = new Map();
    for (const item of events) {
      if (item.type === 'tool/call') calls.set(item.data.callId, item.data);
      if (item.type === 'tool/result') calls.delete(item.data.message?.source?.callId);
    }
    if (calls.size) return null;
    let streak = 0;
    for (let i = events.length - 1; i >= 0; i--) {
      const item = events[i];
      if (item.type === 'user/message' && !(item.data?.source?.kind === 'user' && typeof item.data.source.rpcId === 'string' && item.data.source.rpcId.startsWith(SOURCE + ':'))) break;
      if (item.type === 'turn/end') {
        if (item.data?.reason?.kind !== 'max-tokens') break;
        streak++;
      }
    }
    return streak > 0 && streak <= maximum ? streak : null;
  }
  function drive(session) {
    const event = pending.get(session.id);
    if (!event) return;
    const agent = ctx.agents.get(session.id);
    // Keep the live candidate until the native loop reaches idle.
    if (agent && agent.status !== 'idle') return;
    pending.delete(session.id);
    const streak = eligible(session, event, agent);
    if (streak === null || !enabled()) return;
    const key = crypto.createHash('sha256').update(JSON.stringify([session.id, event.seq])).digest('hex');
    let fd;
    try {
      fd = fs.openSync(path.join(directory, key + '.json'), fs.constants.O_WRONLY | fs.constants.O_CREAT | fs.constants.O_EXCL | (fs.constants.O_NOFOLLOW || 0), 0o600);
      const record = {version: 1, sessionHash: crypto.createHash('sha256').update(session.id).digest('hex'), seq: event.seq, streak, status: 'claimed'};
      const persist = () => { fs.ftruncateSync(fd, 0); fs.writeSync(fd, JSON.stringify(record), 0, 'utf8'); fs.fsyncSync(fd); };
      persist();
      const directoryFd = fs.openSync(directory, fs.constants.O_RDONLY);
      try { fs.fsyncSync(directoryFd); } finally { fs.closeSync(directoryFd); }
      // No asynchronous boundary between this final validation and native admission.
      if (eligible(session, event, agent) === null || !enabled()) {
        record.status = 'cancelled'; persist(); return;
      }
      const content = Object.freeze([Object.freeze({type: 'text', text: MESSAGE})]);
      const message = Object.freeze({id: crypto.randomUUID(), role: 'user', content, source: Object.freeze({kind: 'user', rpcId: SOURCE + ':' + key})});
      try { agent.followup(message); record.status = 'queued'; }
      catch (_) { record.status = 'uncertain'; }
      persist();
    } catch (_) {
      // Existing claim or disk failure: never replay or infer successful dispatch.
    } finally { if (fd !== undefined) fs.closeSync(fd); }
  }
  ctx.on('session/event', (session, event) => {
    if (disposed) return;
    if (event.type === 'turn/end' && event.data?.reason?.kind === 'max-tokens' && Number.isSafeInteger(event.seq)) {
      pending.set(session.id, event);
      setImmediate(() => drive(session));
    } else if (pending.has(session.id)) pending.delete(session.id);
  });
  ctx.on('agent/status', ({agent, status}) => {
    if (status === 'idle' && pending.has(agent.session.id)) setImmediate(() => drive(agent.session));
  });
  ctx.effect(() => () => { disposed = true; pending.clear(); });
}
module.exports = {name: 'reprise-output-resume', inject: ['agents', 'sessions', 'workspaceRegistry'], apply};
