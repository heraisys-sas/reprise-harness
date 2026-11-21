'use strict';

// Keep the orchestrator at ASI:One's lowest reasoning depth. Without an
// explicit effort, asi1 reasons at its deepest and tends to answer directly
// instead of delegating. Reprise inserts this into the preset.
//
// Since agent-loop v0.2.1-alpha.4, the deprecated `agent/request` hook may no
// longer add, change, or clear reasoning effort (it only fixes sampling and
// other controls); the selection must be recorded before step preparation via
// agent.model.select(). We record it at agent/created, which precedes every
// step, so the captured selection already carries the effort.
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
  ctx.on('agent/created', (payload) => {
    const agent = payload?.agent;
    if (!agent || typeof agent.model?.select !== 'function') return;
    const current = agent.model.current();
    if (!current) return;
    if (!providers.has(current.provider) || !models.has(current.model)) return;
    if (preset !== undefined) {
      const projections = typeof ctx.get === 'function' ? ctx.get('sessionProjections') : undefined;
      if (projections === undefined || !agent.session) return;
      if (projections.stateOf(agent.session, 'agentPreset') !== preset) return;
    }
    if (current.reasoningEffort === effort) return;
    agent.model.select({ ...current, reasoningEffort: effort });
  });
}

module.exports = { name: 'asi1-orchestrator-effort', apply };
