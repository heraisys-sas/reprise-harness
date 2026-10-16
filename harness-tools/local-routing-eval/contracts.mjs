import { classifyTier } from '../../routing/plugin/lib/upstream-heuristic.js'
import { classifyHybrid } from '../../routing/plugin/lib/hybrid-routing.js'
const prompt = 'Assess whether the chosen approach meets the stated goal.'
const expected = classifyTier('', prompt)
const unavailable = await classifyHybrid('', prompt, '/private/tmp/reprise-no-such-helper')
const controller = new AbortController()
controller.abort()
const cancelled = await classifyHybrid('', prompt, undefined, controller.signal)
const results = [
  { name: 'missing-helper-keeps-baseline', passed: unavailable.tier === expected && unavailable.source.startsWith('fallback:') },
  { name: 'cancelled-keeps-baseline', passed: cancelled.tier === expected && cancelled.source === 'fallback:cancelled' },
]
console.log(JSON.stringify({ results, actual: { unavailable, cancelled }, passed: results.every(r => r.passed) }))
if (!results.every(r => r.passed)) process.exitCode = 1
