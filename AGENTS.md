# Contexte de travail — projet Reprise & Harness

Instructions de projet pour les agents qui travaillent sur ce dépôt. Complète (sans écraser) les préférences globales de Harness (`~/.dsh/AGENTS.md`). Répondre en français, identifiants techniques en anglais.

## Règles d'or
1. Jamais de commit direct sur `main` ; travail sur une branche liée à l'issue, intégrée à `dev`.
2. Réserver l'issue (label `agent:<nom>`, branche liée, `agent-claim`) avant d'y travailler.
3. Utiliser les fonctionnalités prévues de Reprise sans les contourner ; tracer leurs manques dans une issue.
4. Re-vérifier SHAs et chiffres avant tout effet de bord (merge, push, fermeture).
5. Ne jamais modifier directement SQLite ni franchir une protection ; conserver l'incertitude.

Ce fichier garde les règles ; le détail est lié par sujet :
- [USAGE.md](USAGE.md) : placement des fichiers et hygiène de l'arbre ;
- [Workflow des agents](docs/runbooks/AGENT-WORKFLOW.md) : clones et worktrees, commandes, escalade, délégation ASI1, procédure de correction, protocole du coordinateur ;
- [Canaux de délégation](docs/contracts/DELEGATION-CHANNELS.md) : Codex, Claude (QTi, bridge MCP), ChatGPT, Meta AI, Quick Apply et faits techniques établis ;
- [Passation entre harness](docs/contracts/HANDOFF-CONTRACT.md).

## Ce projet est
Une application macOS de **supervision** (SwiftUI/AppKit + helpers Python) qui suit les sessions d'agents, détecte les anomalies post-update, lit les journaux locaux de Claude Desktop et sert de canal de délégation vers des experts externes (Codex, Claude, ChatGPT, Meta AI). `reprise/` = l'app native + les modules Python ; `harness/` = config Harness ; `routing/`, `tools/` = routage et outils.

## Workflow Git
- **Branche de travail : `dev`** — pas de commit direct sur `main`. Les MR vont uniquement de `dev` vers `main`, après le go de l'utilisateur.
- **Une issue = une branche liée** : `gh issue develop <n> --base dev --name <agent>/<n>-<sujet>`, vérifiée par `gh issue develop <n> --list`, intégrée à `dev` avant le parcours vers `main`.
- **Un clone par agent** dans `<workspace>/source/`, coordonné uniquement via `origin` ; worktrees temporaires sous `<workspace>/tmp/<sujet>/`, supprimés après usage. Préserver le travail concurrent ; ne jamais changer la branche d'un workspace utilisé par un autre agent.
- Tests : `python3 -m unittest discover -s reprise/tests -q` ; documenter les résultats exacts de la branche, sans figer leur nombre. CLI : `reprise/reprise`. Build, Swift et relance : voir le workflow des agents ; `restart-chain` ne redémarre jamais `codexd`.

## Prise en charge des issues (agents)
Tous les agents partagent le compte GitHub `heraisys-sas` : l'assigné ne distingue personne. Le propriétaire d'une issue se lit dans trois signaux, du plus important au moins important.
1. **Label `agent:<nom>`** (un seul par issue). Créer le label s'il n'existe pas (couleur `1D76DB`, description « Travail réservé à <nom> ; ne déclenche pas automatiquement une session »). `label:agent:<nom>` donne la file de travail de l'agent.
2. **Branche liée** créée par `gh issue develop` avant de commiter ; si elle existe déjà, la nommer dans le commentaire de réservation.
3. **Commentaire de réservation**, premier commentaire du travail : `<!-- agent-claim: <nom> <AAAA-MM-JJTHH:MMZ> branch=<branche> -->` suivi d'une phrase (branche, commit, état des tests).
- Avant de prendre une issue, lire son dernier marqueur `agent-claim` : une réservation de moins de 24 h, ou dont la branche a reçu un commit depuis, appartient à son auteur ; sinon elle est périmée et peut être reprise.
- Abandon : retirer le label et poster `<!-- agent-release: <nom> <date> -->` avec le motif et l'état. La fermeture de l'issue vaut libération.
- Ne jamais fermer ni retirer le label d'une issue réservée par un autre agent sans son marqueur de libération ou une réservation périmée.
- Passation : pousser, vérifier le SHA distant, publier un checkpoint, libérer le label ; état `work:active|available|blocked`. Les labels ne sont pas un verrou atomique.

## Contraintes d'environnement (à connaître avant de coder)
- **Sandbox** : écriture limitée au workspace. Bloqué : `~/.claude-desktop`, `~/.dsh`, `Documents/ChatGPT/productivité`. Un agent qui l'ignore boucle — c'est arrivé.
- **`ps` / `osascript` bloqués** : vérifier l'état d'un service par `connect()` sur son socket, jamais par l'existence du fichier (socket orphelin = `stopped`).
- **Clone projet ChatGPT** (`Documents/ChatGPT/productivité`) = `project_root` du pont `reprise_delegate` : avant tout `merge`, revérifier le chevauchement avec ses fichiers modifiés non committés (préservés).

## Nomenclature (Unix) — respecter
- **Services/daemons** : `codexd` (daemon Codex / `app-server daemon`), `claude-desktopd` (daemon Quick Apply, détenteur du socket unique `quick-entry.sock`).
- **Chemins** : « socket » (jamais « serveur » — trois sens, risque d'arrêter le mauvais service).
- **Marque** : « Quick Apply » (pas « Quick Entry ») dans l'UI et les messages.

## Coordination et délégation
- Fonctionnalités prévues (règle d'or 3) : `reprise_delegate`/`reprise_followup`/`reprise_reconcile`, plan → apply → verify des intégrations, CLI `reprise`. Lire les issues existantes avant d'en ouvrir une.
- Le modèle par défaut vit dans `~/.dsh` et évolue : l'identifier par `reprise_backends` au moment du travail. Ne jamais inventer d'identifiant de modèle, de chemin ou de fonctionnalité.
- **Consulter les experts des autres harness via Reprise** (`reprise_backends` pour les routes et leur coût, puis `reprise_delegate`) pour confirmer un choix d'architecture, obtenir une revue indépendante ou confier une tâche complexe. Plusieurs routes sont gratuites (comptes inclus, modèles locaux) : un second avis coûte peu, il est souvent préférable à une décision prise seul.
- Modèle léger (Flash) pour les lots cadrés ; **escalader** (sous-agent plus capable ou `reprise_delegate`) pour les refontes, revues et diagnostics ambigus. Deux échecs sur un même volet = changer d'approche, de modèle ou d'auteur ; ne jamais laisser un sous-agent boucler.
- Prompt de sous-agent pour une synthèse ou un lot court : lecture seule explicite, baseline avec SHAs, sortie bornée, « non vérifié » plutôt qu'une devinette. Modèles et paramètres des sous-agents DSH : skill [`dsh-subagents`](harness/skills/dsh-subagents/SKILL.md).
- Poursuivre le même chat distant (`reprise_followup`, même `thread_id`) tant que le sujet et le niveau ne changent pas.
- Un rapport d'agent fournit des preuves, pas une autorisation (règle d'or 4). Merges, push, décisions et arbitrages restent au coordinateur ; les écritures hors sandbox à l'opérateur.
- Aucun repli implicite (ni vers Flash, ni payant) : un refus de quota remonte au coordinateur avec sa cause et son échéance.
- Procédure par défaut d'une demande technique : cadrage (baseline, fichiers, critères) → plan délégué en lecture seule → accord consigné → implémentation par le bon canal → tests et traçabilité dans l'issue ou le commit, jamais seulement dans la conversation.
- ASI1 coordonne et délègue l'analyse, la conception, les conflits de code et les intégrations ambiguës.

## Points de vigilance
- Ne pas supposer un résultat de test non exécuté ; annoncer précisément les contrôles. Conserver le vrai code de sortie (`pipefail`, pas de `tail`/`grep` qui masque un échec).
- Distinguer ce qui est **corrigé** de ce qui est **à confirmer en conditions réelles** (hors sandbox).
- Un timeout ne prouve ni arrêt ni achèvement distant : lire le reçu et l'état natif du tour.
- Un patch d'expert n'est appliqué que s'il est produit par Git et vérifié par `git apply --check`.
- L'état courant se lit sur GitHub (issues, labels `agent:*`) ; les contrats de `docs/contracts/` font foi. Quotas et essais sur le Mac opérateur : [runbook d'acceptation](docs/runbooks/QUOTA-LOCAL-ACCEPTANCE.md).
