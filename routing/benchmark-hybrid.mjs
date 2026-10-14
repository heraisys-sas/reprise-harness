import fs from 'node:fs'
import { classifyTier } from './plugin/lib/upstream-heuristic.js'
import { classifyLocal } from './plugin/lib/apple-routing.js'
import { classifyHybrid } from './plugin/lib/hybrid-routing.js'
const mode = process.argv[2] || 'hybrid'
const dataset = JSON.parse(fs.readFileSync(process.argv[3] || new URL('./dataset-holdout.json', import.meta.url), 'utf8'))
const helper = process.argv[4]
const classify = async prompt => mode === 'upstream' ? { tier: classifyTier('', prompt), source: 'upstream' } : (mode === 'local' ? classifyLocal : classifyHybrid)('', prompt, helper)
const percentile = (values, fraction) => values.sort((a,b) => a-b)[Math.min(values.length-1, Math.floor(values.length * fraction))] ?? 0
const rows = []
for (const item of dataset.rows) {
  const start = performance.now(), result = await classify(item.prompt)
  rows.push({ ...item, ...result, ms: performance.now()-start, correct: result.tier === item.expected })
}
const warm = []
for (const item of dataset.rows) {
  const start = performance.now()
  for (let n=0; n<10; n++) await classify(item.prompt)
  warm.push((performance.now()-start)/10)
}
const embeddings = rows.filter(r => r.source?.startsWith('apple-embedding'))
console.log(JSON.stringify({ mode, note: 'Cache-cold classification in a fresh Node process; OS assets may already be warm. Latency excludes remote LLM generation.', cases: rows.length, exact: rows.filter(r => r.correct).length,
  calls: embeddings.length, firstPassP50Ms: percentile(rows.map(r => r.ms), .5), firstPassP95Ms: percentile(rows.map(r => r.ms), .95),
  embeddingP50Ms: percentile(embeddings.map(r => r.ms), .5), embeddingP95Ms: percentile(embeddings.map(r => r.ms), .95),
  cachedP50Ms: percentile(warm, .5), cachedP95Ms: percentile(warm, .95),
  nativePreparationP50Ms: percentile(embeddings.map(r => r.timings?.preparationMs ?? 0), .5),
  nativeQueryP50Ms: percentile(embeddings.map(r => r.timings?.queryMs ?? 0), .5),
  nativeScoringP50Ms: percentile(embeddings.map(r => r.timings?.scoringMs ?? 0), .5),
  nativeHelperP50Ms: percentile(embeddings.map(r => r.timings?.helperMs ?? 0), .5), rows }, null, 2))
