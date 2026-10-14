import { describe, expect, it } from 'vitest'
import { mkdtempSync, writeFileSync, chmodSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { classifyTier } from '../src/upstream-heuristic.ts'
import { readFileSync } from 'node:fs'
import { classifyHybrid } from '../src/hybrid-routing.ts'
async function helper(tier: string, margin: number, test: (path: string) => Promise<void>) {
  const root=mkdtempSync(join(tmpdir(), 'hybrid-test-')), path=join(root,'helper')
  const output=JSON.stringify([{ available:true,tier,score:.8,margin,language:'en',revision:1 }])
  writeFileSync(path, `#!/bin/sh\nprintf '%s' '${output}'`);chmodSync(path,0o700)
  try { await test(path) } finally { rmSync(root,{recursive:true,force:true}) }
}
describe('hybrid arbitration', () => {
  it('keeps upstream complex when a semantic match suggests less', async () => {
    await helper('standard', .1, async path => {
      expect((await classifyHybrid('', 'Prove that this algorithm terminates.', path)).tier).toBe('complex')
    })
  })
  it('keeps upstream on an insufficient margin', async () => {
    await helper('light', .03, async path => {
      expect((await classifyHybrid('', 'An unfamiliar request.', path)).tier).toBe('trivial')
    })
  })
  it('accepts separated evidence for a non-complex baseline', async () => {
    await helper('light', .1, async path => {
      expect((await classifyHybrid('', 'An unfamiliar request.', path)).tier).toBe('light')
    })
  })
  it('retains upstream after helper failure', async () => {
    expect((await classifyHybrid('', 'Implement a new operation.', '/missing/helper')).tier).toBe('complex')
  })
  it('uses translation as a cheap upstream fast path', async () => {
    expect(await classifyHybrid('', 'Translate a short error message.', '/missing/helper')).toMatchObject({tier:'light',source:'upstream-fast-path'})
  })
  it('recognizes simple technical consultation without semantic work', async () => {
    expect(await classifyHybrid('', 'Locate the function declaration.', '/missing/helper')).toMatchObject({tier:'trivial',source:'lookup-rule'})
  })
  it('does not treat composite work as consultation', async () => {
    expect((await classifyHybrid('', 'List the files then implement the fix.', '/missing/helper')).tier).toBe('complex')
  })
  it('preserves the original heuristic results on the recorded English set', () => {
    const original = JSON.parse(readFileSync(new URL('../../evaluation-baseline-english.json', import.meta.url), 'utf8'))
    for (const row of original.rows) expect(classifyTier('', row.prompt)).toBe(row.tier)
  })

})
