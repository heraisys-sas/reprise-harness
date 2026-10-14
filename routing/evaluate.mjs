import { classifyLocal } from './plugin/lib/apple-routing.js'
const engine = process.argv[2] === 'contextual' ? 'contextual' : 'sentence'
const cases = [
  ['Liste les fichiers Python.', 'trivial'],
  ['Trouve les usages de cette fonction.', 'trivial'],
  ['Corrige la couleur du libellé.', 'light'],
  ['Renomme cette variable.', 'light'],
  ['Évalue les garanties de cohérence de ce système distribué.', 'complex'],
  ['Analyse les frontières de sécurité du service.', 'complex'],
  ['Démontre que cet algorithme termine pour toutes les entrées.', 'complex'],
  ['Liste les fichiers puis implémente le correctif et ses tests.', 'standard'],
  ['Ajoute un contrôle du formulaire et un test associé.', 'standard'],
  ['Répertorie les fichiers de configuration du projet.', 'trivial'],
  ['Traduis ce court message en anglais.', 'light'],
  ['Élabore une approche globale pour moderniser progressivement le logiciel.', 'complex'],
  ['List all configuration files.', 'trivial'],
  ['Rename the button label.', 'light'],
  ['Evaluate consistency guarantees of this distributed system.', 'complex'],
  ['Explain the tradeoffs between alternative algorithms.', 'complex'],
  ['Write regression tests for the parser.', 'standard'],
  ['Locate the function declaration in the source tree.', 'trivial'],
  ['Translate the error message into French.', 'light'],
  ['Improve the wording of this heading.', 'light'],
]
const rows = []
for (const [prompt, expected] of cases) {
  const start = performance.now()
  const result = await classifyLocal('', prompt, undefined, undefined, engine)
  rows.push({ prompt, expected, ...result, ms: Math.round(performance.now() - start), correct: result.tier === expected })
}
const start = performance.now()
await classifyLocal('', 'Explain the tradeoffs between alternative algorithms.', undefined, undefined, engine)
console.log(JSON.stringify({ engine, label: 'Small synthetic diagnostic set; not an independent benchmark', cases: rows.length, exact: rows.filter(r => r.correct).length, embeddingCalls: rows.filter(r => r.source.startsWith('apple-embedding')).length, cachedMs: performance.now() - start, rows }, null, 2))
