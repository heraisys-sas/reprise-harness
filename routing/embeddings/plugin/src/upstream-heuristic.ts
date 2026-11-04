import type { Tier as AutoTier } from './apple-routing.ts'

const COMPLEX_MARKERS = [
  /```|=>|#include|require\(/,
  /\b(function|class|interface|import|export|const|def)\s/,
  /\b(JSON|schema|structured|matrix|architecture|algorithm)\b/i,
  /\b(analy[sz]e|architect|design|optimize|debug|refactor|synthesi[sz]e|evaluate|investigate|research|derive|proof|implement|migrate|complex)\b/i,
]

/** Model-id signals for a strong / reasoning model. */
export const STRONG_MODEL = /\b(pro|max|reason|think|ultra|code|turbo|large|deep)\b/i

/** Model-id signals for a cheap / fast model. */
export const LIGHT_MODEL = /\b(flash|mini|lite|fast|small|quick|nano|light)\b/i

/**
 * 任务动词 → 复杂度档位字典。
 * 中英双语，提取 prompt 首句中的核心动词来判断任务复杂度。
 * 按优先级排序：complex 先匹配（避免"分析"被光标的"列"抢先）。
 */
const VERB_TIER_MAP: ReadonlyArray<{ verbs: readonly string[]; tier: AutoTier }> = [
  // complex：分析/设计/研究类
  { verbs: ['分析', '研究', '调研', '评估', '调查', '推导', '架构', '设计', '综合', '迁移', '集成', '验证'], tier: 'complex' },
  { verbs: ['analyze', 'analyse', 'research', 'investigate', 'design', 'evaluate', 'synthesize', 'derive', 'architect', 'migrate', 'integrate', 'validate', 'explain', 'explore'], tier: 'complex' },
  // standard：实现/重构/开发类
  { verbs: ['实现', '重构', '优化', '调试', '编写', '修改', '构建', '开发', '测试', '配置', '部署'], tier: 'standard' },
  { verbs: ['implement', 'refactor', 'optimize', 'debug', 'write', 'modify', 'build', 'develop', 'test', 'configure', 'deploy'], tier: 'standard' },
  // light：简单操作
  { verbs: ['总结', '翻译', '创建', '更新', '添加', '删除', '复制', '移动', '重命名', '转换', '提取', '生成'], tier: 'light' },
  { verbs: ['summarize', 'summarise', 'translate', 'create', 'update', 'add', 'delete', 'copy', 'move', 'rename', 'convert', 'extract', 'generate'], tier: 'light' },
  // trivial：查询/查看类
  { verbs: ['查看', '列出', '显示', '搜索', '检查', '读取', '寻找', '查询', '浏览', '打开'], tier: 'trivial' },
  { verbs: ['列', '查', '找', '看'], tier: 'trivial' }, // 单字动词（仅匹配开头/礼貌前缀后，防误配复合词）
  { verbs: ['list', 'show', 'find', 'check', 'read', 'search', 'look', 'browse', 'open', 'view', 'ls', 'cat', 'grep'], tier: 'trivial' },
]

/**
 * 中文单字动词的已知复合词前缀。
 * 当单字动词前是这些字时，该字属于复合词的一部分而非独立动词。
 * 如"序列"中"列"不是独立动词 → 不匹配；"请列一下"中"列"是独立动词 → 匹配。
 */
const COMPOUND_PREFIX: Record<string, string> = { '列': '序列排陈罗', '查': '调查检审普', '找': '寻找', '看': '查看' }

/**
 * 从 prompt 文本中提取核心动词并返回对应档位。
 * 匹配策略：按字典序依次匹配，先匹配到的动词的档位为结果。
 * 匹配范围：prompt 首句（前 200 字符，按标点截断）。
 * 中文单字动词（"列""查""找""看"）仅匹配开头 8 字符内，且排除已知复合词前缀。
 */
function extractVerbTier(text: string): AutoTier | undefined {
  // 取首句：按句号/问号/感叹号/换行截断，取前 200 字符
  const sentenceEnd = text.search(/[。？！\n!?.]\s|$/)
  const head = text.slice(0, sentenceEnd >= 0 && sentenceEnd <= 200 ? sentenceEnd + 1 : 200).toLowerCase()
  for (const entry of VERB_TIER_MAP) {
    for (const verb of entry.verbs) {
      // 中文单字动词：排除已知复合词前缀，其余情况匹配
      if (verb.length === 1 && /[\u4e00-\u9fff]/.test(verb)) {
        const wideStart = head.slice(0, 8)
        const idx = wideStart.indexOf(verb)
        if (idx >= 0) {
          if (idx === 0) return entry.tier // 开头直接匹配
          const prevChar = wideStart[idx - 1]
          const badPrefixes = COMPOUND_PREFIX[verb] || ''
          if (!badPrefixes.includes(prevChar)) return entry.tier
        }
      } else if (head.includes(verb.toLowerCase())) {
        return entry.tier
      }
    }
  }
  return undefined
}

/** Classify a delegation task into the auto-selection tier. */
export function classifyTier(description: string, prompt: string): AutoTier {
  const task = `${description}\n${prompt}`

  // 1. Length-based complex fast path: enough text to be complex regardless of content
  if (task.length >= 1200) return 'complex'

  // 2. Code/structured markers → complex (language-agnostic)
  if (COMPLEX_MARKERS.some(marker => marker.test(task))) return 'complex'

  // 3. Verb extraction from prompt (more reliable) and description (fallback)
  const verbTier = extractVerbTier(prompt) ?? extractVerbTier(description)
  if (verbTier !== undefined) return verbTier

  // 4. Fallback: length-based heuristic (4 tiers instead of 3)
  if (task.length <= 160) return 'trivial'
  if (task.length <= 400) return 'light'
  return 'standard'
}

