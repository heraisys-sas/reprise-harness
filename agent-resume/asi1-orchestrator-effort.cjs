'use strict';

// Keep the orchestrator at ASI:One's lowest reasoning depth. Without an
// explicit effort, asi1 reasons at its deepest and tends to answer directly
// instead of delegating. Reprise inserts this into the preset; the optional
// preset guard still supports a root-level mount in other compositions.
function apply(ctx, config = {}) {
  const effort = config.effort;
  if (typeof effort !== 'string' || effort.length === 0) {
    throw new Error('asi1-orchestrator-effort: config.effort is required');
  }
  const providers = new Set(config.providers ?? []);
  const models = new Set(config.models ?? []);
  if (providers.size === 0 || models.size === 0) {
    throw new Error('asi1-orchestrator-effort: config.providers and config.models must not be empty');
  }
  const preset = config.preset;
  ctx.on('agent/request', async (payload, next) => {
    const resolved = await next();
    if (!providers.has(resolved.provider) || !models.has(resolved.model)) return resolved;
    if (preset !== undefined) {
      const projections = typeof ctx.get === 'function' ? ctx.get('sessionProjections') : undefined;
      if (projections === undefined || !payload?.agent?.session) return resolved;
      if (projections.stateOf(payload.agent.session, 'agentPreset') !== preset) return resolved;
    }
    if (resolved.reasoningEffort === effort) return resolved;
    return { ...resolved, reasoningEffort: effort };
  }, { prepend: true });
}

module.exports = { name: 'asi1-orchestrator-effort', apply };
