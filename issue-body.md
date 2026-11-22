**Constat (validation réelle du lot #255, 22/11/2026 18h20, dev 072bce1, release b2630f38 déployé)**

Le parcours inspect → plan → apply → verify a fonctionné sur `rechatgpt` (apply = update-off + freeze-record, manifeste créé, garde `target_running` prouvée). Mais `verify` est incohérent avec `plan` pour `reclaude` :

- `clone-plan` → `reclaude: []` (aucune issue : identité, signature, su_off OK)
- `clone-verify` → `reclaude: {ok: false, problems: ["not_frozen"]}`

Cause (`reprise/app_clones.py`) :
- `verify()` (l.304-308) ajoute `not_frozen` pour toute app présente absente du manifeste, sans regarder `kind`.
- `plan()` (l.214-229) n'ajoute `freeze-record` que DANS la boucle des issues : une app sans issue n'obtient jamais d'action, donc `apply` ne peut jamais résoudre `not_frozen`.

**Effet** : `verify` ne peut jamais retourner `ok: true` sur un système où une app patched saine (ReClaude, recréée/re-signée à chaque mise à jour Desktop par l'outillage d'intégration) n'a pas d'entrée de gel. État observable : `clone-verify` → `ok: false` permanent.

**Arbitrage à trancher avant correctif** :
1. Le gel (manifeste) concerne-t-il uniquement `kind: 'clone'` (contrat APP-CLONES : clones figés) et pas `kind: 'patched'` ? → alors `verify` ne devrait exiger le gel que pour les clones.
2. Ou les apps patched doivent-elles aussi être gelées ? → alors `plan` doit proposer `freeze-record` même sans issue (hors boucle).

**Option retenue a priori** (à confirmer) : (1) — `verify` conditionne `not_frozen` à `kind == 'clone'`. ReClaude est re-patchée volontairement à chaque montée de Claude Desktop ; la figer masquerait ce cycle.

**Preuves** :
- `clone-plan` : `{"rechatgpt": ["update-off","freeze-record"], "reclaude": []}`
- `clone-apply rechatgpt` → `applied`, manifeste `~/Library/Application Support/Reprise/app-clones.json` créé (frozen 26.1111.21308, upstream 26.1117.41037)
- `clone-apply rechatgpt` (app active, pid 69315) → `refused` `target_running:69315`, `steps: []` — garde OK
- `clone-verify` → `rechatgpt` problems `["refresh"]` (upstream_newer, attendu), `reclaude` `["not_frozen"]`

Lié : #255 (lot UI), contrat `docs/contracts/APP-CLONES-CONTRACT.md`.
