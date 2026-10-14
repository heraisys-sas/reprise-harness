import { spawn } from 'node:child_process'
import { createHash } from 'node:crypto'
import { fileURLToPath } from 'node:url'

export type Tier = 'trivial' | 'light' | 'standard' | 'complex'
export type Classification = { tier: Tier; source: string; score?: number; margin?: number; language?: string; revision?: number; engine?: string; baseline?: Tier; arbitration?: string; timings?: Record<string, number> }
const ranks: Tier[] = ['trivial', 'light', 'standard', 'complex']
const cache = new Map<string, Classification>()
let active = 0

function rule(text: string): Classification | undefined {
  if (/\b(consensus|preuve|proof|démontre|demontrer|prouve|cohérence|cryptograph\w*|chiffrement|cohérence|coherence|invariants?|deadlock|interblocage|authentification|authentication|sécurité|securite|security|distributed|distribué\w*|concurrence|concurrency)\b/iu.test(text)
      || /(?:python|javascript|typescript).{0,35}(?:rust)/iu.test(text)) return { tier: 'complex', source: 'risk-rule' }
  const compound = /(?:\b(?:puis|then|ensuite|implement|implémente|bug|cause|correctif|diagnostic|pourquoi|why|tests?)\b)/iu.test(text)
  if (!compound && /^(?:liste|list|affiche|show|trouve|find|cherche|search|repère|repere|retrouve|read|lis|locate|enumerate|répertorie)\b/iu.test(text.trim())) return { tier: 'trivial', source: 'lookup-rule' }
  if (!compound && /^(?:renomme|rename|corrige|change|modifie|remove|supprime)\b/iu.test(text.trim())
      && /\b(couleur|color|libellé|label|titre|title|typo|faute|bouton|button|variable)\b/iu.test(text)) return { tier: 'light', source: 'edit-rule' }
  return undefined
}

export function classifyLocal(description: string, prompt: string, executable?: string, signal?: AbortSignal, engine: 'sentence' | 'contextual' = 'sentence'): Promise<Classification> {
  if (signal?.aborted) return Promise.resolve({ tier: 'standard', source: 'fallback:cancelled' })
  const text = (prompt.trim() || description).slice(0, 4000)
  const explicit = rule(text)
  if (explicit) return Promise.resolve(explicit)
  const helper = executable || fileURLToPath(new URL('../native/RouteEmbedding', import.meta.url))
  const key = createHash('sha256').update(helper).update(engine).update('\0').update(text).digest('hex')
  const hit = cache.get(key)
  if (hit) return Promise.resolve({ ...hit, source: `${hit.source}:cache` })
  if (active >= 2) return Promise.resolve({ tier: 'standard', source: 'fallback:busy' })
  const task = new Promise<Classification>(resolve => {
    active++
    let done = false, output = ''
    const child = spawn(helper, [fileURLToPath(new URL('../native/examples.json', import.meta.url))], { shell: false, stdio: ['pipe', 'pipe', 'ignore'], env: {} })
    const finish = (result: Classification) => {
      if (done) return
      done = true; clearTimeout(timer); signal?.removeEventListener('abort', cancel); active--
      if (result.source === 'apple-embedding') {
        if (cache.size >= 128) cache.delete(cache.keys().next().value!)
        cache.set(key, result)
      }
      resolve(result)
    }
    const cancel = () => { child.kill(); finish({ tier: 'standard', source: 'fallback:cancelled' }) }
    const timer = setTimeout(() => { child.kill(); finish({ tier: 'standard', source: 'fallback:timeout' }) }, 2000)
    signal?.addEventListener('abort', cancel, { once: true })
    child.on('error', () => finish({ tier: 'standard', source: 'fallback:helper-unavailable' }))
    child.stdin.on('error', () => {})
    child.stdout.on('data', chunk => {
      output += chunk.toString()
      if (output.length > 8192) { child.kill(); finish({ tier: 'standard', source: 'fallback:oversize' }) }
    })
    child.on('close', code => {
      if (done) return
      try {
        const result = JSON.parse(output)?.[0]
        if (code !== 0 || result?.available !== true || !ranks.includes(result.tier)
            || !Number.isFinite(result.score) || !Number.isFinite(result.margin)
            || !['fr', 'en'].includes(result.language) || !Number.isInteger(result.revision)) throw Error()
        finish({ tier: result.ambiguous ? 'standard' : result.tier, source: 'apple-embedding', score: result.score, margin: result.margin, language: result.language, revision: result.revision, engine: result.engine, timings: result.timings })
      } catch { finish({ tier: 'standard', source: 'fallback:invalid-output' }) }
    })
    child.stdin.end(JSON.stringify({ texts: [text], engine }))
  })
  return task
}
