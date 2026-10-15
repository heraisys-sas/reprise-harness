import { describe, expect, it } from 'vitest'
import { Config, roleFor, selectedRoute } from '../src/index.ts'
const parent = { ctx: undefined as never, options: {}, session: {header:{}} }
const route = {provider:'mock',model:'executor',reasoningEffort:'low'}
describe('stable roles', () => {
  it('recognizes a root and a persisted child', () => {
    expect(roleFor(parent)).toBe('planner')
    expect(roleFor({...parent,options:{subagentDepth:1}})).toBe('executor')
    expect(roleFor({...parent,session:{header:{origin:'subagent'}}})).toBe('executor')
  })
  it('preserves the selected root model when no fixed planner is configured', () => {
    expect(selectedRoute(parent,{enabled:true,executor:route})).toBeUndefined()
  })
  it('pins only configured child routes', () => {
    expect(selectedRoute({...parent,options:{subagentDepth:1}},{enabled:true,executor:route})).toEqual(route)
  })
  it('keeps the disabled switch authoritative', () => {
    expect(selectedRoute(parent,{enabled:false,planner:route})).toBeUndefined()
  })
  it('rejects incomplete routes without silently guessing', () => {
    expect(()=>selectedRoute(parent,{enabled:true,planner:{...route,provider:''}})).toThrow()
  })
  it('defaults to disabled in the actual loader schema', () => {
    expect(Config({}).enabled.get()).toBe(false)
  })
})

import { apply } from '../src/index.ts'
import type { Context } from '@deepseek-ai/cordis'
it('keeps snapshots stable and disposes agent listeners', async () => {
  const hooks: Record<string, (...args: any[]) => any> = {}
  let request: (...args: any[]) => any = () => undefined
  let disposed = 0
  const settings = { enabled: true, planner: { ...route, model: 'initial' } }
  const ctx = {
    effect: () => {}, inject: () => {}, systemPrompt: { section: () => {} },
    llm: { listProviders: () => [{id:'mock'}], listModels: async () => [{id:'initial'}] },
    on: (event: string, fn: (...args: any[]) => any) => { hooks[event] = fn },
  }
  const agent = { ...parent, ctx: { on: (_: string, fn: (...args: any[]) => any) => {
    request = fn; return () => { disposed++ }
  } } }
  apply(ctx as unknown as Context, settings)
  hooks['agent/created']({agent})
  settings.planner = { ...route, model: '' }
  const next = async () => ({provider:'ui',model:'ui'})
  expect((await request({}, next)).model).toBe('initial')
  settings.enabled = false
  expect((await request({}, next)).model).toBe('ui')
  hooks['agent/disposed']({agent})
  expect(disposed).toBe(1)
})
