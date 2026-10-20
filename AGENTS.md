# Contexte de travail — projet Reprise & Harness

Instructions de projet pour les agents qui travaillent sur ce dépôt. Complète (sans écraser) les préférences globales de Harness (`~/.dsh/AGENTS.md`). Répondre en français, identifiants techniques en anglais.

## Ce projet est
Une application macOS de **supervision** (SwiftUI/AppKit + helpers Python) qui suit les sessions d'agents, détecte les anomalies post-update, lit les journaux locaux de Claude Desktop et sert de canal de délégation vers des experts externes (Codex, Claude). `agent-resume/` = l'app native + les modules Python ; `deepseek-harness-config/` = config Harness ; `routing/`, `harness-tools/` = routage et outils.

## Workflow Git
- **Branche de travail : `dev`** — pas de commit direct sur `main`.
- **MR `dev` → `main`** (une MR ouverte : #1). Chaque lot de travail = commit sur `dev`, puis fusion via MR.
- Tests : `python3 -m unittest discover -s agent-resume/tests -q` (~257 OK).
- Compile Swift (sans relancer l'app) : `sh native-install` ; `--open` pour lancer l'app.
- Check syntaxe Swift : `swiftc -parse -module-cache-path /private/tmp/agent-resume-swift-cache Native.swift Dashboard.swift` (dans `agent-resume/`).
- Redémarrer toute la chaîne (codexd + claude-desktopd + Reprise) : `./restart-chain` (ou `--no-build`, `--server`).

## Architecture — canaux de délégation et leurs limites
| Canal | Sens | Limite |
|---|---|---|
| **Codex** (`reprise_delegate`) | aller-retour, vérifiable | **seul opérationnel** ; plafond **local** de 8 appels/jour UTC/projet/endpoint → `budget_blocked` (compteur `expert_calls` du pont dans `expert.sqlite` — **pas** un quota ChatGPT ; le quota fournisseur donne `quota_blocked`) |
| **Quick Entry / Quick Apply** | Reprise → Claude Desktop | **unidirectionnel** : soumet un tour, ne relit PAS la réponse (`response: None`) |
| **MCP `get_task`/`submit_result`** | aller-retour via SQLite | 1 fichier joint, 1 réponse/tâche ; Claude doit invoquer l'outil (pas push) |

`sampling/createMessage` est refusé par le client MCP Desktop (`-32601`). Pas d'API d'inférence publique. Voir `agent-resume/CLAUDE-MCP-PROBE.md`, `ARCHITECTURE-QUESTION-PENDING.md`, `reprise-delegation.md`.

## Contraintes d'environnement (à connaître avant de coder)
- **Sandbox** : écriture limitée au workspace. Bloqué : `~/.claude-desktop` (socket orphinel impossible à `rm`), `~/.dsh`, `Documents/ChatGPT/productivité`. Un agent qui l'ignore boucle — c'est arrivé.
- **`ps` / `osascript` bloqués** — pas d'inspection de processus fine. Vérifier l'état par **connexion au socket** (`connect()`), pas par existence du fichier socket.
- **Clone du pont MCP** (`Documents/ChatGPT/productivité` = même dossier que `~/Library/Mobile Documents/…/productivité`, inode `.git` identique) = `project_root` du pont `reprise_delegate`. **Synchronisé le 20/10** : `18ff0b4` → `2702215` par `fetch` + `merge --ff-only`, après vérification d'un **chevauchement vide** avec les **7 fichiers modifiés localement non committés** (préservés). Les fichiers créés après `18ff0b4` (`ARCHITECTURE-QUESTION-PENDING.md`, `ux_review_probe.py`, `UX-UI-ANALYSIS.md`) sont donc présents → `reprise_delegate` avec `paths` résout (`resolve(strict=True)`). Avant tout futur `merge`, revérifier le chevauchement incoming ↔ 7 fichiers sales.

## Nomenclature (Unix) — respecter
- **Services/daemons** : `codexd` (daemon Codex / `app-server daemon`), `claude-desktopd` (daemon Quick Apply).
- **Onglet** : « Connexions » (onglet unique, Picker interne codex-desktop / claude-desktop).
- **Chemins** : « socket » (jamais « serveur » — le mot « serveur » avait 3 sens, risque d'arrêter le mauvais service).
- **Marque** : « Quick Apply » (pas « Quick Entry ») dans l'UI et les messages.

## Faits techniques établis (ne pas re-découvrir)
- Le serveur Quick Apply **démarre SANS Claude Desktop** (il bind son socket seul) → pas de blocage d'ordre ; les soumissions sont livrées quand Desktop est prêt.
- Un **socket orphelin** (fichier présent, connexion refusée) = `stopped`, pas `running`. Vérifier par `connect()`.
- `native-install` ne relance l'app **qu'avec `--open`** (compiler ne perturbe pas la session).
- Les timestamps de l'UI sont en **heure locale 24h** (helpers `localTime`/`localDateTime` dans `Dashboard.swift`).
- `runtime.terminate()` doit être résilient (un `killpg` refusé ne doit pas écraser le retour de l'appelant) — corrigé, source du bug des quotas manquants.

## Modèles & escalade
- Le modèle par défaut du workspace est configuré dans `~/.dsh` (hors dépôt) — **il évolue, ne pas le figer ici**. L'identifier par `reprise_backends` / la config du profil au moment du travail.
- Un modèle **léger** (type Flash) est adapté aux tâches **cadrées/localisées** : ajout de composants/labels, correctifs ciblés, tests. Il peut **bloquer sur de la refonte structurante** (échec observé : boucle sans résultat sur une fusion d'onglets).
- **Pour les tâches complexes, ESCALADER vers un modèle plus compétent** plutôt que de forcer le modèle léger :
  - via **sous-agent** (`subagent`) avec un modèle plus capable (DeepSeek Pro / raisonnement élevé, ou un modèle Codex type Sol/Astra selon `reprise_backends`) ;
  - ou via **l'outil de délégation Reprise** (`reprise_delegate`) pour un expert externe (revue, expertise, batch).
- Ne jamais laisser un sous-agent boucler indéfiniment sur une tâche hors de sa portée : le constater, l'interrompre, et **escalader ou faire soi-même**. Deux échecs sur un même volet = changer d'approche (modèle ou auteur).

## État et suivi
- Issues GitHub #2–#5 : symptômes UI, architecture de retour (avis Astra obtenu le 20/10 — voir plus bas), canal MCP UX review (prêt, non branché), analyse UX/UI.
- Revue UX/UI : les 4 priorités sont traitées (badges + résumé Santé, nomenclature, accessibilité + feedback, fusion onglets). Détails dans `agent-resume/UX-UI-ANALYSIS.md`.
- Conseil architectural **Astra obtenu le 20/10** (`gpt-6-astra` high, thread `01a15938…`) : **A + B** — Quick Apply déclenche, MCP `get_task`/`submit_result` reçoit, SQLite registre ; seul `submit_result` vaut retour. Réponse intégrale + invariant dans `agent-resume/ARCHITECTURE-QUESTION-PENDING.md`. **Suite : implémenter le manifeste/`request_hash` et brancher `reprise_ux_review` (validation opérateur)**. Suivi via `codex_followup` sur ce thread.

## Points de vigilance
- Ne pas supposer un résultat de test non exécuté ; annoncer précisément les contrôles.
- Distinguer ce qui est **corrigé** de ce qui est **à confirmer en conditions réelles** (hors sandbox).
- Ne pas inventer d'identifiant de modèle, de chemin ou de fonctionnalité — vérifier dans le code/config.
