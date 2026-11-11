# Usage du dépôt

Règles de placement et d'hygiène pour toute personne ou agent qui modifie cet arbre.
Complète [AGENTS.md](AGENTS.md) (workflow Git, nomenclature, coordination) — en cas de
conflit, la règle la plus stricte gagne.

## L'arbre et sa charte

| Chemin | Contient | Ne contient jamais |
| --- | --- | --- |
| `reprise/` | L'application macOS : modules Python à plat, `swift/`, points d'entrée, assets runtime, `tests/`, `vendor/` | documents durables, artefacts de session |
| `docs/contracts/` | Invariants, limites, garanties encore vraies | procédures, notes datées |
| `docs/integrations/<canal>/` | Un canal par dossier : `codex/`, `claude/`, `harness/`, `apple/`, `ironclaw/` | invariants généraux |
| `docs/runbooks/` | Opérations reproductibles (build, packaging, update, scheduler) | contrats |
| `docs/templates/` | Gabarits réutilisables | autre chose |
| `routing/embeddings/` · `routing/roles/` | Chaque routeur : sources, scripts, jeux d'entrée | résultats d'évaluation |
| `tools/` | Outils d'usage local (`eval/`, `probes/`, `examples/`) | code de l'application |
| `harness/` | Profil Harness (`profile/`) et skills portables (`skills/`) | secrets |
| `archive/` | Code retraité mais réutilisable | documents de session, reçus |
| racine | six fichiers : README, MAINTENANCE, AGENTS, ROADMAP, CHANGELOG, USAGE | tout le reste |

## Placement d'un nouveau fichier

1. Exécutable de l'application ou module importé par elle → `reprise/`, à plat ; une source Swift → `reprise/swift/`.
2. Invariant ou limite encore vraie → le contrat concerné dans `docs/contracts/` ; s'il n'existe pas, le créer avant d'écrire ailleurs.
3. Opération reproductible → `docs/runbooks/`.
4. Sortie d'expérience, reçu, log, inventaire de poste, checkpoint de session → **jamais dans l'arbre** : commentaires de l'issue concernée ou espace privé de l'opérateur.
5. Code retiré mais réutilisable → `archive/` ; sinon Git est l'archive — supprimer sans hésiter un fichier obsolète.
6. Tout nouveau dossier racine ou famille de documentation exige une décision de l'opérateur ; en cas de doute, demander.

## Hygiène de l'historique

- Ne jamais committer : identifiants, clés, reçus de session, inventaires de poste, bases d'état. `state/`, `quick-state/`, `evidence/` et `.env*` sont ignorés — ne pas les forcer avec `-f`.
- L'historique est public pour tous les agents du dépôt : ce qui y entre y reste, et le retirer coûte une réécriture (branches protégées, force-push, resynchronisation de tous les workspaces). La prévention remplace la purge.
- Un document daté qui énonce une règle permanente : distiller la règle dans le contrat concerné, puis supprimer le document — Git le conserve.
- Les résultats d'évaluation se régénèrent par leurs scripts ; seuls les jeux d'entrée et les schémas sont versionnés.

## Workflow Git

- Travail sur `dev`, ou branche d'issue basée sur `dev` nommée `<agent>/<n>-<sujet>` ; jamais de commit direct sur `main`.
- MR `dev` → `main` uniquement avec le go de l'opérateur.
- `dev` est protégée : un force-push exige l'assouplissement temporaire par l'opérateur (protection rétablie immédiatement après). Toute réécriture d'historique est exceptionnelle et coordonnée — tous les workspaces doivent resynchroniser (`git fetch` + `git reset --hard origin/<branche>`).
- Avant de pousser : suite de tests, `swiftc -parse` si Swift touché, `sh -n` si script modifié, et la liste `DOCS` de `package_helpers.py` si la documentation embarquée a bougé. Les échecs pré-existants sont signalés comme tels, jamais corrigés en douceur.
- Suite de tests locale rapide : `python3 reprise/ci_test.py --parallel 8 --reuse-log` — mêmes tests et même partition que les 4 shards CI, exécutés en parallèle sur l'interpréteur CPython épinglé (extrait une fois depuis l'archive en cache `~/Library/Caches/Reprise/python`, sha256 vérifié). Sans `--reuse-log`, la suite tourne toujours. **Si un échec reste à analyser, relire le log complet `reprise/.runtime-py/test-parallel.log` (écrasé à chaque run) au lieu de relancer la suite** — c'est le garde-fou anti-relance : `--reuse-log` ne relance pas si le log date de moins de 24 h (option `--force` pour forcer). Les shards CI macOS vérifiés avec le même découpage (`ci_test.py --shard-index N --shard-count 4`) restent la référence avant fusion ; seul le test swiftc `test_apple_pilot_ui` (Deadline.swift) échoue hors CI par refus sandbox — bruit baseline connu, à signaler comme tel, jamais masqué.

## Identifiants hérités

Les identifiants hérités de l'ancien nommage — label launchd `local.agent-resume`, service Trousseau `local.agent-resume.vault.v1`, `agent-resume-share.zip` avec son arcname, cache Swift `/private/tmp/agent-resume-swift-cache` — ne sont pas des chemins de dépôt et n'ont plus d'adhérence installée (vérification indépendante, sans service ni données qui y tiennent encore). Ils se renomment vers le nommage `reprise` dans une passe unique et coordonnée : code, tests qui assertent ces chaînes, documentation et skills de diagnostic changent ensemble, et les services launchd se réinstallent dans la foulée. Une clé Trousseau existante sous l'ancien service se recrée (l'index et les caches chiffrés sont régénérables) ou se migre explicitement — jamais de perte de chiffrement silencieuse.
