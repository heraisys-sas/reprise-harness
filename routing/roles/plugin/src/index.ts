import type { Context } from '@deepseek-ai/cordis'
import type {} from '@deepseek-ai/dsh-agent'
import { ReasoningEffortId } from '@deepseek-ai/dsh-llm'
import type {} from '@deepseek-ai/dsh-system-prompt'
import '@deepseek-ai/dsh-settings'
import z from '@deepseek-ai/schemastery'

const route = z.object({
  provider: z.string().default(''), model: z.string().default(''),
  reasoningEffort: z.union(['inherit', 'off', 'low', 'medium', 'high', 'max']).default('inherit'),
})
export const Config = z.object({
  enabled: z.boolean().default(false).volatile(),
  planner: route.default({} as never), executor: route.default({} as never),
})
type Route = { provider: string; model: string; reasoningEffort: string }
type Settings = { enabled?: boolean | { get(): boolean }; planner?: Route; executor?: Route }
type Agent = { options?: { subagentDepth?: number }; session?: { header?: { origin?: string } }; ctx: Context }
export function roleFor(agent: Agent): 'planner' | 'executor' {
  return (agent.options?.subagentDepth ?? 0) >= 1 || agent.session?.header?.origin === 'subagent' ? 'executor' : 'planner'
}
export function enabled(settings: Settings): boolean {
  const value = settings.enabled
  return !!(typeof value === 'object' ? value.get() : value)
}
export function selectedRoute(agent: Agent, settings: Settings): Route | undefined {
  if (!enabled(settings)) return undefined
  const selected = settings[roleFor(agent)]
  if (!selected?.provider && !selected?.model) return undefined
  if (!selected?.provider || !selected.model) throw Error('Both provider and model are required for a fixed role route')
  return selected
}
export const name = '@heraisys/dsh-role-router'
export const inject = ['llm', 'systemPrompt']
export function apply(ctx: Context, config: Settings) {
  const disposers = new Map<Agent, () => void>()
  ctx.effect(() => () => { for (const dispose of disposers.values()) dispose(); disposers.clear() })
  ctx.on('agent/created', ({ agent }) => {
    const target = agent as unknown as Agent
    // Capture eligibility and route at creation; disabling remains live.
    const role = roleFor(target)
    const chosen = enabled(config) && config[role] ? { ...config[role]! } : undefined
    const dispose = target.ctx.on('agent/request', async (_payload, next) => {
      const resolved = await next()
      if (!enabled(config) || !chosen) return resolved
      if (!selectedRoute(target, { enabled: true, [role]: chosen })) return resolved
      const providers = ctx.llm.listProviders()
      if (!providers.some(p => p.id === chosen.provider)) throw Error('Role provider is not registered')
      const models = await ctx.llm.listModels(chosen.provider)
      if (!enabled(config)) return resolved
      if (!models.some(m => m.id === chosen.model)) throw Error('Role model is absent from the provider catalog')
      return { ...resolved, provider: chosen.provider, model: chosen.model,
        ...(chosen.reasoningEffort !== 'inherit' ? { reasoningEffort: ReasoningEffortId(chosen.reasoningEffort) } : {}) }
    }, { prepend: true })
    disposers.get(target)?.(); disposers.set(target, dispose)
    return undefined
  })
  ctx.on('agent/disposed', ({ agent }) => { disposers.get(agent as unknown as Agent)?.(); disposers.delete(agent as unknown as Agent) })
  ctx.inject(['settings'], sctx => { sctx.effect(() => sctx.settings.configure({ auto: true }, sctx.fiber)) })
  ctx.systemPrompt.section({ name: 'economy-role-router', order: 115,
    text: () => {
      const enabled = config.enabled
      if (!(typeof enabled === 'object' ? enabled.get() : enabled)) return ''
      return 'Configured role routes are fixed for each agent. Keep simple work on the main agent; delegate only independent coherent work. Reuse a child for follow-ups, preserve stable instructions, and validate with project tools. If a child needs expertise beyond its fixed model, return its evidence to the parent rather than creating a retry cascade.'
    } })
}
export default { name, inject, Config, apply }
