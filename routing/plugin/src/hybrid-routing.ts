import { classifyLocal, type Classification } from './apple-routing.ts'
import { classifyTier } from './upstream-heuristic.ts'

export async function classifyHybrid(description: string, prompt: string, helper?: string, signal?: AbortSignal, engine: 'sentence' | 'contextual' = 'sentence'): Promise<Classification> {
  const baseline = classifyTier(description, prompt)
  if (/^(?:translate|traduis)\b/iu.test(prompt.trim()) && !/\b(then|puis|implement|debug|design)\b/iu.test(prompt)) {
    return { tier: baseline, source: 'upstream-fast-path', baseline, arbitration: 'explicit-translation' }
  }
  const local = await classifyLocal(description, prompt, helper, signal, engine)
  let tier = baseline, arbitration = 'keep-upstream'
  if (local.source.endsWith('-rule')) {
    tier = local.tier; arbitration = 'explicit-local-rule'
  } else if (local.source.startsWith('apple-embedding') && local.margin !== undefined && local.margin >= 0.04) {
    if (baseline !== 'complex' || local.tier === 'complex') {
      tier = local.tier; arbitration = 'separated-semantic-match'
    }
  }
  return { ...local, tier, baseline, arbitration }
}
