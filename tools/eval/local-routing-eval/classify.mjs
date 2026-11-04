import fs from 'node:fs'
import { classifyTier } from '../../routing/plugin/lib/upstream-heuristic.js'
import { classifyLocal } from '../../routing/plugin/lib/apple-routing.js'
import { classifyHybrid } from '../../routing/plugin/lib/hybrid-routing.js'

const [mode, file] = process.argv.slice(2)
const cases = JSON.parse(fs.readFileSync(file, 'utf8'))
const classify = prompt => mode === 'rules' ? { tier: classifyTier('', prompt), source: 'upstream-rules' }
  : mode === 'rules-sentence' ? classifyLocal('', prompt) : classifyHybrid('', prompt)
if (!['rules', 'rules-sentence', 'hybrid'].includes(mode)) throw Error('Unknown mode')
const rows = []
for (const item of cases) {
  const t = performance.now(), first = await classify(item.prompt), firstMs = performance.now()-t
  const w = performance.now(), again = await classify(item.prompt)
  rows.push({ id: item.id, expected: item.expected, critical: item.critical, ...first,
    status: first.source.startsWith('fallback:') ? 'fallback' : 'ok',
    first_ms: firstMs, repeat_ms: performance.now()-w, repeat_tier: again.tier,
    correct: first.tier === item.expected })
}
console.log(JSON.stringify({ mode, rows, note: 'Fresh process per arm. OS model assets may be warm; repeat measures in-process cache. Not remote billing.' }))
