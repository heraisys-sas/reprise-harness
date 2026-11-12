import fs from 'node:fs'
import { fileURLToPath } from 'node:url'
import { classifyLocal } from './plugin/lib/apple-routing.js'
const root = fileURLToPath(new URL('.', import.meta.url))
const dataset = JSON.parse(fs.readFileSync(root + 'dataset-english.json', 'utf8'))
const baseline = JSON.parse(fs.readFileSync(root + 'evaluation-baseline-english.json', 'utf8'))
const results = [{ engine: 'upstream-0.6.0', exact: baseline.exact, rows: baseline.rows }]
for (const engine of ['rules', 'sentence', 'contextual']) {
  const rows = []
  for (const item of dataset.rows) {
    const start = performance.now()
    const result = await classifyLocal('', item.prompt, engine === 'rules' ? '/nonexistent/reprise-helper' : undefined, undefined, engine === 'contextual' ? 'contextual' : 'sentence')
    rows.push({ ...item, ...result, correct: result.tier === item.expected, ms: performance.now() - start })
  }
  results.push({ engine, exact: rows.filter(r => r.correct).length, rows })
}
const output = { language: 'en', cases: dataset.rows.length, note: dataset.note, results }
fs.writeFileSync(root + 'comparison-english.json', JSON.stringify(output, null, 2) + '\n')
for (const r of results) console.log(`${r.engine}: ${r.exact}/${dataset.rows.length}`)
