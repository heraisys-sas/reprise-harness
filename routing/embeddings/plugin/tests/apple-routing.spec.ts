import { describe, expect, it } from 'vitest'
import { mkdtempSync, writeFileSync, chmodSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { classifyLocal } from '../src/apple-routing.ts'

async function fake(body: string, run: (path: string) => Promise<void>) {
  const root = mkdtempSync(join(tmpdir(), 'routing-test-'))
  const path = join(root, 'helper')
  writeFileSync(path, '#!/bin/sh\n' + body); chmodSync(path, 0o700)
  try { await run(path) } finally { rmSync(root, { recursive: true, force: true }) }
}
describe('bounded local classifier', () => {
  it('falls back conservatively when the executable is missing', async () => {
    expect(await classifyLocal('', 'Une demande sans catégorie explicite.', '/nonexistent/reprise-helper')).toMatchObject({ tier: 'standard', source: 'fallback:helper-unavailable' })
  })
  it('rejects malformed output without caching the failure', async () => {
    await fake("printf '%s' 'not-json'", async path => {
      for (let n = 0; n < 2; n++) expect((await classifyLocal('', 'Texte ambigu.', path)).source).toBe('fallback:invalid-output')
    })
  })
  it('caches successful classifications in memory', async () => {
    await fake(`printf '%s' '[{"available":true,"tier":"complex","score":0.8,"margin":0.1,"language":"fr","revision":1}]'`, async path => {
      expect((await classifyLocal('', 'Texte sans règle.', path)).source).toBe('apple-embedding')
      expect((await classifyLocal('', 'Texte sans règle.', path)).source).toBe('apple-embedding:cache')
    })
  })
  it('maps ambiguous similarities to standard', async () => {
    await fake(`printf '%s' '[{"available":true,"tier":"trivial","ambiguous":true,"score":0.8,"margin":0.001,"language":"fr","revision":1}]'`, async path => {
      expect((await classifyLocal('', 'Texte indéterminé.', path)).tier).toBe('standard')
    })
  })
  it('stops a stuck helper after two seconds', async () => {
    await fake('exec /bin/sleep 10', async path => {
      const start = Date.now()
      expect((await classifyLocal('', 'Texte difficile à classer.', path)).source).toBe('fallback:timeout')
      expect(Date.now() - start).toBeLessThan(3000)
    })
  })
  it('handles cancellation without spawning a helper', async () => {
    const abort = new AbortController(); abort.abort()
    expect((await classifyLocal('', 'Texte ambigu.', '/unused', abort.signal)).source).toBe('fallback:cancelled')
  })
  it('uses the real Apple model for an English conceptual paraphrase', async () => {
    if (process.platform !== 'darwin') return
    const result = await classifyLocal('Assessment', 'Explain the tradeoffs between alternative algorithms.')
    expect(result.source).toBe('apple-embedding')
    expect(result.language).toBe('en')
    expect(result.tier).toBe('complex')
  })
  it('does not short-circuit compound lookup requests as trivial', async () => {
    const result = await classifyLocal('', 'Liste les fichiers puis implémente le correctif et ses tests.', '/missing/helper')
    expect(result).toMatchObject({ tier: 'standard', source: 'fallback:helper-unavailable' })
  })
  it('keeps cancellation independent for simultaneous identical requests', async () => {
    await fake(` /bin/sleep 0.15
printf '%s' '[{"available":true,"tier":"complex","score":0.8,"margin":0.1,"language":"fr","revision":1}]'`, async path => {
      const abort = new AbortController()
      const first = classifyLocal('', 'Deux demandes identiques.', path, abort.signal)
      const second = classifyLocal('', 'Deux demandes identiques.', path)
      abort.abort()
      expect((await first).source).toBe('fallback:cancelled')
      expect((await second).source).toBe('apple-embedding')
    })
  })

  it('keeps sentence and contextual cache entries separate', async () => {
    await fake(`printf '%s' '[{"available":true,"tier":"complex","score":0.8,"margin":0.1,"language":"fr","revision":1}]'`, async path => {
      expect((await classifyLocal('', 'Même demande pour deux moteurs.', path, undefined, 'sentence')).source).toBe('apple-embedding')
      expect((await classifyLocal('', 'Même demande pour deux moteurs.', path, undefined, 'contextual')).source).toBe('apple-embedding')
    })
  })

})
